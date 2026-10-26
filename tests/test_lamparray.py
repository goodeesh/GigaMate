import struct
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gigamate import cli, lamparray, profiles, protocol


_find_device = lamparray.find_device


def test_profile_roundtrip_and_protocol_validation():
    profile = lamparray.make_profile(0x0414, 0x8104, 4)
    restored = profiles.DeviceProfile.from_dict(profile.to_dict())
    assert restored.rgb_protocol == "lamparray"
    assert restored.colour_map == profile.colour_map
    assert profiles.validate_profile(restored) == []
    restored.rgb_protocol = "unknown"
    assert "Unknown RGB protocol: unknown" in profiles.validate_profile(restored)


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


def test_lamparray_never_uses_usb_or_detaches_driver():
    profile = lamparray.make_profile(0x0414, 0x8104, 4)
    with patch("gigamate.lamparray.find_device", return_value=("/dev/hidraw4", 4)), \
         patch("gigamate.protocol.usb.core.find") as usb_find:
        device = protocol.get_keyboard(profile=profile)
    assert isinstance(device, lamparray.LampArrayDevice)
    usb_find.assert_not_called()
    with patch.object(device, "set_colour", return_value=True) as send:
        assert protocol.set_static(device, "red", 1, profile)
        send.assert_called_with(1, 25)
        assert protocol.set_off(device, profile)
        send.assert_called_with(1, 0)
        send.side_effect = PermissionError("denied")
        assert not protocol.set_static(device, "red", 2, profile)
        assert not protocol.set_off(device, profile)


def test_calibrate_lamparray_saves_profile_without_colour_probes():
    args = SimpleNamespace(vid=0x0414, pid=0x8104, interface=4)
    existing = profiles.DeviceProfile(vid=0x0414, pid=0x8104, name="Test keyboard",
                                      acpi=profiles.AcpiConfig())
    with patch("gigamate.lamparray.find_device", return_value=("/dev/hidraw4", 4)), \
         patch("gigamate.lamparray.LampArrayDevice.lamp_count", return_value=1), \
         patch("gigamate.cli.resolve_profile", return_value=existing), \
         patch("gigamate.cli.run_calibrate") as probe, \
         patch("gigamate.cli.get_keyboard") as usb, \
         patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
        cli.cmd_rgb_calibrate(args)
    saved = profiles.load_user_profiles()[(0x0414, 0x8104)]
    assert saved.rgb_protocol == "lamparray"
    assert saved.acpi == existing.acpi
    assert saved.control_interface == 4
    probe.assert_not_called()
    usb.assert_not_called()


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
