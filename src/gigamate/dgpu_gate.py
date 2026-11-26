"""GigaMate — Discrete GPU Power Gate (AI Power Gear III for Linux).

Drives true hardware dGPU power gating via ACPI (`_OFF`/`_ON` on a PowerResource
such as ``\\_SB.PCI0.GPP9.PG00`` or `_PS3`/`_PS0` on the dGPU device) paired
with PCI device removal and bus rescanning.

Three modes (matching GiMATE 1.1):
- **optimus** (default): no gating; kernel RTD3 / runtime PM manages D3cold.
- **eco**: dGPU power rail cut until the user switches modes.
- **auto**: gated off on battery, restored when AC plugs in.

All monitoring, guard, and topology functions are purely sysfs/procfs reads and
NEVER touch NVML or `nvidia-smi`, so they never wake a sleeping dGPU.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .acpi import GIGAMATE_ACPI_SYSFS, acpi_eval_backend, read_dgpu_acpi_path
from .battery import get_battery_manager
from .config import DEFAULT_CONFIG, load as load_config, update_config
from .dgpu_tune import wake_holders as _raw_nvidia_wake_holders
from .gpu import AMD_VENDOR, NVIDIA_VENDOR, _UMA_APERTURE_MAX_BYTES

logger = logging.getLogger(__name__)


@dataclass
class DgpuGateConfig:
    """ACPI dGPU power-gate method configuration."""

    path: str
    method: str = "_OFF"
    on_method: str = "_ON"
    sta_method: str = "_STA"
    quirks: List[str] = field(default_factory=list)
    source: str = "auto"

    @property
    def effective_on_method(self) -> str:
        if self.on_method:
            return self.on_method
        return "_PS0" if self.method == "_PS3" else "_ON"

    @property
    def effective_sta_method(self) -> str:
        return self.sta_method or "_STA"

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "method": self.method,
            "on_method": self.effective_on_method,
            "sta_method": self.effective_sta_method,
            "quirks": list(self.quirks),
            "source": self.source,
        }

_MANAGED_POWER_DAEMONS = frozenset({"nvidia-powerd"})

# Session/compositor/system processes that must NEVER be terminated by force-close.
_PROTECTED_COMM = frozenset({
    "kwin_wayland",
    "kwin_x11",
    "xwayland",
    "xorg",
    "gnome-shell",
    "plasmashell",
    "sway",
    "hyprland",
    "mutter",
    "weston",
    "wayfire",
    "sddm",
    "gdm",
    "lightdm",
    "systemd",
    "dbus-daemon",
    "dbus-broker",
    "pipewire",
    "wireplumber",
    "pulseaudio",
    "nvidia-powerd",
    "gigamate",
    "gigamate-tray",
    "gigamate-center",
    "gigamate-dgpu-watch",
})

_PROTECTED_UNIT_PREFIXES = (
    "gigamate",
    "plasma-",
    "kwin",
    "dbus",
    "pipewire",
    "wireplumber",
    "xdg-",
    "gnome-",
    "wayland",
    "xorg",
    "at-spi",
    "gvfs",
)


def _nvidia_wake_holders() -> List[str]:
    """Return user/app process names holding /dev/nvidia* (excluding nvidia-powerd)."""
    return [h for h in _raw_nvidia_wake_holders() if h not in _MANAGED_POWER_DAEMONS]

MODE_OPTIMUS = "optimus"
MODE_ECO = "eco"
MODE_AUTO = "auto"
VALID_MODES = (MODE_OPTIMUS, MODE_ECO, MODE_AUTO)

PCI_SYSFS = Path("/sys/bus/pci/devices")
DRM_SYSFS = Path("/sys/class/drm")
STATE_FILE = Path("/run/gigamate-dgpu-gate.json")
AC_EVENT_FILE = Path("/run/gigamate/ac-event")
HELPER_INSTALLED = "/usr/lib/gigamate/gigamate-dgpu-gate"

_HELPER_TIMEOUT = 25.0
_RETRY_BACKOFF_SEC = 15.0
_BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
_GATE_LOCK = threading.Lock()
_RESUME_THREAD: Optional[threading.Thread] = None

# Post-boot settle window: disabled by default because the wake-first hand-off
# handles cold PG00._ON safely at any uptime. Override for testing with
# GIGAMATE_GATE_SETTLE_SEC.
_BOOT_SETTLE_SEC = float(os.environ.get("GIGAMATE_GATE_SETTLE_SEC", "0") or 0)

# User-session single-flight lock shared by the tray, Center, watcher, CLI and
# confirm dialog (all separate processes) so exactly one gate transition runs.
_TRANSITION_LOCK_NAME = "gigamate-dgpu-transition.lock"
_TRANSITION_LOCK_WAIT_SEC = 10.0

# Watcher edge-tracking state (protected by _WATCHER_LOCK).
_SUPPORT_CACHE_TTL_SEC = 60.0
_WATCHER_LOCK = threading.Lock()
_watcher_state: Dict[str, object] = {
    "last_ac": None,
    "last_event_mtime": None,
    "last_mode": None,
    "last_attempt_at": 0.0,
    "last_attempt_rstatus": None,
    "last_policy_publish": 0.0,
    "awaiting_confirm_at": 0.0,
    "supported_cache": None,
    "supported_checked_at": 0.0,
    "want_off_since": 0.0,
    "boot_prompted": False,
}


@dataclass(frozen=True)
class DgpuTopology:
    """Sysfs/ACPI topology discovered for the discrete GPU (read-only)."""

    bdf: Optional[str] = None
    vendor: Optional[str] = None
    audio_bdf: Optional[str] = None
    bridge_bdf: Optional[str] = None
    acpi_path: Optional[str] = None
    bridge_acpi_path: Optional[str] = None
    power_resources: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuardResult:
    """Result of pre-gate-off safety checks."""

    allowed: bool
    reason: Optional[str] = None  # "not_present", "display_connected", "gpu_active"
    message: str = ""
    displays: List[str] = field(default_factory=list)
    holders: List[str] = field(default_factory=list)
    runtime_status: Optional[str] = None


@dataclass(frozen=True)
class HolderProcess:
    """A process currently holding an open ``/dev/nvidia*`` descriptor."""

    pid: int
    name: str
    uid: int
    cmdline: str = ""
    user_unit: Optional[str] = None
    protected: bool = False

    @property
    def label(self) -> str:
        if self.user_unit:
            return f"{self.name} ({self.user_unit})"
        return f"{self.name} (PID {self.pid})"


def _read_text(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        return text or None
    except OSError:
        return None


def normalize_acpi_path(path: str) -> str:
    """Normalize kernel-padded ``\\_SB_.`` segment names to ``\\_SB.``."""
    return path.replace("\\_SB_.", "\\_SB.")


def _is_integrated_amd(device: Path) -> bool:
    if (device / "uma").is_dir():
        return True
    total = _read_text(device / "mem_info_vram_total")
    if total:
        try:
            if int(total) < _UMA_APERTURE_MAX_BYTES:
                return True
        except (TypeError, ValueError):
            pass
    return False


def find_dgpu_topology(
    pci_sysfs: Path = PCI_SYSFS,
    acpi_sysfs: Path = GIGAMATE_ACPI_SYSFS,
) -> DgpuTopology:
    """Discover discrete GPU BDF, audio sibling, root bridge, and ACPI paths.

    Pure sysfs read; never wakes a suspended dGPU.
    """
    chosen_bdf: Optional[str] = None
    chosen_vendor: Optional[str] = None
    amd_bdf: Optional[str] = None

    if pci_sysfs.is_dir():
        try:
            entries = sorted(pci_sysfs.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for entry in entries:
            if not entry.is_dir() or not _BDF_RE.fullmatch(entry.name):
                continue
            vendor = (_read_text(entry / "vendor") or "").lower()
            klass = (_read_text(entry / "class") or "").lower()
            if not klass.startswith("0x03"):
                continue
            if vendor == NVIDIA_VENDOR and (
                klass.startswith("0x0300") or klass.startswith("0x0302")
            ):
                chosen_bdf = entry.name
                chosen_vendor = "nvidia"
                break
            if vendor == AMD_VENDOR and amd_bdf is None:
                if _is_integrated_amd(entry):
                    continue
                boot_vga = _read_text(entry / "boot_vga")
                if boot_vga == "0" or (klass.startswith("0x0302") and boot_vga != "1"):
                    amd_bdf = entry.name

    if chosen_bdf is None and amd_bdf is not None:
        chosen_bdf = amd_bdf
        chosen_vendor = "amd"

    cached_acpi = read_dgpu_acpi_path(acpi_sysfs)

    if chosen_bdf is None:
        return DgpuTopology(acpi_path=cached_acpi)

    dev = pci_sysfs / chosen_bdf
    audio_bdf: Optional[str] = None
    cand_audio = f"{chosen_bdf[:-1]}1"
    if cand_audio != chosen_bdf and (pci_sysfs / cand_audio).is_dir():
        aklass = (_read_text(pci_sysfs / cand_audio / "class") or "").lower()
        if aklass.startswith("0x0403"):
            audio_bdf = cand_audio

    bridge_bdf: Optional[str] = None
    try:
        parent_name = os.path.basename(os.path.dirname(os.path.realpath(dev)))
        if _BDF_RE.fullmatch(parent_name):
            bklass = (_read_text(pci_sysfs / parent_name / "class") or "").lower()
            if bklass.startswith("0x0604"):
                bridge_bdf = parent_name
    except OSError:
        bridge_bdf = None

    acpi_path = _read_text(dev / "firmware_node" / "path") or cached_acpi
    bridge_acpi_path = (
        _read_text(pci_sysfs / bridge_bdf / "firmware_node" / "path")
        if bridge_bdf
        else None
    )

    power_resources: List[str] = []
    seen = set()
    pr_roots = []
    if bridge_bdf:
        pr_roots.append(pci_sysfs / bridge_bdf / "firmware_node")
    pr_roots.append(dev / "firmware_node")

    for root in pr_roots:
        for pr_dir_name in ("power_resources_D3hot", "power_resources_D3cold", "power_resources_D0"):
            pr_dir = root / pr_dir_name
            if not pr_dir.is_dir():
                continue
            try:
                children = sorted(pr_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for child in children:
                p = _read_text(child / "path")
                if p and p not in seen:
                    seen.add(p)
                    power_resources.append(p)

    return DgpuTopology(
        bdf=chosen_bdf,
        vendor=chosen_vendor,
        audio_bdf=audio_bdf,
        bridge_bdf=bridge_bdf,
        acpi_path=acpi_path,
        bridge_acpi_path=bridge_acpi_path,
        power_resources=power_resources,
    )


def connected_dgpu_displays(
    bdf: str,
    pci_sysfs: Path = PCI_SYSFS,
    drm_sysfs: Path = DRM_SYSFS,
) -> List[str]:
    """Return names of connected displays wired to ``bdf`` (e.g. ['HDMI-A-1'])."""
    if not bdf or not drm_sysfs.is_dir():
        return []
    gpu_real = os.path.realpath(pci_sysfs / bdf)
    connected: List[str] = []
    try:
        entries = sorted(drm_sysfs.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for entry in entries:
        if "-" not in entry.name or not entry.name.startswith("card"):
            continue
        card_dev = entry / "device"
        if not card_dev.exists():
            continue
        card_dev_real = os.path.realpath(card_dev)
        pci_dev = card_dev / "device"
        pci_dev_real = os.path.realpath(pci_dev) if pci_dev.exists() else ""
        if not (
            card_dev_real == gpu_real
            or pci_dev_real == gpu_real
            or card_dev_real.startswith(gpu_real + os.sep)
        ):
            continue
        status = (_read_text(entry / "status") or "").lower()
        if status == "connected":
            connected.append(entry.name.split("-", 1)[1])
    return connected


def check_gate_off_guards(
    bdf: Optional[str] = None,
    pci_sysfs: Path = PCI_SYSFS,
    drm_sysfs: Path = DRM_SYSFS,
    holders: Optional[List[str]] = None,
    allow_active: bool = False,
) -> GuardResult:
    """Verify displays are detached and no user processes hold the dGPU before cutting power."""
    if bdf is None:
        topo = find_dgpu_topology(pci_sysfs=pci_sysfs)
        bdf = topo.bdf
        vendor = topo.vendor
    else:
        vraw = (_read_text(pci_sysfs / bdf / "vendor") or "").lower()
        vendor = "nvidia" if vraw == NVIDIA_VENDOR else ("amd" if vraw == AMD_VENDOR else None)

    if not bdf or not (pci_sysfs / bdf).is_dir():
        return GuardResult(
            allowed=False,
            reason="not_present",
            message="No discrete GPU detected on PCI bus.",
        )

    displays = connected_dgpu_displays(bdf, pci_sysfs=pci_sysfs, drm_sysfs=drm_sysfs)
    if displays:
        joined = ", ".join(displays)
        return GuardResult(
            allowed=False,
            reason="display_connected",
            displays=displays,
            message=(
                f"Cannot power off dGPU while display output is connected ({joined}). "
                "Disconnect it first."
            ),
        )

    rstatus = _read_text(pci_sysfs / bdf / "power" / "runtime_status")
    if holders is None:
        holders = _nvidia_wake_holders() if vendor == "nvidia" else []
    else:
        holders = list(holders)

    if (
        not holders
        and rstatus == "active"
        and is_gpu_wedged(bdf=bdf, vendor=vendor, rstatus=rstatus, pci_sysfs=pci_sysfs)
    ):
        return GuardResult(
            allowed=False,
            reason="wedged",
            runtime_status=rstatus,
            holders=holders,
            message=(
                "GPU is wedged (driver reset required) after a hung nvidia-powerd stop. "
                "Reboot the laptop to recover it; power gating is paused."
            ),
        )

    status_bad = (rstatus not in ("suspended", "active")) if allow_active else (rstatus != "suspended")
    if holders or status_bad:
        holder_str = f" (held by: {', '.join(holders)})" if holders else ""
        return GuardResult(
            allowed=False,
            reason="gpu_active",
            runtime_status=rstatus,
            holders=holders,
            message=(
                f"dGPU is currently {rstatus or 'active'}{holder_str}; "
                "cannot cut power until it suspends."
            ),
        )

    return GuardResult(
        allowed=True,
        reason=None,
        runtime_status=rstatus,
        holders=[],
        displays=[],
        message="Ready to power off dGPU.",
    )


def _detect_user_unit(pid: int, proc_root: Path = Path("/proc"), uid: Optional[int] = None) -> Optional[str]:
    """Return the systemd ``--user`` service unit owning ``pid``, if any (and safe to stop)."""
    uid = os.getuid() if uid is None else uid
    cg = _read_text(proc_root / str(pid) / "cgroup")
    if not cg:
        return None
    marker = f"user@{uid}.service/"
    for line in cg.splitlines():
        idx = line.find(marker)
        if idx < 0:
            continue
        tail = line[idx + len(marker):].strip("/")
        if not tail:
            continue
        leaf = tail.split("/")[-1]
        if not leaf.endswith(".service"):
            continue
        if any(leaf.lower().startswith(pref) for pref in _PROTECTED_UNIT_PREFIXES):
            return None
        return leaf
    return None


def _pid_holds_nvidia(pid: int, proc_root: Path = Path("/proc")) -> bool:
    fd_dir = proc_root / str(pid) / "fd"
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return False
    for fd in fds:
        try:
            target = os.readlink(fd_dir / fd)
        except OSError:
            continue
        if target.startswith("/dev/nvidia"):
            return True
    return False


def list_gpu_holder_processes(
    proc_root: Path = Path("/proc"),
    my_uid: Optional[int] = None,
) -> List[HolderProcess]:
    """List processes currently holding ``/dev/nvidia*`` open (read-only procfs walk).

    Never inspects ``/dev/dri/card*`` (which compositors like Xwayland keep open
    even while the dGPU is suspended) and excludes ``nvidia-powerd`` which the
    privileged gate helper manages automatically.
    """
    my_uid = os.getuid() if my_uid is None else my_uid
    my_pid = os.getpid()
    found: List[HolderProcess] = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return []

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid <= 1 or pid == my_pid:
            continue
        if not _pid_holds_nvidia(pid, proc_root=proc_root):
            continue
        pdir = proc_root / entry
        comm = _read_text(pdir / "comm") or entry
        if comm in _MANAGED_POWER_DAEMONS:
            continue
        try:
            st_uid = pdir.stat().st_uid
        except OSError:
            continue
        try:
            raw_cmd = (pdir / "cmdline").read_bytes()
            cmdline = " ".join(p.decode("utf-8", errors="replace") for p in raw_cmd.split(b"\x00") if p)
        except OSError:
            cmdline = comm
        user_unit = _detect_user_unit(pid, proc_root=proc_root, uid=my_uid) if st_uid == my_uid else None
        protected = (
            st_uid != my_uid
            or comm.lower() in _PROTECTED_COMM
        )
        found.append(
            HolderProcess(
                pid=pid,
                name=comm,
                uid=st_uid,
                cmdline=cmdline,
                user_unit=user_unit,
                protected=protected,
            )
        )

    found.sort(key=lambda h: (h.name.lower(), h.pid))
    return found


def closable_gpu_holders(
    proc_root: Path = Path("/proc"),
    my_uid: Optional[int] = None,
) -> List[HolderProcess]:
    """Return user-owned, non-protected processes holding ``/dev/nvidia*``."""
    return [h for h in list_gpu_holder_processes(proc_root=proc_root, my_uid=my_uid) if not h.protected]


def close_gpu_holders(
    holders: Optional[List[HolderProcess]] = None,
    term_timeout: float = 3.0,
    kill_timeout: float = 2.0,
) -> Dict:
    """Close user apps / user systemd services holding ``/dev/nvidia*``.

    - User systemd services (e.g. ``sunshine.service`` with ``Restart=always``)
      are stopped via ``systemctl --user stop <unit>`` so systemd does not
      immediately respawn them.
    - Regular user processes receive ``SIGTERM``, then ``SIGKILL`` after
      ``term_timeout`` if they are still alive and holding ``/dev/nvidia*``.
    - Protected / other-UID processes are never touched.
    """
    targets = closable_gpu_holders() if holders is None else [h for h in holders if not h.protected]
    if not targets:
        return {
            "ok": True,
            "stopped_units": [],
            "terminated": [],
            "killed": [],
            "remaining": [],
        }

    stopped_units: List[str] = []
    seen_units = set()
    unit_pids = set()
    for h in targets:
        if h.user_unit and h.user_unit not in seen_units:
            seen_units.add(h.user_unit)
            try:
                proc = subprocess.run(
                    ["systemctl", "--user", "stop", h.user_unit],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    check=False,
                )
                if proc.returncode == 0:
                    stopped_units.append(h.user_unit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to stop user unit %s: %s", h.user_unit, exc)
        if h.user_unit:
            unit_pids.add(h.pid)

    direct = [h for h in targets if h.pid not in unit_pids]
    terminated: List[str] = []
    for h in direct:
        try:
            os.kill(h.pid, signal.SIGTERM)
            terminated.append(h.label)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            logger.warning("Permission denied sending SIGTERM to %s: %s", h.label, exc)

    # Wait up to term_timeout for terminated processes to exit or release /dev/nvidia*.
    if direct and term_timeout > 0:
        deadline = time.monotonic() + term_timeout
        while time.monotonic() < deadline:
            still = [h for h in direct if _pid_holds_nvidia(h.pid)]
            if not still:
                break
            time.sleep(0.1)

    # Escalate stubborn direct processes to SIGKILL.
    killed: List[str] = []
    stubborn = [h for h in direct if _pid_holds_nvidia(h.pid)]
    for h in stubborn:
        try:
            os.kill(h.pid, signal.SIGKILL)
            killed.append(h.label)
        except (ProcessLookupError, PermissionError):
            pass

    if stubborn and kill_timeout > 0:
        deadline = time.monotonic() + kill_timeout
        while time.monotonic() < deadline:
            if not any(_pid_holds_nvidia(h.pid) for h in stubborn):
                break
            time.sleep(0.1)

    remaining = [h.label for h in list_gpu_holder_processes()]
    return {
        "ok": not remaining,
        "stopped_units": stopped_units,
        "terminated": terminated,
        "killed": killed,
        "remaining": remaining,
    }


def wait_for_dgpu_suspend(
    bdf: Optional[str] = None,
    timeout: float = 15.0,
    poll_interval: float = 0.25,
    pci_sysfs: Path = PCI_SYSFS,
) -> bool:
    """Poll sysfs ``runtime_status`` until the dGPU suspends (never touches NVML)."""
    if bdf is None:
        topo = find_dgpu_topology(pci_sysfs=pci_sysfs)
        bdf = topo.bdf
    if not bdf:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        rstatus = _read_text(pci_sysfs / bdf / "power" / "runtime_status")
        if rstatus == "suspended" or not (pci_sysfs / bdf).is_dir():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)


_AUDIO_DAEMONS = frozenset({"pipewire", "wireplumber", "pulseaudio"})


def audio_card_holders(
    audio_bdf: Optional[str],
    pci_sysfs: Path = PCI_SYSFS,
    proc_root: Path = Path("/proc"),
) -> List[str]:
    """Return process names holding ALSA nodes (e.g. controlC0) for ``audio_bdf``."""
    if not audio_bdf or not _BDF_RE.fullmatch(audio_bdf):
        return []
    sound_dir = pci_sysfs / audio_bdf / "sound"
    if not sound_dir.is_dir():
        return []
    indices: List[str] = []
    try:
        for entry in sound_dir.iterdir():
            m = re.fullmatch(r"card(\d+)", entry.name)
            if m:
                indices.append(m.group(1))
    except OSError:
        return []
    if not indices:
        return []
    pat = re.compile(rf"^/dev/snd/.*C(?:{'|'.join(indices)})(?:\D|$)")
    holders = set()
    try:
        pids = os.listdir(proc_root)
    except OSError:
        return []
    for pid in pids:
        if not pid.isdigit():
            continue
        fd_dir = proc_root / pid / "fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd_dir / fd)
            except OSError:
                continue
            if pat.match(target):
                comm = _read_text(proc_root / pid / "comm")
                holders.add(comm or pid)
                break
    return sorted(holders)


def _detect_active_audio_units(holders: List[str]) -> List[str]:
    """Return active systemd --user units corresponding to ALSA audio holders."""
    candidates: List[str] = []
    lower_holders = {h.lower() for h in holders}
    if lower_holders & {"pipewire", "wireplumber"}:
        # Stop pipewire-pulse.socket + service together with pipewire/wireplumber;
        # otherwise desktop Pulse clients immediately socket-activate
        # pipewire-pulse and pull pipewire.service back up within milliseconds.
        candidates.extend([
            "pipewire-pulse.socket",
            "pipewire.socket",
            "pipewire-pulse.service",
            "wireplumber.service",
            "pipewire.service",
        ])
    if "pulseaudio" in lower_holders:
        candidates.extend(["pulseaudio.socket", "pulseaudio.service"])

    active: List[str] = []
    for unit in candidates:
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0:
                active.append(unit)
        except Exception:  # noqa: BLE001
            continue
    return active


def _start_order_for_audio_units(stopped_units: List[str]) -> List[str]:
    """Order units so sockets start before pipewire/pulseaudio before wireplumber."""
    priority = {
        "pipewire.socket": 0,
        "pipewire-pulse.socket": 1,
        "pulseaudio.socket": 2,
        "pipewire.service": 3,
        "pulseaudio.service": 4,
        "wireplumber.service": 5,
        "pipewire-pulse.service": 6,
    }
    return sorted(stopped_units, key=lambda u: priority.get(u, 99))


@contextmanager
def _release_dgpu_audio_holders(
    audio_bdf: Optional[str],
    pci_sysfs: Path = PCI_SYSFS,
    force_if_audio_dev: bool = False,
) -> Iterator[None]:
    """Temporarily release PipeWire/WirePlumber from dGPU HDMI audio around PCI remove.

    Prevents a kernel uninterruptible ``D``-state deadlock inside ``snd_card_free()``
    while keeping user audio disruption under ~0.5 s.
    """
    holders = audio_card_holders(audio_bdf, pci_sysfs=pci_sysfs) if audio_bdf else []
    need_release = bool(holders and (set(h.lower() for h in holders) & _AUDIO_DAEMONS))
    if not need_release and force_if_audio_dev and audio_bdf and (pci_sysfs / audio_bdf).is_dir():
        holders = ["wireplumber", "pipewire"]
        need_release = True

    stopped_units: List[str] = []
    if need_release:
        active_units = _detect_active_audio_units(holders)
        if active_units:
            try:
                proc = subprocess.run(
                    ["systemctl", "--user", "stop", *active_units],
                    capture_output=True,
                    text=True,
                    timeout=8.0,
                    check=False,
                )
                if proc.returncode == 0:
                    stopped_units = active_units
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to pause user audio units (%s): %s", active_units, exc)

        if audio_bdf:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not audio_card_holders(audio_bdf, pci_sysfs=pci_sysfs):
                    break
                time.sleep(0.05)

    try:
        yield
    finally:
        if stopped_units:
            ordered = _start_order_for_audio_units(stopped_units)
            try:
                subprocess.run(
                    ["systemctl", "--user", "start", *ordered],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    check=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to restart user audio units (%s): %s", ordered, exc)


def force_close_and_gate_off(
    mode: Optional[str] = None,
    suspend_timeout: float = 15.0,
) -> Dict:
    """Explicit user-confirmed flow: close user apps holding dGPU and gate off."""
    mode_norm: Optional[str] = None
    if mode is not None:
        mode_norm = str(mode).strip().lower()
        if mode_norm not in VALID_MODES:
            raise ValueError(f"Unknown dGPU power mode {mode!r}; expected one of {VALID_MODES}")
        if mode_norm == MODE_ECO and not eco_allowed_on_power():
            return {
                "ok": False,
                "reason": "ac_online",
                "mode": configured_mode(),
                "error": "ECO mode is only available on battery power; use Optimus or Auto on AC.",
            }
        if mode_norm == MODE_AUTO:
            try:
                if bool(get_battery_manager().is_ac_online()):
                    return set_mode(MODE_AUTO)
            except Exception:  # noqa: BLE001
                pass

    with _user_transition_lock() as acquired:
        if not acquired:
            res = _busy_result()
            if mode_norm is not None:
                res["mode"] = mode_norm
            return res
        with _GATE_LOCK:
            topo = find_dgpu_topology()
            if topo.bdf:
                displays = connected_dgpu_displays(topo.bdf)
                if displays:
                    joined = ", ".join(displays)
                    return {
                        "ok": False,
                        "reason": "display_connected",
                        "displays": displays,
                        "error": (
                            f"Cannot power off dGPU while display output is connected ({joined}). "
                            "Disconnect it first."
                        ),
                    }

            close_res = close_gpu_holders()
            if not close_res.get("ok"):
                remaining = list(close_res.get("remaining") or [])
                holder_str = f" ({', '.join(remaining)})" if remaining else ""
                res = {
                    "ok": False,
                    "reason": "gpu_active",
                    "holders": remaining,
                    "closed": close_res,
                    "error": f"Could not close all processes using the dGPU{holder_str}.",
                }
                if mode_norm is not None:
                    res["mode"] = mode_norm
                return res

            resolved = get_gate_config()
            quirks = resolved[0].quirks if resolved is not None else []
            if "off_before_remove" in quirks:
                if topo.bdf:
                    wait_for_dgpu_suspend(bdf=topo.bdf, timeout=suspend_timeout)
                allow_active = False
            else:
                allow_active = True

            res = _gate_off_locked(allow_active=allow_active, mode_override=mode_norm)
            if res.get("ok") and mode_norm is not None:
                update_config(lambda c: {**c, "dgpu_power_mode": mode_norm})
                _publish_policy(mode=mode_norm, pending_off=False)
            res["closed"] = close_res
            if mode_norm is not None:
                res["mode"] = mode_norm
            return res


# ──────────────────────────────────────────────────────────────────────────────
# Runtime state files
# ──────────────────────────────────────────────────────────────────────────────


def gate_state_file_path() -> Path:
    override = os.environ.get("GIGAMATE_GATE_STATE_FILE")
    if override:
        return Path(override)
    return STATE_FILE


def read_gate_state() -> Dict:
    """Read the privileged helper's gate state file (/run/gigamate-dgpu-gate.json)."""
    try:
        data = json.loads(gate_state_file_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def policy_state_file_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / "gigamate-dgpu-gate-ui.json"
    return Path(f"/tmp/gigamate-dgpu-gate-ui-{os.getuid()}.json")


def read_policy_state() -> Dict:
    """Read the user-session policy/pending state file."""
    try:
        data = json.loads(policy_state_file_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_policy_state(state: Dict) -> None:
    path = policy_state_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def is_gated_off(
    pci_sysfs: Optional[Path] = None,
    *,
    topo: Optional[DgpuTopology] = None,
    gate_state: Optional[Dict] = None,
) -> bool:
    """Whether the dGPU is currently gated off (self-healing if PCI device returned)."""
    sysfs = PCI_SYSFS if pci_sysfs is None else pci_sysfs
    state = read_gate_state() if gate_state is None else gate_state
    if not state.get("gated"):
        return False
    if topo is not None:
        return topo.bdf is None
    bdf = state.get("bdf")
    if isinstance(bdf, str) and _BDF_RE.fullmatch(bdf):
        # Fast path: single stat() check on the known gated BDF.
        return not (sysfs / bdf).is_dir()
    # If the GPU is visible on PCI again (e.g. BIOS repowered after resume),
    # treat it as NOT gated off.
    if find_dgpu_topology(pci_sysfs=sysfs).bdf is not None:
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Boot settle, wedged detection & cross-process transition lock
# ──────────────────────────────────────────────────────────────────────────────


def boot_settle_remaining() -> float:
    """Seconds of the post-boot gate-off settle window still to run (0 = past)."""
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="utf-8", errors="ignore").split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0
    return max(0.0, _BOOT_SETTLE_SEC - uptime)


_CONFIRM_LOCK_NAME = "gigamate-dgpu-confirm.lock"
_HELPER_LOCK_FILE = Path("/run/gigamate-dgpu-gate.lock")


def _transition_lock_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / _TRANSITION_LOCK_NAME
    return Path(f"/tmp/gigamate-dgpu-transition-{os.getuid()}.lock")


def _confirm_lock_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / _CONFIRM_LOCK_NAME
    return Path(f"/tmp/gigamate-dgpu-confirm-{os.getuid()}.lock")


def _is_flock_held(path: Path, readonly: bool = False) -> bool:
    """Return True if another open file description holds an exclusive flock on ``path``."""
    try:
        if not path.exists():
            return False
        mode = "r" if readonly else "r+"
        op = (fcntl.LOCK_SH if readonly else fcntl.LOCK_EX) | fcntl.LOCK_NB
        with open(path, mode, encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, op)
            except BlockingIOError:
                return True
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            except OSError:
                pass
            return False
    except OSError:
        return False


def is_transition_in_progress() -> bool:
    """Whether any gate transition currently holds the in-process, user, or helper lock."""
    if _GATE_LOCK.locked():
        return True
    if _is_flock_held(_transition_lock_path(), readonly=False):
        return True
    return _is_flock_held(_HELPER_LOCK_FILE, readonly=True)


def is_confirm_dialog_open() -> bool:
    """Whether the standalone confirmation window currently holds its single-instance lock."""
    return _is_flock_held(_confirm_lock_path(), readonly=True)


@contextmanager
def _user_transition_lock(wait: float = _TRANSITION_LOCK_WAIT_SEC) -> Iterator[bool]:
    """Cross-process single-flight lock for dGPU power transitions.

    The tray, GigaMate Center, the background watcher, the CLI and the confirm
    dialog are separate processes that can each request a gate-off within
    seconds of each other (observed: three helper invocations in 18 s). This
    flock lets exactly one transition run at a time; contending callers wait up
    to ``wait`` seconds and otherwise get a "busy" result instead of piling on.
    """
    fh = None
    try:
        path = _transition_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+", encoding="utf-8")
    except OSError:
        fh = None
    if fh is None:
        # No lock file available: proceed guarded by the in-process lock only.
        yield True
        return

    acquired = False
    deadline = time.monotonic() + max(0.0, wait)
    while not acquired:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        except OSError:
            # flock unavailable: fall back to in-process serialization only.
            acquired = True
    if not acquired:
        try:
            fh.close()
        except OSError:
            pass
        yield False
        return
    try:
        yield True
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass


def _busy_result() -> Dict:
    return {
        "ok": False,
        "reason": "busy",
        "error": "Another dGPU power transition is already in progress; try again shortly.",
    }


_WEDGED_REASONS = frozenset({"powerd_stop_timeout", "wedged"})


def _systemctl_query(*args: str, timeout: float = 3.0) -> Optional[str]:
    """Run a read-only ``systemctl`` query and return its stdout, or None."""
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return None
    return (proc.stdout or "").strip() or None


def nvidia_powerd_stop_timed_out() -> bool:
    """Whether nvidia-powerd.service failed this boot with Result=timeout.

    That is the systemd signature of a stop whose SIGKILL could not remove the
    daemon (stuck in an uninterruptible driver call) — i.e. the GPU resume path
    hung and the GPU is wedged until reboot.
    """
    out = _systemctl_query(
        "show", "nvidia-powerd.service", "-p", "ActiveState", "-p", "Result", "--value",
    )
    if not out:
        return False
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return len(lines) >= 2 and lines[0] == "failed" and lines[1] == "timeout"


def is_gpu_wedged(
    bdf: Optional[str] = None,
    vendor: Optional[str] = None,
    rstatus: Optional[str] = None,
    pci_sysfs: Path = PCI_SYSFS,
) -> bool:
    """Heuristic for a driver-wedged dGPU that only a reboot can recover.

    Signature (observed on AERO X16 / RTX 5060 Max-Q): the GPU sits in D0,
    runtime PM cannot suspend it (kernel spams ``NVRM: Failed to get GCx
    pre-requisite``), no process holds ``/dev/nvidia*``, and nvidia-powerd died
    by hang. Pure sysfs/systemd reads; never touches NVML or the helper.
    """
    if bdf is None or vendor is None:
        try:
            topo = find_dgpu_topology(pci_sysfs=pci_sysfs)
        except Exception:  # noqa: BLE001
            return False
        bdf = bdf or topo.bdf
        vendor = vendor or topo.vendor
    if vendor != "nvidia" or not bdf or not (pci_sysfs / bdf).is_dir():
        return False
    if rstatus is None:
        rstatus = _read_text(pci_sysfs / bdf / "power" / "runtime_status")
    if rstatus != "active":
        return False
    if _nvidia_wake_holders():
        return False
    if read_policy_state().get("last_reason") in _WEDGED_REASONS:
        return True
    return nvidia_powerd_stop_timed_out()


# ──────────────────────────────────────────────────────────────────────────────
# Helper invocation & profile resolution
# ──────────────────────────────────────────────────────────────────────────────


def helper_path() -> Optional[str]:
    env = os.environ.get("GIGAMATE_DGPU_GATE_HELPER")
    if env and os.path.exists(env):
        return env
    if os.path.exists(HELPER_INSTALLED):
        return HELPER_INSTALLED
    dev = Path(__file__).resolve().parent.parent.parent / "data" / "gigamate-dgpu-gate"
    if dev.exists():
        return str(dev)
    return None


def _run_helper(args: List[str], timeout: float = _HELPER_TIMEOUT) -> Dict:
    path = helper_path()
    if path is None:
        return {"ok": False, "error": "dgpu-gate helper not installed"}

    if os.geteuid() == 0:
        cmd = [path, *args]
    else:
        if not os.path.exists("/usr/bin/pkexec"):
            return {"ok": False, "error": "pkexec not available"}
        cmd = ["pkexec", path, *args]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "helper/pkexec not found"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    try:
        stdout_txt, stderr_txt = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Unprivileged callers cannot SIGKILL a setuid pkexec root child; ignore EPERM.
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=0.5)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": "dgpu-gate helper timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    out = (stdout_txt or "").strip().splitlines()
    if not out:
        err = (stderr_txt or "").strip()
        return {"ok": False, "error": err or f"helper exit {proc.returncode}"}
    try:
        return json.loads(out[-1])
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid helper output"}


_CACHED_GATE_CONFIG: Optional[Tuple[DgpuGateConfig, Optional[object]]] = None
_GATE_CACHE_POPULATED: bool = False
_GATE_PROBE_IN_PROGRESS: bool = False


def resolve_gate_config(force_refresh: bool = False) -> Optional[Tuple[DgpuGateConfig, Optional[object]]]:
    """Auto-discover and cache ``(DgpuGateConfig, None)`` via ``probe_dgpu_gate``."""
    global _CACHED_GATE_CONFIG, _GATE_CACHE_POPULATED, _GATE_PROBE_IN_PROGRESS
    if _GATE_CACHE_POPULATED and not force_refresh:
        return _CACHED_GATE_CONFIG
    if _GATE_PROBE_IN_PROGRESS:
        return None

    _GATE_PROBE_IN_PROGRESS = True
    try:
        probe = probe_dgpu_gate(live_test=False)
        candidates = probe.get("candidates") or []
        if not candidates:
            _CACHED_GATE_CONFIG = None
            _GATE_CACHE_POPULATED = True
            return None

        # Prefer PG00._OFF, then any _OFF, then _PS3, then first candidate.
        def _rank(cand: Dict) -> Tuple[int, int]:
            path = str(cand.get("path", "")).upper()
            method = str(cand.get("method", "")).upper()
            is_pg00 = 0 if "PG00" in path else 1
            if method == "_OFF":
                m_rank = 0
            elif method == "_PS3":
                m_rank = 1
            else:
                m_rank = 2
            return (is_pg00, m_rank)

        best = min(candidates, key=_rank)
        cfg = DgpuGateConfig(
            path=best["path"],
            method=best.get("method", "_OFF"),
            on_method=best.get("on_method", "_ON"),
            sta_method=best.get("sta_method", "_STA"),
            quirks=list(best.get("quirks") or []),
            source=str(best.get("source", "auto")),
        )
        _CACHED_GATE_CONFIG = (cfg, None)
        _GATE_CACHE_POPULATED = True
        return _CACHED_GATE_CONFIG
    except Exception:  # noqa: BLE001
        return None
    finally:
        _GATE_PROBE_IN_PROGRESS = False


def get_gate_config() -> Optional[Tuple[DgpuGateConfig, Optional[object]]]:
    """Return ``(DgpuGateConfig, None)`` for this laptop, or None."""
    return resolve_gate_config()


def get_availability(
    pci_sysfs: Path = PCI_SYSFS,
    acpi_sysfs: Path = GIGAMATE_ACPI_SYSFS,
    *,
    topo: Optional[DgpuTopology] = None,
    gated: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """Return ``(supported, unsupported_reason)`` for dGPU power gating."""
    try:
        is_off = gated
        if is_off is None and topo is None:
            is_off = is_gated_off(pci_sysfs=pci_sysfs)
        if not is_off:
            if topo is None:
                topo = find_dgpu_topology(pci_sysfs=pci_sysfs, acpi_sysfs=acpi_sysfs)
            if topo.bdf is None:
                if is_off is None:
                    is_off = is_gated_off(pci_sysfs=pci_sysfs, topo=topo)
                if not is_off:
                    return False, "no_dgpu"
        if get_gate_config() is None:
            return False, "no_profile"
        if acpi_eval_backend(sysfs_dir=acpi_sysfs) == "none":
            return False, "no_acpi_backend"
        if os.geteuid() != 0 and (
            helper_path() is None or not os.path.exists("/usr/bin/pkexec")
        ):
            return False, "helper_missing"
        return True, None
    except Exception:  # noqa: BLE001
        return False, "no_profile"


def availability_message(reason: Optional[str]) -> str:
    """Human-readable explanation for an unsupported ``get_availability`` code."""
    messages = {
        "no_dgpu": "No discrete GPU detected on this system.",
        "no_profile": (
            "Your laptop model profile does not define an ACPI dGPU power gate yet. "
            "Run 'gigamate gpu power probe' or 'gigamate calibrate acpi' to discover candidate paths."
        ),
        "no_acpi_backend": (
            "No ACPI method evaluation backend found (install/load gigamate_acpi or acpi_call)."
        ),
        "helper_missing": (
            "Privileged helper (/usr/lib/gigamate/gigamate-dgpu-gate) or pkexec is missing; "
            "re-run ./install.sh to install it."
        ),
    }
    return messages.get(reason or "", "dGPU power gating is unavailable on this hardware.")


def is_supported(
    pci_sysfs: Path = PCI_SYSFS,
    acpi_sysfs: Path = GIGAMATE_ACPI_SYSFS,
    *,
    topo: Optional[DgpuTopology] = None,
    gated: Optional[bool] = None,
) -> bool:
    """Whether dGPU power gating is supported and actionable on this system."""
    return get_availability(
        pci_sysfs=pci_sysfs,
        acpi_sysfs=acpi_sysfs,
        topo=topo,
        gated=gated,
    )[0]


def configured_mode() -> str:
    try:
        mode = str(load_config().get("dgpu_power_mode", MODE_OPTIMUS)).lower()
    except Exception:  # noqa: BLE001
        return MODE_OPTIMUS
    return mode if mode in VALID_MODES else MODE_OPTIMUS


# ──────────────────────────────────────────────────────────────────────────────
# Discovery / Probe
# ──────────────────────────────────────────────────────────────────────────────


def probe_dgpu_gate(live_test: bool = False) -> Dict:
    """Discover candidate dGPU ACPI power-gate methods (read-only by default).

    When ``live_test`` is True (explicit opt-in), verifies an off -> on -> rescan
    cycle after confirming all gate-off safety guards pass.
    """
    topo = find_dgpu_topology()
    eval_be = acpi_eval_backend()
    resolved = None if _GATE_PROBE_IN_PROGRESS else get_gate_config()

    candidates: List[Dict] = []
    seen_keys = set()

    def _add_candidate(path: str, method: str, sta_method: str, on_method: str,
                       quirks: List[str], source: str) -> None:
        norm = normalize_acpi_path(path)
        key = (norm, method)
        if key in seen_keys:
            return
        seen_keys.add(key)
        candidates.append({
            "path": norm,
            "method": method,
            "on_method": on_method,
            "sta_method": sta_method,
            "quirks": list(quirks),
            "source": source,
        })

    if resolved is not None:
        cfg_gate, match = resolved
        _add_candidate(
            cfg_gate.path,
            cfg_gate.method,
            cfg_gate.effective_sta_method,
            cfg_gate.effective_on_method,
            cfg_gate.quirks,
            f"profile:{match.source}",
        )

    for pr in topo.power_resources:
        _add_candidate(pr, "_OFF", "_STA", "_ON", ["remove_before_off"], "sysfs:power_resource")

    if topo.acpi_path:
        _add_candidate(topo.acpi_path, "_PS3", "_PSC", "_PS0", ["remove_before_off"], "sysfs:firmware_node")

    probe_results: List[Dict] = []
    sta_by_path: Dict[str, Dict] = {}
    if candidates and eval_be != "none" and (os.geteuid() == 0 or helper_path() is not None):
        unique_paths: List[str] = []
        for c in candidates:
            if c["path"] not in unique_paths:
                unique_paths.append(c["path"])
        helper_args = ["probe"]
        for p in unique_paths:
            helper_args.extend(["--path", p])
        res = _run_helper(helper_args)
        if res.get("ok"):
            probe_results = list(res.get("candidates") or [])
            for item in probe_results:
                sta_by_path[item.get("path", "")] = item

    recommended: Optional[Dict] = None
    best_score = -1
    for cand in candidates:
        p = cand["path"]
        p_info = sta_by_path.get(p, {})
        sta_info = p_info.get(cand["sta_method"], {})
        score = 0
        if cand["method"] == "_OFF":
            score += 2
        if sta_info.get("ok"):
            score += 4
        if cand["source"].startswith("profile:"):
            score += 1
        cand["sta_ok"] = bool(sta_info.get("ok"))
        cand["sta_result"] = sta_info.get("result")
        if score > best_score and (cand["sta_ok"] or not sta_by_path):
            best_score = score
            recommended = {
                "path": cand["path"],
                "method": cand["method"],
                "quirks": list(cand["quirks"]),
                "confidence": "experimental",
            }

    live_result: Optional[Dict] = None
    if live_test:
        if recommended is None:
            live_result = {"ok": False, "error": "No candidate ACPI power-gate path found."}
        else:
            guard = check_gate_off_guards(bdf=topo.bdf)
            if not guard.allowed:
                live_result = {
                    "ok": False,
                    "reason": guard.reason,
                    "error": guard.message,
                }
            else:
                with _user_transition_lock(wait=0.0) as acquired:
                    if not acquired:
                        live_result = _busy_result()
                    else:
                        guard = check_gate_off_guards(bdf=topo.bdf)
                        if not guard.allowed:
                            live_result = {
                                "ok": False,
                                "reason": guard.reason,
                                "error": guard.message,
                            }
                        else:
                            off_res = _gate_off_with_spec(
                                topo=topo,
                                path=recommended["path"],
                                method=recommended["method"],
                                quirks=recommended["quirks"],
                                sta_method="_PSC" if recommended["method"] == "_PS3" else "_STA",
                                on_method="_PS0" if recommended["method"] == "_PS3" else "_ON",
                            )
                            if not off_res.get("ok"):
                                live_result = off_res
                            else:
                                time.sleep(0.5)
                                with _GATE_LOCK:
                                    on_res = _gate_on_locked()
                                live_result = {
                                    "ok": bool(on_res.get("ok")),
                                    "off": off_res,
                                    "on": on_res,
                                }
                                if on_res.get("ok"):
                                    recommended["confidence"] = "verified"

    return {
        "supported": bool(candidates and eval_be != "none"),
        "eval_backend": eval_be,
        "topology": {
            "bdf": topo.bdf,
            "vendor": topo.vendor,
            "audio_bdf": topo.audio_bdf,
            "bridge_bdf": topo.bridge_bdf,
            "acpi_path": topo.acpi_path,
            "bridge_acpi_path": topo.bridge_acpi_path,
            "power_resources": list(topo.power_resources),
        },
        "profile_match": (
            {
                "name": getattr(resolved[1], "clean_name", "Auto-discovered ACPI") if resolved[1] is not None else "Auto-discovered ACPI",
                "source": getattr(resolved[1], "source", resolved[0].source) if resolved[1] is not None else resolved[0].source,
                "gate": resolved[0].to_dict(),
            }
            if resolved is not None
            else None
        ),
        "candidates": candidates,
        "probe_results": probe_results,
        "recommended": recommended,
        "live_test": live_result,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Gate OFF / ON sequences
# ──────────────────────────────────────────────────────────────────────────────


def _publish_policy(
    *,
    mode: str,
    pending_off: bool,
    last_reason: Optional[str] = None,
    last_error: Optional[str] = None,
    holders: Optional[List[str]] = None,
    displays: Optional[List[str]] = None,
) -> None:
    _write_policy_state({
        "mode": mode,
        "pending_off": bool(pending_off),
        "last_reason": last_reason,
        "last_error": last_error,
        "holders": list(holders or []),
        "displays": list(displays or []),
        "ts": time.time(),
    })


def _gate_off_with_spec(
    *,
    topo: DgpuTopology,
    path: str,
    method: str,
    quirks: List[str],
    sta_method: str,
    on_method: str,
    allow_active: bool = False,
) -> Dict:
    if not topo.bdf:
        return {"ok": False, "reason": "not_present", "error": "No discrete GPU detected on PCI bus."}

    guard = check_gate_off_guards(bdf=topo.bdf, allow_active=allow_active)
    if not guard.allowed:
        out = {
            "ok": False,
            "reason": guard.reason,
            "displays": guard.displays,
            "holders": guard.holders,
            "runtime_status": guard.runtime_status,
            "error": guard.message,
        }
        if guard.reason == "wedged":
            out["reboot_recommended"] = True
        return out

    # Stage 1: wake GPU + stop nvidia-powerd while PipeWire/WirePlumber are still
    # undisturbed so a cold ~30.6s PG00._ON wake never mutes system audio.
    prepared = False
    stopped_powerd = False
    if topo.vendor == "nvidia":
        prep_args = ["prepare-off", "--bdf", topo.bdf]
        if allow_active:
            prep_args.append("--allow-active")
        prep_res = _run_helper(prep_args, timeout=90.0)
        if prep_res.get("ok"):
            prepared = True
            stopped_powerd = bool(prep_res.get("stopped_nvidia_powerd"))
        else:
            prep_reason = prep_res.get("reason")
            prep_err = str(prep_res.get("error") or "").lower()
            if (
                prep_reason in (
                    "gpu_active",
                    "display_connected",
                    "wedged",
                    "powerd_stop_timeout",
                    "powerd_transition",
                    "not_present",
                    "busy",
                )
                or "timed out" in prep_err
            ):
                return prep_res

    def _build_gate_off_args(*, eff_allow_active: bool, eff_stopped_powerd: bool) -> List[str]:
        cmd = [
            "gate-off",
            "--bdf", topo.bdf,
            "--path", normalize_acpi_path(path),
            "--method", method,
            "--sta-method", sta_method,
            "--on-method", on_method,
        ]
        if topo.audio_bdf:
            cmd.extend(["--audio-bdf", topo.audio_bdf])
        if topo.bridge_bdf:
            cmd.extend(["--bridge-bdf", topo.bridge_bdf])
        for quirk in quirks:
            cmd.extend(["--quirk", quirk])
        if eff_allow_active:
            cmd.append("--allow-active")
        if eff_stopped_powerd:
            cmd.append("--stopped-nvidia-powerd")
        return cmd

    # Stage 2: pause PipeWire/WirePlumber/PipeWire-Pulse only around the brief PCI
    # device remove + ACPI power-off window (~0.5 s).
    args = _build_gate_off_args(
        eff_allow_active=bool(allow_active or prepared),
        eff_stopped_powerd=stopped_powerd,
    )
    with _release_dgpu_audio_holders(topo.audio_bdf):
        res = _run_helper(args, timeout=90.0)

    # If the NVIDIA driver re-registered/opened the HDA audio function mid-transition
    # and _cmd_gate_off aborted (restarting nvidia-powerd), retry once with forced
    # audio-unit release without claiming nvidia-powerd is still stopped.
    if not res.get("ok") and res.get("reason") == "audio_busy" and topo.audio_bdf:
        retry_args = _build_gate_off_args(
            eff_allow_active=allow_active,
            eff_stopped_powerd=False,
        )
        with _release_dgpu_audio_holders(topo.audio_bdf, force_if_audio_dev=True):
            res = _run_helper(retry_args, timeout=90.0)
    return res


def gate_off(allow_active: bool = False) -> Dict:
    """Power-gate the discrete GPU off after verifying safety guards."""
    with _user_transition_lock() as acquired:
        if not acquired:
            return _busy_result()
        with _GATE_LOCK:
            return _gate_off_locked(allow_active=allow_active)


def _gate_off_locked(
    allow_active: bool = False,
    mode_override: Optional[str] = None,
) -> Dict:
    cfg_mode = configured_mode()
    mode = mode_override if mode_override is not None else cfg_mode
    if is_gated_off():
        _publish_policy(mode=mode, pending_off=False)
        return {"ok": True, "already_gated": True, "gated": True}

    resolved = get_gate_config()
    if resolved is None:
        return {
            "ok": False,
            "reason": "unsupported",
            "error": "No dGPU power-gate profile found for this laptop model. Run 'gigamate calibrate acpi' or 'gigamate gpu power probe'.",
        }
    gate_cfg, _match = resolved

    if acpi_eval_backend() == "none":
        return {
            "ok": False,
            "reason": "no_backend",
            "error": "No ACPI evaluation backend available (gigamate_acpi or acpi_call required).",
        }

    settle = boot_settle_remaining()
    if settle > 0.0:
        err = (
            f"Deferring dGPU power-off for ~{int(settle) + 1}s — boot settle window "
            "still active. GigaMate gates off automatically once the window passes."
        )
        res = {
            "ok": False,
            "reason": "boot_settle",
            "settle_remaining_sec": int(settle) + 1,
            "error": err,
        }
        _publish_policy(
            mode=cfg_mode,
            pending_off=(cfg_mode in (MODE_ECO, MODE_AUTO)),
            last_reason="boot_settle",
            last_error=err,
        )
        return res

    topo = find_dgpu_topology()
    res = _gate_off_with_spec(
        topo=topo,
        path=gate_cfg.path,
        method=gate_cfg.method,
        quirks=gate_cfg.quirks,
        sta_method=gate_cfg.effective_sta_method,
        on_method=gate_cfg.effective_on_method,
        allow_active=allow_active,
    )
    if res.get("ok"):
        _step_down_high_power_profile()
        _publish_policy(mode=mode, pending_off=False)
    else:
        reason = res.get("reason")
        # Only retryable outcomes keep a pending_off latch against the saved
        # config mode; unpersisted confirmation failures stay non-pending.
        retryable = reason in ("gpu_active", "display_connected", "audio_busy", "boot_settle")
        _publish_policy(
            mode=cfg_mode,
            pending_off=bool(cfg_mode in (MODE_ECO, MODE_AUTO)) and retryable,
            last_reason=reason,
            last_error=res.get("error"),
            holders=res.get("holders"),
            displays=res.get("displays"),
        )
    return res


def gate_on() -> Dict:
    """Power the discrete GPU back on and rescan PCI to rebind its driver."""
    with _user_transition_lock() as acquired:
        if not acquired:
            return _busy_result()
        with _GATE_LOCK:
            return _gate_on_locked()


def _gate_on_locked() -> Dict:
    mode = configured_mode()
    state = read_gate_state()

    if not is_gated_off():
        topo = find_dgpu_topology()
        if topo.bdf is not None:
            switched_mode = None
            if mode == MODE_ECO:
                update_config(lambda c: {**c, "dgpu_power_mode": MODE_OPTIMUS})
                mode = MODE_OPTIMUS
                switched_mode = MODE_OPTIMUS
            _publish_policy(mode=mode, pending_off=False)
            out: Dict = {
                "ok": True,
                "already_on": True,
                "gated": False,
                "bdf": topo.bdf,
            }
            if switched_mode:
                out["switched_mode"] = switched_mode
            return out

    path = state.get("acpi_path")
    on_method = state.get("on_method")
    sta_method = state.get("sta_method")
    bridge_bdf = state.get("bridge_bdf")
    restart_powerd = bool(state.get("stopped_nvidia_powerd"))

    if not path:
        resolved = get_gate_config()
        if resolved is not None:
            gate_cfg, _match = resolved
            path = gate_cfg.path
            on_method = on_method or gate_cfg.effective_on_method
            sta_method = sta_method or gate_cfg.effective_sta_method

    if not path:
        return {
            "ok": False,
            "reason": "unsupported",
            "error": "No dGPU power-gate ACPI path known to restore.",
        }

    on_method = on_method or "_ON"
    sta_method = sta_method or ("_PSC" if on_method == "_PS0" else "_STA")

    args = [
        "gate-on",
        "--path", normalize_acpi_path(str(path)),
        "--method", str(on_method),
        "--sta-method", str(sta_method),
    ]
    if bridge_bdf:
        args.extend(["--bridge-bdf", str(bridge_bdf)])
    if restart_powerd:
        args.append("--restart-nvidia-powerd")

    res = _run_helper(args, timeout=45.0)
    if res.get("ok"):
        if mode == MODE_ECO:
            update_config(lambda c: {**c, "dgpu_power_mode": MODE_OPTIMUS})
            mode = MODE_OPTIMUS
            res["switched_mode"] = MODE_OPTIMUS
        _publish_policy(mode=mode, pending_off=False)
    else:
        _publish_policy(
            mode=mode,
            pending_off=False,
            last_reason="gate_on_failed",
            last_error=res.get("error"),
        )
    return res


# ──────────────────────────────────────────────────────────────────────────────
# Mode policy (Optimus / ECO / Auto) & AC / Resume sync
# ──────────────────────────────────────────────────────────────────────────────

_HIGH_POWER_PROFILE_IDS = frozenset({2, 3})


def eco_allowed_on_power() -> bool:
    """Whether ECO mode is permitted on the current power source (battery only)."""
    try:
        return not bool(get_battery_manager().is_ac_online())
    except Exception:  # noqa: BLE001
        return True


def high_power_profiles_allowed() -> bool:
    """Whether Performance/Gaming profiles are allowed (blocked when dGPU is gated/ECO)."""
    if is_gated_off():
        return False
    return configured_mode() != MODE_ECO


def _step_down_high_power_profile() -> None:
    """Step down Performance/Gaming to Balanced when the dGPU is gated off."""
    try:
        cfg = load_config()
        prof = cfg.get("acpi_profile")
        if prof not in _HIGH_POWER_PROFILE_IDS:
            return
        update_config(lambda c: {**c, "acpi_profile": 1})
        try:
            from .acpi import AcpiController, FanProfile

            ctrl = AcpiController()
            if ctrl.available:
                ctrl.set_profile(FanProfile.BALANCED)
        except Exception:  # noqa: BLE001
            pass
        if cfg.get("sync_system_power", False):
            try:
                from .system_power import sync_system_power

                sync_system_power(1)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def set_mode(mode: str, apply_now: bool = True) -> Dict:
    """Persist ``dgpu_power_mode`` ('optimus' | 'eco' | 'auto') and reconcile."""
    mode = str(mode or "").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown dGPU power mode {mode!r}; expected one of {VALID_MODES}")

    if mode == MODE_ECO and not eco_allowed_on_power():
        return {
            "ok": False,
            "reason": "ac_online",
            "mode": configured_mode(),
            "error": "ECO mode is only available on battery power; use Optimus or Auto on AC.",
        }

    update_config(lambda c: {**c, "dgpu_power_mode": mode})

    if not apply_now:
        return {"ok": True, "mode": mode}

    with _user_transition_lock() as acquired:
        if not acquired:
            return _busy_result()
        with _GATE_LOCK:
            if mode == MODE_OPTIMUS:
                if is_gated_off():
                    res = _gate_on_locked()
                    res["mode"] = mode
                    return res
                _publish_policy(mode=mode, pending_off=False)
                return {"ok": True, "mode": mode, "gated": False}
            return _sync_policy_locked(reason="set_mode", mode=mode)


def sync_policy(reason: str = "sync") -> Dict:
    """Enforce the configured ``dgpu_power_mode`` policy ('eco' / 'auto').

    In ``optimus`` mode this is a safe no-op so manual ``gigamate gpu power off``
    or kernel runtime PM are left alone.
    """
    with _user_transition_lock() as acquired:
        if not acquired:
            return _busy_result()
        with _GATE_LOCK:
            return _sync_policy_locked(reason=reason, mode=None)


def _sync_policy_locked(reason: str = "sync", mode: Optional[str] = None) -> Dict:
    if mode is None:
        mode = configured_mode()

    if mode == MODE_OPTIMUS:
        _publish_policy(mode=mode, pending_off=False)
        return {"ok": True, "mode": mode, "action": "noop", "gated": is_gated_off()}

    if not is_supported():
        return {"ok": True, "mode": mode, "action": "unsupported", "gated": False}

    if mode == MODE_ECO:
        if not eco_allowed_on_power():
            update_config(lambda c: {**c, "dgpu_power_mode": MODE_OPTIMUS})
            if is_gated_off():
                res = _gate_on_locked()
                res["mode"] = MODE_OPTIMUS
                res["action"] = "gate_on"
                res["auto_switched_from"] = MODE_ECO
                return res
            _publish_policy(mode=MODE_OPTIMUS, pending_off=False)
            return {"ok": True, "mode": MODE_OPTIMUS, "action": "auto_optimus_ac", "gated": False}
        want_off = True
    else:  # MODE_AUTO
        try:
            ac_online = bool(get_battery_manager().is_ac_online())
        except Exception:  # noqa: BLE001
            ac_online = True
        want_off = not ac_online

    currently_off = is_gated_off()

    if want_off and not currently_off:
        res = _gate_off_locked()
        res["mode"] = mode
        res["action"] = "gate_off"
        return res

    if not want_off and currently_off:
        res = _gate_on_locked()
        res["mode"] = mode
        res["action"] = "gate_on"
        return res

    _publish_policy(mode=mode, pending_off=False)
    return {"ok": True, "mode": mode, "action": "steady", "gated": currently_off}


def _ac_event_mtime() -> Optional[float]:
    try:
        return AC_EVENT_FILE.stat().st_mtime
    except OSError:
        return None


def _spawn_confirm_dialog(
    trigger: str,
    target_mode: str = MODE_ECO,
    prev_mode: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """Spawn the active confirmation dialog in the graphical session."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    cmd = [
        sys.executable,
        "-m",
        "gigamate.ui.dgpu_gate_dialog",
        "--trigger",
        str(trigger),
        "--target-mode",
        str(target_mode),
    ]
    if prev_mode:
        cmd.extend(["--prev-mode", str(prev_mode)])
    try:
        return subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to launch dGPU gate confirmation dialog: %s", exc)
        return None


def launch_confirm_dialog(
    trigger: str,
    target_mode: str = MODE_ECO,
    prev_mode: Optional[str] = None,
) -> bool:
    """Launch the standalone active confirmation window; returns True if spawned."""
    return _spawn_confirm_dialog(
        trigger=trigger,
        target_mode=target_mode,
        prev_mode=prev_mode,
    ) is not None


def should_pause_tuning(ac_online: Optional[bool] = None) -> bool:
    """Return True when NVML tuning should be paused to avoid waking/holding the dGPU."""
    if is_gated_off():
        return True
    if configured_mode() not in (MODE_ECO, MODE_AUTO):
        return False
    if ac_online is None:
        try:
            ac_online = bool(get_battery_manager().is_ac_online())
        except Exception:  # noqa: BLE001
            ac_online = True
    if ac_online:
        return False
    return bool(is_supported(gated=False))


def watcher_tick(now: Optional[float] = None) -> None:
    """Periodic lightweight policy check run by ``gigamate-dgpu-watch``.

    Never wakes a sleeping GPU and never spawns pkexec while the GPU is active.
    In Auto mode with a graphical session, unplugging AC triggers an active
    confirmation dialog instead of silently closing user apps or bypassing consent.
    """
    now = time.monotonic() if now is None else now
    mode = configured_mode()

    try:
        ac_online = bool(get_battery_manager().is_ac_online())
    except Exception:  # noqa: BLE001
        ac_online = True
    ev_mtime = _ac_event_mtime()

    with _WATCHER_LOCK:
        prev_ac = _watcher_state["last_ac"]
        mode_changed = _watcher_state["last_mode"] != mode
        ac_changed = prev_ac is not None and prev_ac != ac_online
        ev_changed = (
            ev_mtime is not None and ev_mtime != _watcher_state["last_event_mtime"]
        )
        _watcher_state["last_mode"] = mode
        _watcher_state["last_ac"] = ac_online
        _watcher_state["last_event_mtime"] = ev_mtime
        last_attempt_at = float(_watcher_state["last_attempt_at"] or 0.0)
        last_attempt_rstatus = _watcher_state["last_attempt_rstatus"]
        last_policy_publish = float(_watcher_state["last_policy_publish"] or 0.0)
        awaiting_confirm_at = float(_watcher_state.get("awaiting_confirm_at") or 0.0)
        supported_cache = _watcher_state.get("supported_cache")
        supported_checked_at = float(_watcher_state.get("supported_checked_at") or 0.0)
        if ac_changed or mode_changed:
            _watcher_state["boot_prompted"] = False

    if mode == MODE_OPTIMUS:
        with _WATCHER_LOCK:
            if awaiting_confirm_at:
                _watcher_state["awaiting_confirm_at"] = 0.0
            _watcher_state["want_off_since"] = 0.0
            _watcher_state["boot_prompted"] = False
        if mode_changed:
            with _GATE_LOCK:
                _publish_policy(mode=mode, pending_off=False)
        return

    currently_off = is_gated_off()
    if (
        supported_cache is None
        or mode_changed
        or ac_changed
        or ev_changed
        or now < supported_checked_at
        or (now - supported_checked_at >= _SUPPORT_CACHE_TTL_SEC)
    ):
        supported_cache = is_supported(gated=currently_off)
        with _WATCHER_LOCK:
            _watcher_state["supported_cache"] = supported_cache
            _watcher_state["supported_checked_at"] = now
    if not supported_cache:
        return

    want_off = (mode == MODE_ECO and not ac_online) or (mode == MODE_AUTO and not ac_online)

    if not want_off:
        with _WATCHER_LOCK:
            if awaiting_confirm_at:
                _watcher_state["awaiting_confirm_at"] = 0.0
            _watcher_state["want_off_since"] = 0.0
            _watcher_state["boot_prompted"] = False
        if (currently_off or mode == MODE_ECO) and (ac_changed or ev_changed or mode_changed or (now - last_attempt_at >= _RETRY_BACKOFF_SEC)):
            with _WATCHER_LOCK:
                _watcher_state["last_attempt_at"] = now
            sync_policy(reason="watcher_restore")
        elif mode_changed or ac_changed or ev_changed:
            with _GATE_LOCK:
                _publish_policy(mode=mode, pending_off=False)
        return

    # want_off == True
    if currently_off:
        with _WATCHER_LOCK:
            if awaiting_confirm_at:
                _watcher_state["awaiting_confirm_at"] = 0.0
            _watcher_state["want_off_since"] = 0.0
            _watcher_state["boot_prompted"] = False
        if mode_changed or ac_changed or ev_changed:
            with _GATE_LOCK:
                _publish_policy(mode=mode, pending_off=False)
        return

    with _WATCHER_LOCK:
        want_off_since = float(_watcher_state.get("want_off_since") or 0.0)
        if want_off_since == 0.0:
            want_off_since = now
            _watcher_state["want_off_since"] = now
        boot_prompted = bool(_watcher_state.get("boot_prompted"))

    if is_confirm_dialog_open() or is_transition_in_progress():
        return

    has_display_session = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    ac_disconnected = (
        (ac_changed and not ac_online)
        or (ev_changed and not ac_online and (prev_ac is True or not awaiting_confirm_at))
    )
    if mode == MODE_AUTO and has_display_session:
        if ac_disconnected and (not awaiting_confirm_at or (now - awaiting_confirm_at >= 2.0)):
            with _WATCHER_LOCK:
                _watcher_state["awaiting_confirm_at"] = now
                _watcher_state["last_policy_publish"] = now
            launch_confirm_dialog(
                trigger="auto_ac_unplug",
                target_mode=MODE_AUTO,
                prev_mode=MODE_AUTO,
            )
            _publish_policy(
                mode=mode,
                pending_off=True,
                last_reason="awaiting_confirmation",
                last_error="Waiting for confirmation to power off dGPU on battery.",
            )
            return
        if awaiting_confirm_at:
            return

    # Need to gate off: check sysfs runtime_status BEFORE calling helper so we
    # never spawn pkexec on an active GPU tick.
    topo = find_dgpu_topology()
    if topo.bdf is None:
        return
    rstatus = _read_text(PCI_SYSFS / topo.bdf / "power" / "runtime_status")
    displays = connected_dgpu_displays(topo.bdf)

    # Never poke a wedged GPU (driver reset required): report it and stand down
    # until reboot. This also stops the retry loop from re-running pkexec
    # helper calls into the same hang.
    if is_gpu_wedged(bdf=topo.bdf, vendor=topo.vendor, rstatus=rstatus):
        if mode_changed or ac_changed or ev_changed or (now - last_policy_publish >= 10.0):
            with _WATCHER_LOCK:
                _watcher_state["last_policy_publish"] = now
                _watcher_state["last_attempt_rstatus"] = rstatus
            _publish_policy(
                mode=mode,
                pending_off=False,
                last_reason="wedged",
                last_error=(
                    "GPU is wedged (driver reset required): reboot the laptop to recover it. "
                    "dGPU power gating is paused until then."
                ),
            )
        return

    # Right after boot the nvidia-powerd <-> SBIOS Dynamic Boost handshake is
    # still settling; a rail cut in that window can wedge the GPU resume.
    # Defer the first automatic gate-off until the window passes.
    settle = boot_settle_remaining()
    if settle > 0.0:
        if mode_changed or ac_changed or ev_changed or (now - last_policy_publish >= 10.0):
            with _WATCHER_LOCK:
                _watcher_state["last_policy_publish"] = now
            _publish_policy(
                mode=mode,
                pending_off=True,
                last_reason="boot_settle",
                last_error=(
                    f"Waiting {int(settle) + 1}s after boot for the NVIDIA power handshake "
                    "to settle before gating off the dGPU."
                ),
            )
        return

    should_attempt = False
    if not displays and rstatus == "suspended":
        if (
            mode_changed
            or ac_changed
            or ev_changed
            or last_attempt_rstatus != "suspended"
            or (now - last_attempt_at >= _RETRY_BACKOFF_SEC)
        ):
            should_attempt = True

    if should_attempt:
        with _WATCHER_LOCK:
            _watcher_state["last_attempt_at"] = now
            _watcher_state["last_attempt_rstatus"] = rstatus
            _watcher_state["last_policy_publish"] = now
        sync_policy(reason="watcher_gate_off")
        return

    if rstatus == "active" and not displays and (now - want_off_since) >= 12.0 and not boot_prompted:
        holders = list_gpu_holder_processes(topo.bdf)
        with _WATCHER_LOCK:
            _watcher_state["boot_prompted"] = True
            _watcher_state["last_attempt_at"] = now
        if not holders:
            gate_off(allow_active=True)
            return
        if has_display_session and not awaiting_confirm_at:
            with _WATCHER_LOCK:
                _watcher_state["awaiting_confirm_at"] = now
                _watcher_state["last_policy_publish"] = now
            launch_confirm_dialog(
                trigger="boot_battery",
                target_mode=mode,
                prev_mode=mode,
            )
            _publish_policy(
                mode=mode,
                pending_off=True,
                last_reason="awaiting_confirmation",
                last_error="Waiting for confirmation to power off dGPU on battery.",
            )
            return

    # If the GPU is already suspended with no displays attached, we are only
    # waiting out the retry backoff after a helper failure — preserve the real
    # error published by sync_policy() instead of overwriting it with
    # "Waiting for dGPU to suspend".
    if not displays and rstatus == "suspended":
        return

    # Keep policy UI state updated about why we are still waiting (throttled).
    if mode_changed or ac_changed or ev_changed or (now - last_policy_publish >= 10.0):
        with _WATCHER_LOCK:
            _watcher_state["last_attempt_rstatus"] = rstatus
            _watcher_state["last_policy_publish"] = now
        holders = _nvidia_wake_holders() if topo.vendor == "nvidia" else []
        if displays:
            reason = "display_connected"
            err = (
                f"Cannot power off dGPU while display output is connected ({', '.join(displays)})."
            )
        else:
            reason = "gpu_active"
            holder_str = f" (held by: {', '.join(holders)})" if holders else ""
            err = f"Waiting for dGPU to suspend{holder_str} before cutting power."
        _publish_policy(
            mode=mode,
            pending_off=True,
            last_reason=reason,
            last_error=err,
            holders=holders,
            displays=displays,
        )


def schedule_resume_reconcile() -> None:
    """Re-apply ECO / Auto gating after suspend/resume without blocking the main loop."""
    global _RESUME_THREAD
    try:
        mode = configured_mode()
        if mode not in (MODE_ECO, MODE_AUTO) or not is_supported():
            return
    except Exception:  # noqa: BLE001
        return

    if _RESUME_THREAD is not None and _RESUME_THREAD.is_alive():
        return

    def _worker() -> None:
        # Try immediately, then wait briefly in case BIOS repowered the dGPU and
        # the NVIDIA driver needs a few seconds to settle back into RTD3 suspend.
        for delay in (0.5, 2.0, 3.0, 5.0, 5.0, 5.0):
            time.sleep(delay)
            res = sync_policy(reason="resume")
            if res.get("ok") or res.get("reason") not in ("gpu_active", "busy"):
                return

    t = threading.Thread(target=_worker, name="gigamate-dgpu-resume", daemon=True)
    _RESUME_THREAD = t
    t.start()


def get_status(check_helper: bool = False) -> Dict:
    """Return unified dGPU power-gate status for CLI, UI, tray, and reports."""
    mode = configured_mode()
    gate_state = read_gate_state()
    gated = is_gated_off(gate_state=gate_state)
    topo = (
        DgpuTopology(acpi_path=read_dgpu_acpi_path())
        if gated
        else find_dgpu_topology()
    )
    policy_state = read_policy_state()
    resolved = get_gate_config()
    eval_be = acpi_eval_backend()
    supported, unsupported_reason = get_availability(topo=topo, gated=gated)

    try:
        ac_online = bool(get_battery_manager().is_ac_online())
    except Exception:  # noqa: BLE001
        ac_online = True

    rstatus = (
        _read_text(PCI_SYSFS / topo.bdf / "power" / "runtime_status")
        if topo.bdf
        else ("gated_off" if gated else None)
    )
    pstate = (
        _read_text(PCI_SYSFS / topo.bdf / "power_state")
        if topo.bdf
        else ("OFF" if gated else None)
    )
    displays = connected_dgpu_displays(topo.bdf) if topo.bdf else []

    wedged = False
    if not gated and topo.bdf:
        try:
            wedged = is_gpu_wedged(bdf=topo.bdf, vendor=topo.vendor, rstatus=rstatus)
        except Exception:  # noqa: BLE001
            wedged = False

    acpi_sta = None
    if check_helper and resolved is not None and eval_be != "none":
        gate_cfg, _ = resolved
        st = _run_helper([
            "status",
            "--path", normalize_acpi_path(gate_cfg.path),
            "--sta-method", gate_cfg.effective_sta_method,
        ])
        if st.get("ok"):
            acpi_sta = st.get("result")

    return {
        "supported": supported,
        "unsupported_reason": unsupported_reason,
        "mode": mode,
        "gated": gated,
        "ac_online": ac_online,
        "eco_allowed": not ac_online,
        "high_power_profiles_allowed": (not gated) and (mode != MODE_ECO),
        "eval_backend": eval_be,
        "helper_installed": helper_path() is not None,
        "bdf": topo.bdf or gate_state.get("bdf"),
        "vendor": topo.vendor or gate_state.get("vendor"),
        "audio_bdf": topo.audio_bdf or gate_state.get("audio_bdf"),
        "bridge_bdf": topo.bridge_bdf or gate_state.get("bridge_bdf"),
        "acpi_path": topo.acpi_path or read_dgpu_acpi_path(),
        "runtime_status": rstatus,
        "power_state": pstate,
        "displays": displays,
        "wedged": wedged,
        "boot_settle_remaining_sec": int(round(boot_settle_remaining())),
        "pending_off": bool(policy_state.get("pending_off")) and not gated and mode in (MODE_ECO, MODE_AUTO),
        "last_reason": policy_state.get("last_reason"),
        "last_error": policy_state.get("last_error"),
        "holders": policy_state.get("holders") or [],
        "gate_config": resolved[0].to_dict() if resolved is not None else None,
        "profile_source": (resolved[1].source if resolved[1] is not None else resolved[0].source) if resolved is not None else None,
        "profile_name": (resolved[1].clean_name if resolved[1] is not None else "Auto-discovered ACPI") if resolved is not None else None,
        "experimental": False,
        "acpi_sta": acpi_sta,
    }
