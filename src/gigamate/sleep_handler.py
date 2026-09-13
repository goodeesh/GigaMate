"""GigaMate — Suspend / Resume Clean State Handler.

Subscribes to systemd-logind's PrepareForSleep D-Bus signal:
- Before Suspend (going_to_sleep=True):
  - Gracefully turns off keyboard RGB lighting to prevent stuck LEDs.
  - Pauses idle timer threads.
- After Resume (going_to_sleep=False):
  - Re-applies the user's saved profile, RGB colour/brightness and battery
    charge limit via the unified hardware settings layer.
  - Re-synchronizes dGPU power features (Dynamic Boost / SmartShift).
"""

import logging
import threading
import time
from typing import Callable, Optional

from .config import resolve_active_profile
from .hardware import apply_hardware_settings
from .protocol import get_keyboard, set_off

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
        self._loop = None

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

    def stop_listening(self) -> None:
        """Quit the D-Bus main loop and join the listener thread (best effort)."""
        loop = self._loop
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass

        thread = self._listener_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        self._listener_thread = None
        self._loop = None
        self._listening = False

    def _run_dbus_listener(self) -> None:
        """Background loop subscribing to org.freedesktop.login1 PrepareForSleep."""
        try:
            from gi.repository import Gio, GLib

            # Use a dedicated main context owned by this thread instead of the
            # process-wide default context (which belongs to the GUI thread).
            context = GLib.MainContext.new()
            context.push_thread_default()
            try:
                loop = GLib.MainLoop.new(context, False)
                self._loop = loop
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
                loop.run()
            finally:
                context.pop_thread_default()
                self._loop = None
            return
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
            self._loop = loop
            loop.run()
        except Exception as e2:
            logger.warning(f"Could not establish D-Bus sleep listener: {e2}")
        finally:
            self._loop = None

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
                apply_hardware_settings()
            except Exception as exc:
                logger.warning(f"Could not restore state via apply_hardware_settings: {exc}")

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
