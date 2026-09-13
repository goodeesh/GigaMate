"""GigaMate — Suspend / Resume Clean State Handler.

Subscribes to systemd-logind's PrepareForSleep D-Bus signal:
- Before Suspend (going_to_sleep=True):
  - Gracefully turns off keyboard RGB lighting to prevent stuck LEDs.
  - Pauses idle timer threads.
- After Resume (going_to_sleep=False):
  - Re-evaluates AC vs Battery state (in case charger was removed during sleep).
  - Enforces appropriate profile (Quiet on Battery, Balanced on AC).
  - Restores keyboard RGB to configured brightness and colour.
  - Re-synchronizes dGPU power features (Dynamic Boost / SmartShift).
"""

import logging
import threading
import time
from typing import Callable, Optional

from .battery import get_battery_manager
from .config import load as load_config, resolve_active_profile
from .power_automation import get_power_automation_engine
from .protocol import get_keyboard, set_off, set_static
from .gpu import sync_gpu_power

logger = logging.getLogger(__name__)


class SleepHandler:
    """Listens for system sleep/wake events and handles hardware state transitions."""

    def __init__(
        self,
        on_suspend_hook: Optional[Callable[[], None]] = None,
        on_resume_hook: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_suspend_hook = on_suspend_hook
        self._on_resume_hook = on_resume_hook
        self._listening = False
        self._listener_thread: Optional[threading.Thread] = None

    def start_listening(self) -> bool:
        """Start listening for PrepareForSleep signals in background thread."""
        if self._listening:
            return True

        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
        except ImportError:
            try:
                from gi.repository import Gio, GLib
            except ImportError:
                logger.debug("Neither dbus-python nor PyGObject Gio available for sleep handling.")
                return False

        self._listening = True
        self._listener_thread = threading.Thread(
            target=self._run_dbus_listener,
            daemon=True,
            name="GigaMateSleepListener",
        )
        self._listener_thread.start()
        return True

    def _run_dbus_listener(self) -> None:
        """Background loop subscribing to org.freedesktop.login1 PrepareForSleep."""
        try:
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.signal_subscribe(
                "org.freedesktop.login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                "/org/freedesktop/login1",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_gio_signal,
                None,
            )
            loop = GLib.MainLoop()
            loop.run()
        except Exception as exc:
            logger.debug(f"Gio sleep listener failed ({exc}), trying dbus-python...")
            try:
                import dbus
                from dbus.mainloop.glib import DBusGMainLoop
                from gi.repository import GLib

                DBusGMainLoop(set_as_default=True)
                system_bus = dbus.SystemBus()
                system_bus.add_signal_receiver(
                    self.on_prepare_for_sleep,
                    signal_name="PrepareForSleep",
                    dbus_interface="org.freedesktop.login1.Manager",
                )
                loop = GLib.MainLoop()
                loop.run()
            except Exception as e2:
                logger.warning(f"Could not establish D-Bus sleep listener: {e2}")

    def _on_gio_signal(self, connection, sender_name, object_path, interface_name, signal_name, parameters, user_data) -> None:
        """Gio signal callback unpacker."""
        try:
            going_to_sleep = parameters.unpack()[0]
            self.on_prepare_for_sleep(bool(going_to_sleep))
        except Exception as exc:
            logger.warning(f"Error handling Gio sleep signal: {exc}")

    def on_prepare_for_sleep(self, going_to_sleep: bool) -> None:
        """Dispatch clean hardware transitions before sleep and after wake."""
        if going_to_sleep:
            logger.info("System entering sleep: turning off RGB and cleaning state...")
            try:
                # 1. Turn off RGB
                dev = get_keyboard()
                if dev is not None:
                    profile = resolve_active_profile()
                    set_off(dev, profile)
            except Exception as exc:
                logger.warning(f"Could not turn off RGB on sleep: {exc}")

            if self._on_suspend_hook:
                try:
                    self._on_suspend_hook()
                except Exception as exc:
                    logger.warning(f"Suspend hook failed: {exc}")

        else:
            logger.info("System resumed from sleep: restoring state...")
            # Allow kernel and USB devices 500ms to re-enumerate
            time.sleep(0.5)

            try:
                # 1. Re-evaluate AC power and auto-profile
                auto_engine = get_power_automation_engine()
                is_ac = auto_engine.battery_mgr.is_ac_online()
                auto_engine.on_power_changed(is_ac)

                # 2. Restore RGB lighting
                cfg = load_config()
                dev = get_keyboard()
                if dev is not None:
                    profile = resolve_active_profile()
                    brightness = cfg.get("brightness", 2)
                    colour = cfg.get("colour", "light_purple")
                    if brightness == 0:
                        set_off(dev, profile)
                    else:
                        set_static(dev, colour, brightness, profile)

                # 3. Sync discrete GPU dynamic boost if active
                acpi_profile = cfg.get("acpi_profile", 1)
                sync_gpu_power(acpi_profile)

            except Exception as exc:
                logger.warning(f"Could not restore state on resume: {exc}")

            if self._on_resume_hook:
                try:
                    self._on_resume_hook()
                except Exception as exc:
                    logger.warning(f"Resume hook failed: {exc}")


_default_sleep_handler: Optional[SleepHandler] = None


def get_sleep_handler() -> SleepHandler:
    """Singleton getter for default SleepHandler."""
    global _default_sleep_handler
    if _default_sleep_handler is None:
        _default_sleep_handler = SleepHandler()
    return _default_sleep_handler
