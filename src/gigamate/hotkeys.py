"""GigaMate — Hotkey Listener

Monitors Gigabyte laptop keyboard interfaces for hardware hotkeys
(e.g. F7 Performance Mode switch on Aero X16 / 0414:8105).
"""

import os
import glob
import time
import select
import threading
from typing import Callable, Optional

try:
    from gi.repository import GLib
    _HAS_GLIB = True
except Exception:
    _HAS_GLIB = False


def find_hotkey_hidraw(vid: Optional[int] = 0x0414, pid: Optional[int] = 0x8105, iface_num: int = 2) -> Optional[str]:
    """Find the /dev/hidraw path for the specified keyboard interface.

    Args:
        vid: Vendor ID (e.g. 0x0414)
        pid: Product ID (e.g. 0x8105)
        iface_num: Interface index (default 2 for consumer/vendor hotkey reports)

    Returns:
        Device path such as '/dev/hidraw2', or None if not found.
    """
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            dev_path = os.path.realpath(os.path.join(h, "device"))
            # Check VID/PID if provided
            if vid is not None and pid is not None:
                id_pattern = f"{vid:04x}:{pid:04x}".lower()
                if id_pattern not in dev_path.lower():
                    continue

            # Check interface number (e.g. :1.2/ or :1.2.)
            if f":1.{iface_num}/" in dev_path or f":1.{iface_num}." in dev_path:
                node = "/dev/" + os.path.basename(h)
                if os.path.exists(node):
                    return node
        except Exception:
            continue
    return None


class HotkeyListener:
    """Background listener for hardware hotkey events from Gigabyte keyboards."""

    def __init__(
        self,
        on_mode_switch: Callable[[], None],
        vid: Optional[int] = 0x0414,
        pid: Optional[int] = 0x8105,
        iface_num: int = 2,
        debounce_sec: float = 0.25,
    ) -> None:
        """Initialize hotkey listener.

        Args:
            on_mode_switch: Callback invoked when the mode switch hotkey is pressed.
            vid: Vendor ID.
            pid: Product ID.
            iface_num: USB interface number.
            debounce_sec: Minimum seconds between hotkey activations.
        """
        self._on_mode_switch = on_mode_switch
        self._vid = vid
        self._pid = pid
        self._iface_num = iface_num
        self._debounce_sec = debounce_sec

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_pipe_r: Optional[int] = None
        self._stop_pipe_w: Optional[int] = None
        self._last_trigger: float = 0.0

    @property
    def is_running(self) -> bool:
        """Whether listener thread is currently running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start listening in a background daemon thread.

        Returns:
            True if listener successfully started, False otherwise.
        """
        if self.is_running:
            return True

        device_path = find_hotkey_hidraw(self._vid, self._pid, self._iface_num)
        if not device_path:
            return False

        try:
            self._stop_pipe_r, self._stop_pipe_w = os.pipe()
        except OSError:
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._worker,
            args=(device_path,),
            name="GigaMateHotkeyListener",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop listening and clean up resources."""
        self._running = False
        if self._stop_pipe_w is not None:
            try:
                os.write(self._stop_pipe_w, b"\x01")
            except OSError:
                pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
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

    def _worker(self, device_path: str) -> None:
        """Worker thread loop reading from hidraw device."""
        fd = -1
        try:
            fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self._running = False
            return

        try:
            while self._running:
                r_fds = [fd]
                if self._stop_pipe_r is not None:
                    r_fds.append(self._stop_pipe_r)

                try:
                    r, _, _ = select.select(r_fds, [], [], 1.0)
                except (OSError, ValueError):
                    break

                if not self._running:
                    break

                if self._stop_pipe_r in r:
                    break

                if fd in r:
                    try:
                        data = os.read(fd, 64)
                    except OSError:
                        break

                    if not data:
                        continue

                    # Mode switch key report on 0414:8105 is Report ID 0x04, data 00 00 84
                    if len(data) >= 4 and data[0] == 0x04 and data[3] == 0x84:
                        now = time.monotonic()
                        if now - self._last_trigger >= self._debounce_sec:
                            self._last_trigger = now
                            self._dispatch_mode_switch()
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._running = False

    def _dispatch_mode_switch(self) -> None:
        """Safely invoke callback, delegating to GLib main loop if present."""
        if _HAS_GLIB:
            GLib.idle_add(self._on_mode_switch)
        else:
            try:
                self._on_mode_switch()
            except Exception:
                pass
