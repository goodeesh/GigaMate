"""GigaMate Center — Battery Care, Charge Limiter & Cell Health Page."""

from __future__ import annotations

import time
from typing import Dict, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
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
    ArcGaugeWidget,
    BatteryTankWidget,
    LinkPillButton,
    M3Banner,
    M3Card,
    M3PageHeader,
    M3ResponsiveGrid,
    M3SegmentedBar,
    M3Slider,
)


class BatteryPage(QWidget):
    """Battery health diagnostics and charge threshold limiter."""

    navigate_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.battery_mgr = get_battery_manager()
        self._applied_limit: int = 80
        self._accent_hex: str = THEME.primary
        self._last_pct: int = 0
        self._last_topup: bool = False
        self._last_present: bool = True
        self._init_ui()

        # Periodic refresh timer (every 3 seconds); only refreshes while visible.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_battery_tick)
        self.timer.start(3000)

    def set_accent(self, accent_hex: str) -> None:
        """Update the dynamic profile accent color used by the battery tank."""
        self._accent_hex = accent_hex or THEME.primary
        self.battery_tank.set_state(
            self._last_pct,
            int(self.limit_slider.value()),
            topup_active=self._last_topup,
            accent_hex=self._accent_hex,
            present=self._last_present,
        )

    def _on_battery_tick(self) -> None:
        """Timer entry point that skips work while the page is hidden."""
        if self.isVisible():
            self._refresh_battery_data()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)

        header = M3PageHeader(
            "Battery Care",
            "Charge threshold tank, one-time top-up & cell health",
        )
        self.cal_link_pill = LinkPillButton("Auto-Calibration: OFF ↗", "automation:calibration")
        self.cal_link_pill.navigate_requested.connect(self.navigate_requested.emit)
        header.set_trailing_widget(self.cal_link_pill)
        layout.addWidget(header)

        # ── Card 1: Charge Level & Limiter Tank ──
        self.care_card = M3Card()
        c_layout = QVBoxLayout(self.care_card)
        c_layout.setContentsMargins(18, 16, 18, 16)
        c_layout.setSpacing(12)

        c_title = QLabel("Charge Level & Limiter Tank")
        c_title.setProperty("class", "CardTitle")
        self.care_sub = QLabel(
            "Capping maximum charge at 80% significantly reduces chemical wear on lithium-ion "
            "cells, prolonging battery lifespan."
        )
        self.care_sub.setProperty("class", "CardSubtitle")
        self.care_sub.setWordWrap(True)
        c_layout.addWidget(c_title)
        c_layout.addWidget(self.care_sub)

        self.battery_tank = BatteryTankWidget()
        self.progress_bar = self.battery_tank
        c_layout.addWidget(self.battery_tank)

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
            btn.clicked.connect(lambda _, v=p_val: self._on_preset_clicked(v))
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
        self.limit_slider.sliderReleased.connect(self._apply_charge_limit)

        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.limit_slider, stretch=1)
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

        # ── Card 2: Battery Health & Wear ──
        health_card = M3Card()
        h_layout = QVBoxLayout(health_card)
        h_layout.setContentsMargins(18, 16, 18, 16)
        h_layout.setSpacing(12)

        h_title = QLabel("Battery Health & Wear")
        h_title.setProperty("class", "CardTitle")
        h_layout.addWidget(h_title)

        self.health_grid = M3ResponsiveGrid(
            wide_cols=3, narrow_cols=1, breakpoint=500, h_spacing=12, v_spacing=12
        )

        # Tile 1: Health Arc Gauge + Full vs Design Wh + health_label
        tile1 = QFrame()
        tile1.setProperty("class", "MetricTile")
        t1_layout = QVBoxLayout(tile1)
        t1_layout.setContentsMargins(12, 12, 12, 12)
        t1_layout.setSpacing(6)
        t1_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.health_gauge = ArcGaugeWidget("HEALTH", "%", 0, 100, size=88)
        t1_layout.addWidget(self.health_gauge, alignment=Qt.AlignmentFlag.AlignCenter)

        self.wh_label = QLabel("-- / -- Wh")
        self.wh_label.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; font-weight: 600;"
        )
        self.wh_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t1_layout.addWidget(self.wh_label)

        self.health_label = QLabel("--")
        self.health_label.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; font-weight: 500;"
        )
        self.health_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t1_layout.addWidget(self.health_label)
        self.health_grid.addWidget(tile1)

        # Tile 2: Power Source + Cycle Count
        tile2 = QFrame()
        tile2.setProperty("class", "MetricTile")
        t2_layout = QVBoxLayout(tile2)
        t2_layout.setContentsMargins(12, 12, 12, 12)
        t2_layout.setSpacing(6)

        t2_hdr = QHBoxLayout()
        t2_hdr.setSpacing(6)
        t2_icon = QLabel()
        t2_icon.setPixmap(
            icons.make_icon("plug", color=THEME.on_surface_muted, size=14).pixmap(14, 14)
        )
        t2_title = QLabel("POWER SOURCE")
        t2_title.setProperty("class", "MetricTitle")
        t2_hdr.addWidget(t2_icon)
        t2_hdr.addWidget(t2_title)
        t2_hdr.addStretch()
        t2_layout.addLayout(t2_hdr)

        self.ac_label = QLabel("--")
        self.ac_label.setProperty("class", "MetricValue")
        self.ac_label.setWordWrap(True)
        t2_layout.addWidget(self.ac_label)

        self.cycles_label = QLabel("-- cycles")
        self.cycles_label.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 500;"
        )
        self.cycles_label.setWordWrap(True)
        t2_layout.addWidget(self.cycles_label)
        t2_layout.addStretch()
        self.health_grid.addWidget(tile2)

        # Tile 3: Battery Status + Last Full Charge
        tile3 = QFrame()
        tile3.setProperty("class", "MetricTile")
        t3_layout = QVBoxLayout(tile3)
        t3_layout.setContentsMargins(12, 12, 12, 12)
        t3_layout.setSpacing(6)

        t3_hdr = QHBoxLayout()
        t3_hdr.setSpacing(6)
        t3_icon = QLabel()
        t3_icon.setPixmap(
            icons.make_icon("battery", color=THEME.on_surface_muted, size=14).pixmap(14, 14)
        )
        t3_title = QLabel("BATTERY STATUS")
        t3_title.setProperty("class", "MetricTitle")
        t3_hdr.addWidget(t3_icon)
        t3_hdr.addWidget(t3_title)
        t3_hdr.addStretch()
        t3_layout.addLayout(t3_hdr)

        self.status_label = QLabel("--")
        self.status_label.setProperty("class", "MetricValue")
        self.status_label.setWordWrap(True)
        t3_layout.addWidget(self.status_label)

        self.last_full_label = QLabel("Last Full Charge: Calculating...")
        self.last_full_label.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 500;"
        )
        self.last_full_label.setWordWrap(True)
        t3_layout.addWidget(self.last_full_label)
        t3_layout.addStretch()
        self.health_grid.addWidget(tile3)

        h_layout.addWidget(self.health_grid)
        layout.addWidget(health_card)
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

    def _sync_cal_pill(self, cfg: dict) -> None:
        """Update the header Auto-Calibration link pill from config."""
        auto_cal = bool(cfg.get("charge_calibration_auto", False))
        if auto_cal:
            days = int(cfg.get("charge_calibration_interval_days", 30))
            self.cal_link_pill.setText(f"Auto-Calibration: {days}d ↗")
        else:
            self.cal_link_pill.setText("Auto-Calibration: OFF ↗")

    def _sync_limit_action_state(
        self,
        feedback_msg: Optional[str] = None,
        feedback_ok: Optional[bool] = None,
    ) -> None:
        """Sync preset segments and status label."""
        cur = int(self.limit_slider.value())
        for v, b in self._preset_buttons.items():
            b.blockSignals(True)
            b.setChecked(cur == v)
            b.blockSignals(False)

        if feedback_msg is not None:
            color = THEME.success if feedback_ok else THEME.warning
            self.limit_status_label.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 500;"
            )
            self.limit_status_label.setText(feedback_msg)
        elif cur == 100:
            self.limit_status_label.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 500;"
            )
            self.limit_status_label.setText("Charge limit disabled (charges to 100%).")
        else:
            self.limit_status_label.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 500;"
            )
            self.limit_status_label.setText(f"Active threshold: {self._applied_limit}%")

    def _sync_topup_button(self, topup_active: bool, info) -> None:
        """Update the One-Shot 100% Top-Up button and status caption."""
        limit_active = self._applied_limit < 100
        if topup_active:
            self.topup_btn.setText("Cancel 100% Top-Up")
            self.topup_btn.setProperty("class", "DangerButton")
            self.topup_btn.setIcon(
                icons.make_icon("close", color=THEME.on_surface, size=15)
            )
            self.topup_btn.setEnabled(True)
            self.topup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.topup_status_label.setStyleSheet(
                f"color: {THEME.info}; font-size: 12px; font-weight: 500;"
            )
            self.topup_status_label.setText(
                f"Top-Up active — charging to 100% ({info.capacity}%), "
                f"will restore {self._applied_limit}% automatically"
            )
        elif not limit_active:
            self.topup_btn.setText("Charge to 100% Once")
            self.topup_btn.setProperty("class", "TonalButton")
            self.topup_btn.setIcon(
                icons.make_icon("bolt", color=THEME.on_surface_disabled, size=15)
            )
            self.topup_btn.setEnabled(False)
            self.topup_btn.setCursor(Qt.CursorShape.ArrowCursor)
            self.topup_status_label.setStyleSheet(
                f"color: {THEME.on_surface_disabled}; font-size: 12px; font-weight: 500;"
            )
            self.topup_status_label.setText("Set a limit below 100% to use one-shot top-up.")
        else:
            self.topup_btn.setText("Charge to 100% Once")
            self.topup_btn.setProperty("class", "TonalButton")
            self.topup_btn.setIcon(
                icons.make_icon("bolt", color=THEME.on_primary_container, size=15)
            )
            self.topup_btn.setEnabled(True)
            self.topup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.topup_status_label.setText("")

        # Re-polish button so dynamic class swap takes effect immediately
        self.topup_btn.style().unpolish(self.topup_btn)
        self.topup_btn.style().polish(self.topup_btn)

    def reload_from_config(self) -> None:
        """Reload charge limit and calibration pill state from config."""
        cfg = load_config()
        self._applied_limit = self._resolve_applied_limit(cfg)
        saved_limit = int(cfg.get("charge_limit", 80))
        self.limit_slider.blockSignals(True)
        self._set_slider_value(saved_limit)
        self.limit_slider.blockSignals(False)
        self._sync_cal_pill(cfg)
        self._sync_limit_action_state()
        self._refresh_battery_data()

    def _on_topup_clicked(self) -> None:
        cfg = load_config()
        if cfg.get("charge_topup_active", False):
            cancel_topup(self.battery_mgr)
        else:
            start_topup(self.battery_mgr)
        self._refresh_battery_data()

    def _on_preset_clicked(self, val: int) -> None:
        self._set_slider_value(val)
        self._apply_charge_limit()

    def _set_slider_value(self, val: int) -> None:
        self.limit_slider.setValue(val)
        self.slider_label.setText(f"Limit: {val}%")
        self._sync_limit_action_state()
        self.battery_tank.set_state(
            self._last_pct,
            val,
            topup_active=self._last_topup,
            accent_hex=self._accent_hex,
            present=self._last_present,
        )

    def _on_slider_changed(self, val: int) -> None:
        self.slider_label.setText(f"Limit: {val}%")
        self._sync_limit_action_state()
        self.battery_tank.set_state(
            self._last_pct,
            val,
            topup_active=self._last_topup,
            accent_hex=self._accent_hex,
            present=self._last_present,
        )

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
        cfg = load_config()
        self._sync_cal_pill(cfg)

        self.battery_tank.setVisible(bool(info.present))

        if not info.present:
            self._last_pct = 0
            self._last_topup = False
            self._last_present = False
            self.battery_tank.set_state(
                0,
                int(self.limit_slider.value()),
                topup_active=False,
                accent_hex=self._accent_hex,
                present=False,
            )
            self.health_gauge.set_value(None, color=THEME.on_surface_muted, animated=False)
            self.wh_label.setText("-- / -- Wh")
            self.health_label.setText("Continuous AC")
            self.ac_label.setText("AC Mains Online" if info.ac_online else "Unknown")
            self.cycles_label.setText("-- cycles")
            self.status_label.setText("Not Installed")
            self.last_full_label.setText("Last Full Charge: N/A")

            # Update care card for desktop / no-battery mode
            self.slider_container.setVisible(False)
            self.care_sub.setVisible(False)
            self.unsupported_notice.set_tone("info", icon="bolt")
            self.notice_title.setText("Continuous AC Mains Power Mode")
            self.notice_body.setText(
                "No internal battery was detected on this system. Operating directly on continuous AC mains power. "
                "Battery charge threshold limiting is not applicable."
            )
            self.unsupported_notice.setVisible(True)
            return

        try:
            cal = evaluate_calibration_state(self.battery_mgr, cfg)
            if cal.get("event") in ("completed", "timeout", "auto_started"):
                cfg = load_config()
        except Exception:
            pass

        self._applied_limit = self._resolve_applied_limit(cfg)
        topup_active = bool(cfg.get("charge_topup_active", False))

        self._last_pct = info.capacity
        self._last_topup = topup_active
        self._last_present = True
        self.battery_tank.set_state(
            info.capacity,
            int(self.limit_slider.value()),
            topup_active=topup_active,
            accent_hex=self._accent_hex,
            present=True,
        )

        self.status_label.setText(f"{info.status} ({info.capacity}%)")
        self.ac_label.setText("AC Connected" if info.ac_online else "On Battery")
        self.cycles_label.setText(
            f"{info.cycle_count} cycles" if info.cycle_count is not None else "-- cycles"
        )

        if info.health_percent is not None:
            gauge_color = (
                THEME.success
                if info.health_percent >= 80.0
                else (THEME.warning if info.health_percent >= 60.0 else THEME.error)
            )
            self.health_gauge.set_value(info.health_percent, color=gauge_color)
            self.health_label.setText(f"{info.health_percent:.1f}% capacity")
        else:
            self.health_gauge.set_value(None, color=THEME.on_surface_muted)
            self.health_label.setText("Available")

        if info.charge_full is not None and info.charge_full_design is not None:
            if info.charge_full >= 15_000_000:
                full_wh = info.charge_full / 1_000_000.0
                design_wh = info.charge_full_design / 1_000_000.0
                self.wh_label.setText(f"{full_wh:.1f} / {design_wh:.1f} Wh")
            else:
                self.wh_label.setText(
                    f"{info.charge_full // 1000:,} / {info.charge_full_design // 1000:,} mAh"
                )
        else:
            self.wh_label.setText("-- / -- Wh")

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
            self.unsupported_notice.setVisible(False)
        else:
            self.slider_container.setVisible(False)
            self.care_sub.setVisible(False)
            self.unsupported_notice.set_tone("info", icon="info")
            self.notice_title.setText("Hardware Charge Limiting Unavailable")
            self.notice_body.setText(
                "Your laptop's Embedded Controller (EC) or current firmware does not expose a programmable "
                "charge threshold. Battery health monitoring and charging diagnostics remain active."
            )
            self.unsupported_notice.setVisible(True)
