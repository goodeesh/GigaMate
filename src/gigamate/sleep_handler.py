"""GigaMate — Suspend / Resume Clean State Handler.

Subscribes to systemd-logind's PrepareForSleep D-Bus signal:
- Before Suspend (going_to_sleep=True):
  - Gracefully turns off keyboard RGB lighting to prevent stuck LEDs.
  - Pauses idle timer threads.
- After Resume (going_to_sleep=False):
  - Re-applies the user's saved profile, RGB colour/brightness and battery
    charge limit via the unified hardware settings layer.
  - Re-synchronizes dGPU power features (Dynamic Boost / SmartShift).

The D-Bus signal is delivered on a dedicated listener thread, but the
suspend/resume hooks belong to the (single-threaded) desktop UI, so they are
marshalled back to the main loop before running.
"""

import logging
import threading
import time
from typing import Callable, Optional

from .config import load as load_config, resolve_active_profile
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
        self._subscription_id = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def set_hooks(
        self,
        on_suspend: Optional[Callable[[], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
    ) -> None:
        """Attach suspend/resume callbacks (used by the tray to pause idle monitoring)."""
        self._on_suspend_hook = on_suspend
        self._on_resume_hook = on_resume

    def start_listening(self) -> bool:
        """Start listening for PrepareForSleep signals in a background thread.

        Returns True only if a system bus is actually reachable, so callers can
        trust the return value.
        """
        with self._lock:
            if self._listening:
                return True
            # Never start a second worker over a wedged one.
            if self._listener_thread is not None and self._listener_thread.is_alive():
                return False
            # Reserve the slot before the (slower) preflight so two concurrent
            # callers cannot both start a listener.
            self._listening = True

        try:
            from gi.repository import Gio
        except ImportError:
            logger.debug("PyGObject Gio unavailable; sleep handling disabled.")
            self._listening = False
            return False

        # One-time preflight so a transient bus failure is reported honestly
        # instead of being discovered later in the listener thread.
        try:
            Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception as exc:
            logger.warning(f"Could not connect to the system bus for sleep handling: {exc}")
            self._listening = False
            return False

        with self._lock:
            self._stop_event.clear()
            self._listener_thread = threading.Thread(
                target=self._run_dbus_listener,
                daemon=True,
                name="GigaMateSleepListener",
            )
            self._listener_thread.start()
        return True

    def stop_listening(self) -> None:
        """Quit the D-Bus main loop and join the listener thread (best effort)."""
        self._stop_event.set()
        with self._lock:
            loop = self._loop
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass

        thread = self._listener_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        with self._lock:
            self._listening = False
            if thread is None or not thread.is_alive():
                self._listener_thread = None
                self._loop = None

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
                with self._lock:
                    self._loop = loop

                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                sub_id = bus.signal_subscribe(
                    "org.freedesktop.login1",
                    "org.freedesktop.login1.Manager",
                    "PrepareForSleep",
                    "/org/freedesktop/login1",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_gio_signal,
                    None,
                )
                with self._lock:
                    self._subscription_id = sub_id

                # A stop may have raced in before the loop was published.
                if self._stop_event.is_set():
                    loop.quit()
                loop.run()

                try:
                    bus.signal_unsubscribe(sub_id)
                except Exception:
                    pass
            finally:
                context.pop_thread_default()
        except Exception as exc:
            logger.warning(f"Could not establish D-Bus sleep listener: {exc}")
        finally:
            with self._lock:
                self._listening = False
                self._subscription_id = None
                self._loop = None

    def _dispatch_hook(self, hook: Optional[Callable[[], None]]) -> None:
        """Run a UI hook on the main loop; call directly when already on it."""
        if hook is None:
            return
        if threading.current_thread() is threading.main_thread():
            hook()
            return
        try:
            from gi.repository import GLib

            GLib.idle_add(hook)
        except Exception:
            hook()

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
            # Only touch the keyboard when the user opted into startup control;
            # otherwise we would change a backlight we do not manage.
            try:
                cfg = load_config()
            except Exception:
                cfg = {}
            if cfg.get("startup_apply", False):
                try:
                    dev = get_keyboard()
                    if dev is not None:
                        profile = resolve_active_profile()
                        set_off(dev, profile)
                except Exception as exc:
                    logger.warning(f"Could not turn off RGB on sleep: {exc}")

            try:
                self._dispatch_hook(self._on_suspend_hook)
            except Exception as exc:
                logger.warning(f"Suspend hook failed: {exc}")

        else:
            logger.info("System resumed from sleep: restoring state...")
            # Allow kernel and USB devices 500ms to re-enumerate
            time.sleep(0.5)

            try:
                # apply_hardware_settings honours the opt-in flags
                # (startup_apply / charge_limit_enabled / sync_system_power).
                apply_hardware_settings()
            except Exception as exc:
                logger.warning(f"Could not restore state via apply_hardware_settings: {exc}")

            try:
                self._dispatch_hook(self._on_resume_hook)
            except Exception as exc:
                logger.warning(f"Resume hook failed: {exc}")


_default_sleep_handler: Optional[SleepHandler] = None


def get_sleep_handler() -> SleepHandler:
    """Singleton getter for default SleepHandler."""
    global _default_sleep_handler
    if _default_sleep_handler is None:
        _default_sleep_handler = SleepHandler()
    return _default_sleep_handler
