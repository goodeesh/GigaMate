import os
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# Ensure headless Qt execution in tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt6.QtWidgets import QApplication
except (ImportError, OSError) as exc:
    pytest.skip(f"PyQt6/libEGL not available in this test environment: {exc}", allow_module_level=True)

# Ensure QApplication singleton for test suite
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _import_tray_app():
    """Import the PyQt6 tray app."""
    from gigamate.tray import GigaMateTrayApp
    return GigaMateTrayApp


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    import gigamate.config as config_mod
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    # main_window/onboarding/daemon bound CONFIG_FILE at import; redirect them too.
    monkeypatch.setattr("gigamate.ui.main_window.CONFIG_FILE", cfg_file, raising=False)
    monkeypatch.setattr("gigamate.ui.onboarding.CONFIG_FILE", cfg_file, raising=False)
    monkeypatch.setattr("gigamate.daemon.CONFIG_FILE", cfg_file, raising=False)
    # Start each test with DEFAULT_CONFIG
    config_mod.save(dict(config_mod.DEFAULT_CONFIG))


def test_main_window_creation(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "GigaMate Center"
    assert win.stack.count() == 6

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_rgb.click()
    assert win.stack.currentIndex() == 2

    win.btn_gpu.click()
    assert win.stack.currentIndex() == 3

    win.btn_automation.click()
    assert win.stack.currentIndex() == 4

    win.btn_settings.click()
    assert win.stack.currentIndex() == 5

    win.btn_dashboard.click()
    assert win.stack.currentIndex() == 0


@pytest.mark.parametrize("visible,status", [(False, "active"), (True, "suspended")])
def test_gpu_holders_skip_hidden_or_sleeping(qapp, visible, status):
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    page = gp.GpuPage()
    page.setVisible(visible)
    try:
        with patch.object(gp.dgpu_tune, "wake_holders") as scan:
            page._refresh_holders(GpuState(present=True, vendor="nvidia", status=status))
            scan.assert_not_called()
            assert not page._holders_busy
    finally:
        page.close()
        page.deleteLater()


def test_gpu_holders_scan_keeps_ui_responsive_and_caches(qapp):
    import threading
    from PyQt6.QtCore import QTimer
    from PyQt6.QtTest import QTest
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    page = gp.GpuPage()
    page.show()
    page.timer.stop()
    awake = GpuState(present=True, vendor="nvidia", status="active")
    started = threading.Event()
    release = threading.Event()
    done = threading.Event()
    worker_threads = []

    def slow_scan():
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait(3)
        done.set()
        return ["game"]

    try:
        with patch.object(gp.dgpu_tune, "wake_holders", side_effect=slow_scan) as scan, \
             patch.object(gp, "get_gpu_state", return_value=awake):
            page._refresh_holders(awake)
            assert started.wait(1)
            assert len(worker_threads) == 1
            assert worker_threads[0] != threading.get_ident()
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            QTest.qWait(30)
            assert heartbeat == [True]
            page._refresh_holders(awake)
            assert scan.call_count == 1
            release.set()
            assert done.wait(1)
            for _ in range(100):
                QTest.qWait(10)
                if not page._holders_busy:
                    break
            assert page.gpu_holders_lbl.text() == "Awake held by: game"
            page._refresh_holders(awake)
            assert scan.call_count == 1
    finally:
        release.set()
        page.close()
        page.deleteLater()


def test_gpu_page_max_clock_controls(qapp):
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({
        "dgpu_undervolt_enabled": True,
        "dgpu_undervolt_offset_mhz": 100,
        "dgpu_max_clock_enabled": True,
        "dgpu_max_clock_mhz": 2500,
    })
    config_mod.save(cfg)

    page = gp.GpuPage()
    with patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 100}), \
         patch.object(gp.dgpu_tune, "probe",
                      return_value={"supported": True, "device": "RTX 5060",
                                    "gpu_max_clock_mhz": 3090}), \
         patch.object(gp.dgpu_tune, "read_state_file",
                      return_value={"ts": 1.0, "offset_mhz": 100,
                                    "applied_offset": 100, "applied_max": 2500}), \
         patch.object(gp.dgpu_tune, "wake_holders", return_value=[]), \
         patch.object(gp, "get_gpu_state",
                      return_value=GpuState(present=True, vendor="nvidia",
                                            status="active", power_state="D0")), \
         patch.object(gp, "gpu_status_text", return_value="Active"):
        page.reload_from_config()

    # Presets/checkbox/spinbox are gone; it is slider-only.
    assert not hasattr(page, "max_preset_buttons")
    assert not hasattr(page, "max_spin")
    assert not hasattr(page, "max_check")
    assert not hasattr(page, "preset_buttons")

    # Range: [normal - 800, normal + headroom]; normal = 3090 + 300 headroom.
    assert page.max_slider.minimum() == 3090 - gp.dgpu_tune.MAX_CLOCK_SPAN
    assert page.max_slider.maximum() == 3090 + gp.dgpu_tune.MAX_CLOCK_HEADROOM
    assert page.max_slider.value() == 2500
    assert "cap 2500 MHz" in page.max_status_lbl.text()
    assert "+100 MHz" in page.uv_status_lbl.text()

    # Top of the slider reads as stock/unlocked.
    page._update_max_readout(page._normal_max)
    assert page.max_val.text() == "Stock (unlocked)"
    page._update_max_readout(2500)
    assert "Cap 2500 MHz" in page.max_val.text()

    # Apply behaviour: top = unlock, below = cap.
    with patch.object(gp.dgpu_tune, "set_desired_config") as sdc, \
         patch.object(page, "_read_published"):
        page.max_slider.setValue(page._normal_max)
        page._apply_all()
        assert sdc.call_args.kwargs["max_enabled"] is False
        assert sdc.call_args.kwargs["max_clock"] == 0
        sdc.reset_mock()
        page.max_slider.setValue(2500)
        page._apply_all()
        assert sdc.call_args.kwargs["max_enabled"] is True
        assert sdc.call_args.kwargs["max_clock"] == 2500

    # A cap set below the default travel (e.g. via CLI) widens the low end.
    cfg["dgpu_max_clock_mhz"] = 1800
    config_mod.save(cfg)
    with patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 100}), \
         patch.object(gp.dgpu_tune, "probe",
                      return_value={"supported": True, "gpu_max_clock_mhz": 3090}), \
         patch.object(gp.dgpu_tune, "read_state_file",
                      return_value={"applied_max": 1800}), \
         patch.object(gp.dgpu_tune, "wake_holders", return_value=[]), \
         patch.object(gp, "get_gpu_state",
                      return_value=GpuState(present=True, vendor="nvidia",
                                            status="active", power_state="D0")), \
         patch.object(gp, "gpu_status_text", return_value="Active"):
        page.reload_from_config()
    assert page.max_slider.minimum() == 1800
    assert page.max_slider.value() == 1800
    page.deleteLater()


def test_gpu_page_sliders_not_reset_while_dragging(qapp):
    """Regression: the refresh timer must not yank a slider mid-drag."""
    from PyQt6.QtWidgets import QSlider

    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({
        "dgpu_undervolt_enabled": True,
        "dgpu_undervolt_offset_mhz": 100,
        "dgpu_max_clock_enabled": True,
        "dgpu_max_clock_mhz": 2500,
    })
    config_mod.save(cfg)

    page = gp.GpuPage()
    with patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 100}), \
         patch.object(gp.dgpu_tune, "probe",
                      return_value={"supported": True, "gpu_max_clock_mhz": 3090}), \
         patch.object(gp.dgpu_tune, "read_state_file", return_value={"applied_max": 2500}), \
         patch.object(gp.dgpu_tune, "wake_holders", return_value=[]), \
         patch.object(gp, "get_gpu_state",
                      return_value=GpuState(present=True, vendor="nvidia", status="active")), \
         patch.object(gp, "gpu_status_text", return_value="Active"):
        page.reload_from_config()

    # Simulate the user dragging both handles to new, not-yet-applied values.
    page.uv_slider.setValue(220)
    page.max_slider.setValue(2350)

    with patch.object(QSlider, "isSliderDown", return_value=True), \
         patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 100}), \
         patch.object(gp.dgpu_tune, "probe",
                      return_value={"supported": True, "gpu_max_clock_mhz": 3090}), \
         patch.object(gp.dgpu_tune, "read_state_file", return_value={"applied_max": 2500}), \
         patch.object(gp.dgpu_tune, "wake_holders", return_value=[]), \
         patch.object(gp, "get_gpu_state",
                      return_value=GpuState(present=True, vendor="nvidia", status="active")), \
         patch.object(gp, "gpu_status_text", return_value="Active"):
        page._refresh()
        page._refresh()

    assert page.uv_slider.value() == 220
    assert page.max_slider.value() == 2350
    page.deleteLater()


def _gpu_page_patches(gp, *, probe=None, state_file=None,
                      gpu_state=None, holders=()):
    """Common patch set for GpuPage: no real helper, sysfs or hardware."""
    probe = probe if probe is not None else {
        "supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
        "mem_max_clock_mhz": 12001, "mem_offset_api": True,
        "helper_version": 4,
    }
    state_file = state_file if state_file is not None else {}
    return [
        patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 0}),
        # The page probes through the NVML gate, so patch both entry points.
        patch.object(gp.dgpu_tune, "probe", return_value=probe),
        patch.object(gp.dgpu_tune, "probe_if_awake", return_value=probe),
        patch.object(gp.dgpu_tune, "read_state_file", return_value=state_file),
        patch.object(gp.dgpu_tune, "wake_holders", return_value=list(holders)),
        patch.object(gp.dgpu_tune, "cached_helper_version", return_value=4),
        patch.object(gp, "get_gpu_state", return_value=gpu_state),
    ]


class _StartPatch:
    """Context manager applying a list of patches."""

    def __init__(self, patches):
        self._patches = patches
        self._started = []

    def __enter__(self):
        for p in self._patches:
            p.start()
            self._started.append(p)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._started):
            p.stop()
        return False


def test_gpu_page_mem_offset_slider(qapp):
    """The memory slider is a signed offset, read back from the card."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({"dgpu_mem_offset_enabled": True, "dgpu_mem_offset_mhz": 300})
    config_mod.save(cfg)

    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    state_file = {"applied_mem": 300}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, state_file=state_file)):
        page.reload_from_config()

    assert page.mem_slider.minimum() == gp.dgpu_tune.MEM_OFFSET_MIN_MHZ
    assert page.mem_slider.maximum() == gp.dgpu_tune.MEM_OFFSET_MAX_MHZ
    assert page._mem_offset() == 300
    assert page.mem_val.text() == "+300 MHz"
    assert "Applied: +300 MHz" in page.mem_status_lbl.text()

    # Dragging only stages: it must not touch the hardware by itself.
    with patch.object(gp.dgpu_tune, "set_desired_config") as sdc:
        page.mem_slider.setValue(-150)
        assert sdc.call_count == 0
        assert page.mem_val.text() == "-150 MHz"
        assert "memory offset" in page.apply_hint_lbl.text()
    page.deleteLater()


def test_gpu_page_single_apply_commits_all_controls(qapp):
    """One Apply sends all three controls in a single call."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    config_mod.save(dict(config_mod.load()))
    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu)):
        page.reload_from_config()
        page.uv_slider.setValue(120)
        page.max_slider.setValue(2500)
        page.mem_slider.setValue(300)

        with patch.object(gp.dgpu_tune, "set_desired_config") as sdc, \
             patch.object(page, "_read_published"):
            page._apply_all()
            sdc.assert_called_once_with(
                enabled=True, offset=120,
                max_enabled=True, max_clock=2500,
                mem_enabled=True, mem_offset=300,
            )
    page.deleteLater()


def test_gpu_page_reset_buttons_apply_immediately(qapp):
    """Reset is unambiguous, so each one acts on its own control immediately."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({
        "dgpu_undervolt_enabled": True, "dgpu_undervolt_offset_mhz": 100,
        "dgpu_max_clock_enabled": True, "dgpu_max_clock_mhz": 2500,
        "dgpu_mem_offset_enabled": True, "dgpu_mem_offset_mhz": 300,
    })
    config_mod.save(cfg)

    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu)):
        page.reload_from_config()
        with patch.object(gp.dgpu_tune, "set_desired_config") as sdc, \
             patch.object(page, "_read_published"), patch.object(page, "_refresh"):
            page._reset_uv()
            assert sdc.call_args.kwargs == {"enabled": False, "offset": 0}
            sdc.reset_mock()
            page._reset_max()
            assert sdc.call_args.kwargs == {"max_enabled": False, "max_clock": 0}
            sdc.reset_mock()
            page._reset_mem()
            assert sdc.call_args.kwargs == {"mem_enabled": False, "mem_offset": 0}
    page.deleteLater()


def test_gpu_page_mem_offset_hidden_when_unsupported(qapp):
    """No memory-offset API (or a stale helper) must disable the control."""
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    probe = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
             "mem_max_clock_mhz": None, "mem_offset_api": False, "helper_version": 4}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, probe=probe)):
        page.reload_from_config()
    assert not page.mem_slider.isEnabled()
    assert "does not support the memory-clock offset" in page.mem_support_lbl.text()

    # A helper too old to understand the offset argument must say so.
    page2 = gp.GpuPage()
    probe2 = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
              "mem_max_clock_mhz": 12001, "mem_offset_api": True, "helper_version": 3}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, probe=probe2)), \
         patch.object(gp.dgpu_tune, "cached_helper_version", return_value=3):
        page2.reload_from_config()
    assert "too old" in page2.mem_support_lbl.text()
    assert not page2.mem_slider.isEnabled()
    page.deleteLater()
    page2.deleteLater()


def test_gpu_page_never_wakes_a_sleeping_dgpu(qapp):
    """Regression: the page must not poll NVML while the dGPU is suspended.

    Opening NVML takes a PM reference on the device, so a page that polled it on
    a timer kept a laptop's dGPU awake for as long as it was visible — reported
    as "I opened GigaMate Center and the dGPU woke up".
    """
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    spawns = []

    def counting_run(args):
        spawns.append(args[0])
        return {"ok": True, "device": "x", "offset_mhz": 200, "util": 0,
                "gpu_max_clock_mhz": 3090, "mem_max_clock_mhz": 12001,
                "mem_offset_api": True, "helper_version": 4}

    asleep = GpuState(present=True, vendor="nvidia", status="suspended", power_state="D3cold")
    page = gp.GpuPage()
    page.show()
    try:
        with mock.patch.object(gp.dgpu_tune, "_run_helper", counting_run), \
             mock.patch.object(gp, "get_gpu_state", return_value=asleep), \
             mock.patch.object(gp.dgpu_tune, "read_state_file", return_value={}), \
             mock.patch.object(gp.dgpu_tune, "wake_holders", return_value=[]), \
             mock.patch.object(gp.dgpu_tune, "find_nvidia_bdf",
                               return_value="0000:64:00.0"), \
             mock.patch.object(gp.dgpu_tune, "runtime_status", return_value="suspended"):
            page.reload_from_config()
            for i in range(60):        # 60 ticks * 1.5 s = 90 s of being visible
                page._tick_count = i
                page._on_tick()
            assert spawns == [], f"page woke the dGPU: {spawns}"
    finally:
        page.close()
        page.deleteLater()


def test_gpu_page_does_not_poll_nvml_while_awake_either(qapp):
    """With the watcher publishing, the page must read the file, not the GPU."""
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    spawns = []

    def counting_run(args):
        spawns.append(args[0])
        return {"ok": True, "device": "x", "gpu_max_clock_mhz": 3090,
                "mem_max_clock_mhz": 12001,
                "mem_offset_api": True, "helper_version": 4}

    awake = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    state_file = {"ts": 1.0, "offset_mhz": 200,
                  "applied_offset": 200, "applied_max": 3000, "applied_mem": 11001}
    page = gp.GpuPage()
    page.show()
    try:
        with mock.patch.object(gp.dgpu_tune, "_run_helper", counting_run), \
             mock.patch.object(gp, "get_gpu_state", return_value=awake), \
             mock.patch.object(gp.dgpu_tune, "read_state_file", return_value=state_file), \
             mock.patch.object(gp.dgpu_tune, "wake_holders", return_value=["game"]), \
             mock.patch.object(gp.dgpu_tune, "find_nvidia_bdf",
                               return_value="0000:64:00.0"), \
             mock.patch.object(gp.dgpu_tune, "runtime_status", return_value="active"):
            page.reload_from_config()          # one probe for the initial load
            initial = len(spawns)
            for i in range(1, 60):             # then nothing on the timer
                page._tick_count = i
                page._on_tick()
            assert len(spawns) == initial, f"timer polled NVML: {spawns[initial:]}"
    finally:
        page.close()
        page.deleteLater()
def test_gpu_page_shows_configured_value_while_asleep(qapp):
    """The dGPU reads 0 while asleep, so don't claim the tuning is off."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({"dgpu_undervolt_enabled": True, "dgpu_undervolt_offset_mhz": 100})
    config_mod.save(cfg)

    asleep = GpuState(present=True, vendor="nvidia", status="suspended", power_state="D3cold")
    page = gp.GpuPage()
    state_file = {"ts": 1.0, "offset_mhz": 0, "applied_offset": 0, "applied_max": 0}
    probe = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
             "mem_max_clock_mhz": 12001,
             "mem_offset_api": True, "helper_version": 4}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=asleep, probe=probe,
                                       state_file=state_file)):
        page.reload_from_config()
    assert "applies when the dGPU wakes" in page.uv_status_lbl.text()
    assert "Off (stock)" not in page.uv_status_lbl.text()
    page.deleteLater()


def test_gpu_page_reports_paused_while_awake_idle(qapp):
    """Awake but idle-cleared must not claim 'asleep or pending'."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({"dgpu_undervolt_enabled": True, "dgpu_undervolt_offset_mhz": 100})
    config_mod.save(cfg)

    awake = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    page = gp.GpuPage()
    state_file = {"ts": 1.0, "offset_mhz": 0, "applied_offset": 0, "applied_max": 0,
                  "applied_mem": 0, "idle_cleared": True, "runtime": "active", "auto": True}
    probe = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
             "mem_max_clock_mhz": 12001,
             "mem_offset_api": True, "helper_version": 4}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=awake, probe=probe,
                                       state_file=state_file)):
        page.reload_from_config()
    text = page.uv_status_lbl.text()
    assert "paused while the dGPU is idle" in text
    assert "asleep or pending" not in text
    page.deleteLater()


def test_every_page_fits_the_narrowest_window(qapp):
    """No page may need more width than the window can give it.

    The pages sit in scroll areas with the horizontal scrollbar switched off, so
    a page whose minimum width exceeds the viewport is simply *clipped* — that is
    what made the battery (775 px) and settings (823 px) pages look broken on a
    narrow window while the GPU page (351 px) reflowed correctly. Prose must wrap
    and chip/button rows must flow.
    """
    from PyQt6.QtWidgets import QScrollArea

    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    try:
        # The worst case: the window at its own minimum width.
        win.resize(win.minimumSize())
        qapp.processEvents()
        available = win.stack.width()
        assert available > 0

        for idx, name in enumerate(("dashboard", "battery", "rgb", "gpu", "automation", "settings")):
            win.stack.setCurrentIndex(idx)
            qapp.processEvents()
            area = win.stack.currentWidget()
            assert isinstance(area, QScrollArea)
            page = area.widget()
            need = page.minimumSizeHint().width()
            assert need <= available, (
                f"{name} page needs {need}px but only {available}px is available — "
                f"its prose must wrap and its rows must flow"
            )
    finally:
        win.close()
        win.deleteLater()


def test_flow_container_wraps_and_reports_height(qapp):
    """A flow row must get taller as it gets narrower, not wider."""
    from PyQt6.QtWidgets import QLabel

    from gigamate.ui.flow_layout import FlowContainer

    container = FlowContainer(spacing=8)
    for i in range(4):
        container.addWidget(QLabel(f"chip number {i}"))
    container.resize(600, 40)
    container.show()
    qapp.processEvents()

    wide = container.heightForWidth(600)
    narrow = container.heightForWidth(150)
    assert narrow > wide, f"flow row did not wrap (wide={wide}, narrow={narrow})"
    assert container.minimumSize().width() < 150, \
        "a wrapping row must not carry the sum of its children as a minimum"
    container.deleteLater()


def test_pages_scroll_instead_of_being_squeezed(qapp):
    """Regression: a small window must not collapse a page's layout.

    The GPU page needs ~900 px, but the window's minimum is 640 px tall. Without
    a scroll area Qt squeezed the page below its minimum, which crushed its
    layouts into overlapping widgets (seen when moving the window to the laptop's
    own panel).
    """
    from PyQt6.QtWidgets import QScrollArea

    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    try:
        for idx, name in ((0, "dashboard"), (3, "gpu"), (4, "settings")):
            area = win.stack.currentWidget() if idx == win.stack.currentIndex() else None
            if area is None:
                win.stack.setCurrentIndex(idx)
                area = win.stack.currentWidget()
            assert isinstance(area, QScrollArea), f"{name} page is not scrollable"

        # The worst case: the window at its own minimum height.
        win.resize(win.minimumSize())
        win.stack.setCurrentIndex(3)
        qapp.processEvents()

        page = win.page_gpu
        assert page.height() >= page.minimumSizeHint().height(), \
            "GPU page was squeezed below its minimum instead of scrolling"

        # The sliders must keep their natural height rather than being crushed.
        for name, slider in (("undervolt", page.uv_slider), ("max clock", page.max_slider),
                             ("memory", page.mem_slider)):
            assert slider.height() >= slider.sizeHint().height(), \
                f"{name} slider collapsed: {slider.height()} < {slider.sizeHint().height()}"
    finally:
        win.close()
        win.deleteLater()


def test_gpu_sliders_do_not_eat_the_scroll_wheel(qapp):
    """Scrolling the page must not retune the GPU clocks."""
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent

    import gigamate.ui.pages.gpu_page as gp

    page = gp.GpuPage()
    page.uv_slider.setValue(120)
    for slider in (page.uv_slider, page.max_slider, page.mem_slider):
        before = slider.value()
        event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        slider.wheelEvent(event)
        assert slider.value() == before, "wheel over a slider changed its value"
        assert not event.isAccepted(), "wheel event should be passed to the scroll area"
    page.deleteLater()
def test_dashboard_profile_selection(qapp):
    from unittest.mock import patch
    from gigamate.ui.main_window import MainWindow

    profiles = {
        0: {"name": "Quiet"},
        1: {"name": "Balanced"},
        2: {"name": "Performance"},
        3: {"name": "Gaming"},
    }

    with patch("gigamate.ui.pages.dashboard_page.discover_acpi_profiles", return_value=profiles):
        win = MainWindow()
        dash = win.page_dashboard

        # Select Quiet (id 0)
        dash._select_profile(0)
        assert dash._profile_buttons[0].isChecked()
        assert not dash._profile_buttons[3].isChecked()

        # Select Gaming (id 3)
        dash._select_profile(3)
        assert dash._profile_buttons[3].isChecked()
        assert not dash._profile_buttons[0].isChecked()


def test_rgb_page_color_highlighting(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    rgb = win.page_rgb

    # Select Red
    rgb.hex_input.setText("#ff0000")
    assert rgb.hex_input.text().upper() == "#FF0000"

    # Select Blue
    rgb.hex_input.setText("#0000ff")
    assert rgb.hex_input.text().upper() == "#0000FF"


def test_single_instance_ipc(qapp):
    from gigamate.ui.main_window import MainWindow, SingleInstanceServer, IPC_SOCKET_NAME
    from PyQt6.QtNetwork import QLocalSocket

    win = MainWindow()
    server = SingleInstanceServer(win)
    try:
        # Simulate secondary client connecting
        client = QLocalSocket()
        client.connectToServer(IPC_SOCKET_NAME)
        assert client.waitForConnected(1000)
        client.write(b"ACTIVATE\n")
        client.waitForBytesWritten(500)
        client.disconnectFromServer()
    finally:
        server.server.close()


def test_battery_page(qapp):
    from gigamate.ui.pages.battery_page import BatteryPage

    page = BatteryPage()
    assert page.limit_slider.value() in range(40, 101)

    # Move slider to 60%
    page._set_slider_value(60)
    assert page.limit_slider.value() == 60
    assert "Limit: 60%" in page.slider_label.text()

    # Move slider to 80%
    page._set_slider_value(80)
    assert page.limit_slider.value() == 80
    assert "Limit: 80%" in page.slider_label.text()


def test_battery_page_topup_and_calibration(qapp):
    from gigamate.ui.pages.battery_page import BatteryPage
    from gigamate.ui.pages.automation_page import AutomationPage
    from gigamate.config import load as load_config, save as save_config

    page = BatteryPage()
    auto_page = AutomationPage()

    # Verify topup button exists
    assert page.topup_btn is not None
    assert "Charge to 100% Once" in page.topup_btn.text()

    # Toggle topup on
    with patch("gigamate.ui.pages.battery_page.start_topup", return_value=True) as mock_start:
        with patch("gigamate.ui.pages.battery_page.load_config", return_value={
            "charge_limit": 80,
            "charge_topup_active": False,
            "charge_calibration_auto": False,
            "charge_calibration_interval_days": 30,
        }):
            page._on_topup_clicked()
            mock_start.assert_called_once()

    # Auto calibration checkbox (moved to AutomationPage)
    assert auto_page.auto_cal_chk is not None
    assert auto_page.auto_cal_chk.switch.isChecked() is False  # Opt-in default False

    # Check auto calibration
    auto_page.auto_cal_chk.switch.setChecked(True)
    cfg = load_config()
    assert cfg.get("charge_calibration_auto") is True
    assert auto_page.interval_row_widget.isEnabled() is True

    # Interval buttons
    assert 30 in auto_page.interval_buttons
    assert 14 in auto_page.interval_buttons
    assert 60 in auto_page.interval_buttons

    auto_page.interval_buttons[14].click()
    cfg = load_config()
    assert cfg.get("charge_calibration_interval_days") == 14
    assert auto_page.interval_buttons[14].isChecked() is True

    # Reload from config with topup active
    cfg["charge_topup_active"] = True
    save_config(cfg)
    page.reload_from_config()
    assert "Cancel 100% Top-Up" in page.topup_btn.text()
    assert "Top-Up active" in page.topup_status_label.text()



def test_rgb_page_reload_from_config(qapp):
    from gigamate.ui.main_window import MainWindow
    from gigamate.config import load as load_config, save as save_config

    win = MainWindow()
    rgb = win.page_rgb

    # External change to light_blue and dim brightness (1)
    cfg = load_config()
    cfg["colour"] = "light_blue"
    cfg["brightness"] = 1
    cfg["idle_off_enabled"] = True
    cfg["idle_timeout_sec"] = 300
    save_config(cfg)

    # Trigger reload
    win.sync_all_from_config()

    assert "Active: Light Blue" in rgb.active_color_badge.text()
    assert rgb.brightness_slider.value() >= 5
    # 5m is now a canonical step, so it is preserved exactly (idle_combo moved to AutomationPage).
    win.page_automation.reload_from_config()
    assert win.page_automation.idle_combo.currentData() == 300


def test_rgb_page_idle_options_match_tray_steps(qapp):
    """Center and tray must expose the same idle-timeout steps (gigamate.idle)."""
    from gigamate.ui.main_window import MainWindow
    from gigamate.idle import IDLE_TIMEOUT_STEPS

    win = MainWindow()
    auto_page = win.page_automation

    canonical = [sec for sec, _ in IDLE_TIMEOUT_STEPS]
    assert [sec for sec, _ in auto_page.idle_options] == canonical
    assert 15 not in canonical


def test_tray_sync_from_external_config():
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    d._current_acpi_profile = 1
    d._idle_enabled = False
    d._idle_timeout = 60
    d._last_config_mtime = 0.0
    d._init_idle = MagicMock()

    with patch("gigamate.daemon.load_config", return_value={
        "colour": "red",
        "brightness": 1,
        "brightness_pct": 50,
        "rgb_enabled": True,
        "acpi_profile": 3,
        "idle_off_enabled": True,
        "idle_timeout_sec": 30,
        "startup_apply": False,
        "sync_system_power": True,
    }), patch.object(d, "_get_config_mtime", return_value=12345.0):
        assert d.sync_from_external_config() is True
        assert d._current_acpi_profile == 3
        assert d._idle_enabled is True
        assert d._idle_timeout == 30
        d._init_idle.assert_called_once()
        state = d.get_menu_state()
        assert state["active_profile"] == 3
        assert state["backlight_pct"] == 50


def test_single_instance_quit_ipc(qapp):
    from gigamate.ui.main_window import MainWindow, SingleInstanceServer
    from unittest.mock import patch, MagicMock

    win = MainWindow()
    server = SingleInstanceServer(win)
    try:
        with patch("gigamate.ui.main_window.QApplication.quit") as mock_quit:
            mock_client = MagicMock()
            mock_client.readAll.return_value = b"QUIT\n"
            server._on_ready_read(mock_client)
            assert mock_quit.called
            assert mock_client.disconnectFromServer.called
    finally:
        server.server.close()


def test_ensure_tray_running():
    from gigamate.ui.main_window import ensure_tray_running
    from unittest.mock import patch, MagicMock

    # 1. When already active
    mock_run = MagicMock()
    mock_run.return_value = MagicMock(returncode=0, stdout="active\n")
    with patch("gigamate.ui.main_window.subprocess.run", mock_run):
        ensure_tray_running()
        assert mock_run.call_count == 1
        assert "is-active" in mock_run.call_args[0][0]

    # 2. When inactive, attempts start
    def fake_run(cmd, **kwargs):
        if "is-active" in cmd:
            if fake_run.called_start:
                return MagicMock(returncode=0, stdout="active\n")
            return MagicMock(returncode=3, stdout="inactive\n")
        if "start" in cmd:
            fake_run.called_start = True
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=1)

    fake_run.called_start = False
    with patch("gigamate.ui.main_window.subprocess.run", side_effect=fake_run):
        ensure_tray_running()
        assert fake_run.called_start is True


def test_tray_on_quit_signals_center(qapp):
    GigaMateTrayApp = _import_tray_app()

    tray = GigaMateTrayApp(start_daemon=False)

    mock_socket_cls = MagicMock()
    mock_socket_instance = MagicMock()
    mock_socket_cls.return_value.__enter__.return_value = mock_socket_instance

    with patch("os.path.exists", return_value=True), \
         patch("gigamate.daemon.socket.socket", mock_socket_cls), \
         patch("gigamate.tray.QApplication.instance", return_value=MagicMock()):
        tray.quit()
        assert mock_socket_instance.sendall.called
        assert b"QUIT\n" in mock_socket_instance.sendall.call_args[0][0]


def test_battery_page_reload_from_config(qapp):
    from gigamate.ui.pages.battery_page import BatteryPage
    from gigamate.config import save as save_config, load as load_config

    page = BatteryPage()
    cfg = load_config()
    cfg["charge_limit"] = 70
    save_config(cfg)

    page.reload_from_config()
    assert page.limit_slider.value() == 70
    assert "Limit: 70%" in page.slider_label.text()


def test_tray_save_config_preserves_memory_keys():
    """Daemon backlight writes must never clobber unrelated keys like charge_limit."""
    from gigamate.config import load as load_config, update_config
    from gigamate.daemon import GigaMateDaemon

    update_config(lambda c: {**c, "charge_limit": 65, "charge_limit_enabled": True, "acpi_profile": 2})
    d = GigaMateDaemon()
    d.set_backlight_pct(50)

    cfg = load_config()
    assert cfg["charge_limit"] == 65
    assert cfg["charge_limit_enabled"] is True
    assert cfg["acpi_profile"] == 2
    assert cfg["brightness_pct"] == 50
    assert cfg["rgb_enabled"] is True


def test_settings_page(qapp):
    from gigamate.ui.pages.automation_page import AutomationPage
    from gigamate.ui.pages.settings_page import SettingsPage
    from gigamate.config import load as load_config, save as save_config

    auto_page = AutomationPage()
    assert auto_page.chk_startup_apply is not None
    assert auto_page.chk_sync_power is not None
    assert auto_page.chk_dgpu_auto is not None

    # Test toggling startup apply
    auto_page.chk_startup_apply.switch.setChecked(False)
    assert load_config()["startup_apply"] is False

    auto_page.chk_startup_apply.switch.setChecked(True)
    assert load_config()["startup_apply"] is True

    # Test toggling sync power
    auto_page.chk_sync_power.switch.setChecked(False)
    assert load_config()["sync_system_power"] is False

    auto_page.chk_sync_power.switch.setChecked(True)
    assert load_config()["sync_system_power"] is True

    # Test toggling dGPU auto-apply (patched: no real GPU/helper access).
    with patch("gigamate.dgpu_tune.find_nvidia_bdf", return_value=None), \
         patch("gigamate.dgpu_tune._write_state_file"):
        auto_page.chk_dgpu_auto.switch.setChecked(False)
        assert load_config()["dgpu_undervolt_auto"] is False

        auto_page.chk_dgpu_auto.switch.setChecked(True)
        assert load_config()["dgpu_undervolt_auto"] is True

    # Test external config reload
    cfg = load_config()
    cfg["startup_apply"] = False
    cfg["sync_system_power"] = False
    cfg["dgpu_undervolt_auto"] = False
    save_config(cfg)

    auto_page.reload_from_config()
    assert auto_page.chk_startup_apply.switch.isChecked() is False
    assert auto_page.chk_sync_power.switch.isChecked() is False
    assert auto_page.chk_dgpu_auto.switch.isChecked() is False

    # Test built-in keyboard & touchpad only idle toggle
    auto_page.idle_combo.setCurrentIndex(1)  # enable idle timeout (10s)
    assert auto_page.internal_only_row.isEnabled() is True
    auto_page.internal_only_row.switch.setChecked(True)
    assert load_config()["idle_internal_only"] is True
    auto_page.internal_only_row.switch.setChecked(False)
    assert load_config()["idle_internal_only"] is False

    # Test SettingsPage controls
    settings_page = SettingsPage()
    assert settings_page._profile_enable_checks is not None
    assert settings_page.btn_restart_service is not None
    assert settings_page.btn_copy_doctor is not None
    assert settings_page.btn_check_updates is not None

    settings_page._service_status_cache = (9999999999.0, "active")
    with patch("gigamate.ui.pages.settings_page.QTimer.singleShot"), \
         patch("subprocess.run", return_value=MagicMock(returncode=0)):
        settings_page.btn_restart_service.click()
        settings_page._finish_restart_service()
    assert settings_page._service_status_cache != (9999999999.0, "active")


def test_tray_self_heals_acpi_after_repair():
    """The daemon must recover its ACPI status once the driver is back."""
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    d._acpi_controller = None
    d._acpi_caps = None
    d._acpi_retry_at = 0.0
    d._last_status_text = "Status: reading..."
    mock_ctrl = MagicMock()
    mock_ctrl.available = True
    with patch("gigamate.daemon.AcpiController", return_value=mock_ctrl):
        healed = d._ensure_acpi_controller()
    assert healed is True
    assert d._acpi_controller is mock_ctrl
    assert d._acpi_caps is mock_ctrl.capabilities
    assert d._last_status_text == ""


def test_tray_self_heal_is_throttled():
    """Re-probing the ACPI backend must not spam AcpiController() every tick."""
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    d._acpi_controller = None
    d._acpi_retry_at = 0.0
    with patch("gigamate.daemon.AcpiController", return_value=MagicMock(available=False)) as mock_cls:
        d._ensure_acpi_controller()  # first attempt
        d._acpi_retry_at = 9999999999.0  # now inside the throttle window
        d._ensure_acpi_controller()  # second attempt must be skipped
        assert mock_cls.call_count == 1


def test_settings_page_has_repair_driver_button(qapp):
    from gigamate.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    assert page.btn_repair_driver is not None
    assert page.btn_restart_service is not None


def test_settings_repair_finished_parses_json(qapp):
    """A successful helper run restarts the tray and refreshes capabilities."""
    import json as json_mod

    from gigamate.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    page._repair_output = [json_mod.dumps({"ok": True, "version": "3.1.1"})]
    with patch("gigamate.ui.pages.settings_page.subprocess.run") as mock_run, \
         patch("gigamate.ui.pages.settings_page.invalidate_capabilities") as mock_inv, \
         patch.object(page, "reload_from_config") as mock_reload, \
         patch.object(page, "_show_repair_info") as mock_show:
        page._on_repair_finished(0, 0)
        assert mock_inv.called
        assert mock_reload.called
        args = [c.args[0] for c in mock_run.call_args_list]
        assert any("systemctl" in a and "gigamate.service" in a for a in args)
        mock_show.assert_called_once_with("Repair complete", "The ACPI kernel driver was rebuilt and reloaded.")


def test_battery_page_when_battery_missing(qapp):
    """Verify BatteryPage gracefully handles desktop / continuous AC mode."""
    from gigamate.ui.pages.battery_page import BatteryPage
    from gigamate.battery import BatteryInfo

    mock_info = BatteryInfo(present=False, ac_online=True)
    with patch("gigamate.ui.pages.battery_page.get_battery_manager") as mock_getter:
        mock_mgr = MagicMock()
        mock_mgr.is_available = False
        mock_mgr.is_charge_limit_supported.return_value = False
        mock_mgr.get_battery_info.return_value = mock_info
        mock_getter.return_value = mock_mgr

        page = BatteryPage()
        page.show()
        qapp.processEvents()
        assert page.battery_tank.isVisible() is False
        assert page.progress_bar.isVisible() is False
        assert page.slider_container.isVisible() is False
        assert page.unsupported_notice.isVisible() is True
        assert "Continuous AC" in page.notice_title.text()


def test_battery_page_when_charge_limit_unsupported(qapp):
    """Verify BatteryPage shows diagnostics but hides limiter when firmware lacks charge registers."""
    from gigamate.ui.pages.battery_page import BatteryPage
    from gigamate.battery import BatteryInfo

    mock_info = BatteryInfo(present=True, capacity=85, status="Discharging", ac_online=False)
    with patch("gigamate.ui.pages.battery_page.get_battery_manager") as mock_getter:
        mock_mgr = MagicMock()
        mock_mgr.is_available = True
        mock_mgr.is_charge_limit_supported.return_value = False
        mock_mgr.get_battery_info.return_value = mock_info
        mock_getter.return_value = mock_mgr

        page = BatteryPage()
        page.show()
        qapp.processEvents()
        assert page.battery_tank.isVisible() is True
        assert page.progress_bar.isVisible() is True
        assert page.slider_container.isVisible() is False
        assert page.unsupported_notice.isVisible() is True
        assert "Hardware Charge Limiting Unavailable" in page.notice_title.text()


def test_dashboard_page_when_acpi_driver_missing(qapp):
    """Verify DashboardPage shows ACPI driver warning when module is not loaded on Gigabyte laptop."""
    from gigamate.ui.pages.dashboard_page import DashboardPage
    from gigamate.capabilities import HardwareCapabilities

    mock_caps = HardwareCapabilities(
        product_name="GIGABYTE AERO 16",
        is_gigabyte_laptop=True,
        acpi_available=False,
        acpi_backend="none",
        acpi_driver_loaded=False,
        acpi_driver_missing=True,
        has_power_profiles=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=True,
        charge_limit_supported=True,
        battery_name="BAT0",
        keyboard_detected=True,
        keyboard_lamparray_supported=True,
        keyboard_vid_pid=(0x0414, 0x8105),
        has_dgpu=True,
        gpu_name="NVIDIA GPU",
    )

    with patch("gigamate.ui.pages.dashboard_page.detect_system_capabilities", return_value=mock_caps), \
         patch("gigamate.ui.pages.dashboard_page.AcpiController") as mock_ctrl_cls:
        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl_cls.return_value = mock_ctrl

        page = DashboardPage()
        page.show()
        qapp.processEvents()
        assert page.acpi_warning_box.isVisible() is True
        assert "ACPI Kernel Driver" in page.wb_title.text()
        for btn in page._profile_buttons.values():
            assert btn.isEnabled() is False


def test_dashboard_page_when_non_gigabyte(qapp):
    """Verify DashboardPage shows the generic-hardware notice on non-Gigabyte machines."""
    from gigamate.ui.pages.dashboard_page import DashboardPage
    from gigamate.capabilities import HardwareCapabilities
    from gigamate.gpu import GpuState

    mock_caps = HardwareCapabilities(
        product_name="Dell XPS 15",
        is_gigabyte_laptop=False,
        acpi_available=False,
        acpi_backend="none",
        acpi_driver_loaded=False,
        acpi_driver_missing=False,
        has_power_profiles=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=True,
        charge_limit_supported=False,
        battery_name="BAT0",
        keyboard_detected=False,
        keyboard_vid_pid=None,
        has_dgpu=True,
        gpu_name="NVIDIA GPU",
    )

    with patch("gigamate.ui.pages.dashboard_page.detect_system_capabilities", return_value=mock_caps), \
         patch("gigamate.ui.pages.dashboard_page.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.ui.pages.dashboard_page.get_gpu_state", return_value=GpuState(present=False)):
        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl_cls.return_value = mock_ctrl

        page = DashboardPage()
        page.show()
        qapp.processEvents()
        assert page.acpi_warning_box.isVisible() is True
        assert "Non-Gigabyte" in page.wb_title.text()
        for btn in page._profile_buttons.values():
            assert btn.isEnabled() is False


def test_dashboard_page_single_fan(qapp):
    """Verify a single-fan chassis shows one unified System Fan tile."""
    from PyQt6.QtWidgets import QLabel
    from gigamate.ui.pages.dashboard_page import DashboardPage
    from gigamate.acpi import AcpiCapabilities, FanProfile, FanState
    from gigamate.gpu import GpuState

    caps = AcpiCapabilities(
        has_temperature=True,
        has_fan_rpm=True,
        has_fan_duty=True,
        has_power_profiles=True,
        fan_count=1,
        backend="module",
    )
    state = FanState(temp_cpu=52, fan1_rpm=1350, duty_total=32, profile=FanProfile.BALANCED)

    with patch("gigamate.ui.pages.dashboard_page.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.ui.pages.dashboard_page.get_gpu_state", return_value=GpuState(present=False)):
        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        mock_ctrl.capabilities = caps
        mock_ctrl.get_profile.return_value = FanProfile.BALANCED.value
        mock_ctrl.read_state.return_value = state
        mock_ctrl_cls.return_value = mock_ctrl

        page = DashboardPage()
        page.show()
        qapp.processEvents()
        assert page.fan2_tile.isVisible() is False
        title = page.findChild(QLabel, "stat_fan1_rpm_title")
        assert title is not None
        assert title.text() == "System Fan Speed"


def _dashboard_with_profiles(caps, profiles, qapp):
    from unittest.mock import MagicMock, patch
    from gigamate.ui.pages.dashboard_page import DashboardPage
    from gigamate.acpi import FanState
    from gigamate.gpu import GpuState

    with patch("gigamate.ui.pages.dashboard_page.AcpiController") as c_cls, \
         patch("gigamate.ui.pages.dashboard_page.discover_acpi_profiles", return_value=profiles), \
         patch("gigamate.ui.pages.dashboard_page.get_gpu_state",
               return_value=GpuState(present=False)):
        c = MagicMock()
        c.available = True
        c.capabilities = caps
        c.get_profile.return_value = None
        c.read_state.return_value = FanState()
        c_cls.return_value = c
        page = DashboardPage()
        page.show()
        qapp.processEvents()
    return page, c


def test_dashboard_discovered_acpi_profiles(qapp):
    from gigamate.acpi import AcpiCapabilities

    caps = AcpiCapabilities(has_temperature=True, has_fan_rpm=True,
                            has_fan_duty=True, fan_count=2)
    profiles = {0: {"name": "Eco"}, 1: {"name": "Balanced"}, 2: {"name": "Boost"}}
    page, ctrl = _dashboard_with_profiles(caps, profiles, qapp)
    assert sorted(page._profile_buttons.keys()) == [0, 1, 2]
    assert all(b.isEnabled() for b in page._profile_buttons.values())
    assert page.acpi_warning_box.isVisible() is False
    page._profile_buttons[1].click()
    ctrl.set_profile.assert_called_once_with(1)


def test_dashboard_acpi_available_without_profiles_shows_notice(qapp):
    from gigamate.acpi import AcpiCapabilities

    caps = AcpiCapabilities(has_temperature=True, has_fan_rpm=True,
                            has_fan_duty=True, fan_count=2, discovered_profiles={})
    page, _ctrl = _dashboard_with_profiles(caps, {}, qapp)
    assert page._profile_buttons == {}
    assert page.acpi_warning_box.isVisible() is True
    assert "No Power Profiles Exposed" in page.wb_title.text()
    assert "WMBD(0xED)" in page.wb_desc.text()


def test_rgb_page_when_keyboard_not_detected(qapp):
    """Verify RgbPage shows notice when no compatible keyboard is detected."""
    from gigamate.ui.pages.rgb_page import RgbPage
    from gigamate.capabilities import HardwareCapabilities

    mock_caps = HardwareCapabilities(
        product_name="Generic PC",
        is_gigabyte_laptop=False,
        acpi_available=False,
        acpi_backend="none",
        acpi_driver_loaded=False,
        acpi_driver_missing=False,
        has_power_profiles=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=False,
        charge_limit_supported=False,
        battery_name="None",
        keyboard_detected=False,
        keyboard_vid_pid=None,
        has_dgpu=False,
        gpu_name="Integrated Only",
    )

    with patch("gigamate.ui.pages.rgb_page.detect_system_capabilities", return_value=mock_caps):
        page = RgbPage()
        page.show()
        qapp.processEvents()
        assert page.palette_container.isVisible() is False
        assert page.kbd_unsupported_notice.isVisible() is True
        assert page.opts_card.isVisible() is False


def test_main_window_construction_does_not_write_hardware(qapp):
    """Guard against tests mutating real ACPI/USB/battery hardware.

    The shared conftest fixture must keep all hardware side effects inert when
    the app applies persisted settings on launch. We assert both that the
    guarded apply entry point actually ran and that no low-level write
    primitive was reached.
    """
    import gigamate.hardware as hardware
    from gigamate.ui.main_window import MainWindow

    guarded_apply = hardware.apply_hardware_settings

    with patch("gigamate.protocol.set_static") as mock_set_static, \
         patch("gigamate.protocol.set_off") as mock_set_off, \
         patch("gigamate.battery.BatteryManager.set_charge_limit") as mock_set_charge, \
         patch("gigamate.acpi.AcpiController.set_profile") as mock_set_profile:
        win = MainWindow()

        # The launch path must have exercised the guarded reapply hook...
        assert win is not None
        guarded_apply.assert_called_once()
        # ...without ever reaching a real hardware write.
        mock_set_static.assert_not_called()
        mock_set_off.assert_not_called()
        mock_set_charge.assert_not_called()
        mock_set_profile.assert_not_called()


def test_sync_all_from_config_invalidates_capabilities(qapp):
    """Re-probe hardware when the window re-syncs from on-disk config."""
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    with patch("gigamate.ui.main_window.invalidate_capabilities") as mock_invalidate:
        win.sync_all_from_config(invalidate=True)
        mock_invalidate.assert_called_once()








def _onboarding_caps():
    from gigamate.capabilities import HardwareCapabilities
    return HardwareCapabilities(
        product_name="GIGABYTE AERO",
        is_gigabyte_laptop=True,
        acpi_available=False,
        acpi_backend="none",
        acpi_driver_loaded=False,
        acpi_driver_missing=True,
        has_power_profiles=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=2,
        battery_present=True,
        charge_limit_supported=True,
        battery_name="BAT0",
        keyboard_detected=True,
        keyboard_lamparray_supported=True,
        keyboard_vid_pid=(0x0414, 0x8105),
        has_dgpu=True,
        gpu_name="NVIDIA GPU",
    )


def test_onboarding_new_user_writes_optin(qapp):
    from gigamate.ui.onboarding import OnboardingWizard
    from gigamate.config import load as load_config

    with patch("gigamate.ui.onboarding.detect_system_capabilities", return_value=_onboarding_caps()), \
         patch("gigamate.ui.onboarding.has_user_config", return_value=False):
        wiz = OnboardingWizard()
        assert wiz.mode == "new"
        wiz.p_battery.enable_chk.setChecked(True)
        wiz.p_battery.limit_slider.setValue(70)
        wiz.p_idle.enable_chk.setChecked(True)
        wiz.p_rgb.apply_chk.setChecked(True)
        wiz._apply_choices(complete=True)

    cfg = load_config()
    assert cfg["onboarding_complete"] is True
    assert cfg["charge_limit_enabled"] is True
    assert cfg["charge_limit"] == 70
    assert cfg["idle_off_enabled"] is True
    assert cfg["startup_apply"] is True


def test_onboarding_upgrade_mode_marks_complete(qapp):
    from gigamate.ui.onboarding import OnboardingWizard
    from gigamate.config import load as load_config

    with patch("gigamate.ui.onboarding.detect_system_capabilities", return_value=_onboarding_caps()), \
         patch("gigamate.ui.onboarding.has_user_config", return_value=True):
        wiz = OnboardingWizard()
        assert wiz.mode == "upgrade"
        # Skip must still record completion (never nags again).
        wiz.reject()

    assert load_config()["onboarding_complete"] is True


def test_onboarding_disable_battery_resets_cap(qapp):
    from unittest.mock import MagicMock, patch
    from gigamate.ui.onboarding import OnboardingWizard
    from gigamate.config import update_config

    # Pre-existing cap enabled at 70%.
    update_config(lambda c: {**c, "charge_limit_enabled": True, "charge_limit": 70})

    mock_mgr = MagicMock()
    mock_mgr.set_charge_limit.return_value = True
    with patch("gigamate.ui.onboarding.detect_system_capabilities", return_value=_onboarding_caps()), \
         patch("gigamate.ui.onboarding.has_user_config", return_value=True), \
         patch("gigamate.battery.get_battery_manager", return_value=mock_mgr):
        wiz = OnboardingWizard()
        wiz.p_battery.initializePage()
        wiz.p_battery.enable_chk.setChecked(False)
        wiz.accept()

    mock_mgr.set_charge_limit.assert_any_call(100)


def test_run_gui_refuses_root(qapp):
    from unittest.mock import patch
    from gigamate.ui import main_window

    with patch.object(main_window.os, "geteuid", return_value=0):
        with pytest.raises(SystemExit):
            main_window.run_gui()


def test_tray_save_config_does_not_clobber_when_clean():
    """Daemon backlight off must not clobber charge_limit or charge_limit_enabled."""
    from gigamate.config import load as load_config, update_config
    from gigamate.daemon import GigaMateDaemon

    update_config(lambda c: {**c, "charge_limit": 80, "charge_limit_enabled": False})
    d = GigaMateDaemon()
    d.set_backlight_pct(0)

    cfg = load_config()
    assert cfg["charge_limit"] == 80
    assert cfg["charge_limit_enabled"] is False
    assert cfg["rgb_enabled"] is False


def test_tray_battery_topup_menu_and_actions():
    """Daemon status_text reflects active 100% Top-Up state."""
    from gigamate.battery import BatteryInfo
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    d._config = {"charge_limit": 80, "charge_limit_enabled": True, "charge_topup_active": True}

    mgr = MagicMock()
    mgr.is_available = True
    mgr.get_battery_info.return_value = BatteryInfo(present=True, capacity=85, ac_online=True)

    with patch("gigamate.daemon.get_battery_manager", return_value=mgr), \
         patch("gigamate.daemon.evaluate_calibration_state", return_value={}):
        snap = d.poll_tick(run_automation=False)
        assert "Batt: 85% (AC - 100% Top-Up)" in snap.status_text


def test_onboarding_unsupported_opts_out_everything(qapp):
    from gigamate.capabilities import HardwareCapabilities
    from gigamate.ui.onboarding import OnboardingWizard
    from gigamate.config import load as load_config

    caps = HardwareCapabilities(
        product_name="Generic PC", is_gigabyte_laptop=False, acpi_available=False,
        acpi_backend="none", acpi_driver_loaded=False, acpi_driver_missing=False,
        has_power_profiles=False, has_temperature=False, has_fan_rpm=False, fan_count=0,
        battery_present=False, charge_limit_supported=False, battery_name="None",
        keyboard_detected=False,
        keyboard_vid_pid=None, has_dgpu=False, gpu_name="Integrated Only",
    )
    with patch("gigamate.ui.onboarding.detect_system_capabilities", return_value=caps), \
         patch("gigamate.ui.onboarding.has_user_config", return_value=False):
        OnboardingWizard().accept()

    cfg = load_config()
    assert cfg["onboarding_complete"] is True
    assert cfg["charge_limit_enabled"] is False
    assert cfg["startup_apply"] is False
    assert cfg["idle_off_enabled"] is False
    assert cfg["sync_system_power"] is False


def test_has_user_config_ignores_config_written_by_30():
    """A config 3.0 created (CLI/tray) carries onboarding_complete -> not upgrade."""
    import json
    from gigamate.ui import onboarding as ob

    ob.CONFIG_FILE.write_text(json.dumps({"colour": "white", "onboarding_complete": False}))
    with patch("gigamate.config._OLD_CONFIG_FILE", ob.CONFIG_FILE.parent / "no-legacy.json"):
        assert ob.has_user_config() is False


def test_has_user_config_treats_pre_30_config_as_upgrade():
    """A 2.0.x config never wrote onboarding_complete -> genuine upgrade."""
    import json
    from gigamate.ui import onboarding as ob

    ob.CONFIG_FILE.write_text(json.dumps({"colour": "white", "brightness": 1}))
    with patch("gigamate.config._OLD_CONFIG_FILE", ob.CONFIG_FILE.parent / "no-legacy.json"):
        assert ob.has_user_config() is True


def test_has_user_config_detects_legacy_dir(tmp_path):
    from gigamate.ui import onboarding as ob

    legacy = tmp_path / "gigabyte-keyboard-rgb" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    with patch("gigamate.config._OLD_CONFIG_FILE", legacy):
        assert ob.has_user_config() is True


def test_has_user_config_false_when_missing_or_corrupt():
    from gigamate.ui import onboarding as ob

    with patch("gigamate.config._OLD_CONFIG_FILE", ob.CONFIG_FILE.parent / "no-legacy.json"):
        ob.CONFIG_FILE.write_text("{ not valid json")
        assert ob.has_user_config() is False
        ob.CONFIG_FILE.unlink()
        assert ob.has_user_config() is False


def test_onboarding_cli_created_config_stays_new(qapp):
    import json
    from gigamate.ui import onboarding as ob

    # Simulate `gigamate rgb ...` persisting a config before the wizard runs.
    ob.CONFIG_FILE.write_text(json.dumps({
        "colour": "blue", "brightness": 2, "startup_apply": False,
        "onboarding_complete": False,
    }))
    with patch("gigamate.ui.onboarding.detect_system_capabilities", return_value=_onboarding_caps()), \
         patch("gigamate.config._OLD_CONFIG_FILE", ob.CONFIG_FILE.parent / "no-legacy.json"):
        wiz = ob.OnboardingWizard()
        assert wiz.mode == "new"
        assert wiz.rerun is False


def test_tray_hotkey_action_dispatch():
    """Hotkey actions route to profile cycling or Center activation."""
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    with patch.object(d, "cycle_profile_hotkey") as cyc, \
         patch.object(d, "open_center") as act:
        d._on_hotkey_action("mode_switch")
        cyc.assert_called_once()
        act.assert_not_called()
        d._on_hotkey_action("open_center")
        act.assert_called_once()
        cyc.assert_called_once()
        # Unknown actions are logged, not raised.
        d._on_hotkey_action("bogus")


def test_tray_resolve_hotkey_specs_precedence():
    """Overrides > built-in defaults."""
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    d._detected_vid, d._detected_pid = 0x0414, 0x8105
    d._config = {"hotkey_overrides": {
        "open_center": {"interface": 2, "report_id": 4, "payload": "000091"},
    }}
    by_action = {s.action: s for s in d._resolve_hotkey_specs()}
    assert by_action["open_center"].payload == bytes.fromhex("000091")
    assert by_action["mode_switch"].payload == bytes.fromhex("000084")

    # No overrides: falls back to built-in default hotkeys.
    d._detected_vid, d._detected_pid = 0x9999, 0x1234
    d._config = {}
    assert len(d._resolve_hotkey_specs()) == 2


class _FakeCenterSocket:
    """Stand-in for socket.socket speaking to a Center instance."""
    instance = None

    def __init__(self, *args, **kwargs):
        _FakeCenterSocket.instance = self
        self.sent = b""

    def settimeout(self, timeout):
        pass

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.sent += data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tray_activate_center_uses_socket():
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    with patch("gigamate.daemon.socket.socket", side_effect=_FakeCenterSocket), \
         patch("gigamate.daemon.subprocess.Popen") as popen:
        d.open_center()
    popen.assert_not_called()
    assert _FakeCenterSocket.instance is not None
    assert _FakeCenterSocket.instance.sent == b"ACTIVATE\n"


def test_tray_activate_center_falls_back_to_spawn():
    from gigamate.daemon import GigaMateDaemon

    d = GigaMateDaemon()
    with patch("gigamate.daemon.socket.socket", side_effect=OSError("no server")), \
         patch("gigamate.daemon.subprocess.Popen") as popen:
        d.open_center()
    popen.assert_called_once()


def test_main_window_init_does_not_wake_suspended_dgpu(qapp):
    """Opening GigaMate Center while the dGPU is suspended must never invoke the NVML helper."""
    from gigamate.ui.main_window import MainWindow
    from gigamate.gpu import GpuState

    fake_gpu = GpuState(present=True, status="suspended", power_state="D3cold", vendor="nvidia")
    with patch("gigamate.ui.pages.gpu_page.get_gpu_state", return_value=fake_gpu), \
         patch("gigamate.capabilities.get_gpu_state", return_value=fake_gpu), \
         patch("gigamate.dgpu_tune.find_nvidia_bdf", return_value="0000:64:00.0"), \
         patch("gigamate.dgpu_tune.runtime_status", return_value="suspended"), \
         patch("gigamate.dgpu_tune.power_state", return_value="D3cold"), \
         patch("gigamate.dgpu_tune._run_helper") as rh:
        win = MainWindow()
        # Ensure that during __init__ and page setups no helper subprocess is run
        rh.assert_not_called()

        # Switch to GPU page
        win.btn_gpu.click()
        rh.assert_not_called()


def test_gate_dialog_auto_dismiss_preserves_auto_mode(qapp):
    """Dismissing or clicking 'Keep dGPU On for Now' in auto_ac_unplug preserves Auto mode."""
    from PyQt6.QtGui import QCloseEvent
    from gigamate import dgpu_gate
    from gigamate.ui.dgpu_gate_dialog import DgpuGateConfirmDialog

    fake_topo = dgpu_gate.DgpuTopology(bdf="0000:01:00.0", audio_bdf=None)
    with patch("gigamate.dgpu_gate.find_dgpu_topology", return_value=fake_topo), \
         patch("gigamate.dgpu_gate.connected_dgpu_displays", return_value=[]), \
         patch("gigamate.dgpu_gate.closable_gpu_holders", return_value=[]), \
         patch("gigamate.dgpu_gate.audio_card_holders", return_value=[]), \
         patch("gigamate.dgpu_gate.set_mode") as mock_set_mode, \
         patch("gigamate.dgpu_gate._publish_policy") as mock_publish:
        dlg = DgpuGateConfirmDialog(
            trigger="auto_ac_unplug",
            target_mode=dgpu_gate.MODE_AUTO,
            start_timers=False,
        )
        assert dlg.revert_btn.text() == "Keep dGPU On for Now"
        dlg.reject()
        mock_set_mode.assert_not_called()
        mock_publish.assert_called_once()

        mock_publish.reset_mock()
        dlg2 = DgpuGateConfirmDialog(
            trigger="auto_ac_unplug",
            target_mode=dgpu_gate.MODE_AUTO,
            start_timers=False,
        )
        dlg2.closeEvent(QCloseEvent())
        mock_set_mode.assert_not_called()
        mock_publish.assert_called_once()


def test_gate_dialog_auto_powers_off_when_holders_clear(qapp):
    """_collect_holders_refresh() triggers _on_confirm_clicked(auto_triggered=True) when ([], []) arrives."""
    from gigamate import dgpu_gate
    from gigamate.ui.dgpu_gate_dialog import DgpuGateConfirmDialog

    fake_topo = dgpu_gate.DgpuTopology(bdf="0000:01:00.0", audio_bdf=None)
    fake_holder = dgpu_gate.HolderProcess(
        pid=1234, name="python3", uid=1000, cmdline="python3 app.py"
    )
    with patch("gigamate.dgpu_gate.find_dgpu_topology", return_value=fake_topo), \
         patch("gigamate.dgpu_gate.connected_dgpu_displays", return_value=[]), \
         patch("gigamate.dgpu_gate.closable_gpu_holders", return_value=[fake_holder]), \
         patch("gigamate.dgpu_gate.audio_card_holders", return_value=[]):
        dlg = DgpuGateConfirmDialog(
            trigger="auto_ac_unplug",
            target_mode=dgpu_gate.MODE_AUTO,
            start_timers=False,
        )
        with patch.object(dlg, "_on_confirm_clicked") as mock_confirm:
            dlg._refresh_queue.put(([], []))
            dlg._collect_holders_refresh()
            mock_confirm.assert_called_once_with(auto_triggered=True)


def test_rgb_page_full_rgb_mode_shows_picker_and_slider(qapp):
    """When supports_full_rgb is True, RgbPage shows 2D pad, 1-100% slider & power button."""
    from types import SimpleNamespace
    from gigamate.ui.pages.rgb_page import RgbPage

    mock_caps = SimpleNamespace(keyboard_detected=True, keyboard_lamparray_supported=True, keyboard_vid_pid=(0x0414, 0x7A42))
    with patch("gigamate.ui.pages.rgb_page.detect_system_capabilities", return_value=mock_caps), \
         patch("gigamate.ui.pages.rgb_page.get_keyboard", return_value=object()), \
         patch("gigamate.ui.pages.rgb_page.set_rgb") as mock_set_rgb, \
         patch("gigamate.ui.pages.rgb_page.set_off") as mock_set_off:
        page = RgbPage()
        page.show()
        qapp.processEvents()

        assert page.full_rgb_container.isVisible() is True
        assert page.color_pad.isVisible() is True
        assert page.brightness_slider.minimum() == 5
        assert page.brightness_slider.maximum() == 100
        assert not hasattr(page, "full_brightness_seg")
        assert page.power_toggle_btn.isVisible() is True

        # Picking a custom colour and then setting slider to 5% preserves rgb_hex
        page.hex_input.setText("#00b4a0")
        page.hex_input.editingFinished.emit()
        page.brightness_slider.setValue(5)
        page.brightness_slider.sliderReleased.emit()
        qapp.processEvents()
        assert page.cfg["rgb_hex"] == "#00b4a0"
        assert page.cfg["brightness_pct"] == 5
        assert page.cfg["rgb_enabled"] is True
        assert page.brightness_slider.value() == 5
        assert page.brightness_readout.text() == "5%"
        mock_set_rgb.assert_called()

        # Clicking power_toggle_btn turns off backlight while preserving rgb_hex & brightness_pct
        page.power_toggle_btn.click()
        qapp.processEvents()
        assert page.cfg["rgb_enabled"] is False
        assert page.cfg["rgb_hex"] == "#00b4a0"
        assert page.cfg["brightness_pct"] == 5
        assert "Off" in page.active_color_badge.text()
        assert page.power_toggle_btn.text() == "Turn On"
        mock_set_off.assert_called()


def test_rgb_page_permission_denied_banner(qapp):
    """When diagnose_lamparray returns permission_denied, RgbPage shows the warning banner."""
    from types import SimpleNamespace
    from gigamate.ui.pages.rgb_page import RgbPage

    mock_caps = SimpleNamespace(keyboard_detected=True, keyboard_lamparray_supported=False, keyboard_vid_pid=(0x0414, 0x7A42))
    diag = {
        "status": "permission_denied",
        "reason": "HID LampArray detected on /dev/hidraw9, but permission was denied.",
        "node": "/dev/hidraw9",
    }
    with patch("gigamate.ui.pages.rgb_page.detect_system_capabilities", return_value=mock_caps), \
         patch("gigamate.ui.pages.rgb_page.diagnose_lamparray", return_value=diag):
        page = RgbPage()
        page.show()
        qapp.processEvents()

        assert page.lamparray_permission_banner.isVisible() is True
        assert "permission was denied" in page.lamparray_permission_banner.body_lbl.text()


def test_rgb_page_unsupported_firmware_shows_legacy_v4_banner(qapp):
    """When diagnose_lamparray returns unsupported_firmware, show legacy_v4_banner and disable controls."""
    from types import SimpleNamespace
    from gigamate.ui.pages.rgb_page import RgbPage

    mock_caps = SimpleNamespace(keyboard_detected=True, keyboard_lamparray_supported=False, keyboard_vid_pid=(0x0414, 0x7A42))
    diag = {
        "status": "unsupported_firmware",
        "reason": "Device does not expose HID LampArray usage page 0x59.",
        "node": None,
    }
    with patch("gigamate.ui.pages.rgb_page.detect_system_capabilities", return_value=mock_caps), \
         patch("gigamate.ui.pages.rgb_page.diagnose_lamparray", return_value=diag):
        page = RgbPage()
        page.show()
        qapp.processEvents()

        assert page.lamparray_permission_banner.isVisible() is False
        assert page.legacy_v4_banner.isVisible() is True
        assert "v4.5.x" in page.legacy_v4_banner.body_lbl.text()


def test_automation_page_profile_rgb_toggle_and_reset(qapp):
    """Verify AutomationPage toggles profile_rgb_enabled and resets profile_rgb_map."""
    from gigamate.config import DEFAULT_CONFIG, load as load_config
    from gigamate.ui.pages.automation_page import AutomationPage

    page = AutomationPage()
    page.profile_rgb_switch.switch.setChecked(True)
    assert load_config()["profile_rgb_enabled"] is True

    with patch("gigamate.ui.pages.automation_page.QColorDialog.getColor") as mock_dlg:
        from PyQt6.QtGui import QColor

        mock_dlg.return_value = QColor("#123456")
        page._pick_profile_color(1)
        assert load_config()["profile_rgb_map"]["1"] == "#123456"
        assert page._swatch_buttons[1].text() == "#123456"

    page._reset_profile_rgb_defaults()
    assert load_config()["profile_rgb_map"] == DEFAULT_CONFIG["profile_rgb_map"]


def test_automation_page_ac_mode_and_tlp_warning(qapp):
    """Verify AC mode selector toggles selectors and shows TLP warning when external AC switching is active."""
    from gigamate.config import load as load_config
    from gigamate.ui.pages.automation_page import AutomationPage

    with patch(
        "gigamate.ui.pages.automation_page.detect_power_managers",
        return_value={
            "managers": ["tlp.service"],
            "has_external_ac_switching": True,
            "recommended_mode": "follow_system",
        },
    ), patch("gigamate.ui.pages.automation_page.sync_system_power", return_value=True):
        page = AutomationPage()
        page.show()
        qapp.processEvents()

        assert set(page._ac_mode_cards.keys()) == {"off", "follow_system", "switch"}
        for key, card in page._ac_mode_cards.items():
            assert card.info_badge.toolTip().strip() != ""
            assert card.recommended_lbl.isVisible() is (key == "follow_system")

        assert "tlp.service" in page.power_managers_lbl.text()
        assert page.tlp_warning_banner.isVisible() is False
        assert page.ac_selectors_widget.isVisible() is False
        assert "Manual only" in page.ac_mode_desc_lbl.text()

        page._ac_mode_cards["switch"].clicked.emit()
        qapp.processEvents()

        assert page._ac_mode_cards["switch"].is_selected() is True
        assert page._ac_mode_cards["off"].is_selected() is False
        assert "GigaMate switches both Gigabyte EC" in page.ac_mode_desc_lbl.text()
        assert load_config()["ac_profile_mode"] == "switch"
        assert page.chk_sync_power.switch.isChecked() is True
        assert page.chk_sync_power.isEnabled() is False
        assert load_config()["sync_system_power"] is True
        assert page.tlp_warning_banner.isVisible() is True
        assert page.ac_selectors_widget.isVisible() is True

        # Switching away from GigaMate-Driven unlocks the toggle again
        page._ac_mode_cards["off"].clicked.emit()
        qapp.processEvents()
        assert page.chk_sync_power.isEnabled() is True


def test_automation_page_sole_controller_banner_when_no_conflicts(qapp):
    """Verify gigamate_sole_banner is shown and conflict widgets are hidden when conflicts is empty in switch mode."""
    from gigamate.ui.pages.automation_page import AutomationPage

    with (
        patch(
            "gigamate.ui.pages.automation_page.detect_power_managers",
            return_value={
                "managers": ["tlp.service"],
                "conflicts": [],
                "has_external_ac_switching": False,
                "tlp_summary": "TLP: tuning backend active (auto-switch paused by GigaMate)",
            },
        ),
        patch("gigamate.ui.pages.automation_page.has_handoff_to_restore", return_value=False),
    ):
        page = AutomationPage()
        page.show()
        page._set_ac_mode("switch")
        qapp.processEvents()

        assert page._ac_mode_cards["switch"].is_selected() is True
        assert page.gigamate_sole_banner.isVisible() is True
        assert page.tlp_warning_banner.isVisible() is False
        assert page.conflict_actions_widget.isVisible() is False
        assert page.ac_selectors_widget.isVisible() is True


def test_automation_page_tlp_conflict_details_and_fallback_follow(qapp):
    """Verify conflict details are shown in switch mode and fallback button switches to follow_system."""
    from gigamate.config import load as load_config
    from gigamate.ui.pages.automation_page import AutomationPage

    with (
        patch(
            "gigamate.ui.pages.automation_page.detect_power_managers",
            return_value={
                "managers": ["tlp.service"],
                "conflicts": [
                    {
                        "id": "tlp",
                        "name": "TLP",
                        "scope": "system",
                        "detail": "TLP_AUTO_SWITCH=2 (AC→performance, Battery→balanced)",
                    }
                ],
                "has_external_ac_switching": True,
                "tlp_summary": "TLP: AC → Performance, Battery → Balanced",
            },
        ),
        patch("gigamate.ui.pages.automation_page.has_handoff_to_restore", return_value=False),
    ):
        page = AutomationPage()
        page.show()
        page._set_ac_mode("switch")
        qapp.processEvents()

        assert page.tlp_warning_banner.isVisible() is True
        assert "TLP_AUTO_SWITCH=2" in page.tlp_warning_banner.body_lbl.text()
        assert page.conflict_actions_widget.isVisible() is True

        page.btn_fallback_follow.click()
        qapp.processEvents()

        assert page._ac_profile_mode == "follow_system"
        assert page._ac_mode_cards["follow_system"].is_selected() is True
        assert "Mirroring system profile changes" in page.ac_mode_desc_lbl.text()
        assert load_config()["ac_profile_mode"] == "follow_system"
        assert page.tlp_warning_banner.isVisible() is False
        assert page.conflict_actions_widget.isVisible() is False


def test_automation_page_restore_handoff_visible_in_follow_system(qapp):
    """Verify btn_restore_handoff is visible in follow_system mode when a handoff exists to restore."""
    from gigamate.ui.pages.automation_page import AutomationPage

    with (
        patch(
            "gigamate.ui.pages.automation_page.detect_power_managers",
            return_value={"managers": ["tlp.service"], "conflicts": [], "has_external_ac_switching": False},
        ),
        patch("gigamate.ui.pages.automation_page.has_handoff_to_restore", return_value=True),
    ):
        page = AutomationPage()
        page.show()
        page._set_ac_mode("follow_system")
        qapp.processEvents()

        assert page.btn_restore_handoff.isVisible() is True
        assert "restored anytime" in page.ac_action_status_lbl.text()


def test_automation_page_shortcuts_copy_and_reset(qapp):
    """Verify SettingsPage copies desktop shortcut commands and resets custom hotkeys."""
    from gigamate.config import load as load_config, save as save_config
    from gigamate.ui.pages.settings_page import SettingsPage

    cfg = load_config()
    cfg["custom_hotkeys"] = {"open_center": {"key_name": "HID iface 3 (0x01)"}}
    save_config(cfg)
    page = SettingsPage()

    assert "HID iface 3 (0x01)" in page._hotkey_labels["open_center"].text()
    page._reset_hotkey("open_center", "GigaMate")
    assert "open_center" not in load_config().get("custom_hotkeys", {})
    assert "Default: GigaMate" in page._hotkey_labels["open_center"].text()

    page._copy_command("gigamate center")
    assert qapp.clipboard().text() == "gigamate center"
    assert "Copied: gigamate center" in page.shortcut_status_lbl.text()


def test_gpu_page_gate_disclaimer_cancel_and_accept(qapp):
    """dGPU Power Gate shows a one-time risk disclaimer; Cancel reverts, Yes persists."""
    from PyQt6.QtWidgets import QMessageBox
    from gigamate.config import load as load_config
    from gigamate.ui.pages.gpu_page import GpuPage

    with patch("gigamate.ui.pages.gpu_page.dgpu_gate.eco_allowed_on_power", return_value=True), \
         patch("gigamate.ui.pages.gpu_page.dgpu_gate.is_gated_off", return_value=False), \
         patch("gigamate.ui.pages.gpu_page.dgpu_gate.read_policy_state", return_value={}):
        page = GpuPage()
        assert load_config().get("dgpu_gate_disclaimer_accepted", False) is False

        # Cancel reverts button selection and does not persist disclaimer acceptance
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QMessageBox.StandardButton.Cancel):
            page._on_gate_mode_clicked("eco")
        assert load_config().get("dgpu_gate_disclaimer_accepted", False) is False
        assert page._gate_buttons["optimus"].isChecked() is True

        # Yes persists dgpu_gate_disclaimer_accepted=True
        mock_bm = MagicMock()
        mock_bm.is_ac_online.return_value = False
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes), \
             patch("gigamate.ui.pages.gpu_page.get_battery_manager", return_value=mock_bm), \
             patch("gigamate.ui.dgpu_gate_dialog.DgpuGateConfirmDialog.exec", return_value=0), \
             patch.object(page, "_refresh"):
            page._on_gate_mode_clicked("auto")
        assert load_config().get("dgpu_gate_disclaimer_accepted") is True


def test_rgb_brightness_debounce_persists_and_apply_profile_preserves(qapp):
    from unittest.mock import MagicMock, patch
    from gigamate import hardware
    from gigamate.config import load as load_config
    from gigamate.ui.pages.rgb_page import RgbPage

    page = RgbPage()
    page.brightness_slider.setValue(42)
    page._on_brightness_debounce()

    cfg = load_config()
    assert cfg["brightness_pct"] == 42

    mock_acpi = MagicMock()
    mock_acpi.available = True
    hardware.apply_profile(1, cfg={**cfg, "profile_rgb_enabled": True}, sync_system=False, acpi=mock_acpi)
    assert load_config()["brightness_pct"] == 42


def test_tray_backlight_full_rgb_persists_brightness_atomically(qapp):
    from gigamate.config import load as load_config
    from gigamate.daemon import GigaMateDaemon
    GigaMateTrayApp = _import_tray_app()

    d = GigaMateDaemon()
    d._has_lamparray = True
    d._idle_dimmed = True
    fake_dev = MagicMock()
    with patch.object(d, "_get_keyboard", return_value=fake_dev), \
         patch("gigamate.daemon.set_rgb") as mock_rgb:
        assert d.set_backlight_pct(35) is True
        assert load_config()["brightness_pct"] == 35
        assert load_config()["brightness"] == 1
        assert d._idle_dimmed is False
        mock_rgb.assert_called_once()

        # Re-clicking an already-checked 100% menu action must still re-apply 100%
        tray = GigaMateTrayApp(daemon=d, start_daemon=False)
        d.set_backlight_pct(100)
        tray._populate_menu()
        assert tray._backlight_actions[100].isChecked() is True
        mock_rgb.reset_mock()
        tray._backlight_actions[100].trigger()
        mock_rgb.assert_called_once()
        assert load_config()["brightness_pct"] == 100


def test_tray_menu_about_to_show_refreshes_dgpu_profile_sensitivity(qapp):
    """Opening the tray menu after dGPU wakes must immediately re-enable Performance/Gaming."""
    from gigamate.daemon import GigaMateDaemon
    GigaMateTrayApp = _import_tray_app()

    discovered = {
        0: {"name": "Quiet"},
        1: {"name": "Balanced"},
        2: {"name": "Performance"},
        3: {"name": "Gaming"},
    }
    d = GigaMateDaemon()
    d._acpi_controller = MagicMock(available=True)
    d.get_discovered_profiles = MagicMock(return_value=discovered)

    allowed = {"val": False}
    with patch("gigamate.daemon.dgpu_gate.high_power_profiles_allowed", side_effect=lambda: allowed["val"]):
        tray = GigaMateTrayApp(daemon=d, start_daemon=False)
        assert tray._profile_actions[2].isEnabled() is False
        assert "(dGPU off)" in tray._profile_actions[2].text()
        assert tray._profile_actions[3].isEnabled() is False

        # AC plugged in -> dGPU wakes up; next menu open must refresh live!
        allowed["val"] = True
        tray.menu.aboutToShow.emit()
        assert tray._profile_actions[2].isEnabled() is True
        assert tray._profile_actions[2].text() == "Performance"
        assert tray._profile_actions[3].isEnabled() is True
        assert tray._profile_actions[3].text() == "Gaming"


def test_dashboard_disable_profiles_and_automation_refresh(qapp):
    from unittest.mock import MagicMock, patch
    from gigamate.acpi import AcpiCapabilities
    from gigamate.config import load as load_config
    from gigamate.ui.pages.automation_page import AutomationPage
    from gigamate.ui.pages.settings_page import SettingsPage

    caps = AcpiCapabilities(
        has_temperature=True,
        has_fan_rpm=True,
        has_fan_duty=True,
        fan_count=2,
    )
    profiles = {
        0: {"name": "Quiet"},
        1: {"name": "Balanced"},
        2: {"name": "Performance"},
        3: {"name": "Gaming"},
    }
    page, ctrl = _dashboard_with_profiles(caps, profiles, qapp)
    assert sorted(page._profile_buttons.keys()) == [0, 1, 2, 3]

    with patch("gigamate.power_limits.discover_acpi_profiles", return_value=profiles), \
         patch("gigamate.ui.pages.settings_page.discover_acpi_profiles", return_value=profiles), \
         patch("gigamate.ui.pages.dashboard_page.discover_acpi_profiles", return_value=profiles):
        settings_page = SettingsPage()
        # Toggle off Gaming (3)
        settings_page._profile_enable_checks[3].setChecked(False)
        qapp.processEvents()
        page.reload_from_config()
        assert load_config()["disabled_profiles"] == [3]
        assert 3 not in page._profile_buttons

        # Disable 0 and 2 -> only 1 remains enabled, and its checkbox is disabled
        settings_page._profile_enable_checks[0].setChecked(False)
        settings_page._profile_enable_checks[2].setChecked(False)
        qapp.processEvents()
        page.reload_from_config()
        assert sorted(page._profile_buttons.keys()) == [1]
        assert settings_page._profile_enable_checks[1].isEnabled() is False

        # Re-enable 0, 2 so only 3 is disabled, then verify AutomationPage.refresh_profiles()
        settings_page._profile_enable_checks[0].setChecked(True)
        settings_page._profile_enable_checks[2].setChecked(True)
        qapp.processEvents()

    with patch("gigamate.power_limits.discover_acpi_profiles", return_value=profiles):
        auto_page = AutomationPage()
        auto_page.refresh_profiles()
        lbl_3, swatch_3, _base = auto_page._profile_color_rows[3]
        assert "(Disabled in Settings)" in lbl_3.text()
        assert swatch_3.isEnabled() is False
        assert swatch_3.text() == "—"
        assert "#ff0000" not in swatch_3.styleSheet().lower()
        ac_ids = [auto_page.ac_profile_combo.itemData(i) for i in range(auto_page.ac_profile_combo.count())]
        bat_ids = [auto_page.battery_profile_combo.itemData(i) for i in range(auto_page.battery_profile_combo.count())]
        assert 3 not in ac_ids
        assert 3 not in bat_ids


def test_tray_hotkey_cycle_profile_skips_disabled(qapp):
    from gigamate.config import update_config
    from gigamate.daemon import GigaMateDaemon

    discovered = {
        0: {"name": "Quiet"},
        1: {"name": "Balanced"},
        2: {"name": "Performance"},
        3: {"name": "Gaming"},
    }
    update_config(lambda c: {**c, "disabled_profiles": [2, 3]})
    with patch("gigamate.daemon.dgpu_gate.high_power_profiles_allowed", return_value=True), \
         patch("gigamate.daemon.hardware.apply_profile", return_value=True) as mock_apply:
        d = GigaMateDaemon()
        d._acpi_controller = MagicMock(available=True)
        d._current_acpi_profile = 1
        d.get_discovered_profiles = MagicMock(return_value=discovered)

        d.cycle_profile_hotkey()
        mock_apply.assert_called_once()
        assert mock_apply.call_args[0][0] == 0


def test_tray_hotkey_cycle_profile_passes_name_and_description_to_osd(qapp):
    from gigamate.config import update_config
    from gigamate.daemon import GigaMateDaemon

    discovered = {
        0: {"name": "Quiet", "description": "Low fan noise, capped GPU power"},
        1: {"name": "Balanced", "description": "Balanced performance and noise"},
        2: {"name": "Performance", "description": "High performance, more fan noise"},
        3: {"name": "Gaming", "description": "Maximum GPU power and cooling"},
    }
    update_config(lambda c: {**c, "disabled_profiles": [2, 3]})
    with patch("gigamate.daemon.dgpu_gate.high_power_profiles_allowed", return_value=True), \
         patch("gigamate.daemon.hardware.apply_profile", return_value=True) as mock_apply, \
         patch("gigamate.daemon.show_profile_osd") as mock_osd:
        d = GigaMateDaemon()
        d._acpi_controller = MagicMock(available=True)
        d._current_acpi_profile = 1
        d.get_discovered_profiles = MagicMock(return_value=discovered)

        d.cycle_profile_hotkey()
        mock_apply.assert_called_once()
        assert mock_apply.call_args[0][0] == 0
        mock_osd.assert_called_once_with("Quiet", "Low fan noise, capped GPU power")


def test_automation_auto_gate_locks_battery_high_power_only(qapp):
    """Auto dGPU Power Gate locks Performance/Gaming only in Battery combo, keeping AC unlocked."""
    from gigamate.config import load as load_config, update_config
    from gigamate.ui.pages.automation_page import AutomationPage

    profiles = {
        0: {"name": "Quiet"},
        1: {"name": "Balanced"},
        2: {"name": "Performance"},
        3: {"name": "Gaming"},
    }
    update_config(
        lambda c: {
            **c,
            "dgpu_gate_disclaimer_accepted": True,
            "dgpu_power_mode": "optimus",
            "ac_action_mode": "switch",
            "ac_profile_id": 2,
            "battery_profile_id": 2,
            "disabled_profiles": [],
        }
    )

    mock_mgr = MagicMock()
    mock_mgr.is_ac_online.return_value = True

    def _fake_set_mode(mode, **_kw):
        update_config(lambda c: {**c, "dgpu_power_mode": mode})

    with patch("gigamate.power_limits.discover_acpi_profiles", return_value=profiles), \
         patch("gigamate.ui.pages.automation_page.dgpu_gate.set_mode", side_effect=_fake_set_mode) as mock_set_mode, \
         patch("gigamate.battery.get_battery_manager", return_value=mock_mgr):
        page = AutomationPage()
        page.show()
        page._set_ac_mode("switch")
        qapp.processEvents()

        # Enable auto_gate_switch
        page.auto_gate_switch.switch.setChecked(True)
        qapp.processEvents()

        mock_set_mode.assert_called_with("auto", apply_now=True)
        bat_ids = [page.battery_profile_combo.itemData(i) for i in range(page.battery_profile_combo.count())]
        ac_ids = [page.ac_profile_combo.itemData(i) for i in range(page.ac_profile_combo.count())]
        assert 2 not in bat_ids
        assert 3 not in bat_ids
        assert 2 in ac_ids
        assert 3 in ac_ids
        assert load_config().get("battery_profile_id") == 0
        assert page.dgpu_battery_guard_banner.isVisible() is True

        # Turn auto_gate_switch OFF -> 2 and 3 unlocked in battery_profile_combo
        page.auto_gate_switch.switch.setChecked(False)
        qapp.processEvents()
        mock_set_mode.assert_called_with("optimus", apply_now=True)
        bat_ids_after = [page.battery_profile_combo.itemData(i) for i in range(page.battery_profile_combo.count())]
        assert 2 in bat_ids_after
        assert 3 in bat_ids_after


def test_aero_instrument_visual_widgets_and_dynamic_accent(qapp):
    """MainWindow navigates all 6 pages, syncs automation, switches dynamic accent, and grabs offscreen."""
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()

    for key in ("automation", "settings", "gpu", "battery", "keyboard", "overview"):
        win.navigate_to(key)
        qapp.processEvents()

    with patch.object(win.page_automation, "reload_from_config") as mock_reload:
        win.sync_all_from_config()
        mock_reload.assert_called_once()

    win._on_profile_accent_changed(0)
    win._on_profile_accent_changed(2)
    qapp.processEvents()

    try:
        for idx in range(6):
            win.stack.setCurrentIndex(idx)
            qapp.processEvents()
            pix = win.stack.currentWidget().grab()
            assert not pix.isNull()

        win.resize(2560, 1360)
        qapp.processEvents()
        for page in (
            win.page_dashboard,
            win.page_battery,
            win.page_rgb,
            win.page_gpu,
            win.page_automation,
            win.page_settings,
        ):
            idx = {
                win.page_dashboard: 0,
                win.page_battery: 1,
                win.page_rgb: 2,
                win.page_gpu: 3,
                win.page_automation: 4,
                win.page_settings: 5,
            }[page]
            win.stack.setCurrentIndex(idx)
            qapp.processEvents()
            assert page.width() <= 1140
            assert page.x() > 0

        # Deep-link navigation scrolls to and highlights the exact target setting
        win.resize(1020, 700)
        qapp.processEvents()
        assert any(tx.text() == "Automation" for _b, _ic, tx, _nm in win._nav_items)
        assert win.page_rgb.profile_link_pill._target_page == "automation:profile_rgb"
        assert win.page_rgb.idle_link_pill._target_page == "automation:idle_sleep"
        assert win.page_gpu.auto_gate_link_pill._target_page == "automation:dgpu_gate"
        assert win.page_gpu.boot_apply_link_pill._target_page == "automation:boot_apply"
        assert win.page_battery.cal_link_pill._target_page == "automation:calibration"

        assert win.page_dashboard.kb_brightness_slider._ignore_wheel is True
        win.navigate_to("automation:idle_sleep")
        qapp.processEvents()
        assert win.stack.currentIndex() == 4
        assert win._last_highlighted_widget is win.page_automation.idle_card
        assert win._scroll_by_idx[4].verticalScrollBar().value() > 0
    finally:
        win.close()
        win.deleteLater()
