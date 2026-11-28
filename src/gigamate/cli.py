"""GigaMate — Command-line interface.

Usage:
    gigamate rgb static <colour> [options]    Set keyboard colour
    gigamate rgb off                           Turn backlight off
    gigamate rgb detect                        Scan for keyboards
    gigamate rgb cycle                         Cycle colours
    gigamate status                            Show hardware status
    gigamate gpu status                        Show discrete GPU power state
    gigamate gpu power <mode|off|on|probe>     dGPU power gate (Optimus / ECO / Auto)
    gigamate gpu memoffset 300               Memory-clock V/F offset (MHz; + = overclock)
    gigamate profile [name]                    Show/set power profile
    gigamate profile report                    Print auto-discovered hardware report
    gigamate detect [--acpi]                   Detect hardware
    gigamate doctor                            Diagnose hardware and permissions
    gigamate version                           Show version
    gigamate update [--check] [--yes]          Check for / install updates
    gigamate center                            Launch GigaMate Center GUI
    gigamate tray                              Launch GigaMate System Tray daemon
"""

import sys
import os
import time
import argparse
from dataclasses import replace
from typing import Optional, List

from .protocol import (
    detect_keyboards,
    hardware_supports_lamparray,
    get_keyboard,
    print_detect,
    set_rgb,
    set_static,
    set_off,
)
from .lamparray import PALETTE_HEX, parse_hex_rgb
from .power_limits import discover_acpi_profiles
from .capabilities import (
    _read_dmi,
    get_dmi_chassis_type,
    get_dmi_product_name,
    get_dmi_vendor,
)
from .config import (
    load as load_config,
    save as save_config,
    update_config,
    effective_rgb_hex,
    effective_brightness_pct,
    enabled_acpi_profiles,
)
from .acpi import (
    AcpiController,
    FanProfile,
    FanState,
    acpi_module_status,
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
    brightness_raw = getattr(args, "brightness", None)
    level_raw = brightness_raw if brightness_raw is not None else args.level

    is_numeric_pct = False
    pct = 100
    level = 2
    if isinstance(level_raw, str) and level_raw.strip().lstrip("-").isdigit():
        pct = int(level_raw.strip())
        if pct != 0 and not (5 <= pct <= 100):
            print("Brightness percentage must be 0 (off) or 5..100", file=sys.stderr)
            sys.exit(1)
        is_numeric_pct = True
        level = 0 if pct == 0 else (1 if pct <= 60 else 2)
    else:
        level = _parse_level(level_raw)
        pct = 0 if level == 0 else (25 if level == 1 else 100)

    parsed_hex = parse_hex_rgb(colour)
    is_hex = parsed_hex is not None

    dev = get_keyboard(args.vid, args.pid)
    if dev is None:
        vid = args.vid or 0
        pid = args.pid or 0
        print(f"Keyboard not found (VID={vid:04X} PID={pid:04X})")
        if detect_keyboards() and not hardware_supports_lamparray(args.vid, args.pid):
            print("Note: Non-LampArray pre-2025 keyboards require GigaMate v4.5.x.")
        else:
            print("Use 'gigamate rgb detect' to scan for compatible keyboards.")
        sys.exit(1)

    if level == 0 or pct == 0:
        set_off(dev)
        try:
            update_config(lambda c: {**c, "rgb_enabled": False, "brightness_pct": 0, "brightness": 0})
        except Exception:
            pass
        print("Set to off")
        return

    if is_hex:
        r, g, b = parsed_hex
        rgb_hex = f"#{r:02x}{g:02x}{b:02x}"
    else:
        if colour not in PALETTE_HEX:
            print(f"Unknown colour: {colour}")
            print(f"Available: {', '.join(sorted(PALETTE_HEX.keys()))}")
            sys.exit(1)
        rgb_hex = PALETTE_HEX[colour]

    ok = set_rgb(dev, rgb_hex, pct)
    if ok:
        try:
            def _mut(c):
                out = {**c, "rgb_enabled": True, "brightness_pct": pct, "rgb_hex": rgb_hex}
                if not is_hex:
                    out["colour"] = colour
                return out
            update_config(_mut)
        except Exception:
            pass
        print(f"Set to {rgb_hex if is_hex else colour} ({pct}%)")
    else:
        print("Failed to send command", file=sys.stderr)
        sys.exit(1)


def cmd_rgb_off(args) -> None:
    """Turn keyboard backlight off."""
    dev = get_keyboard(args.vid, args.pid)
    if dev is None:
        print("Keyboard not found")
        if detect_keyboards() and not hardware_supports_lamparray(args.vid, args.pid):
            print("Note: Non-LampArray pre-2025 keyboards require GigaMate v4.5.x.")
        sys.exit(1)
    set_off(dev)
    try:
        def _mut(c):
            if int(c.get("brightness", 2)) > 0:
                c["last_brightness"] = c["brightness"]
            c["brightness"] = 0
            c["rgb_enabled"] = False
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
    dev = get_keyboard(args.vid, args.pid)
    if dev is None:
        print("Keyboard not found")
        sys.exit(1)
    level = _parse_level(args.level)
    pct = 25 if level == 1 else 100
    print("Cycling through colours (Ctrl+C to stop)...")
    try:
        while True:
            for colour_name, rgb_hex in PALETTE_HEX.items():
                print(f"  {colour_name}...", end=" ", flush=True)
                set_rgb(dev, rgb_hex, pct)
                time.sleep(2)
                print()
    except KeyboardInterrupt:
        print("\nStopped.")


def _explain_hidraw_access(path: str, label: str = "hidraw device") -> None:
    """Print actionable guidance when a hidraw node cannot be opened."""
    from .doctor import permission_lines

    print(f"Cannot access {label} {path}: permission denied", file=sys.stderr)
    for line in permission_lines(path):
        print(f"  {line}" if line else "", file=sys.stderr)


def cmd_status(args) -> None:
    """Show full hardware status (keyboard + ACPI)."""
    # Keyboard info
    detected = detect_keyboards()
    vid, pid = detected[0] if detected else (0, 0)

    print("╔══════════════════════════════════╗")
    print("║        GigaMate — Status        ║")
    print("╚══════════════════════════════════╝")
    print()

    # Model name
    dmi_model = get_dmi_product_name()
    if detected:
        name = dmi_model or "Unknown"
        print(f"  Model: {name}  ({vid:04X}:{pid:04X})")
    elif dmi_model:
        print(f"  Model: {dmi_model}")
    else:
        print(f"  Model: Not detected")

    # Keyboard info
    if detected and hardware_supports_lamparray(vid, pid):
        cfg = load_config()
        if cfg.get("rgb_enabled", True):
            rgb_hex = effective_rgb_hex(cfg)
            pct = effective_brightness_pct(cfg)
            print(f"  Keyboard: {rgb_hex} ({pct}%)")
        else:
            print("  Keyboard: Off")
    elif detected:
        print("  Keyboard: Detected (non-LampArray; use GigaMate v4.5.x for legacy 0x08)")
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
                pname = _profile_name(state.profile, discover_acpi_profiles())
                print(f"  ── Power Profile ──")
                print(f"     {pname}  ({state.profile.value})")
                try:
                    from .power_limits import discover_power_limits
                    sys_limits = discover_power_limits(active_profile_id=state.profile.value)
                    lim = sys_limits.profiles.get(state.profile.value)
                    if lim is not None and lim.has_any and not sys_limits.on_battery:
                        if lim.combined_w is not None:
                            print(f"     Combined:     {lim.combined_w} W")
                        if lim.cpu_sustained_w is not None:
                            burst = f" ({lim.cpu_burst_w} W burst)" if lim.cpu_burst_w and lim.cpu_burst_w > lim.cpu_sustained_w else ""
                            print(f"     CPU Max:      {lim.cpu_sustained_w} W{burst}")
                        if lim.gpu_fixed_w is not None:
                            print(f"     GPU Fixed:    {lim.gpu_fixed_w} W")
                        if lim.gpu_boost_w is not None and lim.gpu_fixed_w is not None and lim.gpu_boost_w > lim.gpu_fixed_w:
                            print(f"     GPU Boost:    Up to {lim.gpu_boost_w} W")
                except Exception:
                    pass
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
        try:
            from . import dgpu_gate

            gst = dgpu_gate.get_status(check_helper=False)
            if gst.get("supported") or gst.get("gated"):
                pending = " (pending off)" if gst.get("pending_off") else ""
                print(f"     Power gate:   {gst['mode']}{pending}")
        except Exception:
            pass
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
    try:
        from . import dgpu_gate

        gst = dgpu_gate.get_status(check_helper=False)
        if gst.get("supported") or gst.get("gated"):
            pending = " (pending off)" if gst.get("pending_off") else ""
            print(f"  Power gate:   {gst['mode']}{pending}")
    except Exception:
        pass
    if gpu.dynamic_boost_supported:
        if gpu.dynamic_boost_active:
            print("  Dynamic Boost: Active (nvidia-powerd)")
        else:
            print("  Dynamic Boost: Inactive (run: systemctl enable --now nvidia-powerd)")
    elif gpu.smartshift_supported:
        bias_str = f" (bias: {gpu.smartshift_bias:+d})" if gpu.smartshift_bias is not None else ""
        print(f"  SmartShift:   Supported{bias_str}")
    try:
        from . import usbc_power

        cfg = load_config()
        src = usbc_power.detect_power_source()
        if bool(cfg.get("usbc_power_budget_enabled")) or src.kind == "usbc_pd":
            st = usbc_power.read_state_file()
            w_str = f"{src.detected_wattage_w} W " if src.detected_wattage_w else ""
            en_str = "on" if cfg.get("usbc_power_budget_enabled") else "off (opt-in)"
            mode_str = st.get("mode") or ("active" if src.kind == "usbc_pd" else "standby")
            print(f"  USB-C Budget: {en_str} ({w_str}{src.kind}, state={mode_str})")
    except Exception:
        pass


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


def cmd_gpu_power(args) -> None:
    """Manage true ACPI + PCI discrete GPU power gating (AI Power Gear III)."""
    from . import dgpu_gate

    action = (getattr(args, "action", None) or "status").lower()

    if action == "confirm-dialog":
        from .ui.dgpu_gate_dialog import main as dialog_main

        d_args = [
            "--trigger",
            str(getattr(args, "trigger", "eco") or "eco"),
            "--target-mode",
            str(getattr(args, "target_mode", "eco") or "eco"),
        ]
        if getattr(args, "prev_mode", None):
            d_args.extend(["--prev-mode", str(args.prev_mode)])
        sys.exit(dialog_main(d_args))

    if action == "status":
        st = dgpu_gate.get_status(check_helper=True)
        gpu = get_gpu_state()
        print("GigaMate — dGPU Power Gate")
        print()
        print(f"  Supported:    {'yes' if st['supported'] else 'no'}")
        if not st["supported"] and st.get("unsupported_reason"):
            print(f"  Reason:       {dgpu_gate.availability_message(st.get('unsupported_reason'))}")
        print(f"  Mode:         {st['mode']}")
        print(f"  Power source: {'AC' if st['ac_online'] else 'Battery'}")
        print(f"  dGPU state:   {gpu_status_text(gpu)}")
        if st.get("wedged"):
            print("  Wedged:       GPU requires reset — reboot to recover (power gating paused)")
        if st.get("bdf"):
            print(f"  PCI device:   {st['bdf']} (bridge: {st.get('bridge_bdf') or '-'})")
        if st.get("acpi_path"):
            print(f"  ACPI node:    {st['acpi_path']}")
        print(f"  Eval backend: {st['eval_backend']}")
        gcfg = st.get("gate_config")
        if gcfg:
            source = gcfg.get("source", "acpi")
            quirks = ", ".join(gcfg.get("quirks") or []) or "none"
            print(f"  Gate method:  {gcfg['path']}.{gcfg['method']} ({source}; quirks: {quirks})")
            if st.get("acpi_sta") is not None:
                print(f"  ACPI status:  {st['acpi_sta']}")
        else:
            print("  Gate method:  none configured (run 'gigamate gpu power probe')")
        if st.get("displays"):
            print(f"  Attached:     {', '.join(st['displays'])} (blocks power-off)")
        if st.get("pending_off"):
            print(f"  Pending off:  yes ({st.get('last_error') or st.get('last_reason') or 'waiting'})")
        elif st.get("last_error"):
            print(f"  Last note:    {st['last_error']}")
        print()
        print("  Note: HDMI/DisplayPort outputs wired to the dGPU do not work while gated off.")
        return

    if action == "probe":
        live = bool(getattr(args, "live_test", False))
        res = dgpu_gate.probe_dgpu_gate(live_test=live)
        topo = res["topology"]
        print("GigaMate — dGPU Power Gate Probe")
        print()
        print(f"  Eval backend:    {res['eval_backend']}")
        print(f"  PCI dGPU:        {topo.get('bdf') or 'not present'} (audio: {topo.get('audio_bdf') or '-'}, bridge: {topo.get('bridge_bdf') or '-'})")
        print(f"  ACPI node:       {topo.get('acpi_path') or '-'}")
        print(f"  PowerResources:  {', '.join(topo.get('power_resources') or []) or 'none'}")
        print()
        if res["candidates"]:
            print("  Candidates:")
            for c in res["candidates"]:
                sta = c.get("sta_result") if c.get("sta_ok") else "unprobed/failed"
                print(f"    - {c['path']}.{c['method']} (via {c['source']}, {c['sta_method']}={sta})")
        else:
            print("  No candidate power-gate ACPI paths discovered.")
        if res["recommended"]:
            import json as _json

            print()
            print("  Recommended auto-discovered gate target:")
            print("  " + _json.dumps(res["recommended"], indent=2).replace("\n", "\n  "))
        if live:
            lt = res.get("live_test") or {}
            print()
            if lt.get("ok"):
                print("  Live off -> on -> rescan test: PASSED")
            else:
                print(f"  Live off -> on -> rescan test: FAILED ({lt.get('error')})")
                sys.exit(1)
        return

    if action == "off":
        supported, unsup_reason = dgpu_gate.get_availability()
        if not supported:
            print(dgpu_gate.availability_message(unsup_reason), file=sys.stderr)
            sys.exit(1)
        force_close = bool(getattr(args, "force_close", False))
        if force_close:
            closable = dgpu_gate.closable_gpu_holders()
            if closable:
                print("Closing apps/services holding dGPU:")
                for h in closable:
                    print(f"  - {h.label}")
            res = dgpu_gate.force_close_and_gate_off()
        else:
            res = dgpu_gate.gate_off()
        if res.get("ok"):
            if res.get("already_gated"):
                print("dGPU is already gated off.")
            else:
                print("dGPU gated off (power rail cut). HDMI/DP ports wired to the dGPU are inactive until restored.")
            return
        print(f"Could not gate off dGPU: {res.get('error')}", file=sys.stderr)
        if res.get("reboot_recommended"):
            print("Reboot the laptop to recover the GPU.", file=sys.stderr)
        if not force_close and res.get("reason") == "gpu_active":
            print("Tip: re-run with --force-close to close user apps holding the dGPU and power off immediately.", file=sys.stderr)
        sys.exit(1)

    if action == "on":
        res = dgpu_gate.gate_on()
        if res.get("ok"):
            switched = f" Switched mode to {res['switched_mode']}." if res.get("switched_mode") else ""
            if res.get("already_on"):
                print(f"dGPU is already active on PCI ({res.get('bdf')}).{switched}")
            else:
                print(
                    f"dGPU restored ({res.get('bdf')}, driver={res.get('driver') or 'binding'}, "
                    f"runtime={res.get('runtime_status') or 'unknown'}).{switched}"
                )
            return
        print(f"Could not restore dGPU: {res.get('error')}", file=sys.stderr)
        sys.exit(1)

    if action in ("optimus", "eco", "auto"):
        if action in ("eco", "auto"):
            supported, unsup_reason = dgpu_gate.get_availability()
            if not supported:
                print(dgpu_gate.availability_message(unsup_reason), file=sys.stderr)
                sys.exit(1)
        force_close = bool(getattr(args, "force_close", False))
        try:
            if force_close and action in ("eco", "auto"):
                closable = dgpu_gate.closable_gpu_holders()
                if closable:
                    print("Closing apps/services holding dGPU:")
                    for h in closable:
                        print(f"  - {h.label}")
                res = dgpu_gate.force_close_and_gate_off(mode=action)
            else:
                res = dgpu_gate.set_mode(action)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        if res.get("ok"):
            state_desc = "Off (gated)" if res.get("gated") else "Active / Runtime PM"
            print(f"dGPU power mode set to: {action} (state: {state_desc})")
            return
        if res.get("reason") == "gpu_active":
            print(f"dGPU power mode saved: {action}")
            print(f"  Pending: {res.get('error')}")
            print("  GigaMate will gate off the dGPU automatically once it suspends.")
            if not force_close:
                print("  Tip: re-run with --force-close to close user apps holding the dGPU immediately.")
            return
        if res.get("reason") == "boot_settle":
            print(f"dGPU power mode saved: {action}")
            print(f"  {res.get('error')}")
            return
        print(f"Mode saved ({action}), but action failed: {res.get('error')}", file=sys.stderr)
        if res.get("reboot_recommended"):
            print("Reboot the laptop to recover the GPU.", file=sys.stderr)
        sys.exit(1)

    if action == "sync":
        res = dgpu_gate.sync_policy(reason="cli_sync")
        if res.get("ok"):
            print(f"dGPU power gate synced (mode={res.get('mode')}, action={res.get('action')})")
            return
        if res.get("reason") == "gpu_active":
            print(f"Sync pending (mode={res.get('mode')}): {res.get('error')}")
            return
        print(f"Sync failed: {res.get('error')}", file=sys.stderr)
        sys.exit(1)

    print("Unknown action; use: status, off, on, eco, auto, optimus, probe, sync", file=sys.stderr)
    sys.exit(2)


def cmd_gpu_usbc_budget(args) -> None:
    """Inspect or configure the opt-in USB-C PD Power Budget."""
    from . import usbc_power

    action_raw = getattr(args, "action", None) or "status"
    value_raw = getattr(args, "value", None)
    action = str(action_raw).strip().lower()

    if action == "on":
        update_config(
            lambda c: {
                **c,
                "usbc_power_budget_enabled": True,
                "usbc_power_budget_disclaimer_accepted": True,
            }
        )
        print("USB-C PD Power Budget: enabled (opt-in).")
        print("  Takes effect automatically within ~2 s while on a >= 90 W USB-C PD charger.")
        return

    if action == "off":
        update_config(lambda c: {**c, "usbc_power_budget_enabled": False})
        print("USB-C PD Power Budget: disabled (stock 30 W NVIDIA safety cap restored on USB-C PD).")
        return

    if action == "set" or action_raw.lstrip("+-").isdigit():
        raw_w = value_raw if action == "set" else action_raw
        try:
            watts = int(str(raw_w))
        except (TypeError, ValueError):
            print("Invalid GPU budget: expected an integer in 30..150 (e.g. 'set 55').", file=sys.stderr)
            sys.exit(2)
        if not (30 <= watts <= 150):
            print(f"GPU budget {watts} W is out of valid range (30..150).", file=sys.stderr)
            sys.exit(2)
        update_config(lambda c: {**c, "usbc_gpu_budget_w": watts})
        print(f"USB-C GPU target split saved: {watts} W (clamped to active charger/GPU envelope).")
        return

    if action == "buffer":
        try:
            pct = int(str(value_raw))
        except (TypeError, ValueError):
            print("Invalid safety buffer: expected an integer percentage in 15..25 (e.g. 'buffer 20').", file=sys.stderr)
            sys.exit(2)
        if not (usbc_power.MIN_BUFFER_PCT <= pct <= usbc_power.MAX_BUFFER_PCT):
            print(
                f"Safety buffer {pct}% is out of valid range ({usbc_power.MIN_BUFFER_PCT}..{usbc_power.MAX_BUFFER_PCT}%).",
                file=sys.stderr,
            )
            sys.exit(2)
        update_config(lambda c: {**c, "usbc_buffer_pct": pct})
        print(f"USB-C safety buffer saved: {pct}% (rest goes to GPU + CPU split).")
        return

    if action != "status":
        print("Unknown action; use: status, on, off, set <gpu_w>, buffer <15..25>, or <gpu_w>", file=sys.stderr)
        sys.exit(2)

    cfg = load_config()
    src = usbc_power.detect_power_source()
    gpu = get_gpu_state()
    ctrl = AcpiController()
    caps = usbc_power.read_cached_gpu_caps_only()
    st = usbc_power.read_state_file()
    tgp_w = st.get("default_tgp_w") or caps.default_tgp_w
    max_w = st.get("max_constraint_w") or caps.max_constraint_w
    has_cpu_ppt = ctrl.available and ctrl.has_cpu_ppt_control()
    has_ctgp = ctrl.available and ctrl.has_gpu_ctgp_control()
    has_db = ctrl.available and ctrl.has_gpu_dynamic_boost_control()
    fw_usbc = ctrl.read_usbc_adapter() if ctrl.available else src.firmware_usbc_flag
    buf_pct = int(cfg.get("usbc_buffer_pct", usbc_power.DEFAULT_BUFFER_PCT) or usbc_power.DEFAULT_BUFFER_PCT)
    env = usbc_power.compute_budget_envelope(
        src.detected_wattage_w,
        tgp_w,
        max_w,
        has_ctgp_ctrl=has_ctgp,
        has_cpu_ppt_ctrl=has_cpu_ppt,
        has_dyn_boost_ctrl=has_db,
        has_nvidia_gpu=bool(gpu.present and gpu.vendor == "nvidia"),
        buffer_pct=buf_pct,
    )

    print("GigaMate — USB-C PD Power Budget")
    print()
    print(f"  Opt-in:       {'enabled' if cfg.get('usbc_power_budget_enabled') else 'disabled'}")
    print(f"  Target GPU:   {cfg.get('usbc_gpu_budget_w', 55)} W")
    print(f"  Safety buffer:{buf_pct:3d}% ({usbc_power.MIN_BUFFER_PCT}..{usbc_power.MAX_BUFFER_PCT}%)")
    w_info = f"{src.detected_wattage_w} W ({src.wattage_source})" if src.detected_wattage_w else "unknown"
    curr_info = f", {src.negotiated_current_a:.2f} A" if src.negotiated_current_a is not None else ""
    print(f"  Power source: {src.kind} (charger={w_info}{curr_info}, ac_online={src.ac_online})")
    print(
        f"  ACPI ctrls:   cpu_ppt={'yes' if has_cpu_ppt else 'no'}, "
        f"gpu_ctgp={'yes' if has_ctgp else 'no'}, "
        f"dyn_boost={'yes' if has_db else 'no'}, "
        f"usbc_adapter={fw_usbc}"
    )
    if tgp_w is not None:
        print(f"  GPU VBIOS:    base TGP {tgp_w} W" + (f" (max {max_w} W)" if max_w else ""))
    if env.supported:
        g_w, c_w = usbc_power.clamp_split(int(cfg.get("usbc_gpu_budget_w", env.default_gpu_w)), env)
        print(
            f"  Envelope:     {env.buffer_w} W buffer ({env.buffer_pct}%) + {env.silicon_budget_w} W silicon "
            f"(GPU {env.gpu_min_w}..{env.gpu_max_w} W -> split {g_w} W GPU / {c_w} W CPU)"
        )
    elif src.kind == "usbc_pd":
        print(f"  Envelope:     unsupported ({env.reason})")
    else:
        print("  Envelope:     standby (waiting for >= 90 W USB-C PD charger)")
    if st:
        applied = f"{st.get('applied_gpu_w')} W GPU / {st.get('applied_cpu_w')} W CPU" if st.get("active") else "inactive"
        print(f"  Daemon state: {st.get('mode', 'unknown')} ({applied})")
        if st.get("last_error"):
            print(f"  Last error:   {st['last_error']}")


# ────────────────────────────────────────────
# Profile commands
# ────────────────────────────────────────────


def _profile_name(profile: FanProfile, profiles_map: Optional[dict] = None) -> str:
    """Get the human-readable name for a FanProfile value."""
    if profiles_map:
        entry = profiles_map.get(profile.value) or profiles_map.get(str(profile.value), {})
        if "name" in entry:
            return entry["name"]
    return profile.name.capitalize()


def _open_profiles(args):
    """Shared gate for profile commands.

    Returns ``(ctrl, profiles_map, cfg)`` when profile switching may proceed.
    """
    ctrl = AcpiController()
    if not ctrl.available:
        print("ACPI not available. No power profile control.")
        sys.exit(1)
    profiles_map = discover_acpi_profiles()
    if not profiles_map and getattr(ctrl, "capabilities", None) is not None:
        profiles_map = getattr(ctrl.capabilities, "discovered_profiles", {}) or {}
    if not profiles_map:
        print("No power profiles exposed by BIOS.")
        sys.exit(1)
    cfg = load_config()
    return ctrl, profiles_map, cfg


def _profile_set_for(profiles_map: Optional[dict]):
    """Return ``(pids, profiles_map)`` for discovered profiles."""
    if profiles_map:
        pids = sorted(int(k) for k in profiles_map.keys())
        return pids, profiles_map
    return None, None


def cmd_profile_show(args) -> None:
    """Show current power profile."""
    ctrl, profiles_map, _cfg = _open_profiles(args)
    profile_val = ctrl.get_profile()
    if profile_val is None:
        print("Current power profile: Unknown")
        return
    pname = _profile_name(profile_val, profiles_map)
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
    ctrl, profiles_map, cfg = _open_profiles(args)
    pids, _p_data = _profile_set_for(profiles_map)
    enabled_map = enabled_acpi_profiles(profiles_map, cfg)
    disabled_set = set(cfg.get("disabled_profiles", []))

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
        if val in disabled_set and len(enabled_map) >= 1 and val not in enabled_map:
            pname = _profile_name(fp, profiles_map)
            print(f"Profile '{pname}' is currently disabled in Dashboard. Re-enable it to switch to it.")
            sys.exit(1)
        if val in (2, 3):
            from . import dgpu_gate

            if not dgpu_gate.high_power_profiles_allowed():
                pname = _profile_name(fp, profiles_map)
                print(
                    f"Profile '{pname}' ({val}) is unavailable while the discrete GPU is powered off (ECO / gated).",
                    file=sys.stderr,
                )
                sys.exit(1)
        if ctrl.set_profile(fp):
            _record_profile_and_sync(val)
            pname = _profile_name(fp, profiles_map)
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
    if fp.value in disabled_set and len(enabled_map) >= 1 and fp.value not in enabled_map:
        pname = _profile_name(fp, profiles_map)
        print(f"Profile '{pname}' is currently disabled in Dashboard. Re-enable it to switch to it.")
        sys.exit(1)
    if fp.value in (2, 3):
        from . import dgpu_gate

        if not dgpu_gate.high_power_profiles_allowed():
            pname = _profile_name(fp, profiles_map)
            print(
                f"Profile '{pname}' ({fp.value}) is unavailable while the discrete GPU is powered off (ECO / gated).",
                file=sys.stderr,
            )
            sys.exit(1)
    if ctrl.set_profile(fp):
        _record_profile_and_sync(fp.value)
        pname = _profile_name(fp, profiles_map)
        print(f"Power profile set to: {pname}  ({fp.value})")
        return
    else:
        print(f"Failed to set profile '{name}'", file=sys.stderr)
        sys.exit(1)


def cmd_profile_cycle(args) -> None:
    """Cycle to the next power profile and show OSD."""
    ctrl, profiles_map, cfg = _open_profiles(args)
    profiles_map = enabled_acpi_profiles(profiles_map, cfg)
    pids, p_data = _profile_set_for(profiles_map)
    from . import dgpu_gate

    if not dgpu_gate.high_power_profiles_allowed():
        filtered = [p for p in pids if p not in (2, 3)]
        if filtered:
            pids = filtered

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
        entry = p_data.get(next_profile_id) or p_data.get(str(next_profile_id)) or {}
        pname = entry.get("name", f"Profile {next_profile_id}")
        desc = entry.get("description") or entry.get("desc", "")
        print(f"Power profile cycled to: {pname}  ({next_profile_id})")
        show_profile_osd(pname, desc)
    else:
        print(f"Failed to cycle profile to {next_profile_id}", file=sys.stderr)
        sys.exit(1)


def cmd_profile_report(args) -> None:
    """Print a paste-able summary of auto-discovered hardware capabilities."""
    from . import __version__
    from .battery import get_battery_manager  # noqa: F401 (kept side-effect free)

    cfg = load_config()
    ctrl = AcpiController()
    caps = ctrl.capabilities if ctrl.available else None
    discovered = discover_acpi_profiles()

    print("GigaMate — model report")
    print()
    print(f"  GigaMate version:   {__version__}")
    print(f"  DMI vendor:         {get_dmi_vendor() or '-'}")
    print(f"  DMI product_name:   {get_dmi_product_name() or '-'}")
    print(f"  DMI product_family: {_read_dmi('product_family') or '-'}")
    chassis = get_dmi_chassis_type()
    print(f"  DMI chassis:        {chassis if chassis is not None else '-'}")
    if caps is not None:
        print(f"  ACPI backend:       {caps.backend}")
        print(f"  Sensors:            temp={caps.has_temperature} fan_rpm={caps.has_fan_rpm} "
              f"fan_duty={caps.has_fan_duty} fans={caps.fan_count}")
        print(f"  Profile interface present: {caps.has_power_profiles}")
    else:
        status = acpi_module_status()
        print("  ACPI backend:       none")
        print(f"  ACPI module:        {status.summary}")
    if discovered:
        names = ", ".join(
            f"{k}={v.get('name', '?')}" for k, v in sorted(discovered.items(), key=lambda kv: int(kv[0]))
        )
        print(f"  Discovered profiles: {names}")
    else:
        print("  Discovered profiles: none")
    print(f"  Selected profile:    {cfg.get('acpi_profile')}")
    try:
        from . import dgpu_gate

        gst = dgpu_gate.get_status(check_helper=False)
        gcfg = gst.get("gate_config")
        if gcfg is None:
            resolved = dgpu_gate.resolve_gate_config()
            if resolved is not None:
                gcfg = resolved[0].to_dict()
        if gcfg:
            print(
                f"  dGPU power gate:     {gcfg['path']}.{gcfg['method']} "
                f"({gcfg.get('source', 'acpi')}; quirks={gcfg.get('quirks') or []})"
            )
        else:
            print("  dGPU power gate:     none")
        print(
            f"  dGPU gate runtime:   mode={gst['mode']} gated={gst['gated']} "
            f"eval={gst['eval_backend']} bdf={gst.get('bdf') or '-'} "
            f"acpi_node={gst.get('acpi_path') or '-'}"
        )
    except Exception:
        pass
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
    """Show instructions for sharing hardware reports with the community."""
    detected = detect_keyboards()
    dmi_model = get_dmi_product_name()
    ctrl = AcpiController()

    if not detected and not ctrl.available:
        print("No Gigabyte hardware detected on this system.")
        print("Run this on the laptop you want to report.")
        sys.exit(1)

    print()
    print("GigaMate 5.0 auto-discovers ACPI profiles, LampArray RGB, and dGPU gates.")
    if dmi_model:
        print(f"  Model: {dmi_model}")
    if detected:
        vid, pid = detected[0]
        print(f"  Keyboard: {vid:04X}:{pid:04X}")
    print()
    print("  To share a hardware report or issue, run:")
    print("    gigamate profile report")
    print("    gigamate doctor")
    print()
    print("  Then paste the output at:")
    print("    https://github.com/goodeesh/GigaMate/issues")
    print()


# ────────────────────────────────────────────
# Detect commands
# ────────────────────────────────────────────


def cmd_detect(args) -> None:
    """Show all detected hardware (keyboard + ACPI)."""
    detected = detect_keyboards()
    dmi_model = get_dmi_product_name()

    print("GigaMate — Hardware Detection")
    print()

    # Model
    if dmi_model:
        print(f"  Model:    {dmi_model}")

    # Keyboard
    if detected:
        vid, pid = detected[0]
        lamparray = hardware_supports_lamparray(vid, pid)
        rgb_label = "HID LampArray" if lamparray else "USB HID"
        print(f"  Keyboard: {rgb_label}  ({vid:04X}:{pid:04X})")
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
            discovered = discover_acpi_profiles()
            print(f"  ACPI:   ✅ {backend_name} backend")
            print(f"          Temperature:    {'✅' if caps.has_temperature else '❌'}")
            print(f"          Fan RPM:        {'✅' if caps.has_fan_rpm else '❌'}")
            print(f"          Fan Duty:       {'✅' if caps.has_fan_duty else '❌'}")
            print(f"          Power Profiles: {'✅' if discovered else '❌'}")
            try:
                from . import dgpu_gate
                gcfg = dgpu_gate.resolve_gate_config()
                if gcfg:
                    print(f"          dGPU Gate:      ✅ {gcfg['path']}.{gcfg['method']} ({gcfg.get('source', 'acpi')})")
            except Exception:
                pass
            print()
            print("  For detailed probe: gigamate detect --acpi")
            print("  For full diagnostics: gigamate doctor")
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
        status = acpi_module_status()
        print("  No ACPI interface detected.")
        print(f"  Running kernel: {status.running_kernel}")
        print(f"  Module:         {status.summary}")
        print(f"  {status.hint}")
        print()
        print("  ACPI features use the bundled gigamate_acpi module.")
        print("  If the acpi_call module is already installed, it is used as a fallback.")
        return

    backend_name = {"module": "kernel module", "acpi_call": "acpi_call", "mock": "mock"}.get(
        caps.backend, caps.backend
    )
    print(f"  Backend:    {backend_name}")
    print(f"  Temperature: {'✅ Detected' if caps.has_temperature else '❌ Not available'}")
    print(f"  Fan RPM:    {'✅ Detected' if caps.has_fan_rpm else '❌ Not available'}")
    print(f"  Fan Duty:   {'✅ Detected' if caps.has_fan_duty else '❌ Not available'}")
    discovered = discover_acpi_profiles()
    if discovered:
        names = ", ".join(
            f"{k}={v.get('name', '?')}" for k, v in sorted(discovered.items(), key=lambda kv: int(kv[0]))
        )
        print(f"  Profiles:   ✅ Auto-discovered ({names})")
    elif caps.has_power_profiles:
        print("  Profiles:   ⚠️ Interface detected, no profiles discovered (run 'gigamate doctor')")
    else:
        print("  Profiles:   ❌ Not available")
    if caps.fan_count > 0:
        print(f"  Fans:       {caps.fan_count}")
    print()


# ────────────────────────────────────────────
# Calibrate command
# ────────────────────────────────────────────


def cmd_hotkeys_list(args) -> None:
    """Show the hotkey mappings configured for a keyboard."""
    from . import protocol
    from .config import load as load_config
    from .hotkeys import DEFAULT_GIGABYTE_HOTKEYS, merge_hotkey_specs

    detected = protocol.detect_keyboards()
    if not detected and args.vid is None and args.pid is None:
        print("No supported keyboard detected.")
        sys.exit(1)
    vid = args.vid if args.vid is not None else detected[0][0]
    pid = args.pid if args.pid is not None else detected[0][1]
    cfg = load_config()
    specs = merge_hotkey_specs(
        cfg.get("custom_hotkeys") or {},
        cfg.get("hotkey_overrides") or {},
        DEFAULT_GIGABYTE_HOTKEYS,
    )

    print("GigaMate — Hotkey mappings")
    print()
    print(f"  Keyboard: {vid:04X}:{pid:04X}")
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
    from . import protocol
    from .config import load as load_config
    from .hotkeys import (
        DEFAULT_GIGABYTE_HOTKEYS,
        format_report,
        list_hotkey_hidraw,
        match_spec,
        merge_hotkey_specs,
    )

    detected = protocol.detect_keyboards()
    if not detected and args.vid is None and args.pid is None:
        print("No supported keyboard detected.")
        sys.exit(1)
    vid = args.vid if args.vid is not None else detected[0][0]
    pid = args.pid if args.pid is not None else detected[0][1]

    nodes = list_hotkey_hidraw(vid, pid)
    if args.interface is not None:
        nodes = [(i, n) for i, n in nodes if i == args.interface]
    if not nodes:
        print(f"No hidraw interfaces for keyboard {vid:04X}:{pid:04X}.")
        sys.exit(1)

    cfg = load_config()
    specs = merge_hotkey_specs(
        cfg.get("custom_hotkeys") or {},
        cfg.get("hotkey_overrides") or {},
        DEFAULT_GIGABYTE_HOTKEYS,
    )

    print("GigaMate — hotkey capture (Ctrl-C to stop)", flush=True)
    print(f"  Keyboard ({vid:04X}:{pid:04X})", flush=True)
    print("  Listening on: " + ", ".join(f"iface {i} ({n})" for i, n in nodes), flush=True)
    print("  Press the buttons whose signatures you want to capture.", flush=True)

    fds = {}
    open_errors = {}
    for iface, path in nodes:
        try:
            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = iface
        except OSError as exc:
            print(f"  Cannot open {path}: {exc}")
            open_errors[path] = exc
    if not fds:
        print("Could not open any hidraw interface.")
        for path, exc in open_errors.items():
            if isinstance(exc, PermissionError):
                _explain_hidraw_access(path, "keyboard hidraw interface")
                break
        else:
            print("Check that the keyboard is connected.")
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


def cmd_doctor(args) -> None:
    """Diagnose hardware and keyboard permissions."""
    from .doctor import run_doctor

    sys.exit(run_doctor())


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
        print("RGB calibration was removed in GigaMate 5.0 (LampArray HID auto-detects RGB).")
        return
    if args.list:
        list_options()
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
        print("Available subcommands: rgb, status, profile, detect, doctor, battery, gpu, hotkeys, version")
        print("For help: gigamate --help")
        return

    # RGB set command
    cmd_rgb_static(args)


def cmd_rgb_reset(args) -> None:
    """Report LampArray driver status (no kernel driver detach needed in v5.0)."""
    dev = get_keyboard(args.vid, args.pid)
    if dev is None:
        print("Keyboard not found")
        if detect_keyboards() and not hardware_supports_lamparray(args.vid, args.pid):
            print("Note: Non-LampArray pre-2025 keyboards require GigaMate v4.5.x.")
        sys.exit(1)
    print("GigaMate 5.0 uses HID LampArray (/dev/hidraw*) without detaching kernel drivers; typing remains active.")


def list_options():
    """List available RGB options."""
    print("Available effects: static, off, cycle")
    print()
    print("Available palette colours (or any #RRGGBB hex):")
    for name in sorted(PALETTE_HEX.keys()):
        print(f"  {name:15s} ({PALETTE_HEX[name]})")
    print()
    print("Brightness: 0 (off) or 5-100%")


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────


def cmd_version(args) -> None:
    """Show GigaMate version."""
    from . import __version__
    print(f"GigaMate {__version__}")


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
    from .battery import (
        get_battery_manager,
        evaluate_calibration_state,
        start_topup,
        cancel_topup,
    )
    mgr = get_battery_manager()
    if not mgr.is_available:
        print("No battery detected on this system.", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "topup", False):
        if start_topup(mgr):
            print("Top-up charge to 100% initiated. Limit will automatically restore once full.")
            return
        else:
            print("Failed to initiate top-up charge (unsupported or error).", file=sys.stderr)
            sys.exit(1)

    if getattr(args, "cancel_topup", False):
        cancel_topup(mgr, restore_limit=True)
        print("Top-up charge cancelled; configured charge threshold restored.")
        return

    mutated = False
    if getattr(args, "auto_topup", None) is not None:
        enabled = (args.auto_topup.strip().lower() == "on")
        update_config(lambda cfg: {**cfg, "charge_calibration_auto": enabled})
        print(f"Automatic periodic battery calibration {'enabled' if enabled else 'disabled'}.")
        mutated = True

    if getattr(args, "topup_interval", None) is not None:
        days = max(1, min(365, int(args.topup_interval)))
        update_config(lambda cfg: {**cfg, "charge_calibration_interval_days": days})
        print(f"Battery calibration interval set to {days} days.")
        mutated = True

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

    if mutated:
        return

    cfg = load_config()
    evaluate_calibration_state(mgr, cfg)
    cfg = load_config()

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
        topup_active = cfg.get("charge_topup_active", False)
        if topup_active:
            limit_str = "100% (Top-Up / Calibration active)"
        else:
            limit_str = f"{info.charge_limit}%" if info.charge_limit else "None (100%)"
        print(f"  Charge Limit:   {limit_str} (Backend: {info.backend})")
        auto_cal = cfg.get("charge_calibration_auto", False)
        cal_interval = cfg.get("charge_calibration_interval_days", 30)
        print(f"  Auto Calibrate: {'Enabled' if auto_cal else 'Disabled'} (every {cal_interval} days)")
        last_full = cfg.get("last_full_charge_ts", 0)
        if last_full > 0:
            days_ago = max(0, int((time.time() - last_full) / 86400))
            if days_ago == 0:
                age_str = "today"
            elif days_ago == 1:
                age_str = "yesterday"
            else:
                age_str = f"{days_ago} days ago"
            print(f"  Last Full:      {age_str}")
        else:
            print("  Last Full:      Never recorded")
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
  gigamate rgb static purple         Set keyboard colour (or #RRGGBB)
  gigamate rgb off                   Turn backlight off
  gigamate status                    Show hardware status
  gigamate profile gaming            Set Gaming power profile
  gigamate gpu power status          Check dGPU power gate status
  gigamate detect                    Detect hardware
  gigamate doctor                    Diagnose hardware and permissions
  gigamate center                    Launch GigaMate Center GUI""",
    )
    parser.add_argument("--center", "-c", action="store_true", help="Launch GigaMate Center GUI")
    parser.add_argument("--tray", "-t", action="store_true", help="Launch GigaMate System Tray daemon")
    sub = parser.add_subparsers(dest="command", help="Sub-command")

    # --- rgb subcommand ---
    rgb_parser = sub.add_parser("rgb", help="Keyboard RGB control")
    rgb_sub = rgb_parser.add_subparsers(dest="rgb_action", help="RGB action")

    # rgb static
    static_parser = rgb_sub.add_parser("static", help="Set static colour")
    static_parser.add_argument("colour", nargs="?", default="light_purple", help="Colour name or #RRGGBB")
    static_parser.add_argument("brightness", nargs="?", default=None, help="Brightness: off/dim/full or 0, 5..100")
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
                                     "or a profile name/number to set")
    profile_parser.add_argument("name", nargs="?", default=None,
                                help="Profile name/number (for 'set')")
    profile_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    profile_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- detect subcommand ---
    detect_parser = sub.add_parser("detect", help="Detect hardware")
    detect_parser.add_argument("--acpi", action="store_true", help="Detailed ACPI probe")
    detect_parser.add_argument("--vid", type=lambda x: int(x, 16), default=None)
    detect_parser.add_argument("--pid", type=lambda x: int(x, 16), default=None)

    # --- doctor subcommand ---
    sub.add_parser("doctor", help="Diagnose hardware and permissions")

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
    gpu_power_parser = gpu_sub.add_parser(
        "power", help="Discrete GPU power gate (Optimus / ECO / Auto / manual off & on)")
    gpu_power_parser.add_argument(
        "action", nargs="?", default="status",
        choices=["status", "off", "on", "eco", "auto", "optimus", "probe", "sync", "confirm-dialog"],
        help="Power-gate action or mode (default: status)")
    gpu_power_parser.add_argument(
        "--live-test", action="store_true",
        help="With 'probe', run a live off -> on -> rescan test (idle GPU, no displays)")
    gpu_power_parser.add_argument(
        "--force-close", action="store_true",
        help="With 'off', 'eco', or 'auto', close user apps/services holding the dGPU and wait briefly for suspend before gating off")
    gpu_power_parser.add_argument(
        "--trigger", default="eco", choices=["eco", "auto", "auto_ac_unplug", "boot_battery"],
        help="With 'confirm-dialog', origin trigger label")
    gpu_power_parser.add_argument(
        "--target-mode", default="eco", choices=["eco", "auto"],
        help="With 'confirm-dialog', requested target power mode")
    gpu_power_parser.add_argument(
        "--prev-mode", default=None,
        help="With 'confirm-dialog', previous mode before prompt")
    gpu_usbc_parser = gpu_sub.add_parser(
        "usbc-budget", help="Opt-in USB-C PD power budget (override 30 W cap on >=90 W USB-C PD)")
    gpu_usbc_parser.add_argument(
        "action", nargs="?", default="status",
        help="'status', 'on', 'off', 'set', 'buffer', or target GPU watts (30-150)")
    gpu_usbc_parser.add_argument(
        "value", nargs="?", default=None,
        help="Target GPU watts (30-150) for 'set', or buffer percent (15-25) for 'buffer'")

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
    battery_parser.add_argument("--topup", action="store_true",
                                help="Initiate one-time top-up charge to 100%% (calibration)")
    battery_parser.add_argument("--cancel-topup", action="store_true",
                                help="Cancel active top-up charge and restore configured limit")
    battery_parser.add_argument("--auto-topup", choices=["on", "off"], default=None,
                                help="Enable or disable periodic auto-calibration (100%% top-up every N days)")
    battery_parser.add_argument("--topup-interval", type=int, default=None,
                                help="Set interval in days for periodic auto-calibration (1..365)")
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
    elif args.command == "doctor":
        cmd_doctor(args)
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
        elif args.gpu_action == "power":
            cmd_gpu_power(args)
        elif args.gpu_action == "usbc-budget":
            cmd_gpu_usbc_budget(args)
        else:
            print("GPU actions: status, power, usbc-budget, undervolt, maxclock, memoffset, auto")
            print("Examples: gigamate gpu status | gigamate gpu power eco | gigamate gpu usbc-budget status | gigamate gpu undervolt 100")
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
    elif action == "idle":
        cmd_rgb_idle(args)
    else:
        print("RGB actions: static, off, detect, cycle, reset, idle")
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
        print("Experimental profile mode was removed in GigaMate 5.0.")
        sys.exit(1)
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
    """Handle removed calibration subcommand."""
    print("Calibration was removed in GigaMate 5.0 (ACPI profiles and LampArray RGB are auto-discovered).")
    sys.exit(1)


if __name__ == "__main__":
    main()
