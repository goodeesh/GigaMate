import os
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


def test_main_window_creation(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "GigaMate Center"
    assert win.stack.count() == 4

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_gpu.click()
    assert win.stack.currentIndex() == 2

    win.btn_rgb.click()
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
    assert page.limit_slider.value() in (60, 80, 100)

    # Click 60% preset
    page.btn_preset_60.click()
    assert page.limit_slider.value() == 60
    assert "Limit: 60%" in page.slider_label.text()

    # Click 80% preset
    page.btn_preset_80.click()
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


