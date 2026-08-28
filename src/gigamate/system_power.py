"""GigaMate — System Power Profile Integration.

Integrates GigaMate power profiles (Quiet, Balanced, Performance, Gaming)
with the system-wide Linux power profile management (KDE Plasma, GNOME,
power-profiles-daemon, tlp-pd, tuned).

Standard mapping:
  - Quiet (0)       -> "power-saver"
  - Balanced (1)    -> "balanced"
  - Performance (2) -> "performance"
  - Gaming (3)      -> "performance"
"""

import os
import glob
from typing import Dict, List, Optional

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    _HAS_GIO = True
except Exception:
    _HAS_GIO = False


DEFAULT_PROFILE_MAP: Dict[int, str] = {
    0: "power-saver",
    1: "balanced",
    2: "performance",
    3: "performance",
}


class SystemPowerManager:
    """Manager for system-level power profile integration."""

    def __init__(self, profile_map: Optional[Dict[int, str]] = None) -> None:
        self._profile_map = dict(profile_map) if profile_map else dict(DEFAULT_PROFILE_MAP)

    @property
    def is_available(self) -> bool:
        """Check if system power management (net.hadess.PowerProfiles or sysfs) is available."""
        if _HAS_GIO:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                proxy = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                    None,
                    "net.hadess.PowerProfiles",
                    "/net/hadess/PowerProfiles",
                    "org.freedesktop.DBus.Properties",
                    None,
                )
                val = proxy.call_sync(
                    "Get",
                    GLib.Variant("(ss)", ("net.hadess.PowerProfiles", "ActiveProfile")),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                if val:
                    return True
            except Exception:
                pass

        # Check sysfs platform_profile or cpufreq EPP
        if os.path.exists("/sys/firmware/acpi/platform_profile"):
            return True

        return bool(glob.glob("/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference"))

    def get_active_profile(self) -> Optional[str]:
        """Get the current system power profile name."""
        if _HAS_GIO:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                proxy = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                    None,
                    "net.hadess.PowerProfiles",
                    "/net/hadess/PowerProfiles",
                    "org.freedesktop.DBus.Properties",
                    None,
                )
                val = proxy.call_sync(
                    "Get",
                    GLib.Variant("(ss)", ("net.hadess.PowerProfiles", "ActiveProfile")),
                    Gio.DBusCallFlags.NONE,
                    500,
                    None,
                )
                if val and len(val) > 0:
                    res = val[0]
                    if isinstance(res, str):
                        return res
                    if hasattr(res, "get_string"):
                        return res.get_string()
                    return str(res)
            except Exception:
                pass

        if os.path.exists("/sys/firmware/acpi/platform_profile"):
            try:
                return open("/sys/firmware/acpi/platform_profile").read().strip()
            except Exception:
                pass

        return None

    def set_system_profile(self, target_profile: str) -> bool:
        """Set the system power profile (e.g. 'power-saver', 'balanced', 'performance').

        Args:
            target_profile: 'power-saver', 'balanced', or 'performance'

        Returns:
            True on success, False otherwise.
        """
        # 1. Try standard DBus net.hadess.PowerProfiles (power-profiles-daemon, tlp-pd, tuned)
        if _HAS_GIO:
            try:
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                proxy = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    "net.hadess.PowerProfiles",
                    "/net/hadess/PowerProfiles",
                    "org.freedesktop.DBus.Properties",
                    None,
                )
                proxy.call_sync(
                    "Set",
                    GLib.Variant(
                        "(ssv)",
                        ("net.hadess.PowerProfiles", "ActiveProfile", GLib.Variant("s", target_profile)),
                    ),
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
                return True
            except Exception:
                pass

        # 2. Try sysfs platform_profile fallback if writable
        platform_profile_path = "/sys/firmware/acpi/platform_profile"
        if os.path.exists(platform_profile_path) and os.access(platform_profile_path, os.W_OK):
            # Mapping for platform_profile
            p_map = {
                "power-saver": "low-power",
                "balanced": "balanced",
                "performance": "performance",
            }
            mapped = p_map.get(target_profile, target_profile)
            try:
                with open(platform_profile_path, "w") as f:
                    f.write(mapped)
                return True
            except Exception:
                pass

        # 3. Try cpufreq energy_performance_preference fallback if writable
        epp_map = {
            "power-saver": "power",
            "balanced": "balance_performance",
            "performance": "performance",
        }
        epp_val = epp_map.get(target_profile)
        if epp_val:
            policies = glob.glob("/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference")
            success = False
            for p in policies:
                if os.access(p, os.W_OK):
                    try:
                        with open(p, "w") as f:
                            f.write(epp_val)
                        success = True
                    except Exception:
                        pass
            if success:
                return True

        return False

    def sync_from_fan_profile(self, fan_profile_id: int) -> bool:
        """Apply the system power profile corresponding to the given fan/ACPI profile.

        Args:
            fan_profile_id: 0 (Quiet), 1 (Balanced), 2 (Performance), 3 (Gaming)

        Returns:
            True if applied successfully, False otherwise.
        """
        target = self._profile_map.get(int(fan_profile_id))
        if not target:
            return False
        return self.set_system_profile(target)


# Global helper instance
_manager = SystemPowerManager()


def sync_system_power(fan_profile_id: int) -> bool:
    """Convenience function to sync system power profile from fan profile ID."""
    return _manager.sync_from_fan_profile(fan_profile_id)


def is_system_power_available() -> bool:
    """Check if system power management is available."""
    return _manager.is_available
