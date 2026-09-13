import os
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


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    import gigamate.config as config_mod
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    # Start each test with DEFAULT_CONFIG
    config_mod.save(dict(config_mod.DEFAULT_CONFIG))


def test_main_window_creation(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "GigaMate Center"
    assert win.stack.count() == 4

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_rgb.click()
    assert win.stack.currentIndex() == 2

    win.btn_settings.click()
    assert win.stack.currentIndex() == 3

    win.btn_dashboard.click()
    assert win.stack.currentIndex() == 0


def test_dashboard_profile_selection(qapp):
    from gigamate.ui.main_window import MainWindow
    from gigamate.acpi import FanProfile

    win = MainWindow()
    dash = win.page_dashboard

    # Select Quiet
    dash._select_profile(FanProfile.QUIET)
    assert dash._profile_buttons[FanProfile.QUIET].isChecked()
    assert not dash._profile_buttons[FanProfile.GAMING].isChecked()

    # Select Gaming
    dash._select_profile(FanProfile.GAMING)
    assert dash._profile_buttons[FanProfile.GAMING].isChecked()
    assert not dash._profile_buttons[FanProfile.QUIET].isChecked()


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
    cfg["idle_timeout_sec"] = 300
    save_config(cfg)

    # Trigger reload
    win.sync_all_from_config()

    assert "Active: Light Blue" in rgb.active_color_badge.text()
    assert rgb.brightness_buttons[1].isChecked()
    assert rgb.idle_combo.currentData() == 300


def test_tray_sync_from_external_config():
    from unittest.mock import MagicMock, patch
    from gigamate.tray import GigaMateTrayApp

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
    from gigamate.tray import GigaMateTrayApp
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
    from gigamate.tray import GigaMateTrayApp
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
        tray._get_config_mtime = MagicMock(return_value=100.0)

        with patch("gigamate.tray.save_config") as mock_save, \
             patch("gigamate.tray.load_config", return_value={"charge_limit": 80}):
            tray._save_config()

            mock_save.assert_called_once()
            saved = mock_save.call_args[0][0]
            # Must preserve the charge limit set in memory (65), not revert to 80
            assert saved["charge_limit"] == 65
            assert saved["acpi_profile"] == 2


def test_settings_page(qapp):
    from gigamate.ui.pages.settings_page import SettingsPage
    from gigamate.config import load as load_config, save as save_config

    page = SettingsPage()
    assert page.chk_startup_apply is not None
    assert page.chk_sync_power is not None

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

    # Test external config reload
    cfg = load_config()
    cfg["startup_apply"] = False
    cfg["sync_system_power"] = False
    save_config(cfg)

    page.reload_from_config()
    assert page.chk_startup_apply.isChecked() is False
    assert page.chk_sync_power.isChecked() is False


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






