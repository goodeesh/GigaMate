"""Tests for `gigamate hotkeys` CLI commands (watch/list)."""

from argparse import Namespace
from unittest.mock import patch

import pytest

from gigamate.cli import cmd_hotkeys_list, cmd_hotkeys_watch


def _args(**kwargs):
    base = {"vid": None, "pid": None, "interface": None}
    base.update(kwargs)
    return Namespace(**base)


def _aero_profile():
    from gigamate.profiles import DeviceProfile
    return DeviceProfile(
        vid=0x0414, pid=0x8105, name="Gigabyte Aero X16",
        interfaces=[1, 3], control_interface=3,
        colour_map={},
        hotkeys={
            "mode_switch": {"interface": 2, "report_id": 4, "payload": "000084", "key_name": "Mode"},
            "open_center": {"interface": 2, "report_id": 4, "payload": "000091", "key_name": "GigaMate"},
        },
    )


class TestHotkeysList:
    def test_list_shows_mappings(self, capsys):
        with patch("gigamate.profiles.detect_device", return_value=(0x0414, 0x8105)), \
             patch("gigamate.profiles.resolve_profile", return_value=_aero_profile()), \
             patch("gigamate.profiles.load_builtin_profiles", return_value={}), \
             patch("gigamate.config.load", return_value={"hotkey_overrides": {}}):
            cmd_hotkeys_list(_args())
        out = capsys.readouterr().out
        assert "mode_switch (Mode)" in out
        assert "open_center (GigaMate)" in out
        assert "interface 2" in out
        assert "00 00 84" in out

    def test_list_no_mappings(self, capsys):
        with patch("gigamate.profiles.detect_device", return_value=(0x0414, 0x8105)), \
             patch("gigamate.profiles.resolve_profile", return_value=None), \
             patch("gigamate.profiles.load_builtin_profiles", return_value={}), \
             patch("gigamate.config.load", return_value={}):
            cmd_hotkeys_list(_args())
        assert "No hotkey mappings" in capsys.readouterr().out

    def test_list_no_keyboard(self):
        with patch("gigamate.profiles.detect_device", return_value=None):
            with pytest.raises(SystemExit):
                cmd_hotkeys_list(_args())


class TestHotkeysWatch:
    def test_watch_no_keyboard(self):
        with patch("gigamate.profiles.detect_device", return_value=None):
            with pytest.raises(SystemExit):
                cmd_hotkeys_watch(_args())

    def test_watch_no_nodes(self, capsys):
        with patch("gigamate.profiles.detect_device", return_value=(0x0414, 0x8105)), \
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

        with patch("gigamate.profiles.detect_device", return_value=(0x0414, 0x8105)), \
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
