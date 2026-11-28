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
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

SYSTEM_TO_GIGAMATE_PROFILE: Dict[str, int] = {
    "power-saver": 0,
    "balanced": 1,
    "performance": 2,
}


TLP_HANDOFF_PATH = Path("/etc/tlp.d/99-gigamate.conf")
POWER_HANDOFF_BACKUP = Path.home() / ".config/gigamate/power_handoff_backup.json"

_POWERDEVIL_AC_SECTIONS = (
    "[AC][Performance]",
    "[Battery][Performance]",
    "[LowBattery][Performance]",
    "[AC]",
    "[Battery]",
)


def _read_tlp_effective_config(
    tlp_conf: Path = Path("/etc/tlp.conf"),
    tlp_dir: Path = Path("/etc/tlp.d"),
) -> Dict[str, str]:
    """Read /etc/tlp.conf then sorted /etc/tlp.d/*.conf so later drop-ins override."""
    config: Dict[str, str] = {}
    files: List[Path] = []
    try:
        if tlp_conf.is_file():
            files.append(tlp_conf)
    except OSError:
        pass
    try:
        if tlp_dir.is_dir():
            files.extend(sorted(tlp_dir.glob("*.conf"), key=lambda p: p.name))
    except OSError:
        pass

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                val = val[1:-1]
            if key:
                config[key] = val
    return config


def _detect_powerdevil_ac_profiles(rc_path: Optional[Path] = None) -> Dict[str, str]:
    """Read ~/.config/powerdevilrc and return PowerProfile= entries under AC/Battery sections."""
    rc = Path(rc_path) if rc_path is not None else (Path.home() / ".config/powerdevilrc")
    found: Dict[str, str] = {}
    try:
        if not rc.is_file():
            return found
        section = ""
        for raw_line in rc.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line
            elif line.startswith("PowerProfile=") and section in _POWERDEVIL_AC_SECTIONS:
                found[section] = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return found


def power_config_mtime_token() -> Tuple[Tuple[str, int], ...]:
    """Return lightweight (path, mtime_ns) tuple for TLP and PowerDevil config paths."""
    paths = (
        Path("/etc/tlp.conf"),
        Path("/etc/tlp.d"),
        Path.home() / ".config/powerdevilrc",
    )
    tokens: List[Tuple[str, int]] = []
    for p in paths:
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            mtime = 0
        tokens.append((str(p), mtime))
    return tuple(tokens)


def has_handoff_to_restore() -> bool:
    """Return True if a TLP handoff drop-in or user backup exists to restore."""
    return bool(TLP_HANDOFF_PATH.exists() or POWER_HANDOFF_BACKUP.exists())


def detect_power_managers() -> Dict[str, Any]:
    """Detect active Linux power managers and external AC/battery profile switching."""
    managers: List[str] = []
    conflicts: List[Dict[str, str]] = []

    if shutil.which("systemctl"):
        services = (
            "tlp.service",
            "tlp-pd.service",
            "power-profiles-daemon.service",
            "tuned.service",
            "auto-cpufreq.service",
        )
        for svc in services:
            try:
                res = subprocess.run(
                    ["systemctl", "is-active", "--quiet", svc],
                    timeout=2,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    managers.append(svc)
            except Exception:
                pass

    tlp_active = "tlp.service" in managers or "tlp-pd.service" in managers
    tlp_summary = ""
    if tlp_active:
        tlp_cfg = _read_tlp_effective_config()
        auto_sw = tlp_cfg.get("TLP_AUTO_SWITCH", "2")
        ac_p = tlp_cfg.get("PLATFORM_PROFILE_ON_AC", "performance")
        bat_p = tlp_cfg.get("PLATFORM_PROFILE_ON_BAT", "balanced")
        if auto_sw != "0":
            conflicts.append({
                "id": "tlp",
                "name": "TLP",
                "scope": "system",
                "detail": f"TLP_AUTO_SWITCH={auto_sw} (AC→{ac_p}, Battery→{bat_p})",
            })
            tlp_summary = f"TLP: AC → {ac_p}, Battery → {bat_p}"
        else:
            tlp_summary = "TLP: tuning backend active (auto-switch paused by GigaMate)"

    pd_profiles = _detect_powerdevil_ac_profiles()
    if pd_profiles:
        managers.append("powerdevil")
        detail = ", ".join(f"{k}={v}" for k, v in pd_profiles.items())
        conflicts.append({
            "id": "powerdevil",
            "name": "KDE PowerDevil",
            "scope": "user",
            "detail": detail,
        })

    if "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() and shutil.which("gsettings"):
        try:
            res = subprocess.run(
                [
                    "gsettings",
                    "get",
                    "org.gnome.settings-daemon.plugins.power",
                    "power-saver-profile-on-low-battery",
                ],
                timeout=2,
                check=False,
                capture_output=True,
                text=True,
            )
            if res.returncode == 0 and res.stdout.strip().lower() == "true":
                conflicts.append({
                    "id": "gnome_power",
                    "name": "GNOME Power",
                    "scope": "user",
                    "detail": "power-saver-profile-on-low-battery=true",
                })
        except Exception:
            pass

    if "auto-cpufreq.service" in managers:
        conflicts.append({
            "id": "auto_cpufreq",
            "name": "auto-cpufreq",
            "scope": "system",
            "detail": "auto-cpufreq.service is active",
        })

    has_external_ac_switching = bool(conflicts)
    recommended_mode = "follow_system" if conflicts else "switch"
    if managers:
        summary = f"Detected: {', '.join(managers)}"
    else:
        summary = "No external power manager detected"

    return {
        "managers": managers,
        "conflicts": conflicts,
        "has_external_ac_switching": has_external_ac_switching,
        "recommended_mode": recommended_mode,
        "summary": summary,
        "tlp_summary": tlp_summary,
        "tlp_handoff_installed": bool(TLP_HANDOFF_PATH.exists()),
        "has_user_backup": bool(POWER_HANDOFF_BACKUP.exists()),
    }


def _reload_powerdevil() -> None:
    """Ask KDE PowerDevil to reload configuration if qdbus6/qdbus is available."""
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return
    try:
        subprocess.run(
            [qdbus, "org.kde.Solid.PowerManagement", "/org/kde/Solid/PowerManagement", "reparseConfiguration"],
            timeout=2,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def deactivate_ac_conflicts(
    conflicts: Optional[List[Dict[str, str]]] = None,
) -> Tuple[bool, str]:
    """Deactivate conflicting external AC switchers reversibly, backing up user settings."""
    if conflicts is None:
        conflicts = detect_power_managers().get("conflicts", [])
    if not conflicts:
        return True, "No conflicting AC switchers active."

    conflict_ids = {c["id"] for c in conflicts}
    backup: Dict[str, Any] = {}
    if POWER_HANDOFF_BACKUP.exists():
        try:
            backup = json.loads(POWER_HANDOFF_BACKUP.read_text(encoding="utf-8"))
        except Exception:
            backup = {}

    actions_done: List[str] = []

    if "powerdevil" in conflict_ids:
        rc_path = Path.home() / ".config/powerdevilrc"
        if rc_path.is_file():
            lines = rc_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            new_lines: List[str] = []
            section = ""
            pd_backup: Dict[str, str] = backup.get("powerdevil", {})
            for raw_line in lines:
                stripped = raw_line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped
                    new_lines.append(raw_line)
                elif stripped.startswith("PowerProfile=") and section in _POWERDEVIL_AC_SECTIONS:
                    pd_backup[section] = stripped.split("=", 1)[1].strip()
                else:
                    new_lines.append(raw_line)
            rc_path.write_text("".join(new_lines), encoding="utf-8")
            if pd_backup:
                backup["powerdevil"] = pd_backup
            _reload_powerdevil()
            actions_done.append("KDE PowerDevil")

    if "gnome_power" in conflict_ids and shutil.which("gsettings"):
        res = subprocess.run(
            [
                "gsettings",
                "set",
                "org.gnome.settings-daemon.plugins.power",
                "power-saver-profile-on-low-battery",
                "false",
            ],
            timeout=5,
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return False, f"Failed to update GNOME power setting: {res.stderr.strip()}"
        backup["gnome_power_saver_on_low_battery"] = True
        actions_done.append("GNOME Power")

    sys_cmds: List[str] = []
    if "tlp" in conflict_ids:
        sys_cmds.append(
            "mkdir -p /etc/tlp.d && "
            "printf '# Managed by GigaMate — GigaMate drives AC/Battery switching via tlp-pd\\nTLP_AUTO_SWITCH=0\\n' "
            f"> {TLP_HANDOFF_PATH} && "
            "(systemctl is-active --quiet tlp-pd.service && systemctl restart tlp-pd.service || systemctl reload tlp.service || true)"
        )
        actions_done.append("TLP")
    if "auto_cpufreq" in conflict_ids:
        sys_cmds.append("systemctl disable --now auto-cpufreq.service")
        backup["auto_cpufreq_disabled"] = True
        actions_done.append("auto-cpufreq")

    if sys_cmds:
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return False, "pkexec not found; cannot apply system-level power handoff."
        try:
            res = subprocess.run(
                [pkexec, "/bin/sh", "-c", " && ".join(sys_cmds)],
                timeout=60,
                check=False,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return False, "Privilege prompt timed out."
        if res.returncode != 0:
            err = res.stderr.strip() or f"pkexec exited with code {res.returncode}"
            return False, err

    if backup:
        POWER_HANDOFF_BACKUP.parent.mkdir(parents=True, exist_ok=True)
        POWER_HANDOFF_BACKUP.write_text(json.dumps(backup, indent=2), encoding="utf-8")

    return True, f"Deactivated AC switching for: {', '.join(actions_done)}"


def restore_ac_conflicts() -> Tuple[bool, str]:
    """Restore previously deactivated external AC switchers from backup and drop-in."""
    backup: Dict[str, Any] = {}
    if POWER_HANDOFF_BACKUP.exists():
        try:
            backup = json.loads(POWER_HANDOFF_BACKUP.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Failed to read backup file: {exc}"

    restored: List[str] = []

    pd_backup: Dict[str, str] = backup.get("powerdevil", {})
    if pd_backup:
        rc_path = Path.home() / ".config/powerdevilrc"
        if rc_path.is_file():
            lines = rc_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
        else:
            rc_path.parent.mkdir(parents=True, exist_ok=True)
            lines = []

        remaining = dict(pd_backup)
        new_lines: List[str] = []
        section = ""
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if section in remaining:
                    new_lines.append(f"PowerProfile={remaining.pop(section)}\n")
                section = stripped
                new_lines.append(raw_line)
            else:
                new_lines.append(raw_line)
        if section in remaining:
            new_lines.append(f"PowerProfile={remaining.pop(section)}\n")
        for sec, val in remaining.items():
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"{sec}\nPowerProfile={val}\n")

        rc_path.write_text("".join(new_lines), encoding="utf-8")
        _reload_powerdevil()
        restored.append("KDE PowerDevil")

    if backup.get("gnome_power_saver_on_low_battery") and shutil.which("gsettings"):
        res = subprocess.run(
            [
                "gsettings",
                "set",
                "org.gnome.settings-daemon.plugins.power",
                "power-saver-profile-on-low-battery",
                "true",
            ],
            timeout=5,
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return False, f"Failed to restore GNOME power setting: {res.stderr.strip()}"
        restored.append("GNOME Power")

    sys_cmds: List[str] = []
    if TLP_HANDOFF_PATH.exists():
        sys_cmds.append(
            f"rm -f {TLP_HANDOFF_PATH} && "
            "(systemctl is-active --quiet tlp-pd.service && systemctl restart tlp-pd.service || systemctl reload tlp.service || true)"
        )
        restored.append("TLP")
    if backup.get("auto_cpufreq_disabled"):
        sys_cmds.append("systemctl enable --now auto-cpufreq.service")
        restored.append("auto-cpufreq")

    if sys_cmds:
        pkexec = shutil.which("pkexec")
        if not pkexec:
            return False, "pkexec not found; cannot restore system-level power settings."
        try:
            res = subprocess.run(
                [pkexec, "/bin/sh", "-c", " && ".join(sys_cmds)],
                timeout=60,
                check=False,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return False, "Privilege prompt timed out."
        if res.returncode != 0:
            err = res.stderr.strip() or f"pkexec exited with code {res.returncode}"
            return False, err

    if POWER_HANDOFF_BACKUP.exists():
        try:
            POWER_HANDOFF_BACKUP.unlink()
        except OSError:
            pass

    if not restored:
        return True, "Nothing to restore."
    return True, f"Restored: {', '.join(restored)}"


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
        try:
            target = self._profile_map.get(int(fan_profile_id))
        except (TypeError, ValueError):
            return False
        if not target:
            return False
        return self.set_system_profile(target)


# Global helper instance
_manager = SystemPowerManager()


def sync_system_power(fan_profile_id: int) -> bool:
    """Convenience function to sync system power profile from fan profile ID."""
    sys_ok = _manager.sync_from_fan_profile(fan_profile_id)
    try:
        from gigamate.gpu import sync_gpu_power
        sync_gpu_power(fan_profile_id)
    except Exception:
        pass
    return sys_ok


def is_system_power_available() -> bool:
    """Check if system power management is available."""
    return _manager.is_available
