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

APP_ID = "gigamate"
APP_ICON = "gigamate"
BRIGHTNESS_NAMES = ["Off", "Dim", "Full"]
STATUS_POLL_INTERVAL_MS = 5000  # 5 seconds

# Find icon: prefer local path, fall back to theme name
_icon_paths = [
    Path(__file__).parent.parent.parent / "data" / "gigamate.svg",
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "gigamate.svg",
    Path("/usr/share/icons/hicolor/scalable/apps/gigamate.svg"),
]
APP_ICON_PATH = APP_ICON
for _p in _icon_paths:
    if _p.exists():
        APP_ICON_PATH = str(_p)
        break


class GigaMateTrayApp:
    """System tray application for Gigabyte laptop management."""

    def __init__(self) -> None:
        self._config = load_config()
        self._indicator: Optional[AppIndicator3.Indicator] = None
        self._menu: Optional[Gtk.Menu] = None

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

        # ACPI state
        self._acpi_controller: Optional[AcpiController] = None
        self._acpi_caps: Optional[AcpiCapabilities] = None
        self._profile_items: Dict[int, Gtk.RadioMenuItem] = {}
        self._status_items: List[Gtk.MenuItem] = []
        self._current_acpi_profile: Optional[int] = self._config.get("acpi_profile")
        self._status_timer_id: Optional[int] = None

        # Menu item references (for updating)
        self._reload_item: Optional[Gtk.MenuItem] = None

        # Radio button maps (for grid-based layouts)
        self._colour_rb_map: Dict[str, Gtk.RadioButton] = {}
        self._brightness_rb_map: Dict[int, Gtk.RadioButton] = {}
        self._profile_rb_map: Dict[int, Gtk.RadioButton] = {}

        self._building = True
        self._detect_on_startup()
        self._init_acpi()
        self._build_menu()
        self._building = False
        self._apply_on_startup()
        self._start_status_polling()

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
                # Apply saved ACPI profile if configured
                if self._current_acpi_profile is not None:
                    try:
                        self._acpi_controller.set_profile(
                            FanProfile(self._current_acpi_profile)
                        )
                    except Exception:
                        pass
            else:
                self._acpi_controller = None
                self._acpi_caps = None
        else:
            self._acpi_controller = None
            self._acpi_caps = None

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
            self._indicator.set_menu(self._menu)
            self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

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
        self._append_power_profile_section()
        self._append_status_section()
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
        """Full menu: Status on top, then Profiles, Colours (grid), Brightness (grid)."""
        # ── Live Status section (ACPI) — always top ──
        self._append_status_section()

        # ── Power Profile section (ACPI) — 2-column grid ──
        self._append_power_profile_section()

        # ── Keyboard Colours section — 2-column grid ──
        if self._profile is not None and self._profile.has_rgb:
            self._menu.append(Gtk.SeparatorMenuItem())
            colour_header = Gtk.MenuItem(label="Colour")
            colour_header.set_sensitive(False)
            self._menu.append(colour_header)

            colours = self._profile.colour_names
            items = [(cname, cname.replace("_", " ").title()) for cname in colours]
            active = self._current_colour

            grid_item = self._build_radio_grid(items, 2, self._on_colour_grid_changed, active)
            self._menu.append(grid_item)

        # ── Keyboard Brightness section — 3-column grid ──
        if self._profile is not None and self._profile.has_rgb:
            self._menu.append(Gtk.SeparatorMenuItem())
            bright_header = Gtk.MenuItem(label="Brightness")
            bright_header.set_sensitive(False)
            self._menu.append(bright_header)

            items = [(level, label) for level, label in enumerate(BRIGHTNESS_NAMES)]
            active = self._current_brightness

            grid_item = self._build_radio_grid(items, 3, self._on_brightness_grid_changed, active)
            self._menu.append(grid_item)

        # ── Settings items ──
        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_settings_items()

        self._menu.append(Gtk.SeparatorMenuItem())
        self._append_about()
        self._append_quit()

    def _on_colour_grid_changed(self, cname: str) -> None:
        """Handle colour selection from grid."""
        self._current_colour = cname
        self._apply_colour()

    def _on_brightness_grid_changed(self, level: int) -> None:
        """Handle brightness selection from grid."""
        self._current_brightness = level
        self._apply_colour()

    # ────────────────────────────────────────────
    # Section builders
    # ────────────────────────────────────────────

    def _build_radio_grid(
        self,
        items: List[tuple],
        columns: int,
        callback,
        active_key=None,
    ) -> Gtk.MenuItem:
        """Build a GtkMenuItem containing a GtkGrid of radio buttons.

        Args:
            items: list of (key, label) tuples
            columns: number of columns
            callback: function(key) called when selected
            active_key: key of the initially active button
        Returns:
            GtkMenuItem with embedded GtkGrid
        """
        rows = (len(items) + columns - 1) // columns

        grid = Gtk.Grid()
        grid.set_column_spacing(6)
        grid.set_row_spacing(0)
        grid.set_margin_start(4)
        grid.set_margin_end(4)

        first_rb = None

        for idx, (key, label) in enumerate(items):
            row = idx // columns
            col = idx % columns

            rb = Gtk.RadioButton.new_with_label_from_widget(first_rb, label)
            if first_rb is None:
                first_rb = rb

            if active_key is not None and key == active_key:
                rb.set_active(True)

            rb.connect("toggled", self._on_radio_grid_toggled, key, callback)
            grid.attach(rb, col, row, 1, 1)

        item = Gtk.MenuItem()
        item.add(grid)
        return item

    def _on_radio_grid_toggled(self, button: Gtk.RadioButton, key, callback) -> None:
        """Handle a grid radio button toggle. Fire callback if active, close menu."""
        if not button.get_active():
            return
        if self._building:
            return
        try:
            callback(key)
        except Exception:
            pass
        # Close the menu
        if self._menu is not None:
            self._menu.deactivate()

    def _append_power_profile_section(self) -> None:
        """Add Power Profile as a 2-column radio grid."""
        if self._acpi_caps is None or not self._acpi_caps.has_power_profiles:
            return

        self._menu.append(Gtk.SeparatorMenuItem())
        header = Gtk.MenuItem(label="Power Profile")
        header.set_sensitive(False)
        self._menu.append(header)

        # Get profile names
        if self._profile is not None and self._profile.has_acpi and self._profile.acpi:
            profile_names = self._profile.acpi.profiles
        else:
            profile_names = {
                str(int(v)): {"name": k.capitalize(), "desc": ""}
                for k, v in FanProfile.names().items()
            }

        items = []
        active_key = None
        for pid_int in sorted(int(k) for k in profile_names.keys()):
            entry = profile_names.get(str(pid_int), {})
            label = entry.get("name", f"Profile {pid_int}")
            items.append((pid_int, label))
            if self._current_acpi_profile == pid_int:
                active_key = pid_int

        if not items:
            return

        grid_item = self._build_radio_grid(items, 2, self._on_profile_grid_changed, active_key)
        self._menu.append(grid_item)

    def _on_profile_grid_changed(self, profile_id: int) -> None:
        """Handle profile selection from grid."""
        if self._acpi_controller is None:
            return
        self._acpi_controller.set_profile(FanProfile(profile_id))
        self._current_acpi_profile = profile_id
        self._save_config()

    def _append_status_section(self) -> None:
        """Add a single-line live status display (only if ACPI is available)."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return

        self._status_items = []
        item = Gtk.MenuItem(label="ACPI initialising...")
        item.set_sensitive(False)
        self._menu.append(item)
        self._status_items.append(item)

    def _append_settings_items(self) -> None:
        """Add settings items at the bottom of the menu."""
        self._startup_item = Gtk.CheckMenuItem(label="Apply on startup")
        self._startup_item.set_active(self._startup_apply)
        self._startup_item.connect("toggled", self._on_startup_toggled)
        self._menu.append(self._startup_item)

        reload_item = Gtk.MenuItem(label="Reload profiles")
        reload_item.connect("activate", self._on_reload)
        self._menu.append(reload_item)

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
    # Status polling
    # ────────────────────────────────────────────

    def _start_status_polling(self) -> None:
        """Start the periodic status update timer."""
        if self._acpi_controller is not None and self._acpi_controller.available:
            self._update_status()
            self._status_timer_id = GLib.timeout_add(
                STATUS_POLL_INTERVAL_MS, self._update_status
            )

    def _update_status(self) -> bool:
        """Poll ACPI sensors and update the status labels. Returns True to keep timer alive."""
        if not self._status_items:
            return False
        if self._acpi_controller is None or not self._acpi_controller.available:
            return False

        state: Optional[FanState] = None
        try:
            state = self._acpi_controller.read_state()
        except Exception:
            pass

        if state is None:
            for item in self._status_items:
                item.set_label("Status: N/A")
            return True

        # Build status text based on available data
        parts = []

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

        text = "  |  ".join(parts) if parts else "ACPI: no data"

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
                self._rebuild_menu()
                self._apply_on_startup()
        return False

    def _apply_colour(self) -> None:
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
            self._save_config()
        except Exception:
            pass

    # ────────────────────────────────────────────
    # Callbacks: Settings
    # ────────────────────────────────────────────

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
            # Re-init ACPI (profile may have changed)
            self._init_acpi()
            self._rebuild_menu()
            try:
                self._indicator.set_label("", APP_ID)
            except Exception:
                pass
        elif self._unsupported:
            # Re-init ACPI anyway (may work without profile)
            if self._acpi_controller is None:
                ctrl = AcpiController()
                if ctrl.available:
                    self._acpi_controller = ctrl
                    self._acpi_caps = ctrl.capabilities
            pass
        else:
            self._profile = None
            self._unsupported = True
            self._acpi_controller = None
            self._acpi_caps = None
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
        """Save config and quit."""
        self._save_config()
        if self._status_timer_id is not None:
            GLib.source_remove(self._status_timer_id)
            self._status_timer_id = None
        Gtk.main_quit()

    # ────────────────────────────────────────────
    # Config & startup
    # ────────────────────────────────────────────

    def _save_config(self) -> None:
        """Save current settings to config file."""
        self._config["colour"] = self._current_colour
        self._config["brightness"] = self._current_brightness
        self._config["startup_apply"] = self._startup_apply
        if self._current_acpi_profile is not None:
            self._config["acpi_profile"] = self._current_acpi_profile
        save_config(self._config)

    def _apply_on_startup(self) -> None:
        """Apply saved settings on startup."""
        if not self._startup_apply:
            return
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
        self._colour_rb_map = {}
        self._brightness_rb_map = {}
        self._profile_rb_map = {}
        self._profile_items = {}
        self._status_items = []

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
