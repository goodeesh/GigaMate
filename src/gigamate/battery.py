"""GigaMate — Battery Care & Charging Threshold Management.

Provides hardware detection and control for laptop battery charge thresholds
(e.g. capping maximum charge at 80% to preserve battery lifespan), battery
health diagnostics, and AC connection status monitoring.

Supports:
1. GigaMate ACPI sysfs interface:
   /sys/devices/platform/gigamate_acpi/charge_limit
2. Standard Linux power supply charge control:
   /sys/class/power_supply/BAT*/charge_control_end_threshold
3. AC/Mains online status:
   /sys/class/power_supply/AC*/online
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

POWER_SUPPLY_SYSFS = Path("/sys/class/power_supply")
GIGAMATE_ACPI_SYSFS = Path("/sys/devices/platform/gigamate_acpi")


@dataclass
class BatteryInfo:
    """Snapshot of laptop battery status, health, and charge limits."""

    present: bool = False
    name: str = "BAT0"
    status: str = "Unknown"  # "Charging", "Discharging", "Full", "Not charging"
    capacity: int = 0  # 0..100 %
    health_percent: Optional[float] = None  # charge_full / charge_full_design * 100
    cycle_count: Optional[int] = None
    charge_now: Optional[int] = None  # µAh or µWh
    charge_full: Optional[int] = None
    charge_full_design: Optional[int] = None
    ac_online: bool = False
    charge_limit: Optional[int] = None  # Active charge limit (e.g. 80)
    charge_limit_supported: bool = False
    backend: str = "none"  # "gigamate_acpi", "sysfs", "none"


class BatteryManager:
    """Detects and controls battery charging thresholds and monitoring."""

    def __init__(
        self,
        power_supply_dir: Optional[Path] = None,
        acpi_sysfs_dir: Optional[Path] = None,
    ) -> None:
        self._power_supply_dir = power_supply_dir or POWER_SUPPLY_SYSFS
        self._acpi_sysfs_dir = acpi_sysfs_dir or GIGAMATE_ACPI_SYSFS
        self._battery_path: Optional[Path] = None
        self._ac_path: Optional[Path] = None
        self._detect_paths()

    def _detect_paths(self) -> None:
        """Discover active battery and AC adapter directories in sysfs."""
        if not self._power_supply_dir.exists():
            return

        # Find first primary battery (BAT0, BAT1, etc.)
        for path in sorted(self._power_supply_dir.glob("BAT*")):
            if path.is_dir() and (path / "type").exists():
                try:
                    if (path / "type").read_text().strip().lower() == "battery":
                        self._battery_path = path
                        break
                except (OSError, PermissionError):
                    continue

        # If no BAT* found, search any power supply with type Battery
        if not self._battery_path:
            for path in sorted(self._power_supply_dir.iterdir()):
                if path.is_dir() and (path / "type").exists():
                    try:
                        if (path / "type").read_text().strip().lower() == "battery":
                            self._battery_path = path
                            break
                    except (OSError, PermissionError):
                        continue

        # Find AC / Mains adapter (AC, ACAD, ADP1, etc.)
        for path in sorted(self._power_supply_dir.iterdir()):
            if path.is_dir() and (path / "type").exists():
                try:
                    t = (path / "type").read_text().strip().lower()
                    if t in ("mains", "ac"):
                        self._ac_path = path
                        break
                except (OSError, PermissionError):
                    continue

    @property
    def battery_path(self) -> Optional[Path]:
        return self._battery_path

    @property
    def is_available(self) -> bool:
        """Whether a battery is detected on this system."""
        if self._battery_path is None:
            return False

        present_file = self._battery_path / "present"
        if present_file.exists():
            try:
                return present_file.read_text().strip() != "0"
            except (OSError, PermissionError):
                pass

        # Some batteries omit the `present` attribute; the discovered device
        # directory itself is sufficient evidence that a battery exists.
        return True

    def is_ac_online(self) -> bool:
        """Returns True if AC adapter is connected and delivering power."""
        if self._ac_path:
            online_file = self._ac_path / "online"
            if online_file.exists():
                try:
                    return online_file.read_text().strip() == "1"
                except (OSError, PermissionError):
                    pass

        # Fallback: check all power supplies with online == 1
        if self._power_supply_dir.exists():
            for p in self._power_supply_dir.glob("*/online"):
                try:
                    if p.read_text().strip() == "1":
                        return True
                except (OSError, PermissionError):
                    continue
        return False

    def is_charge_limit_supported(self) -> bool:
        """Whether setting battery charge limit is supported on this hardware."""
        # 1. GigaMate ACPI sysfs module attribute
        if (self._acpi_sysfs_dir / "charge_limit").exists():
            return True

        # 2. Linux standard kernel charge_control_end_threshold
        if self._battery_path and (self._battery_path / "charge_control_end_threshold").exists():
            return True

        return False

    def get_charge_limit(self) -> Optional[int]:
        """Read the currently configured charge limit (e.g. 80), or None if unsupported."""
        # 1. Check GigaMate ACPI module
        acpi_file = self._acpi_sysfs_dir / "charge_limit"
        if acpi_file.exists():
            try:
                val = int(acpi_file.read_text().strip())
                if 0 < val <= 100:
                    return val
            except (ValueError, OSError):
                pass

        # 2. Check standard kernel attribute
        if self._battery_path:
            std_file = self._battery_path / "charge_control_end_threshold"
            if std_file.exists():
                try:
                    val = int(std_file.read_text().strip())
                    if 0 < val <= 100:
                        return val
                except (ValueError, OSError):
                    pass

        return None

    def set_charge_limit(self, limit: int) -> bool:
        """Set maximum battery charge limit percentage (40..100, where 100 is standard/unlimited).

        Returns True on success, False otherwise.
        """
        if limit != 0 and (limit < 40 or limit > 100):
            raise ValueError(f"Charge limit must be between 40 and 100 percent (got {limit})")

        # 1. Try GigaMate ACPI module
        acpi_file = self._acpi_sysfs_dir / "charge_limit"
        if acpi_file.exists():
            try:
                acpi_file.write_text(f"{limit}\n")
                return True
            except (OSError, PermissionError) as exc:
                logger.warning(f"Failed to write to {acpi_file}: {exc}")

        # 2. Try standard sysfs attribute
        if self._battery_path:
            std_file = self._battery_path / "charge_control_end_threshold"
            if std_file.exists():
                try:
                    std_file.write_text(f"{limit}\n")
                    return True
                except (OSError, PermissionError) as exc:
                    logger.warning(f"Failed to write to {std_file}: {exc}")

        return False

    def get_battery_info(self) -> BatteryInfo:
        """Return comprehensive snapshot of battery status and health."""
        if not self.is_available or not self._battery_path:
            return BatteryInfo(
                present=False,
                ac_online=self.is_ac_online(),
                charge_limit_supported=self.is_charge_limit_supported(),
            )

        info = BatteryInfo(
            present=True,
            name=self._battery_path.name,
            ac_online=self.is_ac_online(),
            charge_limit=self.get_charge_limit(),
            charge_limit_supported=self.is_charge_limit_supported(),
        )

        if (self._acpi_sysfs_dir / "charge_limit").exists():
            info.backend = "gigamate_acpi"
        elif (self._battery_path / "charge_control_end_threshold").exists():
            info.backend = "sysfs"

        def _read_int(filename: str) -> Optional[int]:
            p = self._battery_path / filename
            if p.exists():
                try:
                    return int(p.read_text().strip())
                except (ValueError, OSError):
                    pass
            return None

        def _read_str(filename: str) -> Optional[str]:
            p = self._battery_path / filename
            if p.exists():
                try:
                    return p.read_text().strip()
                except OSError:
                    pass
            return None

        info.capacity = _read_int("capacity") or 0
        info.status = _read_str("status") or "Unknown"
        info.cycle_count = _read_int("cycle_count")

        # Health calculation: charge_full vs charge_full_design or energy_full vs energy_full_design
        charge_now = _read_int("charge_now") or _read_int("energy_now")
        charge_full = _read_int("charge_full") or _read_int("energy_full")
        charge_full_design = _read_int("charge_full_design") or _read_int("energy_full_design")

        info.charge_now = charge_now
        info.charge_full = charge_full
        info.charge_full_design = charge_full_design

        if charge_full and charge_full_design and charge_full_design > 0:
            health = (charge_full / charge_full_design) * 100.0
            info.health_percent = round(min(100.0, max(0.0, health)), 1)

        return info


_default_manager: Optional[BatteryManager] = None


def get_battery_manager() -> BatteryManager:
    """Singleton getter for default BatteryManager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = BatteryManager()
    return _default_manager
