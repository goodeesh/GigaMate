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


RULE_PATH = Path("/etc/udev/rules.d/99-gigamate.rules")
RULE_URL = (
    "https://raw.githubusercontent.com/goodeesh/GigaMate/main/data/99-gigamate.rules"
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


def rule_state() -> str:
    if not RULE_PATH.exists():
        return "missing"
    try:
        text = RULE_PATH.read_text()
    except OSError:
        return "unreadable"
    if 'SUBSYSTEM=="hidraw"' in text and "0414" in text:
        return "present"
    return "present but does not cover Gigabyte keyboards"


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
    lines = [f"udev rule: {rule_state()} ({RULE_PATH})"]
    try:
        state = node_state(path)
        lines.append(f"node: {state['mode']} {state['owner']}:{state['group']}")
    except OSError:
        pass
    error = open_error(path)
    if error is not None:
        lines.append(f"open error: {error}")

    if "missing" in lines[0] or "does not cover" in lines[0]:
        lines.extend([
            "",
            "Permanent fix (do not use chmod, it is lost on reboot):",
            f"  sudo install -Dm644 <GigaMate-checkout>/data/99-gigamate.rules {RULE_PATH}",
            f"  # or: curl -sSL {RULE_URL} | sudo tee {RULE_PATH} >/dev/null",
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
        print(f"  udev rule    {rule_state()} ({RULE_PATH})")
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
    print()

    if failed_node is not None:
        print("Keyboard access failed:")
        for line in permission_lines(failed_node):
            print(f"  {line}" if line else "")
        return 1
    return 0
