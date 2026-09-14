"""GigaMate — Unified Hardware State & Settings Reapplication Layer.

Ensures that user settings saved in config.json are safely and reliably applied
to physical hardware across all lifecycle events:
- Machine boot / user login (via systemd gigamate.service)
- Wake from suspend / hibernate / hybrid sleep (via sleep_handler PrepareForSleep)
- GigaMate Center application launch / reopen
- GigaMate Tray daemon restart / reload
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

from .config import DEFAULT_CONFIG, load as load_config, resolve_active_profile

logger = logging.getLogger(__name__)

# Reapplying hardware touches USB/ACPI/battery; serialize across the GUI thread
# (launch) and the sleep-listener thread (resume) to avoid concurrent writes.
_APPLY_LOCK = threading.Lock()


def apply_hardware_settings(
    cfg: Optional[Dict[str, Any]] = None,
    force_keyboard: bool = False,
) -> Dict[str, bool]:
    """Apply all persisted user settings to hardware with isolated error domains.

    If an individual subsystem fails (e.g. keyboard unplugged, or battery missing),
    the other subsystems continue to apply normally.

    Args:
        cfg: Configuration mapping; loaded from disk when omitted.
        force_keyboard: Apply keyboard lighting even when ``startup_apply`` is
            disabled (used to restore the backlight after resume).

    Returns:
        Dict[str, bool] indicating success/failure per subsystem:
        {"profile": bool, "battery": bool, "keyboard": bool}
    """
    with _APPLY_LOCK:
        return _apply_hardware_settings_locked(cfg, force_keyboard)


def _apply_hardware_settings_locked(
    cfg: Optional[Dict[str, Any]],
    force_keyboard: bool,
) -> Dict[str, bool]:
    if cfg is None:
        cfg = load_config()

    results = {
        "profile": False,
        "battery": False,
        "keyboard": False,
    }

    # ─────────────────────────────────────────────────────────────
    # 1. ACPI Fan/Power Profile & System Power Sync
    # ─────────────────────────────────────────────────────────────
    prof_val = cfg.get("acpi_profile")
    if prof_val is not None:
        from .acpi import AcpiController, FanProfile

        # Parse the persisted profile id in its own error domain so a bad value
        # cannot suppress the system/GPU power sync below.
        fp: Optional[FanProfile] = None
        try:
            fp = FanProfile(int(prof_val))
        except (TypeError, ValueError):
            logger.warning("Hardware sync: ignoring invalid acpi_profile %r", prof_val)

        if fp is not None:
            try:
                ctrl = AcpiController()
                if ctrl.available and ctrl.set_profile(fp):
                    results["profile"] = True
                    logger.info(f"Hardware sync: ACPI power profile set to {fp.name} ({fp.value})")
            except Exception as exc:
                logger.warning(f"Hardware sync failed for ACPI power profile: {exc}")

            # System power sync also synchronizes GPU power (Dynamic Boost /
            # SmartShift); honour the user's sync_system_power preference.
            try:
                if cfg.get("sync_system_power", DEFAULT_CONFIG["sync_system_power"]):
                    from .system_power import sync_system_power

                    sync_system_power(int(prof_val))
            except Exception as exc:
                logger.warning(f"Hardware sync failed for system power: {exc}")

    # ─────────────────────────────────────────────────────────────
    # 2. Battery Care & Charging Limit
    # ─────────────────────────────────────────────────────────────
    if cfg.get("charge_limit_enabled", DEFAULT_CONFIG["charge_limit_enabled"]):
        limit = cfg.get("charge_limit", DEFAULT_CONFIG["charge_limit"])
        try:
            from .battery import get_battery_manager

            battery_mgr = get_battery_manager()
            if battery_mgr.is_charge_limit_supported():
                if battery_mgr.set_charge_limit(int(limit)):
                    results["battery"] = True
                    logger.info(f"Hardware sync: Battery charge limit set to {limit}%")
        except Exception as exc:
            logger.warning(f"Hardware sync failed for battery charge limit: {exc}")

    # ─────────────────────────────────────────────────────────────
    # 3. Keyboard RGB Lighting
    # ─────────────────────────────────────────────────────────────
    if force_keyboard or cfg.get("startup_apply", DEFAULT_CONFIG["startup_apply"]):
        try:
            from .protocol import get_keyboard, set_off, set_static

            # On boot/resume, USB devices might need a brief moment to settle
            dev = get_keyboard()
            if dev is None:
                for _ in range(2):
                    time.sleep(0.3)
                    dev = get_keyboard()
                    if dev is not None:
                        break

            if dev is not None:
                profile = resolve_active_profile()
                brightness = int(cfg.get("brightness", DEFAULT_CONFIG["brightness"]))
                colour = cfg.get("colour", DEFAULT_CONFIG["colour"])

                if brightness == 0:
                    set_off(dev, profile)
                else:
                    if profile is not None and profile.colour_map and colour not in profile.colour_map:
                        colour = next(iter(profile.colour_map))
                    set_static(dev, colour, brightness, profile)
                results["keyboard"] = True
                logger.info(f"Hardware sync: Keyboard lighting set to {colour} (brightness {brightness})")
        except Exception as exc:
            logger.warning(f"Hardware sync failed for keyboard lighting: {exc}")

    return results
