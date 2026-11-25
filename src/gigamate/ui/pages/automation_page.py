"""GigaMate Center — Automation Page (Unified Trigger -> Action Hub).

Allows configuring:
- ON AC / BATTERY: Power source & dGPU Auto-Gate rules
- ON PROFILE SWITCH: Profile-linked keyboard lighting & Linux system power sync
- ON INACTIVITY: Keyboard idle sleep timeout & AC keep-alive
- ON BOOT & SCHEDULE: Startup apply, GPU tuning auto-apply & periodic BMS calibration
"""

import time
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import dgpu_gate, dgpu_tune
from ...battery import get_battery_manager
from ...capabilities import detect_system_capabilities
from ...config import (
    DEFAULT_CONFIG,
    enabled_acpi_profiles,
    load as load_config,
    profile_rgb_for,
    update_config,
)
from ...idle import IDLE_TIMEOUT_STEPS, nearest_idle_step
from ...system_power import (
    deactivate_ac_conflicts,
    detect_power_managers,
    has_handoff_to_restore,
    restore_ac_conflicts,
    sync_system_power,
)
from ..dgpu_gate_dialog import DgpuGateConfirmDialog
from ..theme import THEME
from ..widgets import (
    M3Banner,
    M3Card,
    M3ComboBox,
    M3IconHelpButton,
    M3PageHeader,
    M3SegmentedBar,
    M3SwitchRow,
)

_PROFILE_NAMES = (
    (0, "Quiet"),
    (1, "Balanced"),
    (2, "Performance"),
    (3, "Gaming"),
)

_AC_MODES = (
    (
        "off",
        "Off",
        "Manual control only",
        "GigaMate will not automatically change your power profile when plugging or unplugging AC. "
        "Use this if you prefer to switch profiles manually or let TLP / KDE / GNOME manage power "
        "without GigaMate mirroring or overriding them.",
    ),
    (
        "follow_system",
        "Follow System",
        "Mirror TLP / KDE / GNOME",
        "GigaMate watches your OS power manager (TLP, KDE PowerDevil, or GNOME power-profiles-daemon) "
        "and mirrors its AC/Battery profile changes into Gigabyte EC & keyboard RGB. "
        "Manual switches in GigaMate still apply until the next plug/unplug.",
    ),
    (
        "switch",
        "GigaMate-Driven",
        "GigaMate owns AC / Battery",
        "GigaMate takes full control of AC and Battery profile switching, updating both Gigabyte EC "
        "(including Gaming on AC) and your OS power backend automatically on plug/unplug. "
        "Best when no conflicting external AC switcher is active.",
    ),
)


class _AcModeCard(QFrame):
    """Clickable Material 3 card representing one AC / Battery power profile mode."""

    clicked = pyqtSignal()

    def __init__(
        self,
        mode_key: str,
        title: str,
        subtitle: str,
        tooltip: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.mode_key = mode_key
        self._selected = False
        self._recommended = False

        self.setObjectName(f"AcModeCard_{mode_key}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.title_lbl = QLabel(title)
        top_row.addWidget(self.title_lbl)

        self.recommended_lbl = QLabel("Recommended")
        self.recommended_lbl.setStyleSheet(
            f"color: {THEME.primary}; background-color: {THEME.primary_container}; "
            f"border-radius: 6px; padding: 1px 6px; font-size: 10px; font-weight: 600;"
        )
        self.recommended_lbl.setVisible(False)
        top_row.addWidget(self.recommended_lbl)

        top_row.addStretch()

        self.info_badge = QLabel("i")
        self.info_badge.setFixedSize(18, 18)
        self.info_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.info_badge.setToolTip(tooltip)
        self.info_badge.setStyleSheet(
            f"color: {THEME.on_surface_variant}; border: 1px solid {THEME.outline_variant}; "
            "border-radius: 9px; font-size: 11px; font-weight: 600; background: transparent;"
        )
        top_row.addWidget(self.info_badge)

        layout.addLayout(top_row)

        self.subtitle_lbl = QLabel(subtitle)
        self.subtitle_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(self.subtitle_lbl)

        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._apply_style()

    def is_selected(self) -> bool:
        return self._selected

    def set_recommended(self, recommended: bool) -> None:
        self._recommended = bool(recommended)
        self.recommended_lbl.setVisible(self._recommended)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def _apply_style(self) -> None:
        if self._selected:
            border = f"2px solid {THEME.primary}"
            bg = THEME.surface_container_high
            title_color = THEME.primary
        else:
            border = f"1px solid {THEME.outline_variant}"
            bg = THEME.surface_container
            title_color = THEME.on_surface

        self.setStyleSheet(
            f"QFrame#AcModeCard_{self.mode_key} {{ "
            f"background-color: {bg}; border: {border}; border-radius: 10px; }}"
        )
        self.title_lbl.setStyleSheet(
            f"color: {title_color}; font-size: 13px; font-weight: 600; background: transparent;"
        )


class _PowerActionThread(QThread):
    """Run privileged AC conflict deactivation / restoration off the UI thread."""

    finished_result = pyqtSignal(bool, str)

    def __init__(self, fn, *args, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            ok, msg = self._fn(*self._args)
        except Exception as exc:
            ok, msg = False, str(exc)
        self.finished_result.emit(bool(ok), str(msg))


class AutomationPage(QWidget):
    """Unified Trigger -> Action Automation Hub."""

    navigate_requested = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self._power_info = detect_power_managers()
        self._power_action_thread: Optional[_PowerActionThread] = None
        self._swatch_buttons: Dict[int, QPushButton] = {}
        self._profile_color_rows: Dict[int, tuple[QLabel, QPushButton, str]] = {}
        self.interval_buttons: Dict[int, QPushButton] = {}
        self._init_ui()

    @staticmethod
    def _make_divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {THEME.divider}; border: none;")
        return line

    @staticmethod
    def _make_card_header(title: str, help_tooltip: str) -> QHBoxLayout:
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl = QLabel(title)
        lbl.setProperty("class", "CardTitle")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(M3IconHelpButton(help_tooltip))
        return hdr

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(
            M3PageHeader(
                "Automation",
                "Unified trigger → action rules for power, lighting, idle & boot",
            )
        )

        # ── Card 1: ON AC / BATTERY — Power Source & dGPU Rules ──
        ac_card = M3Card()
        ac_layout = QVBoxLayout(ac_card)
        ac_layout.setContentsMargins(20, 18, 20, 18)
        ac_layout.setSpacing(10)
        ac_layout.addLayout(
            self._make_card_header(
                "ON AC / BATTERY — Power Source & dGPU Rules",
                "Automate EC fan/power profiles and discrete GPU power gating when plugging or unplugging AC.",
            )
        )

        self.power_managers_lbl = QLabel(self._format_power_managers_text(self._power_info))
        self.power_managers_lbl.setWordWrap(True)
        self.power_managers_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        ac_layout.addWidget(self.power_managers_lbl)

        self.auto_gate_switch = M3SwitchRow(
            "Auto-gate dGPU off on battery",
            "Cuts discrete GPU power rail when unplugged to maximize battery life.",
            checked=str(self.cfg.get("dgpu_power_mode", "optimus")) == "auto",
        )
        self.auto_gate_switch.switch.toggled.connect(self._on_auto_gate_toggled)
        self.auto_gate_switch.setVisible(dgpu_gate.is_supported())
        ac_layout.addWidget(self.auto_gate_switch)

        ac_layout.addWidget(self._make_divider())

        mode_lbl = QLabel("Power Profile Mode")
        mode_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 500; background: transparent;"
        )
        ac_layout.addWidget(mode_lbl)

        self._ac_mode_cards: Dict[str, _AcModeCard] = {}
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        for key, title, subtitle, tooltip in _AC_MODES:
            card = _AcModeCard(key, title, subtitle, tooltip)
            card.clicked.connect(lambda k=key: self._set_ac_mode(k, persist=True))
            self._ac_mode_cards[key] = card
            cards_row.addWidget(card, 1)
        ac_layout.addLayout(cards_row)

        self.ac_mode_desc_lbl = QLabel("")
        self.ac_mode_desc_lbl.setWordWrap(True)
        self.ac_mode_desc_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        ac_layout.addWidget(self.ac_mode_desc_lbl)

        current_mode = str(self.cfg.get("ac_profile_mode", "off"))
        self._ac_profile_mode: str = current_mode if current_mode in self._ac_mode_cards else "off"
        for key, card in self._ac_mode_cards.items():
            card.set_selected(key == self._ac_profile_mode)

        self.gigamate_sole_banner = M3Banner(
            title="GigaMate is the sole AC/Battery controller",
            body="Other power managers are paused or absent; GigaMate manages AC and Battery transitions directly.",
            tone="info",
            compact=True,
        )
        self.gigamate_sole_banner.setVisible(False)
        ac_layout.addWidget(self.gigamate_sole_banner)

        self.tlp_warning_banner = M3Banner(
            title="External AC/battery switching detected",
            body="",
            tone="warning",
            compact=True,
        )
        self.tlp_warning_banner.setVisible(False)
        ac_layout.addWidget(self.tlp_warning_banner)

        self.conflict_actions_widget = QWidget()
        conflict_actions_layout = QHBoxLayout(self.conflict_actions_widget)
        conflict_actions_layout.setContentsMargins(0, 2, 0, 2)
        conflict_actions_layout.setSpacing(8)

        self.btn_deactivate_conflicts = QPushButton("Deactivate Conflicts (Take Over)")
        self.btn_deactivate_conflicts.setProperty("class", "TonalButton")
        self.btn_deactivate_conflicts.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deactivate_conflicts.clicked.connect(self._on_deactivate_conflicts_clicked)
        conflict_actions_layout.addWidget(self.btn_deactivate_conflicts)

        self.btn_fallback_follow = QPushButton("Switch to Follow System")
        self.btn_fallback_follow.setProperty("class", "OutlinedButton")
        self.btn_fallback_follow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fallback_follow.clicked.connect(self._on_fallback_follow_clicked)
        conflict_actions_layout.addWidget(self.btn_fallback_follow)
        conflict_actions_layout.addStretch()

        self.conflict_actions_widget.setVisible(False)
        ac_layout.addWidget(self.conflict_actions_widget)

        self.btn_restore_handoff = QPushButton("Restore Paused Power Managers")
        self.btn_restore_handoff.setProperty("class", "OutlinedButton")
        self.btn_restore_handoff.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restore_handoff.clicked.connect(self._on_restore_handoff_clicked)
        self.btn_restore_handoff.setVisible(False)
        ac_layout.addWidget(self.btn_restore_handoff, alignment=Qt.AlignmentFlag.AlignLeft)

        self.ac_action_status_lbl = QLabel("")
        self.ac_action_status_lbl.setWordWrap(True)
        self.ac_action_status_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        ac_layout.addWidget(self.ac_action_status_lbl)

        self.dgpu_battery_guard_banner = M3Banner(
            title="Battery profile guard active",
            body=(
                "Performance & Gaming are locked for On Battery while dGPU Auto-Gate is active "
                "(they require NVIDIA Dynamic Boost). On AC Power, the dGPU wakes first and then "
                "applies your AC target."
            ),
            tone="warning",
            compact=True,
        )
        self.dgpu_battery_guard_banner.setVisible(False)
        ac_layout.addWidget(self.dgpu_battery_guard_banner)

        self.ac_selectors_widget = QWidget()
        sel_layout = QVBoxLayout(self.ac_selectors_widget)
        sel_layout.setContentsMargins(0, 4, 0, 0)
        sel_layout.setSpacing(8)

        ac_row = QHBoxLayout()
        ac_lbl = QLabel("On AC Power")
        ac_lbl.setStyleSheet(f"color: {THEME.on_surface}; font-size: 13px; background: transparent;")
        ac_row.addWidget(ac_lbl)
        ac_row.addStretch()
        self.ac_profile_combo = M3ComboBox()
        self.ac_profile_combo.currentIndexChanged.connect(self._on_ac_profile_changed)
        ac_row.addWidget(self.ac_profile_combo)
        sel_layout.addLayout(ac_row)

        bat_row = QHBoxLayout()
        bat_lbl = QLabel("On Battery")
        bat_lbl.setStyleSheet(f"color: {THEME.on_surface}; font-size: 13px; background: transparent;")
        bat_row.addWidget(bat_lbl)
        bat_row.addStretch()
        self.battery_profile_combo = M3ComboBox()
        self.battery_profile_combo.currentIndexChanged.connect(self._on_battery_profile_changed)
        bat_row.addWidget(self.battery_profile_combo)
        sel_layout.addLayout(bat_row)

        ac_layout.addWidget(self.ac_selectors_widget)
        layout.addWidget(ac_card)

        # ── Card 2: ON PROFILE SWITCH — Lighting & Linux System Sync ──
        rgb_card = M3Card()
        rgb_layout = QVBoxLayout(rgb_card)
        rgb_layout.setContentsMargins(20, 18, 20, 18)
        rgb_layout.setSpacing(10)
        rgb_layout.addLayout(
            self._make_card_header(
                "ON PROFILE SWITCH — Lighting & Linux System Sync",
                "Link keyboard backlight colours and Linux system power profiles to EC fan mode changes.",
            )
        )

        self.profile_rgb_switch = M3SwitchRow(
            "Link keyboard colour to Power Profile",
            "Automatically switch keyboard backlight colour when changing power profiles",
            checked=bool(self.cfg.get("profile_rgb_enabled", False)),
        )
        self.profile_rgb_switch.switch.toggled.connect(self._on_profile_rgb_toggled)
        rgb_layout.addWidget(self.profile_rgb_switch)
        rgb_layout.addWidget(self._make_divider())

        for pid, name in self._all_discovered_profiles():
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 4)
            row.setSpacing(12)

            lbl = QLabel(name)
            row.addWidget(lbl)
            row.addStretch()

            swatch = QPushButton()
            swatch.setFixedSize(92, 28)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.clicked.connect(lambda _checked=False, p=pid: self._pick_profile_color(p))
            self._swatch_buttons[pid] = swatch
            self._profile_color_rows[pid] = (lbl, swatch, name)
            self._update_swatch_style(pid, profile_rgb_for(self.cfg, pid))
            row.addWidget(swatch)

            rgb_layout.addLayout(row)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        self.btn_reset_profile_rgb = QPushButton("Reset Defaults")
        self.btn_reset_profile_rgb.setProperty("class", "TonalButton")
        self.btn_reset_profile_rgb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_profile_rgb.clicked.connect(self._reset_profile_rgb_defaults)
        reset_row.addWidget(self.btn_reset_profile_rgb)
        rgb_layout.addLayout(reset_row)

        rgb_layout.addWidget(self._make_divider())

        self.chk_sync_power = M3SwitchRow(
            "Synchronize system power profile with fan profile",
            "Aligns Linux power-profiles-daemon and NVIDIA/AMD Dynamic Boost with your active fan mode.",
            checked=bool(self.cfg.get("sync_system_power", False)),
        )
        self.chk_sync_power.switch.toggled.connect(self._on_sync_power_toggled)
        rgb_layout.addWidget(self.chk_sync_power)

        layout.addWidget(rgb_card)

        # ── Card 3: ON INACTIVITY — Keyboard Idle Sleep ──
        self.idle_card = M3Card()
        idle_layout = QVBoxLayout(self.idle_card)
        idle_layout.setContentsMargins(20, 18, 20, 18)
        idle_layout.setSpacing(10)
        idle_layout.addLayout(
            self._make_card_header(
                "ON INACTIVITY — Keyboard Idle Sleep",
                "Turn off keyboard backlight after a period without input and restore it on activity.",
            )
        )

        idle_row = QHBoxLayout()
        idle_lbl = QLabel("Idle timeout")
        idle_lbl.setStyleSheet(f"color: {THEME.on_surface}; font-size: 13px; background: transparent;")
        idle_row.addWidget(idle_lbl)
        idle_row.addStretch()

        self.idle_options = list(IDLE_TIMEOUT_STEPS)
        self.idle_combo = M3ComboBox()
        for secs, label in self.idle_options:
            self.idle_combo.addItem(label, secs)
        self.idle_combo.currentIndexChanged.connect(self._on_idle_timeout_changed)
        idle_row.addWidget(self.idle_combo)
        idle_layout.addLayout(idle_row)

        self.keep_on_ac_row = M3SwitchRow(
            "Keep backlight on while plugged into AC",
            "Only sleep the keyboard backlight when running on battery.",
            checked=bool(self.cfg.get("idle_keep_on_ac", False)),
        )
        self.keep_on_ac_row.switch.toggled.connect(self._on_idle_keep_ac_toggled)
        idle_layout.addWidget(self.keep_on_ac_row)

        layout.addWidget(self.idle_card)

        # ── Card 4: ON BOOT & SCHEDULE — Startup & Periodic BMS Calibration ──
        boot_card = M3Card()
        boot_layout = QVBoxLayout(boot_card)
        boot_layout.setContentsMargins(20, 18, 20, 18)
        boot_layout.setSpacing(10)
        boot_layout.addLayout(
            self._make_card_header(
                "ON BOOT & SCHEDULE — Startup & Periodic BMS Calibration",
                "Configure login restoration, GPU tuning persistence, and periodic battery calibration.",
            )
        )

        self.chk_startup_apply = M3SwitchRow(
            "Restore keyboard backlight on login",
            "Re-applies your saved keyboard backlight when you log in.",
            checked=bool(self.cfg.get("startup_apply", False)),
        )
        self.chk_startup_apply.switch.toggled.connect(self._on_startup_apply_toggled)
        boot_layout.addWidget(self.chk_startup_apply)

        boot_layout.addWidget(self._make_divider())

        self.chk_dgpu_auto = M3SwitchRow(
            "Auto-apply GPU tuning on boot and resume",
            "Applies saved dGPU undervolt / clock caps automatically on boot and resume.",
            checked=bool(self.cfg.get("dgpu_undervolt_auto", True)),
        )
        self.chk_dgpu_auto.switch.toggled.connect(self._on_dgpu_auto_toggled)
        boot_layout.addWidget(self.chk_dgpu_auto)

        self._bms_divider = self._make_divider()
        boot_layout.addWidget(self._bms_divider)

        self.auto_cal_chk = M3SwitchRow(
            "Periodic BMS full-charge calibration",
            "Periodically charges to 100% to keep the battery gauge accurate.",
            checked=bool(self.cfg.get("charge_calibration_auto", False)),
        )
        self.auto_cal_chk.switch.toggled.connect(self._on_auto_cal_toggled)
        boot_layout.addWidget(self.auto_cal_chk)

        self.interval_row_widget = QWidget()
        int_row = QHBoxLayout(self.interval_row_widget)
        int_row.setContentsMargins(0, 2, 0, 2)
        int_row.setSpacing(10)
        int_lbl = QLabel("Calibration interval")
        int_lbl.setStyleSheet(f"color: {THEME.on_surface}; font-size: 13px; background: transparent;")
        int_row.addWidget(int_lbl)
        int_row.addStretch()

        seg_bar = M3SegmentedBar()
        for days in (14, 30, 60):
            btn = seg_bar.add_segment(f"{days}d", tooltip=f"Calibrate every {days} days", min_height=34)
            btn.clicked.connect(lambda _checked=False, d=days: self._on_cal_interval_clicked(d))
            self.interval_buttons[days] = btn
        int_row.addWidget(seg_bar)
        boot_layout.addWidget(self.interval_row_widget)

        self.last_full_label = QLabel("")
        self.last_full_label.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        boot_layout.addWidget(self.last_full_label)

        layout.addWidget(boot_card)
        layout.addStretch()

        self._sections: Dict[str, QWidget] = {
            "dgpu_gate": self.auto_gate_switch,
            "profile_rgb": self.profile_rgb_switch,
            "idle_sleep": self.idle_card,
            "boot_apply": self.chk_dgpu_auto,
            "calibration": self.auto_cal_chk,
        }

        self.reload_from_config()

    def resolve_section(self, key: str) -> Optional[QWidget]:
        """Return the target setting widget for a deep-link section key."""
        return self._sections.get(key)

    @staticmethod
    def _contrast_text_color(hex_color: str) -> str:
        c = QColor(hex_color)
        if not c.isValid():
            return "#FAFAFA"
        luminance = (0.299 * c.redF()) + (0.587 * c.greenF()) + (0.114 * c.blueF())
        return "#09090B" if luminance > 0.45 else "#FAFAFA"

    def _update_swatch_style(self, pid: int, hex_color: str) -> None:
        btn = self._swatch_buttons.get(pid)
        if btn is None:
            return
        fg = self._contrast_text_color(hex_color)
        btn.setText(hex_color)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {hex_color}; color: {fg}; "
            f"border: 1px solid {THEME.outline}; border-radius: 7px; "
            f"font-family: {THEME.mono_family}; font-size: 11px; font-weight: 700; "
            f"padding: 0px 8px; min-height: 0px; }}"
        )

    def _apply_active_profile_rgb(self) -> None:
        """Apply the currently active profile's linked colour to the keyboard right away."""
        fresh_cfg = load_config()
        if not fresh_cfg.get("profile_rgb_enabled") or not fresh_cfg.get("rgb_enabled", True):
            return
        try:
            from ... import protocol
            from ...config import effective_brightness_pct

            active_pid = int(fresh_cfg.get("acpi_profile", 1))
            hex_color = profile_rgb_for(fresh_cfg, active_pid)
            update_config(lambda c: {**c, "rgb_hex": hex_color})
            self.cfg["rgb_hex"] = hex_color
            dev = protocol.get_keyboard()
            if dev is not None:
                protocol.set_rgb(dev, hex_color, effective_brightness_pct(fresh_cfg))
        except Exception:
            pass

    def _on_profile_rgb_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["profile_rgb_enabled"] = val
        update_config(lambda c: {**c, "profile_rgb_enabled": val})
        self._refresh_profile_rgb_rows()
        if val:
            self._apply_active_profile_rgb()

    def _pick_profile_color(self, pid: int) -> None:
        current = QColor(profile_rgb_for(self.cfg, pid))
        chosen = QColorDialog.getColor(current, self, "Select Profile Colour")
        if not chosen.isValid():
            return
        hex_color = chosen.name().lower()
        rgb_map = dict(self.cfg.get("profile_rgb_map") or DEFAULT_CONFIG["profile_rgb_map"])
        rgb_map[str(pid)] = hex_color
        self.cfg["profile_rgb_map"] = rgb_map
        update_config(lambda c: {
            **c,
            "profile_rgb_map": {
                **dict(c.get("profile_rgb_map") or DEFAULT_CONFIG["profile_rgb_map"]),
                str(pid): hex_color,
            },
        })
        self._update_swatch_style(pid, hex_color)
        fresh = load_config()
        if fresh.get("profile_rgb_enabled") and int(fresh.get("acpi_profile", 1)) == pid:
            self._apply_active_profile_rgb()

    def _reset_profile_rgb_defaults(self) -> None:
        defaults = dict(DEFAULT_CONFIG["profile_rgb_map"])
        self.cfg["profile_rgb_map"] = defaults
        update_config(lambda c: {**c, "profile_rgb_map": dict(defaults)})
        for pid, _name in self._all_discovered_profiles():
            self._update_swatch_style(pid, profile_rgb_for(self.cfg, pid))
        if load_config().get("profile_rgb_enabled"):
            self._apply_active_profile_rgb()

    def _on_sync_power_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["sync_system_power"] = val
        if self.cfg.get("acpi_profile") is None:
            self.cfg["acpi_profile"] = 1
            update_config(lambda c: {**c, "sync_system_power": val, "acpi_profile": 1})
        else:
            update_config(lambda c: {**c, "sync_system_power": val})
        if val:
            pid = int(self.cfg.get("acpi_profile") or 1)
            try:
                sync_system_power(pid)
            except Exception:
                pass

    def _on_auto_gate_toggled(self, checked: bool) -> None:
        if checked:
            fresh = load_config()
            if not fresh.get("dgpu_gate_disclaimer_accepted", False):
                res = QMessageBox.warning(
                    self,
                    "Enable dGPU Auto-Gate?",
                    "Auto-Gate cuts the discrete GPU power rail when on battery. "
                    "Active GPU workloads or external displays wired to the dGPU may be interrupted.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if res != QMessageBox.StandardButton.Yes:
                    self.auto_gate_switch.switch.blockSignals(True)
                    self.auto_gate_switch.switch.setChecked(False)
                    self.auto_gate_switch.switch.blockSignals(False)
                    return
                update_config(lambda c: {**c, "dgpu_gate_disclaimer_accepted": True})

            prev_mode = str(fresh.get("dgpu_power_mode", dgpu_gate.MODE_OPTIMUS))
            on_battery = False
            try:
                on_battery = not get_battery_manager().is_ac_online()
            except Exception:
                on_battery = False

            holders = []
            if on_battery:
                try:
                    topo = dgpu_gate.find_dgpu_topology()
                    holders = dgpu_gate.list_gpu_holder_processes(topo.bdf) if topo.bdf else []
                except Exception:
                    holders = []

            if on_battery and holders:
                dlg = DgpuGateConfirmDialog(
                    trigger="auto",
                    target_mode=dgpu_gate.MODE_AUTO,
                    prev_mode=prev_mode,
                    parent=self,
                )
                dlg.exec()
                if not dlg.result():
                    self.auto_gate_switch.switch.blockSignals(True)
                    self.auto_gate_switch.switch.setChecked(False)
                    self.auto_gate_switch.switch.blockSignals(False)
                    return
            else:
                try:
                    dgpu_gate.set_mode(dgpu_gate.MODE_AUTO, apply_now=True)
                except Exception:
                    pass

            # Coerce battery_profile_id if it was Performance (2) or Gaming (3)
            fresh_after = load_config()
            bat_pid = int(fresh_after.get("battery_profile_id", 0))
            if bat_pid in (2, 3):
                safe_rows = [p for p, _n in self._enabled_discovered_profiles(fresh_after) if p not in (2, 3)]
                fallback_pid = 0 if any(p == 0 for p in safe_rows) else (safe_rows[0] if safe_rows else 0)
                update_config(lambda c: {**c, "battery_profile_id": fallback_pid})
        else:
            try:
                dgpu_gate.set_mode(dgpu_gate.MODE_OPTIMUS, apply_now=True)
            except Exception:
                pass

        self.reload_from_config()

    @staticmethod
    def _format_power_managers_text(power_info: Dict[str, object]) -> str:
        managers = power_info.get("managers") or []
        managers_text = ", ".join(str(m) for m in managers) if managers else "None detected"
        tlp_summary = str(power_info.get("tlp_summary") or "").strip()
        if tlp_summary:
            return f"Detected power managers: {managers_text} — {tlp_summary}"
        return f"Detected power managers: {managers_text}"

    def _refresh_power_info(self) -> None:
        self._power_info = detect_power_managers()
        self.power_managers_lbl.setText(self._format_power_managers_text(self._power_info))

    def _update_ac_mode_description(self) -> None:
        desc_map = {
            "off": (
                "Manual only: GigaMate will not automatically change your power profile when plugging or unplugging AC."
            ),
            "follow_system": (
                "Mirroring system profile changes from TLP, KDE PowerDevil, or GNOME power-profiles-daemon "
                "into Gigabyte EC & keyboard RGB."
            ),
            "switch": (
                "GigaMate switches both Gigabyte EC (including Gaming on AC) and your OS power backend "
                "automatically on plug/unplug."
            ),
        }
        self.ac_mode_desc_lbl.setText(desc_map.get(self._ac_profile_mode, ""))

        recommended_mode = self._power_info.get("recommended_mode")
        for key, card in self._ac_mode_cards.items():
            card.set_recommended(key == recommended_mode)

    def _sync_ac_visibility(self) -> None:
        self._refresh_power_info()
        self._update_ac_mode_description()

        conflicts = list(self._power_info.get("conflicts") or [])
        has_conflicts = bool(conflicts) or bool(self._power_info.get("has_external_ac_switching", False))
        formatted_conflicts = []
        for c in conflicts:
            if isinstance(c, dict):
                name = str(c.get("name") or c.get("id") or "external")
                detail = str(c.get("detail") or "").strip()
                formatted_conflicts.append(f"{name} ({detail})" if detail else name)
            else:
                formatted_conflicts.append(str(c))
        conflict_list = ", ".join(formatted_conflicts) if formatted_conflicts else "an external power manager"

        is_switch = self._ac_profile_mode == "switch"
        self.ac_selectors_widget.setVisible(is_switch)

        dgpu_mode = str(self.cfg.get("dgpu_power_mode", "optimus"))
        self.dgpu_battery_guard_banner.setVisible(is_switch and dgpu_mode in ("auto", "eco"))

        if is_switch and has_conflicts:
            self.gigamate_sole_banner.setVisible(False)
            self.tlp_warning_banner.set_body(
                f"{conflict_list} also switches profiles on AC/battery. "
                "Deactivate conflicts so GigaMate owns AC/battery, or switch to Follow System."
            )
            self.tlp_warning_banner.setVisible(True)
            self.conflict_actions_widget.setVisible(True)
        elif is_switch and not has_conflicts:
            self.tlp_warning_banner.setVisible(False)
            self.conflict_actions_widget.setVisible(False)
            self.gigamate_sole_banner.setVisible(True)
        else:
            self.tlp_warning_banner.setVisible(False)
            self.conflict_actions_widget.setVisible(False)
            self.gigamate_sole_banner.setVisible(False)

        self.btn_restore_handoff.setVisible((not is_switch) and has_handoff_to_restore())

    def _set_ac_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in self._ac_mode_cards:
            mode = "off"
        self._ac_profile_mode = mode
        for key, card in self._ac_mode_cards.items():
            card.set_selected(key == mode)
        if persist:
            self.cfg["ac_profile_mode"] = mode
            update_config(lambda c: {**c, "ac_profile_mode": mode})
        if mode != "switch" and has_handoff_to_restore():
            self.ac_action_status_lbl.setText(
                "Previously paused power manager rules can be restored anytime using the button below."
            )
        self._sync_ac_visibility()

    def _on_fallback_follow_clicked(self) -> None:
        self._set_ac_mode("follow_system", persist=True)

    def _on_deactivate_conflicts_clicked(self) -> None:
        if self._power_action_thread is not None and self._power_action_thread.isRunning():
            return
        reply = QMessageBox.question(
            self,
            "Deactivate External AC Switchers?",
            "GigaMate will ask for administrator permission (pkexec) to pause "
            "conflicting AC/battery services (TLP / power-profiles-daemon / tuned) "
            "so GigaMate owns AC/battery switching cleanly.\n\n"
            "You can restore them anytime from this page.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.btn_deactivate_conflicts.setEnabled(False)
        self.btn_fallback_follow.setEnabled(False)
        self.ac_action_status_lbl.setText("Deactivating conflicting power managers…")
        self._power_action_thread = _PowerActionThread(deactivate_ac_conflicts, parent=self)
        self._power_action_thread.finished_result.connect(self._on_deactivate_conflicts_finished)
        self._power_action_thread.start()

    def _on_deactivate_conflicts_finished(self, ok: bool, msg: str) -> None:
        self.btn_deactivate_conflicts.setEnabled(True)
        self.btn_fallback_follow.setEnabled(True)
        self.ac_action_status_lbl.setText(msg)
        self._sync_ac_visibility()

    def _on_restore_handoff_clicked(self) -> None:
        if self._power_action_thread is not None and self._power_action_thread.isRunning():
            return
        self.btn_restore_handoff.setEnabled(False)
        self.ac_action_status_lbl.setText("Restoring paused power managers…")
        self._power_action_thread = _PowerActionThread(restore_ac_conflicts, parent=self)
        self._power_action_thread.finished_result.connect(self._on_restore_handoff_finished)
        self._power_action_thread.start()

    def _on_restore_handoff_finished(self, ok: bool, msg: str) -> None:
        self.btn_restore_handoff.setEnabled(True)
        self.ac_action_status_lbl.setText(msg)
        self._sync_ac_visibility()

    def _on_ac_profile_changed(self) -> None:
        pid = self.ac_profile_combo.currentData()
        if pid is not None:
            self.cfg["ac_profile_id"] = int(pid)
            update_config(lambda c: {**c, "ac_profile_id": int(pid)})

    def _on_battery_profile_changed(self) -> None:
        pid = self.battery_profile_combo.currentData()
        if pid is not None:
            self.cfg["battery_profile_id"] = int(pid)
            update_config(lambda c: {**c, "battery_profile_id": int(pid)})

    def _on_idle_timeout_changed(self) -> None:
        secs = self.idle_combo.currentData()
        if secs is None:
            return
        secs_int = int(secs)
        enabled = secs_int != 0
        self.keep_on_ac_row.setEnabled(enabled)
        if enabled:
            update_config(lambda c: {**c, "idle_off_enabled": True, "idle_timeout_sec": secs_int})
        else:
            update_config(lambda c: {**c, "idle_off_enabled": False})

    def _on_idle_keep_ac_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["idle_keep_on_ac"] = val
        update_config(lambda c: {**c, "idle_keep_on_ac": val})

    def _on_startup_apply_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["startup_apply"] = val
        update_config(lambda c: {**c, "startup_apply": val})

    def _on_dgpu_auto_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["dgpu_undervolt_auto"] = val
        dgpu_tune.set_auto_config(val)

    def _on_auto_cal_toggled(self, checked: bool) -> None:
        val = bool(checked)
        self.cfg["charge_calibration_auto"] = val
        update_config(lambda c: {**c, "charge_calibration_auto": val})

    def _on_cal_interval_clicked(self, days: int) -> None:
        self.cfg["charge_calibration_interval_days"] = days
        update_config(lambda c: {**c, "charge_calibration_interval_days": days})
        for d, btn in self.interval_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(d == days)
            btn.blockSignals(False)

    def _all_discovered_profiles(self) -> list[tuple[int, str]]:
        from ...power_limits import discover_acpi_profiles

        try:
            discovered = discover_acpi_profiles()
        except Exception:
            discovered = {}
        if discovered:
            return [
                (int(pid), (meta or {}).get("name", f"Profile {pid}"))
                for pid, meta in sorted(discovered.items())
            ]
        return list(_PROFILE_NAMES)

    def _enabled_discovered_profiles(self, cfg: Optional[dict] = None) -> list[tuple[int, str]]:
        all_rows = self._all_discovered_profiles()
        as_dict = {pid: {"name": name} for pid, name in all_rows}
        enabled = enabled_acpi_profiles(as_dict, cfg)
        return [(int(pid), (meta or {}).get("name", f"Profile {pid}")) for pid, meta in sorted(enabled.items())]

    def _refresh_profile_rgb_rows(self) -> None:
        disabled_set = {int(x) for x in (self.cfg.get("disabled_profiles") or [])}
        rgb_on = bool(self.profile_rgb_switch.switch.isChecked())
        for pid, (lbl, swatch, base_name) in self._profile_color_rows.items():
            is_disabled = pid in disabled_set
            lbl.setText(f"{base_name} (Disabled in Settings)" if is_disabled else base_name)
            lbl_color = THEME.on_surface_muted if is_disabled else THEME.on_surface
            lbl.setStyleSheet(
                f"color: {lbl_color}; font-size: 13px; font-weight: 500; background: transparent;"
            )
            swatch.setEnabled(rgb_on and not is_disabled)
            self._update_swatch_style(pid, profile_rgb_for(self.cfg, pid))

    def refresh_profiles(self) -> None:
        """Refresh disabled-profile state for colour swatches and AC/Battery combos."""
        self.cfg = load_config()
        self._refresh_profile_rgb_rows()

        enabled_rows = self._enabled_discovered_profiles(self.cfg)
        dgpu_mode = str(self.cfg.get("dgpu_power_mode", "optimus"))
        gate_active = dgpu_mode in ("auto", "eco")

        self.ac_profile_combo.blockSignals(True)
        self.ac_profile_combo.clear()
        for pid, name in enabled_rows:
            self.ac_profile_combo.addItem(name, pid)
        ac_idx = self.ac_profile_combo.findData(int(self.cfg.get("ac_profile_id", 1)))
        self.ac_profile_combo.setCurrentIndex(ac_idx if ac_idx >= 0 else 0)
        self.ac_profile_combo.blockSignals(False)

        self.battery_profile_combo.blockSignals(True)
        self.battery_profile_combo.clear()
        for pid, name in enabled_rows:
            if gate_active and pid in (2, 3):
                continue
            self.battery_profile_combo.addItem(name, pid)
        bat_idx = self.battery_profile_combo.findData(int(self.cfg.get("battery_profile_id", 0)))
        self.battery_profile_combo.setCurrentIndex(bat_idx if bat_idx >= 0 else 0)
        self.battery_profile_combo.blockSignals(False)

    def reload_from_config(self) -> None:
        """Reload and refresh all 4 cards cleanly without emitting spurious signals."""
        self.cfg = load_config()

        # Card 1: AC / Battery & dGPU Auto-Gate
        self.auto_gate_switch.setVisible(dgpu_gate.is_supported())
        self.auto_gate_switch.switch.blockSignals(True)
        self.auto_gate_switch.switch.setChecked(
            str(self.cfg.get("dgpu_power_mode", "optimus")) == "auto"
        )
        self.auto_gate_switch.switch.blockSignals(False)

        mode = str(self.cfg.get("ac_profile_mode", "off"))
        self._ac_profile_mode = mode if mode in self._ac_mode_cards else "off"
        for key, card in self._ac_mode_cards.items():
            card.set_selected(key == self._ac_profile_mode)
        self._sync_ac_visibility()

        # Card 2: Profile RGB & System Power Sync
        self.profile_rgb_switch.switch.blockSignals(True)
        self.profile_rgb_switch.switch.setChecked(bool(self.cfg.get("profile_rgb_enabled", False)))
        self.profile_rgb_switch.switch.blockSignals(False)
        self.refresh_profiles()

        self.chk_sync_power.switch.blockSignals(True)
        self.chk_sync_power.switch.setChecked(bool(self.cfg.get("sync_system_power", False)))
        self.chk_sync_power.switch.blockSignals(False)

        # Card 3: Keyboard Idle Sleep
        idle_enabled = bool(self.cfg.get("idle_off_enabled", False))
        timeout_sec = nearest_idle_step(int(self.cfg.get("idle_timeout_sec", 60))) if idle_enabled else 0
        self.idle_combo.blockSignals(True)
        idx = self.idle_combo.findData(timeout_sec)
        self.idle_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.idle_combo.blockSignals(False)

        self.keep_on_ac_row.setEnabled(idle_enabled)
        self.keep_on_ac_row.switch.blockSignals(True)
        self.keep_on_ac_row.switch.setChecked(bool(self.cfg.get("idle_keep_on_ac", False)))
        self.keep_on_ac_row.switch.blockSignals(False)

        # Card 4: Boot & BMS Calibration
        self.chk_startup_apply.switch.blockSignals(True)
        self.chk_startup_apply.switch.setChecked(bool(self.cfg.get("startup_apply", False)))
        self.chk_startup_apply.switch.blockSignals(False)

        self.chk_dgpu_auto.switch.blockSignals(True)
        self.chk_dgpu_auto.switch.setChecked(bool(self.cfg.get("dgpu_undervolt_auto", True)))
        self.chk_dgpu_auto.switch.blockSignals(False)

        caps = detect_system_capabilities()
        bms_supported = bool(caps.battery_present and caps.charge_limit_supported)
        self._bms_divider.setVisible(bms_supported)
        self.auto_cal_chk.setVisible(bms_supported)
        self.interval_row_widget.setVisible(bms_supported)
        self.last_full_label.setVisible(bms_supported)

        if bms_supported:
            self.auto_cal_chk.switch.blockSignals(True)
            self.auto_cal_chk.switch.setChecked(bool(self.cfg.get("charge_calibration_auto", False)))
            self.auto_cal_chk.switch.blockSignals(False)

            interval_days = int(self.cfg.get("charge_calibration_interval_days", 30))
            for d, btn in self.interval_buttons.items():
                btn.blockSignals(True)
                btn.setChecked(d == interval_days)
                btn.blockSignals(False)

            last_full = int(self.cfg.get("last_full_charge_ts", 0) or 0)
            if last_full > 0:
                days_ago = max(0, int((time.time() - last_full) / 86400))
                if days_ago == 0:
                    age_str = "Today"
                elif days_ago == 1:
                    age_str = "Yesterday"
                else:
                    age_str = f"{days_ago} days ago"
                self.last_full_label.setText(f"Last full charge calibration: {age_str}")
            else:
                self.last_full_label.setText("Last full charge calibration: Never recorded")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.reload_from_config()
