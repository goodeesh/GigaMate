import struct
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gigamate import cli, lamparray, protocol


_find_device = lamparray.find_device


@pytest.mark.parametrize("brightness, expected", [(100, [255, 0, 0]), (25, [64, 0, 0]), (0, [0, 0, 0])])
def test_reports_match_gigabyte_kbd(brightness, expected):
    reports = []

    def ioctl(fd, request, data):
        assert fd == 7
        if request == lamparray._ioctl(0x07, 23):
            data[1:3] = type(data)("B", struct.pack("<H", 3))
        else:
            assert request == lamparray._ioctl(0x06, len(data))
            reports.append(list(data))

    with patch("gigamate.lamparray.os.open", return_value=7), \
         patch("gigamate.lamparray.os.close") as close, \
         patch("gigamate.lamparray.fcntl.ioctl", side_effect=ioctl):
        dev = lamparray.LampArrayDevice("/dev/hidraw4")
        assert dev.set_colour(1, brightness)
    assert reports == [[6, 0], [5, 1, 0, 0, 2, 0, *expected, 255]]
    close.assert_called_once_with(7)






def test_descriptor_discovery_matches_ids_and_interface(tmp_path):
    node = tmp_path / "hidraw4"
    usb = tmp_path / "usb"
    hid = usb / "hid"
    hid.mkdir(parents=True)
    node.mkdir()
    (node / "device").symlink_to(hid)
    (usb / "bInterfaceNumber").write_text("04")
    (hid / "uevent").write_text("HID_ID=0003:00000414:00008104\n")
    (hid / "report_descriptor").write_bytes(b"\x05\x59\x85\x01\x85\x05\x85\x06")
    with patch("gigamate.lamparray.Path.glob", return_value=[node]):
        assert _find_device(0x0414, 0x8104, 4) == ("/dev/hidraw4", 4)
        assert _find_device(0x0414, 0x8104, 3) is None
        assert _find_device(0x0414, 0x8105, 4) is None


def test_parse_hex_rgb():
    from gigamate.lamparray import parse_hex_rgb
    assert parse_hex_rgb("#ff8040") == (255, 128, 64)
    assert parse_hex_rgb("00FF00") == (0, 255, 0)
    assert parse_hex_rgb("invalid") is None
    assert parse_hex_rgb("#12345") is None


def test_set_rgb_ultra_dim_floor_and_zero(monkeypatch):
    from gigamate import lamparray
    reports = []

    def fake_ioctl(fd, req, buf):
        if req == lamparray._ioctl(0x07, len(buf)):
            buf[1] = 2
            buf[2] = 0
        else:
            reports.append(bytes(buf))

    monkeypatch.setattr(lamparray.os, "open", lambda path, flags: 42)
    monkeypatch.setattr(lamparray.os, "close", lambda fd: None)
    monkeypatch.setattr(lamparray.fcntl, "ioctl", fake_ioctl)

    dev = lamparray.LampArrayDevice("/dev/hidraw0")
    # 5% of (10, 0, 255): 10*0.05=0.5 -> floor 1, 0 -> 0, 255*0.05=12.75 -> 13
    assert dev.set_rgb(10, 0, 255, brightness_pct=5) is True
    assert reports[-1] == bytes([5, 1, 0, 0, 1, 0, 1, 0, 13, 255])

    reports.clear()
    # 0% -> all zeros
    assert dev.set_rgb(255, 128, 64, brightness_pct=0) is True
    assert reports[-1] == bytes([5, 1, 0, 0, 1, 0, 0, 0, 0, 255])








def _setup_fake_hidraw_sysfs(tmp_path, monkeypatch, vid, pid, descriptor, iface="03"):
    sysfs_root = tmp_path / "sys_hidraw"
    node_dir = sysfs_root / "hidraw9"
    dev_dir = node_dir / "device"
    usb_iface_dir = tmp_path / "usb_iface"
    usb_iface_dir.mkdir(parents=True)
    (usb_iface_dir / "bInterfaceNumber").write_text(f"{iface}\n")
    (usb_iface_dir / "child").mkdir()
    dev_dir.parent.mkdir(parents=True)
    dev_dir.symlink_to(usb_iface_dir / "child")
    (dev_dir / "uevent").write_text(f"HID_ID=0003:{vid:08X}:{pid:08X}\n")
    (dev_dir / "report_descriptor").write_bytes(descriptor)

    orig_glob = lamparray.Path.glob

    def fake_glob(self, pattern):
        if str(self) == "/sys/class/hidraw":
            return [node_dir]
        return orig_glob(self, pattern)

    monkeypatch.setattr(lamparray.Path, "glob", fake_glob)




def test_diagnose_lamparray_permission_denied(tmp_path, monkeypatch):
    desc = b"\x05\x59\x85\x01\x85\x05\x85\x06"
    _setup_fake_hidraw_sysfs(tmp_path, monkeypatch, 0x0414, 0x8105, desc, iface="03")

    def raise_perm(self):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(lamparray.LampArrayDevice, "lamp_count", raise_perm)

    res = lamparray.diagnose_lamparray(0x0414, 0x8105)
    assert res["status"] == "permission_denied"
    assert res["node"] == "/dev/hidraw9"
    assert res["interface"] == 3
    assert res["lamp_count"] is None
    assert "gigamate doctor" in res["reason"]


def test_diagnose_lamparray_unsupported_firmware(tmp_path, monkeypatch):
    desc = b"\x05\x01\x09\x06"  # Generic Desktop Keyboard, no 0x59
    _setup_fake_hidraw_sysfs(tmp_path, monkeypatch, 0x0414, 0x7A42, desc, iface="00")

    res = lamparray.diagnose_lamparray(0x0414, 0x7A42)
    assert res["status"] == "unsupported_firmware"
    assert res["node"] is None
    assert res["lamp_count"] is None


def test_diagnose_lamparray_unsupported_layout(tmp_path, monkeypatch):
    desc = b"\x05\x59\x85\x01\x85\x05"  # Missing Report 6
    _setup_fake_hidraw_sysfs(tmp_path, monkeypatch, 0x0414, 0x9999, desc, iface="02")

    res = lamparray.diagnose_lamparray(0x0414, 0x9999)
    assert res["status"] == "unsupported_layout"
    assert res["node"] == "/dev/hidraw9"
    assert res["interface"] == 2
    assert res["lamp_count"] is None


def test_cli_rgb_static_hex_and_pct(monkeypatch):
    import sys
    from gigamate import cli

    called = {}
    saved_cfg = {}

    monkeypatch.setattr(cli, "get_keyboard", lambda *a, **k: object())

    def _fake_set_rgb(dev, rgb_hex, pct, profile=None, fallback_colour=None, interface=3):
        called["rgb_hex"] = rgb_hex
        called["pct"] = pct
        return True

    def _fake_update_config(mut):
        saved_cfg.update(mut({}))
        return saved_cfg

    monkeypatch.setattr(cli, "set_rgb", _fake_set_rgb)
    monkeypatch.setattr(cli, "update_config", _fake_update_config)
    monkeypatch.setattr(sys, "argv", ["gigamate", "rgb", "static", "#00b4a0", "40"])

    cli.main()
    assert called == {"rgb_hex": "#00b4a0", "pct": 40}
    assert saved_cfg["rgb_hex"] == "#00b4a0"
    assert saved_cfg["brightness_pct"] == 40
    assert saved_cfg["rgb_enabled"] is True




