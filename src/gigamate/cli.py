"""GigaMate — Command-line interface.

Usage:
    gigamate rgb static <colour> [options]    Set keyboard colour
    gigamate rgb off                           Turn backlight off
    gigamate rgb detect                        Scan for keyboards
    gigamate rgb cycle                         Cycle colours
    gigamate rgb calibrate                     Interactive RGB calibration
    gigamate status                            Show hardware status
    gigamate gpu status                        Show discrete GPU power state
    gigamate gpu memoffset 300               Memory-clock V/F offset (MHz; + = overclock)
    gigamate profile [name]                    Show/set power profile
    gigamate profile contribute                Pull Request instructions
    gigamate detect [--acpi]                   Detect hardware
    gigamate calibrate [rgb|acpi|all]        Run calibration
    gigamate version                           Show version
    gigamate update [--check] [--yes]          Check for / install updates

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
    resolve_model,
    profile_usable,
    match_dmi_profile,
    load_user_dmi_profiles,
    ModelMatch,
    calibrate as run_calibrate,
    save_user_profile,
    DeviceProfile,
    DmiConfig,
    get_dmi_product_name,
    get_dmi_product_family,
    get_dmi_vendor,
    get_dmi_chassis_type,
)
from .config import load as load_config, save as save_config, update_config
from .acpi import (
    AcpiController,
    FanProfile,
    FanState,
    probe_acpi_capabilities,
    AcpiCapabilities,
)
from .osd import show_profile_osd
from .system_power import sync_system_power
from .gpu import get_gpu_state, gpu_status_text
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
        try:
            update_config(lambda c: {**c, "colour": colour, "brightness": level})
        except Exception:
            pass
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
    try:
        def _mut(c):
            if int(c.get("brightness", 2)) > 0:
                c["last_brightness"] = c["brightness"]
            c["brightness"] = 0
            return c
        update_config(_mut)
    except Exception:
        pass
    print("Keyboard backlight turned off.")


def cmd_rgb_detect(args) -> None:
    """Scan for compatible Gigabyte keyboards."""
    print_detect()


def cmd_rgb_idle(args) -> None:
    """Configure keyboard backlight idle auto-off (tray/GUI read this config)."""
    from .idle import clamp_timeout

    if args.value == "off":
        def _mutate(cfg):
            cfg["idle_off_enabled"] = False
            return cfg
        print("Keyboard idle auto-off disabled.")
    else:
        sec = clamp_timeout(int(args.value))

        def _mutate(cfg):
            cfg["idle_off_enabled"] = True
            cfg["idle_timeout_sec"] = sec
            return cfg
        print(f"Keyboard idle auto-off set to {sec} seconds.")
    update_config(_mutate)


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
    dmi_model = get_dmi_product_name()
    if profile is not None:
        print(f"  Model: {profile.name}  ({vid:04X}:{pid:04X})")
    elif detected:
        name = dmi_model or "Unknown"
        print(f"  Model: {name}  ({vid:04X}:{pid:04X})")
    elif dmi_model:
        print(f"  Model: {dmi_model}")
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
        print(f"  Keyboard: Not detected (no USB RGB)")

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
        from .capabilities import detect_system_capabilities
        if detect_system_capabilities().is_gigabyte_laptop:
            print("     Re-run install.sh to build and load the bundled gigamate_acpi module.")
        else:
            print("     This is not a supported Gigabyte laptop; fan/power control is unavailable.")
        print()

    # Discrete GPU state
    gpu = get_gpu_state()
    if gpu.present:
        print("  ── Discrete GPU ──")
        print(f"     dGPU state:   {gpu_status_text(gpu)}")
        if gpu.dynamic_boost_supported:
            boost_state = "Active" if gpu.dynamic_boost_active else "Inactive"
            print(f"     Dynamic Boost: {boost_state}")
        elif gpu.smartshift_supported:
            bias_str = f"  (bias: {gpu.smartshift_bias:+d})" if gpu.smartshift_bias is not None else ""
            print(f"     SmartShift:   Active{bias_str}")
        print()


def cmd_gpu_status(args) -> None:
    """Show the discrete GPU power state.

    On systems without a discrete GPU this prints nothing.
    """
    gpu = get_gpu_state()
    if not gpu.present:
        print("GigaMate — Discrete GPU")
        print()
        print("  No discrete GPU detected (integrated graphics only).")
        return
    print("GigaMate — Discrete GPU")
    print()
    print(f"  dGPU vendor:  {gpu.vendor or 'unknown'}")
    print(f"  dGPU state:   {gpu_status_text(gpu)}")
    print(f"  Runtime PM:   {gpu.status or 'unknown'}")
    print(f"  Power state:  {gpu.power_state or 'unknown'}")
    if gpu.dynamic_boost_supported:
        if gpu.dynamic_boost_active:
            print("  Dynamic Boost: Active (nvidia-powerd)")
        else:
            print("  Dynamic Boost: Inactive (run: systemctl enable --now nvidia-powerd)")
    elif gpu.smartshift_supported:
        bias_str = f" (bias: {gpu.smartshift_bias:+d})" if gpu.smartshift_bias is not None else ""
        print(f"  SmartShift:   Supported{bias_str}")


def _dgpu_status_with_applied(dgpu_tune, st):
    """Overlay the watcher service's runtime state file onto a local status dict.

    The CLI runs in its own process, so its watcher cannot know what the
    background service applied — especially when auto-apply is off. The state
    file written by the service is the cross-process source of truth.
    """
    sf = dgpu_tune.read_state_file() or {}
    if not sf:
        return st
    off = sf.get("applied_offset")
    mx = sf.get("applied_max")
    mem = sf.get("applied_mem")
    if off is not None:
        st["offset_mhz"] = off
    if mx is not None:
        st["max_clock_mhz"] = mx
    if mem is not None:
        st["mem_offset_mhz"] = mem
    st["applied_offset"] = off
    st["applied_max"] = mx
    st["applied_mem"] = mem
    st["applied"] = bool(off or mx or mem)
    return st


def cmd_gpu_undervolt(args) -> None:
    """Sleep-aware NVIDIA dGPU V/F-offset undervolt."""
    from . import dgpu_tune

    if getattr(args, "probe", False):
        res = dgpu_tune.probe(force=True)
        print("GigaMate — dGPU undervolt probe")
        print(f"  supported: {bool(res.get('supported'))}")
        print(f"  device:    {res.get('device') or '—'}")
        if res.get("offset_mhz") is not None:
            print(f"  offset:    +{res['offset_mhz']} MHz")
        if res.get("error"):
            print(f"  error:     {res['error']}")
        return

    value = getattr(args, "value", "status")

    if value in (None, "status"):
        dgpu_tune.watcher.load_desired_from_config()
        dgpu_tune.watcher.tick()
        st = _dgpu_status_with_applied(dgpu_tune, dgpu_tune.watcher.status())
        print("GigaMate — dGPU tuning")
        print()
        print(f"  device:      {st.get('device') or '—'}")
        print(f"  supported:   {bool(dgpu_tune.probe().get('supported'))}")
        print(f"  dGPU state:  {st.get('runtime') or '—'} ({st.get('power_state') or '—'})")
        uv = f"on (+{st.get('desired_offset')} MHz)" if st.get("desired_offset") else "off"
        print(f"  undervolt:   {uv}")
        cap = int(st.get("desired_max_clock") or 0)
        print(f"  max clock:   {('cap ' + str(cap) + ' MHz') if cap else 'off (unlocked)'}")
        mem = int(st.get("desired_mem_offset") or 0)
        print(f"  memory offset:{(' ' + f'{mem:+d} MHz') if mem else ' off (stock)'}")
        print(f"  auto-apply:  {'on' if st.get('auto', True) else 'off (manual)'}")
        print(f"  applied:     {st.get('applied')}"
              + (f" (offset +{st.get('offset_mhz')} MHz)" if st.get('offset_mhz') is not None else "")
              + (f" (cap {st.get('max_clock_mhz')} MHz)" if st.get('max_clock_mhz') else "")
              + (f" (mem offset {st.get('mem_offset_mhz'):+d} MHz)" if st.get('mem_offset_mhz') else ""))
        if st.get("last_error"):
            print(f"  last error:  {st['last_error']}")
        holders = dgpu_tune.wake_holders()
        if holders:
            print(f"  awake held by: {', '.join(holders)}")
        print()
        print("  Note: applied only while the dGPU is awake; cleared when it sleeps.")
        return

    cfg = load_config()
    auto = cfg.get("dgpu_undervolt_auto", True) if getattr(args, "auto", None) is None else bool(args.auto)

    if str(value).lower() in ("off", "stock", "reset"):
        dgpu_tune.set_desired_config(False, 0, auto)
        print("dGPU undervolt: off (stock offset +0 MHz)")
        return

    try:
        mhz = int(str(value))
    except (TypeError, ValueError):
        print("Invalid value: use a MHz offset 0-255, 'off', or 'status'")
        sys.exit(2)

    mhz = max(0, min(dgpu_tune.MAX_OFFSET_MHZ, mhz))
    dgpu_tune.set_desired_config(mhz > 0, mhz, auto)
    print(f"dGPU undervolt: +{mhz} MHz" if mhz else "dGPU undervolt: off (stock offset +0 MHz)")


def cmd_gpu_maxclock(args) -> None:
    """Sleep-aware NVIDIA dGPU maximum-clock cap."""
    from . import dgpu_tune

    if getattr(args, "probe", False):
        res = dgpu_tune.probe(force=True)
        print("GigaMate — dGPU max-clock probe")
        print(f"  supported: {bool(res.get('supported'))}")
        print(f"  device:    {res.get('device') or '—'}")
        ceiling = res.get("gpu_max_clock_mhz")
        print(f"  ceiling:   {ceiling if ceiling is not None else '—'} MHz")
        if res.get("error"):
            print(f"  error:     {res['error']}")
        return

    value = getattr(args, "value", "status")

    if value in (None, "status"):
        dgpu_tune.watcher.load_desired_from_config()
        dgpu_tune.watcher.tick()
        st = _dgpu_status_with_applied(dgpu_tune, dgpu_tune.watcher.status())
        cap = int(st.get("desired_max_clock") or 0)
        print("GigaMate — dGPU max clock")
        print()
        print(f"  device:      {st.get('device') or '—'}")
        print(f"  dGPU state:  {st.get('runtime') or '—'} ({st.get('power_state') or '—'})")
        print(f"  configured:  {('cap ' + str(cap) + ' MHz') if cap else 'off (unlocked)'}")
        print(f"  applied:     {st.get('applied')}"
              + (f" (cap {st.get('max_clock_mhz')} MHz)" if st.get('max_clock_mhz') else ""))
        if st.get("last_error"):
            print(f"  last error:  {st['last_error']}")
        return

    if str(value).lower() in ("off", "unlock", "reset", "stock"):
        dgpu_tune.set_desired_config(max_enabled=False, max_clock=0)
        print("dGPU max clock: off (unlocked)")
        return

    try:
        mhz = int(str(value))
    except (TypeError, ValueError):
        print("Invalid value: use a MHz cap 0-4000, 'off', or 'status'")
        sys.exit(2)

    mhz = max(0, min(dgpu_tune.MAX_CLOCK_MHZ, mhz))
    dgpu_tune.set_desired_config(max_enabled=mhz > 0, max_clock=mhz)
    print(f"dGPU max clock: cap {mhz} MHz" if mhz else "dGPU max clock: off (unlocked)")


def cmd_gpu_memoffset(args) -> None:
    """Sleep-aware NVIDIA dGPU memory-clock V/F offset (overclock/underclock)."""
    from . import dgpu_tune

    if getattr(args, "probe", False):
        res = dgpu_tune.probe(force=True)
        print("GigaMate — dGPU memory-clock offset probe")
        print(f"  supported:   {bool(res.get('supported'))}")
        print(f"  device:      {res.get('device') or '—'}")
        print(f"  max clock:   {res.get('mem_max_clock_mhz') or '—'} MHz")
        print(f"  offset API:  {'present' if res.get('mem_offset_api') else 'missing'}")
        if not dgpu_tune.helper_is_current():
            print("  warning:     installed helper is out of date — re-run ./install.sh")
        if res.get("error"):
            print(f"  error:       {res['error']}")
        return

    value = getattr(args, "value", "status")

    if value in (None, "status"):
        dgpu_tune.watcher.load_desired_from_config()
        dgpu_tune.watcher.tick()
        st = _dgpu_status_with_applied(dgpu_tune, dgpu_tune.watcher.status())
        mem = int(st.get("desired_mem_offset") or 0)
        print("GigaMate — dGPU memory-clock offset")
        print()
        print(f"  device:      {st.get('device') or '—'}")
        print(f"  dGPU state:  {st.get('runtime') or '—'} ({st.get('power_state') or '—'})")
        print(f"  offset API:  {'present' if dgpu_tune.probe().get('mem_offset_api') else 'missing'}")
        print(f"  range:       {dgpu_tune.MEM_OFFSET_MIN_MHZ:+d}..{dgpu_tune.MEM_OFFSET_MAX_MHZ:+d} MHz")
        print(f"  configured:  {f'{mem:+d} MHz' if mem else 'off (stock)'}")
        print(f"  applied:     {st.get('applied')}"
              + (f" (mem offset {st.get('mem_offset_mhz'):+d} MHz)" if st.get('mem_offset_mhz') else ""))
        if st.get("last_error"):
            print(f"  last error:  {st['last_error']}")
        if not dgpu_tune.helper_is_current():
            print("  warning:     installed helper is out of date — re-run ./install.sh")
        return

    if str(value).lower() in ("off", "unlock", "reset", "stock", "auto"):
        dgpu_tune.set_desired_config(mem_enabled=False, mem_offset=0)
        print("dGPU memory offset: off (stock)")
        return

    try:
        mhz = int(str(value))
    except (TypeError, ValueError):
        print(f"Invalid value: use a signed MHz offset "
              f"({dgpu_tune.MEM_OFFSET_MIN_MHZ}..{dgpu_tune.MEM_OFFSET_MAX_MHZ}), 'off', or 'status'")
        sys.exit(2)

    if not (dgpu_tune.MEM_OFFSET_MIN_MHZ <= mhz <= dgpu_tune.MEM_OFFSET_MAX_MHZ):
        print(f"Offset {mhz} MHz is out of range "
              f"({dgpu_tune.MEM_OFFSET_MIN_MHZ}..{dgpu_tune.MEM_OFFSET_MAX_MHZ}).")
        sys.exit(2)

    dgpu_tune.set_desired_config(mem_enabled=mhz != 0, mem_offset=mhz)
    print(f"dGPU memory offset: {mhz:+d} MHz" if mhz else "dGPU memory offset: off (stock)")


def cmd_gpu_auto(args) -> None:
    """Enable/disable automatic application of dGPU tuning on boot/wake."""
    from . import dgpu_tune

    value = getattr(args, "value", "status")

    if value in (None, "status"):
        auto = bool(load_config().get("dgpu_undervolt_auto", True))
        print("GigaMate — dGPU auto-apply")
        print()
        print(f"  auto-apply:  {'on' if auto else 'off (manual)'}")
        print("  applies on:  boot, wake and resume" if auto else "  applies on:  explicit Apply / CLI only")
        return

    if str(value).lower() in ("on", "true", "yes", "enable", "enabled"):
        dgpu_tune.set_auto_config(True)
        print("dGPU auto-apply: on")
        return

    if str(value).lower() in ("off", "false", "no", "disable", "disabled"):
        dgpu_tune.set_auto_config(False)
        print("dGPU auto-apply: off (current GPU state left unchanged)")
        return

    print("Invalid value: use 'on', 'off', or 'status'")
    sys.exit(2)


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


_PROFILE_UNVERIFIED_HINT = (
    "Power profiles are not enabled for this model. GigaMate only exposes profile\n"
    "switching when a matching model profile declares a profile set (built-in or\n"
    "created via 'gigamate calibrate acpi')."
)

_EXPERIMENTAL_NOTICE = (
    "Note: profile switching is EXPERIMENTAL on this model "
    "(community-evidenced, unconfirmed) — it may do nothing."
)


def _model_match(args) -> Optional[ModelMatch]:
    """Resolve the model profile that gates profile switching."""
    try:
        return resolve_model(getattr(args, "vid", None), getattr(args, "pid", None))
    except Exception:
        return None


def _open_profiles(args):
    """Shared gate for profile commands.

    Returns ``(ctrl, match, cfg)`` when profile switching may proceed; otherwise
    prints why and exits. Experimental models require the one-time opt-in.
    """
    ctrl = AcpiController()
    if not ctrl.available:
        print("ACPI not available. No power profile control.")
        sys.exit(1)
    match = _model_match(args)
    if match is None:
        print("Power profiles: not enabled for this model.")
        print()
        print(_PROFILE_UNVERIFIED_HINT)
        sys.exit(1)
    cfg = load_config()
    if not profile_usable(match, cfg):
        print("Power profiles: EXPERIMENTAL and not enabled for this model.")
        print()
        print("These profiles are community-evidenced but unconfirmed on this unit.")
        print("Enable them with:")
        print("  gigamate profile experimental on")
        sys.exit(1)
    if match.experimental:
        print(_EXPERIMENTAL_NOTICE)
        print()
    return ctrl, match, cfg


def _profile_set_for(profile: Optional[DeviceProfile]):
    """Return ``(pids, profiles_map)`` for a profile that declares profiles."""
    if profile is not None and profile.has_acpi and profile.acpi:
        pids = sorted(int(k) for k in profile.acpi.profiles.keys())
        return pids, profile.acpi.profiles
    return None, None


def cmd_profile_show(args) -> None:
    """Show current power profile."""
    ctrl, match, _cfg = _open_profiles(args)
    profile_val = ctrl.get_profile()
    if profile_val is None:
        print("Current power profile: Unknown")
        return
    pname = _profile_name(profile_val, match.profile)
    print(f"Power Profile: {pname}  ({profile_val.value})")


def _record_profile_and_sync(val: int) -> None:
    """Persist the chosen profile; sync system power only if the user opted in."""
    try:
        cfg = update_config(lambda c: {**c, "acpi_profile": val})
    except Exception:
        cfg = {}
    if cfg.get("sync_system_power", False):
        try:
            sync_system_power(val)
        except Exception:
            pass


def cmd_profile_set(args, name: str) -> None:
    """Set power profile by name or number."""
    ctrl, match, _cfg = _open_profiles(args)
    profile = match.profile
    pids, _p_data = _profile_set_for(profile)

    # Try parsing as number first
    try:
        val = int(name)
    except ValueError:
        val = None
    if val is not None:
        if val not in pids:
            print(f"Profile {val} is not available on this model.")
            print(f"Available: {', '.join(str(p) for p in pids)}")
            sys.exit(1)
        fp = FanProfile(val)
        if ctrl.set_profile(fp):
            _record_profile_and_sync(val)
            pname = _profile_name(fp, profile)
            print(f"Power profile set to: {pname}  ({val})")
            return
        else:
            print(f"Failed to set profile {val}", file=sys.stderr)
            sys.exit(1)

    # Try parsing as name
    try:
        fp = FanProfile.from_name(name)
    except KeyError:
        print(f"Unknown profile: '{name}'")
        print(f"Available: {', '.join(str(p) for p in pids)}")
        sys.exit(1)
    if fp.value not in pids:
        print(f"Profile '{name}' ({fp.value}) is not available on this model.")
        print(f"Available: {', '.join(str(p) for p in pids)}")
        sys.exit(1)
    if ctrl.set_profile(fp):
        _record_profile_and_sync(fp.value)
        pname = _profile_name(fp, profile)
        print(f"Power profile set to: {pname}  ({fp.value})")
        return
    else:
        print(f"Failed to set profile '{name}'", file=sys.stderr)
        sys.exit(1)


def cmd_profile_cycle(args) -> None:
    """Cycle to the next power profile and show OSD."""
    ctrl, match, _cfg = _open_profiles(args)
    pids, p_data = _profile_set_for(match.profile)

    current_fp = ctrl.get_profile()
    current_val = current_fp.value if current_fp is not None else pids[0]

    try:
        curr_idx = pids.index(current_val)
        next_idx = (curr_idx + 1) % len(pids)
    except ValueError:
        next_idx = 0

    next_profile_id = pids[next_idx]
    fp = FanProfile(next_profile_id)

    if ctrl.set_profile(fp):
        _record_profile_and_sync(next_profile_id)
        entry = p_data.get(str(next_profile_id), {})
        pname = entry.get("name", f"Profile {next_profile_id}")
        desc = entry.get("desc", "")
        print(f"Power profile cycled to: {pname}  ({next_profile_id})")
        show_profile_osd(pname, desc)
    else:
        print(f"Failed to cycle profile to {next_profile_id}", file=sys.stderr)
        sys.exit(1)


def cmd_profile_experimental(args) -> None:
    """Show or change consent for experimental (unconfirmed) model profiles."""
    value = (getattr(args, "value", "status") or "status").lower()

    if value == "status":
        cfg = load_config()
        match = _model_match(args)
        enabled = bool(cfg.get("experimental_profiles_enabled", False))
        print("GigaMate — experimental profiles")
        print()
        print(f"  enabled:  {'on' if enabled else 'off'}")
        if match is None:
            print("  model:    no matching model profile")
        else:
            kind = "experimental (unconfirmed)" if match.experimental else "verified"
            print(f"  model:    {match.clean_name}  [{match.source}] — {kind}")
        return

    if value in ("on", "true", "yes", "enable", "enabled"):
        update_config(lambda c: {**c, "experimental_profiles_enabled": True})
        print("Experimental profiles: ON — unconfirmed profile controls are now enabled.")
        return

    if value in ("off", "false", "no", "disable", "disabled"):
        update_config(lambda c: {**c, "experimental_profiles_enabled": False})
        print("Experimental profiles: OFF — experimental controls are hidden again.")
        return

    print("Invalid value: use 'on', 'off', or 'status'")
    sys.exit(2)


def cmd_profile_report(args) -> None:
    """Print a paste-able summary to help confirm an experimental model."""
    from . import __version__
    from .battery import get_battery_manager  # noqa: F401 (kept side-effect free)

    cfg = load_config()
    match = _model_match(args)
    ctrl = AcpiController()
    caps = ctrl.capabilities if ctrl.available else None

    print("GigaMate — model report")
    print()
    print(f"  GigaMate version:   {__version__}")
    print(f"  DMI vendor:         {get_dmi_vendor() or '-'}")
    print(f"  DMI product_name:   {get_dmi_product_name() or '-'}")
    print(f"  DMI product_family: {get_dmi_product_family() or '-'}")
    chassis = get_dmi_chassis_type()
    print(f"  DMI chassis:        {chassis if chassis is not None else '-'}")
    if caps is not None:
        print(f"  ACPI backend:       {caps.backend}")
        print(f"  Sensors:            temp={caps.has_temperature} fan_rpm={caps.has_fan_rpm} "
              f"fan_duty={caps.has_fan_duty} fans={caps.fan_count}")
        print(f"  Interface answers profile writes: {caps.has_power_profiles}")
    else:
        print("  ACPI backend:       none")
    if match is None:
        print("  Model profile:      none")
    else:
        profiles = match.profile.acpi.profiles if match.profile.acpi else {}
        names = ", ".join(
            f"{k}={v.get('name', '?')}" for k, v in sorted(profiles.items(), key=lambda kv: int(kv[0]))
        )
        print(f"  Model profile:      {match.clean_name}")
        print(f"  Profile source:     {match.source}")
        print(f"  Confidence:         {'experimental (unconfirmed)' if match.experimental else 'verified'}")
        print(f"  Profiles:           {names or '-'}")
    print(f"  Experimental opt-in: {bool(cfg.get('experimental_profiles_enabled', False))}")
    print(f"  Selected profile:    {cfg.get('acpi_profile')}")
    if caps is not None and ctrl.available:
        try:
            state = ctrl.read_state()
            if state is not None:
                print(f"  Live:               temp_cpu={state.temp_cpu} fan1={state.fan1_rpm} "
                      f"fan2={state.fan2_rpm} duty={state.duty_cpu}")
        except Exception:
            pass
    print()
    print("  Paste this in a comment at:")
    print("    https://github.com/goodeesh/GigaMate/issues")


def cmd_profile_contribute(args) -> None:
    """Show Pull Request instructions for contributing a profile."""
    detected = detect_device()
    dmi_model = get_dmi_product_name()
    ctrl = AcpiController()

    profile_dir = CONFIG_DIR / "profiles"
    candidates = []
    if detected is not None:
        candidates.append(profile_dir / f"{detected[0]:04X}_{detected[1]:04X}.json")
    if profile_dir.is_dir():
        candidates.extend(sorted(profile_dir.glob("dmi_*.json")))

    existing = next((p for p in candidates if p.exists()), None)

    if existing is None:
        # Nothing generated yet — guide the user instead of dead-ending on the
        # fork/cp steps for a file that does not exist.
        print()
        if dmi_model:
            print(f"  Model: {dmi_model}")
        if detected is None and not ctrl.available:
            print("No Gigabyte hardware detected on this system.")
            print("Run this on the laptop you want to add support for.")
            sys.exit(1)
        print()
        print("  No profile has been generated yet — create one first:")
        print()
        step = 1
        if detected is not None:
            print(f"    {step}. gigamate calibrate rgb      # map the keyboard colours (if RGB)")
            step += 1
        print(f"    {step}. gigamate calibrate acpi     # generate the model profile")
        print(f"    {step + 1}. gigamate profile contribute # then follow the PR steps")
        print()
        return

    profile_path_str = str(existing)
    # Header metadata: USB key when known, otherwise the DMI model name.
    if detected is not None:
        name = f"{detected[0]:04X}:{detected[1]:04X}"
        key_line = f"  VID:PID: {detected[0]:04X}:{detected[1]:04X}"
    else:
        name = dmi_model or "Gigabyte Laptop"
        key_line = f"  DMI:     {dmi_model or '-'}"

    print()
    print("🌟  Share your model profile with the community!")
    print()
    print(f"  Model:   {name}")
    print(key_line)
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
    print("  If your unit is unconfirmed, mark the profile's acpi.confidence as")
    print("  \"experimental\" so it ships as Experimental until a user verifies it.")
    print()


# ────────────────────────────────────────────
# Detect commands
# ────────────────────────────────────────────


def cmd_detect(args) -> None:
    """Show all detected hardware (keyboard + ACPI)."""
    detected = detect_device()
    profile = resolve_profile(args.vid, args.pid)
    dmi_model = get_dmi_product_name()

    print("GigaMate — Hardware Detection")
    print()

    # Model
    if profile is not None:
        print(f"  Model:    {profile.name}")
    elif dmi_model:
        print(f"  Model:    {dmi_model}")

    # Keyboard
    if detected:
        vid, pid = detected
        if profile is not None:
            print(f"  Keyboard: {profile.name}  ({vid:04X}:{pid:04X})")
        else:
            print(f"  Keyboard: Unknown model  ({vid:04X}:{pid:04X})")
    else:
        print(f"  Keyboard: Not detected (no USB RGB)")
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
            print(f"          Power Profiles: {'⚠️ unverified' if caps.has_power_profiles else '❌'}")
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
        print("    1. Install kernel headers and re-run install.sh")
        print("       (builds and loads the bundled gigamate_acpi module, no extra packages)")
        print("    2. If you already have the acpi_call module installed,")
        print("       it is used automatically as a fallback.")
        print("    3. If the module exists but is missing for the running kernel,")
        print("       run 'gigamate repair' to rebuild and reload it.")
        return

    backend_name = {"module": "kernel module", "acpi_call": "acpi_call", "mock": "mock"}.get(
        caps.backend, caps.backend
    )
    print(f"  Backend:    {backend_name}")
    print(f"  Temperature: {'✅ Detected' if caps.has_temperature else '❌ Not available'}")
    print(f"  Fan RPM:    {'✅ Detected' if caps.has_fan_rpm else '❌ Not available'}")
    print(f"  Fan Duty:   {'✅ Detected' if caps.has_fan_duty else '❌ Not available'}")
    if caps.has_power_profiles:
        print("  Profiles:   ⚠️ Interface detected, but unverified for this model")
        print("              Profile switching is only enabled by a matching model profile.")
        print("              Run 'gigamate calibrate acpi' to generate one.")
    else:
        print("  Profiles:   ❌ Not available")
    if caps.fan_count > 0:
        print(f"  Fans:       {caps.fan_count}")
    print()

    if caps.backend in ("module", "acpi_call"):
        print("  To generate a model profile (sensors auto-detected, profiles opt-in):")
        print("  Run 'gigamate calibrate acpi'")
    print()


# ────────────────────────────────────────────
# Calibrate command
# ────────────────────────────────────────────


def cmd_hotkeys_list(args) -> None:
    """Show the hotkey mappings configured for a keyboard."""
    from .config import load as load_config
    from .hotkeys import merge_hotkey_specs
    from .profiles import detect_device, load_builtin_profiles, resolve_profile

    detected = detect_device()
    if detected is None and args.vid is None and args.pid is None:
        print("No supported keyboard detected.")
        sys.exit(1)
    vid = args.vid if args.vid is not None else detected[0]
    pid = args.pid if args.pid is not None else detected[1]
    profile = resolve_profile(vid, pid)
    builtin = load_builtin_profiles().get((vid, pid))
    cfg = load_config()
    raw_overrides = cfg.get("hotkey_overrides")
    overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    specs = merge_hotkey_specs(
        overrides,
        profile.hotkeys if profile is not None else {},
        builtin.hotkeys if builtin is not None else {},
    )

    print("GigaMate — Hotkey mappings")
    print()
    if profile is not None:
        print(f"  Keyboard: {profile.name}  ({vid:04X}:{pid:04X})")
    else:
        print(f"  Keyboard: Unknown model  ({vid:04X}:{pid:04X})")
    if not specs:
        print("  No hotkey mappings for this keyboard.")
        print("  Run 'gigamate hotkeys watch' and press the buttons to capture signatures.")
        return
    for spec in specs:
        payload = " ".join(f"{b:02x}" for b in spec.payload)
        label = f" ({spec.key_name})" if spec.key_name != spec.action else ""
        print(f"  {spec.action}{label}: interface {spec.interface}, "
              f"report 0x{spec.report_id:02x}, payload {payload}")


def cmd_hotkeys_watch(args) -> None:
    """Stream raw hidraw reports to capture button signatures (Ctrl-C stops)."""
    import os
    import select
    import time
    from .config import load as load_config
    from .hotkeys import (
        format_report, list_hotkey_hidraw, match_spec, merge_hotkey_specs,
    )
    from .profiles import detect_device, load_builtin_profiles, resolve_profile

    detected = detect_device()
    if detected is None and args.vid is None and args.pid is None:
        print("No supported keyboard detected.")
        sys.exit(1)
    vid = args.vid if args.vid is not None else detected[0]
    pid = args.pid if args.pid is not None else detected[1]

    nodes = list_hotkey_hidraw(vid, pid)
    if args.interface is not None:
        nodes = [(i, n) for i, n in nodes if i == args.interface]
    if not nodes:
        print(f"No hidraw interfaces for keyboard {vid:04X}:{pid:04X}.")
        sys.exit(1)

    profile = resolve_profile(vid, pid)
    builtin = load_builtin_profiles().get((vid, pid))
    cfg = load_config()
    raw_overrides = cfg.get("hotkey_overrides")
    overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    specs = merge_hotkey_specs(
        overrides,
        profile.hotkeys if profile is not None else {},
        builtin.hotkeys if builtin is not None else {},
    )

    print("GigaMate — hotkey capture (Ctrl-C to stop)", flush=True)
    print("  Keyboard "
          f"{profile.name + '  ' if profile is not None else ''}({vid:04X}:{pid:04X})", flush=True)
    print("  Listening on: " + ", ".join(f"iface {i} ({n})" for i, n in nodes), flush=True)
    print("  Press the buttons whose signatures you want to capture.", flush=True)

    fds = {}
    for iface, path in nodes:
        try:
            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = iface
        except OSError as exc:
            print(f"  Cannot open {path}: {exc}")
    if not fds:
        print("Could not open any hidraw interface (check udev permissions).")
        sys.exit(1)

    last = {}
    start = time.monotonic()
    try:
        while True:
            readable, _, _ = select.select(list(fds), [], [], 1.0)
            for fd in readable:
                try:
                    data = os.read(fd, 64)
                except OSError:
                    continue
                if not data:
                    continue
                text = format_report(data)
                if last.get(fd) == text:
                    continue
                last[fd] = text
                elapsed = time.monotonic() - start
                line = (f"[{elapsed:7.2f}s] iface={fds[fd]} len={len(data)} "
                        f"report_id=0x{data[0]:02x} payload={text[3:] if len(text) > 3 else text}")
                hit = match_spec(specs, data, fds[fd])
                if hit is not None:
                    label = f" ({hit.key_name})" if hit.key_name != hit.action else ""
                    line += f"  <- {hit.action}{label}"
                print(line, flush=True)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


def cmd_calibrate_rgb(args) -> None:
    """Run keyboard RGB calibration."""
    # Delegate to the existing rgb calibrate command
    cmd_rgb_calibrate(args)


# Documented ACPI profile layouts, keyed by DMI product family. These come from
# community reverse-engineering (e.g. the gigabyte-laptop-wmi driver family):
# Aero/AORUS use four fan-curve profiles, while Gigabyte Gaming models (e.g.
# A16) expose eco/balanced/boost that program the NVIDIA platform controller
# for GPU TGP. They are *hypotheses* to seed a candidate profile — never
# evidence that a given unit honours them.
_ACPI_PROFILE_TEMPLATES = {
    "AERO": {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"},
    "AORUS": {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"},
    "GIGABYTE AERO": {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"},
    "GIGABYTE GAMING": {0: "eco", 1: "balanced", 2: "boost"},
}


def _family_acpi_template() -> Optional[dict]:
    """Return the documented profile-name template for the DMI product family, if any."""
    family = (get_dmi_product_family() or "").upper()
    for key, tpl in _ACPI_PROFILE_TEMPLATES.items():
        if key in family:
            return dict(tpl)
    return None


def cmd_calibrate_acpi(args) -> None:
    """Probe ACPI capabilities and generate/update a model profile.

    USB-keyed when a Gigabyte keyboard is present; DMI-keyed otherwise (ACPI-only
    laptops, e.g. GAMING A16) so those models can be supported and contributed.
    """
    print("GigaMate — ACPI Calibration")
    print()
    print("Probing ACPI capabilities...")
    caps = probe_acpi_capabilities()
    _print_acpi_caps(caps)

    if caps.backend == "none":
        print("No ACPI interface found. Nothing to save.")
        return

    detected = detect_device()
    dmi_model = get_dmi_product_name()

    if detected is not None:
        vid, pid = detected
        print(f"Detected keyboard: {vid:04X}:{pid:04X}")
        profile = resolve_profile(vid, pid)
        if profile is None:
            profile = DeviceProfile(
                vid=vid, pid=pid, name=dmi_model or f"{vid:04X}:{pid:04X}",
                interfaces=[], control_interface=0,
            )
            print(f"Creating a new ACPI-only profile for: {profile.name}")
        else:
            print(f"Updating existing profile: {profile.name}")
    elif dmi_model:
        # No Gigabyte USB keyboard: key the profile on DMI (ACPI-only laptops).
        print(f"No USB keyboard detected — keying the profile on DMI: {dmi_model}")
        profile = match_dmi_profile(load_user_dmi_profiles())
        if profile is None:
            profile = DeviceProfile(
                vid=0, pid=0, name=dmi_model, interfaces=[], control_interface=0,
                dmi=DmiConfig(product_names=[dmi_model]),
            )
            print(f"Creating a new DMI-keyed profile for: {profile.name}")
        else:
            print(f"Updating existing profile: {profile.name}")
    else:
        print("No Gigabyte USB device and no DMI model name detected — nothing can be saved on this system.")
        return

    # Build the ACPI section from what the probe actually detected. Sensors are
    # reliably detectable; power-profile semantics are not, so profile support
    # stays off by default and only gets enabled with explicit, informed consent.
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

    profiles_map = {}
    template = _family_acpi_template()
    if caps.has_power_profiles and template:
        print()
        print("The AMW0 interface answers, and this machine's DMI product family matches")
        print("a documented layout. We can seed profile names from community knowledge:")
        print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(template.items())))
        print()
        print("⚠️  These controls are NOT verified on your specific unit. On some EC")
        print("    firmwares, profile writes are accepted but do nothing. Enabling this")
        print("    shows profile controls that may have no effect.")
        ans = input("Enable power profiles for this model? [y/N] ").strip().lower()
        if ans in ("y", "yes"):
            profiles_map = {str(k): {"name": v, "desc": ""} for k, v in template.items()}
    elif caps.has_power_profiles:
        print()
        print("The AMW0 interface answers sensor reads, but no documented profile layout is")
        print("known for this model family, so power-profile switching stays DISABLED.")
        print()

    profile.acpi = AcpiConfig(
        has_fan_control=caps.has_fan_rpm or caps.has_fan_duty,
        has_temperature=caps.has_temperature,
        has_power_profiles=bool(profiles_map),
        fan_count=caps.fan_count,
        fan_labels=fan_labels,
        sensor_labels=sensor_labels,
        profiles=profiles_map,
        backend=caps.backend,
    )

    path = save_user_profile(profile)
    print(f"\n✅ Profile saved: {path}")
    print()
    if profiles_map:
        print("Profile switching is now enabled for this model.")
    else:
        print("Profile switching stays disabled for this model.")
    print()
    print("To share with the community:")
    print("  gigamate profile contribute")


# ────────────────────────────────────────────
# Other commands
# ────────────────────────────────────────────


def cmd_version(args) -> None:
    """Show version information."""
    from . import __version__
    print(f"GigaMate v{__version__}")
    print("Gigabyte laptop management for Linux")
    print()


def cmd_repair(args) -> None:
    """Rebuild + reload the ACPI kernel driver for the running kernel."""
    import json
    import subprocess

    from .updates import (
        REPAIR_HELPER_PATH,
        build_terminal_repair_command,
        repair_available,
        repair_command,
    )

    print("GigaMate — ACPI driver repair")
    print()

    if not repair_available():
        print(f"  Repair helper not installed: {REPAIR_HELPER_PATH}")
        print("  Re-run install.sh to install it (or use the terminal commands below).")
        print()
        term_cmd = build_terminal_repair_command()
        if term_cmd:
            try:
                subprocess.Popen(term_cmd, start_new_session=True)
                print("  Opened a terminal running the repair (sudo will ask there).")
                return
            except Exception:
                pass
        print("  Run manually in a terminal:")
        print("    sudo dkms install gigamate_acpi/<version> -k $(uname -r) --force")
        print("    sudo modprobe gigamate_acpi")
        return

    cmd = repair_command()
    if getattr(args, "status", False):
        cmd = cmd + ["status"]
    print(f"  Running: {' '.join(cmd)}")
    print()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception as exc:
        print(f"  Repair failed to start: {exc}")
        sys.exit(1)
    out = (proc.stdout or "").strip()
    if out:
        print(out)
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    try:
        result = json.loads(out.splitlines()[-1]) if out else {}
        ok = bool(result.get("ok"))
    except Exception:
        ok = proc.returncode == 0
    print()
    if ok:
        print("  ACPI driver repaired and reloaded.")
        sys.exit(0)
    print("  Repair failed — see the output above.", file=sys.stderr)
    sys.exit(1)


def cmd_update(args) -> None:
    """Check for updates or self-update to the latest tagged release."""
    import subprocess
    from . import updates as update_checker

    check_only = bool(getattr(args, "check", False))
    assume_yes = bool(getattr(args, "yes", False))

    if check_only:
        result = update_checker.check_for_updates(force=True)
        print(f"Installed: {result['current']}")
        print(f"Latest:    {result['latest'] or 'unknown'}")
        if result["update_available"]:
            print(f"Update available: {result['latest']}")
        elif result["latest"]:
            print("Up to date.")
        elif result.get("reachable"):
            print("No releases published yet (maintainer hasn't tagged one).")
        else:
            print("Could not reach github.com.")
        return

    result = update_checker.check_for_updates(force=True)
    current = result["current"]
    latest = result["latest"]
    if not latest:
        print(f"Installed: {current}")
        if result.get("reachable"):
            print("No releases published yet (maintainer hasn't tagged one).")
        else:
            print("Could not reach github.com — try again later.")
        sys.exit(1)
    if not result["update_available"]:
        print(f"GigaMate {current} is up to date (latest {latest}).")
        return
    print(f"Update available: {current} → {latest}")
    if not assume_yes:
        ans = input("Update now? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            print("Cancelled.")
            return
    try:
        admin = update_checker.admin_status()
    except Exception:
        admin = "password"
    if admin in ("denied", "no-sudo"):
        print(f"Administrator rights required (status: {admin}).")
        print("The driver steps need sudo, which is not available for you.")
        print(update_checker.manual_update_instructions())
        sys.exit(1)
    cmd = update_checker.build_update_command()
    print("Running background update (install.sh --update)...")
    print(f"  {' '.join(cmd)}")
    rc = subprocess.call(cmd)
    sys.exit(rc)


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


def cmd_center(args) -> None:
    """Launch GigaMate Center GUI."""
    try:
        from .ui.main_window import run_gui
        run_gui()
    except ImportError as exc:
        print(f"Error launching GigaMate Center: {exc}", file=sys.stderr)
        print("Ensure PyQt6 is installed: sudo pacman -S python-pyqt6", file=sys.stderr)
        sys.exit(1)


def cmd_tray(args) -> None:
    """Launch GigaMate System Tray daemon."""
    from .tray import main as tray_main
    tray_main()


def cmd_battery(args) -> None:
    """Show battery status or set charge limit."""
    from .battery import get_battery_manager
    mgr = get_battery_manager()
    if not mgr.is_available:
        print("No battery detected on this system.", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "limit", None) is not None:
        try:
            val = int(args.limit)
            if mgr.set_charge_limit(val):
                def _mutate(cfg):
                    cfg["charge_limit"] = val
                    cfg["charge_limit_enabled"] = True
                    return cfg
                update_config(_mutate)
                print(f"Battery charge threshold set to {val}%.")
            else:
                print("Failed to set battery charge threshold (unsupported on this firmware).", file=sys.stderr)
                sys.exit(1)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    info = mgr.get_battery_info()
    print("Battery Information:")
    print(f"  Device:         {info.name}")
    print(f"  Capacity:       {info.capacity}%")
    print(f"  Status:         {info.status}")
    print(f"  Power Source:   {'AC Mains' if info.ac_online else 'Battery'}")
    if info.health_percent is not None:
        print(f"  Health:         {info.health_percent:.1f}%")
    if info.cycle_count is not None:
        print(f"  Cycle Count:    {info.cycle_count}")
    if info.charge_limit_supported:
        limit_str = f"{info.charge_limit}%" if info.charge_limit else "None (100%)"
        print(f"  Charge Limit:   {limit_str} (Backend: {info.backend})")
    else:
        print("  Charge Limit:   Unsupported by current kernel/firmware")

    if getattr(args, "status", False):
        print(f"  Backend:        {info.backend or 'none'}")
        if info.charge_now is not None:
            print(f"  Charge Now:     {info.charge_now}")
        if info.charge_full is not None:
            print(f"  Charge Full:    {info.charge_full}")
        if info.charge_full_design is not None:
            print(f"  Design Full:    {info.charge_full_design}")


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
    parser.add_argument("--center", "-c", action="store_true", help="Launch GigaMate Center GUI")
    parser.add_argument("--tray", "-t", action="store_true", help="Launch GigaMate System Tray daemon")
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

    # rgb idle
    idle_parser = rgb_sub.add_parser(
        "idle", help="Keyboard backlight idle auto-off timeout")
    idle_parser.add_argument("value", choices=["off", "10", "30", "60", "120", "300", "900"],
                             help="'off' or a timeout in seconds")

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
                                help="'show', 'cycle', 'contribute', 'report', "
                                     "'experimental [on|off|status]', or a profile name/number to set")
    profile_parser.add_argument("name", nargs="?", default=None,
                                help="Profile name/number (for 'set') or on/off/status (for 'experimental')")
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

    # --- repair subcommand ---
    repair_parser = sub.add_parser(
        "repair", help="Rebuild + reload the ACPI kernel driver for the running kernel")
    repair_parser.add_argument("--status", action="store_true",
                               help="Only report driver/helper state, do not repair")

    # --- update subcommand ---
    update_parser = sub.add_parser("update", help="Check for / install updates")
    update_parser.add_argument("--check", action="store_true",
                               help="Only check, do not install")
    update_parser.add_argument("--yes", "-y", action="store_true",
                               help="Non-interactive (assume yes)")

    # --- gpu subcommand ---
    gpu_parser = sub.add_parser("gpu", help="Discrete GPU power state")
    gpu_sub = gpu_parser.add_subparsers(dest="gpu_action", help="GPU action")
    gpu_sub.add_parser("status", help="Show discrete GPU power state")
    gpu_uv_parser = gpu_sub.add_parser("undervolt", help="Sleep-aware NVIDIA dGPU undervolt (V/F offset)")
    gpu_uv_parser.add_argument("value", nargs="?", default="status",
                               help="MHz offset 0-255, 'off', or 'status' (default)")
    gpu_uv_parser.add_argument("--probe", action="store_true",
                               help="Report whether the V/F offset API is supported")
    gpu_uv_parser.add_argument("--auto", dest="auto", action="store_true", default=None,
                               help="Auto-apply on wake (default)")
    gpu_uv_parser.add_argument("--no-auto", dest="auto", action="store_false",
                               help="Do not auto-apply on wake (manual)")
    gpu_mc_parser = gpu_sub.add_parser("maxclock", help="Sleep-aware NVIDIA dGPU max clock cap")
    gpu_mc_parser.add_argument("value", nargs="?", default="status",
                               help="MHz cap 0-4000, 'off', or 'status' (default)")
    gpu_mc_parser.add_argument("--probe", action="store_true",
                               help="Report the GPU clock ceiling")
    gpu_mem_parser = gpu_sub.add_parser("memoffset",
                                        help="Sleep-aware NVIDIA dGPU memory-clock V/F offset (overclock)")
    gpu_mem_parser.add_argument("value", nargs="?", default="status",
                                help="signed MHz offset (-1000..2000), 'off', or 'status' (default)")
    gpu_mem_parser.add_argument("--probe", action="store_true",
                                help="Report whether the memory-clock offset API is available")
    gpu_auto_parser = gpu_sub.add_parser("auto", help="Auto-apply dGPU tuning on boot/wake")
    gpu_auto_parser.add_argument("value", nargs="?", default="status",
                                 help="'on', 'off', or 'status' (default)")

    # --- hotkeys subcommand ---
    hotkeys_parser = sub.add_parser("hotkeys", help="Hardware hotkey mappings and capture")
    hotkeys_sub = hotkeys_parser.add_subparsers(dest="hotkeys_action", help="Hotkey action")
    hotkeys_watch_parser = hotkeys_sub.add_parser(
        "watch", help="Capture raw button reports until interrupted (Ctrl-C)")
    hotkeys_watch_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    hotkeys_watch_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)
    hotkeys_watch_parser.add_argument("--interface", "-i", type=int, default=None,
                                      help="Only listen on this USB interface")
    hotkeys_list_parser = hotkeys_sub.add_parser(
        "list", help="Show configured hotkey mappings")
    hotkeys_list_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    hotkeys_list_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- battery subcommand ---
    battery_parser = sub.add_parser("battery", help="Battery health and charge threshold limiter")
    battery_parser.add_argument("--limit", "-l", type=int, default=None,
                                help="Set max charge limit (40..100)")
    battery_parser.add_argument("--status", action="store_true",
                                help="Show detailed battery status")

    # --- center / gui subcommand ---
    sub.add_parser("center", aliases=["gui"], help="Open GigaMate Center GUI")

    # --- tray subcommand ---
    sub.add_parser("tray", help="Start GigaMate System Tray daemon")

    # Parse
    args = parser.parse_args()

    if getattr(args, "center", False):
        cmd_center(args)
        return
    if getattr(args, "tray", False):
        cmd_tray(args)
        return

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
    elif args.command == "repair":
        cmd_repair(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "battery":
        cmd_battery(args)
    elif args.command in ("center", "gui"):
        cmd_center(args)
    elif args.command == "tray":
        cmd_tray(args)
    elif args.command == "gpu":
        if args.gpu_action in ("status", None):
            cmd_gpu_status(args)
        elif args.gpu_action == "undervolt":
            cmd_gpu_undervolt(args)
        elif args.gpu_action == "maxclock":
            cmd_gpu_maxclock(args)
        elif args.gpu_action == "memoffset":
            cmd_gpu_memoffset(args)
        elif args.gpu_action == "auto":
            cmd_gpu_auto(args)
        else:
            print("GPU actions: status, undervolt, maxclock, memoffset, auto")
            print("Examples: gigamate gpu status | gigamate gpu undervolt 100 | gigamate gpu maxclock 2100 | gigamate gpu memoffset 300")
            sys.exit(1)
    elif args.command == "hotkeys":
        _dispatch_hotkeys(args)
    else:
        if len(sys.argv) == 1:
            cmd_center(args)
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
    elif action == "idle":
        cmd_rgb_idle(args)
    else:
        print("RGB actions: static, off, detect, cycle, reset, calibrate, idle")
        print("Example: gigamate rgb static purple")
        sys.exit(1)


def _dispatch_profile(args) -> None:
    """Dispatch to the correct profile handler.

    Supports:
      gigamate profile               → show current
      gigamate profile gaming        → set (shorthand)
      gigamate profile set gaming    → set (explicit)
      gigamate profile cycle         → cycle to next profile + OSD
      gigamate profile contribute    → contribute
    """
    action = (args.action or "").lower()

    if action in ("cycle", "next"):
        cmd_profile_cycle(args)
    elif action == "contribute":
        cmd_profile_contribute(args)
    elif action == "report":
        cmd_profile_report(args)
    elif action == "experimental":
        args.value = args.name or "status"
        cmd_profile_experimental(args)
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


def _dispatch_hotkeys(args) -> None:
    """Dispatch to the correct hotkeys handler."""
    action = args.hotkeys_action
    if action == "watch":
        cmd_hotkeys_watch(args)
    else:
        cmd_hotkeys_list(args)


def _dispatch_calibrate(args) -> None:
    """Dispatch to the correct calibration handler."""
    action = args.calibrate_action
    if action == "rgb":
        cmd_calibrate_rgb(args)
    elif action == "acpi":
        cmd_calibrate_acpi(args)
    elif action == "all":
        # Run both calibrations; a missing RGB keyboard must not abort the
        # ACPI half (and vice-versa).
        print("=== RGB Calibration ===")
        try:
            cmd_calibrate_rgb(args)
        except SystemExit as exc:
            if exc.code not in (0, None):
                print("Skipping RGB calibration (no compatible keyboard detected).", file=sys.stderr)
        print()
        print("=== ACPI Calibration ===")
        try:
            cmd_calibrate_acpi(args)
        except SystemExit as exc:
            if exc.code not in (0, None):
                print("Skipping ACPI calibration.", file=sys.stderr)
        print()
        print("Combined calibration complete.")
        print("Run 'gigamate profile contribute' to share your profile.")
    elif action is None:
        print("Calibration types: rgb, acpi, all")
        print("Example: gigamate calibrate rgb")
        sys.exit(1)


if __name__ == "__main__":
    main()
