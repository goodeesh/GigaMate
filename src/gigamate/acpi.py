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
import subprocess
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
    backend: str = "none"  # "module", "acpi_call", "mock", "none"


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


class AcpiCallBackend(AcpiBackend):
    """Backend using the acpi_call kernel module via /proc/acpi/call.

    Requires the acpi_call (or acpi_call-dkms) kernel module loaded.
    """

    def detect(self) -> AcpiCapabilities:
        caps = AcpiCapabilities(backend="acpi_call")
        if not PROC_ACPI_CALL.exists():
            return caps
        if not os.access(str(PROC_ACPI_CALL), os.W_OK):
            return caps

        # Probe sensors
        if self._wmbc_read(0xE1) is not None:
            caps.has_temperature = True
        if self._wmbc_read(0xE4) is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if self._wmbc_read(0xE5) is not None:
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if self._wmbc_read(0x46) is not None:
            caps.has_fan_duty = True

        # Probe power profiles
        result = self._wmbd_write(0xED, 0)
        if result is not None:
            caps.has_power_profiles = True

        return caps

    def read_state(self) -> FanState:
        state = FanState()
        state.temp_cpu = self._wmbc_read(0xE1)
        state.temp_socket = self._wmbc_read(0xE2)
        state.fan1_rpm = self._wmbc_read(0xE4)
        state.fan2_rpm = self._wmbc_read(0xE5)
        state.duty_total = self._wmbc_read(0x50)
        state.duty_cpu = self._wmbc_read(0x46)
        state.duty_gpu = self._wmbc_read(0x47)
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

    def _wmbc_read(self, cmd: int) -> Optional[int]:
        """Call WMBC via /proc/acpi/call and parse result."""
        try:
            cmd_str = f"WMBC {cmd} 0"
            PROC_ACPI_CALL.write_text(cmd_str)
            result = PROC_ACPI_CALL.read_text().strip()
            return int(result)
        except (OSError, ValueError, IOError):
            return None

    def _wmbd_write(self, cmd: int, val: int) -> Optional[int]:
        """Call WMBD via /proc/acpi/call and parse result."""
        try:
            cmd_str = f"WMBD {cmd} {val}"
            PROC_ACPI_CALL.write_text(cmd_str)
            result = PROC_ACPI_CALL.read_text().strip()
            return int(result)
        except (OSError, ValueError, IOError):
            return None


# ──────────────────────────────────────────────
# Kernel module backend (sysfs)
# ──────────────────────────────────────────────


GIGAMATE_ACPI_SYSFS = Path("/sys/devices/platform/gigamate_acpi")


class ModuleBackend(AcpiBackend):
    """Backend using our own gigamate_acpi kernel module via sysfs.

    This is the preferred backend — fastest, most reliable.
    """

    def detect(self) -> AcpiCapabilities:
        caps = AcpiCapabilities(backend="module")
        if not GIGAMATE_ACPI_SYSFS.is_dir():
            return AcpiCapabilities(backend="none")

        # Check which sysfs files exist
        if (GIGAMATE_ACPI_SYSFS / "temp1_input").exists():
            caps.has_temperature = True
        if (GIGAMATE_ACPI_SYSFS / "fan1_input").exists():
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if (GIGAMATE_ACPI_SYSFS / "fan2_input").exists():
            caps.has_fan_rpm = True
            caps.fan_count += 1
        if (GIGAMATE_ACPI_SYSFS / "pwm1").exists():
            caps.has_fan_duty = True
        if (GIGAMATE_ACPI_SYSFS / "profile").exists():
            caps.has_power_profiles = True

        return caps

    def read_state(self) -> FanState:
        state = FanState()
        state.temp_cpu = self._read_sysfs_int("temp1_input")
        state.temp_socket = self._read_sysfs_int("temp2_input")
        state.fan1_rpm = self._read_sysfs_int("fan1_input")
        state.fan2_rpm = self._read_sysfs_int("fan2_input")
        state.duty_total = self._read_sysfs_int("pwm1_total")
        state.duty_cpu = self._read_sysfs_int("pwm1")
        state.duty_gpu = self._read_sysfs_int("pwm2")
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
            self._backend = ModuleBackend()
        elif backend == "acpi_call":
            self._backend = AcpiCallBackend()
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
            return

        # Fall back to acpi_call
        acpi_call_backend = AcpiCallBackend()
        caps = acpi_call_backend.detect()
        if caps.backend != "none":
            self._backend = acpi_call_backend
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
