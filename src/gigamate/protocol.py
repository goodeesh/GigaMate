import sys
import time
from typing import Optional

import usb.core
import usb.util

from .profiles import (
    DeviceProfile,
    detect_device,
    resolve_profile,
    all_profiles,
    load_builtin_profiles,
)

VID = 0x0414
PID = 0x8105
INTERFACE = 3

DEFAULT_PROFILE: Optional[DeviceProfile] = resolve_profile(VID, PID)

COLOUR_MAP: dict = DEFAULT_PROFILE.colour_map if DEFAULT_PROFILE else {}
COLOURS: dict = {name: mapping[2][0] for name, mapping in COLOUR_MAP.items()}
COLOUR_NAMES: dict = {v: k for k, v in COLOURS.items()}

BRIGHTNESS_LABELS = {0: "off", 1: "dim", 2: "full"}

PROGRAMS = {
    "static":    0x01,
    "breathing": 0x02,
    "wave":      0x03,
    "fade":      0x04,
    "marquee":   0x05,
    "ripple":    0x06,
    "flash":     0x07,
    "neon":      0x08,
    "rainbow-mq":0x09,
    "raindrop":  0x0A,
    "circle-mq": 0x0B,
    "hedge":     0x0C,
    "rotate":    0x0D,
    "custom1":   0x33,
    "custom2":   0x34,
    "custom3":   0x35,
    "custom4":   0x36,
    "custom5":   0x37,
}

SPEEDS = {
    "fastest": 0x01,
    "fast":    0x03,
    "medium":  0x06,
    "slow":    0x08,
    "slowest": 0x0A,
}


def make_checksum(data):
    return (255 - sum(data[:7])) & 0xFF


def make_command(program, speed, brightness, colour):
    data = [0x08, 0x00, program, speed, brightness, colour, 0x01]
    data.append(make_checksum(data))
    return bytes(data)


def get_keyboard(vid=None, pid=None, profile=None, interfaces=None):
    if profile is not None:
        vid, pid = profile.vid, profile.pid
    if vid is None or pid is None:
        detected = detect_device()
        if detected is None:
            return None
        vid, pid = detected
        if profile is None:
            profile = resolve_profile(vid, pid)
        if interfaces is None and profile is not None:
            interfaces = profile.interfaces
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        return None
    if interfaces is None:
        interfaces = [1, 3]
    for iface in interfaces:
        try:
            if dev.is_kernel_driver_active(iface):
                dev.detach_kernel_driver(iface)
        except (usb.core.USBError, ValueError, NotImplementedError):
            pass
    return dev


def send_command(dev, command, interface=INTERFACE):
    try:
        dev.ctrl_transfer(0x21, 0x09, 0x0300, interface, command)
        return True
    except (usb.core.USBError, ValueError):
        return False


def set_static(dev, colour, level=2, profile=None, interface=INTERFACE):
    if profile is not None:
        cmap = profile.colour_map
    else:
        cmap = COLOUR_MAP
    if colour not in cmap:
        return False
    level = int(level)
    if level not in (0, 1, 2):
        level = 2
    byte5, byte4 = cmap[colour][level]
    cmd = make_command(PROGRAMS["static"], SPEEDS["medium"], byte4, byte5)
    iface = profile.control_interface if profile else interface
    return send_command(dev, cmd, iface)


def set_off(dev, profile=None, interface=INTERFACE):
    cmd = make_command(PROGRAMS["static"], SPEEDS["medium"], 0x00, 0x01)
    iface = profile.control_interface if profile else interface
    return send_command(dev, cmd, iface)


def detect_keyboards():
    keyboards = []
    for cfg in usb.core.find(find_all=True):
        if cfg is None:
            continue
        try:
            vid = cfg.idVendor
            pid = cfg.idProduct
        except (AttributeError, usb.core.USBError):
            continue
        if vid == VID and pid not in keyboards:
            keyboards.append((vid, pid, cfg.bDeviceClass))
    if not keyboards:
        for dev in usb.core.find(find_all=True):
            if dev is None:
                continue
            try:
                mfr = (dev.manufacturer or "").upper()
                if "GIGABYTE" in mfr:
                    keyboards.append((dev.idVendor, dev.idProduct, dev.bDeviceClass))
            except (AttributeError, usb.core.USBError):
                pass
    return keyboards


def print_detect():
    profiles = all_profiles()
    print("Scanning for Gigabyte keyboards...")
    found = detect_device()
    if found is None:
        print("  No Gigabyte USB keyboard detected.")
        print()
        print("  Try: lsusb | grep -i gigabyte")
        print("  If you see a keyboard but it's not detected,")
        print("  open an issue on GitHub so we can investigate!")
        return
    vid, pid = found
    profile = resolve_profile(vid, pid) if found else None
    if profile is not None:
        print(f"  Found: VID={vid:04X} PID={pid:04X}  ({vid:04X}:{pid:04X})")
        print(f"  Model: {profile.name}  \u2705 Supported")
        print()
        print(f"  Available colours: {', '.join(profile.colour_names)}")
        print()
        print("  To use:")
        print(f"    gigamate rgb static <colour>")
    else:
        print(f"  Found: VID={vid:04X} PID={pid:04X}  ({vid:04X}:{pid:04X})")
        print(f"  Model: unknown \u2014 not in profile database")
        print(f"  \u2718 Unsupported. To add support:")
        print(f"    gigamate rgb calibrate")
        print()
        print(f"  Safe action:")
        print(f"    gigamate rgb off")
    print()
    print(f"Known profiles ({len(profiles)}):")
    for key, p in sorted(profiles.items()):
        v, pi = key
        print(f"  {v:04X}:{pi:04X}  {p.name}")
