"""GigaMate Center — Automation & Shortcuts Page.

Allows configuring:
- Colour per Power Profile (linking keyboard backlight colour to EC power profiles)
- AC / Battery Power Profile automation & system power manager integration
- Hardware hotkey overrides & desktop shortcut registration
"""

from typing import Dict, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import (
    DEFAULT_CONFIG,
    enabled_acpi_profiles,
    load as load_config,
    profile_rgb_for,
    save as save_config,
    update_config,
)
from ...hotkeys import (
    ACTION_MODE_SWITCH,
    ACTION_OPEN_CENTER,
    capture_hardware_hotkey,
    desktop_shortcut_command,
    register_desktop_shortcut,
)
from ...system_power import (
    deactivate_ac_conflicts,
    detect_power_managers,
    has_handoff_to_restore,
    restore_ac_conflicts,
)
from ..theme import THEME
from ..widgets import (
    M3Banner,
    M3Card,
    M3ComboBox,
    M3PageHeader,
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
            self.setStyleSheet(
                f"QFrame#AcModeCard_{self.mode_key} {{"
                f"  background-color: {THEME.primary_container};"
                f"  border: 1.5px solid {THEME.primary};"
                "  border-radius: 12px;"
                "}"
            )
            self.title_lbl.setStyleSheet(
                f"color: {THEME.primary}; font-size: 13px; font-weight: 600; background: transparent;"
            )
        else:
            self.setStyleSheet(
                f"QFrame#AcModeCard_{self.mode_key} {{"
                f"  background-color: {THEME.surface_container};"
                f"  border: 1px solid {THEME.outline_variant};"
                "  border-radius: 12px;"
                "}"
                f"QFrame#AcModeCard_{self.mode_key}:hover {{"
                f"  background-color: {THEME.surface_container_high};"
                "}"
            )
            self.title_lbl.setStyleSheet(
                f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; background: transparent;"
            )

_SHORTCUT_ACTIONS = (
    (ACTION_OPEN_CENTER, "Open GigaMate Center", "GigaMate"),
    (ACTION_MODE_SWITCH, "Cycle Power Profile", "Mode"),
)


class _HotkeyCaptureThread(QThread):
    """Background thread that captures the next hardware hotkey HID report."""

    captured = pyqtSignal(object)

    def __init__(self, timeout_sec: float = 5.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._timeout_sec = timeout_sec

    def run(self) -> None:
        spec = capture_hardware_hotkey(timeout_sec=self._timeout_sec)
        self.captured.emit(spec)


class _PowerActionThread(QThread):
    """Background thread for reversible power manager handoff/restore."""

    finished_action = pyqtSignal(bool, str)

    def __init__(self, fn, *args, parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            ok, msg = self._fn(*self._args)
        except Exception as exc:
            ok, msg = False, str(exc)
        self.finished_action.emit(bool(ok), str(msg))


class AutomationPage(QWidget):
    """Configuration page for profile RGB, AC/battery power rules, and shortcuts."""

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self._power_info = detect_power_managers()
        self._capture_thread: Optional[_HotkeyCaptureThread] = None
        self._power_action_thread: Optional[_PowerActionThread] = None
        self._swatch_buttons: Dict[int, QPushButton] = {}
        self._hotkey_labels: Dict[str, QLabel] = {}
        self._record_buttons: Dict[str, QPushButton] = {}
        self._init_ui()

    @staticmethod
    def _make_divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {THEME.divider}; border: none;")
        return line

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(
            M3PageHeader(
                "Automation",
                "Profile-linked keyboard lighting, AC/battery power rules & shortcuts",
            )
        )

        # ── Section 1: Colour per Power Profile ──
        rgb_card = M3Card()
        rgb_layout = QVBoxLayout(rgb_card)
        rgb_layout.setContentsMargins(16, 16, 16, 16)
        rgb_layout.setSpacing(10)
        rgb_title = QLabel("Colour per Power Profile")
        rgb_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        rgb_layout.addWidget(rgb_title)

        self.profile_rgb_switch = M3SwitchRow(
            "Link keyboard colour to Power Profile",
            "Automatically switch keyboard backlight colour when changing power profiles",
            checked=bool(self.cfg.get("profile_rgb_enabled", False)),
        )
        self.profile_rgb_switch.switch.toggled.connect(self._on_profile_rgb_toggled)
        rgb_layout.addWidget(self.profile_rgb_switch)
        rgb_layout.addWidget(self._make_divider())

        self._profile_color_rows: Dict[int, tuple[QLabel, QPushButton, str]] = {}
        disabled_set = {int(x) for x in (self.cfg.get("disabled_profiles") or [])}
        profile_rows = self._all_discovered_profiles()

        for pid, name in profile_rows:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 4)
            row.setSpacing(12)

            is_disabled = pid in disabled_set
            lbl = QLabel(f"{name} (Disabled in Dashboard)" if is_disabled else name)
            lbl_color = THEME.on_surface_muted if is_disabled else THEME.on_surface
            lbl.setStyleSheet(
                f"color: {lbl_color}; font-size: 13px; font-weight: 500; background: transparent;"
            )
            row.addWidget(lbl)
            row.addStretch()

            swatch = QPushButton()
            swatch.setFixedSize(64, 28)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setEnabled(not is_disabled)
            swatch.clicked.connect(lambda _checked=False, p=pid: self._pick_profile_color(p))
            self._swatch_buttons[pid] = swatch
            self._profile_color_rows[pid] = (lbl, swatch, name)
            self._update_swatch_style(pid, profile_rgb_for(self.cfg, pid))
            row.addWidget(swatch)

            rgb_layout.addLayout(row)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        self.btn_reset_profile_rgb = QPushButton("Reset Defaults")
        self.btn_reset_profile_rgb.setProperty("variant", "tonal")
        self.btn_reset_profile_rgb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_profile_rgb.clicked.connect(self._reset_profile_rgb_defaults)
        reset_row.addWidget(self.btn_reset_profile_rgb)
        rgb_layout.addLayout(reset_row)

        layout.addWidget(rgb_card)

        # ── Section 2: AC / Battery Power Profile ──
        ac_card = M3Card()
        ac_layout = QVBoxLayout(ac_card)
        ac_layout.setContentsMargins(16, 16, 16, 16)
        ac_layout.setSpacing(10)
        ac_title = QLabel("AC / Battery Power Profile")
        ac_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        ac_layout.addWidget(ac_title)

        self.power_managers_lbl = QLabel(self._format_power_managers_text(self._power_info))
        self.power_managers_lbl.setWordWrap(True)
        self.power_managers_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        ac_layout.addWidget(self.power_managers_lbl)

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
            body="GigaMate is the sole AC/Battery controller.",
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
        self.btn_deactivate_conflicts.clicked.connect(self._on_deactivate_conflicts_clicked)
        conflict_actions_layout.addWidget(self.btn_deactivate_conflicts)

        self.btn_fallback_follow = QPushButton("Switch to Follow System")
        self.btn_fallback_follow.clicked.connect(self._on_fallback_follow_clicked)
        conflict_actions_layout.addWidget(self.btn_fallback_follow)
        conflict_actions_layout.addStretch()

        self.conflict_actions_widget.setVisible(False)
        ac_layout.addWidget(self.conflict_actions_widget)

        self.btn_restore_handoff = QPushButton("Restore Paused Power Managers")
        self.btn_restore_handoff.clicked.connect(self._on_restore_handoff_clicked)
        self.btn_restore_handoff.setVisible(False)
        ac_layout.addWidget(self.btn_restore_handoff, alignment=Qt.AlignmentFlag.AlignLeft)

        self.ac_action_status_lbl = QLabel("")
        self.ac_action_status_lbl.setWordWrap(True)
        self.ac_action_status_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        ac_layout.addWidget(self.ac_action_status_lbl)

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
        enabled_rows = self._enabled_discovered_profiles(self.cfg)
        for pid, name in enabled_rows:
            self.ac_profile_combo.addItem(name, pid)
        ac_idx = self.ac_profile_combo.findData(int(self.cfg.get("ac_profile_id", 2)))
        self.ac_profile_combo.setCurrentIndex(ac_idx if ac_idx >= 0 else 0)
        self.ac_profile_combo.currentIndexChanged.connect(self._on_ac_profile_changed)
        ac_row.addWidget(self.ac_profile_combo)
        sel_layout.addLayout(ac_row)

        bat_row = QHBoxLayout()
        bat_lbl = QLabel("On Battery")
        bat_lbl.setStyleSheet(f"color: {THEME.on_surface}; font-size: 13px; background: transparent;")
        bat_row.addWidget(bat_lbl)
        bat_row.addStretch()
        self.battery_profile_combo = M3ComboBox()
        for pid, name in enabled_rows:
            self.battery_profile_combo.addItem(name, pid)
        bat_idx = self.battery_profile_combo.findData(int(self.cfg.get("battery_profile_id", 0)))
        self.battery_profile_combo.setCurrentIndex(bat_idx if bat_idx >= 0 else 0)
        self.battery_profile_combo.currentIndexChanged.connect(self._on_battery_profile_changed)
        bat_row.addWidget(self.battery_profile_combo)
        sel_layout.addLayout(bat_row)

        ac_layout.addWidget(self.ac_selectors_widget)
        self._sync_ac_visibility()

        layout.addWidget(ac_card)

        # ── Section 3: Shortcuts ──
        shortcuts_card = M3Card()
        shortcuts_layout = QVBoxLayout(shortcuts_card)
        shortcuts_layout.setContentsMargins(16, 16, 16, 16)
        shortcuts_layout.setSpacing(10)
        shortcuts_title = QLabel("Shortcuts")
        shortcuts_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 15px; font-weight: 600; background: transparent;"
        )
        shortcuts_layout.addWidget(shortcuts_title)

        for action, title, default_key in _SHORTCUT_ACTIONS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 4)
            row.setSpacing(10)

            info_col = QVBoxLayout()
            info_col.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"color: {THEME.on_surface}; font-size: 13px; font-weight: 500; background: transparent;"
            )
            info_col.addWidget(title_lbl)

            binding_lbl = QLabel(self._format_hotkey_binding(action, default_key))
            binding_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
            )
            self._hotkey_labels[action] = binding_lbl
            info_col.addWidget(binding_lbl)
            row.addLayout(info_col)
            row.addStretch()

            rec_btn = QPushButton("Record Hardware Key")
            rec_btn.setProperty("variant", "tonal")
            rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rec_btn.clicked.connect(lambda _checked=False, a=action: self._start_hotkey_capture(a))
            self._record_buttons[action] = rec_btn
            row.addWidget(rec_btn)

            rst_btn = QPushButton("Reset")
            rst_btn.setProperty("variant", "outlined")
            rst_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rst_btn.clicked.connect(
                lambda _checked=False, a=action, dk=default_key: self._reset_hotkey(a, dk)
            )
            row.addWidget(rst_btn)

            shortcuts_layout.addLayout(row)

        shortcuts_layout.addWidget(self._make_divider())

        helper_title = QLabel("Desktop Shortcut Helper")
        helper_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        shortcuts_layout.addWidget(helper_title)

        for action, title, _default_key in _SHORTCUT_ACTIONS:
            cmd = desktop_shortcut_command(action)
            cmd_row = QHBoxLayout()
            cmd_row.setSpacing(8)
            cmd_lbl = QLabel(f"{title}: <code>{cmd}</code>")
            cmd_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
            )
            cmd_row.addWidget(cmd_lbl)
            cmd_row.addStretch()

            copy_btn = QPushButton("Copy")
            copy_btn.setProperty("variant", "outlined")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(lambda _checked=False, c=cmd: self._copy_command(c))
            cmd_row.addWidget(copy_btn)
            shortcuts_layout.addLayout(cmd_row)

        reg_row = QHBoxLayout()
        reg_row.setSpacing(10)
        self.btn_register_shortcut = QPushButton("Register Desktop Shortcut")
        self.btn_register_shortcut.setProperty("variant", "tonal")
        self.btn_register_shortcut.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_register_shortcut.clicked.connect(self._register_desktop_shortcut)
        reg_row.addWidget(self.btn_register_shortcut)

        self.shortcut_status_lbl = QLabel("")
        self.shortcut_status_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        reg_row.addWidget(self.shortcut_status_lbl)
        reg_row.addStretch()
        shortcuts_layout.addLayout(reg_row)

        layout.addWidget(shortcuts_card)
        layout.addStretch()

    def _update_swatch_style(self, pid: int, hex_color: str) -> None:
        btn = self._swatch_buttons.get(pid)
        if btn is None:
            return
        btn.setText(hex_color)
        btn.setStyleSheet(
            f"background-color: {hex_color}; color: #ffffff; "
            f"border: 1px solid {THEME.outline}; border-radius: 6px; "
            f"font-size: 11px; font-weight: 600;"
        )

    def _apply_active_profile_rgb(self) -> None:
        """Apply the currently active profile's linked colour to the keyboard right away."""
        fresh_cfg = load_config()
        if not fresh_cfg.get("profile_rgb_enabled") or not fresh_cfg.get("rgb_enabled", True):
            return
        try:
            from ... import protocol
            from ...config import effective_brightness_pct, update_config

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
        for pid, _name in _PROFILE_NAMES:
            self._update_swatch_style(pid, profile_rgb_for(self.cfg, pid))
        if load_config().get("profile_rgb_enabled"):
            self._apply_active_profile_rgb()

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
        self._sync_ac_visibility()

    def _update_ac_mode_description(self) -> None:
        mode = self._ac_profile_mode
        if mode == "off":
            desc = "Manual only — GigaMate will not change your power profile when plugging or unplugging AC."
        elif mode == "follow_system":
            tlp_summary = str(self._power_info.get("tlp_summary") or "").strip()
            if tlp_summary:
                desc = (
                    f"Mirroring system profile changes into Gigabyte EC & RGB ({tlp_summary}). "
                    "Manual switches in GigaMate still apply until the next plug/unplug."
                )
            else:
                desc = (
                    "Mirroring system profile changes into Gigabyte EC & RGB. "
                    "Manual switches in GigaMate still apply until the next plug/unplug."
                )
        else:
            desc = (
                "GigaMate switches both Gigabyte EC (including Gaming on AC) "
                "and your OS power backend automatically on plug/unplug."
            )
        self.ac_mode_desc_lbl.setText(desc)

    def _sync_ac_visibility(self) -> None:
        mode = self._ac_profile_mode
        recommended_mode = self._power_info.get("recommended_mode")
        for key, card in self._ac_mode_cards.items():
            card.set_recommended(key == recommended_mode)
        self._update_ac_mode_description()

        is_switch = mode == "switch"
        conflicts = list(self._power_info.get("conflicts") or [])
        has_conflicts = bool(conflicts) or bool(self._power_info.get("has_external_ac_switching", False))
        managers = self._power_info.get("managers") or []

        if is_switch and not has_conflicts:
            sole_body = (
                "GigaMate is the sole AC/Battery controller — TLP/system tuning remains active as backend."
                if managers
                else "GigaMate is the sole AC/Battery controller."
            )
            self.gigamate_sole_banner.body_lbl.setText(sole_body)
            self.gigamate_sole_banner.body_lbl.setVisible(True)
            self.gigamate_sole_banner.setVisible(True)
        else:
            self.gigamate_sole_banner.setVisible(False)

        if is_switch and has_conflicts:
            if conflicts:
                conflict_lines = ", ".join(
                    f"{c.get('name', c.get('id', 'Unknown'))} ({c.get('detail', '')})"
                    if c.get("detail")
                    else str(c.get("name", c.get("id", "Unknown")))
                    for c in conflicts
                )
            else:
                conflict_lines = "External AC/battery switcher"
            self.tlp_warning_banner.body_lbl.setText(
                f"Conflicting AC switchers: {conflict_lines}. "
                "GigaMate paused OS sync on AC transitions to avoid fighting until resolved."
            )
            self.tlp_warning_banner.body_lbl.setVisible(True)
            self.tlp_warning_banner.setVisible(True)
            self.conflict_actions_widget.setVisible(True)
        else:
            self.tlp_warning_banner.setVisible(False)
            self.conflict_actions_widget.setVisible(False)

        self.btn_restore_handoff.setVisible((not is_switch) and has_handoff_to_restore())
        self.ac_selectors_widget.setVisible(is_switch)

    def _set_ac_mode(self, mode: str, persist: bool = True) -> None:
        normalized = mode if mode in self._ac_mode_cards else "off"
        self._ac_profile_mode = normalized
        for key, card in self._ac_mode_cards.items():
            card.set_selected(key == normalized)
        if persist:
            self.cfg["ac_profile_mode"] = normalized
            update_config(lambda c: {**c, "ac_profile_mode": normalized})
        self._sync_ac_visibility()
        if normalized != "switch" and has_handoff_to_restore():
            self.ac_action_status_lbl.setText(
                "Previously paused power manager rules can be restored anytime using the button below."
            )

    def _on_fallback_follow_clicked(self) -> None:
        self._set_ac_mode("follow_system", persist=True)

    def _on_deactivate_conflicts_clicked(self) -> None:
        if self._power_action_thread is not None and self._power_action_thread.isRunning():
            return
        conflicts = list(self._power_info.get("conflicts") or [])
        steps = []
        if any(c.get("scope") == "system" for c in conflicts):
            steps.append(
                "• Write /etc/tlp.d/99-gigamate.conf with TLP_AUTO_SWITCH=0 and restart tlp-pd.service "
                "(administrator password will be requested via Polkit)."
            )
        if any(c.get("id") in ("powerdevil", "gnome") for c in conflicts):
            steps.append("• Pause KDE PowerDevil / GNOME AC profile rules with reversible backup.")
        if not steps:
            steps.append("• Deactivate conflicting external AC/battery switchers with reversible backup.")

        reply = QMessageBox.question(
            self,
            "Deactivate Conflicting Power Managers",
            "GigaMate will perform the following reversible actions:\n\n" + "\n".join(steps),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.btn_deactivate_conflicts.setEnabled(False)
        self.btn_deactivate_conflicts.setText("Applying...")
        self._power_action_thread = _PowerActionThread(deactivate_ac_conflicts, conflicts, parent=self)
        self._power_action_thread.finished_action.connect(self._on_deactivate_conflicts_finished)
        self._power_action_thread.start()

    def _on_deactivate_conflicts_finished(self, ok: bool, msg: str) -> None:
        self.btn_deactivate_conflicts.setEnabled(True)
        self.btn_deactivate_conflicts.setText("Deactivate Conflicts (Take Over)")
        self._refresh_power_info()
        self.ac_action_status_lbl.setText(msg)

    def _on_restore_handoff_clicked(self) -> None:
        if self._power_action_thread is not None and self._power_action_thread.isRunning():
            return
        reply = QMessageBox.question(
            self,
            "Restore Paused Power Managers",
            "Restore previously paused power manager rules and remove GigaMate's TLP handoff drop-in?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.btn_restore_handoff.setEnabled(False)
        self.btn_restore_handoff.setText("Restoring...")
        self._power_action_thread = _PowerActionThread(restore_ac_conflicts, parent=self)
        self._power_action_thread.finished_action.connect(self._on_restore_handoff_finished)
        self._power_action_thread.start()

    def _on_restore_handoff_finished(self, ok: bool, msg: str) -> None:
        self.btn_restore_handoff.setEnabled(True)
        self.btn_restore_handoff.setText("Restore Paused Power Managers")
        self._refresh_power_info()
        self.ac_action_status_lbl.setText(msg)

    def _on_ac_profile_changed(self) -> None:
        pid = int(self.ac_profile_combo.currentData() or 0)
        self.cfg["ac_profile_id"] = pid
        update_config(lambda c: {**c, "ac_profile_id": pid})

    def _on_battery_profile_changed(self) -> None:
        pid = int(self.battery_profile_combo.currentData() or 0)
        self.cfg["battery_profile_id"] = pid
        update_config(lambda c: {**c, "battery_profile_id": pid})

    def _format_hotkey_binding(self, action: str, default_key: str) -> str:
        custom = self.cfg.get("custom_hotkeys") or {}
        if isinstance(custom, dict) and action in custom and isinstance(custom[action], dict):
            key_name = custom[action].get("key_name") or f"HID 0x{custom[action].get('report_id', 0):02X}"
            return f"Custom: {key_name}"
        if default_key:
            return f"Default: {default_key}"
        return "Not bound"

    def _start_hotkey_capture(self, action: str) -> None:
        if self._capture_thread is not None and self._capture_thread.isRunning():
            return
        btn = self._record_buttons.get(action)
        if btn is not None:
            btn.setText("Listening (5s)...")
            btn.setEnabled(False)
        thread = _HotkeyCaptureThread(timeout_sec=5.0, parent=self)
        thread.captured.connect(lambda spec, a=action: self._on_hotkey_captured(a, spec))
        self._capture_thread = thread
        thread.start()

    def _on_hotkey_captured(self, action: str, spec: Optional[dict]) -> None:
        btn = self._record_buttons.get(action)
        if btn is not None:
            btn.setText("Record Hardware Key")
            btn.setEnabled(True)
        if spec:
            custom = dict(self.cfg.get("custom_hotkeys") or {})
            custom[action] = spec
            self.cfg["custom_hotkeys"] = custom
            update_config(lambda c: {**c, "custom_hotkeys": {**dict(c.get("custom_hotkeys") or {}), action: spec}})
        default_key = next((dk for a, _t, dk in _SHORTCUT_ACTIONS if a == action), "")
        lbl = self._hotkey_labels.get(action)
        if lbl is not None:
            lbl.setText(self._format_hotkey_binding(action, default_key))

    def _reset_hotkey(self, action: str, default_key: str) -> None:
        custom = dict(self.cfg.get("custom_hotkeys") or {})
        if action in custom:
            custom.pop(action, None)
            self.cfg["custom_hotkeys"] = custom
            update_config(lambda c: {
                **c,
                "custom_hotkeys": {k: v for k, v in dict(c.get("custom_hotkeys") or {}).items() if k != action},
            })
        lbl = self._hotkey_labels.get(action)
        if lbl is not None:
            lbl.setText(self._format_hotkey_binding(action, default_key))

    def _copy_command(self, cmd: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(cmd)
        self.shortcut_status_lbl.setText(f"Copied: {cmd}")

    def _register_desktop_shortcut(self) -> None:
        ok, msg = register_desktop_shortcut(ACTION_OPEN_CENTER, "Meta+G")
        self.shortcut_status_lbl.setText(msg if msg else ("Registered" if ok else "Failed"))

    def _all_discovered_profiles(self) -> list[tuple[int, str]]:
        """Return all discovered (pid, name) pairs, falling back to _PROFILE_NAMES."""
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
        """Return enabled (pid, name) pairs filtered via enabled_acpi_profiles."""
        all_rows = self._all_discovered_profiles()
        as_dict = {pid: {"name": name} for pid, name in all_rows}
        enabled = enabled_acpi_profiles(as_dict, cfg)
        return [(int(pid), (meta or {}).get("name", f"Profile {pid}")) for pid, meta in sorted(enabled.items())]

    def refresh_profiles(self) -> None:
        """Refresh disabled-profile state for colour swatches and AC/Battery combos."""
        self.cfg = load_config()
        disabled_set = {int(x) for x in (self.cfg.get("disabled_profiles") or [])}

        for pid, (lbl, swatch, base_name) in self._profile_color_rows.items():
            is_disabled = pid in disabled_set
            lbl.setText(f"{base_name} (Disabled in Dashboard)" if is_disabled else base_name)
            lbl_color = THEME.on_surface_muted if is_disabled else THEME.on_surface
            lbl.setStyleSheet(
                f"color: {lbl_color}; font-size: 13px; font-weight: 500; background: transparent;"
            )
            swatch.setEnabled(not is_disabled)

        enabled_rows = self._enabled_discovered_profiles(self.cfg)
        for combo, cfg_key, default_pid in (
            (self.ac_profile_combo, "ac_profile_id", 2),
            (self.battery_profile_combo, "battery_profile_id", 0),
        ):
            combo.blockSignals(True)
            combo.clear()
            for pid, name in enabled_rows:
                combo.addItem(name, pid)
            idx = combo.findData(int(self.cfg.get(cfg_key, default_pid)))
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh_profiles()
