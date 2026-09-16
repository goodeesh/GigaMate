"""GigaMate — Hotkey Listener

Monitors Gigabyte laptop keyboard interfaces for hardware hotkeys
(e.g. the mode-switch and GigaMate keys on Aero X16 / 0414:8105).

Hotkey definitions are strictly model-scoped: they come from the device
profile matching the detected keyboard (``DeviceProfile.hotkeys``) or from
explicit user overrides in ``config.json``. This module carries **no**
baked-in report signatures, so unknown models are never affected.
"""

import glob
import logging
import os
import select
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from gi.repository import GLib
    _HAS_GLIB = True
except Exception:
    _HAS_GLIB = False


# Actions the listener can dispatch. Keep in sync with the ``hotkeys``
# schema in docs/PROFILE_SCHEMA.md.
ACTION_MODE_SWITCH = "mode_switch"
ACTION_OPEN_CENTER = "open_center"
KNOWN_ACTIONS = (ACTION_MODE_SWITCH, ACTION_OPEN_CENTER)


@dataclass(frozen=True)
class HotkeySpec:
    """One hardware hotkey: the expected HID report on a USB interface."""

    interface: int
    report_id: int
    payload: bytes
    action: str
    key_name: str = ""

    @classmethod
    def from_dict(cls, action: str, data: dict) -> "HotkeySpec":
        """Parse a ``hotkeys.<action>`` mapping; raises ValueError."""
        if not isinstance(data, dict):
            raise ValueError(f"hotkey '{action}' spec must be a mapping")
        try:
            interface = int(data["interface"])
            report_id = int(data["report_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"hotkey '{action}': interface/report_id must be integers") from exc
        if not 0 <= interface <= 255 or not 0 <= report_id <= 255:
            raise ValueError(
                f"hotkey '{action}': interface/report_id out of range (0-255)")
        payload_hex = str(data.get("payload", "")).strip().replace(" ", "")
        if not payload_hex or len(payload_hex) % 2:
            raise ValueError(
                f"hotkey '{action}': payload must be a non-empty even-length hex string")
        try:
            payload = bytes.fromhex(payload_hex)
        except ValueError as exc:
            raise ValueError(f"hotkey '{action}': payload is not valid hex") from exc
        key_name = data.get("key_name", action)
        if not isinstance(key_name, str) or not key_name.strip():
            key_name = action
        return cls(interface=interface, report_id=report_id,
                   payload=payload, action=str(action), key_name=key_name)

    def matches(self, data) -> bool:
        """True when a raw hidraw report matches this spec (prefix match)."""
        if not data or data[0] != self.report_id:
            return False
        return bytes(data[1:1 + len(self.payload)]) == self.payload


def specs_for_hotkeys(mapping) -> List[HotkeySpec]:
    """Parse a ``hotkeys`` action->spec mapping, skipping invalid entries."""
    specs: List[HotkeySpec] = []
    if not isinstance(mapping, dict):
        return specs
    for action, entry in mapping.items():
        try:
            specs.append(HotkeySpec.from_dict(str(action), entry))
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring invalid hotkey spec %r: %s", action, exc)
    return specs


def merge_hotkey_specs(*mappings) -> List[HotkeySpec]:
    """Merge hotkey spec mappings with first-wins precedence per action."""
    merged: Dict[str, dict] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        for action, entry in mapping.items():
            if action not in merged:
                merged[action] = entry
    return specs_for_hotkeys(merged)


def match_spec(specs: List[HotkeySpec], data, interface: Optional[int] = None) -> Optional[HotkeySpec]:
    """Return the first spec matching ``data``, or None."""
    for spec in specs:
        if interface is not None and spec.interface != interface:
            continue
        if spec.matches(data):
            return spec
    return None


def match_action(specs: List[HotkeySpec], data, interface: Optional[int] = None) -> Optional[str]:
    """Return the action of the first spec matching ``data``, or None."""
    spec = match_spec(specs, data, interface)
    return spec.action if spec is not None else None


def format_report(data) -> str:
    """Format a raw HID report as space-separated lowercase hex."""
    return " ".join(f"{b:02x}" for b in bytes(data))


def find_hotkey_hidraw(vid: Optional[int] = 0x0414, pid: Optional[int] = 0x8105,
                       iface_num: int = 2) -> Optional[str]:
    """Find the /dev/hidraw path for the specified keyboard interface.

    Args:
        vid: Vendor ID (e.g. 0x0414)
        pid: Product ID (e.g. 0x8105)
        iface_num: Interface index (default 2 for consumer/vendor hotkey reports)

    Returns:
        Device path such as '/dev/hidraw2', or None if not found.
    """
    for iface, node in list_hotkey_hidraw(vid, pid):
        if iface == iface_num:
            return node
    return None


def list_hotkey_hidraw(vid: Optional[int] = None,
                       pid: Optional[int] = None) -> List[Tuple[int, str]]:
    """List (interface, node) hidraw devices matching a keyboard.

    Used by the discovery tool (``gigamate hotkeys watch``) to enumerate every
    hotkey-relevant interface of a keyboard, not just a single one.
    """
    found: List[Tuple[int, str]] = []
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            dev_path = os.path.realpath(os.path.join(h, "device"))
            # Check VID/PID if provided
            if vid is not None and pid is not None:
                id_pattern = f"{vid:04x}:{pid:04x}".lower()
                if id_pattern not in dev_path.lower():
                    continue

            # Derive the USB interface number from ...:X.Y/ segments.
            if ":1." not in dev_path:
                continue
            tail = dev_path.split(":1.", 1)[1]
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if not digits:
                continue
            node = "/dev/" + os.path.basename(h)
            if os.path.exists(node):
                found.append((int(digits), node))
        except Exception:
            continue
    return found


class HotkeyListener:
    """Background listener for hardware hotkey events from Gigabyte keyboards."""

    def __init__(
        self,
        on_mode_switch: Optional[Callable[[], None]] = None,
        *,
        on_action: Optional[Callable[[str], None]] = None,
        vid: Optional[int] = 0x0414,
        pid: Optional[int] = 0x8105,
        specs: Optional[List[HotkeySpec]] = None,
        debounce_sec: float = 0.25,
    ) -> None:
        """Initialize hotkey listener.

        Args:
            on_mode_switch: Legacy callback invoked on the mode-switch action.
            on_action: Callback invoked with the action name for any spec.
            vid: Vendor ID.
            pid: Product ID.
            specs: Hotkey specs to watch (profile-driven). The listener
                refuses to start when empty, so unmapped models are inert.
            debounce_sec: Minimum seconds between activations of one action.
        """
        self._on_mode_switch = on_mode_switch
        self._on_action = on_action
        self._vid = vid
        self._pid = pid
        self._specs = list(specs) if specs else []
        self._debounce_sec = debounce_sec

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_pipe_r: Optional[int] = None
        self._stop_pipe_w: Optional[int] = None
        self._last_trigger: Dict[str, float] = {}

    @property
    def is_running(self) -> bool:
        """Whether listener thread is currently running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start listening in a background daemon thread.

        Returns:
            True if listener successfully started, False otherwise
            (no specs configured or no matching device).
        """
        if self.is_running:
            return True
        # Refuse to start a second worker over a wedged one.
        if self._thread is not None and self._thread.is_alive():
            return False
        if not self._specs:
            return False

        needed = sorted({spec.interface for spec in self._specs})
        targets: List[Tuple[int, str]] = []
        for iface in needed:
            device_path = find_hotkey_hidraw(self._vid, self._pid, iface)
            if device_path:
                targets.append((iface, device_path))
        if not targets:
            return False

        try:
            self._stop_pipe_r, self._stop_pipe_w = os.pipe()
        except OSError:
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._worker,
            args=(targets,),
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
        # Only tear down once the worker has actually stopped; a timed-out
        # worker may still be using the pipe fds.
        if self._thread is not None and not self._thread.is_alive():
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

    def _worker(self, targets: List[Tuple[int, str]]) -> None:
        """Worker thread loop reading from hidraw devices."""
        fds: Dict[int, int] = {}
        for iface, device_path in targets:
            try:
                fds[os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)] = iface
            except OSError:
                continue
        if not fds and self._stop_pipe_r is None:
            self._running = False
            return

        try:
            while self._running:
                r_fds = list(fds)
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

                for fd in r:
                    if fd not in fds:
                        continue
                    try:
                        data = os.read(fd, 64)
                    except OSError:
                        break

                    if not data:
                        continue

                    action = match_action(self._specs, data, fds[fd])
                    if action is not None:
                        self._dispatch(action)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._running = False

    def _dispatch(self, action: str) -> None:
        """Dispatch a matched action with per-action debouncing."""
        now = time.monotonic()
        if now - self._last_trigger.get(action, 0.0) < self._debounce_sec:
            return
        self._last_trigger[action] = now
        callbacks = []
        if action == ACTION_MODE_SWITCH and self._on_mode_switch is not None:
            callbacks.append(self._on_mode_switch)
        if self._on_action is not None:
            callbacks.append(lambda: self._on_action(action))
        for cb in callbacks:
            self._invoke(cb)

    @staticmethod
    def _invoke(cb: Callable[[], None]) -> None:
        """Safely invoke callback, delegating to GLib main loop if present."""
        if _HAS_GLIB:
            GLib.idle_add(cb)
        else:
            try:
                cb()
            except Exception:
                pass
