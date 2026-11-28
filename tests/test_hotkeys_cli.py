"""Tests for `gigamate hotkeys` CLI commands (watch/list)."""

from argparse import Namespace
from unittest.mock import patch

import pytest

from gigamate.cli import cmd_hotkeys_list, cmd_hotkeys_watch


def _args(**kwargs):
    base = {"vid": None, "pid": None, "interface": None}
    base.update(kwargs)
    return Namespace(**base)


class TestHotkeysList:
    def test_list_shows_mappings(self, capsys):
        with patch("gigamate.protocol.detect_keyboards", return_value=[(0x0414, 0x8105, "Gigabyte Aero X16")]), \
             patch("gigamate.config.load", return_value={"hotkey_overrides": {}}):
            cmd_hotkeys_list(_args())
        out = capsys.readouterr().out
        assert "mode_switch" in out
        assert "open_center" in out
        assert "backlight_cycle" in out
        assert "interface 2" in out
        assert "00 00 84" in out

    def test_list_no_mappings(self, capsys):
        with patch("gigamate.protocol.detect_keyboards", return_value=[(0x0414, 0x8105, "Gigabyte Aero X16")]), \
             patch("gigamate.hotkeys.merge_hotkey_specs", return_value=[]), \
             patch("gigamate.config.load", return_value={}):
            cmd_hotkeys_list(_args())
        assert "No hotkey mappings" in capsys.readouterr().out

    def test_list_no_keyboard(self):
        with patch("gigamate.protocol.detect_keyboards", return_value=[]):
            with pytest.raises(SystemExit):
                cmd_hotkeys_list(_args())


class TestHotkeysWatch:
    def test_watch_no_keyboard(self):
        with patch("gigamate.protocol.detect_keyboards", return_value=[]):
            with pytest.raises(SystemExit):
                cmd_hotkeys_watch(_args())

    def test_watch_no_nodes(self, capsys):
        with patch("gigamate.protocol.detect_keyboards", return_value=[(0x0414, 0x8105, "Gigabyte Aero X16")]), \
             patch("gigamate.hotkeys.list_hotkey_hidraw", return_value=[]):
            with pytest.raises(SystemExit):
                cmd_hotkeys_watch(_args())
        assert "No hidraw interfaces" in capsys.readouterr().out

    def test_watch_prints_reports_and_annotations(self, capsys):
        reports = [b"\x04\x00\x00\x91", b"\x04\x11\x22\x33"]
        calls = {"n": 0}

        def fake_select(r, w, x, timeout=None):
            return (list(r), [], [])

        def fake_open(path, flags):
            return 4242

        def fake_read(fd, n):
            if calls["n"] < len(reports):
                out = reports[calls["n"]]
                calls["n"] += 1
                return out
            raise KeyboardInterrupt()

        with patch("gigamate.protocol.detect_keyboards", return_value=[(0x0414, 0x8105, "Gigabyte Aero X16")]), \
             patch("gigamate.hotkeys.list_hotkey_hidraw", return_value=[(2, "/dev/hidraw2")]), \
             patch("os.open", side_effect=fake_open), \
             patch("os.read", side_effect=fake_read), \
             patch("os.close", return_value=None), \
             patch("select.select", side_effect=fake_select):
            cmd_hotkeys_watch(_args())
        out = capsys.readouterr().out
        assert "iface=2" in out
        assert "report_id=0x04" in out
        assert "00 00 91" in out
        assert "open_center" in out


class TestRgbBrightnessCycleCli:
    def test_brightness_cycle_steps_and_persists(self, capsys):
        from gigamate.cli import cmd_rgb_brightness_cycle

        saved = {}

        def fake_update(mut):
            saved.update(mut({"rgb_enabled": True, "brightness_pct": 15, "rgb_hex": "#ffffff"}))
            return saved

        with patch("gigamate.cli.get_keyboard", return_value=object()), \
             patch("gigamate.cli.load_config", return_value={"rgb_enabled": True, "brightness_pct": 15, "rgb_hex": "#ffffff"}), \
             patch("gigamate.cli.set_rgb", return_value=True) as mock_rgb, \
             patch("gigamate.cli.update_config", side_effect=fake_update), \
             patch("gigamate.cli.show_backlight_osd") as mock_osd:
            cmd_rgb_brightness_cycle(_args())

        mock_rgb.assert_called_once()
        assert mock_rgb.call_args.args[2] == 50
        assert saved["rgb_enabled"] is True
        assert saved["brightness_pct"] == 50
        mock_osd.assert_called_once_with(50)
        assert "50%" in capsys.readouterr().out

    def test_brightness_cycle_wraps_to_off(self, capsys):
        from gigamate.cli import cmd_rgb_brightness_cycle

        saved = {}

        def fake_update(mut):
            saved.update(mut({"rgb_enabled": True, "brightness_pct": 100, "rgb_hex": "#ffffff"}))
            return saved

        with patch("gigamate.cli.get_keyboard", return_value=object()), \
             patch("gigamate.cli.load_config", return_value={"rgb_enabled": True, "brightness_pct": 100, "rgb_hex": "#ffffff"}), \
             patch("gigamate.cli.set_off", return_value=True) as mock_off, \
             patch("gigamate.cli.update_config", side_effect=fake_update), \
             patch("gigamate.cli.show_backlight_osd") as mock_osd:
            cmd_rgb_brightness_cycle(_args())

        mock_off.assert_called_once()
        assert saved["rgb_enabled"] is False
        mock_osd.assert_called_once_with(0)
        assert "Off" in capsys.readouterr().out
