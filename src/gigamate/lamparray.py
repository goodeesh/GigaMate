"""HID LampArray support for Gigabyte keyboards with the standard report map.

Uses the same hidraw feature reports as the local gigabyte-kbd utility.
No USB kernel driver needs to be detached.
"""

import array
import errno
import fcntl
import os
import struct
from pathlib import Path
from typing import Dict, Optional, Tuple


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

PALETTE_HEX = {name: f"#{r:02x}{g:02x}{b:02x}" for name, (r, g, b) in PALETTE.items()}

KEYBOARD_VENDORS = {0x0414, 0x1044, 0x04D9}


def _is_gigabyte_keyboard(vid: int, pid: int, hid_name: str) -> bool:
    """Return True if (vid, pid, hid_name) identifies a Gigabyte/AORUS keyboard."""
    if vid == 0x0414:
        return True
    if vid == 0x04D9 and pid == 0x8105:
        return True
    if vid == 0x1044:
        upper = hid_name.upper()
        return "GIGABYTE" in upper or "AORUS" in upper
    return False


def detect_gigabyte_hidraw_keyboard() -> Optional[Tuple[int, int, str]]:
    """Scan /sys/class/hidraw/hidraw* for a Gigabyte keyboard, returning (vid, pid, hid_name) or None."""
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = node / "device"
        try:
            lines = (device / "uevent").read_text().splitlines()
            hid_id = next(line.split("=", 1)[1] for line in lines if line.startswith("HID_ID="))
            _, vendor, product = hid_id.split(":")
            vid, pid = int(vendor, 16), int(product, 16)
            if vid not in KEYBOARD_VENDORS:
                continue
            hid_name = next(
                (line.split("=", 1)[1] for line in lines if line.startswith("HID_NAME=")),
                "",
            )
            if _is_gigabyte_keyboard(vid, pid, hid_name):
                return (vid, pid, hid_name)
        except (OSError, ValueError, StopIteration):
            continue
    return None


def detect_lamparray_keyboard() -> Optional[Tuple[int, int, str, int]]:
    """Scan /sys/class/hidraw/hidraw* for a Gigabyte keyboard exposing HID LampArray.

    Returns (vid, pid, node_path, iface) or None.
    """
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = node / "device"
        try:
            lines = (device / "uevent").read_text().splitlines()
            hid_id = next(line.split("=", 1)[1] for line in lines if line.startswith("HID_ID="))
            _, vendor, product = hid_id.split(":")
            vid, pid = int(vendor, 16), int(product, 16)
            if vid not in KEYBOARD_VENDORS:
                continue
            hid_name = next(
                (line.split("=", 1)[1] for line in lines if line.startswith("HID_NAME=")),
                "",
            )
            if not _is_gigabyte_keyboard(vid, pid, hid_name):
                continue
            descriptor = (device / "report_descriptor").read_bytes()
            if b"\x05\x59" not in descriptor or not all(
                bytes([0x85, report]) in descriptor for report in (1, 5, 6)
            ):
                continue
            iface = _read_usb_interface_number(device)
            if iface is None:
                continue
            return (vid, pid, str(Path("/dev") / node.name), iface)
        except (OSError, ValueError, StopIteration):
            continue
    return None


def parse_hex_rgb(hex_color: str) -> Optional[Tuple[int, int, int]]:
    """Parse '#RRGGBB' or 'RRGGBB' into an (r, g, b) tuple in 0..255, or None."""
    if not isinstance(hex_color, str):
        return None
    s = hex_color.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError:
        return None
    return (r, g, b)


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


def _read_hidraw_identity(device: Path) -> Optional[Tuple[int, int]]:
    """Parse (vid, pid) from a sysfs hidraw device's uevent HID_ID line."""
    identity = next(
        line.split("=", 1)[1]
        for line in (device / "uevent").read_text().splitlines()
        if line.startswith("HID_ID=")
    )
    _, vendor, product = identity.split(":")
    return int(vendor, 16), int(product, 16)


def _read_usb_interface_number(device: Path) -> Optional[int]:
    """Return the USB bInterfaceNumber for a sysfs hidraw device, or None."""
    try:
        usb_interface = next(
            parent for parent in device.resolve().parents
            if (parent / "bInterfaceNumber").exists()
        )
        return int((usb_interface / "bInterfaceNumber").read_text(), 16)
    except (OSError, ValueError, StopIteration):
        return None


def diagnose_lamparray(vid: int, pid: int, legacy_mode: bool = False) -> Dict[str, object]:
    """Probe sysfs hidraw nodes for (vid, pid) and return structured LampArray diagnostics."""
    matched_any = False
    layout_mismatch: Optional[Tuple[str, Optional[int]]] = None

    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        device = node / "device"
        try:
            if _read_hidraw_identity(device) != (vid, pid):
                continue
        except (OSError, ValueError, StopIteration):
            continue

        matched_any = True
        node_path = str(Path("/dev") / node.name)
        iface = _read_usb_interface_number(device)

        try:
            descriptor = (device / "report_descriptor").read_bytes()
        except OSError:
            continue

        if b"\x05\x59" not in descriptor:
            continue

        if not all(bytes([0x85, r]) in descriptor for r in (1, 5, 6)):
            if layout_mismatch is None:
                layout_mismatch = (node_path, iface)
            continue

        try:
            count = LampArrayDevice(node_path).lamp_count()
        except PermissionError:
            return {
                "status": "permission_denied",
                "node": node_path,
                "interface": iface,
                "lamp_count": None,
                "reason": (
                    f"HID LampArray found on {node_path} (iface {iface}), "
                    "but access was denied — run 'gigamate doctor'."
                ),
            }
        except (OSError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
                return {
                    "status": "permission_denied",
                    "node": node_path,
                    "interface": iface,
                    "lamp_count": None,
                    "reason": (
                        f"HID LampArray found on {node_path} (iface {iface}), "
                        "but access was denied — run 'gigamate doctor'."
                    ),
                }
            return {
                "status": "error",
                "node": node_path,
                "interface": iface,
                "lamp_count": None,
                "reason": str(exc),
            }

        if count >= 1:
            return {
                "status": "supported",
                "node": node_path,
                "interface": iface,
                "lamp_count": count,
                "reason": (
                    f"24-bit HID LampArray active on {node_path} "
                    f"(iface {iface}, {count} lamp{'s' if count != 1 else ''})."
                ),
            }

    if not matched_any:
        return {
            "status": "not_detected",
            "node": None,
            "interface": None,
            "lamp_count": None,
            "reason": "No hidraw nodes found for keyboard.",
        }

    if layout_mismatch is not None:
        node_path, iface = layout_mismatch
        return {
            "status": "unsupported_layout",
            "node": node_path,
            "interface": iface,
            "lamp_count": None,
            "reason": "HID LampArray interface found, but its report layout differs from supported Reports 1/5/6.",
        }

    return {
        "status": "unsupported_firmware",
        "node": None,
        "interface": None,
        "lamp_count": None,
        "reason": "Keyboard firmware does not expose HID LampArray (Usage Page 0x59); legacy 0x08 keyboards require GigaMate v4.5.x.",
    }


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

    def set_rgb(self, r: int, g: int, b: int, brightness_pct: int = 100) -> bool:
        """Set all lamps to a 24-bit RGB colour scaled by 0..100% brightness."""
        pct = max(0, min(100, int(brightness_pct)))
        channels = []
        for channel in (r, g, b):
            c = max(0, min(255, int(channel)))
            if pct == 0 or c == 0:
                channels.append(0)
            else:
                channels.append(max(1, round(c * pct / 100)))
        fd = os.open(self.path, os.O_RDWR)
        try:
            count = self._attributes(fd)
            # Intensity is on/off on Gigabyte keyboards: scale RGB for dimming.
            for report in ([6, 0], [5, 1, 0, 0, (count - 1) & 255,
                                   (count - 1) >> 8, *channels, 255]):
                data = array.array("B", report)
                fcntl.ioctl(fd, _ioctl(0x06, len(data)), data)
            return True
        finally:
            os.close(fd)

    def set_colour(self, colour, brightness):
        rgb = tuple(PALETTE.values())[colour - 1] if 1 <= colour <= len(PALETTE) else None
        if rgb is None:
            return False
        return self.set_rgb(rgb[0], rgb[1], rgb[2], brightness)
