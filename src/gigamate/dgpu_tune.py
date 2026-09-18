"""GigaMate — sleep-aware NVIDIA dGPU tuning (V/F offset + max-clock cap).

Two independent controls, both applied only while the dGPU is awake:

* **Undervolt** — ``nvmlDeviceSetGpcClkVfOffset`` shifts the V/F curve so a
  given clock runs at lower voltage (a "pseudo-undervolt").
* **Max clock** — ``nvmlDeviceSetGpuLockedClocks(0, N)`` caps the boost clock
  ("flatten above N MHz"), which improves stability when undervolting.

Both NVML calls require root, so writes are performed by a small helper
installed at ``/usr/lib/gigamate/gigamate-dgpu-nvml`` and invoked on demand via
``pkexec`` (auto-granted for wheel/sudo by a polkit rule).

Key invariant: this module never links NVML itself and never holds a GPU handle
open — doing so would keep the dGPU awake. It reads ``power/runtime_status``
from sysfs (which does not wake the GPU) and only shells out to the helper when
a change is actually needed. The tuning is applied only while the GPU is awake;
it is cleared when it suspends (and when it is awake but idle), and re-applied
whenever the desired values change or the GPU becomes busy again.

Configuration keys (see ``config.py``):
    dgpu_undervolt_enabled    bool   undervolt on/off
    dgpu_undervolt_offset_mhz int    0..255 (0 = stock)
    dgpu_undervolt_auto       bool   auto-apply on boot/wake (else manual)
    dgpu_max_clock_enabled    bool   max-clock cap on/off
    dgpu_max_clock_mhz        int    0 = off, else MHz cap
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import DEFAULT_CONFIG, load as load_config, update_config

logger = logging.getLogger(__name__)

MAX_OFFSET_MHZ = 255
MAX_CLOCK_MHZ = 4000  # validation ceiling; the real range comes from the GPU
MAX_CLOCK_HEADROOM = 300  # added above the observed max so "stock" is uncapped
MAX_CLOCK_SPAN = 800      # how far below the baseline the cap slider reaches

PCI_SYSFS = Path("/sys/bus/pci/devices")
NVIDIA_VENDOR = "0x10de"
HELPER_INSTALLED = "/usr/lib/gigamate/gigamate-dgpu-nvml"

# Watcher timing (seconds). Each idle probe opens NVML briefly, so keep the
# cadence modest to avoid churn (and avoid resetting the driver's autosuspend).
TICK_SEC = 2.0
IDLE_POLL_SEC = 30.0
IDLE_CLEAR_SEC = 60.0
RETRY_BACKOFF_SEC = 30.0
IDLE_UTIL_REAPPLY = 5  # percent

_HELPER_TIMEOUT = 15


# ──────────────────────────────────────────────────────────────────────────────
# Device discovery / sysfs (never wakes the GPU)
# ──────────────────────────────────────────────────────────────────────────────

def find_nvidia_bdf() -> Optional[str]:
    """Return the PCI BDF of the NVIDIA 3D/VGA controller, or None."""
    try:
        names = os.listdir(PCI_SYSFS)
    except OSError:
        return None
    for name in sorted(names):
        dev = PCI_SYSFS / name
        try:
            vendor = (dev / "vendor").read_text().strip()
            klass = (dev / "class").read_text().strip()
        except OSError:
            continue
        if vendor != NVIDIA_VENDOR:
            continue
        if klass.startswith("0x0300") or klass.startswith("0x0302"):
            return name
    return None


def _read_text(path: Path) -> Optional[str]:
    try:
        text = path.read_text().strip()
        return text or None
    except OSError:
        return None


def runtime_status(bdf: str) -> Optional[str]:
    return _read_text(PCI_SYSFS / bdf / "power" / "runtime_status")


def power_state(bdf: str) -> Optional[str]:
    return _read_text(PCI_SYSFS / bdf / "power_state")


def wake_holders() -> List[str]:
    """Best-effort list of process names holding /dev/nvidia* open."""
    holders = set()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            fd_dir = Path("/proc") / pid / "fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            for fd in fds:
                try:
                    target = os.readlink(fd_dir / fd)
                except OSError:
                    continue
                if target.startswith("/dev/nvidia"):
                    comm = _read_text(Path("/proc") / pid / "comm")
                    if comm:
                        holders.add(comm)
                    break
    except OSError:
        pass
    return sorted(holders)


# ──────────────────────────────────────────────────────────────────────────────
# Helper invocation
# ──────────────────────────────────────────────────────────────────────────────

def helper_path() -> Optional[str]:
    """Locate the privileged helper (installed path, dev fallback, or override)."""
    env = os.environ.get("GIGAMATE_DGPU_HELPER")
    if env and os.path.exists(env):
        return env
    if os.path.exists(HELPER_INSTALLED):
        return HELPER_INSTALLED
    dev = Path(__file__).resolve().parent.parent.parent / "data" / "gigamate-dgpu-nvml"
    if dev.exists():
        return str(dev)
    return None


def _run_helper(args: List[str]) -> Dict:
    """Run the helper (as root) and return its parsed JSON result."""
    path = helper_path()
    if path is None:
        return {"ok": False, "error": "helper not installed"}

    if os.geteuid() == 0:
        cmd = [path, *args]
    else:
        if not os.path.exists("/usr/bin/pkexec"):
            return {"ok": False, "error": "pkexec not available"}
        cmd = ["pkexec", path, *args]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_HELPER_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "helper/pkexec not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "helper timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    out = (proc.stdout or "").strip().splitlines()
    if not out:
        err = (proc.stderr or "").strip()
        return {"ok": False, "error": err or f"helper exit {proc.returncode}"}
    try:
        return json.loads(out[-1])
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid helper output"}


_probe_cache: Optional[Dict] = None


def probe(force: bool = False) -> Dict:
    """Return {"supported": bool, "device": str, "gpu_max_clock_mhz": int, ...}."""
    global _probe_cache
    if _probe_cache is not None and not force:
        return _probe_cache
    bdf = find_nvidia_bdf()
    if bdf is None:
        _probe_cache = {"supported": False, "error": "no NVIDIA dGPU"}
        return _probe_cache
    res = _run_helper(["probe"])
    supported = bool(res.get("ok") and res.get("supported"))
    _probe_cache = {
        "supported": supported,
        "device": res.get("device", bdf),
        "offset_mhz": res.get("offset_mhz"),
        "gpu_max_clock_mhz": res.get("gpu_max_clock_mhz"),
        "error": res.get("error"),
    }
    return _probe_cache


def get_state() -> Dict:
    """Read the current offset (+ utilization) via the helper."""
    return _run_helper(["get"])


def apply_tune(offset_mhz: int, max_clock_mhz: int = 0) -> Dict:
    """Apply the V/F offset (0..255) and the max-clock cap (0 = off) together."""
    offset_mhz = max(0, min(MAX_OFFSET_MHZ, int(offset_mhz)))
    max_clock_mhz = max(0, min(MAX_CLOCK_MHZ, int(max_clock_mhz)))
    return _run_helper(["apply", str(offset_mhz), str(max_clock_mhz)])


def clear_tune() -> Dict:
    """Return the dGPU to stock (offset 0, clocks unlocked)."""
    return _run_helper(["clear"])


# Backwards-compatible helpers (used by tests / callers).
def apply_offset(mhz: int) -> Dict:
    return apply_tune(mhz, 0)


def clear_offset() -> Dict:
    return clear_tune()


# ──────────────────────────────────────────────────────────────────────────────
# Runtime state file (cross-process "what is actually applied")
# ──────────────────────────────────────────────────────────────────────────────

def state_file_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return Path(runtime_dir) / "gigamate-dgpu.json"
    return Path(f"/tmp/gigamate-dgpu-{os.getuid()}.json")


def read_state_file() -> Dict:
    """Read the watcher's runtime state (empty dict if absent/unreadable)."""
    try:
        return json.loads(state_file_path().read_text())
    except (OSError, ValueError):
        return {}


def _write_state_file(state: Dict) -> None:
    path = state_file_path()
    try:
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, path)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Watcher
# ──────────────────────────────────────────────────────────────────────────────

class DgpuWatcher:
    """Applies/clears the dGPU tuning based on the GPU power state.

    ``tick()`` must be called periodically (the watcher service calls it; tests
    call it directly). It is cheap when nothing changes: it reads one sysfs file
    and only shells out to the helper on transitions, on a desired-value change,
    or during the idle probe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._desired_enabled = False      # undervolt master switch
        self._desired_offset = 0
        self._desired_max_enabled = False  # max-clock master switch
        self._desired_max = 0
        self._auto = True

        # What we last successfully applied to the hardware (0 = stock).
        self._applied_offset = 0
        self._applied_max = 0
        self._idle_cleared = False
        self._idle_since: Optional[float] = None
        self._last_poll = 0.0
        self._last_apply_fail = 0.0
        self._last_state_json: Optional[str] = None

        self._status: Dict = {
            "supported": None,
            "device": None,
            "runtime": None,
            "power_state": None,
            "applied": False,
            "offset_mhz": None,
            "max_clock_mhz": None,
            "util": None,
            "enabled": False,
            "desired_offset": 0,
            "desired_max_clock": 0,
            "auto": True,
            "last_error": None,
        }

    # ── configuration ──
    def set_desired(self, enabled: bool, offset: int, auto: bool = True,
                    max_enabled: bool = False, max_clock: int = 0) -> None:
        offset = max(0, min(MAX_OFFSET_MHZ, int(offset)))
        max_clock = max(0, min(MAX_CLOCK_MHZ, int(max_clock)))
        eff_offset = offset if enabled else 0
        eff_max = max_clock if max_enabled else 0
        with self._lock:
            changed = (eff_offset != self._desired_offset) or (eff_max != self._desired_max)
            self._desired_enabled = bool(enabled)
            self._desired_offset = eff_offset
            self._desired_max_enabled = bool(max_enabled)
            self._desired_max = eff_max
            self._auto = bool(auto)
            self._status["enabled"] = bool(enabled) or bool(max_enabled)
            self._status["desired_offset"] = eff_offset
            self._status["desired_max_clock"] = eff_max
            self._status["auto"] = bool(auto)
            # An explicit change should take effect immediately, even if we had
            # idle-cleared; the idle logic will clear again if still idle.
            if changed:
                self._idle_cleared = False
                self._idle_since = None

    def load_desired_from_config(self) -> None:
        cfg = load_config()
        self.set_desired(
            bool(cfg.get("dgpu_undervolt_enabled", DEFAULT_CONFIG["dgpu_undervolt_enabled"])),
            int(cfg.get("dgpu_undervolt_offset_mhz", DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"])),
            bool(cfg.get("dgpu_undervolt_auto", DEFAULT_CONFIG["dgpu_undervolt_auto"])),
            bool(cfg.get("dgpu_max_clock_enabled", DEFAULT_CONFIG["dgpu_max_clock_enabled"])),
            int(cfg.get("dgpu_max_clock_mhz", DEFAULT_CONFIG["dgpu_max_clock_mhz"])),
        )

    def status(self) -> Dict:
        with self._lock:
            return dict(self._status)

    def _target(self) -> Tuple[int, int]:
        with self._lock:
            return self._desired_offset, self._desired_max

    # ── state machine ──
    def tick(self, now: Optional[float] = None, force_apply: bool = False) -> None:
        now = time.monotonic() if now is None else now
        bdf = find_nvidia_bdf()
        if bdf is None:
            with self._lock:
                self._status.update({"supported": False, "device": None, "runtime": None})
            self._sync_status()
            return

        rstatus = runtime_status(bdf)
        pstate = power_state(bdf)
        asleep = (rstatus == "suspended") or (pstate in ("D3hot", "D3cold"))

        with self._lock:
            self._status.update({
                "device": bdf,
                "runtime": rstatus,
                "power_state": pstate,
                "supported": bool(probe().get("supported")),
            })

        if asleep:
            if self._applied_offset or self._applied_max:
                clear_tune()
                self._set_applied(0, 0)
            self._idle_cleared = False
            self._idle_since = None
            self._sync_status()
            return

        if rstatus != "active":
            # Transitional/unknown: do nothing (avoid touching a sleeping GPU).
            return

        eff_offset, eff_max = self._target()
        enabled = eff_offset > 0 or eff_max > 0

        if not enabled:
            if self._applied_offset or self._applied_max:
                clear_tune()
                self._set_applied(0, 0)
            self._idle_cleared = False
            self._idle_since = None
            self._sync_status()
            return

        # Auto-apply disabled: never (re)apply on boot/wake; leave any manually
        # applied state untouched (suspend/disable clears above already ran).
        if not (force_apply or self._auto):
            self._sync_status()
            return

        # Idle-cleared: wait for the GPU to become busy before re-applying.
        if self._idle_cleared:
            if now - self._last_poll >= IDLE_POLL_SEC:
                self._poll_and_maybe_reapply(now)
            self._sync_status()
            return

        # Desired changed since our last apply -> (re)apply now.
        if self._applied_offset != eff_offset or self._applied_max != eff_max:
            self._do_apply(now)
            self._sync_status()
            return

        # Applied and matching: poll for idle-clear.
        if now - self._last_poll >= IDLE_POLL_SEC:
            self._poll_and_maybe_idle_clear(now)
        self._sync_status()

    def _set_applied(self, offset: int, max_clock: int) -> None:
        with self._lock:
            self._applied_offset = offset
            self._applied_max = max_clock
            self._status["offset_mhz"] = offset or None
            self._status["max_clock_mhz"] = max_clock or None

    def _do_apply(self, now: float) -> None:
        if now - self._last_apply_fail < RETRY_BACKOFF_SEC:
            return
        eff_offset, eff_max = self._target()
        res = apply_tune(eff_offset, eff_max)
        if res.get("ok"):
            # If the clock cap failed, record the offset but keep max at 0 so we
            # retry (and surface the error) instead of pretending it applied.
            applied_max = 0 if res.get("max_clock_error") else eff_max
            self._set_applied(eff_offset, applied_max)
            with self._lock:
                self._idle_cleared = False
                self._last_poll = now
                self._status["last_error"] = res.get("max_clock_error")
        else:
            with self._lock:
                self._last_apply_fail = now
                self._status["last_error"] = res.get("error") or "apply failed"

    def _poll_and_maybe_idle_clear(self, now: float) -> None:
        self._last_poll = now
        res = get_state()
        if not res.get("ok"):
            with self._lock:
                self._status["last_error"] = res.get("error")
            return
        util = res.get("util")
        with self._lock:
            self._status["util"] = util
        if util == 0:
            if self._idle_since is None:
                self._idle_since = now
            elif now - self._idle_since >= IDLE_CLEAR_SEC:
                clear_tune()
                self._set_applied(0, 0)
                self._idle_cleared = True
                self._idle_since = None
        else:
            self._idle_since = None

    def _poll_and_maybe_reapply(self, now: float) -> None:
        self._last_poll = now
        res = get_state()
        if not res.get("ok"):
            with self._lock:
                self._status["last_error"] = res.get("error")
            return
        util = res.get("util") or 0
        with self._lock:
            self._status["util"] = util
        if util >= IDLE_UTIL_REAPPLY:
            self._do_apply(now)

    def _sync_status(self) -> None:
        with self._lock:
            self._status["applied"] = bool(self._applied_offset or self._applied_max)

    def write_state(self) -> None:
        """Publish the current status to the runtime state file.

        Only the long-lived watcher service (and explicit apply actions) should
        call this; short-lived CLI/Center processes must never overwrite the
        service's authoritative state.
        """
        with self._lock:
            snapshot = dict(self._status)
            snapshot["applied_offset"] = self._applied_offset
            snapshot["applied_max"] = self._applied_max
            snapshot["idle_cleared"] = self._idle_cleared
            snapshot["ts"] = time.time()
        blob = json.dumps(snapshot, sort_keys=True)
        if blob != self._last_state_json:
            self._last_state_json = blob
            _write_state_file(snapshot)

    def mark_cleared(self) -> None:
        """Forget any applied state (used after an out-of-band clear)."""
        self._set_applied(0, 0)
        with self._lock:
            self._idle_cleared = False
            self._idle_since = None
            self._status["applied"] = False


# Module-level singleton used by the CLI / Center / hardware.
watcher = DgpuWatcher()


# ──────────────────────────────────────────────────────────────────────────────
# High-level API used by CLI / Center / hardware
# ──────────────────────────────────────────────────────────────────────────────

def set_desired_config(enabled: Optional[bool] = None, offset: Optional[int] = None,
                       auto: Optional[bool] = None,
                       max_enabled: Optional[bool] = None,
                       max_clock: Optional[int] = None) -> None:
    """Persist the desired dGPU tuning and act immediately.

    Only the arguments that are not None are changed, so the undervolt and
    max-clock controls stay independent. Disabling always forces the affected
    value back to stock, even though applied state is process-local.
    """
    cfg0 = load_config()
    if enabled is None:
        enabled = bool(cfg0.get("dgpu_undervolt_enabled", DEFAULT_CONFIG["dgpu_undervolt_enabled"]))
    if offset is None:
        offset = int(cfg0.get("dgpu_undervolt_offset_mhz", DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"]))
    offset = max(0, min(MAX_OFFSET_MHZ, int(offset)))
    enabled = bool(enabled) and offset > 0

    def _mutate(cfg):
        cfg["dgpu_undervolt_enabled"] = bool(enabled)
        cfg["dgpu_undervolt_offset_mhz"] = offset
        if auto is not None:
            cfg["dgpu_undervolt_auto"] = bool(auto)
        if max_enabled is not None:
            cfg["dgpu_max_clock_enabled"] = bool(max_enabled)
        if max_clock is not None:
            mc = max(0, min(MAX_CLOCK_MHZ, int(max_clock)))
            cfg["dgpu_max_clock_mhz"] = mc
        # Keep the stored cap consistent with its switch.
        if not cfg.get("dgpu_max_clock_enabled", False):
            cfg["dgpu_max_clock_mhz"] = 0
        return cfg

    cfg = update_config(_mutate)

    new_max_enabled = bool(cfg.get("dgpu_max_clock_enabled", DEFAULT_CONFIG["dgpu_max_clock_enabled"]))
    new_max = int(cfg.get("dgpu_max_clock_mhz", DEFAULT_CONFIG["dgpu_max_clock_mhz"]))
    watcher.set_desired(enabled, offset,
                        bool(cfg.get("dgpu_undervolt_auto", DEFAULT_CONFIG["dgpu_undervolt_auto"])),
                        new_max_enabled, new_max)

    # Ensure the hardware reflects the change immediately (or is cleared). An
    # explicit user action always applies now, even when auto-apply is off.
    bdf = find_nvidia_bdf()
    active = bdf is not None and runtime_status(bdf) == "active"
    if active and (enabled or (new_max_enabled and new_max > 0)):
        watcher.tick(force_apply=True)
    elif active:
        clear_tune()
        watcher.mark_cleared()
    else:
        watcher.mark_cleared()
    watcher.write_state()


def set_auto_config(enabled: bool) -> Dict:
    """Enable/disable automatic application on boot, wake and resume.

    Disabling never clears the current hardware state — it only stops GigaMate
    from (re)applying the tuning automatically. Enabling applies immediately if
    something is configured and the GPU is awake.
    """
    def _mutate(cfg):
        cfg["dgpu_undervolt_auto"] = bool(enabled)
        return cfg

    update_config(_mutate)
    watcher.load_desired_from_config()
    if enabled:
        watcher.tick()
        watcher.write_state()
    return watcher.status()


def apply_from_config() -> None:
    """Reconcile the watcher with persisted config and act once (login/resume)."""
    watcher.load_desired_from_config()
    watcher.tick()


def human_status() -> str:
    st = watcher.status()
    if not st.get("supported"):
        err = st.get("last_error") or probe().get("error") or "unsupported"
        return f"unsupported ({err})"
    if not st.get("runtime"):
        return "no dGPU"
    if st.get("runtime") == "suspended":
        return "asleep (cleared)"
    if not st.get("enabled"):
        return "off"
    parts = []
    if st.get("desired_offset"):
        parts.append(f"+{st['desired_offset']} MHz")
    if st.get("desired_max_clock"):
        parts.append(f"cap {st['desired_max_clock']} MHz")
    label = ", ".join(parts) or "off"
    if st.get("applied"):
        suffix = "" if st.get("auto", True) else " (manual)"
        return f"{label} applied{suffix}"
    if st.get("last_error"):
        return f"error: {st['last_error']}"
    if not st.get("auto", True):
        return f"{label} pending (auto-apply off)"
    return f"{label} pending"


def run_watcher() -> None:
    """Entry point for the watcher service (``gigamate-dgpu-watch``).

    Runs the sleep-aware state machine independently of the tray and Center so
    the tuning is applied on wake and cleared on suspend/disable even when no UI
    process is running. Reloads the desired values when the config changes.
    """
    import signal

    from .config import CONFIG_FILE

    stopping = {"flag": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stopping["flag"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    def _mtime() -> float:
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0.0

    watcher.load_desired_from_config()
    watcher.write_state()
    last_mtime = _mtime()
    while not stopping["flag"]:
        try:
            m = _mtime()
            if m != last_mtime:
                last_mtime = m
                watcher.load_desired_from_config()
            watcher.tick()
            watcher.write_state()
        except Exception as exc:  # noqa: BLE001
            logger.debug("dGPU watcher tick failed: %s", exc)
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    run_watcher()
