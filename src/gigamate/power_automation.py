"""GigaMate — Smart Power Automation Engine.

Automates profile switching, display refresh rate adjustments, and RGB dimming
based on AC mains connection state:
- On AC (plugged in): Switches to AC profile (Balanced/Gaming), restores maximum
  display refresh rate (e.g. 165Hz/240Hz), and restores full RGB keyboard lighting.
- On Battery (unplugged): Switches to Battery profile (Quiet), lowers internal
  display refresh rate to 60Hz (to save significant panel wattage), and dims RGB.

Supports multi-desktop environments: KDE Plasma (kscreen-doctor), Hyprland (hyprctl),
and X11 (xrandr) with graceful degradation when unsupported.
"""

from dataclasses import dataclass
import logging
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from .battery import BatteryManager, get_battery_manager
from .config import load as load_config, save as save_config
from .acpi import AcpiController, FanProfile

logger = logging.getLogger(__name__)


@dataclass
class DisplayMode:
    id: str
    resolution: str  # e.g. "2560x1600"
    rate: float  # e.g. 165.0, 60.0


@dataclass
class DisplayPanel:
    name: str  # e.g. "eDP-1"
    connected: bool
    enabled: bool
    modes: List[DisplayMode]


class DisplayManager:
    """Detects and controls display refresh rates across Linux desktop environments."""

    def __init__(self) -> None:
        self._kscreen_doctor = shutil.which("kscreen-doctor")
        self._hyprctl = shutil.which("hyprctl")
        self._xrandr = shutil.which("xrandr")

    def _get_kde_panels(self) -> List[DisplayPanel]:
        """Query display panels using kscreen-doctor."""
        if not self._kscreen_doctor:
            return []

        panels: List[DisplayPanel] = []
        try:
            out = subprocess.check_output([self._kscreen_doctor, "-o"], text=True, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            return []

        blocks = out.split("Output: ")
        for block in blocks[1:]:
            lines = block.splitlines()
            if not lines:
                continue
            header = lines[0].strip().split()
            if len(header) < 2:
                continue
            name = header[1]
            connected = any("connected" in line for line in lines[:8])
            enabled = any("enabled" in line for line in lines[:8])

            modes: List[DisplayMode] = []
            for line in lines:
                if "Modes:" in line:
                    for mid, res, rate in re.findall(r"(\d+):(\d+x\d+)@([\d.]+)", line):
                        try:
                            modes.append(DisplayMode(id=mid, resolution=res, rate=float(rate)))
                        except ValueError:
                            pass
            panels.append(DisplayPanel(name=name, connected=connected, enabled=enabled, modes=modes))

        return panels

    def find_internal_panel(self) -> Optional[DisplayPanel]:
        """Find the internal laptop display panel (eDP, LVDS, etc.)."""
        # 1. Check KDE panels
        for p in self._get_kde_panels():
            if p.connected and (p.name.startswith("eDP") or p.name.startswith("LVDS")):
                return p

        # Fallback to any connected eDP panel name
        return None

    def set_refresh_rate(self, target_hz: int, max_rate: bool = False) -> bool:
        """Set the internal display refresh rate.

        Args:
            target_hz: Desired refresh rate (e.g. 60).
            max_rate: If True, select the highest available refresh rate.

        Returns:
            True if applied successfully, False otherwise.
        """
        panel = self.find_internal_panel()
        if not panel or not panel.modes:
            logger.debug("No supported internal display panel detected for refresh rate switching.")
            return False

        # Identify native resolution (resolution with highest number of pixels)
        def _res_pixels(res: str) -> int:
            parts = res.split("x")
            return int(parts[0]) * int(parts[1]) if len(parts) == 2 else 0

        native_res = max((m.resolution for m in panel.modes), key=_res_pixels, default="")
        native_modes = [m for m in panel.modes if m.resolution == native_res]
        if not native_modes:
            native_modes = panel.modes

        target_mode: Optional[DisplayMode] = None
        if max_rate:
            target_mode = max(native_modes, key=lambda m: m.rate)
        else:
            # Find mode closest to target_hz (e.g. 60Hz)
            target_mode = min(native_modes, key=lambda m: abs(m.rate - target_hz))

        if not target_mode:
            return False

        logger.info(f"Switching display {panel.name} to {target_mode.resolution}@{target_mode.rate:.1f}Hz (Mode {target_mode.id})")

        # Apply via kscreen-doctor
        if self._kscreen_doctor:
            try:
                cmd = [self._kscreen_doctor, f"output.{panel.name}.mode.{target_mode.id}"]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning(f"kscreen-doctor command failed: {exc}")

        # Apply via hyprctl
        if self._hyprctl and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            try:
                rate_str = f"{target_mode.rate:.0f}"
                cmd = [self._hyprctl, "keyword", "monitor", f"{panel.name},preferred,auto,1,vrr,0,{rate_str}"]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning(f"hyprctl command failed: {exc}")

        # Apply via xrandr
        if self._xrandr and os.environ.get("DISPLAY"):
            try:
                cmd = [self._xrandr, "--output", panel.name, "--rate", str(int(target_mode.rate))]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning(f"xrandr command failed: {exc}")

        return False


class PowerAutomationEngine:
    """Coordinates automatic actions when switching between AC power and Battery."""

    def __init__(
        self,
        battery_mgr: Optional[BatteryManager] = None,
        acpi_ctrl: Optional[AcpiController] = None,
        display_mgr: Optional[DisplayManager] = None,
    ) -> None:
        self.battery_mgr = battery_mgr or get_battery_manager()
        self.acpi_ctrl = acpi_ctrl or AcpiController()
        self.display_mgr = display_mgr or DisplayManager()
        self._last_ac_state: Optional[bool] = None

    def poll(self) -> Optional[bool]:
        """Poll AC state and trigger automations if state has changed.

        Returns:
            True if transitioned to AC, False if transitioned to Battery, None if unchanged.
        """
        current_ac = self.battery_mgr.is_ac_online()
        if self._last_ac_state is None:
            self._last_ac_state = current_ac
            # Apply initial charge limit if configured
            cfg = load_config()
            if cfg.get("charge_limit_enabled", True) and self.battery_mgr.is_charge_limit_supported():
                limit = cfg.get("charge_limit", 80)
                try:
                    self.battery_mgr.set_charge_limit(limit)
                except Exception as e:
                    logger.debug(f"Could not apply startup charge limit: {e}")
            return None

        if current_ac != self._last_ac_state:
            self._last_ac_state = current_ac
            self.on_power_changed(current_ac)
            return current_ac

        return None

    def on_power_changed(self, is_ac: bool) -> None:
        """Execute automated profile, display, and RGB changes on power transition."""
        cfg = load_config()
        if not cfg.get("power_automation_enabled", True):
            return

        if is_ac:
            logger.info("Power source changed: AC connected")
            # 1. Switch to AC power profile
            ac_profile_id = cfg.get("ac_profile", 1)  # Default Balanced
            if self.acpi_ctrl.available:
                try:
                    self.acpi_ctrl.set_profile(ac_profile_id)
                except Exception as exc:
                    logger.warning(f"Failed to set AC profile: {exc}")

            # 2. Restore maximum display refresh rate
            if cfg.get("display_refresh_auto", True):
                self.display_mgr.set_refresh_rate(60, max_rate=True)

            # 3. Apply charge limit if configured
            if cfg.get("charge_limit_enabled", True) and self.battery_mgr.is_charge_limit_supported():
                limit = cfg.get("charge_limit", 80)
                try:
                    self.battery_mgr.set_charge_limit(limit)
                except Exception:
                    pass

        else:
            logger.info("Power source changed: On Battery")
            # 1. Switch to Battery power profile (Quiet)
            bat_profile_id = cfg.get("battery_profile", 0)  # Default Quiet
            if self.acpi_ctrl.available:
                try:
                    self.acpi_ctrl.set_profile(bat_profile_id)
                except Exception as exc:
                    logger.warning(f"Failed to set Battery profile: {exc}")

            # 2. Set internal display to 60Hz
            if cfg.get("display_refresh_auto", True):
                target_rate = cfg.get("battery_refresh_rate", 60)
                self.display_mgr.set_refresh_rate(target_rate, max_rate=False)


_default_engine: Optional[PowerAutomationEngine] = None


def get_power_automation_engine() -> PowerAutomationEngine:
    """Singleton getter for default PowerAutomationEngine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = PowerAutomationEngine()
    return _default_engine
