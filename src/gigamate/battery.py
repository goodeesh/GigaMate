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
        self._battery_paths: list = []
        if not self._power_supply_dir.exists():
            return

        def _type_of(path: Path) -> Optional[str]:
            type_file = path / "type"
            if type_file.exists():
                try:
                    return type_file.read_text().strip().lower()
                except (OSError, PermissionError):
                    return None
            return None

        try:
            entries = sorted(self._power_supply_dir.iterdir())
        except (OSError, PermissionError):
            return

        batteries = []
        for path in entries:
            if path.is_dir() and _type_of(path) == "battery":
                batteries.append(path)

        self._battery_paths = batteries

        # Prefer the first battery that explicitly reports itself present,
        # so a pack on BAT1 is still found when BAT0 reports present=0.
        for path in batteries:
            present_file = path / "present"
            if present_file.exists():
                try:
                    if present_file.read_text().strip() == "0":
                        continue
                except (OSError, PermissionError):
                    pass
            self._battery_path = path
            break
        else:
            if batteries:
                self._battery_path = batteries[0]

        # Find AC / Mains adapter (AC, ACAD, ADP1, USB-C PD, etc.)
        for path in entries:
            if path.is_dir() and _type_of(path) in ("mains", "ac", "usb", "usb_pd"):
                self._ac_path = path
                break

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

        # Fallback: check mains-like supplies for online == 1 (ignore unrelated
        # online supplies such as USB gadgets or peripherals).
        if self._power_supply_dir.exists():
            for p in self._power_supply_dir.glob("*/online"):
                supply = p.parent
                type_file = supply / "type"
                try:
                    stype = type_file.read_text().strip().lower() if type_file.exists() else ""
                except (OSError, PermissionError):
                    continue
                if stype not in ("mains", "ac", "usb", "usb_pd"):
                    continue
                try:
                    if p.read_text().strip() == "1":
                        return True
                except (OSError, PermissionError):
                    continue
        return False

    def is_charge_limit_supported(self) -> bool:
        """Whether this process can actually set a battery charge limit.

        The kernel module always creates the ``charge_limit`` attribute, so we
        must read it (``-ENODATA`` means the EC is unsupported). For the
        standard kernel attribute we also require write access — on most
        non-Gigabyte laptops it is root-only, so we report unsupported rather
        than offering a control that will fail on apply.
        """
        # 1. GigaMate ACPI sysfs module attribute
        acpi_file = self._acpi_sysfs_dir / "charge_limit"
        if acpi_file.exists():
            try:
                val = int(acpi_file.read_text().strip())
                return 0 < val <= 100 and os.access(str(acpi_file), os.W_OK)
            except (OSError, ValueError):
                return False

        # 2. Linux standard kernel charge_control_end_threshold
        if self._battery_path:
            std_file = self._battery_path / "charge_control_end_threshold"
            if std_file.exists() and os.access(str(std_file), os.W_OK):
                try:
                    int(std_file.read_text().strip())
                    return True
                except (OSError, ValueError):
                    return False

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
        if limit is None or not (40 <= int(limit) <= 100):
            raise ValueError(f"Charge limit must be between 40 and 100 percent (got {limit})")
        limit = int(limit)

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
