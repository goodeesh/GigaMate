"""GigaMate — System Tray Application

The primary user interface for GigaMate. Provides:
- Keyboard RGB colour and brightness control
- Power profile switching (Quiet/Balanced/Performance/Gaming)
- Live system status (temperatures, fan speeds)
- Community contribution flow for model support
- Graceful degradation: RGB works without ACPI, ACPI works without RGB
"""

import sys
import os
import time
import signal
import threading
import random
from typing import Optional, Dict, List

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gio

try:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3
except Exception:
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator3
    except Exception:  # missing typelib — degrade to no tray indicator
        AppIndicator3 = None

from pathlib import Path

from .protocol import (
    detect_keyboards,
    hardware_supports_lamparray,
    get_keyboard,
    set_off,
    set_rgb,
    supports_full_rgb,
)
from .lamparray import LampArrayDevice, PALETTE_HEX
from .capabilities import get_dmi_product_name
from .power_limits import discover_acpi_profiles
from .config import (
    CONFIG_FILE,
    load as load_config,
    update_config,
    effective_brightness_pct,
    is_rgb_enabled,
    effective_rgb_hex,
)
from .acpi import (
    AcpiController, FanProfile, FanState, AcpiCapabilities,
)
from .hotkeys import HotkeyListener, resolve_hotkey_specs
from .gpu import gpu_icon_key
from .paths import ICON_PATHS, get_runtime_ipc_paths
from .idle import (
    DEFAULT_TIMEOUT_SEC,
    IDLE_STEP_OFF,
    IDLE_TIMEOUT_STEPS,
    IdleMonitor,
    clamp_timeout,
    fallback_idle_ms,
    idle_step_label as _idle_step_label,
    nearest_idle_step as _nearest_idle_step,
)
from .osd import show_profile_osd
from .system_power import (
    SYSTEM_TO_GIGAMATE_PROFILE,
    SystemPowerManager,
    detect_power_managers,
    is_system_power_available,
    power_config_mtime_token,
    sync_system_power,
)
from .gpu import get_gpu_state, gpu_short_status_text
import subprocess
import shutil
import logging
from .battery import (
    get_battery_manager,
    evaluate_calibration_state,
    start_topup,
    cancel_topup,
)
from .sleep_handler import get_sleep_handler
from . import hardware
from .hardware import apply_hardware_settings
from . import dgpu_gate
from . import updates as update_checker

logger = logging.getLogger(__name__)

APP_ID = "gigamate"
APP_ICON = "gigamate"
BRIGHTNESS_NAMES = ["Off", "Dim", "Full"]
STATUS_POLL_INTERVAL_MS = 5000  # 5 seconds on AC
STATUS_POLL_BATTERY_MS = 15000  # 15 seconds on battery
_ACPI_RETRY_SEC = 5.0  # throttle for re-probing the ACPI backend when absent
IDLE_FALLBACK_POLL_MS = 5000  # 5 seconds (only when evdev unavailable)
UPDATE_CHECK_INTERVAL_MS = 24 * 3600 * 1000  # daily; one tiny HTTPS req/day
APP_ICON_PATHS = ICON_PATHS

# Icon variants: plain + dGPU-awake dots (green NVIDIA, red AMD discrete).
# Distinct files (not runtime rewrites) so indicator hosts refresh reliably.
# Resolution lives in paths.py (gi-free, testable); tray aliases the names.
APP_ICON_PATH = ICON_PATHS["gigamate"]
APP_ICON_PATHS = ICON_PATHS


class GigaMateTrayApp:
    """System tray application for Gigabyte laptop management."""

    def __init__(self) -> None:
        self._config = load_config()
        self._indicator: Optional[AppIndicator3.Indicator] = None
        self._menu: Optional[Gtk.Menu] = None
        self._icon_key: Optional[str] = None

        # Keyboard state
        self._has_lamparray = False
        self._unsupported = False
        self._no_keyboard = False
        self._keyboard_retry_timer_id: Optional[int] = None
        self._detected_vid: Optional[int] = None
        self._detected_pid: Optional[int] = None

        # RGB state
        self._colour_items: Dict[str, Gtk.RadioMenuItem] = {}
        self._brightness_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._current_colour = self._config.get("colour", "light_purple")
        self._current_brightness = self._config.get("brightness", 2)
        self._current_rgb_hex = self._config.get("rgb_hex")
        self._current_brightness_pct = effective_brightness_pct(self._config)
        self._rgb_enabled = is_rgb_enabled(self._config)
        self._full_rgb = False
        self._startup_item: Optional[Gtk.CheckMenuItem] = None
        self._startup_apply = self._config.get("startup_apply", False)
        self._sync_system_power = self._config.get("sync_system_power", False)
        self._sync_power_item: Optional[Gtk.CheckMenuItem] = None
        self._battery_care_item: Optional[Gtk.CheckMenuItem] = None
        self._battery_parent_item: Optional[Gtk.MenuItem] = None
        self._battery_parent_label: Optional[str] = None
        self._battery_dirty = False
        self._last_topup_active = False

        # dGPU Power Gate state
        self._dgpu_power_mode = self._config.get("dgpu_power_mode", "optimus")
        self._dgpu_power_items: Dict[str, Gtk.RadioMenuItem] = {}
        self._dgpu_power_parent_item: Optional[Gtk.MenuItem] = None
        self._dgpu_power_parent_label: Optional[str] = None
        self._dgpu_power_status_item: Optional[Gtk.MenuItem] = None
        self._dgpu_power_status_label: Optional[str] = None
        self._dgpu_power_busy = False

        # ACPI state
        self._acpi_controller: Optional[AcpiController] = None
        self._acpi_caps: Optional[AcpiCapabilities] = None
        self._profile_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._status_items: List[Gtk.MenuItem] = []
        self._current_acpi_profile: Optional[int] = self._config.get("acpi_profile")
        self._status_timer_id: Optional[int] = None
        self._status_poll_ms: int = STATUS_POLL_INTERVAL_MS

        # Hotkey listener state
        self._hotkey_listener: Optional[HotkeyListener] = None

        # Keyboard idle auto-off state
        self._idle_enabled = bool(self._config.get("idle_off_enabled", False))
        self._idle_timeout = clamp_timeout(
            self._config.get("idle_timeout_sec", DEFAULT_TIMEOUT_SEC))
        self._idle_dimmed = False
        self._idle_monitor: Optional[IdleMonitor] = None
        self._last_ac_online: Optional[bool] = None
        self._last_seen_system_profile: Optional[str] = None
        self._profile_sync_until: float = 0.0
        self._idle_fallback_timer_id: Optional[int] = None
        self._idle_parent_item: Optional[Gtk.MenuItem] = None
        self._idle_timeout_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._idle_lock = threading.RLock()

        # Menu item references (for updating)
        self._reload_item: Optional[Gtk.MenuItem] = None

        # Update state (battery-efficient daily check, maintainer tags only)
        self._update_available = False
        self._latest_version: Optional[str] = None
        self._update_timer_id: Optional[int] = None

        self._building = True
        self._detect_on_startup()
        self._init_acpi()
        self._init_hotkeys()
        self._build_menu()
        self._building = False
        self._apply_on_startup()
        self._init_idle()
        self._init_sleep_handler()
        self._last_config_mtime = self._get_config_mtime()
        self._config_monitor = None
        self._init_config_monitor()
        self._start_status_polling()
        self._init_update_check()

    # ────────────────────────────────────────────
    # Initialisation
    # ────────────────────────────────────────────

    def _detect_on_startup(self) -> None:
        """Detect keyboard hardware via HID LampArray."""
        detected = detect_keyboards()
        if not detected:
            self._no_keyboard = True
            self._has_lamparray = False
            self._full_rgb = False
            self._unsupported = False
            return
        self._no_keyboard = False
        self._detected_vid, self._detected_pid = detected[0]
        self._has_lamparray = hardware_supports_lamparray(
            self._detected_vid, self._detected_pid
        )
        self._full_rgb = self._has_lamparray
        self._unsupported = not self._has_lamparray

    def _init_acpi(self) -> None:
        """Initialise ACPI controller (auto-detected)."""
        ctrl = AcpiController()
        if ctrl.available:
            self._acpi_controller = ctrl
            self._acpi_caps = ctrl.capabilities
        else:
            self._acpi_controller = None
            self._acpi_caps = None

    def _resolve_hotkey_specs(self):
        """Resolve hotkey specs using DEFAULT_GIGABYTE_HOTKEYS + config overrides."""
        try:
            return resolve_hotkey_specs(cfg=self._config)
        except Exception:
            return {}

    def _init_hotkeys(self) -> None:
        """Initialise and start background hotkey listener."""
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None

        vid = self._detected_vid or 0x0414
        pid = self._detected_pid or 0x8105

        specs = self._resolve_hotkey_specs()
        if not specs:
            return
        self._hotkey_listener = HotkeyListener(
            on_action=self._on_hotkey_action,
            vid=vid,
            pid=pid,
            specs=specs,
        )
        self._hotkey_listener.start()

    def _on_hotkey_action(self, action: str) -> None:
        """Dispatch a hardware hotkey action from the listener."""
        if action == "mode_switch":
            self._on_hotkey_cycle_profile()
        elif action == "open_center":
            self._activate_center()
        else:
            logger.warning("Unknown hotkey action: %s", action)

    def _get_config_mtime(self) -> float:
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def _init_config_monitor(self) -> None:
        """Watch config.json via Gio FileMonitor so changes from Center/CLI sync instantly."""
        try:
            gfile = Gio.File.new_for_path(str(CONFIG_FILE))
            self._config_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._config_monitor.connect("changed", self._on_config_file_event)
        except Exception:
            self._config_monitor = None

    def _on_config_file_event(self, _monitor, _file, _other_file, event_type) -> None:
        if event_type in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        ):
            self._sync_from_external_config()

    def _sync_from_external_config(self) -> None:
        """Reload configuration when changed externally by GigaMate Center or CLI."""
        mtime = self._get_config_mtime()
        if mtime <= getattr(self, "_last_config_mtime", 0.0):
            return
        self._last_config_mtime = mtime

        new_cfg = load_config()
        self._config = new_cfg

        new_colour = new_cfg.get("colour")
        new_bright = new_cfg.get("brightness")
        new_rgb_hex = new_cfg.get("rgb_hex")
        new_bright_pct = effective_brightness_pct(new_cfg)
        new_rgb_enabled = is_rgb_enabled(new_cfg)
        new_profile = new_cfg.get("acpi_profile")
        new_idle_off = new_cfg.get("idle_off_enabled", False)
        new_idle_sec = new_cfg.get("idle_timeout_sec", DEFAULT_TIMEOUT_SEC)
        new_startup = new_cfg.get("startup_apply", False)
        new_sync_power = new_cfg.get("sync_system_power", False)
        new_limit = new_cfg.get("charge_limit", 80)
        new_limit_enabled = new_cfg.get("charge_limit_enabled", False)
        new_dgpu_mode = new_cfg.get("dgpu_power_mode", "optimus")

        old_building = self._building
        self._building = True
        try:
            if new_colour and new_colour != self._current_colour:
                self._current_colour = new_colour
                if new_colour in self._colour_items:
                    self._colour_items[new_colour].set_active(True)

            if new_bright is not None and new_bright != self._current_brightness:
                self._current_brightness = new_bright
                if not getattr(self, "_full_rgb", False) and new_bright in self._brightness_items:
                    self._brightness_items[new_bright].set_active(True)

            self._current_rgb_hex = new_rgb_hex
            self._rgb_enabled = new_rgb_enabled
            if new_bright_pct != getattr(self, "_current_brightness_pct", None):
                self._current_brightness_pct = new_bright_pct
            if getattr(self, "_full_rgb", False):
                target_pct = 0 if not new_rgb_enabled else new_bright_pct
                if target_pct in self._brightness_items:
                    self._brightness_items[target_pct].set_active(True)

            if new_profile is not None and new_profile != self._current_acpi_profile:
                self._current_acpi_profile = new_profile
                if new_sync_power:
                    self._mark_profile_synced(new_profile)
                if new_profile in self._profile_items:
                    self._profile_items[new_profile].set_active(True)

            if (new_idle_off != self._idle_enabled) or (new_idle_sec != self._idle_timeout):
                self._idle_enabled = new_idle_off
                self._idle_timeout = clamp_timeout(new_idle_sec)
                self._update_idle_parent_label()
                eff = self._idle_effective_step()
                if eff in self._idle_timeout_items:
                    self._idle_timeout_items[eff].set_active(True)
                self._init_idle()

            if new_startup != self._startup_apply:
                self._startup_apply = new_startup
                if self._startup_item is not None:
                    self._startup_item.set_active(new_startup)

            if new_sync_power != self._sync_system_power:
                self._sync_system_power = new_sync_power
                if self._sync_power_item is not None:
                    self._sync_power_item.set_active(new_sync_power)

            if getattr(self, "_battery_care_item", None) is not None:
                self._battery_care_item.set_label(f"Battery Care (Cap at {new_limit}%)")
                self._battery_care_item.set_active(new_limit_enabled and 40 <= new_limit < 100)
            self._battery_cap_limit = new_limit

            dgpu_items = getattr(self, "_dgpu_power_items", {})
            if new_dgpu_mode != getattr(self, "_dgpu_power_mode", "optimus"):
                self._dgpu_power_mode = new_dgpu_mode
                if new_dgpu_mode in dgpu_items:
                    dgpu_items[new_dgpu_mode].set_active(True)
        finally:
            self._building = old_building

        new_topup = bool(new_cfg.get("charge_topup_active", False))
        if getattr(self, "_last_topup_active", False) != new_topup:
            self._last_topup_active = new_topup
            self._rebuild_menu()

    # ────────────────────────────────────────────
    # Keyboard idle auto-off
    # ────────────────────────────────────────────

    def _init_idle(self) -> None:
        """Start event-driven idle monitoring (evdev) or D-Bus fallback."""
        with self._idle_lock:
            self._init_idle_locked()

    def _init_idle_locked(self) -> None:
        self._stop_idle_locked()
        self._idle_dimmed = False
        if not self._idle_enabled:
            return
        if self._no_keyboard and not getattr(self, "_has_lamparray", False):
            # No backlight to manage; polling fallback is pointless.
            return
        monitor = IdleMonitor(
            on_idle=self._on_idle_fired,
            on_active=self._on_idle_active,
            timeout_sec=self._idle_timeout,
            enabled=True,
        )
        if monitor.start():
            self._idle_monitor = monitor
            return
        # evdev unavailable (missing dep or permissions) → poll a
        # platform idle query if one exists (Mutter/ScreenSaver/X11).
        ms, _name = fallback_idle_ms()
        if ms is not None:
            self._idle_fallback_timer_id = GLib.timeout_add(
                IDLE_FALLBACK_POLL_MS, self._fallback_idle_poll
            )

    def _stop_idle(self) -> None:
        with self._idle_lock:
            self._stop_idle_locked()

    def _stop_idle_locked(self) -> None:
        if self._idle_monitor is not None:
            try:
                self._idle_monitor.stop()
            except Exception:
                pass
            self._idle_monitor = None
        if self._idle_fallback_timer_id is not None:
            try:
                GLib.source_remove(self._idle_fallback_timer_id)
            except Exception:
                pass
            self._idle_fallback_timer_id = None

    def _on_idle_fired(self) -> None:
        """Backlight off after timeout (no config write — transient)."""
        if not self._idle_enabled or self._idle_dimmed:
            return
        if self._current_brightness == 0:
            return  # user already wants it off
        if self._config.get("idle_keep_on_ac", False) and get_battery_manager().is_ac_online():
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        try:
            set_off(dev)
            self._idle_dimmed = True
        except Exception:
            pass

    def _on_idle_active(self) -> None:
        """Restore saved backlight on first input after idle."""
        if not self._idle_dimmed:
            return
        self._idle_dimmed = False
        if self._current_brightness == 0:
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        try:
            self._sync_from_external_config()
            self._apply_current_rgb(dev)
        except Exception:
            pass

    def _fallback_idle_poll(self) -> bool:
        """Polling fallback when evdev is unavailable. True = keep timer."""
        if not self._idle_enabled or self._idle_monitor is not None:
            return False
        try:
            ms, _name = fallback_idle_ms()
        except Exception:
            return True
        if ms is None:
            return True  # keep waiting; backend may appear later
        if ms >= self._idle_timeout * 1000:
            self._on_idle_fired()
        else:
            self._on_idle_active()
        return True

    def _idle_effective_step(self) -> int:
        """Selectable step reflecting current state (Off when disabled)."""
        if not self._idle_enabled:
            return IDLE_STEP_OFF
        return _nearest_idle_step(self._idle_timeout)

    def _idle_parent_label(self) -> str:
        """Label of the idle submenu parent item (shows the effective timeout)."""
        return f"Backlight idle off: {_idle_step_label(self._idle_effective_step())}"

    def _update_idle_parent_label(self) -> None:
        if self._idle_parent_item is not None:
            try:
                self._idle_parent_item.set_label(self._idle_parent_label())
            except Exception:
                pass

    def _restore_from_idle(self) -> None:
        """Restore saved backlight if currently idle-dimmed."""
        if not self._idle_dimmed:
            return
        self._idle_dimmed = False
        dev = self._get_keyboard()
        if dev is not None and self._current_brightness != 0:
            try:
                self._apply_current_rgb(dev)
            except Exception:
                pass

    def _on_idle_step_changed(self, item: Gtk.RadioMenuItem, step: int) -> None:
        if not item.get_active() or self._building:
            return
        if step == IDLE_STEP_OFF:
            # Off = do nothing: disable monitoring, keep stored timeout.
            self._idle_enabled = False
            self._restore_from_idle()
        else:
            self._idle_enabled = True
            self._idle_timeout = clamp_timeout(step)
            self._idle_dimmed = False
        self._save_config()
        self._update_idle_parent_label()
        self._init_idle()

    def _append_idle_section(self, menu: Optional[Gtk.Menu] = None) -> None:
        """Add idle auto-off submenu (Off/10s/30s/1m/2m, RGB menus only)."""
        if not getattr(self, "_has_lamparray", False):
            return
        target = menu if menu is not None else self._menu
        target.append(Gtk.SeparatorMenuItem())

        self._idle_parent_item = Gtk.MenuItem(label=self._idle_parent_label())
        submenu = Gtk.Menu()
        self._idle_parent_item.set_submenu(submenu)
        target.append(self._idle_parent_item)

        group = None
        self._idle_timeout_items = {}
        effective = self._idle_effective_step()
        for step, label in IDLE_TIMEOUT_STEPS:
            item = Gtk.RadioMenuItem(group=group, label=label)
            if group is None:
                group = item
            if step == effective:
                item.set_active(True)
            item.connect("toggled", self._on_idle_step_changed, step)
            submenu.append(item)
            self._idle_timeout_items[step] = item

    # ────────────────────────────────────────────
    # Menu building
    # ────────────────────────────────────────────

    def _build_menu(self) -> None:
        """Build or rebuild the entire tray menu."""
        self._menu = Gtk.Menu()

        if self._no_keyboard and self._acpi_controller is None:
            self._build_no_hardware_menu()
        elif self._no_keyboard and self._acpi_controller is not None:
            self._build_acpi_only_menu()
        elif self._unsupported:
            self._build_unsupported_menu()
        else:
            self._build_supported_menu()

        if self._menu is not None:
            self._menu.show_all()

        if AppIndicator3 is None:
            # No AppIndicator typelib: the daemon still runs (hotkeys, sleep,
            # config monitoring) but cannot show a tray icon.
            logger.warning("AppIndicator3 not available; running without a tray icon.")
            return

        if self._indicator is None:
            icon_dir = str(Path(APP_ICON_PATH).parent)
            self._indicator = AppIndicator3.Indicator.new_with_path(
                APP_ID,
                APP_ICON_PATH,
                AppIndicator3.IndicatorCategory.HARDWARE,
                icon_dir,
            )
            self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(self._menu)
        self._refresh_tray_icon()

    def _add_separator(self, menu: Optional[Gtk.Menu] = None) -> None:
        """Append a separator unless the last child already is one.

        Keeps optional sections (battery, keyboard, dGPU) from stacking
        consecutive separators when the section before them is absent.
        """
        target = menu if menu is not None else self._menu
        try:
            children = target.get_children()
        except Exception:
            children = []
        if children and isinstance(children[-1], Gtk.SeparatorMenuItem):
            return
        target.append(Gtk.SeparatorMenuItem())

    def _make_submenu(self, label: str) -> Gtk.MenuItem:
        """Append a submenu parent item and return it (children added by caller).

        The tray menu must stay short: Plasma's popup lays out SNI menus in
        multiple columns when they are taller than the screen, which looks
        broken. Everything that is not a one-click action therefore lives in
        a flyout instead of a flat row.
        """
        parent = Gtk.MenuItem(label=label)
        parent.set_submenu(Gtk.Menu())
        self._menu.append(parent)
        return parent

    def _init_sleep_handler(self) -> None:
        """Initialize clean state handling before suspend and after wake."""
        try:
            handler = get_sleep_handler()
            # Pause idle monitoring across suspend and restart it on resume so
            # the backlight state stays consistent with apply_hardware_settings.
            handler.set_hooks(self._on_suspend_hook, self._on_resume_hook)
            handler.start_listening()
        except Exception:
            pass

    def _on_suspend_hook(self) -> None:
        """Called before the machine sleeps."""
        self._stop_idle()

    def _on_resume_hook(self) -> None:
        """Called after the machine wakes."""
        # If the backlight was idle-dimmed before suspend, restore it first;
        # _init_idle() would otherwise clear the flag and leave it dark.
        if self._idle_dimmed:
            self._on_idle_active()
        self._init_idle()

    def _append_center_item(self) -> None:
        """Add launcher item for GigaMate Center GUI."""
        item = Gtk.MenuItem(label="GigaMate Center...")
        item.connect("activate", self._on_open_center)
        self._menu.append(item)
        self._add_separator()

    def _activate_center(self) -> None:
        """Raise an existing Center instance via IPC, else launch one."""
        sock_path = get_runtime_ipc_paths()[0]

        # 1. Try activating existing instance via Unix socket directly (0ms latency, 0 new processes)
        try:
            import socket as py_socket
            with py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM) as s:
                s.settimeout(0.8)
                s.connect(sock_path)
                s.sendall(b"ACTIVATE\n")
                return
        except Exception:
            pass

        # 2. Not running: launch primary instance detached
        try:
            subprocess.Popen(["gigamate", "center"], start_new_session=True)
        except Exception:
            pass

    def _on_open_center(self, _widget) -> None:
        self._activate_center()

    def _on_run_setup(self, *_widget) -> None:
        """Open GigaMate Center's setup wizard (works even if already onboarded)."""
        sock_path = get_runtime_ipc_paths()[0]
        try:
            import socket as py_socket
            if os.path.exists(sock_path):
                with py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM) as s:
                    s.settimeout(0.8)
                    s.connect(sock_path)
                    s.sendall(b"SETUP\n")
                    return
        except Exception:
            pass
        try:
            env = dict(os.environ)
            env["GIGAMATE_FORCE_SETUP"] = "1"
            subprocess.Popen(["gigamate", "center"], env=env, start_new_session=True)
        except Exception:
            pass

    def _battery_label(self, info) -> str:
        """Submenu (or status) label for the current battery state."""
        ac_str = "AC" if info.ac_online else "Battery"
        topup_active = bool(self._config.get("charge_topup_active", False))
        if topup_active:
            return f"Battery: {info.capacity}% ({ac_str} — Topping up to 100%)"
        return f"Battery: {info.capacity}% ({ac_str})"

    def _update_battery_label(self, info) -> None:
        """Keep the battery flyout label in sync with capacity/AC state."""
        parent = getattr(self, "_battery_parent_item", None)
        if parent is None:
            return
        label = self._battery_label(info)
        if label == getattr(self, "_battery_parent_label", None):
            return
        self._battery_parent_label = label
        try:
            parent.set_label(label)
        except Exception:
            pass

    def _append_battery_section(self) -> Optional[Gtk.MenuItem]:
        """Add a Battery flyout: live state plus charge-limit controls.

        The live percentage stays in the parent label, so it is visible
        without opening the flyout.
        """
        self._battery_parent_item = None
        self._battery_parent_label = None
        mgr = get_battery_manager()
        if not mgr.is_available:
            return None

        info = mgr.get_battery_info()
        topup_active = bool(self._config.get("charge_topup_active", False))

        if not mgr.is_charge_limit_supported():
            # Nothing actionable: keep a single, non-interactive info row.
            item = Gtk.MenuItem(label=self._battery_label(info))
            item.set_sensitive(False)
            self._menu.append(item)
            return item

        label = self._battery_label(info)
        parent = self._make_submenu(label)
        self._battery_parent_item = parent
        self._battery_parent_label = label
        submenu = parent.get_submenu()

        limit_enabled = self._config.get("charge_limit_enabled", False)
        try:
            limit = int(self._config.get("charge_limit", 80))
        except (TypeError, ValueError):
            limit = 80
        if not (40 <= limit <= 100):
            limit = 80
        # Any limit below 100% counts as an active cap, not just 80%.
        cap_active = bool(limit_enabled) and limit < 100
        chk_limit = Gtk.CheckMenuItem(label=f"Battery Care (Cap at {limit}%)")
        chk_limit.set_active(cap_active and not topup_active)
        chk_limit.connect("toggled", self._on_toggle_battery_care)
        self._battery_cap_limit = limit
        self._battery_care_item = chk_limit
        submenu.append(chk_limit)

        if topup_active:
            topup_item = Gtk.MenuItem(label="Cancel 100% Top-Up")
            topup_item.connect("activate", self._on_cancel_topup)
        else:
            topup_item = Gtk.MenuItem(label="Charge to 100% Once")
            topup_item.connect("activate", self._on_start_topup)
        submenu.append(topup_item)
        return parent

    def _on_start_topup(self, _widget: Gtk.MenuItem) -> None:
        mgr = get_battery_manager()
        if start_topup(mgr):
            self._config["charge_topup_active"] = True
            self._rebuild_menu()

    def _on_cancel_topup(self, _widget: Gtk.MenuItem) -> None:
        mgr = get_battery_manager()
        cancel_topup(mgr, restore_limit=True)
        self._config["charge_topup_active"] = False
        self._rebuild_menu()

    def _on_toggle_battery_care(self, widget: Gtk.CheckMenuItem) -> None:
        if self._building:
            return
        mgr = get_battery_manager()
        if widget.get_active():
            cap = getattr(self, "_battery_cap_limit", 80)
            target = cap if 40 <= cap < 100 else 80
        else:
            target = 100
        try:
            ok = mgr.set_charge_limit(target)
        except Exception:
            ok = False

        if ok:
            if widget.get_active():
                self._config["charge_limit"] = target
            self._config["charge_limit_enabled"] = widget.get_active()
            self._battery_dirty = True
            self._save_config()
        else:
            # Revert the checkbox without re-entering this handler.
            self._building = True
            try:
                widget.set_active(not widget.get_active())
            finally:
                self._building = False

    def _build_no_hardware_menu(self) -> None:
        """Menu when no Gigabyte hardware is detected at all."""
        self._append_center_item()
        item = Gtk.MenuItem(label="No Gigabyte hardware detected")
        item.set_sensitive(False)
        self._menu.append(item)
        self._add_separator()
        self._append_battery_section()
        self._add_separator()
        self._append_settings_section()
        self._append_quit()

    def _build_acpi_only_menu(self) -> None:
        """Menu when ACPI is available but keyboard is not found."""
        self._dgpu_power_parent_item = None
        self._colour_items = {}
        self._brightness_items = {}

        self._append_center_item()
        self._append_status_section()
        self._append_power_profile_section()

        if self._update_available and self._latest_version:
            self._add_separator()
            upd_item = Gtk.MenuItem(label=update_checker.menu_item_label(True))
            upd_item.connect("activate", self._on_update_clicked)
            self._menu.append(upd_item)

        self._append_quit()

    def _build_unsupported_menu(self) -> None:
        """Menu when keyboard is found but not in the profile database."""
        self._append_center_item()
        vid = self._detected_vid or 0
        pid = self._detected_pid or 0
        dmi_model = get_dmi_product_name()
        label = f"{dmi_model} ({vid:04X}:{pid:04X})" if dmi_model else f"Unknown model ({vid:04X}:{pid:04X})"

        header = Gtk.MenuItem(label=label)
        header.set_sensitive(False)
        self._menu.append(header)

        # Check if ACPI is available independently
        if self._acpi_controller is None:
            ctrl = AcpiController()
            if ctrl.available:
                self._acpi_controller = ctrl
                self._acpi_caps = ctrl.capabilities

        if self._acpi_controller is not None:
            self._append_power_profile_section()
            self._append_status_section()
            self._add_separator()
            self._append_battery_section()

        self._add_separator()
        self._append_settings_section()
        self._append_quit()

    def _build_supported_menu(self) -> None:
        """Full menu: Open Center, live status, power profiles, optional update, Quit."""
        self._dgpu_power_parent_item = None
        self._colour_items = {}
        self._brightness_items = {}

        # ── Open GigaMate Center ──
        self._append_center_item()

        # ── Live Status section (ACPI) — always on top ──
        self._append_status_section()

        # ── Power Profile section (ACPI) — primary action, one click away ──
        self._append_power_profile_section()

        # ── Backlight submenu ──
        self._append_backlight_section()

        if self._update_available and self._latest_version:
            self._add_separator()
            upd_item = Gtk.MenuItem(label=update_checker.menu_item_label(True))
            upd_item.connect("activate", self._on_update_clicked)
            self._menu.append(upd_item)

        self._append_quit()

    # ────────────────────────────────────────────
    # Section builders
    # ────────────────────────────────────────────

    def _append_backlight_section(self) -> None:
        """Add compact Backlight submenu (Off/5%/50%/100% for LampArray full RGB)."""
        if not getattr(self, "_has_lamparray", False):
            return
        self._add_separator()
        parent = self._make_submenu("Backlight")
        sub = parent.get_submenu()
        group = None

        rgb_enabled = bool(self._config.get("rgb_enabled", True))
        pct = int(self._config.get("brightness_pct", 100) or 0)
        active_pct = 0 if not rgb_enabled else pct
        steps = [("Off", 0), ("5%", 5), ("50%", 50), ("100%", 100)]
        for label, val in steps:
            item = Gtk.RadioMenuItem(group=group, label=label)
            if group is None:
                group = item
            if (val == 0 and active_pct == 0) or (val > 0 and active_pct == val):
                item.set_active(True)
            item.connect("toggled", self._on_backlight_full_rgb_selected, val)
            sub.append(item)
            self._brightness_items[val] = item

    def _on_backlight_full_rgb_selected(self, item: Gtk.RadioMenuItem, pct: int) -> None:
        if not item.get_active() or self._building:
            return
        dev = self._get_keyboard()
        if pct == 0:
            self._config["rgb_enabled"] = False
            self._save_config()
            if dev is not None:
                try:
                    set_off(dev)
                except Exception:
                    pass
        else:
            self._config["rgb_enabled"] = True
            self._config["brightness_pct"] = pct
            self._save_config()
            if dev is not None:
                try:
                    set_rgb(
                        dev,
                        effective_rgb_hex(self._config),
                        pct,
                    )
                except Exception:
                    pass

    def _dgpu_power_labels(self, gst=None):
        """Return (parent_label, status_label) for the dGPU Power Gate flyout."""
        if gst is None:
            try:
                gst = dgpu_gate.get_status(check_helper=False)
            except Exception:
                gst = {}
        mode = gst.get("mode") or getattr(self, "_dgpu_power_mode", "optimus")
        gated = bool(gst.get("gated"))
        wedged = bool(gst.get("wedged"))
        pending = bool(gst.get("pending_off"))
        ac = bool(gst.get("ac_online", True))
        if getattr(self, "_dgpu_power_busy", False):
            parent_label = "dGPU Power: Switching…"
        elif mode == "optimus":
            parent_label = "dGPU Power: Optimus (Off)" if gated else "dGPU Power: Optimus"
        elif mode == "eco":
            if gated:
                parent_label = "dGPU Power: ECO (Off)"
            elif pending:
                parent_label = "dGPU Power: ECO (Pending)"
            else:
                parent_label = "dGPU Power: ECO"
        else:
            if gated:
                parent_label = "dGPU Power: Auto (Battery — Off)"
            elif ac:
                parent_label = "dGPU Power: Auto (AC)"
            elif pending:
                parent_label = "dGPU Power: Auto (Battery — Pending)"
            else:
                parent_label = "dGPU Power: Auto (Battery)"

        if gated:
            status_label = "State: Off (gated)"
        elif wedged:
            status_label = "State: Wedged — GPU needs a reboot"
        elif pending:
            reason = gst.get("last_reason")
            holders = gst.get("holders") or []
            displays = gst.get("displays") or []
            if reason == "display_connected" and displays:
                status_label = f"Blocked: display on {', '.join(displays)}"
            elif holders:
                status_label = f"Pending off: held by {', '.join(holders)}"
            elif reason == "boot_settle":
                status_label = f"Pending off: {gst.get('last_error') or 'boot settle'}"
            else:
                status_label = "Pending off: waiting for dGPU suspend"
        else:
            rstatus = gst.get("runtime_status")
            if rstatus == "suspended":
                status_label = "State: Asleep (runtime PM)"
            elif rstatus == "active":
                status_label = "State: Awake (D0)"
            else:
                status_label = f"State: {rstatus or 'Active'}"
        return parent_label, status_label

    def _update_dgpu_power_labels(self, gst=None) -> None:
        parent = getattr(self, "_dgpu_power_parent_item", None)
        status_item = getattr(self, "_dgpu_power_status_item", None)
        eco_item = getattr(self, "_dgpu_power_items", {}).get(dgpu_gate.MODE_ECO)
        if parent is None and status_item is None and eco_item is None:
            return
        if gst is None:
            try:
                gst = dgpu_gate.get_status(check_helper=False)
            except Exception:
                gst = {}
        parent_label, status_label = self._dgpu_power_labels(gst)
        if parent is not None and parent_label != getattr(self, "_dgpu_power_parent_label", None):
            self._dgpu_power_parent_label = parent_label
            try:
                parent.set_label(parent_label)
            except Exception:
                pass
        if status_item is not None and status_label != getattr(self, "_dgpu_power_status_label", None):
            self._dgpu_power_status_label = status_label
            try:
                status_item.set_label(status_label)
            except Exception:
                pass
        if eco_item is not None:
            ac = bool(gst.get("ac_online", True))
            eco_allowed = not ac
            eco_label = "ECO (Battery only)" if ac else "ECO (dGPU Off)"
            if eco_allowed != getattr(self, "_dgpu_eco_allowed_cache", None):
                self._dgpu_eco_allowed_cache = eco_allowed
                try:
                    eco_item.set_sensitive(eco_allowed)
                    eco_item.set_label(eco_label)
                except Exception:
                    pass
        self._update_profile_items_sensitivity(gst=gst)

    def _append_dgpu_power_section(self) -> Optional[Gtk.MenuItem]:
        """Add a dGPU Power Gate flyout (Optimus / ECO / Auto) when supported."""
        self._dgpu_power_items = {}
        self._dgpu_power_parent_item = None
        self._dgpu_power_parent_label = None
        self._dgpu_power_status_item = None
        self._dgpu_power_status_label = None
        try:
            if not dgpu_gate.is_supported():
                return None
            gst = dgpu_gate.get_status(check_helper=False)
        except Exception:
            return None

        mode = gst.get("mode") or self._config.get("dgpu_power_mode", "optimus")
        self._dgpu_power_mode = mode
        parent_label, status_label = self._dgpu_power_labels(gst)

        parent = self._make_submenu(parent_label)
        self._dgpu_power_parent_item = parent
        self._dgpu_power_parent_label = parent_label
        submenu = parent.get_submenu()

        ac = bool(gst.get("ac_online", True))
        self._dgpu_eco_allowed_cache = not ac
        group = None
        for key, label in (
            ("optimus", "Optimus (Default)"),
            ("eco", "ECO (Battery only)" if ac else "ECO (dGPU Off)"),
            ("auto", "Auto (Off on Battery)"),
        ):
            item = Gtk.RadioMenuItem(group=group, label=label)
            if group is None:
                group = item
            if key == mode:
                item.set_active(True)
            if key == "eco":
                item.set_sensitive(not ac)
            item.connect("toggled", self._on_dgpu_power_mode_changed, key)
            submenu.append(item)
            self._dgpu_power_items[key] = item

        submenu.append(Gtk.SeparatorMenuItem())
        status_item = Gtk.MenuItem(label=status_label)
        status_item.set_sensitive(False)
        submenu.append(status_item)
        self._dgpu_power_status_item = status_item
        self._dgpu_power_status_label = status_label

        note_item = Gtk.MenuItem(label="HDMI/DP on dGPU off when gated")
        note_item.set_sensitive(False)
        submenu.append(note_item)
        return parent

    def _on_dgpu_power_mode_changed(self, item: Gtk.RadioMenuItem, mode: str) -> None:
        if not item.get_active() or self._building:
            return
        if getattr(self, "_dgpu_power_busy", False):
            return
        prev_mode = getattr(self, "_dgpu_power_mode", "optimus")
        if mode == dgpu_gate.MODE_ECO and not dgpu_gate.eco_allowed_on_power():
            items = getattr(self, "_dgpu_power_items", {})
            if prev_mode in items:
                old_b = self._building
                self._building = True
                try:
                    items[prev_mode].set_active(True)
                finally:
                    self._building = old_b
            self._update_dgpu_power_labels()
            return
        self._dgpu_power_mode = mode
        self._dgpu_power_busy = True
        self._update_dgpu_power_labels()

        needs_confirm = False
        try:
            if not dgpu_gate.is_gated_off():
                if mode == dgpu_gate.MODE_ECO:
                    needs_confirm = True
                elif mode == dgpu_gate.MODE_AUTO:
                    try:
                        ac_online = bool(get_battery_manager().is_ac_online())
                    except Exception:  # noqa: BLE001
                        ac_online = True
                    needs_confirm = not ac_online
        except Exception:  # noqa: BLE001
            needs_confirm = False

        if needs_confirm:
            trigger = "eco" if mode == dgpu_gate.MODE_ECO else "auto"
            proc = dgpu_gate._spawn_confirm_dialog(
                trigger=trigger,
                target_mode=mode,
                prev_mode=prev_mode,
            )
            if proc is not None:
                def _wait_dialog() -> None:
                    try:
                        proc.wait()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        GLib.idle_add(self._on_dgpu_power_dialog_finished, mode)
                    except Exception:
                        pass

                threading.Thread(
                    target=_wait_dialog,
                    name="gigamate-tray-dgpu-dialog",
                    daemon=True,
                ).start()
                return

        def _worker() -> None:
            closable = []
            try:
                res = dgpu_gate.set_mode(mode)
                if not res.get("ok") and res.get("reason") == "gpu_active":
                    closable = dgpu_gate.closable_gpu_holders()
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            try:
                GLib.idle_add(self._on_dgpu_power_mode_done, mode, res, closable, True)
            except Exception:
                pass

        threading.Thread(target=_worker, name="gigamate-tray-dgpu-gate", daemon=True).start()

    def _on_dgpu_power_dialog_finished(self, fallback_mode: str) -> bool:
        self._dgpu_power_busy = False
        real_mode = fallback_mode
        try:
            self._config = load_config()
            self._last_config_mtime = self._get_config_mtime()
            real_mode = str(self._config.get("dgpu_power_mode", fallback_mode)).lower()
        except Exception:
            pass
        self._dgpu_power_mode = real_mode
        old_building = self._building
        self._building = True
        try:
            items = getattr(self, "_dgpu_power_items", {})
            if real_mode in items:
                items[real_mode].set_active(True)
        finally:
            self._building = old_building
        self._update_status()
        return False

    def _on_dgpu_power_mode_done(
        self,
        mode: str,
        res: dict,
        closable: Optional[list] = None,
        prompt_allowed: bool = False,
    ) -> bool:
        self._dgpu_power_busy = False
        try:
            self._config = load_config()
            self._last_config_mtime = self._get_config_mtime()
            self._dgpu_power_mode = self._config.get("dgpu_power_mode", mode)
        except Exception:
            pass
        self._update_status()
        if (
            prompt_allowed
            and mode in (dgpu_gate.MODE_ECO, dgpu_gate.MODE_AUTO)
            and not res.get("ok")
            and res.get("reason") == "gpu_active"
            and closable
        ):
            app_list = "\n".join(f"  • {h.label}" for h in closable)
            dlg = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text="Close Apps Holding Discrete GPU?",
            )
            dlg.format_secondary_text(
                "The discrete GPU is currently in use by:\n"
                f"{app_list}\n\n"
                "Warning: Force-closing them may cause unsaved work to be lost.\n"
                "Close them now to power off the dGPU immediately, or keep waiting in Pending mode?"
            )
            dlg.add_button("Keep Waiting (Pending)", Gtk.ResponseType.CANCEL)
            dlg.add_button("Close Apps & Power Off", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.CANCEL)
            resp = dlg.run()
            dlg.destroy()
            if resp == Gtk.ResponseType.OK:
                self._dgpu_power_busy = True
                self._update_dgpu_power_labels()

                def _force_worker() -> None:
                    try:
                        fres = dgpu_gate.force_close_and_gate_off(mode=mode)
                    except Exception as exc:  # noqa: BLE001
                        fres = {"ok": False, "error": str(exc)}
                    try:
                        GLib.idle_add(self._on_dgpu_power_mode_done, mode, fres, [], False)
                    except Exception:
                        pass

                threading.Thread(
                    target=_force_worker,
                    name="gigamate-tray-dgpu-force-close",
                    daemon=True,
                ).start()
            return False

        if not res.get("ok") and res.get("reason") != "gpu_active":
            err = res.get("error") or "Failed to switch dGPU power mode."
            dlg = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="dGPU Power Gate",
            )
            dlg.format_secondary_text(err)
            dlg.run()
            dlg.destroy()
        return False

    def _append_keyboard_section(self) -> None:
        """Add the Keyboard flyout: colour, brightness and idle auto-off."""
        if not getattr(self, "_has_lamparray", False):
            return
        parent = self._make_submenu("Keyboard")
        submenu = parent.get_submenu()

        colour_header = Gtk.MenuItem(label="Colour")
        colour_header.set_sensitive(False)
        submenu.append(colour_header)

        colour_group = None
        for cname in PALETTE_HEX.keys():
            label = cname.replace("_", " ").title()
            item = Gtk.RadioMenuItem(group=colour_group, label=label)
            if colour_group is None:
                colour_group = item
            if cname == self._current_colour:
                item.set_active(True)
            item.connect("toggled", self._on_colour_changed, cname)
            submenu.append(item)
            self._colour_items[cname] = item

        submenu.append(Gtk.SeparatorMenuItem())

        brightness_header = Gtk.MenuItem(label="Brightness")
        brightness_header.set_sensitive(False)
        submenu.append(brightness_header)

        bright_group = None
        if getattr(self, "_full_rgb", False):
            full_rgb_levels = [
                (0, "Off"),
                (5, "5%"),
                (15, "15%"),
                (40, "40%"),
                (100, "100%"),
            ]
            current_pct = getattr(self, "_current_brightness_pct", 100)
            if not getattr(self, "_rgb_enabled", True):
                nearest_pct = 0
            else:
                nearest_pct = min(
                    (p for p, _ in full_rgb_levels if p > 0),
                    key=lambda p: abs(p - current_pct),
                )
            for pct, label in full_rgb_levels:
                item = Gtk.RadioMenuItem(group=bright_group, label=label)
                if bright_group is None:
                    bright_group = item
                if pct == nearest_pct:
                    item.set_active(True)
                item.connect("toggled", self._on_brightness_pct_changed, pct)
                submenu.append(item)
                self._brightness_items[pct] = item
        else:
            for level, label in enumerate(BRIGHTNESS_NAMES):
                item = Gtk.RadioMenuItem(group=bright_group, label=label)
                if bright_group is None:
                    bright_group = item
                if level == self._current_brightness:
                    item.set_active(True)
                item.connect("toggled", self._on_brightness_changed, level)
                submenu.append(item)
                self._brightness_items[level] = item

        self._append_idle_section(submenu)

    def _get_discovered_profiles(self) -> Dict[int, Dict[str, str]]:
        """Return discovered ACPI power profiles, falling back to cached caps."""
        profiles = discover_acpi_profiles()
        if not profiles and self._acpi_caps is not None:
            profiles = getattr(self._acpi_caps, "discovered_profiles", {}) or {}
        return profiles

    def _append_power_profile_section(self) -> None:
        """Add Power Profile radio group from discovered ACPI profiles."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return

        profile_names = self._get_discovered_profiles()
        self._add_separator()
        header = Gtk.MenuItem(label="Power Profile")
        header.set_sensitive(False)
        self._menu.append(header)

        if not profile_names:
            empty_item = Gtk.MenuItem(label="No power profiles exposed by BIOS")
            empty_item.set_sensitive(False)
            self._menu.append(empty_item)
            return

        group = None
        self._profile_items = {}
        self._profile_base_labels: Dict[int, str] = {}
        high_ok = dgpu_gate.high_power_profiles_allowed()
        self._high_power_allowed_cache = high_ok

        for pid_int in sorted(int(k) for k in profile_names.keys()):
            entry = profile_names.get(pid_int) or profile_names.get(str(pid_int), {})
            label = entry.get("name", f"Profile {pid_int}")
            self._profile_base_labels[pid_int] = label
            allowed = high_ok or pid_int not in (2, 3)
            disp_label = f"{label} (dGPU off)" if (not high_ok and pid_int in (2, 3)) else label
            item = Gtk.RadioMenuItem(group=group, label=disp_label)
            if group is None:
                group = item
            if self._current_acpi_profile == pid_int:
                item.set_active(True)
            item.set_sensitive(allowed)
            item.connect("toggled", self._on_profile_changed, pid_int)
            self._menu.append(item)
            self._profile_items[pid_int] = item

    def _append_status_section(self) -> None:
        """Add a single-line live status display (ACPI sensors and/or dGPU state)."""
        acpi_ok = self._acpi_controller is not None and self._acpi_controller.available
        gpu_present = get_gpu_state().present
        if not acpi_ok and not gpu_present:
            return

        self._add_separator()
        self._status_items = []
        item = Gtk.MenuItem(label="Status: reading...")
        item.set_sensitive(False)
        self._menu.append(item)
        self._status_items.append(item)

    def _append_settings_section(self) -> None:
        """Add the Settings flyout: startup, maintenance, updates and about."""
        parent = self._make_submenu("Settings")
        submenu = parent.get_submenu()
        self._append_settings_items(submenu)
        self._append_about(submenu)

    def _append_settings_items(self, menu: Optional[Gtk.Menu] = None) -> None:
        """Add settings items to the settings flyout."""
        target = menu if menu is not None else self._menu
        # System power-profile sync only makes sense when a usable model
        # profile exists to map from; without one there is nothing to sync.
        if is_system_power_available() and self._get_discovered_profiles():
            self._sync_power_item = Gtk.CheckMenuItem(label="Sync system power profile")
            self._sync_power_item.set_active(self._sync_system_power)
            self._sync_power_item.connect("toggled", self._on_sync_power_toggled)
            target.append(self._sync_power_item)

        self._startup_item = Gtk.CheckMenuItem(label="Apply on startup")
        self._startup_item.set_active(self._startup_apply)
        self._startup_item.connect("toggled", self._on_startup_toggled)
        target.append(self._startup_item)

        reload_item = Gtk.MenuItem(label="Reload hardware")
        reload_item.connect("activate", self._on_reload)
        target.append(reload_item)

        setup_item = Gtk.MenuItem(label="Set up GigaMate…")
        setup_item.connect("activate", self._on_run_setup)
        target.append(setup_item)

        target.append(Gtk.SeparatorMenuItem())
        check_item = Gtk.MenuItem(
            label=update_checker.menu_item_label(self._update_available))
        if self._update_available and self._latest_version:
            check_item.connect("activate", self._on_update_clicked)
        else:
            check_item.connect("activate", self._on_check_updates_clicked)
        target.append(check_item)

    def _append_about(self, menu: Optional[Gtk.Menu] = None) -> None:
        target = menu if menu is not None else self._menu
        about_item = Gtk.MenuItem(label="About")
        about_item.connect("activate", self._on_about)
        target.append(about_item)

    def _append_quit(self) -> None:
        self._add_separator()
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_quit)
        self._menu.append(quit_item)

    # ────────────────────────────────────────────
    # Updates (battery-efficient daily check)
    # ────────────────────────────────────────────

    def _init_update_check(self) -> None:
        """Startup check (cached) + daily re-check with jitter."""
        self._run_update_check_async(force=False)
        jitter_ms = random.randint(0, 30 * 60 * 1000)
        self._update_timer_id = GLib.timeout_add(
            UPDATE_CHECK_INTERVAL_MS + jitter_ms, self._update_timer_tick)

    def _update_timer_tick(self) -> bool:
        self._run_update_check_async(force=False)
        return True  # keep daily timer alive

    def _run_update_check_async(self, force: bool = False) -> None:
        t = threading.Thread(target=self._check_updates_bg,
                             args=(force,), daemon=True)
        t.start()

    def _check_updates_bg(self, force: bool) -> None:
        try:
            result = update_checker.check_for_updates(force=force)
        except Exception:
            return
        try:
            GLib.idle_add(self._on_update_result, result)
        except Exception:
            pass

    def _on_update_result(self, result: dict) -> bool:
        changed = (result.get("update_available") != self._update_available
                   or result.get("latest") != self._latest_version)
        self._update_available = bool(result.get("update_available"))
        self._latest_version = result.get("latest")
        self._refresh_tray_icon()
        if changed:
            self._rebuild_menu()
        return False  # single-shot idle callback

    def _on_update_clicked(self, *args) -> None:
        """Confirm dialog, then update via background or terminal path."""
        # Fast, non-interactive probes first so the dialog states the
        # actual path. sudo -n never prompts and changes nothing.
        try:
            admin = update_checker.admin_status()
        except Exception:
            admin = "password"
        if admin in ("denied", "no-sudo"):
            self._show_no_admin_dialog(admin)
            return
        # Password needed → always use the user's own terminal: sudo
        # prompts in-band there. Background runs are only for passwordless
        # sudo, where no prompt can appear. (Detached background sudo
        # fails on stock systems: no tty and no askpass wired in.)
        use_terminal = admin == "password"

        current = update_checker.get_installed_version()
        latest = self._latest_version or "latest"
        if use_terminal:
            secondary = (
                "A terminal window will open and run the update there "
                "(re-runs install.sh for the latest tagged release, "
                "including drivers and tray). Enter your password in "
                "the terminal when asked.\n\n"
                "Update now?")
        else:
            secondary = (
                "GigaMate will update in the background (re-runs install.sh "
                "for the latest tagged release, including drivers and tray).\n"
                "No password prompt is expected (passwordless sudo).\n\n"
                "Update now?")
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Update available ({current} → {latest})",
        )
        dlg.format_secondary_text(secondary)
        response = dlg.run()
        dlg.destroy()
        if response != Gtk.ResponseType.YES:
            return
        if use_terminal:
            self._run_update_in_terminal()
        else:
            self._run_update_in_background()

    def _show_no_admin_dialog(self, admin: str) -> None:
        # No admin rights → the privileged steps would fail: abort now
        # with instructions instead of a doomed background run.
        noadmin = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text="Administrator rights required",
        )
        noadmin.format_secondary_text(
            "Updating drivers needs sudo, which is not available "
            f"for your user (status: {admin}).\n\n"
            f"{update_checker.manual_update_instructions()}")
        noadmin.run()
        noadmin.destroy()

    def _run_update_in_terminal(self) -> None:
        """Stock-system path: normal terminal runs the curl update."""
        argv = update_checker.build_terminal_update_command(
            desktop=os.environ.get("XDG_CURRENT_DESKTOP", ""),
            tag=self._latest_version)
        if argv is None:
            self._show_no_admin_dialog("no-terminal")
            return
        # No dismiss here: if the user closes the terminal without
        # updating, the badge must stay. install.sh --update clears the
        # state file on success.
        try:
            GLib.spawn_async(argv, flags=GLib.SpawnFlags.SEARCH_PATH)
        except Exception:
            self._show_update_failed("Could not open a terminal.")
            return
        info = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Updating in terminal",
        )
        info.format_secondary_text(
            "The update is running in the terminal window.\n"
            "Enter your password there when asked.")
        info.run()
        info.destroy()

    def _run_update_in_background(self) -> None:
        """Askpass-wired path: detached updater with log + child watch."""
        if self._latest_version:
            update_checker.dismiss_version(self._latest_version)
        try:
            pid, _, _, _ = GLib.spawn_async(
                update_checker.build_update_command(tag=self._latest_version),
                flags=(GLib.SpawnFlags.SEARCH_PATH
                       | GLib.SpawnFlags.DO_NOT_REAP_CHILD))
            GLib.child_watch_add(pid, self._on_update_done)
        except Exception:
            update_checker.undismiss()
            self._show_update_failed("Could not start the updater.")
            return
        info = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Updating in background",
        )
        info.format_secondary_text(
            "The update is running. The tray will restart automatically "
            "when install.sh finishes (systemd service restart).\n"
            f"Progress log: {update_checker.UPDATE_LOG_FILE}")
        info.run()
        info.destroy()

    def _on_update_done(self, pid: int, status: int) -> None:
        """Updater child exited: report failures, revive badge if needed."""
        if status == 0:
            return  # service restart (inside install.sh) takes it from here
        update_checker.undismiss()  # show the badge again, not silent
        self._show_update_failed(f"Updater exited with status {status}.")

    def _show_update_failed(self, detail: str) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Update failed",
        )
        dlg.format_secondary_text(
            f"{detail}\n\nLog: {update_checker.UPDATE_LOG_FILE}\n\n"
            f"{update_checker.manual_update_instructions()}")
        dlg.run()
        dlg.destroy()

    def _on_check_updates_clicked(self, *args) -> None:
        """Manual 'Check for updates' — forced network check off the main loop."""
        threading.Thread(target=self._manual_update_check_bg, daemon=True).start()

    def _manual_update_check_bg(self) -> None:
        try:
            result = update_checker.check_for_updates(force=True)
        except Exception:
            result = {"current": "?", "latest": None,
                      "update_available": False}
        try:
            GLib.idle_add(self._after_manual_update_check, result)
        except Exception:
            pass

    def _after_manual_update_check(self, result: dict) -> bool:
        # Feed through the normal path so icon + menu refresh.
        try:
            self._on_update_result(result)
        except Exception:
            pass
        if result.get("update_available"):
            self._on_update_clicked()
            return False
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="GigaMate is up to date"
            if result.get("latest") else "Update check failed",
        )
        detail = (f"Installed: {result.get('current')}  |  "
                  f"Latest: {result.get('latest')}"
                  if result.get("latest")
                  else ("No releases published yet."
                        if result.get("reachable")
                        else "Could not reach github.com. Will retry tomorrow."))
        dlg.format_secondary_text(detail)
        dlg.run()
        dlg.destroy()
        return False

    # ────────────────────────────────────────────
    # Status polling
    # ────────────────────────────────────────────

    def _desired_status_poll_ms(self, ac_online: Optional[bool]) -> int:
        if ac_online is False:
            return STATUS_POLL_BATTERY_MS
        return STATUS_POLL_INTERVAL_MS

    def _poll_status_timer(self) -> bool:
        expected_id = self._status_timer_id
        keep = self._update_status(_from_timer=True)
        if not keep:
            if self._status_timer_id == expected_id:
                self._status_timer_id = None
            return False
        if self._status_timer_id != expected_id:
            return False
        return True

    def _sync_status_poll_interval(
        self, ac_online: Optional[bool], *, from_timer: bool = False
    ) -> None:
        desired_ms = self._desired_status_poll_ms(ac_online)
        if desired_ms == getattr(self, "_status_poll_ms", STATUS_POLL_INTERVAL_MS):
            return
        self._status_poll_ms = desired_ms
        old_id = getattr(self, "_status_timer_id", None)
        if old_id is not None:
            if not from_timer:
                try:
                    GLib.source_remove(old_id)
                except Exception:
                    pass
            self._status_timer_id = GLib.timeout_add(
                desired_ms, self._poll_status_timer
            )

    def _start_status_polling(self) -> None:
        """Start the periodic status update timer."""
        self._update_status()
        self._status_timer_id = GLib.timeout_add(
            self._status_poll_ms, self._poll_status_timer
        )

    def _tray_icon_key(self, gpu=None) -> str:
        """Combine dGPU state with update badge into one of 6 icon keys."""
        base = gpu_icon_key(gpu if gpu is not None else get_gpu_state())
        if self._update_available:
            return f"{base}-update" if base != "gigamate" else "gigamate-update"
        return base

    def _refresh_tray_icon(self, gpu=None) -> None:
        """Switch tray icon to match dGPU + update state (change-only)."""
        key = self._tray_icon_key(gpu)
        if key == self._icon_key and getattr(self, "_icon_applied", False):
            return
        self._icon_key = key
        self._icon_applied = True
        if self._indicator is None:
            return
        try:
            icon_path = APP_ICON_PATHS.get(key, APP_ICON_PATH)
            icon_dir = str(Path(icon_path).parent)
            self._indicator.set_icon_theme_path(icon_dir)
            self._indicator.set_icon_full(icon_path, f"GigaMate {key}")
        except Exception:
            pass

    def _ensure_acpi_controller(self) -> None:
        """Self-heal: re-probe the ACPI backend if it became available later.

        The ACPI controller is initialized once at startup; if the gigamate_acpi
        kernel module was missing then (e.g. after a botched update), it stays
        ``None`` and the tray keeps showing "Status: reading..." even after the
        driver is repaired (via ``gigamate repair``). Retry periodically and,
        when the backend appears, rebuild the menu so the status/profile
        sections come back without needing a tray restart.
        """
        if self._acpi_controller is not None and self._acpi_controller.available:
            return
        now = time.monotonic()
        if now - getattr(self, "_acpi_retry_at", 0.0) < _ACPI_RETRY_SEC:
            return
        self._acpi_retry_at = now
        try:
            ctrl = AcpiController()
            available = bool(ctrl.available)
        except Exception:
            ctrl = None
            available = False
        if ctrl is None or not available:
            return
        self._acpi_controller = ctrl
        self._acpi_caps = ctrl.capabilities
        self._last_status_text = None
        try:
            self._rebuild_menu()
        except Exception:
            pass

    def _update_status(self, _from_timer: bool = False) -> bool:
        """Poll ACPI sensors and dGPU state, updating the status labels. Returns True to keep timer alive."""
        self._sync_from_external_config()
        self._ensure_acpi_controller()
        gpu = get_gpu_state()
        self._refresh_tray_icon(gpu)
        if not self._status_items:
            return False

        parts = []

        # ACPI sensors
        if self._acpi_controller is not None and self._acpi_controller.available:
            state: Optional[FanState] = None
            try:
                state = self._acpi_controller.read_state()
            except Exception:
                pass

            if state is not None:
                # Temperature
                if state.temp_cpu is not None:
                    parts.append(f"CPU: {state.temp_cpu} C")
                elif state.temp_socket is not None:
                    parts.append(f"Socket: {state.temp_socket} C")

                # Fan
                if state.fan1_rpm is not None:
                    parts.append(f"Fan: {state.fan1_rpm} RPM")

                # Duty
                if state.duty_cpu is not None:
                    parts.append(f"{state.duty_cpu}%")

                # Profile
                if state.profile is not None:
                    profiles = self._get_discovered_profiles()
                    entry = profiles.get(state.profile.value) or profiles.get(str(state.profile.value), {})
                    pname = entry.get("name", str(state.profile.value))
                    parts.append(f"Profile: {pname}")

        # Discrete GPU state (reuse the value fetched for the icon).
        if gpu.present:
            parts.append(f"dGPU: {gpu_short_status_text(gpu)}")
        if getattr(self, "_dgpu_power_parent_item", None) is not None:
            self._update_dgpu_power_labels()


        # Battery status
        ac_online: Optional[bool] = None
        try:
            bat_mgr = get_battery_manager()
            bat_info = bat_mgr.get_battery_info()
            if bat_info.present:
                ac_online = bool(bat_info.ac_online)
                cal = evaluate_calibration_state(bat_mgr, self._config)
                if cal.get("event") in ("completed", "timeout", "auto_started"):
                    self._sync_from_external_config()
                    self._rebuild_menu()

                ac_str = "AC" if bat_info.ac_online else "Bat"
                if self._config.get("charge_topup_active"):
                    parts.append(f"Batt: {bat_info.capacity}% ({ac_str} - 100% Top-Up)")
                else:
                    parts.append(f"Batt: {bat_info.capacity}% ({ac_str})")
                self._update_battery_label(bat_info)
        except Exception:
            pass

        self._sync_status_poll_interval(ac_online, from_timer=_from_timer)

        # Restore idle-dimmed backlight immediately when AC comes online
        if (
            self._idle_dimmed
            and ac_online is True
            and self._last_ac_online is False
            and self._config.get("idle_keep_on_ac", False)
        ):
            self._on_idle_active()

        # AC / Battery & Follow-System profile automation (timer ticks only, never
        # re-entrantly inside a manual click or hotkey callback)
        if _from_timer:
            self._run_profile_automation(ac_online)

        if ac_online is not None:
            self._last_ac_online = ac_online

        text = "  |  ".join(parts) if parts else "Status: N/A"

        # Only touch GTK when the text actually changed (each set is a DBus
        # property update / shell redraw).
        if text != getattr(self, "_last_status_text", None):
            self._last_status_text = text
            for item in self._status_items:
                try:
                    item.set_label(text)
                except Exception:
                    pass
            if self._indicator is not None:
                try:
                    self._indicator.set_title(f"GigaMate 3.0\n{text}")
                except Exception:
                    pass

        return True  # keep timer alive

    def _mark_profile_synced(self, profile_id: int, grace_sec: float = 4.0) -> None:
        """Record a GigaMate-initiated profile switch and hold follow_system briefly.

        ``tlp-pd`` spawns detached ``tlp`` asynchronously on ``Set(ActiveProfile)``
        so ``Get(ActiveProfile)`` can return the previous profile for ~0.5-2 s.
        Holding ``follow_system`` for a short settle window prevents reacting to
        that stale value and snapping the profile/RGB back.
        """
        from .system_power import DEFAULT_PROFILE_MAP

        mapped = DEFAULT_PROFILE_MAP.get(int(profile_id))
        if mapped is not None:
            self._last_seen_system_profile = mapped
        self._profile_sync_until = time.monotonic() + grace_sec

    def _run_profile_automation(self, ac_online: Optional[bool]) -> None:
        """Apply AC/Battery or Follow-System power profile automation on transitions."""
        cfg = load_config()
        self._config = cfg
        ac_profile_mode = cfg.get("ac_profile_mode", "off")
        target: Optional[int] = None
        sync_sys = True

        if ac_profile_mode == "switch":
            token = power_config_mtime_token()
            if token != getattr(self, "_last_power_cfg_token", None) or not hasattr(self, "_cached_power_info"):
                self._last_power_cfg_token = token
                self._cached_power_info = detect_power_managers()

            power_info = self._cached_power_info
            conflicts = power_info.get("conflicts", [])
            notified = getattr(self, "_notified_ac_conflicts", None)
            if notified is None:
                notified = set()
                self._notified_ac_conflicts = notified

            if conflicts:
                conflict_ids = {c["id"] for c in conflicts if "id" in c}
                new_ids = conflict_ids - notified
                if new_ids:
                    notified.update(new_ids)
                    try:
                        if shutil.which("notify-send"):
                            subprocess.Popen(
                                [
                                    "notify-send",
                                    "GigaMate — External Power Manager Detected",
                                    "An external power manager re-enabled AC auto-switching. "
                                    "OS profile pushes are paused until resolved in Automation.",
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                    except Exception:
                        pass
            else:
                notified.clear()

            prev = self._last_ac_online
            if prev is not None and ac_online is not None and ac_online != prev:
                target = int(
                    cfg.get("ac_profile_id", 1)
                    if ac_online
                    else cfg.get("battery_profile_id", 0)
                )
                has_ext = power_info.get("has_external_ac_switching", False)
                sync_sys = not has_ext
        elif ac_profile_mode == "follow_system":
            try:
                sys_prof = SystemPowerManager().get_active_profile()
            except Exception:
                sys_prof = None
            if not sys_prof:
                return
            now = time.monotonic()
            if now < getattr(self, "_profile_sync_until", 0.0):
                if sys_prof == self._last_seen_system_profile:
                    self._profile_sync_until = 0.0
                return
            if self._last_seen_system_profile is None:
                self._last_seen_system_profile = sys_prof
                return
            if sys_prof != self._last_seen_system_profile:
                self._last_seen_system_profile = sys_prof
                mapped = SYSTEM_TO_GIGAMATE_PROFILE.get(sys_prof)
                # Both Performance (2) and Gaming (3) map to system "performance";
                # never snap an explicit Gaming selection back to Performance.
                already_matches = (
                    mapped == self._current_acpi_profile
                    or (sys_prof == "performance" and self._current_acpi_profile in (2, 3))
                )
                if mapped is not None and not already_matches:
                    target = int(mapped)
                    sync_sys = False

        if target in (2, 3) and not dgpu_gate.high_power_profiles_allowed():
            target = None

        if target is not None:
            try:
                hardware.apply_profile(target, cfg=cfg, sync_system=sync_sys)
                self._current_acpi_profile = target
                item = getattr(self, "_profile_items", {}).get(target)
                if item is not None:
                    old_b = self._building
                    self._building = True
                    try:
                        item.set_active(True)
                    finally:
                        self._building = old_b
            except Exception:
                pass

    # ────────────────────────────────────────────
    # Callbacks: RGB
    # ────────────────────────────────────────────

    def _get_keyboard(self):
        """Get HID LampArray keyboard device handle."""
        if self._detected_vid and self._detected_pid:
            dev = get_keyboard(vid=self._detected_vid, pid=self._detected_pid)
        else:
            dev = get_keyboard()
        if dev is None:
            self._show_no_keyboard()
            return None
        if self._no_keyboard:
            self._no_keyboard = False
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
        return dev

    def _show_no_keyboard(self) -> None:
        if not self._no_keyboard:
            self._no_keyboard = True
            try:
                self._indicator.set_label("No keyboard", APP_ID)
            except Exception:
                pass
        # Avoid stacking retry timers when _get_keyboard fails repeatedly.
        if self._keyboard_retry_timer_id is None:
            self._keyboard_retry_timer_id = GLib.timeout_add(10000, self._retry_keyboard)

    def _retry_keyboard(self) -> bool:
        """Try to re-detect keyboard. Returns False (single-shot timer)."""
        self._keyboard_retry_timer_id = None
        detected = detect_keyboards()
        if detected:
            self._no_keyboard = False
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
            self._detected_vid, self._detected_pid = detected[0]
            self._has_lamparray = hardware_supports_lamparray(
                self._detected_vid, self._detected_pid
            )
            self._full_rgb = self._has_lamparray
            self._unsupported = not self._has_lamparray
            self._init_hotkeys()
            self._rebuild_menu()
            self._apply_on_startup()
            self._init_idle()
        return False

    def _apply_colour(self) -> None:
        # While idle-dimmed the hardware stays off; only persist the
        # user's choice — it is applied on the next wake.
        if self._idle_dimmed:
            self._save_config()
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        self._apply_current_rgb(dev)
        self._save_config()

    def _apply_current_rgb(self, dev) -> None:
        if not getattr(self, "_rgb_enabled", True):
            set_off(dev)
            return
        pct = max(1, getattr(self, "_current_brightness_pct", 100))
        rgb_hex = self._current_rgb_hex or PALETTE_HEX.get(self._current_colour, "#ffffff")
        set_rgb(dev, rgb_hex, pct)

    def _on_colour_changed(self, item: Gtk.RadioMenuItem, cname: str) -> None:
        if not item.get_active() or self._building:
            return
        self._current_colour = cname
        self._current_rgb_hex = PALETTE_HEX.get(cname)
        self._rgb_enabled = True
        self._apply_colour()

    def _on_brightness_changed(self, item: Gtk.RadioMenuItem, level: int) -> None:
        if not item.get_active() or self._building:
            return
        self._current_brightness = level
        self._rgb_enabled = level > 0
        if level > 0:
            self._current_brightness_pct = {1: 25, 2: 100}.get(level, 100)
        self._apply_colour()

    def _on_brightness_pct_changed(self, item: Gtk.RadioMenuItem, pct: int) -> None:
        if not item.get_active() or self._building:
            return
        if pct <= 0:
            self._rgb_enabled = False
        else:
            self._rgb_enabled = True
            self._current_brightness_pct = pct
        self._apply_colour()

    # ────────────────────────────────────────────
    # Callbacks: ACPI / Power Profile
    # ────────────────────────────────────────────

    def _update_profile_items_sensitivity(self, gst=None) -> None:
        items = getattr(self, "_profile_items", {})
        if not items:
            return
        if gst is not None and "high_power_profiles_allowed" in gst:
            high_ok = bool(gst["high_power_profiles_allowed"])
        else:
            high_ok = dgpu_gate.high_power_profiles_allowed()
        if high_ok == getattr(self, "_high_power_allowed_cache", None):
            return
        self._high_power_allowed_cache = high_ok
        base_labels = getattr(self, "_profile_base_labels", {})
        for pid, item in items.items():
            allowed = high_ok or pid not in (2, 3)
            base = base_labels.get(pid, f"Profile {pid}")
            lbl = f"{base} (dGPU off)" if (not high_ok and pid in (2, 3)) else base
            try:
                item.set_sensitive(allowed)
                item.set_label(lbl)
            except Exception:
                pass

    def _on_profile_changed(self, item: Gtk.RadioMenuItem, profile_id: int) -> None:
        """Handle power profile radio button selection."""
        if not item.get_active() or self._building:
            return
        if self._acpi_controller is None:
            return
        if profile_id in (2, 3) and not dgpu_gate.high_power_profiles_allowed():
            prev = self._current_acpi_profile
            if prev in self._profile_items:
                old_b = self._building
                self._building = True
                try:
                    self._profile_items[prev].set_active(True)
                finally:
                    self._building = old_b
            return
        try:
            hardware.apply_profile(
                profile_id, cfg=None, sync_system=self._sync_system_power
            )
            self._current_acpi_profile = profile_id
            if self._sync_system_power:
                self._mark_profile_synced(profile_id)
            self._update_status()
        except Exception:
            pass

    def _on_hotkey_cycle_profile(self) -> None:
        """Handle hardware hotkey (e.g. the Mode key): cycle profile and show OSD."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return

        # Get available profiles
        p_data = self._get_discovered_profiles()
        if not p_data:
            return
        pids = sorted(int(k) for k in p_data.keys())
        if not dgpu_gate.high_power_profiles_allowed():
            filtered = [p for p in pids if p not in (2, 3)]
            if filtered:
                pids = filtered

        if not pids:
            return

        # Find current active profile
        current_val = self._current_acpi_profile
        if current_val is None:
            current_fp = self._acpi_controller.get_profile()
            current_val = current_fp.value if current_fp is not None else pids[0]

        try:
            curr_idx = pids.index(current_val)
            next_idx = (curr_idx + 1) % len(pids)
        except ValueError:
            next_idx = 0

        next_profile_id = pids[next_idx]
        fp = FanProfile(next_profile_id)

        try:
            if hardware.apply_profile(
                next_profile_id, cfg=None, sync_system=self._sync_system_power
            ):
                self._current_acpi_profile = next_profile_id
                if self._sync_system_power:
                    self._mark_profile_synced(next_profile_id)

                # Update the active RadioMenuItem in GUI without triggering redundant callbacks
                item = self._profile_items.get(next_profile_id)
                if item is not None:
                    self._building = True
                    item.set_active(True)
                    self._building = False

                # Show OSD
                entry = p_data.get(str(next_profile_id), {})
                name = entry.get("name", f"Profile {next_profile_id}")
                desc = entry.get("desc", "")
                show_profile_osd(name, desc)

                self._update_status()
        except Exception:
            pass

    # ────────────────────────────────────────────
    # Callbacks: Settings
    # ────────────────────────────────────────────

    def _on_sync_power_toggled(self, item: Gtk.CheckMenuItem) -> None:
        """Handle toggle for syncing system-level power profile."""
        if self._building:
            return
        self._sync_system_power = item.get_active()
        self._save_config()
        if self._sync_system_power and self._current_acpi_profile is not None:
            sync_system_power(self._current_acpi_profile)

    def _on_startup_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._building:
            return
        self._startup_apply = item.get_active()
        self._save_config()

    def _on_reload(self, *args) -> None:
        """Re-detect hardware and rebuild menu."""
        self._detect_on_startup()
        self._init_acpi()
        self._init_hotkeys()
        self._rebuild_menu()
        if not self._no_keyboard:
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
        self._init_idle()
        self._apply_on_startup()

    def _on_about(self, *args) -> None:
        """Show the About dialog."""
        version = __import__('gigamate', fromlist=['']).__version__

        if self._has_lamparray:
            secondary = (
                f"Version {version}\n\n"
                "Keyboard: HID LampArray\n\n"
                "GigaMate — Gigabyte laptop management for Linux.\n"
                "Keyboard RGB, fan monitoring, power profiles.\n\n"
                "MIT License - use at your own risk."
            )
        elif self._acpi_controller is not None and self._acpi_controller.available:
            secondary = (
                f"Version {version}\n\n"
                "ACPI hardware detected\n\n"
                "GigaMate — Gigabyte laptop management for Linux.\n"
                "Fan monitoring, power profiles, and GPU management.\n\n"
                "MIT License - use at your own risk."
            )
        elif self._unsupported:
            vid = self._detected_vid or 0
            pid = self._detected_pid or 0
            secondary = (
                f"Version {version}\n\n"
                f"Keyboard (VID={vid:04X} PID={pid:04X}) does not expose HID LampArray.\n"
                "For pre-2025 legacy 0x08 keyboards, use GigaMate v4.5.x.\n"
            )
        else:
            secondary = (
                f"Version {version}\n\n"
                "No Gigabyte hardware detected.\n\n"
                "GigaMate — Gigabyte laptop management for Linux.\n"
                "MIT License - use at your own risk."
            )
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="GigaMate",
        )
        dlg.format_secondary_text(secondary)
        dlg.run()
        dlg.destroy()

    def _on_quit(self, *args) -> None:
        """Save config and quit (restore backlight if idle-dimmed)."""
        # Re-entrancy guard: SIGTERM/SIGINT and the menu can both fire.
        if getattr(self, "_quitting", False):
            return
        self._quitting = True
        # Close GigaMate Center window if open
        try:
            import socket as py_socket
            sock_path = get_runtime_ipc_paths()[0]
            if os.path.exists(sock_path):
                with py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    s.connect(sock_path)
                    s.sendall(b"QUIT\n")
        except Exception:
            pass

        if self._idle_dimmed:
            self._idle_dimmed = False
            try:
                dev = self._get_keyboard()
                if dev is not None and self._current_brightness != 0:
                    self._apply_current_rgb(dev)
            except Exception:
                pass
        self._stop_idle()
        try:
            get_sleep_handler().stop_listening()
        except Exception:
            pass
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        if self._status_timer_id is not None:
            GLib.source_remove(self._status_timer_id)
            self._status_timer_id = None
        if self._update_timer_id is not None:
            try:
                GLib.source_remove(self._update_timer_id)
            except Exception:
                pass
            self._update_timer_id = None
        if self._keyboard_retry_timer_id is not None:
            try:
                GLib.source_remove(self._keyboard_retry_timer_id)
            except Exception:
                pass
            self._keyboard_retry_timer_id = None
        if self._config_monitor is not None:
            try:
                self._config_monitor.cancel()
            except Exception:
                pass
            self._config_monitor = None
        Gtk.main_quit()

    # ────────────────────────────────────────────
    # Config & startup
    # ────────────────────────────────────────────

    def _save_config(self) -> None:
        """Save only the settings the tray actually changed (delta write)."""
        def _mutate(cfg):
            # Write a key only when the tray's value differs from its last
            # known value, so a stale snapshot cannot clobber a concurrent
            # Center/CLI edit made within the sync window.
            pairs = (
                ("colour", self._current_colour, self._config.get("colour")),
                ("brightness", self._current_brightness, self._config.get("brightness")),
                ("rgb_hex", getattr(self, "_current_rgb_hex", self._config.get("rgb_hex")), self._config.get("rgb_hex")),
                ("brightness_pct", getattr(self, "_current_brightness_pct", self._config.get("brightness_pct")), self._config.get("brightness_pct")),
                ("rgb_enabled", getattr(self, "_rgb_enabled", self._config.get("rgb_enabled", True)), self._config.get("rgb_enabled", True)),
                ("startup_apply", self._startup_apply, self._config.get("startup_apply")),
                ("sync_system_power", self._sync_system_power, self._config.get("sync_system_power")),
                ("idle_off_enabled", self._idle_enabled, self._config.get("idle_off_enabled")),
                ("idle_timeout_sec", self._idle_timeout, self._config.get("idle_timeout_sec")),
            )
            for key, current, last in pairs:
                if current != last:
                    cfg[key] = current
            if (self._current_acpi_profile is not None
                    and self._current_acpi_profile != self._config.get("acpi_profile")):
                cfg["acpi_profile"] = self._current_acpi_profile
            if getattr(self, "_battery_dirty", False):
                cfg["charge_limit"] = self._config.get("charge_limit", cfg.get("charge_limit"))
                cfg["charge_limit_enabled"] = self._config.get(
                    "charge_limit_enabled", cfg.get("charge_limit_enabled"))
                if "charge_topup_active" in self._config:
                    cfg["charge_topup_active"] = self._config["charge_topup_active"]
            return cfg

        self._config = update_config(_mutate)
        self._battery_dirty = False
        self._last_config_mtime = self._get_config_mtime()

    def _apply_on_startup(self) -> None:
        """Apply saved settings on startup."""
        if self._current_acpi_profile is not None:
            self._config["acpi_profile"] = self._current_acpi_profile
        apply_hardware_settings(self._config)

    # ────────────────────────────────────────────
    # Menu management
    # ────────────────────────────────────────────

    def _clear_menu(self) -> None:
        """Destroy existing menu (safe for rebuild)."""
        if getattr(self, "_status_timer_id", None) is not None:
            GLib.source_remove(self._status_timer_id)
            self._status_timer_id = None
        if getattr(self, "_menu", None) is not None:
            self._menu.destroy()
            self._menu = None
        self._colour_items = {}
        self._brightness_items = {}
        self._profile_items = {}
        self._status_items = []
        self._idle_timeout_items = {}
        self._idle_parent_item = None
        self._sync_power_item = None
        self._startup_item = None
        self._battery_care_item = None
        self._battery_parent_item = None
        self._battery_parent_label = None
        self._dgpu_power_items = {}
        self._dgpu_power_parent_item = None
        self._dgpu_power_parent_label = None
        self._dgpu_power_status_item = None
        self._dgpu_power_status_label = None

    def _rebuild_menu(self) -> None:
        """Clear and rebuild the entire menu."""
        self._clear_menu()
        self._building = True
        self._build_menu()
        self._building = False
        if self._menu is not None:
            self._menu.show_all()
        self._start_status_polling()


def main() -> None:
    """Entry point for the GigaMate tray application."""
    if os.geteuid() == 0:
        print(
            "GigaMate Tray must be run as your desktop user, not root.\n"
            "Running it with sudo would create root-owned settings and cannot "
            "reach your user session. Re-run as your normal user.",
            file=sys.stderr,
        )
        sys.exit(1)

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Single-instance guard to prevent duplicate tray icons
    import fcntl
    from .paths import get_runtime_dir
    runtime_dir = get_runtime_dir()
    lock_file_path = runtime_dir / f"gigamate-tray-{os.getuid()}.lock"
    try:
        lock_fd = open(lock_file_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        # Tray already running: focus/raise GigaMate Center instead
        try:
            subprocess.Popen(["gigamate", "center"])
        except Exception:
            pass
        return

    app = GigaMateTrayApp()
    try:
        from gi.repository import GLibUnix
        GLibUnix.signal_add(0, signal.SIGTERM, lambda: (app._on_quit(), False)[1])
        GLibUnix.signal_add(0, signal.SIGINT, lambda: (app._on_quit(), False)[1])
    except Exception:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
