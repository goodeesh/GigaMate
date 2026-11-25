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
