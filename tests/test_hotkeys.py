"""Tests for hotkey detection and listener (hotkeys.py)."""

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from gigamate.hotkeys import HotkeyListener, find_hotkey_hidraw


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


class TestHotkeyListener:
    def test_init(self):
        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, vid=0x0414, pid=0x8105, debounce_sec=0.2)
        assert listener.is_running is False
        assert listener._vid == 0x0414
        assert listener._pid == 0x8105
        assert listener._debounce_sec == 0.2

    def test_start_fails_when_no_device(self):
        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, vid=0x9999, pid=0x9999)
        with patch("gigamate.hotkeys.find_hotkey_hidraw", return_value=None):
            started = listener.start()
            assert started is False
            assert listener.is_running is False

    def test_debounce_and_dispatch(self):
        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, debounce_sec=0.2)
        
        # Test direct dispatching
        with patch("gigamate.hotkeys._HAS_GLIB", False):
            listener._dispatch_mode_switch()
            cb.assert_called_once()

    def test_start_and_stop_lifecycle(self, tmp_path):
        dummy_dev = tmp_path / "hidraw_dummy"
        dummy_dev.touch()

        cb = MagicMock()
        listener = HotkeyListener(on_mode_switch=cb, vid=0x0414, pid=0x8105)

        with patch("gigamate.hotkeys.find_hotkey_hidraw", return_value=str(dummy_dev)):
            started = listener.start()
            assert started is True
            assert listener.is_running is True

            # Stop
            listener.stop()
            assert listener.is_running is False
