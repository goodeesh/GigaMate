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
    assert win.stack.count() == 3

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_rgb.click()
    assert win.stack.currentIndex() == 2

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




