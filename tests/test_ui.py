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
    """Import the GTK tray app, skipping if the GUI stack is unavailable."""
    pytest.importorskip("gi")
    try:
        from gigamate.tray import GigaMateTrayApp
    except Exception as exc:  # gi.require_version / typelib failures
        pytest.skip(f"GTK/AppIndicator stack unavailable: {exc}")
    return GigaMateTrayApp


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    import gigamate.config as config_mod
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    # main_window/onboarding bound CONFIG_FILE at import; redirect them too.
    monkeypatch.setattr("gigamate.ui.main_window.CONFIG_FILE", cfg_file, raising=False)
    monkeypatch.setattr("gigamate.ui.onboarding.CONFIG_FILE", cfg_file, raising=False)
    # Start each test with DEFAULT_CONFIG
    config_mod.save(dict(config_mod.DEFAULT_CONFIG))


def test_main_window_creation(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "GigaMate Center"
    assert win.stack.count() == 5

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_rgb.click()
    assert win.stack.currentIndex() == 2

    win.btn_gpu.click()
    assert win.stack.currentIndex() == 3

    win.btn_settings.click()
    assert win.stack.currentIndex() == 4

    win.btn_dashboard.click()
    assert win.stack.currentIndex() == 0


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
        "mem_max_clock_mhz": 12001,
        "mem_supported_clocks": [405, 810, 9001, 11001, 12001],
        "mem_lock_api": True, "helper_version": 2,
    }
    state_file = state_file if state_file is not None else {}
    return [
        patch.object(gp.dgpu_tune, "get_state", return_value={"offset_mhz": 0}),
        # The page probes through the NVML gate, so patch both entry points.
        patch.object(gp.dgpu_tune, "probe", return_value=probe),
        patch.object(gp.dgpu_tune, "probe_if_awake", return_value=probe),
        patch.object(gp.dgpu_tune, "read_state_file", return_value=state_file),
        patch.object(gp.dgpu_tune, "wake_holders", return_value=list(holders)),
        patch.object(gp.dgpu_tune, "cached_helper_version", return_value=3),
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


def test_gpu_page_mem_clock_slider(qapp):
    """The memory slider only offers clocks the GPU reported, and stages values."""
    from gigamate import config as config_mod
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    cfg = dict(config_mod.load())
    cfg.update({"dgpu_mem_clock_enabled": True, "dgpu_mem_clock_mhz": 11001})
    config_mod.save(cfg)

    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    state_file = {"applied_mem": 11001}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, state_file=state_file)):
        page.reload_from_config()

    # Idle memory states (405/810) are not offered; steps map to real clocks.
    assert page._mem_choices == [9001, 11001, 12001]
    assert page.mem_slider.minimum() == 0
    assert page.mem_slider.maximum() == 2
    assert page._mem_clock() == 11001
    assert page.mem_val.text() == "11001 MHz"
    assert "pinned at 11001 MHz" in page.mem_status_lbl.text()

    # Dragging only stages: it must not touch the hardware by itself.
    with patch.object(gp.dgpu_tune, "set_desired_config") as sdc:
        page.mem_slider.setValue(2)
        assert sdc.call_count == 0
        assert page.mem_val.text() == "12001 MHz (max)"
        assert "memory clock" in page.apply_hint_lbl.text()
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
        page.mem_slider.setValue(0)  # 9001 MHz

        with patch.object(gp.dgpu_tune, "set_desired_config") as sdc, \
             patch.object(page, "_read_published"):
            page._apply_all()
            sdc.assert_called_once_with(
                enabled=True, offset=120,
                max_enabled=True, max_clock=2500,
                mem_enabled=True, mem_clock=9001,
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
        "dgpu_mem_clock_enabled": True, "dgpu_mem_clock_mhz": 11001,
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
            assert sdc.call_args.kwargs == {"mem_enabled": False, "mem_clock": 0}
    page.deleteLater()


def test_gpu_page_mem_clock_hidden_when_unsupported(qapp):
    """No selectable clocks (or a stale helper) must disable the control."""
    from gigamate.gpu import GpuState
    import gigamate.ui.pages.gpu_page as gp

    page = gp.GpuPage()
    gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    probe = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
             "mem_supported_clocks": [], "mem_max_clock_mhz": None}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, probe=probe)):
        page.reload_from_config()
    assert page._mem_choices == []
    assert not page.mem_slider.isEnabled()
    assert "did not report" in page.mem_support_lbl.text()

    # A helper too old to understand the memory argument must say so.
    page2 = gp.GpuPage()
    probe2 = {"supported": True, "device": "RTX 5060", "gpu_max_clock_mhz": 3090,
              "mem_supported_clocks": [9001, 12001], "mem_max_clock_mhz": 12001}
    with _StartPatch(_gpu_page_patches(gp, gpu_state=gpu, probe=probe2)), \
         patch.object(gp.dgpu_tune, "cached_helper_version", return_value=1):
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
                "mem_supported_clocks": [9001, 11001, 12001],
                "mem_lock_api": True, "helper_version": 2}

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
                "mem_supported_clocks": [9001, 11001, 12001],
                "mem_lock_api": True, "helper_version": 2}

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
             "mem_supported_clocks": [9001, 11001, 12001],
             "mem_lock_api": True, "helper_version": 2}
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
             "mem_supported_clocks": [9001, 11001, 12001],
             "mem_lock_api": True, "helper_version": 2}
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

        for idx, name in enumerate(("dashboard", "battery", "rgb", "gpu", "settings")):
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
    from gigamate.profiles import AcpiConfig, DeviceProfile, ModelMatch

    prof = DeviceProfile(
        vid=0x0414, pid=0x8105, name="Test",
        acpi=AcpiConfig(has_power_profiles=True, profiles={
            "0": {"name": "Quiet"}, "1": {"name": "Balanced"},
            "2": {"name": "Performance"}, "3": {"name": "Gaming"}}))
    match = ModelMatch(prof, "builtin-usb", False)

    with patch("gigamate.ui.pages.dashboard_page.resolve_model", return_value=match):
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
    rgb._set_colour("red")
    assert "border: 2px solid #fc8181" in rgb.color_buttons["red"].styleSheet()
    assert "✓" in rgb.color_buttons["red"].text()
    assert "border: 1px solid #2f3a4e" in rgb.color_buttons["blue"].styleSheet()
    assert "✓" not in rgb.color_buttons["blue"].text()

    # Select Blue
    rgb._set_colour("blue")
    assert "border: 2px solid #63b3ed" in rgb.color_buttons["blue"].styleSheet()
    assert "✓" in rgb.color_buttons["blue"].text()
    # Red must be reset!
    assert "border: 1px solid #2f3a4e" in rgb.color_buttons["red"].styleSheet()
    assert "✓" not in rgb.color_buttons["red"].text()


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


def test_rgb_page_all_profile_colours(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    rgb = win.page_rgb

    # All profile colours must exist as interactive buttons
    expected_colours = [
        "red", "green", "yellow", "blue", "orange", "dark_yellow",
        "purple", "light_purple", "white", "light_blue", "blush_pink"
    ]
    for col in expected_colours:
        assert col in rgb.color_buttons, f"Colour {col} missing from RGB page!"

    # Test selecting yellow (previously missing from Center)
    rgb._set_colour("yellow")
    assert "border: 2px solid #f6e05e" in rgb.color_buttons["yellow"].styleSheet()
    assert "Active: Yellow" in rgb.active_color_badge.text()

    # Test selecting dark_yellow
    rgb._set_colour("dark_yellow")
    assert "border: 2px solid #ecc94b" in rgb.color_buttons["dark_yellow"].styleSheet()
    assert "Active: Dark Yellow" in rgb.active_color_badge.text()


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
    assert rgb.brightness_buttons[1].isChecked()
    # 5m is now a canonical step, so it is preserved exactly.
    assert rgb.idle_combo.currentData() == 300


def test_rgb_page_idle_options_match_tray_steps(qapp):
    """Center and tray must expose the same idle-timeout steps (gigamate.idle)."""
    from gigamate.ui.main_window import MainWindow
    from gigamate.idle import IDLE_TIMEOUT_STEPS

    win = MainWindow()
    rgb = win.page_rgb

    canonical = [sec for sec, _ in IDLE_TIMEOUT_STEPS]
    assert [sec for _, sec in rgb.idle_options] == canonical
    assert 15 not in canonical


def test_tray_sync_from_external_config():
    from unittest.mock import MagicMock, patch
    GigaMateTrayApp = _import_tray_app()

    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._current_colour = "white"
        tray._current_brightness = 2
        tray._current_acpi_profile = 1
        tray._idle_enabled = True
        tray._idle_timeout = 60
        tray._startup_apply = True
        tray._sync_system_power = False
        tray._building = False
        tray._last_config_mtime = 0.0
        tray._colour_items = {"red": MagicMock(), "white": MagicMock()}
        tray._brightness_items = {1: MagicMock(), 2: MagicMock()}
        tray._profile_items = {3: MagicMock()}
        tray._idle_timeout_items = {}
        tray._startup_item = MagicMock()
        tray._sync_power_item = MagicMock()
        tray._idle_parent_item = MagicMock()
        tray._init_idle = MagicMock()

        with patch("gigamate.tray.load_config", return_value={
            "colour": "red",
            "brightness": 1,
            "acpi_profile": 3,
            "idle_off_enabled": True,
            "idle_timeout_sec": 60,
            "startup_apply": False,
            "sync_system_power": True,
        }), patch.object(tray, "_get_config_mtime", return_value=12345.0):
            tray._sync_from_external_config()

            assert tray._current_colour == "red"
            assert tray._current_brightness == 1
            assert tray._current_acpi_profile == 3
            assert tray._startup_apply is False
            assert tray._sync_system_power is True
            tray._colour_items["red"].set_active.assert_called_with(True)
            tray._brightness_items[1].set_active.assert_called_with(True)
            tray._profile_items[3].set_active.assert_called_with(True)


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


def test_tray_on_quit_signals_center():
    GigaMateTrayApp = _import_tray_app()
    from unittest.mock import patch, MagicMock

    with patch("gigamate.tray.load_config", return_value={}), \
         patch("gigamate.tray.get_keyboard", return_value=None), \
         patch("gi.repository.AppIndicator3.Indicator.new_with_path", return_value=MagicMock()):
        tray = GigaMateTrayApp()

        # Mock socket
        mock_socket_cls = MagicMock()
        mock_socket_instance = MagicMock()
        mock_socket_cls.return_value.__enter__.return_value = mock_socket_instance

        with patch("os.path.exists", return_value=True), \
             patch("socket.socket", mock_socket_cls), \
             patch("gi.repository.Gtk.main_quit"):
            tray._on_quit()
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
    GigaMateTrayApp = _import_tray_app()
    from unittest.mock import patch, MagicMock

    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._current_colour = "red"
        tray._current_brightness = 2
        tray._startup_apply = True
        tray._sync_system_power = True
        tray._idle_enabled = True
        tray._idle_timeout = 60
        tray._current_acpi_profile = 2
        tray._config = {"charge_limit": 65, "charge_limit_enabled": True}
        tray._battery_dirty = True  # simulate a tray-originated battery change
        tray._get_config_mtime = MagicMock(return_value=100.0)

        captured = {}

        def fake_update(mutate):
            cfg = mutate({"charge_limit": 80})
            captured.update(cfg)
            return cfg

        with patch("gigamate.tray.update_config", side_effect=fake_update) as mock_update:
            tray._save_config()

            mock_update.assert_called_once()
            # Must preserve the charge limit set in memory (65), not revert to 80
            assert captured["charge_limit"] == 65
            assert captured["acpi_profile"] == 2
            assert captured["colour"] == "red"


def test_settings_page(qapp):
    from gigamate.ui.pages.settings_page import SettingsPage
    from gigamate.config import load as load_config, save as save_config

    page = SettingsPage()
    assert page.chk_startup_apply is not None
    assert page.chk_sync_power is not None
    assert page.chk_dgpu_auto is not None

    # Test toggling startup apply
    page.chk_startup_apply.setChecked(False)
    assert load_config()["startup_apply"] is False

    page.chk_startup_apply.setChecked(True)
    assert load_config()["startup_apply"] is True

    # Test toggling sync power
    page.chk_sync_power.setChecked(False)
    assert load_config()["sync_system_power"] is False

    page.chk_sync_power.setChecked(True)
    assert load_config()["sync_system_power"] is True

    # Test toggling dGPU auto-apply (patched: no real GPU/helper access).
    with patch("gigamate.dgpu_tune.find_nvidia_bdf", return_value=None), \
         patch("gigamate.dgpu_tune._write_state_file"):
        page.chk_dgpu_auto.setChecked(False)
        assert load_config()["dgpu_undervolt_auto"] is False

        page.chk_dgpu_auto.setChecked(True)
        assert load_config()["dgpu_undervolt_auto"] is True

    # Test external config reload
    cfg = load_config()
    cfg["startup_apply"] = False
    cfg["sync_system_power"] = False
    cfg["dgpu_undervolt_auto"] = False
    save_config(cfg)

    page.reload_from_config()
    assert page.chk_startup_apply.isChecked() is False
    assert page.chk_sync_power.isChecked() is False
    assert page.chk_dgpu_auto.isChecked() is False


def test_tray_self_heals_acpi_after_repair():
    """The tray must recover its ACPI status once the driver is back."""
    GigaMateTrayApp = _import_tray_app()

    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._acpi_controller = None
        tray._acpi_caps = None
        tray._acpi_retry_at = 0.0
        tray._last_status_text = "Status: reading..."
        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        with patch("gigamate.tray.AcpiController", return_value=mock_ctrl), \
             patch.object(tray, "_rebuild_menu") as mock_rebuild:
            tray._ensure_acpi_controller()
        assert tray._acpi_controller is mock_ctrl
        assert tray._acpi_caps is mock_ctrl.capabilities
        assert tray._last_status_text is None
        assert mock_rebuild.called


def test_tray_self_heal_is_throttled():
    """Re-probing the ACPI backend must not spam AcpiController() every tick."""
    GigaMateTrayApp = _import_tray_app()

    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._acpi_controller = None
        tray._acpi_retry_at = 0.0
        with patch("gigamate.tray.AcpiController", return_value=MagicMock(available=False)) as mock_cls:
            tray._ensure_acpi_controller()  # first attempt
            tray._acpi_retry_at = 999999.0  # now inside the throttle window
            tray._ensure_acpi_controller()  # second attempt must be skipped
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
        profile_verified=False,
        profile_experimental=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=True,
        charge_limit_supported=True,
        battery_name="BAT0",
        keyboard_detected=True,
        keyboard_profile_loaded=True,
        keyboard_profile_name="Aero 16",
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
        profile_verified=False,
        profile_experimental=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=True,
        charge_limit_supported=False,
        battery_name="BAT0",
        keyboard_detected=False,
        keyboard_profile_loaded=False,
        keyboard_profile_name=None,
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


def _experimental_match():
    from gigamate.profiles import AcpiConfig, DeviceProfile, DmiConfig, ModelMatch

    prof = DeviceProfile(
        name="Gigabyte GAMING A16 / A18 (family)",
        dmi=DmiConfig(product_families=["GIGABYTE GAMING"]),
        acpi=AcpiConfig(
            has_fan_control=True, has_temperature=True, has_power_profiles=True,
            confidence="experimental", fan_count=2,
            profiles={"0": {"name": "Eco"}, "1": {"name": "Balanced"},
                      "2": {"name": "Boost"}},
        ),
    )
    return ModelMatch(prof, "builtin-dmi", True)


def _dashboard_with_match(caps, match, cfg, qapp):
    from unittest.mock import MagicMock, patch
    from gigamate.ui.pages.dashboard_page import DashboardPage
    from gigamate.acpi import FanState
    from gigamate.gpu import GpuState

    with patch("gigamate.ui.pages.dashboard_page.AcpiController") as c_cls, \
         patch("gigamate.ui.pages.dashboard_page.resolve_model", return_value=match), \
         patch("gigamate.ui.pages.dashboard_page.load_config", return_value=cfg), \
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
    return page


def test_dashboard_experimental_hidden_until_consent(qapp):
    from gigamate.acpi import AcpiCapabilities

    caps = AcpiCapabilities(has_temperature=True, has_fan_rpm=True,
                            has_fan_duty=True, fan_count=2)
    page = _dashboard_with_match(caps, _experimental_match(),
                                 {"experimental_profiles_enabled": False}, qapp)
    # Buttons are generated from the model's 3-entry set and disabled.
    assert sorted(page._profile_buttons.keys()) == [0, 1, 2]
    assert all(not b.isEnabled() for b in page._profile_buttons.values())
    assert page.btn_enable_experimental.isVisible() is True


def test_dashboard_experimental_enabled_after_consent(qapp):
    from gigamate.acpi import AcpiCapabilities

    caps = AcpiCapabilities(has_temperature=True, has_fan_rpm=True,
                            has_fan_duty=True, fan_count=2)
    page = _dashboard_with_match(caps, _experimental_match(),
                                 {"experimental_profiles_enabled": True}, qapp)
    assert all(b.isEnabled() for b in page._profile_buttons.values())
    assert page.btn_enable_experimental.isVisible() is False


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
        profile_verified=False,
        profile_experimental=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=False,
        charge_limit_supported=False,
        battery_name="None",
        keyboard_detected=False,
        keyboard_profile_loaded=False,
        keyboard_profile_name=None,
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
        assert page.kbd_uncalibrated_notice.isVisible() is False
        assert page.opts_card.isVisible() is False


def test_rgb_page_when_keyboard_uncalibrated(qapp):
    """Verify RgbPage shows calibration warning when Gigabyte keyboard has no profile."""
    from gigamate.ui.pages.rgb_page import RgbPage
    from gigamate.capabilities import HardwareCapabilities

    mock_caps = HardwareCapabilities(
        product_name="Gigabyte Aorus 15",
        is_gigabyte_laptop=True,
        acpi_available=True,
        acpi_backend="module",
        acpi_driver_loaded=True,
        acpi_driver_missing=False,
        has_power_profiles=True,
        profile_verified=False,
        profile_experimental=False,
        has_temperature=True,
        has_fan_rpm=True,
        fan_count=2,
        battery_present=True,
        charge_limit_supported=True,
        battery_name="BAT0",
        keyboard_detected=True,
        keyboard_profile_loaded=False,
        keyboard_profile_name=None,
        keyboard_vid_pid=(0x0414, 0x8200),
        has_dgpu=True,
        gpu_name="NVIDIA GPU",
    )

    with patch("gigamate.ui.pages.rgb_page.detect_system_capabilities", return_value=mock_caps):
        page = RgbPage()
        page.show()
        qapp.processEvents()
        assert page.palette_container.isVisible() is False
        assert page.kbd_unsupported_notice.isVisible() is False
        assert page.kbd_uncalibrated_notice.isVisible() is True
        assert "0x0414" in page.uncalibrated_title.text()
        assert "0x8200" in page.uncalibrated_title.text()


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
        profile_verified=False,
        profile_experimental=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=2,
        battery_present=True,
        charge_limit_supported=True,
        battery_name="BAT0",
        keyboard_detected=True,
        keyboard_profile_loaded=True,
        keyboard_profile_name="X",
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
    """A tray save with no tray-originated battery change must not write it."""
    GigaMateTrayApp = _import_tray_app()
    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._current_colour = "blue"
        tray._current_brightness = 2
        tray._startup_apply = False
        tray._sync_system_power = False
        tray._idle_enabled = False
        tray._idle_timeout = 60
        tray._current_acpi_profile = None
        tray._config = {"colour": "blue", "charge_limit": 65, "charge_limit_enabled": True}
        tray._battery_dirty = False
        tray._get_config_mtime = MagicMock(return_value=100.0)

        captured = {}

        def fake_update(mutate):
            cfg = mutate({"charge_limit": 80, "charge_limit_enabled": False})
            captured.update(cfg)
            return cfg

        with patch("gigamate.tray.update_config", side_effect=fake_update):
            tray._save_config()

        assert captured["charge_limit"] == 80
        assert captured["charge_limit_enabled"] is False


def test_onboarding_unsupported_opts_out_everything(qapp):
    from gigamate.capabilities import HardwareCapabilities
    from gigamate.ui.onboarding import OnboardingWizard
    from gigamate.config import load as load_config

    caps = HardwareCapabilities(
        product_name="Generic PC", is_gigabyte_laptop=False, acpi_available=False,
        acpi_backend="none", acpi_driver_loaded=False, acpi_driver_missing=False,
        has_power_profiles=False, profile_verified=False, profile_experimental=False, has_temperature=False, has_fan_rpm=False, fan_count=0,
        battery_present=False, charge_limit_supported=False, battery_name="None",
        keyboard_detected=False, keyboard_profile_loaded=False, keyboard_profile_name=None,
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
    GigaMateTrayApp = _import_tray_app()
    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        with patch.object(tray, "_on_hotkey_cycle_profile") as cyc, \
             patch.object(tray, "_activate_center") as act:
            tray._on_hotkey_action("mode_switch")
            cyc.assert_called_once()
            act.assert_not_called()
            tray._on_hotkey_action("open_center")
            act.assert_called_once()
            cyc.assert_called_once()
            # Unknown actions are logged, not raised.
            tray._on_hotkey_action("bogus")


def test_tray_resolve_hotkey_specs_precedence():
    """Overrides > active profile > built-in profile; unknown models get none."""
    GigaMateTrayApp = _import_tray_app()
    from gigamate.profiles import DeviceProfile

    def _profile(name, hotkeys):
        return DeviceProfile(vid=0x0414, pid=0x8105, name=name,
                             interfaces=[1, 3], control_interface=3,
                             colour_map={}, hotkeys=hotkeys)

    builtin = _profile("builtin", {
        "mode_switch": {"interface": 2, "report_id": 4, "payload": "000084"},
    })
    user = _profile("user", {
        "mode_switch": {"interface": 2, "report_id": 4, "payload": "0000ff"},
    })
    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        tray._detected_vid, tray._detected_pid = 0x0414, 0x8105
        tray._profile = user
        tray._config = {"hotkey_overrides": {
            "open_center": {"interface": 2, "report_id": 4, "payload": "000091"},
        }}
        with patch("gigamate.tray.load_builtin_profiles",
                   return_value={(0x0414, 0x8105): builtin}):
            by_action = {s.action: s for s in tray._resolve_hotkey_specs()}
        assert by_action["open_center"].payload == bytes.fromhex("000091")
        assert by_action["mode_switch"].payload == bytes.fromhex("0000ff")

        # Unknown model, no profile, no overrides: no specs, no listener.
        tray._profile = None
        tray._detected_vid, tray._detected_pid = 0x9999, 0x1234
        tray._config = {}
        tray._hotkey_listener = None
        with patch("gigamate.tray.load_builtin_profiles", return_value={}):
            assert tray._resolve_hotkey_specs() == []
            tray._init_hotkeys()
            assert tray._hotkey_listener is None


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
    GigaMateTrayApp = _import_tray_app()
    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        with patch("socket.socket", side_effect=_FakeCenterSocket), \
             patch("gigamate.tray.subprocess.Popen") as popen:
            tray._activate_center()
        popen.assert_not_called()
        assert _FakeCenterSocket.instance is not None
        assert _FakeCenterSocket.instance.sent == b"ACTIVATE\n"


def test_tray_activate_center_falls_back_to_spawn():
    GigaMateTrayApp = _import_tray_app()
    with patch.object(GigaMateTrayApp, "__init__", return_value=None):
        tray = GigaMateTrayApp()
        with patch("socket.socket", side_effect=OSError("no server")), \
             patch("gigamate.tray.subprocess.Popen") as popen:
            tray._activate_center()
        popen.assert_called_once()
