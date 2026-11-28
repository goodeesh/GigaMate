"""ACPI WMI communication layer for Gigabyte laptop fan/power control.

Provides multiple backends (kernel module, acpi_call, mock) to read
sensor data and switch power profiles via the AMW0 WMI device.

Usage:
    ctrl = AcpiController()           # auto-detect best backend
    if ctrl.available:
        state = ctrl.read_state()      # FanState with all sensors
        ctrl.set_profile(FanProfile.GAMING)
"""

import enum
import os
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ──────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────


class FanProfile(enum.IntEnum):
    """Power/thermal profiles available on Gigabyte Aero/AORUS laptops."""

    QUIET = 0
    BALANCED = 1
    PERFORMANCE = 2
    GAMING = 3

    @classmethod
    def from_name(cls, name: str) -> "FanProfile":
        """Parse from lowercase name, e.g. 'gaming' -> FanProfile.GAMING."""
        mapping = {
            "quiet": cls.QUIET,
            "balanced": cls.BALANCED,
            "performance": cls.PERFORMANCE,
            "gaming": cls.GAMING,
        }
        return mapping[name.lower()]

    @classmethod
    def names(cls) -> Dict[str, "FanProfile"]:
        return {
            "quiet": cls.QUIET,
            "balanced": cls.BALANCED,
            "performance": cls.PERFORMANCE,
            "gaming": cls.GAMING,
        }


@dataclass
class FanState:
    """Snapshot of all fan/temperature sensors and current power profile.

    All fields are Optional — partial ACPI support is handled gracefully.
    """

    temp_cpu: Optional[int] = None  # °C
    temp_socket: Optional[int] = None  # °C
    fan1_rpm: Optional[int] = None  # RPM
    fan2_rpm: Optional[int] = None  # RPM
    duty_total: Optional[int] = None  # percentage
    duty_cpu: Optional[int] = None  # percentage
    duty_gpu: Optional[int] = None  # percentage
    profile: Optional[FanProfile] = None


@dataclass
class AcpiCapabilities:
    """What ACPI features are available on this system."""

    has_temperature: bool = False
    has_fan_rpm: bool = False
    has_fan_duty: bool = False
    has_power_profiles: bool = False
    fan_count: int = 0
    profile_ids: List[int] = field(default_factory=lambda: [0, 1, 2, 3])
    discovered_profiles: Dict[int, Dict[str, str]] = field(default_factory=dict)
    backend: str = "none"  # "module", "acpi_call", "mock", "none"


@dataclass
class AcpiModuleStatus:
    """Why the ``gigamate_acpi`` module is or is not usable right now."""

    running_kernel: str
    loaded: bool = False
    sysfs_present: bool = False
    module_for_running: bool = False
    other_kernels: List[str] = field(default_factory=list)
    # loaded, not-loaded, wrong-kernel, not-installed, amw0-missing,
    # amw0-no-response, unknown
    reason: str = "unknown"

    @property
    def summary(self) -> str:
        if self.reason == "loaded":
            return f"loaded for {self.running_kernel}"
        if self.reason == "amw0-no-response":
            return f"loaded for {self.running_kernel}, but sensor reads returned nothing"
        if self.reason == "amw0-missing":
            return f"loaded for {self.running_kernel}, but AMW0 was not found"
        if self.reason == "not-loaded":
            return f"installed for {self.running_kernel}, not loaded"
        if self.reason == "wrong-kernel":
            kernels = ", ".join(self.other_kernels) or "another kernel"
            return f"installed for {kernels}, not for running {self.running_kernel}"
        if self.reason == "not-installed":
            return "not installed"
        return "unknown"

    @property
    def hint(self) -> str:
        if self.reason == "loaded":
            return "ACPI sensors are available."
        if self.reason == "amw0-no-response":
            return ("AMW0 is present, but the known sensor commands returned nothing. "
                    "This model may use a different EC command set.")
        if self.reason == "amw0-missing":
            return ("The module loaded but did not find an AMW0 device. "
                    "Check 'dmesg | grep gigamate_acpi'.")
        if self.reason == "not-loaded":
            return ("Run 'sudo modprobe gigamate_acpi'. If it fails, check "
                    "'dmesg | grep gigamate_acpi'.")
        if self.reason == "wrong-kernel":
            if len(self.other_kernels) > 1:
                target = "one of: " + ", ".join(self.other_kernels)
            else:
                target = self.other_kernels[0] if self.other_kernels else "the installed kernel"
            return (f"Reboot into {target}, or install headers for {self.running_kernel} "
                    "and run 'gigamate repair'.")
        if self.reason == "not-installed":
            return ("Re-run install.sh, or install the kernel headers for "
                    f"{self.running_kernel} and run 'gigamate repair'.")
        return "Run 'gigamate doctor' for more detail."


# ──────────────────────────────────────────────
# Backend interface
# ──────────────────────────────────────────────


class AcpiBackend(ABC):
    """Abstract base for ACPI communication backends."""

    @abstractmethod
    def detect(self) -> AcpiCapabilities:
        """Probe hardware and report available features."""
        ...

    @abstractmethod
    def read_state(self) -> FanState:
        """Read all sensors and return a FanState snapshot."""
        ...

    @abstractmethod
    def set_profile(self, profile: int) -> bool:
        """Switch power profile (0-3). Returns True on success."""
        ...

    @abstractmethod
    def get_profile(self) -> Optional[int]:
        """Read current power profile index (0-3), or None if unknown."""
        ...

    def has_cpu_ppt_control(self) -> bool:
        """Whether writable CPU PPT sysfs controls are available."""
        return False

    def has_gpu_ctgp_control(self) -> bool:
        """Whether writable GPU cTGP offset sysfs control is available."""
        return False

    def has_gpu_dynamic_boost_control(self) -> bool:
        """Whether writable GPU Dynamic Boost sysfs control is available."""
        return False

    def set_cpu_ppt_limits(self, stapm_w: int, slow_w: int, fast_w: int) -> bool:
        """Set CPU STAPM/Slow/Fast PPT limits in Watts."""
        return False

    def set_gpu_ctgp_offset(self, offset_w: int) -> bool:
        """Set GPU configurable TGP offset above base (Watts)."""
        return False

    def set_gpu_dynamic_boost(self, enabled: bool) -> bool:
        """Enable or disable NVIDIA Dynamic Boost via ACPI."""
        return False

    def read_usbc_adapter(self) -> Optional[bool]:
        """Read EC USB-C adapter indicator if exposed, else None."""
        return None

    def read_npcf_ctgp_offset_w(self) -> Optional[int]:
        """Read live NPCF cTGP offset (ACBT / 8) in Watts if exposed."""
        return None

    def read_npcf_dynamic_boost(self) -> Optional[bool]:
        """Read live NPCF Dynamic Boost enabled flag (!DBAC) if exposed."""
        return None


# ──────────────────────────────────────────────
# Mock backend (for testing)
# ──────────────────────────────────────────────


class MockBackend(AcpiBackend):
    """Mock backend returning plausible static values.

    Activated by setting environment variable GIGAMATE_ACPI_MOCK=1.
    """

    def __init__(self) -> None:
        self._last_profile: Optional[int] = 1  # Balanced

    def detect(self) -> AcpiCapabilities:
        return AcpiCapabilities(
            has_temperature=True,
            has_fan_rpm=True,
            has_fan_duty=True,
            has_power_profiles=True,
            fan_count=2,
            profile_ids=[0, 1, 2, 3],
            discovered_profiles={
                0: {"name": "Quiet", "description": "Low fan noise, capped GPU power"},
                1: {"name": "Balanced", "description": "Balanced performance and noise"},
                2: {"name": "Performance", "description": "High performance, more fan noise"},
                3: {"name": "Gaming", "description": "Maximum GPU power and cooling"},
            },
            backend="mock",
        )

    def read_state(self) -> FanState:
        return FanState(
            temp_cpu=65,
            temp_socket=55,
            fan1_rpm=3580,
            fan2_rpm=2450,
            duty_total=48,
            duty_cpu=45,
            duty_gpu=52,
            profile=FanProfile(self._last_profile) if self._last_profile is not None else None,
        )

    def set_profile(self, profile: int) -> bool:
        if 0 <= profile <= 3:
            self._last_profile = profile
            return True
        return False

    def get_profile(self) -> Optional[int]:
        return self._last_profile


# ──────────────────────────────────────────────
# acpi_call backend (/proc/acpi/call)
# ──────────────────────────────────────────────


PROC_ACPI_CALL = Path("/proc/acpi/call")


def _plausible(value: Optional[int], lo: int, hi: int) -> Optional[int]:
    """Return ``value`` if it is a plausible sensor reading, else None.

    Guards against EC error/invalid sentinels leaking into the UI as bogus
    temperatures or fan speeds.
    """
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


class AcpiCallBackend(AcpiBackend):
    """Backend using the acpi_call kernel module via /proc/acpi/call.

    Requires the acpi_call (or acpi_call-dkms) kernel module loaded.
    """

    # /proc/acpi/call is a write-command-then-read-result interface with no
    # per-call isolation; serialize access so concurrent processes/threads
    # cannot observe each other's replies.
    _CALL_LOCK = threading.Lock()

    def detect(self) -> AcpiCapabilities:
        if not PROC_ACPI_CALL.exists():
            return AcpiCapabilities(backend="none")
        if not os.access(str(PROC_ACPI_CALL), os.W_OK):
            return AcpiCapabilities(backend="none")

        caps = AcpiCapabilities(backend="acpi_call")

        # Probe sensors (all read-only WMBC queries).
        interface_ok = False
        if self._wmbc_read(0xE1) is not None:
            caps.has_temperature = True
            interface_ok = True
        if self._wmbc_read(0xE4) is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
            interface_ok = True
        if self._wmbc_read(0xE5) is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
            interface_ok = True
        if self._wmbc_read(0x46) is not None:
            caps.has_fan_duty = True
            interface_ok = True

        # Never probe profile support with a WMBD write (it would change the
        # active profile). If the AMW0 WMI interface answers WMBC queries at
        # all, WMBD profile control is assumed available.
        caps.has_power_profiles = interface_ok
        if not interface_ok:
            # /proc/acpi/call present but nothing answered: not a usable backend.
            return AcpiCapabilities(backend="none")

        return caps

    def read_state(self) -> FanState:
        state = FanState()
        state.temp_cpu = _plausible(self._wmbc_read(0xE1), 0, 150)
        state.temp_socket = _plausible(self._wmbc_read(0xE2), 0, 150)
        state.fan1_rpm = _plausible(self._wmbc_read(0xE4), 0, 20000)
        state.fan2_rpm = _plausible(self._wmbc_read(0xE5), 0, 20000)
        state.duty_total = _plausible(self._wmbc_read(0x50), 0, 100)
        state.duty_cpu = _plausible(self._wmbc_read(0x46), 0, 100)
        state.duty_gpu = _plausible(self._wmbc_read(0x47), 0, 100)
        # No known WMBC command to read current profile; leave as None
        return state

    def set_profile(self, profile: int) -> bool:
        if not (0 <= profile <= 3):
            return False
        result = self._wmbd_write(0xED, profile)
        return result is not None

    def get_profile(self) -> Optional[int]:
        # No known WMBC command to read current profile
        # Could probe by writing/reading back if we had a cache
        return None

    def _acpi_call(self, cmd_str: str) -> Optional[int]:
        """Issue a command to /proc/acpi/call and parse its reply.

        acpi_call writes the command and exposes the result via the same file;
        the write+read pair is serialized with a process-wide lock. Replies are
        typically hexadecimal (``0x...``), so parse with base 0.
        """
        with self._CALL_LOCK:
            try:
                PROC_ACPI_CALL.write_text(cmd_str)
                result = PROC_ACPI_CALL.read_text().strip()
            except (OSError, IOError):
                return None

        if not result or result.lower() in ("none", "not supported", "error"):
            return None
        try:
            value = int(result, 0)
        except (ValueError, TypeError):
            return None
        # acpi_call uses all-ones as an error sentinel (32- and 64-bit).
        if value in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            return None
        return value

    def _wmbc_read(self, cmd: int) -> Optional[int]:
        """Call WMBC (read) via /proc/acpi/call and parse the result.

        The AMW0 WMBC/WMBD methods take three integer arguments
        ``(0, command, value)`` (see the kernel module), so the leading ``0``
        must be present.
        """
        return self._acpi_call(f"WMBC 0 {cmd} 0")

    def _wmbd_write(self, cmd: int, val: int) -> Optional[int]:
        """Call WMBD (write) via /proc/acpi/call and parse the result."""
        return self._acpi_call(f"WMBD 0 {cmd} {val}")


# ──────────────────────────────────────────────
# Kernel module backend (sysfs)
# ──────────────────────────────────────────────


GIGAMATE_ACPI_SYSFS = Path("/sys/devices/platform/gigamate_acpi")
MODULES_DIR = Path("/lib/modules")


def _module_kernels() -> List[str]:
    """Kernels that have a gigamate_acpi module file installed."""
    found = set()
    patterns = (
        "*/updates/dkms/gigamate_acpi.ko*",
        "*/updates/gigamate_acpi.ko*",
        "*/extra/gigamate_acpi.ko*",
        "*/kernel/**/gigamate_acpi.ko*",
    )
    for pattern in patterns:
        try:
            for path in MODULES_DIR.glob(pattern):
                try:
                    found.add(path.relative_to(MODULES_DIR).parts[0])
                except (ValueError, IndexError):
                    continue
        except OSError:
            continue
    return sorted(found)


def _module_available(kernel: str) -> bool:
    if kernel in _module_kernels():
        return True
    modinfo = shutil.which("modinfo")
    if not modinfo:
        return False
    try:
        proc = subprocess.run(
            [modinfo, "-k", kernel, "gigamate_acpi"],
            capture_output=True,
            timeout=3,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _sysfs_has_sensor() -> bool:
    """Whether any of the sensor attributes can actually be read."""
    for name in ("temp1_input", "fan1_input", "pwm1"):
        try:
            int((GIGAMATE_ACPI_SYSFS / name).read_text().strip())
            return True
        except (OSError, ValueError):
            continue
    return False


def acpi_module_status() -> AcpiModuleStatus:
    """Explain why the ACPI backend is or is not available."""
    running = os.uname().release
    loaded = Path("/sys/module/gigamate_acpi").exists()
    sysfs_present = GIGAMATE_ACPI_SYSFS.is_dir()
    installed = _module_kernels()
    status = AcpiModuleStatus(
        running_kernel=running,
        loaded=loaded,
        sysfs_present=sysfs_present,
        module_for_running=_module_available(running),
        other_kernels=[kernel for kernel in installed if kernel != running],
    )

    if loaded:
        if not sysfs_present:
            status.reason = "amw0-missing"
        elif not _sysfs_has_sensor():
            status.reason = "amw0-no-response"
        else:
            status.reason = "loaded"
    elif status.module_for_running:
        status.reason = "not-loaded"
    elif status.other_kernels:
        status.reason = "wrong-kernel"
    else:
        status.reason = "not-installed"
    return status


class ModuleBackend(AcpiBackend):
    """Backend using our own gigamate_acpi kernel module via sysfs.

    This is the preferred backend — fastest, most reliable.
    """

    def detect(self) -> AcpiCapabilities:
        if not GIGAMATE_ACPI_SYSFS.is_dir():
            return AcpiCapabilities(backend="none")

        caps = AcpiCapabilities(backend="module")

        # The module creates every attribute unconditionally, so a file's mere
        # existence does not prove the EC implements it. Read actual values to
        # learn the real sensor/fan layout (and allow fan_count == 1).
        if self._read_sysfs_int("temp1_input") is not None:
            caps.has_temperature = True
        if self._read_sysfs_int("fan1_input") is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if self._read_sysfs_int("fan2_input") is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if self._read_sysfs_int("pwm1") is not None:
            caps.has_fan_duty = True
        if (GIGAMATE_ACPI_SYSFS / "profile").exists():
            from .power_limits import discover_acpi_profiles

            caps.discovered_profiles = discover_acpi_profiles()
            caps.has_power_profiles = len(caps.discovered_profiles) >= 2
            if caps.discovered_profiles:
                caps.profile_ids = sorted(caps.discovered_profiles.keys())

        if not (caps.has_temperature or caps.has_fan_rpm or caps.has_fan_duty or caps.has_power_profiles):
            return AcpiCapabilities(backend="none")

        return caps

    def read_state(self) -> FanState:
        state = FanState()
        state.temp_cpu = _plausible(self._read_sysfs_int("temp1_input"), 0, 150)
        state.temp_socket = _plausible(self._read_sysfs_int("temp2_input"), 0, 150)
        state.fan1_rpm = _plausible(self._read_sysfs_int("fan1_input"), 0, 20000)
        state.fan2_rpm = _plausible(self._read_sysfs_int("fan2_input"), 0, 20000)
        state.duty_total = _plausible(self._read_sysfs_int("pwm1_total"), 0, 100)
        state.duty_cpu = _plausible(self._read_sysfs_int("pwm1"), 0, 100)
        state.duty_gpu = _plausible(self._read_sysfs_int("pwm2"), 0, 100)
        prof = self._read_sysfs_int("profile")
        if prof is not None and 0 <= prof <= 3:
            state.profile = FanProfile(prof)
        return state

    def set_profile(self, profile: int) -> bool:
        if not (0 <= profile <= 3):
            return False
        try:
            (GIGAMATE_ACPI_SYSFS / "profile").write_text(f"{profile}\n")
            return True
        except OSError:
            return False

    def get_profile(self) -> Optional[int]:
        return self._read_sysfs_int("profile")

    def _read_sysfs_int(self, name: str) -> Optional[int]:
        """Read an integer from a sysfs file, returning None on failure."""
        path = GIGAMATE_ACPI_SYSFS / name
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError, IOError):
            return None

    def _write_sysfs_int(self, name: str, val: int) -> bool:
        """Write an integer to a sysfs file, returning True on success."""
        path = GIGAMATE_ACPI_SYSFS / name
        try:
            path.write_text(f"{int(val)}\n")
            return True
        except (OSError, IOError):
            return False

    def has_cpu_ppt_control(self) -> bool:
        return all(
            (GIGAMATE_ACPI_SYSFS / name).exists()
            for name in ("cpu_stapm_w", "cpu_slow_ppt_w", "cpu_fast_ppt_w")
        )

    def has_gpu_ctgp_control(self) -> bool:
        return (GIGAMATE_ACPI_SYSFS / "gpu_ctgp_offset_w").exists()

    def has_gpu_dynamic_boost_control(self) -> bool:
        return (GIGAMATE_ACPI_SYSFS / "gpu_dynamic_boost").exists()

    def set_cpu_ppt_limits(self, stapm_w: int, slow_w: int, fast_w: int) -> bool:
        if not self.has_cpu_ppt_control():
            return False
        s_clamped = max(10, min(120, int(stapm_w)))
        l_clamped = max(10, min(120, int(slow_w)))
        f_clamped = max(10, min(120, int(fast_w)))
        return (
            self._write_sysfs_int("cpu_stapm_w", s_clamped)
            and self._write_sysfs_int("cpu_slow_ppt_w", l_clamped)
            and self._write_sysfs_int("cpu_fast_ppt_w", f_clamped)
        )

    def set_gpu_ctgp_offset(self, offset_w: int) -> bool:
        if not self.has_gpu_ctgp_control():
            return False
        return self._write_sysfs_int("gpu_ctgp_offset_w", max(0, min(10, int(offset_w))))

    def set_gpu_dynamic_boost(self, enabled: bool) -> bool:
        if not self.has_gpu_dynamic_boost_control():
            return False
        return self._write_sysfs_int("gpu_dynamic_boost", 1 if enabled else 0)

    def read_usbc_adapter(self) -> Optional[bool]:
        val = self._read_sysfs_int("usbc_adapter")
        return bool(val) if val in (0, 1) else None

    def read_npcf_ctgp_offset_w(self) -> Optional[int]:
        val = self._read_sysfs_int("npcf_acbt")
        return (val // 8) if (val is not None and val >= 0) else None

    def read_npcf_dynamic_boost(self) -> Optional[bool]:
        val = self._read_sysfs_int("npcf_dbac")
        return (val == 0) if val in (0, 1) else None


# ──────────────────────────────────────────────
# AcpiController — unified interface
# ──────────────────────────────────────────────


class AcpiController:
    """High-level ACPI controller for Gigabyte laptops.

    Automatically selects the best available backend:
    1. Our gigamate_acpi kernel module (sysfs)
    2. acpi_call module (/proc/acpi/call)
    3. Mock backend (only with GIGAMATE_ACPI_MOCK=1 env var)

    All methods are safe to call even if no ACPI hardware is available —
    they return None / False gracefully.
    """

    def __init__(self, backend: Optional[str] = None) -> None:
        """Initialize controller.

        Args:
            backend: Force a specific backend ('module', 'acpi_call', 'mock').
                     If None, auto-detect.
        """
        self._backend: Optional[AcpiBackend] = None
        self._capabilities: Optional[AcpiCapabilities] = None

        if backend == "mock":
            self._backend = MockBackend()
        elif backend == "module":
            module_backend = ModuleBackend()
            caps = module_backend.detect()
            if caps.backend != "none":
                self._backend = module_backend
                self._capabilities = caps
        elif backend == "acpi_call":
            acpi_call_backend = AcpiCallBackend()
            caps = acpi_call_backend.detect()
            if caps.backend != "none":
                self._backend = acpi_call_backend
                self._capabilities = caps
        elif backend is None:
            self._auto_detect()
        else:
            raise ValueError(f"Unknown backend: {backend}")

    def _auto_detect(self) -> None:
        """Auto-detect the best available backend."""
        # Check env var for mock testing
        if os.environ.get("GIGAMATE_ACPI_MOCK"):
            self._backend = MockBackend()
            return

        # Prefer our kernel module
        module_backend = ModuleBackend()
        caps = module_backend.detect()
        if caps.backend != "none":
            self._backend = module_backend
            self._capabilities = caps
            return

        # Fall back to acpi_call
        acpi_call_backend = AcpiCallBackend()
        caps = acpi_call_backend.detect()
        if caps.backend != "none":
            self._backend = acpi_call_backend
            self._capabilities = caps
            return

        # No backend available
        self._backend = None

    @property
    def available(self) -> bool:
        """Whether ACPI hardware is available and working."""
        return self._backend is not None

    @property
    def capabilities(self) -> AcpiCapabilities:
        """Detected ACPI capabilities (cached after first call)."""
        if self._capabilities is None and self._backend is not None:
            self._capabilities = self._backend.detect()
        elif self._capabilities is None:
            self._capabilities = AcpiCapabilities()
        return self._capabilities

    def detect(self) -> AcpiCapabilities:
        """Re-probe hardware and return current capabilities."""
        if self._backend is not None:
            self._capabilities = self._backend.detect()
        else:
            self._capabilities = AcpiCapabilities()
        return self._capabilities

    def read_state(self) -> Optional[FanState]:
        """Read all sensors and return a FanState snapshot.

        Returns None if no backend is available.
        """
        if self._backend is None:
            return None
        try:
            return self._backend.read_state()
        except Exception:
            return None

    def set_profile(self, profile: FanProfile) -> bool:
        """Switch power profile.

        Args:
            profile: FanProfile enum value (QUIET=0, BALANCED=1, PERFORMANCE=2, GAMING=3)

        Returns True on success, False on failure.
        """
        if self._backend is None:
            return False
        if not isinstance(profile, FanProfile):
            return False
        try:
            return self._backend.set_profile(profile.value)
        except Exception:
            return False

    def get_profile(self) -> Optional[FanProfile]:
        """Read current power profile, or None if unknown."""
        if self._backend is None:
            return None
        try:
            val = self._backend.get_profile()
            if val is not None and 0 <= val <= 3:
                return FanProfile(val)
            return None
        except Exception:
            return None

    def _call_backend_bool(self, method: str, *args) -> bool:
        if self._backend is None:
            return False
        fn = getattr(self._backend, method, None)
        if fn is None:
            return False
        try:
            return bool(fn(*args))
        except Exception:
            return False

    def _call_backend_opt(self, method: str):
        if self._backend is None:
            return None
        fn = getattr(self._backend, method, None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    def has_cpu_ppt_control(self) -> bool:
        return self._call_backend_bool("has_cpu_ppt_control")

    def has_gpu_ctgp_control(self) -> bool:
        return self._call_backend_bool("has_gpu_ctgp_control")

    def has_gpu_dynamic_boost_control(self) -> bool:
        return self._call_backend_bool("has_gpu_dynamic_boost_control")

    def set_cpu_ppt_limits(self, stapm_w: int, slow_w: int, fast_w: int) -> bool:
        return self._call_backend_bool("set_cpu_ppt_limits", stapm_w, slow_w, fast_w)

    def set_gpu_ctgp_offset(self, offset_w: int) -> bool:
        return self._call_backend_bool("set_gpu_ctgp_offset", offset_w)

    def set_gpu_dynamic_boost(self, enabled: bool) -> bool:
        return self._call_backend_bool("set_gpu_dynamic_boost", enabled)

    def read_usbc_adapter(self) -> Optional[bool]:
        val = self._call_backend_opt("read_usbc_adapter")
        return val if isinstance(val, bool) else None

    def read_npcf_ctgp_offset_w(self) -> Optional[int]:
        val = self._call_backend_opt("read_npcf_ctgp_offset_w")
        return val if isinstance(val, int) and not isinstance(val, bool) else None

    def read_npcf_dynamic_boost(self) -> Optional[bool]:
        val = self._call_backend_opt("read_npcf_dynamic_boost")
        return val if isinstance(val, bool) else None


# ──────────────────────────────────────────────
# Probe helper (for calibration / model detection)
# ──────────────────────────────────────────────


def probe_acpi_capabilities(backend: Optional[str] = None) -> AcpiCapabilities:
    """Probe hardware and return detected ACPI capabilities.

    This is the ACPI equivalent of keyboard calibration — but automatic.
    Used by 'gigamate detect --acpi' and calibration workflow.

    Args:
        backend: Force a specific backend ('module', 'acpi_call', 'mock').

    Returns:
        AcpiCapabilities with what was detected.
    """
    controller = AcpiController(backend=backend)
    return controller.detect()


def read_dgpu_acpi_path(sysfs_dir: Path = GIGAMATE_ACPI_SYSFS) -> Optional[str]:
    """Read the discrete GPU ACPI pathname cached/reported by gigamate_acpi."""
    path = sysfs_dir / "dgpu_acpi_path"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        return text or None
    except OSError:
        return None


def acpi_eval_backend(
    sysfs_dir: Path = GIGAMATE_ACPI_SYSFS,
    proc_acpi_call: Path = PROC_ACPI_CALL,
) -> str:
    """Return which ACPI method evaluation backend is available ('module', 'acpi_call', or 'none')."""
    if (sysfs_dir / "acpi_eval").exists():
        return "module"
    if proc_acpi_call.exists():
        return "acpi_call"
    return "none"
