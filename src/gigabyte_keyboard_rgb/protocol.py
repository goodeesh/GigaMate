import sys
import time
import usb.core
import usb.util

VID = 0x0414
PID = 0x8105
INTERFACE = 3

COLOURS = {
    "red":    0x01,
    "green":  0x02,
    "yellow": 0x03,
    "blue":   0x04,
    "orange": 0x05,
    "purple": 0x06,
    "white":  0x07,
}

COLOUR_NAMES = {v: k for k, v in COLOURS.items()}

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


def get_keyboard(vid=VID, pid=PID):
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        return None
    for iface in [1, 3]:
        try:
            if dev.is_kernel_driver_active(iface):
                dev.detach_kernel_driver(iface)
        except Exception:
            pass
    return dev


def send_command(dev, command, interface=INTERFACE):
    try:
        dev.ctrl_transfer(0x21, 0x09, 0x0300, interface, command)
        return True
    except Exception:
        return False


def set_static(dev, colour, brightness=100, interface=INTERFACE):
    colour_id = COLOURS.get(colour)
    if colour_id is None:
        return False
    brightness_byte = min(0x64, max(0x00, brightness * 0x64 // 100))
    cmd = make_command(PROGRAMS["static"], SPEEDS["medium"], brightness_byte, colour_id)
    return send_command(dev, cmd, interface)


def set_off(dev, interface=INTERFACE):
    cmd = make_command(PROGRAMS["static"], SPEEDS["medium"], 0x00, 0x01)
    return send_command(dev, cmd, interface)


def detect_keyboards():
    keyboards = []
    for cfg in usb.core.find(find_all=True, idVendor=VID):
        if cfg.idProduct not in keyboards:
            keyboards.append((cfg.idVendor, cfg.idProduct, cfg.bDeviceClass))
    if not keyboards:
        for dev in usb.core.find(find_all=True):
            try:
                if dev.manufacturer and "GIGABYTE" in str(dev.manufacturer).upper():
                    keyboards.append((dev.idVendor, dev.idProduct, dev.bDeviceClass))
            except Exception:
                pass
    return keyboards


def print_detect():
    from argparse import RawDescriptionHelpFormatter
    kbs = detect_keyboards()
    if not kbs:
        print("No Gigabyte USB-HID keyboards found.")
        print("Try: lsusb | grep -i gigabyte")
        print("If you see a keyboard but the VID is not 0x0414,")
        print("report it so we can add support!")
        return
    for vid, pid, cls in kbs:
        print(f"  VID={vid:04X} PID={pid:04X} class={cls}")
    print()
    print(f"Default: VID={VID:04X} PID={PID:04X} (interface {INTERFACE})")
