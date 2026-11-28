"""Pure HID LampArray keyboard RGB protocol (zero PyUSB)."""

from pathlib import Path
from typing import List, Optional, Tuple

from . import lamparray
from .lamparray import (
    KEYBOARD_VENDORS,
    PALETTE_HEX,
    LampArrayDevice,
    _is_gigabyte_keyboard,
    parse_hex_rgb,
)

VID = 0x0414
BRIGHTNESS_NAMES = ["Off", "Dim", "Full"]


def detect_keyboards() -> List[Tuple[int, int]]:
    """Scan /sys/class/hidraw for Gigabyte keyboards, returning unique (vid, pid) pairs."""
    seen: set = set()
    found: List[Tuple[int, int]] = []
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
            if _is_gigabyte_keyboard(vid, pid, hid_name) and (vid, pid) not in seen:
                seen.add((vid, pid))
                found.append((vid, pid))
        except (OSError, ValueError, StopIteration):
            continue
    return found


def hardware_supports_lamparray(vid: Optional[int] = None, pid: Optional[int] = None) -> bool:
    """Return True if the keyboard hardware exposes HID LampArray with >= 1 lamp."""
    try:
        if vid is not None and pid is not None:
            found = lamparray.find_device(vid, pid)
            if not found:
                return False
            node_path = found[0]
        else:
            detected = lamparray.detect_lamparray_keyboard()
            if not detected:
                return False
            node_path = detected[2]
        return LampArrayDevice(node_path).lamp_count() >= 1
    except Exception:
        return False


def supports_full_rgb(vid: Optional[int] = None, pid: Optional[int] = None) -> bool:
    """Return True if the active or detected keyboard supports 24-bit HID LampArray RGB."""
    return hardware_supports_lamparray(vid, pid)


def get_keyboard(vid: Optional[int] = None, pid: Optional[int] = None) -> Optional[LampArrayDevice]:
    """Return a LampArrayDevice for the detected or specified Gigabyte keyboard, or None."""
    if vid is not None and pid is not None:
        found = lamparray.find_device(vid, pid)
        return LampArrayDevice(found[0]) if found else None
    detected = lamparray.detect_lamparray_keyboard()
    return LampArrayDevice(detected[2]) if detected else None


def set_rgb(dev: Optional[LampArrayDevice], rgb_hex: str, brightness_pct: int = 100) -> bool:
    """Set 24-bit RGB colour from '#RRGGBB' (or named colour) at brightness_pct (0..100)."""
    if dev is None:
        return False
    hex_val = PALETTE_HEX.get(rgb_hex.lower(), rgb_hex) if isinstance(rgb_hex, str) else ""
    rgb = parse_hex_rgb(hex_val)
    if rgb is None:
        return False
    try:
        return dev.set_rgb(rgb[0], rgb[1], rgb[2], brightness_pct)
    except OSError:
        return False


def set_off(dev: Optional[LampArrayDevice]) -> bool:
    """Turn off keyboard RGB via HID LampArray."""
    if dev is None:
        return False
    try:
        return dev.set_rgb(0, 0, 0, 0)
    except OSError:
        return False


def set_static(
    dev: Optional[LampArrayDevice],
    colour: str,
    level: int = 2,
    brightness_pct: Optional[int] = None,
) -> bool:
    """Set static colour (named palette colour or '#RRGGBB') at level (0=off, 1=dim, 2=full) or brightness_pct."""
    if dev is None:
        return False
    if level == 0 or (brightness_pct is not None and brightness_pct <= 0):
        return set_off(dev)
    pct = brightness_pct if brightness_pct is not None else (25 if level == 1 else 100)
    return set_rgb(dev, colour, pct)


def print_detect() -> None:
    """Print detected Gigabyte keyboard and HID LampArray status."""
    kbd = lamparray.detect_gigabyte_hidraw_keyboard()
    if kbd is None:
        print("No Gigabyte/AORUS keyboard detected.")
        return
    vid, pid, hid_name = kbd
    print(f"Detected keyboard: {hid_name or 'Gigabyte/AORUS'} ({vid:04x}:{pid:04x})")
    diag = lamparray.diagnose_lamparray(vid, pid)
    print(f"LampArray status:  {diag.get('status')} — {diag.get('reason')}")
