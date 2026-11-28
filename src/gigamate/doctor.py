"""Hardware and permission diagnostics for ``gigamate doctor``.

The keyboard checks are read-only: they inspect the udev rule, the hidraw
node, and the logind session, then print the commands needed to fix access.
"""

from __future__ import annotations

import glob
import grp
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .acpi import acpi_module_status
from .updates import is_package_managed


RULE_PATH = Path("/etc/udev/rules.d/70-gigamate.rules")
VENDOR_RULE_PATH = Path("/usr/lib/udev/rules.d/70-gigamate.rules")
LEGACY_RULE_PATH = Path("/etc/udev/rules.d/99-gigamate.rules")
RULE_URL = (
    "https://raw.githubusercontent.com/goodeesh/GigaMate/main/data/70-gigamate.rules"
)
KEYBOARD_VENDORS = {0x0414, 0x1044, 0x04D9}


def _hid_identity(device: Path) -> Optional[Tuple[int, int]]:
    try:
        lines = (device / "uevent").read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.startswith("HID_ID="):
            continue
        try:
            _, vendor, product = line.split("=", 1)[1].split(":")
            return int(vendor, 16), int(product, 16)
        except ValueError:
            return None
    return None


def _hid_name(device: Path) -> str:
    try:
        lines = (device / "uevent").read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith("HID_NAME="):
            return line.split("=", 1)[1]
    return ""


def _interface_number(device: Path) -> Optional[int]:
    path = os.path.realpath(device)
    if ":1." not in path:
        return None
    tail = path.split(":1.", 1)[1]
    digits = ""
    for char in tail:
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


def find_keyboard_hidraws() -> List[Dict[str, object]]:
    """Return hidraw nodes belonging to known Gigabyte keyboard vendors."""
    found: List[Dict[str, object]] = []
    for sys_node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        device = Path(sys_node) / "device"
        identity = _hid_identity(device)
        if identity is None or identity[0] not in KEYBOARD_VENDORS:
            continue
        vid, pid = identity
        name = _hid_name(device)
        # 0414 is Gigabyte's own VID. The shared ODM VIDs are only accepted
        # when the product matches a known keyboard or names Gigabyte/AORUS.
        if vid == 0x04D9 and pid != 0x8105:
            continue
        if vid == 0x1044 and not any(
            mark in name.upper() for mark in ("GIGABYTE", "AORUS")
        ):
            continue
        found.append({
            "node": f"/dev/{Path(sys_node).name}",
            "vid": vid,
            "pid": pid,
            "name": name,
            "interface": _interface_number(device),
        })
    return found


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _has_user_acl(path: str) -> bool:
    getfacl = shutil.which("getfacl")
    if not getfacl:
        return False
    try:
        proc = subprocess.run(
            [getfacl, "-p", path], capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(
        line.startswith("user:") and not line.startswith("user::")
        for line in proc.stdout.splitlines()
    )


def node_state(path: str) -> Dict[str, object]:
    st = os.stat(path)
    acl = _has_user_acl(path)
    return {
        "mode": stat.filemode(st.st_mode) + ("+" if acl else ""),
        "owner": _user_name(st.st_uid),
        "group": _group_name(st.st_gid),
        "acl": acl,
    }


def open_error(path: str) -> Optional[OSError]:
    """Try the same read-write open the RGB code uses, and return any error."""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError as exc:
        return exc
    os.close(fd)
    return None


def _active_rule_path() -> Optional[Path]:
    for path in (RULE_PATH, VENDOR_RULE_PATH):
        if path.exists():
            return path
    return None


def rule_state() -> str:
    active_rule = _active_rule_path()
    new_present = active_rule is not None
    old_present = LEGACY_RULE_PATH.exists()
    if old_present and not new_present:
        return "old 99-gigamate.rules present; reinstall GigaMate"
    if old_present:
        return "present, but old 99-gigamate.rules is still installed"
    if not new_present:
        return "missing"
    try:
        text = active_rule.read_text()
    except OSError:
        return "unreadable"
    if 'SUBSYSTEM=="hidraw"' in text and "0414" in text:
        return "present"
    return "present but does not cover Gigabyte keyboards"


def find_shadowed_legacy_files(
    home: Optional[Path] = None,
    etc_polkit_dir: Path = Path("/etc/polkit-1/rules.d"),
    etc_udev_dir: Path = Path("/etc/udev/rules.d"),
    method: Optional[str] = None,
) -> List[str]:
    """Return leftover legacy install files that may shadow a native package."""
    if not is_package_managed(method):
        return []
    home_dir = home if home is not None else Path.home()
    candidates = [
        home_dir / ".local" / "bin" / "gigamate",
        home_dir / ".local" / "bin" / "gigamate-tray",
        home_dir / ".local" / "bin" / "gigamate-center",
        home_dir / ".local" / "bin" / "gigamate-dgpu-watch",
        home_dir / ".config" / "systemd" / "user" / "gigamate.service",
        home_dir / ".config" / "systemd" / "user" / "gigamate-dgpu.service",
        etc_polkit_dir / "50-gigamate-dgpu.rules",
        etc_polkit_dir / "50-gigamate-dgpu-gate.rules",
        etc_polkit_dir / "50-gigamate-repair.rules",
        etc_polkit_dir / "50-gigamate-powerd.rules",
        etc_udev_dir / "70-gigamate.rules",
        etc_udev_dir / "99-gigamate-dgpu-ac.rules",
        etc_udev_dir / "99-gigamate-smartshift.rules",
    ]
    found: List[str] = []
    for path in candidates:
        try:
            if path.exists() or path.is_symlink():
                found.append(str(path))
        except OSError:
            continue
    return found


def session_details() -> Tuple[Dict[str, str], str]:
    details: Dict[str, str] = {}
    session_id = os.environ.get("XDG_SESSION_ID", "").strip()
    if session_id and shutil.which("loginctl"):
        try:
            proc = subprocess.run(
                [
                    "loginctl", "show-session", session_id,
                    "-p", "Active", "-p", "Remote", "-p", "Seat", "-p", "Type",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in proc.stdout.splitlines():
                key, _, value = line.partition("=")
                if key:
                    details[key] = value
        except (OSError, subprocess.SubprocessError):
            pass

    logind = "unknown"
    if shutil.which("systemctl"):
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "systemd-logind"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            logind = (proc.stdout or proc.stderr or "unknown").strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return details, logind


def permission_lines(path: str) -> List[str]:
    """Return a short, actionable explanation for a denied hidraw open."""
    rule_path = _active_rule_path() or RULE_PATH
    lines = [f"udev rule: {rule_state()} ({rule_path})"]
    try:
        state = node_state(path)
        lines.append(f"node: {state['mode']} {state['owner']}:{state['group']}")
    except OSError:
        pass
    error = open_error(path)
    if error is not None:
        lines.append(f"open error: {error}")

    if (
        "missing" in lines[0]
        or "does not cover" in lines[0]
        or "old 99-gigamate.rules" in lines[0]
    ):
        lines.extend([
            "",
            "Permanent fix (do not use chmod, it is lost on reboot):",
            f"  sudo install -Dm644 <GigaMate-checkout>/data/70-gigamate.rules {RULE_PATH}",
            f"  # or: curl -sSL {RULE_URL} | sudo tee {RULE_PATH} >/dev/null",
            f"  sudo rm -f {LEGACY_RULE_PATH}",
            "  sudo udevadm control --reload-rules",
            "  sudo udevadm trigger --subsystem-match=hidraw",
            "  reboot",
        ])
    else:
        lines.extend([
            "",
            "The udev rule is installed but access is still denied.",
            "Check the session with 'gigamate doctor' (remote sessions and",
            "systems without systemd-logind do not get the uaccess ACL).",
            "If you just installed the rule, reboot or log out and back in.",
            "To reload now:",
            "  sudo udevadm control --reload-rules",
            "  sudo udevadm trigger --subsystem-match=hidraw",
        ])
    return lines


def run_doctor() -> int:
    """Print the diagnostic report. Return 1 when a keyboard is inaccessible."""
    print("GigaMate - Diagnostics")
    print()

    keyboards = find_keyboard_hidraws()
    failed_node: Optional[str] = None
    if not keyboards:
        print("Keyboard: not detected")
        print()

    grouped: Dict[Tuple[object, object, object], List[Dict[str, object]]] = {}
    for keyboard in keyboards:
        key = (keyboard["vid"], keyboard["pid"], keyboard["name"])
        grouped.setdefault(key, []).append(keyboard)

    for (vid, pid, name), nodes in grouped.items():
        print("Keyboard")
        print(f"  VID:PID      {vid:04X}:{pid:04X}")
        if name:
            print(f"  name         {name}")
        print("  nodes")
        for keyboard in nodes:
            node = str(keyboard["node"])
            state: Dict[str, object] = {}
            try:
                state = node_state(node)
                permissions = f"{state['mode']} {state['owner']}:{state['group']}"
            except OSError:
                permissions = "unknown"
            error = open_error(node)
            if error is None:
                access = "OK" + (" (user ACL)" if state.get("acl") else "")
            else:
                access = f"DENIED ({error.strerror or error})"
                if failed_node is None:
                    failed_node = node
            iface = keyboard["interface"]
            label = f"iface {iface}" if iface is not None else "iface ?"
            print(f"    {node}  {label}  {permissions}  {access}")
        rule_path = _active_rule_path() or RULE_PATH
        print(f"  udev rule    {rule_state()} ({rule_path})")
        from .lamparray import diagnose_lamparray

        diag = diagnose_lamparray(int(vid), int(pid))
        status = diag["status"]
        if status == "supported":
            rgb_mode = "24-bit HID LampArray"
        elif status in ("unsupported_firmware", "unsupported_layout"):
            rgb_mode = "unavailable (unsupported)"
        else:
            rgb_mode = "unavailable"
        print(f"  rgb mode     {rgb_mode}")
        print(f"  lamparray    {status} — {diag['reason']}")
        if status in ("unsupported_firmware", "unsupported_layout"):
            print("  note         GigaMate 5.0+ requires HID LampArray (Usage Page 0x59); for pre-2025 keyboards install GigaMate v4.5.x from GitHub Releases.")
        print()

    details, logind = session_details()
    print("Session")
    if details:
        print(
            f"  {details.get('Seat', '?')}, type {details.get('Type', '?')}, "
            f"active={details.get('Active', '?')}, remote={details.get('Remote', '?')}"
        )
    else:
        print("  unknown (XDG_SESSION_ID is not set)")
    print(f"  logind       {logind}")
    print()

    status = acpi_module_status()
    print("ACPI")
    print(f"  kernel       {status.running_kernel}")
    print(f"  module       {status.summary}")
    print(f"  {status.hint}")
    try:
        from .power_limits import discover_acpi_profiles

        discovered = discover_acpi_profiles()
    except Exception:
        discovered = {}
    if discovered:
        profiles_str = ", ".join(
            f"{pid}: {info.get('name', f'Profile {pid}')}"
            for pid, info in sorted(discovered.items())
        )
        print(f"  profiles     {profiles_str}")
    else:
        print("  profiles     none (WMBD 0xED not found in wmbd_aml)")
    print()

    try:
        from . import dgpu_gate

        gst = dgpu_gate.get_status(check_helper=False)
        if gst.get("bdf") or gst.get("gated"):
            print("dGPU Power Gate")
            print(f"  mode         {gst['mode']}")
            print(f"  state        {'Off (gated)' if gst['gated'] else (gst.get('runtime_status') or 'present')}")
            print(f"  eval backend {gst['eval_backend']}")
            print(f"  helper       {'installed' if gst['helper_installed'] else 'missing (/usr/lib/gigamate/gigamate-dgpu-gate)'}")
            ac_rule_present = (
                Path("/etc/udev/rules.d/99-gigamate-dgpu-ac.rules").exists()
                or Path("/usr/lib/udev/rules.d/99-gigamate-dgpu-ac.rules").exists()
            )
            print(f"  ac udev rule {'present' if ac_rule_present else 'missing'}")
            gcfg = gst.get("gate_config")
            if gcfg:
                print(f"  gate target  {gcfg['path']}.{gcfg['method']} ({gcfg.get('source', 'acpi')})")
            else:
                print("  gate target  none detected in ACPI")
            print()
    except Exception:
        pass

    try:
        from . import usbc_power
        from .acpi import AcpiController
        from .config import load as load_config
        from .gpu import get_gpu_state

        cfg = load_config()
        src = usbc_power.detect_power_source()
        caps = usbc_power.read_cached_gpu_caps_only()
        ctrl = AcpiController()
        gpu = get_gpu_state()
        has_cpu_ppt = ctrl.available and ctrl.has_cpu_ppt_control()
        has_ctgp = ctrl.available and ctrl.has_gpu_ctgp_control()
        has_db = ctrl.available and ctrl.has_gpu_dynamic_boost_control()
        fw_usbc = ctrl.read_usbc_adapter() if ctrl.available else src.firmware_usbc_flag
        env = usbc_power.compute_budget_envelope(
            src.detected_wattage_w,
            caps.default_tgp_w,
            caps.max_constraint_w,
            has_ctgp_ctrl=has_ctgp,
            has_cpu_ppt_ctrl=has_cpu_ppt,
            has_dyn_boost_ctrl=has_db,
            has_nvidia_gpu=bool(gpu.present and gpu.vendor == "nvidia"),
            buffer_pct=int(cfg.get("usbc_buffer_pct", usbc_power.DEFAULT_BUFFER_PCT) or usbc_power.DEFAULT_BUFFER_PCT),
        )
        print("USB-C Power Budget")
        w_str = f"{src.detected_wattage_w} W ({src.wattage_source})" if src.detected_wattage_w else "unknown"
        curr_str = f", {src.negotiated_current_a:.2f} A" if src.negotiated_current_a is not None else ""
        print(f"  power source {src.kind} (charger={w_str}{curr_str}, ac_online={src.ac_online})")
        print(
            f"  sysfs ctrls  cpu_ppt={'yes' if has_cpu_ppt else 'no'}, "
            f"gpu_ctgp={'yes' if has_ctgp else 'no'}, "
            f"dyn_boost={'yes' if has_db else 'no'}, "
            f"usbc_adapter={fw_usbc}"
        )
        tgp_str = (
            f"{caps.default_tgp_w} W" + (f" (max {caps.max_constraint_w} W)" if caps.max_constraint_w else "")
            if caps.default_tgp_w is not None
            else "uncached (not queried to keep sleeping dGPU off)"
        )
        print(f"  cached gpu   {tgp_str}")
        buf_cfg = int(cfg.get("usbc_buffer_pct", usbc_power.DEFAULT_BUFFER_PCT) or usbc_power.DEFAULT_BUFFER_PCT)
        opt_str = (
            f"enabled (target {cfg.get('usbc_gpu_budget_w', 55)} W, buffer {buf_cfg}%)"
            if cfg.get("usbc_power_budget_enabled")
            else f"disabled (opt-in, buffer {buf_cfg}%)"
        )
        print(f"  opt-in       {opt_str}")
        if env.supported:
            print(
                f"  envelope     {env.buffer_w} W buffer ({env.buffer_pct}%) + {env.silicon_budget_w} W silicon "
                f"(GPU {env.gpu_min_w}..{env.gpu_max_w} W)"
            )
        elif src.kind == "usbc_pd":
            print(f"  envelope     unsupported ({env.reason})")
        print()
    except Exception:
        pass

    shadowed = find_shadowed_legacy_files()
    if shadowed:
        print("Legacy Install Shadowing (warning)")
        print("  Package-managed install detected, but legacy files still exist and may shadow system files:")
        for path in shadowed:
            print(f"    {path}")
        print("  Remove these leftover files so the native package files take effect.")
        print()

    if failed_node is not None:
        print("Keyboard access failed:")
        for line in permission_lines(failed_node):
            print(f"  {line}" if line else "")
        return 1
    return 0
