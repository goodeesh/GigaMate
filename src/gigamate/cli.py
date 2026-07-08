"""GigaMate — Command-line interface.

Usage:
    gigamate rgb static <colour> [options]    Set keyboard colour
    gigamate rgb off                           Turn backlight off
    gigamate rgb detect                        Scan for keyboards
    gigamate rgb cycle                         Cycle colours
    gigamate rgb calibrate                     Interactive RGB calibration
    gigamate status                            Show hardware status
    gigamate profile [name]                    Show/set power profile
    gigamate profile contribute                Pull Request instructions
    gigamate detect [--acpi]                   Detect hardware
    gigamate calibrate [rgb|acpi|--all]        Run calibration
    gigamate version                           Show version

Legacy (still works):
    gigabyte-rgb static purple                 Same as `gigamate rgb static purple`
    gigabyte-rgb --calibrate                   Same as `gigamate rgb calibrate`
"""

import sys
import os
import time
import argparse
from typing import Optional, List

from .protocol import (
    COLOUR_MAP,
    PROGRAMS,
    SPEEDS,
    BRIGHTNESS_LABELS,
    get_keyboard,
    print_detect,
    set_static,
    set_off,
)
from .profiles import (
    detect_device,
    resolve_profile,
    calibrate as run_calibrate,
    save_user_profile,
    DeviceProfile,
)
from .config import load as load_config, save as save_config
from .acpi import (
    AcpiController,
    FanProfile,
    FanState,
    probe_acpi_capabilities,
    AcpiCapabilities,
)
from .paths import CONFIG_DIR


_LEVEL_NAMES = {"off": 0, "dim": 1, "full": 2}


def _parse_level(val):
    if isinstance(val, str) and val.lower() in _LEVEL_NAMES:
        return _LEVEL_NAMES[val.lower()]
    try:
        v = int(val)
        if v in (0, 1, 2):
            return v
        if v <= 12:
            return 0
        if v <= 62:
            return 1
        return 2
    except (ValueError, TypeError):
        pass
    return 2


def _is_legacy_invocation() -> bool:
    """Check if called via legacy 'gigabyte-rgb' command."""
    prog = os.path.basename(sys.argv[0])
    return "gigabyte-rgb" in prog


def _print_deprecation() -> None:
    """Print deprecation notice for legacy command usage."""
    print("info: 'gigabyte-rgb' is deprecated — use 'gigamate' instead",
          file=sys.stderr)


# ────────────────────────────────────────────
# RGB commands
# ────────────────────────────────────────────


def cmd_rgb_static(args) -> None:
    """Set keyboard to a static colour."""
    colour = args.colour or "light_purple"
    level = _parse_level(args.level)
    speed = args.speed or "medium"

    profile = resolve_profile(args.vid, args.pid)
    dev = get_keyboard(args.vid, args.pid, profile)
    if dev is None:
        vid = args.vid or 0
        pid = args.pid or 0
        print(f"Keyboard not found (VID={vid:04X} PID={pid:04X})")
        print("Use 'gigamate rgb detect' to scan for compatible keyboards.")
        sys.exit(1)

    cmap = profile.colour_map if profile is not None else COLOUR_MAP
    if colour not in cmap:
        if profile is None:
            print(f"Colour '{colour}' unknown — no profile loaded for this model.")
            print("Run 'gigamate rgb calibrate' to set up your model.")
        else:
            print(f"Unknown colour: {colour}")
            print(f"Available: {', '.join(sorted(cmap.keys()))}")
        sys.exit(1)

    ok = set_static(dev, colour, level, profile, args.interface)
    label = BRIGHTNESS_LABELS.get(level, f"level-{level}")
    if ok:
        print(f"Set to {colour} ({label})")
    else:
        print("Failed to send command", file=sys.stderr)
        sys.exit(1)


def cmd_rgb_off(args) -> None:
    """Turn keyboard backlight off."""
    profile = resolve_profile(args.vid, args.pid)
    dev = get_keyboard(args.vid, args.pid, profile)
    if dev is None:
        print("Keyboard not found")
        sys.exit(1)
    set_off(dev, profile)
    print("Keyboard backlight turned off.")


def cmd_rgb_detect(args) -> None:
    """Scan for compatible Gigabyte keyboards."""
    print_detect()


def cmd_rgb_cycle(args) -> None:
    """Cycle through all available colours."""
    profile = resolve_profile(args.vid, args.pid)
    dev = get_keyboard(args.vid, args.pid, profile)
    if dev is None:
        print("Keyboard not found")
        sys.exit(1)
    cmap = profile.colour_map if profile is not None else COLOUR_MAP
    level = _parse_level(args.level)
    print("Cycling through colours (Ctrl+C to stop)...")
    try:
        while True:
            for colour_name in cmap:
                print(f"  {colour_name}...", end=" ", flush=True)
                set_static(dev, colour_name, level, profile)
                time.sleep(2)
                print()
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_rgb_calibrate(args) -> None:
    """Interactive keyboard RGB calibration."""
    if args.vid and args.pid:
        vid, pid = args.vid, args.pid
    else:
        detected = detect_device()
        if detected is None:
            print("No Gigabyte USB keyboard detected.")
            print("Try: lsusb | grep -i gigabyte")
            sys.exit(1)
        vid, pid = detected
    existing = resolve_profile(vid, pid)
    if existing is not None:
        print(f"Model already supported: {existing.name}")
        ans = input("Re-run calibration anyway? [y/N] ").strip().lower()
        if ans != "y":
            return
    dev = get_keyboard(vid, pid)
    if dev is None:
        print(f"Keyboard not found (VID={vid:04X} PID={pid:04X})")
        sys.exit(1)
    new_profile = run_calibrate(dev, vid, pid)
    if new_profile is None:
        print("Calibration cancelled.")
        return
    path = save_user_profile(new_profile)
    print(f"\nProfile saved: {path}")
    print()
    print("To use right now:")
    print(f"  gigamate rgb static <colour>")
    print()
    print("To enable in the tray app:")
    print("  Open the tray menu and click 'Reload profiles'")
    print()
    print("To contribute to the community:")
    print("  gigamate profile contribute")
    print(f"  (attach: {path})")


# ────────────────────────────────────────────
# Status command
# ────────────────────────────────────────────


def cmd_status(args) -> None:
    """Show full hardware status (keyboard + ACPI)."""
    # Keyboard info
    profile = resolve_profile(args.vid, args.pid)
    detected = detect_device()
    vid, pid = (detected or (0, 0)) if detected else (0, 0)

    print("╔══════════════════════════════════╗")
    print("║        GigaMate — Status        ║")
    print("╚══════════════════════════════════╝")
    print()

    # Model name
    if profile is not None:
        print(f"  Model: {profile.name}  ({vid:04X}:{pid:04X})")
    elif detected:
        print(f"  Model: Unknown  ({vid:04X}:{pid:04X})")
    else:
        print(f"  Model: Not detected")

    # Keyboard info
    if profile is not None and profile.has_rgb:
        cfg = load_config()
        colour = cfg.get("colour", "?")
        brightness = cfg.get("brightness", "?")
        bname = {0: "Off", 1: "Dim", 2: "Full"}.get(brightness, str(brightness))
        print(f"  Keyboard: {colour} ({bname})")
    elif detected:
        print(f"  Keyboard: Detected (run 'gigamate rgb calibrate')")
    else:
        print(f"  Keyboard: Not found")

    # ACPI info
    ctrl = AcpiController()
    if ctrl.available:
        caps = ctrl.capabilities
        backend_name = {"module": "kernel module", "acpi_call": "acpi_call", "mock": "mock"}.get(
            caps.backend, caps.backend
        )
        print(f"  ACPI:  ✅ {backend_name} backend active")
        print()

        state = ctrl.read_state()
        if state is not None:
            # Temperatures
            if state.temp_cpu is not None or state.temp_socket is not None:
                print("  ── Temperatures ──")
                if state.temp_cpu is not None:
                    print(f"     CPU Temp:     {state.temp_cpu}°C")
                if state.temp_socket is not None:
                    print(f"     Socket Temp:  {state.temp_socket}°C")
                print()

            # Fans
            if state.fan1_rpm is not None or state.fan2_rpm is not None:
                print("  ── Fans ──")
                if state.fan1_rpm is not None:
                    duty = f"  ({state.duty_cpu}%)" if state.duty_cpu is not None else ""
                    print(f"     CPU Fan:      {state.fan1_rpm} RPM{duty}")
                if state.fan2_rpm is not None:
                    duty = f"  ({state.duty_gpu}%)" if state.duty_gpu is not None else ""
                    print(f"     GPU Fan:      {state.fan2_rpm} RPM{duty}")
                if state.duty_total is not None:
                    print(f"     Total Duty:   {state.duty_total}%")
                print()

            # Profile
            if state.profile is not None:
                pname = _profile_name(state.profile, profile)
                print(f"  ── Power Profile ──")
                print(f"     {pname}  ({state.profile.value})")
                print()
    else:
        print(f"  ACPI:  ❌ Not available")
        print("     Install the kernel module or acpi_call module to enable.")
        print()


# ────────────────────────────────────────────
# Profile commands
# ────────────────────────────────────────────


def _profile_name(profile: FanProfile, device_profile: Optional[DeviceProfile] = None) -> str:
    """Get the human-readable name for a FanProfile value."""
    if device_profile is not None and device_profile.acpi:
        names = device_profile.acpi.profiles
        entry = names.get(str(profile.value), {})
        if "name" in entry:
            return entry["name"]
    return profile.name.capitalize()


def cmd_profile_show(args) -> None:
    """Show current power profile."""
    ctrl = AcpiController()
    if not ctrl.available:
        print("ACPI not available. No power profile control.")
        sys.exit(1)
    profile_val = ctrl.get_profile()
    if profile_val is None:
        print("Current power profile: Unknown")
        return
    profile = resolve_profile(args.vid, args.pid)
    pname = _profile_name(profile_val, profile)
    print(f"Power Profile: {pname}  ({profile_val.value})")


def cmd_profile_set(args, name: str) -> None:
    """Set power profile by name or number."""
    ctrl = AcpiController()
    if not ctrl.available:
        print("ACPI not available. No power profile control.")
        sys.exit(1)

    # Try parsing as number first
    try:
        val = int(name)
        if 0 <= val <= 3:
            fp = FanProfile(val)
            if ctrl.set_profile(fp):
                profile = resolve_profile(args.vid, args.pid)
                pname = _profile_name(fp, profile)
                print(f"Power profile set to: {pname}  ({val})")
                return
            else:
                print(f"Failed to set profile {val}", file=sys.stderr)
                sys.exit(1)
    except ValueError:
        pass

    # Try parsing as name
    try:
        fp = FanProfile.from_name(name)
        if ctrl.set_profile(fp):
            profile = resolve_profile(args.vid, args.pid)
            pname = _profile_name(fp, profile)
            print(f"Power profile set to: {pname}  ({fp.value})")
            return
        else:
            print(f"Failed to set profile '{name}'", file=sys.stderr)
            sys.exit(1)
    except KeyError:
        print(f"Unknown profile: '{name}'")
        print(f"Available: {', '.join(FanProfile.names().keys())}")
        sys.exit(1)


def cmd_profile_contribute(args) -> None:
    """Show Pull Request instructions for contributing a profile."""
    profile = resolve_profile(args.vid, args.pid)
    detected = detect_device()

    if profile is not None:
        vid = profile.vid
        pid = profile.pid
        name = profile.name
    elif detected is not None:
        vid, pid = detected
        name = f"{vid:04X}:{pid:04X}"
    else:
        print("No Gigabyte hardware detected on this system.")
        print("This command should be run on the laptop you want to add support for.")
        sys.exit(1)

    profile_path = CONFIG_DIR / "profiles" / f"{vid:04X}_{pid:04X}.json"
    profile_path_str = str(profile_path)

    print()
    print("🌟  Share your model profile with the community!")
    print()
    print(f"  Model:   {name}")
    print(f"  VID:PID: {vid:04X}:{pid:04X}")
    print(f"  Profile: {profile_path_str}")
    print()
    print("  To contribute this profile as a Pull Request:")
    print()
    print(f"  1. Fork the repository:")
    print(f"     https://github.com/goodeesh/GigaMate/fork")
    print()
    print(f"  2. Clone your fork and add the profile:")
    print(f"     git clone https://github.com/YOUR_USERNAME/GigaMate.git")
    print(f"     cd GigaMate")
    print(f"     cp {profile_path_str} src/gigamate/profile_data/")
    print(f"     git add src/gigamate/profile_data/")
    print(f'     git commit -m "Add support for {name}"')
    print(f"     git push")
    print()
    print(f"  3. Create a Pull Request:")
    print(f"     https://github.com/goodeesh/GigaMate/pulls")
    print()
    print("  Or with GitHub CLI:")
    print(f"     gh pr create --repo goodeesh/GigaMate "
          f"--title \"Add support for {name}\"")
    print()


# ────────────────────────────────────────────
# Detect commands
# ────────────────────────────────────────────


def cmd_detect(args) -> None:
    """Show all detected hardware (keyboard + ACPI)."""
    detected = detect_device()
    profile = resolve_profile(args.vid, args.pid)

    print("GigaMate — Hardware Detection")
    print()

    # Keyboard
    if detected:
        vid, pid = detected
        if profile is not None:
            print(f"  Keyboard: {profile.name}  ({vid:04X}:{pid:04X})")
        else:
            print(f"  Keyboard: Unknown model  ({vid:04X}:{pid:04X})")
    else:
        print(f"  Keyboard: Not detected")
    print()

    # ACPI probe (if --acpi flag, do detailed probe)
    if args.acpi:
        print("  Probing ACPI capabilities...")
        caps = probe_acpi_capabilities()
        _print_acpi_caps(caps)
    else:
        ctrl = AcpiController()
        if ctrl.available:
            caps = ctrl.capabilities
            backend_name = {"module": "kernel module", "acpi_call": "acpi_call", "mock": "mock"}.get(
                caps.backend, caps.backend
            )
            print(f"  ACPI:   ✅ {backend_name} backend")
            print(f"          Temperature:  {'✅' if caps.has_temperature else '❌'}")
            print(f"          Fan RPM:      {'✅' if caps.has_fan_rpm else '❌'}")
            print(f"          Fan Duty:     {'✅' if caps.has_fan_duty else '❌'}")
            print(f"          Power Profiles: {'✅' if caps.has_power_profiles else '❌'}")
            print()
            print("  For detailed probe: gigamate detect --acpi")
        else:
            print(f"  ACPI:   ❌ Not available")


def cmd_detect_acpi(args) -> None:
    """Detailed ACPI capabilities probe."""
    print("GigaMate — ACPI Capability Probe")
    print()

    caps = probe_acpi_capabilities()
    _print_acpi_caps(caps)


def _print_acpi_caps(caps: AcpiCapabilities) -> None:
    """Print ACPI capabilities in a human-readable format."""
    if caps.backend == "none":
        print("  No ACPI interface detected.")
        print()
        print("  To enable ACPI features:")
        print("    1. Install kernel headers and run install.sh")
        print("    2. Or install acpi_call-dkms module")
        return

    backend_name = {"module": "kernel module", "acpi_call": "acpi_call", "mock": "mock"}.get(
        caps.backend, caps.backend
    )
    print(f"  Backend:    {backend_name}")
    print(f"  Temperature: {'✅ Detected' if caps.has_temperature else '❌ Not available'}")
    print(f"  Fan RPM:    {'✅ Detected' if caps.has_fan_rpm else '❌ Not available'}")
    print(f"  Fan Duty:   {'✅ Detected' if caps.has_fan_duty else '❌ Not available'}")
    print(f"  Profiles:   {'✅ Detected' if caps.has_power_profiles else '❌ Not available'}")
    if caps.fan_count > 0:
        print(f"  Fans:       {caps.fan_count}")
    if caps.profile_ids:
        print(f"  Profile IDs: {caps.profile_ids}")
    print()

    if caps.backend in ("module", "acpi_call"):
        print("  To save this configuration to your device profile:")
        print("    1. Create/edit your profile at ~/.config/gigamate/profiles/")
        print("    2. Add the 'acpi' section (see docs/PROFILE_SCHEMA.md)")
        print("    3. Run 'gigamate profile contribute' to share it")
    print()


# ────────────────────────────────────────────
# Calibrate command
# ────────────────────────────────────────────


def cmd_calibrate_rgb(args) -> None:
    """Run keyboard RGB calibration."""
    # Delegate to the existing rgb calibrate command
    cmd_rgb_calibrate(args)


def cmd_calibrate_acpi(args) -> None:
    """Run ACPI capability probe and save to profile."""
    print("GigaMate — ACPI Calibration")
    print()

    detected = detect_device()
    if detected is None:
        print("No Gigabyte keyboard detected. ACPI probe still possible.")
        vid, pid = 0, 0
    else:
        vid, pid = detected
        print(f"Detected keyboard: {vid:04X}:{pid:04X}")

    print()
    print("Probing ACPI capabilities...")
    caps = probe_acpi_capabilities()
    _print_acpi_caps(caps)

    if caps.backend == "none":
        print("No ACPI interface found. Nothing to save.")
        return

    # Check if we have a profile to update
    profile = resolve_profile(vid, pid) if detected else None
    if profile is not None:
        print(f"Current profile: {profile.name}")
        ans = input("Add ACPI capabilities to this profile? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("Skipped.")
            return

        # Build AcpiConfig from probed capabilities
        from .profiles import AcpiConfig
        fan_labels = []
        if caps.fan_count >= 1:
            fan_labels.append("Fan 1")
        if caps.fan_count >= 2:
            fan_labels.append("Fan 2")

        sensor_labels = {}
        if caps.has_temperature:
            sensor_labels["temp_cpu"] = "CPU Temp"
            sensor_labels["temp_socket"] = "Socket Temp"

        profile_names = {}
        if caps.has_power_profiles:
            for pid_int in caps.profile_ids:
                default_names = {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"}
                name = default_names.get(pid_int, f"Profile {pid_int}")
                profile_names[str(pid_int)] = {"name": name, "desc": ""}

        profile.acpi = AcpiConfig(
            has_fan_control=caps.has_fan_rpm or caps.has_fan_duty,
            has_temperature=caps.has_temperature,
            has_power_profiles=caps.has_power_profiles,
            fan_count=caps.fan_count,
            fan_labels=fan_labels,
            sensor_labels=sensor_labels,
            profiles=profile_names,
            backend=caps.backend,
        )

        path = save_user_profile(profile)
        print(f"\n✅ Profile updated: {path}")
        print()
        print("To use right now:")
        print("  Open the tray menu and click 'Reload profiles'")
        print()
        print("To share with the community:")
        print("  gigamate profile contribute")
    else:
        print("No device profile to update.")
        print("Run 'gigamate rgb calibrate' first to create a keyboard profile.")


# ────────────────────────────────────────────
# Other commands
# ────────────────────────────────────────────


def cmd_version(args) -> None:
    """Show version information."""
    from . import __version__
    print(f"GigaMate v{__version__}")
    print("Gigabyte laptop management for Linux")
    print()


def cmd_legacy(args) -> None:
    """Handle legacy flat-command syntax (gigabyte-rgb <effect> ...)."""
    _print_deprecation()
    print()

    # Re-parse as old-style command
    effect = args.effect
    colour = args.colour or "light_purple"
    level = _parse_level(args.level)

    if args.calibrate:
        cmd_rgb_calibrate(args)
        return
    if args.list:
        profile = resolve_profile(args.vid, args.pid)
        list_options(profile)
        return
    if args.reset:
        cmd_rgb_reset(args)
        return
    if args.cycle:
        cmd_rgb_cycle(args)
        return
    if effect == "detect":
        cmd_rgb_detect(args)
        return
    if effect == "off":
        cmd_rgb_off(args)
        return
    if effect is None:
        print("Legacy usage: gigabyte-rgb <effect> <colour> [options]")
        print("New usage:    gigamate <subcommand> [options]")
        print()
        print("Available subcommands: rgb, status, profile, detect, calibrate, version")
        print("For help: gigamate --help")
        return

    # RGB set command
    cmd_rgb_static(args)


def cmd_rgb_reset(args) -> None:
    """Re-attach kernel keyboard drivers."""
    profile = resolve_profile(args.vid, args.pid)
    dev = get_keyboard(args.vid, args.pid, profile)
    if dev is None:
        print("Keyboard not found")
        sys.exit(1)
    for i in [0, 2, 4]:
        try:
            dev.attach_kernel_driver(i)
        except Exception:
            pass
    print("Keyboard drivers re-attached for interfaces 0/2/4. Typing should work.")


def list_options(profile=None):
    """List available RGB options."""
    cmap = profile.colour_map if profile is not None else COLOUR_MAP
    print("Available effects (static is the only working one for backlight;")
    print("others may break the keyboard and require a USB reset to recover):")
    for name, val in sorted(PROGRAMS.items(), key=lambda x: x[1]):
        print(f"  {name:15s} (0x{val:02X})")
    print()
    print("Available colours:")
    for name in sorted(cmap.keys()):
        print(f"  {name}")
    print()
    print("Brightness levels: off, dim, full")
    print("Available speeds:")
    for name, val in sorted(SPEEDS.items(), key=lambda x: x[1]):
        print(f"  {name:10s} (0x{val:02X})")


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────


def main() -> None:
    """Entry point: detect subcommand mode vs legacy mode."""
    # Check if this is a legacy invocation
    if _is_legacy_invocation():
        _legacy_main()
        return
    _subcommand_main()


def _legacy_main() -> None:
    """Legacy flat-argument parser (gigabyte-rgb <effect> <colour> ...)."""
    parser = argparse.ArgumentParser(
        description="GigaMate — Gigabyte laptop management (legacy syntax)",
        add_help=False,
    )
    parser.add_argument("effect", nargs="?", default=None)
    parser.add_argument("colour", nargs="?", default="light_purple")
    parser.add_argument("--level", "-l", default="full")
    parser.add_argument("--speed", "-s", default="medium")
    parser.add_argument("--interface", "-i", type=int, default=3)
    parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)
    parser.add_argument("--list", "-L", action="store_true")
    parser.add_argument("--cycle", "-c", action="store_true")
    parser.add_argument("--reset", "-r", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--help", action="store_true")

    args, _ = parser.parse_known_args()

    if args.help:
        print("GigaMate — Gigabyte laptop management for Linux")
        print()
        print("Legacy syntax (gigabyte-rgb):")
        print("  gigabyte-rgb static <colour> [--level off|dim|full]")
        print("  gigabyte-rgb off")
        print("  gigabyte-rgb detect")
        print("  gigabyte-rgb --calibrate")
        print("  gigabyte-rgb --cycle")
        print("  gigabyte-rgb --list")
        print("  gigabyte-rgb --reset")
        print()
        print("New syntax (gigamate):")
        print("  gigamate --help")
        return

    cmd_legacy(args)


def _subcommand_main() -> None:
    """New subcommand-based parser (gigamate <subcommand> ...)."""
    parser = argparse.ArgumentParser(
        prog="gigamate",
        description="GigaMate — Gigabyte laptop management for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  gigamate rgb static purple         Set keyboard colour
  gigamate rgb off                   Turn backlight off
  gigamate status                    Show hardware status
  gigamate profile gaming            Set Gaming power profile
  gigamate detect                    Detect hardware
  gigamate calibrate rgb             Interactive RGB calibration
        
Legacy: gigabyte-rgb <effect> <colour>  (still works)""",
    )
    sub = parser.add_subparsers(dest="command", help="Sub-command")

    # --- rgb subcommand ---
    rgb_parser = sub.add_parser("rgb", help="Keyboard RGB control")
    rgb_sub = rgb_parser.add_subparsers(dest="rgb_action", help="RGB action")

    # rgb static
    static_parser = rgb_sub.add_parser("static", help="Set static colour")
    static_parser.add_argument("colour", nargs="?", default="light_purple", help="Colour name")
    static_parser.add_argument("--level", "-l", default="full", help="Brightness: off/dim/full")
    static_parser.add_argument("--speed", "-s", default="medium", help="Speed: fastest/slowest or 1-10")
    static_parser.add_argument("--interface", "-i", type=int, default=3, help="USB interface")
    static_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    static_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # rgb off
    off_parser = rgb_sub.add_parser("off", help="Turn backlight off")
    off_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    off_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # rgb detect
    rgb_sub.add_parser("detect", help="Scan for compatible keyboards")

    # rgb cycle
    cycle_parser = rgb_sub.add_parser("cycle", help="Cycle through colours")
    cycle_parser.add_argument("--level", "-l", default="full")
    cycle_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    cycle_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # rgb reset
    reset_parser = rgb_sub.add_parser("reset", help="Re-attach keyboard drivers")
    reset_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    reset_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # rgb calibrate
    cal_rgb_parser = rgb_sub.add_parser("calibrate", help="Interactive RGB calibration")
    cal_rgb_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    cal_rgb_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- status subcommand ---
    status_parser = sub.add_parser("status", help="Show hardware status")
    status_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    status_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- profile subcommand ---
    # Supports:
    #   gigamate profile              → show current
    #   gigamate profile gaming       → set (shorthand)
    #   gigamate profile set gaming   → set (explicit)
    #   gigamate profile contribute   → contribute
    profile_parser = sub.add_parser("profile", help="Power profile control")
    profile_parser.add_argument("action", nargs="?", default=None,
                                help="'show', 'contribute', or a profile name/number to set")
    profile_parser.add_argument("name", nargs="?", default=None,
                                help="Profile name or number (for 'set' action)")
    profile_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    profile_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- detect subcommand ---
    detect_parser = sub.add_parser("detect", help="Detect hardware")
    detect_parser.add_argument("--acpi", action="store_true", help="Detailed ACPI probe")
    detect_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    detect_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- calibrate subcommand ---
    calibrate_parser = sub.add_parser("calibrate", help="Calibrate hardware")
    calibrate_sub = calibrate_parser.add_subparsers(dest="calibrate_action", help="Calibration type")

    calibrate_rgb_parser = calibrate_sub.add_parser("rgb", help="Keyboard RGB calibration")
    calibrate_rgb_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    calibrate_rgb_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    calibrate_acpi_parser = calibrate_sub.add_parser("acpi", help="ACPI capabilities probe")
    calibrate_acpi_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    calibrate_acpi_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # calibrate --all (combined)
    calibrate_all_parser = calibrate_sub.add_parser("all", help="RGB + ACPI calibration")
    calibrate_all_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    calibrate_all_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- version subcommand ---
    sub.add_parser("version", help="Show version")

    # Parse
    args = parser.parse_args()

    # Dispatch
    if args.command == "rgb":
        _dispatch_rgb(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "profile":
        _dispatch_profile(args)
    elif args.command == "detect":
        if args.acpi:
            cmd_detect_acpi(args)
        else:
            cmd_detect(args)
    elif args.command == "calibrate":
        _dispatch_calibrate(args)
    elif args.command == "version":
        cmd_version(args)
    else:
        parser.print_help()
        sys.exit(1)


def _dispatch_rgb(args) -> None:
    """Dispatch to the correct RGB handler."""
    action = args.rgb_action
    if action == "static":
        cmd_rgb_static(args)
    elif action == "off":
        cmd_rgb_off(args)
    elif action == "detect":
        cmd_rgb_detect(args)
    elif action == "cycle":
        cmd_rgb_cycle(args)
    elif action == "reset":
        cmd_rgb_reset(args)
    elif action == "calibrate":
        cmd_rgb_calibrate(args)
    else:
        print("RGB actions: static, off, detect, cycle, reset, calibrate")
        print("Example: gigamate rgb static purple")
        sys.exit(1)


def _dispatch_profile(args) -> None:
    """Dispatch to the correct profile handler.

    Supports:
      gigamate profile               → show current
      gigamate profile gaming        → set (shorthand)
      gigamate profile set gaming    → set (explicit)
      gigamate profile contribute    → contribute
    """
    action = (args.action or "").lower()

    if action == "contribute":
        cmd_profile_contribute(args)
    elif action == "show" or action == "":
        cmd_profile_show(args)
    elif action == "set":
        name = args.name
        if not name:
            print("Usage: gigamate profile set <name>")
            print("Example: gigamate profile set gaming")
            sys.exit(1)
        cmd_profile_set(args, name)
    else:
        # Assume it's a profile name/number (shorthand)
        cmd_profile_set(args, action)
        return


def _dispatch_calibrate(args) -> None:
    """Dispatch to the correct calibration handler."""
    action = args.calibrate_action
    if action == "rgb":
        cmd_calibrate_rgb(args)
    elif action == "acpi":
        cmd_calibrate_acpi(args)
    elif action == "all":
        # Run both calibrations
        print("=== RGB Calibration ===")
        cmd_calibrate_rgb(args)
        print()
        print("=== ACPI Calibration ===")
        cmd_calibrate_acpi(args)
        print()
        print("Combined calibration complete.")
        print("Run 'gigamate profile contribute' to share your profile.")
    elif action is None:
        print("Calibration types: rgb, acpi, all")
        print("Example: gigamate calibrate rgb")
        sys.exit(1)


if __name__ == "__main__":
    main()
