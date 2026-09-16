"""Tests for hotkey detection and listener (hotkeys.py)."""

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from gigamate.hotkeys import (
    ACTION_MODE_SWITCH,
    ACTION_OPEN_CENTER,
    HotkeyListener,
    HotkeySpec,
    find_hotkey_hidraw,
    format_report,
    list_hotkey_hidraw,
    match_action,
    match_spec,
    merge_hotkey_specs,
    specs_for_hotkeys,
)


AERO_MODE_SWITCH = {"interface": 2, "report_id": 4, "payload": "000084", "key_name": "Mode"}
AERO_OPEN_CENTER = {"interface": 2, "report_id": 4, "payload": "000091", "key_name": "GigaMate"}


class TestHotkeyDiscovery:
    def test_find_hotkey_hidraw_not_found(self):
        with patch("glob.glob", return_value=[]):
            node = find_hotkey_hidraw(vid=0x1234, pid=0x5678, iface_num=2)
            assert node is None

    def test_find_hotkey_hidraw_matches_sysfs(self, tmp_path):
        hidraw_dir = tmp_path / "sys_class_hidraw"
        hid_node = hidraw_dir / "hidraw2"
        hid_node.mkdir(parents=True)

        target_dev = tmp_path / "devices" / "pci" / "usb3" / "3-4:1.2" / "0003:0414:8105.0003"
        target_dev.mkdir(parents=True)
        (hid_node / "device").symlink_to(target_dev)

        dev_node = tmp_path / "dev" / "hidraw2"
        dev_node.parent.mkdir(parents=True)
        dev_node.touch()

        with patch("glob.glob", return_value=[str(hid_node)]), \
             patch("os.path.exists", side_effect=lambda p: str(p) == str(dev_node) or os.path.exists(p)), \
             patch("os.path.basename", return_value="hidraw2"):

            # Since node path is '/dev/hidraw2', let's mock os.path.exists for '/dev/hidraw2'
            with patch("gigamate.hotkeys.os.path.exists", return_value=True):
                result = find_hotkey_hidraw(vid=0x0414, pid=0x8105, iface_num=2)
                assert result == "/dev/hidraw2"

    def test_list_hotkey_hidraw_enumerates_interfaces(self, tmp_path):
        def make(name, usbpath):
            hid = tmp_path / name
            hid.mkdir()
            target = tmp_path / "devtree" / usbpath
            target.mkdir(parents=True)
            (hid / "device").symlink_to(target)
            return hid

        hid0 = make("hidraw0", "usb3/3-4:1.0/0003:0414:8105.0001")
        hid2 = make("hidraw2", "usb3/3-4:1.2/0003:0414:8105.0003")
        hidx = make("hidraw9", "usb5/5-1:1.0/0003:046D:C084.0001")
        with patch("glob.glob", return_value=[str(hid0), str(hid2), str(hidx)]), \
             patch("gigamate.hotkeys.os.path.exists", return_value=True):
            result = list_hotkey_hidraw(vid=0x0414, pid=0x8105)
        assert sorted(result) == [(0, "/dev/hidraw0"), (2, "/dev/hidraw2")]

    def test_list_hotkey_hidraw_empty(self):
        with patch("glob.glob", return_value=[]):
            assert list_hotkey_hidraw(vid=0x0414, pid=0x8105) == []


class TestHotkeySpec:
    def test_from_dict_valid(self):
        spec = HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH)
        assert spec.interface == 2
        assert spec.report_id == 4
        assert spec.payload == bytes.fromhex("000084")
        assert spec.action == "mode_switch"
        assert spec.key_name == "Mode"

    def test_from_dict_key_name_defaults_to_action(self):
        spec = HotkeySpec.from_dict("open_center", {"interface": 2, "report_id": 4, "payload": "000091"})
        assert spec.key_name == "open_center"

    def test_from_dict_accepts_spaced_upper_hex(self):
        spec = HotkeySpec.from_dict("mode_switch", {"interface": "2", "report_id": "4", "payload": "00 00 84"})
        assert spec.payload == bytes.fromhex("000084")

    @pytest.mark.parametrize("entry", [
        "not-a-dict", None, {},
        {"interface": 2},  # missing report_id
        {"interface": 2, "report_id": 4},  # missing payload
        {"interface": 2, "report_id": 4, "payload": ""},  # empty payload
        {"interface": 2, "report_id": 4, "payload": "00008"},  # odd length
        {"interface": 2, "report_id": 4, "payload": "zz"},  # non-hex
        {"interface": 300, "report_id": 4, "payload": "00"},  # interface out of range
        {"interface": 2, "report_id": 999, "payload": "00"},  # report_id out of range
    ])
    def test_from_dict_invalid(self, entry):
        with pytest.raises(ValueError):
            HotkeySpec.from_dict("mode_switch", entry)

    def test_matches_exact_and_prefix(self):
        spec = HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH)
        assert spec.matches(b"\x04\x00\x00\x84") is True
        # Longer reports match on prefix (payload + trailing bytes).
        assert spec.matches(b"\x04\x00\x00\x84\x00\x00") is True
        assert spec.matches(b"\x04\x00\x00\x91") is False
        assert spec.matches(b"\x05\x00\x00\x84") is False
        assert spec.matches(b"") is False
        assert spec.matches(b"\x04") is False

    def test_match_action_selects_first(self):
        specs = [
            HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH),
            HotkeySpec.from_dict("open_center", AERO_OPEN_CENTER),
        ]
        assert match_action(specs, b"\x04\x00\x00\x84", 2) == "mode_switch"
        assert match_action(specs, b"\x04\x00\x00\x91", 2) == "open_center"
        hit = match_spec(specs, b"\x04\x00\x00\x91", 2)
        assert hit is not None and hit.key_name == "GigaMate"
        # Wrong interface does not match.
        assert match_action(specs, b"\x04\x00\x00\x84", 3) is None
        assert match_action(specs, b"\x04\x00\x00\x84") == "mode_switch"
        assert match_action(specs, b"\x04\x11\x22\x33", 2) is None

    def test_specs_for_hotkeys_skips_invalid(self):
        mapping = {
            "mode_switch": AERO_MODE_SWITCH,
            "broken": {"interface": 2},
        }
        specs = specs_for_hotkeys(mapping)
        assert [s.action for s in specs] == ["mode_switch"]
        assert specs_for_hotkeys(None) == []
        assert specs_for_hotkeys("nope") == []

    def test_merge_first_wins(self):
        merged = merge_hotkey_specs(
            {"mode_switch": {"interface": 9, "report_id": 4, "payload": "00"}},
            {"mode_switch": AERO_MODE_SWITCH, "open_center": AERO_OPEN_CENTER},
        )
        by_action = {s.action: s for s in merged}
        assert by_action["mode_switch"].interface == 9
        assert by_action["open_center"].payload == bytes.fromhex("000091")

    def test_aero_x16_profile_signatures(self):
        """Lock the captured Aero X16 signatures to actions."""
        from gigamate.profiles import load_builtin_profiles

        profile = load_builtin_profiles().get((0x0414, 0x8105))
        assert profile is not None
        specs = specs_for_hotkeys(profile.hotkeys)
        assert match_action(specs, b"\x04\x00\x00\x84", 2) == ACTION_MODE_SWITCH
        assert match_action(specs, b"\x04\x00\x00\x91", 2) == ACTION_OPEN_CENTER

    def test_format_report(self):
        assert format_report(b"\x04\x00\x00\x91") == "04 00 00 91"
        assert format_report(b"") == ""


class TestHotkeyListener:
    def test_init(self):
        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, vid=0x0414, pid=0x8105, debounce_sec=0.2)
        assert listener.is_running is False
        assert listener._vid == 0x0414
        assert listener._pid == 0x8105
        assert listener._debounce_sec == 0.2
        assert listener._specs == []

    def test_start_refuses_without_specs(self):
        """Unmapped models must stay inert: no specs, no device opened."""
        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, vid=0x0414, pid=0x8105)
        with patch("gigamate.hotkeys.find_hotkey_hidraw") as finder:
            assert listener.start() is False
            assert listener.is_running is False
            finder.assert_not_called()

    def test_start_fails_when_no_device(self):
        cb = MagicMock()
        listener = HotkeyListener(
            on_mode_switch=cb, vid=0x9999, pid=0x9999,
            specs=[HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH)],
        )
        with patch("gigamate.hotkeys.find_hotkey_hidraw", return_value=None):
            started = listener.start()
            assert started is False
            assert listener.is_running is False

    def test_debounce_and_dispatch(self):
        mode_cb = MagicMock()
        action_cb = MagicMock()
        listener = HotkeyListener(
            on_mode_switch=mode_cb, on_action=action_cb, debounce_sec=0.2,
            specs=[HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH)],
        )

        # Test direct dispatching
        with patch("gigamate.hotkeys._HAS_GLIB", False):
            listener._dispatch("mode_switch")
            mode_cb.assert_called_once()
            action_cb.assert_called_once_with("mode_switch")

    def test_debounce_is_per_action(self):
        action_cb = MagicMock()
        listener = HotkeyListener(
            on_action=action_cb, debounce_sec=60.0,
            specs=[
                HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH),
                HotkeySpec.from_dict("open_center", AERO_OPEN_CENTER),
            ],
        )
        with patch("gigamate.hotkeys._HAS_GLIB", False):
            listener._dispatch("mode_switch")
            # open_center is unaffected by the mode_switch debounce window.
            listener._dispatch("open_center")
            listener._dispatch("open_center")
        assert action_cb.call_count == 2
        assert [c.args[0] for c in action_cb.call_args_list] == ["mode_switch", "open_center"]

    def test_dispatch_unknown_action_calls_on_action(self):
        action_cb = MagicMock()
        listener = HotkeyListener(on_action=action_cb, debounce_sec=0)
        with patch("gigamate.hotkeys._HAS_GLIB", False):
            listener._dispatch("open_center")
        action_cb.assert_called_once_with("open_center")

    def test_start_and_stop_lifecycle(self, tmp_path):
        dummy_dev = tmp_path / "hidraw_dummy"
        dummy_dev.touch()

        cb = MagicMock()
        listener = HotkeyListener(
            on_mode_switch=cb, vid=0x0414, pid=0x8105,
            specs=[HotkeySpec.from_dict("mode_switch", AERO_MODE_SWITCH)],
        )

        with patch("gigamate.hotkeys.find_hotkey_hidraw", return_value=str(dummy_dev)):
            started = listener.start()
            assert started is True
            assert listener.is_running is True

            # Stop
            listener.stop()
            assert listener.is_running is False
