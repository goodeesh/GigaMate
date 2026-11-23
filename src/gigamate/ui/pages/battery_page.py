"""GigaMate Center — Battery Care, Charge Limiter & BMS Calibration Page."""

from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...battery import (
    cancel_topup,
    evaluate_calibration_state,
    get_battery_manager,
    start_topup,
)
from ...config import load as load_config, update_config
from .. import icons
from ..theme import THEME
from ..widgets import (
    M3Banner,
    M3Card,
    M3PageHeader,
    M3ProgressBar,
    M3ResponsiveGrid,
    M3SegmentedBar,
    M3Slider,
    M3SwitchRow,
)


class BatteryPage(QWidget):
    """Battery health diagnostics and charge threshold limiter."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.battery_mgr = get_battery_manager()
        self._applied_limit: int = 80
        self._init_ui()

        # Periodic refresh timer (every 3 seconds); only refreshes while visible.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_battery_tick)
        self.timer.start(3000)

    def _on_battery_tick(self) -> None:
        """Timer entry point that skips work while the page is hidden."""
        if self.isVisible():
            self._refresh_battery_data()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        header = M3PageHeader(
            "Battery Care",
            "Health diagnostics, charge threshold & BMS calibration",
        )
        layout.addWidget(header)

        # ── Card 1: Battery Status & Diagnostics ──
        status_card = M3Card()
        s_layout = QVBoxLayout(status_card)
        s_layout.setContentsMargins(20, 18, 20, 18)
        s_layout.setSpacing(12)

        s_title = QLabel("Battery Status & Diagnostics")
        s_title.setProperty("class", "CardTitle")
        s_layout.addWidget(s_title)

        self.progress_bar = M3ProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        s_layout.addWidget(self.progress_bar)

        self.chips_container = M3ResponsiveGrid(
            wide_cols=3, narrow_cols=3, breakpoint=460, h_spacing=12, v_spacing=12
        )
        self.chips_container.addWidget(self._make_chip("Power Source", "ac_label", "plug"))
        self.chips_container.addWidget(self._make_chip("Battery Status", "status_label", "battery"))
        self.chips_container.addWidget(self._make_chip("Battery Health", "health_label", "heart"))
        s_layout.addWidget(self.chips_container)
        layout.addWidget(status_card)

        # ── Card 2: Battery Care & Charge Limiter ──
        self.care_card = M3Card()
        c_layout = QVBoxLayout(self.care_card)
        c_layout.setContentsMargins(20, 18, 20, 18)
        c_layout.setSpacing(14)

        c_title = QLabel("Battery Care & Charge Limiter")
        c_title.setProperty("class", "CardTitle")
        self.care_sub = QLabel(
            "Capping maximum charge at 80% significantly reduces chemical wear on lithium-ion "
            "cells, prolonging battery lifespan."
        )
        self.care_sub.setProperty("class", "CardSubtitle")
        self.care_sub.setWordWrap(True)
        c_layout.addWidget(c_title)
        c_layout.addWidget(self.care_sub)

        # Container for interactive slider controls (shown when supported)
        self.slider_container = QWidget()
        sc_layout = QVBoxLayout(self.slider_container)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.setSpacing(12)

        # Quick preset segmented bar
        p_label = QLabel("QUICK PRESETS")
        p_label.setProperty("class", "SectionLabel")
        sc_layout.addWidget(p_label)

        self._presets_bar = M3SegmentedBar()
        self._preset_buttons: Dict[int, QPushButton] = {}
        for p_val, p_text in [(60, "60% Storage"), (80, "80% Daily Care"), (100, "100% Full")]:
            btn = self._presets_bar.add_segment(p_text, checkable=True, min_height=38)
            btn.clicked.connect(lambda _, v=p_val: self._set_slider_value(v))
            self._preset_buttons[p_val] = btn
        sc_layout.addWidget(self._presets_bar)

        slider_layout = QHBoxLayout()
        slider_layout.setSpacing(12)
        self.slider_label = QLabel("Limit: 80%")
        self.slider_label.setProperty("class", "ValueReadoutPill")
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slider_label.setMinimumWidth(104)

        self.limit_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=True)
        self.limit_slider.setRange(40, 100)
        self.limit_slider.setValue(80)
        self.limit_slider.setSingleStep(5)
        self.limit_slider.valueChanged.connect(self._on_slider_changed)

        self.apply_limit_btn = QPushButton("Apply Limit")
        self.apply_limit_btn.setProperty("class", "PrimaryButton")
        self.apply_limit_btn.setMinimumHeight(38)
        self.apply_limit_btn.setIcon(
            icons.make_icon("check", color=THEME.on_accent_cta, size=15)
        )
        self.apply_limit_btn.setIconSize(QSize(15, 15))
        self.apply_limit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_limit_btn.clicked.connect(self._apply_charge_limit)

        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.limit_slider, stretch=1)
        slider_layout.addWidget(self.apply_limit_btn)
        sc_layout.addLayout(slider_layout)

        self.limit_status_label = QLabel("")
        self.limit_status_label.setStyleSheet(
            f"color: {THEME.success}; font-size: 12px; font-weight: 500;"
        )
        self.limit_status_label.setWordWrap(True)
        sc_layout.addWidget(self.limit_status_label)

        # Subtle 1px divider before One-Shot Top-Up
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {THEME.divider}; border: none;")
        sc_layout.addWidget(divider)

        # One-shot 100% Top-Up
        topup_row = QHBoxLayout()
        topup_row.setSpacing(12)
        self.topup_btn = QPushButton("Charge to 100% Once")
        self.topup_btn.setProperty("class", "TonalButton")
        self.topup_btn.setMinimumHeight(36)
        self.topup_btn.setIcon(
            icons.make_icon("bolt", color=THEME.on_primary_container, size=15)
        )
        self.topup_btn.setIconSize(QSize(15, 15))
        self.topup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.topup_btn.clicked.connect(self._on_topup_clicked)
        topup_row.addWidget(self.topup_btn)

        self.topup_status_label = QLabel("")
        self.topup_status_label.setStyleSheet(
            f"color: {THEME.info}; font-size: 12px; font-weight: 500;"
        )
        self.topup_status_label.setWordWrap(True)
        topup_row.addWidget(self.topup_status_label, stretch=1)
        sc_layout.addLayout(topup_row)

        c_layout.addWidget(self.slider_container)

        # Informational banner (shown when charge limiting or battery is unsupported)
        self.unsupported_notice = M3Banner(
            title="Hardware Charge Limiting Unavailable",
            body=(
                "Your laptop's Embedded Controller (EC) or current firmware does not expose a programmable "
                "charge threshold. Battery health monitoring and charging diagnostics remain active."
            ),
            tone="info",
            icon="info",
        )
        self.notice_title = self.unsupported_notice.title_lbl
        self.notice_body = self.unsupported_notice.body_lbl
        c_layout.addWidget(self.unsupported_notice)

        layout.addWidget(self.care_card)

        # ── Card 3: Periodic Calibration (BMS Health) ──
        self.cal_card = M3Card()
        cal_layout = QVBoxLayout(self.cal_card)
        cal_layout.setContentsMargins(20, 18, 20, 18)
        cal_layout.setSpacing(12)

        cal_title = QLabel("Periodic Calibration (BMS Health)")
        cal_title.setProperty("class", "CardTitle")
        cal_layout.addWidget(cal_title)

        cal_sub = QLabel(
            "Continuous charge limiting can cause the battery gauge (coulomb counter) to drift over time. "
            "Periodic calibration charges to 100% to reset the fuel gauge baseline. Once full, GigaMate stops "
            "charging and restores your limit; natural power draw will drift the battery back down without disconnecting AC."
        )
        cal_sub.setProperty("class", "CardSubtitle")
        cal_sub.setWordWrap(True)
        cal_layout.addWidget(cal_sub)

        # Switch row
        self._cal_switch_row = M3SwitchRow(
            "Enable automatic periodic calibration to 100%",
            subtitle="Periodically charges to 100% then restores your limit automatically.",
            checked=False,
        )
        self.auto_cal_chk = self._cal_switch_row.switch
        self.auto_cal_chk.toggled.connect(self._on_auto_cal_toggled)
        cal_layout.addWidget(self._cal_switch_row)

        # Interval selection row
        self.interval_row_widget = QWidget()
        i_layout = QVBoxLayout(self.interval_row_widget)
        i_layout.setContentsMargins(0, 0, 0, 0)
        i_layout.setSpacing(8)

        self._cal_interval_label = QLabel("Calibration Interval:")
        self._cal_interval_label.setProperty("class", "SectionLabel")
        i_layout.addWidget(self._cal_interval_label)

        self._interval_bar = M3SegmentedBar()
        self.interval_buttons: Dict[int, QPushButton] = {}
        for days, label_text in [(14, "14 Days"), (30, "30 Days (Recommended)"), (60, "60 Days")]:
            btn = self._interval_bar.add_segment(label_text, checkable=True, min_height=38)
            btn.clicked.connect(lambda _, d=days: self._set_cal_interval(d))
            self.interval_buttons[days] = btn
        i_layout.addWidget(self._interval_bar)
        cal_layout.addWidget(self.interval_row_widget)

        # Telemetry info (last full charge)
        self.last_full_label = QLabel("Last Full Charge: Calculating...")
        self.last_full_label.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 500;"
        )
        cal_layout.addWidget(self.last_full_label)

        layout.addWidget(self.cal_card)
        layout.addStretch()

        # Initial load
        self.reload_from_config()

    def _resolve_applied_limit(self, cfg: dict) -> int:
        """Resolve the currently active hardware/config charge limit."""
        hw_limit = None
        try:
            hw_limit = self.battery_mgr.get_charge_limit()
        except Exception:
            hw_limit = None
        topup_active = bool(cfg.get("charge_topup_active", False))
        if (
            isinstance(hw_limit, int)
            and not isinstance(hw_limit, bool)
            and 40 <= hw_limit <= 100
            and not topup_active
        ):
            return hw_limit
        return int(cfg.get("charge_limit", 80)) if cfg.get("charge_limit_enabled", False) else 100

    def _sync_calibration_enabled(self, enabled: bool) -> None:
        """Enable/disable the calibration interval row and dim its label."""
        self.interval_row_widget.setEnabled(enabled)
        color = THEME.on_surface_variant if enabled else THEME.on_surface_disabled
        self._cal_interval_label.setStyleSheet(f"color: {color};")

    def _sync_limit_action_state(
        self,
        feedback_msg: Optional[str] = None,
        feedback_ok: Optional[bool] = None,
    ) -> None:
        """Sync preset segments, Apply Limit button state, and status label."""
        cur = int(self.limit_slider.value())
        for v, b in self._preset_buttons.items():
            b.blockSignals(True)
            b.setChecked(cur == v)
            b.blockSignals(False)

        is_dirty = cur != self._applied_limit
        self.apply_limit_btn.setEnabled(is_dirty)
        self.apply_limit_btn.setCursor(
            Qt.CursorShape.PointingHandCursor
            if is_dirty
            else Qt.CursorShape.ArrowCursor
        )
        if is_dirty:
            self.apply_limit_btn.setToolTip(
                "Disable charge limit (charge to 100%)"
                if cur == 100
                else f"Apply {cur}% charge threshold"
            )
        else:
            self.apply_limit_btn.setToolTip(
                f"Charge limit ({self._applied_limit}%) is already applied"
            )

        if feedback_msg is not None:
            self.limit_status_label.setText(feedback_msg)
            color = THEME.success if feedback_ok else THEME.error
        elif is_dirty:
            self.limit_status_label.setText(
                "Disable charge limit (charge to 100%) — click Apply Limit"
                if cur == 100
                else f"Unsaved change: {self._applied_limit}% → {cur}% — click Apply Limit"
            )
            color = THEME.warning
        else:
            self.limit_status_label.setText(
                f"Active limit: {self._applied_limit}% (applied)"
                if self._applied_limit < 100
                else "Charge limiting disabled (charges to 100%)"
            )
            color = THEME.on_surface_muted
        self.limit_status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 500;"
        )

    def _sync_topup_button(self, topup_active: bool, info) -> None:
        """Update Charge to 100% Once button style, enabled state, and tooltip."""
        if topup_active:
            self.topup_btn.setText("Cancel 100% Top-Up")
            self.topup_btn.setProperty("class", "DangerButton")
            self.topup_btn.setIcon(
                icons.make_icon("close", color=THEME.on_accent_cta, size=15)
            )
            self.topup_btn.setEnabled(True)
            self.topup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.topup_btn.setToolTip(
                "Cancel temporary 100% charge and restore your saved limit"
            )
            self.topup_status_label.setText(
                "Top-Up Active (Will restore to limit once full)"
            )
        else:
            self.topup_btn.setText("Charge to 100% Once")
            self.topup_btn.setProperty("class", "TonalButton")
            self.topup_btn.setIcon(
                icons.make_icon("bolt", color=THEME.on_primary_container, size=15)
            )
            self.topup_status_label.setText("")

            already_full = info.capacity >= 100 or info.status == "Full"
            limit_off = self._applied_limit >= 100
            topup_allowed = not already_full and not limit_off
            self.topup_btn.setEnabled(topup_allowed)
            self.topup_btn.setCursor(
                Qt.CursorShape.PointingHandCursor
                if topup_allowed
                else Qt.CursorShape.ArrowCursor
            )
            if already_full:
                self.topup_btn.setToolTip("Battery is already at 100%")
            elif limit_off:
                self.topup_btn.setToolTip(
                    "Charge limiting is already disabled (charging to 100%)"
                )
            else:
                self.topup_btn.setToolTip(
                    "Temporarily charge to 100% once without changing your saved daily limit"
                )

        self.topup_btn.style().unpolish(self.topup_btn)
        self.topup_btn.style().polish(self.topup_btn)

    def reload_from_config(self) -> None:
        """Reload charge limit, calibration settings, and telemetry from saved config."""
        cfg = load_config()
        saved_limit = cfg.get("charge_limit", 80)
        self._applied_limit = self._resolve_applied_limit(cfg)
        self._set_slider_value(saved_limit)

        auto_cal = bool(cfg.get("charge_calibration_auto", False))
        self.auto_cal_chk.blockSignals(True)
        self.auto_cal_chk.setChecked(auto_cal)
        self.auto_cal_chk.blockSignals(False)
        self._sync_calibration_enabled(auto_cal)

        interval = int(cfg.get("charge_calibration_interval_days", 30))
        self._update_interval_buttons(interval)

        self._refresh_battery_data()
        self._sync_limit_action_state()

    def _update_interval_buttons(self, active_days: int) -> None:
        for days, btn in self.interval_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(days == active_days)
            btn.blockSignals(False)

    def _set_cal_interval(self, days: int) -> None:
        update_config(lambda c: {**c, "charge_calibration_interval_days": days})
        self._update_interval_buttons(days)

    def _on_auto_cal_toggled(self, checked: bool) -> None:
        update_config(lambda c: {**c, "charge_calibration_auto": checked})
        self._sync_calibration_enabled(checked)

    def _on_topup_clicked(self) -> None:
        cfg = load_config()
        if cfg.get("charge_topup_active"):
            cancel_topup(self.battery_mgr, restore_limit=True)
        else:
            start_topup(self.battery_mgr)
        self.reload_from_config()

    def _make_chip(self, title: str, label_attr_name: str, icon_name: str) -> QFrame:
        chip = QFrame()
        chip.setProperty("class", "StatusChip")
        chip.setMinimumWidth(0)
        c_lay = QVBoxLayout(chip)
        c_lay.setContentsMargins(14, 10, 14, 10)
        c_lay.setSpacing(3)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(15, 15)
        icon_lbl.setPixmap(
            icons.render_icon_pixmap(icon_name, color=THEME.on_surface_variant, size=15)
        )
        icon_lbl.setStyleSheet("background: transparent;")
        hdr.addWidget(icon_lbl)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; "
            f"text-transform: uppercase; letter-spacing: 0.6px; background: transparent;"
        )
        hdr.addWidget(t_lbl)
        hdr.addStretch()
        c_lay.addLayout(hdr)

        val_lbl = QLabel("Detecting...")
        val_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; "
            f"margin-top: 2px; background: transparent;"
        )
        c_lay.addWidget(val_lbl)
        setattr(self, label_attr_name, val_lbl)
        return chip

    def _set_slider_value(self, val: int) -> None:
        self.limit_slider.setValue(val)
        self.slider_label.setText(f"Limit: {val}%")
        self._sync_limit_action_state()

    def _on_slider_changed(self, val: int) -> None:
        self.slider_label.setText(f"Limit: {val}%")
        self._sync_limit_action_state()

    def _apply_charge_limit(self) -> None:
        val = self.limit_slider.value()
        # Slider at 100% means "no limit": disable the limiter so it is not
        # re-applied on boot/resume.
        enabled = val < 100
        try:
            success = self.battery_mgr.set_charge_limit(val)
            if success:
                def _mutate(cfg):
                    cfg["charge_limit"] = val
                    cfg["charge_limit_enabled"] = enabled
                    return cfg
                update_config(_mutate)
                self._applied_limit = val
                msg = (
                    f"Battery charge threshold successfully set to {val}%"
                    if enabled
                    else "Charge limiting disabled (charges to 100%)"
                )
                self._sync_limit_action_state(feedback_msg=msg, feedback_ok=True)
                cfg = load_config()
                self._sync_topup_button(
                    bool(cfg.get("charge_topup_active", False)),
                    self.battery_mgr.get_battery_info(),
                )
            else:
                self._sync_limit_action_state(
                    feedback_msg="Unable to write charge limit (unsupported on this firmware)",
                    feedback_ok=False,
                )
        except Exception as exc:
            self._sync_limit_action_state(feedback_msg=f"Error: {exc}", feedback_ok=False)

    def _refresh_battery_data(self) -> None:
        info = self.battery_mgr.get_battery_info()
        is_supported = self.battery_mgr.is_charge_limit_supported()

        if not info.present:
            self.progress_bar.setVisible(False)
            self.status_label.setText("Not Installed")
            self.ac_label.setText("AC Mains Online" if info.ac_online else "Unknown")
            self.health_label.setText("Continuous AC")

            # Update care card for desktop / no-battery mode
            self.slider_container.setVisible(False)
            self.care_sub.setVisible(False)
            self.cal_card.setVisible(False)
            self.unsupported_notice.set_tone("info", icon="bolt")
            self.notice_title.setText("Continuous AC Mains Power Mode")
            self.notice_body.setText(
                "No internal battery was detected on this system. Operating directly on continuous AC mains power. "
                "Battery charge threshold limiting is not applicable."
            )
            self.unsupported_notice.setVisible(True)
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(info.capacity)
        if info.capacity >= 100 or info.status == "Full":
            self.progress_bar.set_fill_color("#287E78")
        elif info.capacity >= 40:
            self.progress_bar.set_fill_color("#2D8A5A")
        elif info.capacity >= 20:
            self.progress_bar.set_fill_color("#B8741E")
        else:
            self.progress_bar.set_fill_color(THEME.danger_cta)

        self.status_label.setText(f"{info.status} ({info.capacity}%)")
        self.ac_label.setText("AC Connected" if info.ac_online else "On Battery")

        if info.health_percent is not None:
            cycles_str = f" ({info.cycle_count} cycles)" if info.cycle_count else ""
            self.health_label.setText(f"{info.health_percent:.1f}%{cycles_str}")
        else:
            self.health_label.setText("Available")

        cfg = load_config()
        try:
            cal = evaluate_calibration_state(self.battery_mgr, cfg)
            if cal.get("event") in ("completed", "timeout", "auto_started"):
                cfg = load_config()
        except Exception:
            pass

        self._applied_limit = self._resolve_applied_limit(cfg)
        topup_active = bool(cfg.get("charge_topup_active", False))
        self._sync_topup_button(topup_active, info)
        self._sync_limit_action_state()

        last_full = cfg.get("last_full_charge_ts", 0)
        if last_full > 0:
            days_ago = max(0, int((time.time() - last_full) / 86400))
            if days_ago == 0:
                age_str = "Today"
            elif days_ago == 1:
                age_str = "Yesterday"
            else:
                age_str = f"{days_ago} days ago"
            self.last_full_label.setText(f"Last Full Charge: {age_str}")
        else:
            self.last_full_label.setText("Last Full Charge: Never recorded")

        if is_supported:
            self.slider_container.setVisible(True)
            self.care_sub.setVisible(True)
            self.cal_card.setVisible(True)
            self.unsupported_notice.setVisible(False)
        else:
            self.slider_container.setVisible(False)
            self.care_sub.setVisible(False)
            self.cal_card.setVisible(False)
            self.unsupported_notice.set_tone("info", icon="info")
            self.notice_title.setText("Hardware Charge Limiting Unavailable")
            self.notice_body.setText(
                "Your laptop's Embedded Controller (EC) or current firmware does not expose a programmable "
                "charge threshold. Battery health monitoring and charging diagnostics remain active."
            )
            self.unsupported_notice.setVisible(True)
