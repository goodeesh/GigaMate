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
import signal
import threading
import random
from typing import Optional, Dict, List

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AppIndicator3

from pathlib import Path

from .protocol import set_static, set_off, get_keyboard
from .profiles import (
    detect_device, resolve_profile, save_user_profile, DeviceProfile,
)
from .config import load as load_config, save as save_config
from .acpi import (
    AcpiController, FanProfile, FanState, AcpiCapabilities,
)
from .hotkeys import HotkeyListener
from .gpu import gpu_icon_key
from .paths import ICON_PATHS
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
from .system_power import sync_system_power, is_system_power_available
from .gpu import get_gpu_state, gpu_short_status_text
from . import updates as update_checker

APP_ID = "gigamate"
APP_ICON = "gigamate"
BRIGHTNESS_NAMES = ["Off", "Dim", "Full"]
STATUS_POLL_INTERVAL_MS = 5000  # 5 seconds
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
        self._icon_key = "gigamate"

        # Keyboard state
        self._profile: Optional[DeviceProfile] = None
        self._unsupported = False
        self._no_keyboard = False
        self._detected_vid: Optional[int] = None
        self._detected_pid: Optional[int] = None

        # RGB state
        self._colour_items: Dict[str, Gtk.RadioMenuItem] = {}
        self._brightness_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._current_colour = self._config.get("colour", "light_purple")
        self._current_brightness = self._config.get("brightness", 2)
        self._startup_item: Optional[Gtk.CheckMenuItem] = None
        self._startup_apply = self._config.get("startup_apply", True)
        self._sync_system_power = self._config.get("sync_system_power", True)
        self._sync_power_item: Optional[Gtk.CheckMenuItem] = None

        # ACPI state
        self._acpi_controller: Optional[AcpiController] = None
        self._acpi_caps: Optional[AcpiCapabilities] = None
        self._profile_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._status_items: List[Gtk.MenuItem] = []
        self._current_acpi_profile: Optional[int] = self._config.get("acpi_profile")
        self._status_timer_id: Optional[int] = None

        # Hotkey listener state
        self._hotkey_listener: Optional[HotkeyListener] = None

        # Keyboard idle auto-off state
        self._idle_enabled = bool(self._config.get("idle_off_enabled", True))
        self._idle_timeout = clamp_timeout(
            self._config.get("idle_timeout_sec", DEFAULT_TIMEOUT_SEC))
        self._idle_dimmed = False
        self._idle_monitor: Optional[IdleMonitor] = None
        self._idle_fallback_timer_id: Optional[int] = None
        self._idle_parent_item: Optional[Gtk.MenuItem] = None
        self._idle_timeout_items: Dict[int, Gtk.RadioMenuItem] = {}

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
        self._start_status_polling()
        self._init_update_check()

    # ────────────────────────────────────────────
    # Initialisation
    # ────────────────────────────────────────────

    def _detect_on_startup(self) -> None:
        """Detect keyboard hardware and load profile."""
        detected = detect_device()
        if detected is None:
            self._no_keyboard = True
            return
        self._detected_vid, self._detected_pid = detected
        profile = resolve_profile(self._detected_vid, self._detected_pid)
        if profile is not None:
            self._profile = profile
            self._unsupported = False
            colours = self._profile.colour_names
            if colours:
                self._current_colour = self._config.get("colour", colours[0])
        else:
            self._profile = None
            self._unsupported = True

    def _init_acpi(self) -> None:
        """Initialise ACPI controller from the loaded profile."""
        if self._profile is not None and self._profile.has_acpi:
            self._acpi_controller = AcpiController()
            if self._acpi_controller.available:
                self._acpi_caps = self._acpi_controller.capabilities
            else:
                self._acpi_controller = None
                self._acpi_caps = None
        else:
            self._acpi_controller = None
            self._acpi_caps = None

    def _init_hotkeys(self) -> None:
        """Initialise and start background hotkey listener."""
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None

        vid = self._profile.vid if self._profile else (self._detected_vid or 0x0414)
        pid = self._profile.pid if self._profile else (self._detected_pid or 0x8105)

        self._hotkey_listener = HotkeyListener(
            on_mode_switch=self._on_hotkey_cycle_profile,
            vid=vid,
            pid=pid,
        )
        self._hotkey_listener.start()

    # ────────────────────────────────────────────
    # Keyboard idle auto-off
    # ────────────────────────────────────────────

    def _init_idle(self) -> None:
        """Start event-driven idle monitoring (evdev) or D-Bus fallback."""
        self._stop_idle()
        self._idle_dimmed = False
        if not self._idle_enabled:
            return
        if self._no_keyboard and self._profile is None:
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
        dev = self._get_keyboard()
        if dev is None:
            return
        try:
            set_off(dev, self._profile)
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
            set_static(dev, self._current_colour,
                       self._current_brightness, self._profile)
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

    def _update_idle_parent_label(self) -> None:
        if self._idle_parent_item is not None:
            try:
                self._idle_parent_item.set_label(
                    f"Idle timeout: {_idle_step_label(self._idle_effective_step())}")
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
                set_static(dev, self._current_colour,
                           self._current_brightness, self._profile)
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

    def _append_idle_section(self) -> None:
        """Add idle auto-off submenu (Off/10s/30s/1m/2m, RGB menus only)."""
        if self._profile is None or not self._profile.has_rgb:
            return
        header = Gtk.MenuItem(label="Backlight idle off")
        header.set_sensitive(False)
        self._menu.append(header)

        self._idle_parent_item = Gtk.MenuItem(
            label=f"Idle timeout: {_idle_step_label(self._idle_effective_step())}")
        submenu = Gtk.Menu()
        self._idle_parent_item.set_submenu(submenu)
        self._menu.append(self._idle_parent_item)

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

        if self._indicator is None:
            self._indicator = AppIndicator3.Indicator.new(
                APP_ID,
                APP_ICON_PATH,
                AppIndicator3.IndicatorCategory.HARDWARE,
            )
            self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_menu(self._menu)

    def _build_no_hardware_menu(self) -> None:
        """Menu when no Gigabyte hardware is detected at all."""
        item = Gtk.MenuItem(label="No Gigabyte hardware detected")
        item.set_sensitive(False)
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_settings_items()
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_about()
        self._append_quit()

    def _build_acpi_only_menu(self) -> None:
        """Menu when ACPI is available but keyboard is not found."""
        self._append_status_section()
        self._append_power_profile_section()
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_settings_items()
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_about()
        self._append_quit()

    def _build_unsupported_menu(self) -> None:
        """Menu when keyboard is found but not in the profile database."""
        vid = self._detected_vid or 0
        pid = self._detected_pid or 0

        header = Gtk.MenuItem(label=f"Unknown model ({vid:04X}:{pid:04X})")
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

        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_settings_items()
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_about()
        self._append_quit()

    def _build_supported_menu(self) -> None:
        """Full menu: Status on top, then Profiles, then Colours, Brightness."""
        # ── Live Status section (ACPI) — always on top ──
        self._append_status_section()

        # ── Power Profile section (ACPI) ──
        self._append_power_profile_section()

        # ── Keyboard RGB section ──
        if self._profile is not None and self._profile.has_rgb:
            self._menu.append(Gtk.SeparatorMenuItem())
            colour_header = Gtk.MenuItem(label="Colour")
            colour_header.set_sensitive(False)
            self._menu.append(colour_header)

            colour_group = None
            for cname in self._profile.colour_names:
                label = cname.replace("_", " ").title()
                item = Gtk.RadioMenuItem(group=colour_group, label=label)
                if colour_group is None:
                    colour_group = item
                if cname == self._current_colour:
                    item.set_active(True)
                item.connect("toggled", self._on_colour_changed, cname)
                self._menu.append(item)
                self._colour_items[cname] = item

            self._menu.append(Gtk.SeparatorMenuItem())

            brightness_header = Gtk.MenuItem(label="Brightness")
            brightness_header.set_sensitive(False)
            self._menu.append(brightness_header)

            bright_group = None
            for level, label in enumerate(BRIGHTNESS_NAMES):
                item = Gtk.RadioMenuItem(group=bright_group, label=label)
                if bright_group is None:
                    bright_group = item
                if level == self._current_brightness:
                    item.set_active(True)
                item.connect("toggled", self._on_brightness_changed, level)
                self._menu.append(item)
                self._brightness_items[level] = item

            self._menu.append(Gtk.SeparatorMenuItem())

        # ── Keyboard idle auto-off section ──
        self._append_idle_section()

        # ── Settings items ──
        self._append_settings_items()

        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_about()
        self._append_quit()

    # ────────────────────────────────────────────
    # Section builders
    # ────────────────────────────────────────────

    def _append_power_profile_section(self) -> None:
        """Add Power Profile radio group (only if ACPI has power profiles)."""
        if self._acpi_caps is None or not self._acpi_caps.has_power_profiles:
            return

        self._menu.append(Gtk.SeparatorMenuItem())
        header = Gtk.MenuItem(label="Power Profile")
        header.set_sensitive(False)
        self._menu.append(header)

        # Get profile names: from AcpiConfig if available, otherwise use defaults
        if self._profile is not None and self._profile.has_acpi and self._profile.acpi:
            profile_names = self._profile.acpi.profiles
        else:
            profile_names = {
                str(int(v)): {"name": k.capitalize(), "desc": ""}
                for k, v in FanProfile.names().items()
            }

        group = None
        self._profile_items = {}

        for pid_int in sorted(int(k) for k in profile_names.keys()):
            pid_str = str(pid_int)
            entry = profile_names.get(pid_str, {})
            label = entry.get("name", f"Profile {pid_int}")
            item = Gtk.RadioMenuItem(group=group, label=label)
            if group is None:
                group = item
            if self._current_acpi_profile == pid_int:
                item.set_active(True)
            item.connect("toggled", self._on_profile_changed, pid_int)
            self._menu.append(item)
            self._profile_items[pid_int] = item

    def _append_status_section(self) -> None:
        """Add a single-line live status display (ACPI sensors and/or dGPU state)."""
        acpi_ok = self._acpi_controller is not None and self._acpi_controller.available
        gpu_present = get_gpu_state().present
        if not acpi_ok and not gpu_present:
            return

        self._menu.append(Gtk.SeparatorMenuItem())
        self._status_items = []
        item = Gtk.MenuItem(label="Status: reading...")
        item.set_sensitive(False)
        self._menu.append(item)
        self._status_items.append(item)

    def _append_settings_items(self) -> None:
        """Add settings items at the bottom of the menu."""
        if is_system_power_available():
            self._sync_power_item = Gtk.CheckMenuItem(label="Sync system power profile")
            self._sync_power_item.set_active(self._sync_system_power)
            self._sync_power_item.connect("toggled", self._on_sync_power_toggled)
            self._menu.append(self._sync_power_item)

        self._startup_item = Gtk.CheckMenuItem(label="Apply on startup")
        self._startup_item.set_active(self._startup_apply)
        self._startup_item.connect("toggled", self._on_startup_toggled)
        self._menu.append(self._startup_item)

        reload_item = Gtk.MenuItem(label="Reload profiles")
        reload_item.connect("activate", self._on_reload)
        self._menu.append(reload_item)

        check_item = Gtk.MenuItem(
            label=update_checker.menu_item_label(self._update_available))
        if self._update_available and self._latest_version:
            check_item.connect("activate", self._on_update_clicked)
        else:
            check_item.connect("activate", self._on_check_updates_clicked)
        self._menu.append(check_item)

    def _append_about(self) -> None:
        about_item = Gtk.MenuItem(label="About")
        about_item.connect("activate", self._on_about)
        self._menu.append(about_item)

    def _append_quit(self) -> None:
        self._menu.append(Gtk.SeparatorMenuItem())
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
        """Confirm dialog, then background self-update on approval."""
        current = update_checker.get_installed_version()
        latest = self._latest_version or "latest"
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Update available ({current} → {latest})",
        )
        dlg.format_secondary_text(
            "GigaMate will update in the background (re-runs install.sh "
            "for the latest tagged release, including drivers and tray).\n\n"
            "Update now?")
        response = dlg.run()
        dlg.destroy()
        if response != Gtk.ResponseType.YES:
            return
        if self._latest_version:
            update_checker.dismiss_version(self._latest_version)
        try:
            GLib.spawn_async(update_checker.build_update_command(),
                             flags=GLib.SpawnFlags.SEARCH_PATH)
        except Exception:
            pass
        info = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Updating in background",
        )
        info.format_secondary_text(
            "The update is running. The tray will restart automatically "
            "when install.sh finishes (systemd service restart).")
        info.run()
        info.destroy()

    def _on_check_updates_clicked(self, *args) -> None:
        """Manual 'Check for updates' — forced network check + result dialog."""
        try:
            result = update_checker.check_for_updates(force=True)
        except Exception:
            result = {"current": "?", "latest": None,
                      "update_available": False}
        # Feed through the normal path so icon + menu refresh.
        try:
            self._on_update_result(result)
        except Exception:
            pass
        if result.get("update_available"):
            self._on_update_clicked()
            return
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

    # ────────────────────────────────────────────
    # Status polling
    # ────────────────────────────────────────────

    def _start_status_polling(self) -> None:
        """Start the periodic status update timer."""
        acpi_ok = self._acpi_controller is not None and self._acpi_controller.available
        gpu_present = get_gpu_state().present
        if acpi_ok or gpu_present:
            self._update_status()
            self._status_timer_id = GLib.timeout_add(
                STATUS_POLL_INTERVAL_MS, self._update_status
            )

    def _update_gpu_icon(self) -> None:
        """Legacy alias: refresh combined dGPU + update badge icon."""
        self._refresh_tray_icon()

    def _tray_icon_key(self) -> str:
        """Combine dGPU state with update badge into one of 6 icon keys."""
        base = gpu_icon_key(get_gpu_state())
        if self._update_available:
            return f"{base}-update" if base != "gigamate" else "gigamate-update"
        return base

    def _refresh_tray_icon(self) -> None:
        """Switch tray icon to match dGPU + update state (change-only)."""
        key = self._tray_icon_key()
        if key == self._icon_key:
            return
        self._icon_key = key
        if self._indicator is None:
            return
        try:
            self._indicator.set_icon_full(
                APP_ICON_PATHS.get(key, APP_ICON_PATH), f"GigaMate {key}")
        except Exception:
            pass

    def _update_status(self) -> bool:
        """Poll ACPI sensors and dGPU state, updating the status labels. Returns True to keep timer alive."""
        self._refresh_tray_icon()
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
                if state.profile is not None and self._profile is not None and self._profile.acpi:
                    profiles = self._profile.acpi.profiles
                    entry = profiles.get(str(state.profile.value), {})
                    pname = entry.get("name", str(state.profile.value))
                    parts.append(f"Profile: {pname}")

        # Discrete GPU state
        gpu = get_gpu_state()
        if gpu.present:
            parts.append(f"dGPU: {gpu_short_status_text(gpu)}")

        text = "  |  ".join(parts) if parts else "Status: N/A"

        for item in self._status_items:
            try:
                item.set_label(text)
            except Exception:
                pass

        return True  # keep timer alive

    # ────────────────────────────────────────────
    # Callbacks: RGB
    # ────────────────────────────────────────────

    def _get_keyboard(self):
        """Get USB keyboard device handle."""
        if self._profile is not None:
            dev = get_keyboard(
                vid=self._profile.vid, pid=self._profile.pid, profile=self._profile
            )
        elif self._detected_vid and self._detected_pid:
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
        GLib.timeout_add(10000, self._retry_keyboard)

    def _retry_keyboard(self) -> bool:
        """Try to re-detect keyboard. Returns False (single-shot timer)."""
        detected = detect_device()
        if detected is not None:
            self._no_keyboard = False
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
            self._detected_vid, self._detected_pid = detected
            profile = resolve_profile(self._detected_vid, self._detected_pid)
            if profile is not None:
                self._profile = profile
                self._unsupported = False
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
        if self._current_brightness == 0:
            set_off(dev, self._profile)
        else:
            set_static(dev, self._current_colour, self._current_brightness, self._profile)
        self._save_config()

    def _on_colour_changed(self, item: Gtk.RadioMenuItem, cname: str) -> None:
        if not item.get_active() or self._building:
            return
        self._current_colour = cname
        self._apply_colour()

    def _on_brightness_changed(self, item: Gtk.RadioMenuItem, level: int) -> None:
        if not item.get_active() or self._building:
            return
        self._current_brightness = level
        self._apply_colour()

    def _on_unsupported_off(self, item: Gtk.RadioMenuItem) -> None:
        if not item.get_active() or self._building:
            return
        dev = self._get_keyboard()
        if dev is not None:
            set_off(dev)

    # ────────────────────────────────────────────
    # Callbacks: ACPI / Power Profile
    # ────────────────────────────────────────────

    def _on_profile_changed(self, item: Gtk.RadioMenuItem, profile_id: int) -> None:
        """Handle power profile radio button selection."""
        if not item.get_active() or self._building:
            return
        if self._acpi_controller is None:
            return
        try:
            self._acpi_controller.set_profile(FanProfile(profile_id))
            self._current_acpi_profile = profile_id
            if self._sync_system_power:
                sync_system_power(profile_id)
            self._save_config()
            self._update_status()
        except Exception:
            pass

    def _on_hotkey_cycle_profile(self) -> None:
        """Handle hardware hotkey (e.g. F7): cycle power profile and display OSD."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return

        # Get available profiles
        if self._profile is not None and self._profile.has_acpi and self._profile.acpi:
            pids = sorted(int(k) for k in self._profile.acpi.profiles.keys())
            p_data = self._profile.acpi.profiles
        else:
            pids = [0, 1, 2, 3]
            p_data = {
                str(int(v)): {"name": k.capitalize(), "desc": ""}
                for k, v in FanProfile.names().items()
            }

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
            if self._acpi_controller.set_profile(fp):
                self._current_acpi_profile = next_profile_id
                if self._sync_system_power:
                    sync_system_power(next_profile_id)
                self._save_config()

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
        self._sync_system_power = item.get_active()
        self._save_config()
        if self._sync_system_power and self._current_acpi_profile is not None:
            sync_system_power(self._current_acpi_profile)

    def _on_startup_toggled(self, item: Gtk.CheckMenuItem) -> None:
        self._startup_apply = item.get_active()
        self._save_config()

    def _on_calibrate_rgb(self, *args) -> None:
        """Launch keyboard RGB calibration in a terminal."""
        terminal_cmds = [
            ("gnome-terminal", ["gnome-terminal", "--", "gigamate", "calibrate", "rgb"]),
            ("konsole", ["konsole", "-e", "gigamate", "calibrate", "rgb"]),
            ("xfce4-terminal", ["xfce4-terminal", "-e", "gigamate", "calibrate", "rgb"]),
            ("lxterminal", ["lxterminal", "-e", "gigamate", "calibrate", "rgb"]),
            ("x-terminal-emulator", ["x-terminal-emulator", "-e", "gigamate", "calibrate", "rgb"]),
        ]
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        argv = None
        for term, cmd in terminal_cmds:
            if desktop and term in desktop:
                argv = cmd
                break
        if argv is None:
            argv = ["gigamate", "calibrate", "rgb"]
        try:
            pid, *_ = GLib.spawn_async(
                argv,
                flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
            )
            GLib.child_watch_add(pid, self._on_calibrate_done)
        except GLib.GError:
            self._show_calibrate_fallback()

    def _on_calibrate_done(self, pid: int, status: int) -> None:
        if status == 0:
            self._on_reload()

    def _show_calibrate_fallback(self) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Calibration launcher",
        )
        dlg.format_secondary_text(
            "Could not open a terminal automatically.\n\n"
            "Please open a terminal and run:\n"
            "  gigamate calibrate rgb\n\n"
            "Then click 'Reload profiles' in the tray menu."
        )
        dlg.run()
        dlg.destroy()

    def _on_reload(self, *args) -> None:
        """Reload profiles and re-detect hardware."""
        # Re-detect keyboard
        if self._no_keyboard:
            detected = detect_device()
            if detected is None:
                return
            self._no_keyboard = False
            self._detected_vid, self._detected_pid = detected

        if self._unsupported or self._no_keyboard:
            detected = detect_device()
            if detected is not None:
                self._detected_vid, self._detected_pid = detected

        profile = resolve_profile(self._detected_vid, self._detected_pid) \
            if self._detected_vid else resolve_profile()

        if profile is not None:
            self._profile = profile
            self._unsupported = False
            self._no_keyboard = False
            colours = self._profile.colour_names
            if colours:
                self._current_colour = self._config.get("colour", colours[0])
            # Re-init ACPI & Hotkeys (profile may have changed)
            self._init_acpi()
            self._init_hotkeys()
            self._rebuild_menu()
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
            self._init_idle()
        elif self._unsupported:
            # Re-init ACPI anyway (may work without profile)
            if self._acpi_controller is None:
                ctrl = AcpiController()
                if ctrl.available:
                    self._acpi_controller = ctrl
                    self._acpi_caps = ctrl.capabilities
            self._init_hotkeys()
        else:
            self._profile = None
            self._unsupported = True
            self._acpi_controller = None
            self._acpi_caps = None
            self._init_hotkeys()
            self._rebuild_menu()

        self._apply_on_startup()

    def _on_reset(self, *args) -> None:
        """Re-attach kernel keyboard drivers."""
        dev = self._get_keyboard()
        if dev is None:
            return
        for i in [0, 2, 4]:
            try:
                dev.attach_kernel_driver(i)
            except Exception:
                pass

    def _on_about(self, *args) -> None:
        """Show the About dialog."""
        version = __import__('gigamate', fromlist=['']).__version__

        if self._profile is not None:
            secondary = (
                f"Version {version}\n\n"
                f"Profile: {self._profile.name}\n\n"
                "GigaMate — Gigabyte laptop management for Linux.\n"
                "Keyboard RGB, fan monitoring, power profiles.\n\n"
                "MIT License - use at your own risk."
            )
        elif self._unsupported:
            vid = self._detected_vid or 0
            pid = self._detected_pid or 0
            secondary = (
                f"Version {version}\n\n"
                f"Your keyboard (VID={vid:04X} PID={pid:04X})\n"
                "isn't in our profile database yet.\n\n"
                "Run Calibrate... to add support."
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
        if self._idle_dimmed:
            self._idle_dimmed = False
            try:
                dev = self._get_keyboard()
                if dev is not None and self._current_brightness != 0:
                    set_static(dev, self._current_colour,
                               self._current_brightness, self._profile)
            except Exception:
                pass
        self._save_config()
        self._stop_idle()
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
        Gtk.main_quit()

    # ────────────────────────────────────────────
    # Config & startup
    # ────────────────────────────────────────────

    def _save_config(self) -> None:
        """Save current settings to config file."""
        self._config["colour"] = self._current_colour
        self._config["brightness"] = self._current_brightness
        self._config["startup_apply"] = self._startup_apply
        self._config["sync_system_power"] = self._sync_system_power
        self._config["idle_off_enabled"] = self._idle_enabled
        self._config["idle_timeout_sec"] = self._idle_timeout
        if self._current_acpi_profile is not None:
            self._config["acpi_profile"] = self._current_acpi_profile
        save_config(self._config)

    def _apply_on_startup(self) -> None:
        """Apply saved settings on startup."""
        if not self._startup_apply:
            return
        if self._acpi_controller is not None and self._current_acpi_profile is not None:
            try:
                self._acpi_controller.set_profile(
                    FanProfile(self._current_acpi_profile)
                )
                if self._sync_system_power:
                    sync_system_power(self._current_acpi_profile)
            except Exception:
                pass
        if self._unsupported or self._no_keyboard:
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        if self._current_brightness == 0:
            set_off(dev, self._profile)
        else:
            set_static(dev, self._current_colour, self._current_brightness, self._profile)

    # ────────────────────────────────────────────
    # Menu management
    # ────────────────────────────────────────────

    def _clear_menu(self) -> None:
        """Destroy existing menu (safe for rebuild)."""
        if self._status_timer_id is not None:
            GLib.source_remove(self._status_timer_id)
            self._status_timer_id = None
        if self._menu is not None:
            self._menu.destroy()
            self._menu = None
        self._colour_items = {}
        self._brightness_items = {}
        self._profile_items = {}
        self._status_items = []
        self._idle_timeout_items = {}
        self._idle_parent_item = None

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
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    GigaMateTrayApp()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
