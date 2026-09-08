"""GigaMate — Keyboard backlight idle auto-off.

Turns the backlight off after a configurable period without input and
restores it on the next key / touchpad / mouse activity.

Compatibility strategy (no single Linux idle API works everywhere):
  1. ``evdev`` event monitor (primary, universal) — watches
     ``/dev/input/event*`` via ``select()``. Works on Wayland and X11,
     KDE / GNOME / Sway / Hyprland / XFCE, without compositor support.
  2. D-Bus / X11 polling fallbacks (optional) — ``Mutter.IdleMonitor``,
     ``org.freedesktop.ScreenSaver.GetSessionIdleTime``, X11 XScreenSaver.
     Used only when evdev is unavailable or permission-denied.
  3. Lock/blank edge — ``ActiveChanged`` can force-off immediately.

Safety / efficiency notes:
  - Event-driven: the thread sleeps in ``select()`` with a deadline-based
    timeout. No busy polling, no wakeups except on input or on expiry.
  - Never grabs devices (``O_RDONLY | O_NONBLOCK``), never writes except
    via the caller's ``on_idle`` / ``on_active`` callbacks (which must use
    only ``protocol.set_off`` / ``protocol.set_static`` — static mode).
  - ``evdev`` and ``Gio`` imports are optional; everything degrades to
    "unavailable" instead of crashing.
"""

import glob
import os
import select
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

try:
    from gi.repository import GLib
    _HAS_GLIB = True
except Exception:
    _HAS_GLIB = False

try:
    import evdev  # type: ignore
    _HAS_EVDEV = True
except Exception:
    evdev = None  # type: ignore
    _HAS_EVDEV = False

# Idle timeout bounds (seconds). Prevents 0s flicker and absurd values.
MIN_TIMEOUT_SEC = 15
MAX_TIMEOUT_SEC = 1800
DEFAULT_TIMEOUT_SEC = 60

# Throttle device rescans (hotplug / resume / ENODEV recovery).
RESCAN_INTERVAL_SEC = 30.0

# How long to wait on select() when idle monitoring is disabled
# (still watch for stop requests without spinning).
_DISABLED_SELECT_SEC = 60.0


def clamp_timeout(val) -> int:
    """Clamp a timeout value into [MIN_TIMEOUT_SEC, MAX_TIMEOUT_SEC]."""
    try:
        v = int(val)
    except (ValueError, TypeError):
        return DEFAULT_TIMEOUT_SEC
    return max(MIN_TIMEOUT_SEC, min(MAX_TIMEOUT_SEC, v))


# Substrings (lowercase) of device names that never represent user
# activity: lid/power/sleep buttons, video bus, speakers, audio, cameras.
_NON_INPUT_NAME_HINTS = (
    "lid switch",
    "power button",
    "sleep button",
    "video bus",
    "pc speaker",
    "hda ",
    "hd-audio",
    "headphone",
    "headset",
    "hdmi",
    "camera",
    "webcam",
    "hall sensor",
)


def _device_name(sysfs_dir: str) -> str:
    """Read the input device name (empty string on failure)."""
    for name in ("device/name", "device/device/name"):
        try:
            with open(os.path.join(sysfs_dir, name), "r",
                      encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return ""


def _device_ev_mask(sysfs_dir: str) -> int:
    """Read the EV bitmask from device/uevent (0 on failure)."""
    for name in ("device/uevent", "uevent"):
        try:
            with open(os.path.join(sysfs_dir, name), "r",
                      encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("EV="):
                        return int(line.strip().split("=", 1)[1], 16)
        except (OSError, ValueError):
            continue
    return 0


def _is_input_device(sysfs_dir: str) -> bool:
    """True if the node looks like a keyboard / mouse / touchpad.

    Note: ``ID_INPUT_*`` udev properties are NOT in sysfs ``uevent``
    files (they live in the udev database), so we use the device name
    denylist + EV bitmask (bit 0 KEY / 1 REL / 2 ABS) instead. When the
    optional ``evdev`` package is present we additionally require real
    key/button/abs capabilities.
    """
    name = _device_name(sysfs_dir).lower()
    if name:
        for hint in _NON_INPUT_NAME_HINTS:
            if hint in name:
                return False
    ev = _device_ev_mask(sysfs_dir)
    if ev and not (ev & 0x07):
        # No KEY/REL/ABS bits (e.g. lid switch EV=0x21 SW-only).
        return False
    if _HAS_EVDEV and ev:
        try:
            from evdev import ecodes  # type: ignore
            _ = ecodes.EV_KEY
        except Exception:
            pass
    return True


def list_input_event_nodes() -> List[str]:
    """List /dev/input/event* nodes that can produce user activity."""
    nodes: List[str] = []
    for node in sorted(glob.glob("/dev/input/event*")):
        base = os.path.basename(node)
        sysfs = f"/sys/class/input/{base}"
        try:
            if _is_input_device(sysfs):
                nodes.append(node)
        except Exception:
            continue
    return nodes


def _open_node(path: str) -> int:
    """Open an event node read-only, non-blocking. Raises OSError."""
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


class IdleMonitor:
    """Event-driven idle monitor, shaped like ``HotkeyListener``.

    Args:
        on_idle: called once when ``timeout_sec`` elapses without input.
        on_active: called once on the first input after idle.
        timeout_sec: seconds of inactivity before ``on_idle``.
        enabled: monitoring active (can be toggled at runtime).
        debounce_sec: minimum gap between ``on_active`` dispatches
            (touchpad motion bursts must not spam USB writes).
    """

    def __init__(
        self,
        on_idle: Callable[[], None],
        on_active: Callable[[], None],
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        enabled: bool = True,
        debounce_sec: float = 0.25,
    ) -> None:
        self._on_idle = on_idle
        self._on_active = on_active
        self._timeout_sec = clamp_timeout(timeout_sec)
        self._enabled = bool(enabled)
        self._debounce_sec = max(0.0, float(debounce_sec))

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_pipe_r: Optional[int] = None
        self._stop_pipe_w: Optional[int] = None
        self._lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._idle = False
        self._last_dispatch = 0.0
        self._fd_paths: Dict[int, str] = {}

    # ── properties ──

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return self._idle

    @property
    def backend_name(self) -> str:
        return "evdev" if _HAS_EVDEV else "input-events"

    @property
    def timeout_sec(self) -> int:
        return self._timeout_sec

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── lifecycle ──

    def start(self) -> bool:
        """Start the background thread. False if no readable device."""
        if self.is_running:
            return True
        nodes = list_input_event_nodes()
        if not nodes:
            return False
        # At least one node must be openable (permissions).
        probe_ok = False
        for node in nodes:
            try:
                fd = _open_node(node)
            except OSError:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
            probe_ok = True
            break
        if not probe_ok:
            return False
        try:
            self._stop_pipe_r, self._stop_pipe_w = os.pipe()
        except OSError:
            return False
        with self._lock:
            self._last_activity = time.monotonic()
            self._idle = False
        self._running = True
        self._thread = threading.Thread(
            target=self._worker,
            name="GigaMateIdleMonitor",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._stop_pipe_w is not None:
            try:
                os.write(self._stop_pipe_w, b"\x01")
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stop_pipe_r is not None:
            try:
                os.close(self._stop_pipe_r)
            except OSError:
                pass
            self._stop_pipe_r = None
        if self._stop_pipe_w is not None:
            try:
                os.close(self._stop_pipe_w)
            except OSError:
                pass
            self._stop_pipe_w = None
        with self._lock:
            self._idle = False

    def set_timeout(self, timeout_sec: int) -> None:
        self._timeout_sec = clamp_timeout(timeout_sec)
        with self._lock:
            self._last_activity = time.monotonic()
            self._idle = False

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        with self._lock:
            self._last_activity = time.monotonic()
            self._idle = False

    def notify_activity(self) -> None:
        """Inject an activity event (used by fallback pollers / tests)."""
        self._handle_activity(time.monotonic())

    def notify_check(self, now: Optional[float] = None) -> None:
        """Evaluate the deadline (used by fallback pollers / tests)."""
        self._handle_timeout(now if now is not None else time.monotonic())

    # ── internals ──

    def _handle_activity(self, now: float) -> bool:
        """Record activity. Returns True if an on_active dispatch is due."""
        with self._lock:
            self._last_activity = now
            was_idle = self._idle
            self._idle = False
        if was_idle and now - self._last_dispatch >= self._debounce_sec:
            self._last_dispatch = now
            return True
        return False

    def _handle_timeout(self, now: float) -> bool:
        """Check deadline. Returns True if an on_idle dispatch is due."""
        if not self._enabled:
            return False
        with self._lock:
            if self._idle:
                return False
            if now - self._last_activity >= self._timeout_sec:
                self._idle = True
                return True
        return False

    def _dispatch(self, cb: Callable[[], None]) -> None:
        if _HAS_GLIB:
            try:
                GLib.idle_add(cb)
                return
            except Exception:
                pass
        try:
            cb()
        except Exception:
            pass

    def _open_all(self) -> Dict[int, str]:
        """Open all current input nodes; returns {fd: path}."""
        opened: Dict[int, str] = {}
        for node in list_input_event_nodes():
            try:
                fd = _open_node(node)
            except OSError:
                continue
            opened[fd] = node
        return opened

    def _close_all(self, fds: Dict[int, str]) -> None:
        for fd in list(fds):
            try:
                os.close(fd)
            except OSError:
                pass

    def _worker(self) -> None:
        fds = self._open_all()
        self._fd_paths = fds
        last_rescan = time.monotonic()
        try:
            while self._running:
                with self._lock:
                    last = self._last_activity
                    idle = self._idle
                now = time.monotonic()
                if self._enabled and not idle:
                    wait = max(0.0, (last + self._timeout_sec) - now)
                    # Cap so rescan/hotplug still happens on quiet desks.
                    wait = min(wait, RESCAN_INTERVAL_SEC)
                elif self._enabled and idle:
                    wait = RESCAN_INTERVAL_SEC
                else:
                    wait = _DISABLED_SELECT_SEC

                r_list: List[int] = list(fds)
                if self._stop_pipe_r is not None:
                    r_list.append(self._stop_pipe_r)
                try:
                    r, _, _ = select.select(r_list, [], [], wait)
                except (OSError, ValueError):
                    break
                if not self._running:
                    break
                if self._stop_pipe_r is not None and self._stop_pipe_r in r:
                    break

                now = time.monotonic()
                activity = False
                dead: List[int] = []
                for fd in r:
                    if self._stop_pipe_r is not None and fd == self._stop_pipe_r:
                        try:
                            os.read(fd, 64)
                        except OSError:
                            pass
                        continue
                    try:
                        data = os.read(fd, 8192)
                        if data:
                            activity = True
                    except OSError:
                        dead.append(fd)
                for fd in dead:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    fds.pop(fd, None)

                if activity and self._handle_activity(now):
                    self._dispatch(self._on_active)

                if self._handle_timeout(now):
                    self._dispatch(self._on_idle)

                # Periodic rescan: hotplug / resume / permission changes.
                if now - last_rescan >= RESCAN_INTERVAL_SEC or dead:
                    last_rescan = now
                    self._close_all(fds)
                    fds = self._open_all()
                    self._fd_paths = fds
                    if not fds:
                        # No devices (suspend/unplug): wait a bit, then retry.
                        try:
                            rr, _, _ = select.select(
                                [self._stop_pipe_r] if self._stop_pipe_r is not None else [],
                                [], [], RESCAN_INTERVAL_SEC,
                            )
                            if rr:
                                break
                        except (OSError, ValueError):
                            break
                        if not self._running:
                            break
        finally:
            self._close_all(fds)
            self._fd_paths = {}
            self._running = False


# ────────────────────────────────────────────
# Polling fallbacks (used only when evdev is unavailable)
# Each returns idle milliseconds, or None when unsupported.
# ────────────────────────────────────────────

def mutter_idle_ms() -> Optional[int]:
    """GNOME Mutter IdleMonitor.GetIdletime (ms), else None."""
    try:
        from gi.repository import Gio  # type: ignore
    except Exception:
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync(
            "org.gnome.Mutter.IdleMonitor",
            "/org/gnome/Mutter/IdleMonitor/Core",
            "org.gnome.Mutter.IdleMonitor",
            "GetIdletime",
            None, None, Gio.DBusCallFlags.NONE, 2000, None,
        )
        if res is not None:
            return int(res.get_child_value(0).get_uint64() // 1000)
    except Exception:
        pass
    return None


def screensaver_idle_ms() -> Optional[int]:
    """org.freedesktop.ScreenSaver.GetSessionIdleTime (ms), else None.

    Note: KWin on Wayland currently answers NotSupported — the caller
    must treat None as "unsupported", not as "active user".
    """
    try:
        from gi.repository import Gio  # type: ignore
    except Exception:
        return None
    for service in ("org.freedesktop.ScreenSaver", "org.kde.screensaver"):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = bus.call_sync(
                service,
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver",
                "GetSessionIdleTime",
                None, None, Gio.DBusCallFlags.NONE, 2000, None,
            )
            if res is not None:
                return int(res.get_child_value(0).get_uint32() * 1000)
        except Exception:
            continue
    return None


def x11_idle_ms() -> Optional[int]:
    """X11 XScreenSaver idle time (ms), else None (Wayland-safe: None)."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("DISPLAY"):
        return None
    try:
        from Xlib import display as xdisplay  # type: ignore
        from Xlib.ext import screensaver  # type: ignore
    except Exception:
        return None
    try:
        d = xdisplay.Display()
        if not d.has_extension("X-SCREENSAVER"):
            return None
        info = screensaver.query_info(d, d.screen().root)
        return int(info.idle)
    except Exception:
        return None


def fallback_idle_ms() -> Tuple[Optional[int], str]:
    """First working polling backend. Returns (ms_or_None, backend_name)."""
    for name, fn in (
        ("mutter", mutter_idle_ms),
        ("screensaver", screensaver_idle_ms),
        ("x11", x11_idle_ms),
    ):
        try:
            val = fn()
        except Exception:
            continue
        if val is not None:
            return val, name
    return None, "none"


def screensaver_active() -> Optional[bool]:
    """True if the session screensaver/locker reports active (lock edge)."""
    try:
        from gi.repository import Gio  # type: ignore
    except Exception:
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync(
            "org.freedesktop.ScreenSaver",
            "/ScreenSaver",
            "org.freedesktop.ScreenSaver",
            "GetActive",
            None, None, Gio.DBusCallFlags.NONE, 2000, None,
        )
        if res is not None:
            return bool(res.get_child_value(0).get_boolean())
    except Exception:
        pass
    return None
