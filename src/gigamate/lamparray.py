"""HID LampArray support for Gigabyte keyboards with the standard report map.

Uses the same hidraw feature reports as the local gigabyte-kbd utility.
No USB kernel driver needs to be detached.
"""

import array
import fcntl
import os
import struct
from pathlib import Path


# Profile colour bytes index this palette; brightness bytes are percentages.
PALETTE = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "blue": (0, 0, 255),
    "orange": (255, 128, 0),
    "dark_yellow": (128, 128, 0),
    "purple": (128, 0, 255),
    "light_purple": (192, 128, 255),
    "white": (255, 255, 255),
    "light_blue": (128, 192, 255),
    "blush_pink": (255, 128, 192),
}


def find_device(vid, pid, interface=None):
    """Return (hidraw path, USB interface) matching identity and report layout."""
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = node / "device"
        try:
            identity = next(line.split("=", 1)[1] for line in
                            (device / "uevent").read_text().splitlines()
                            if line.startswith("HID_ID="))
            _, vendor, product = identity.split(":")
            if (int(vendor, 16), int(product, 16)) != (vid, pid):
                continue
            descriptor = (device / "report_descriptor").read_bytes()
            # These report IDs/sizes match the Gigabyte LampArray layout.
            if b"\x05\x59" not in descriptor or not all(
                    bytes([0x85, report]) in descriptor for report in (1, 5, 6)):
                continue
            usb_interface = next(parent for parent in device.resolve().parents
                                 if (parent / "bInterfaceNumber").exists())
            number = int((usb_interface / "bInterfaceNumber").read_text(), 16)
            if interface is None or number == interface:
                return str(Path("/dev") / node.name), number
        except (OSError, ValueError, StopIteration):
            continue
    return None


def _ioctl(number, size):
    return 0xC0000000 | (size << 16) | (ord("H") << 8) | number


class LampArrayDevice:
    def __init__(self, path):
        self.path = path

    @staticmethod
    def _attributes(fd):
        data = array.array("B", [1] + [0] * 22)
        fcntl.ioctl(fd, _ioctl(0x07, len(data)), data)
        count = struct.unpack_from("<H", data, 1)[0]
        if count == 0:
            raise ValueError("LampArray reports zero lamps")
        return count

    def lamp_count(self):
        fd = os.open(self.path, os.O_RDWR)
        try:
            return self._attributes(fd)
        finally:
            os.close(fd)

    def set_colour(self, colour, brightness):
        rgb = tuple(PALETTE.values())[colour - 1] if 1 <= colour <= len(PALETTE) else None
        if rgb is None:
            return False
        fd = os.open(self.path, os.O_RDWR)
        try:
            count = self._attributes(fd)
            # Intensity is on/off on the A16: scale RGB for dimming.
            rgb = [round(channel * max(0, min(100, brightness)) / 100) for channel in rgb]
            for report in ([6, 0], [5, 1, 0, 0, (count - 1) & 255,
                                   (count - 1) >> 8, *rgb, 255]):
                data = array.array("B", report)
                fcntl.ioctl(fd, _ioctl(0x06, len(data)), data)
            return True
        finally:
            os.close(fd)


def make_profile(vid, pid, interface):
    from .profiles import DeviceProfile
    return DeviceProfile(
        vid=vid, pid=pid, name="Gigabyte HID LampArray keyboard",
        interfaces=[interface], control_interface=interface,
        rgb_protocol="lamparray",
        colour_map={name: {0: (index, 0), 1: (index, 25), 2: (index, 100)}
                    for index, name in enumerate(PALETTE, 1)},
    )
