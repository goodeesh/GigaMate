import sys
import time
import argparse

from .protocol import (
    COLOURS, PROGRAMS, SPEEDS, get_keyboard, send_command, make_command,
    print_detect, VID, PID, INTERFACE, set_static,
)
from .config import load as load_config, save as save_config


def get_dev(vid, pid):
    dev = get_keyboard(vid, pid)
    if dev is None:
        print(f"Keyboard not found (VID={vid:04X} PID={pid:04X})")
        print("Use 'gigabyte-rgb detect' to scan for compatible keyboards.")
        sys.exit(1)
    return dev


def list_options():
    print("Available effects (static is the only working one for backlight;")
    print("others may break the keyboard and require a USB reset to recover):")
    for name, val in sorted(PROGRAMS.items(), key=lambda x: x[1]):
        print(f"  {name:15s} (0x{val:02X})")
    print()
    print("Available colours:")
    for name, val in sorted(COLOURS.items(), key=lambda x: x[1]):
        print(f"  {name:10s} (0x{val:02X})")
    print()
    print("Available speeds:")
    for name, val in sorted(SPEEDS.items(), key=lambda x: x[1]):
        print(f"  {name:10s} (0x{val:02X})")


def cmd_cycle(args):
    dev = get_dev(args.vid, args.pid)
    print("Cycling through colours (Ctrl+C to stop)...")
    try:
        while True:
            for colour_name in COLOURS:
                print(f"  {colour_name}...", end=" ", flush=True)
                set_static(dev, colour_name, 100, args.interface)
                time.sleep(2)
                print()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


def cmd_set(effect, colour, speed, brightness, interface, vid, pid):
    dev = get_dev(vid, pid)
    if effect != "static":
        print("WARNING: Non-static effects may hang the keyboard firmware.")
        print("Press Ctrl+C within 3 seconds to cancel...")
        time.sleep(3)
    prog_id = PROGRAMS.get(effect)
    if prog_id is None:
        print(f"Unknown effect: {effect}")
        sys.exit(1)
    colour_id = COLOURS.get(colour)
    if colour_id is None:
        print(f"Unknown colour: {colour}")
        sys.exit(1)
    speed_id = SPEEDS.get(speed)
    if speed_id is None:
        try:
            speed_id = int(speed)
            if speed_id < 1 or speed_id > 10:
                raise ValueError
        except ValueError:
            print(f"Invalid speed: {speed}")
            sys.exit(1)
    brightness_byte = min(0x64, max(0x00, brightness * 0x64 // 100))
    cmd = make_command(prog_id, speed_id, brightness_byte, colour_id)
    ok = send_command(dev, cmd, interface)
    if ok:
        print(f"Set to {colour} {effect} (brightness {brightness}%)")
    else:
        print("Failed to send command", file=sys.stderr)
        sys.exit(1)


def cmd_reset(args):
    dev = get_dev(args.vid, args.pid)
    for i in [0, 2, 4]:
        try:
            dev.attach_kernel_driver(i)
        except Exception:
            pass
    print("Keyboard drivers re-attached for interfaces 0/2/4. Typing should work.")


def cmd_detect(args):
    print("Scanning for Gigabyte keyboards...")
    print_detect()


def cmd_off(args):
    dev = get_dev(args.vid, args.pid)
    from .protocol import set_off
    set_off(dev, args.interface)
    print("Keyboard backlight turned off.")


def main():
    parser = argparse.ArgumentParser(
        description="Gigabyte Keyboard RGB Control",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  gigabyte-rgb static purple          Set static purple
  gigabyte-rgb static blue --bright 50  50% blue
  gigabyte-rgb --cycle                 Cycle all colours
  gigabyte-rgb off                     Turn backlight off
  gigabyte-rgb detect                  Scan for compatible keyboards
  gigabyte-rgb --list                  List all options
  gigabyte-rgb --reset                 Re-attach keyboard drivers (fix typing)
        """,
    )

    parser.add_argument("effect", nargs="?", default=None,
                        help="Effect name (default: static)")
    parser.add_argument("colour", nargs="?", default="white",
                        help="Colour name")
    parser.add_argument("--bright", "-b", type=int, default=100,
                        help="Brightness 0-100")
    parser.add_argument("--speed", "-s", default="medium",
                        help="Speed: fastest/fast/medium/slow/slowest or 1-10")
    parser.add_argument("--interface", "-i", type=int, default=INTERFACE,
                        help=f"USB interface (default: {INTERFACE})")
    parser.add_argument("--vid", type=lambda x: int(x, 16), default=VID,
                        help=f"USB vendor ID hex (default: {VID:04X})")
    parser.add_argument("--pid", type=lambda x: int(x, 16), default=PID,
                        help=f"USB product ID hex (default: {PID:04X})")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available options")
    parser.add_argument("--cycle", "-c", action="store_true",
                        help="Cycle through colours")
    parser.add_argument("--reset", "-r", action="store_true",
                        help="Re-attach keyboard drivers")

    args = parser.parse_args()

    if args.list:
        list_options()
        return

    if args.reset:
        cmd_reset(args)
        return

    if args.cycle:
        cmd_cycle(args)
        return

    if args.effect == "detect":
        cmd_detect(args)
        return

    if args.effect == "off":
        cmd_off(args)
        return

    if args.effect is None:
        parser.print_help()
        return

    cmd_set(args.effect, args.colour, args.speed, args.bright,
            args.interface, args.vid, args.pid)


if __name__ == "__main__":
    main()
