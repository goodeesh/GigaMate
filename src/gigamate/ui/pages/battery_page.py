import time
from typing import Dict, Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...battery import (
    BatteryManager,
    get_battery_manager,
    evaluate_calibration_state,
    start_topup,
    cancel_topup,
)
from ...config import load as load_config, update_config
from ..flow_layout import FlowContainer


class BatteryPage(QWidget):
    """Battery health diagnostics and charge threshold limiter."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.battery_mgr = get_battery_manager()
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
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Battery Status & Health Card ──
        status_card = QFrame()
        status_card.setProperty("class", "Card")
        s_layout = QVBoxLayout(status_card)
        s_layout.setSpacing(10)

        s_title = QLabel("Battery Status & Diagnostics")
        s_title.setProperty("class", "CardTitle")
        s_layout.addWidget(s_title)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(22)
        s_layout.addWidget(self.progress_bar)

        # 3 Status Chips. A flow container so the row reflows onto a second line
        # instead of forcing the page wider than a narrow window can show.
        self.chips_container = FlowContainer(spacing=12)
        chips_layout = self.chips_container

        chips_layout.addWidget(self._make_chip("Power Source", "ac_label", "⚡"))
        chips_layout.addWidget(self._make_chip("Battery Status", "status_label", "🔋"))
        chips_layout.addWidget(self._make_chip("Battery Health", "health_label", "❤️"))
        s_layout.addWidget(self.chips_container)
        layout.addWidget(status_card)

        # ── Battery Care & Charge Threshold Card ──
        self.care_card = QFrame()
        self.care_card.setProperty("class", "Card")
        c_layout = QVBoxLayout(self.care_card)
        c_layout.setSpacing(14)

        c_title = QLabel("Battery Care & Charge Limiter")
        c_title.setProperty("class", "CardTitle")
        self.care_sub = QLabel(
            "Capping maximum charge at 80% significantly reduces chemical wear on lithium-ion "
            "cells, prolonging battery lifespan."
        )
        self.care_sub.setProperty("class", "CardSubtitle")
        # Prose must wrap, otherwise its single-line width becomes the page's
        # minimum width and the card is clipped on a narrow window.
        self.care_sub.setWordWrap(True)
        c_layout.addWidget(c_title)
        c_layout.addWidget(self.care_sub)

        # Container for interactive slider controls (shown when supported)
        self.slider_container = QWidget()
        sc_layout = QVBoxLayout(self.slider_container)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.setSpacing(12)

        # Quick preset buttons
        presets_row = QHBoxLayout()
        presets_row.setSpacing(8)
        p_label = QLabel("Quick Presets:")
        p_label.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
        presets_row.addWidget(p_label)

        for p_val, p_text in [(60, "60% Storage"), (80, "80% Daily Care"), (100, "100% Full")]:
            btn = QPushButton(p_text)
            btn.setProperty("class", "PresetButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, v=p_val: self._set_slider_value(v))
            presets_row.addWidget(btn)
        presets_row.addStretch()
        sc_layout.addLayout(presets_row)

        slider_layout = QHBoxLayout()
        slider_layout.setSpacing(12)
        self.slider_label = QLabel("Limit: 80%")
        self.slider_label.setProperty("class", "ValueReadoutPill")
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slider_label.setMinimumWidth(100)

        self.limit_slider = QSlider(Qt.Orientation.Horizontal)
        self.limit_slider.setRange(40, 100)
        self.limit_slider.setValue(80)
        self.limit_slider.setSingleStep(5)
        self.limit_slider.valueChanged.connect(self._on_slider_changed)

        self.apply_limit_btn = QPushButton("Apply Limit")
        self.apply_limit_btn.setProperty("class", "PrimaryButton")
        self.apply_limit_btn.setMinimumHeight(34)
        self.apply_limit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_limit_btn.clicked.connect(self._apply_charge_limit)

        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.limit_slider)
        slider_layout.addWidget(self.apply_limit_btn)
        sc_layout.addLayout(slider_layout)

        self.limit_status_label = QLabel("")
        self.limit_status_label.setStyleSheet("color: #38a169; font-size: 12px; font-weight: 500;")
        sc_layout.addWidget(self.limit_status_label)

        # One-shot 100% Top-Up
        topup_row = QHBoxLayout()
        topup_row.setSpacing(12)
        self.topup_btn = QPushButton("⚡ Charge to 100% Once")
        self.topup_btn.setProperty("class", "PrimaryButton")
        self.topup_btn.setMinimumHeight(32)
        self.topup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.topup_btn.clicked.connect(self._on_topup_clicked)
        topup_row.addWidget(self.topup_btn)

        self.topup_status_label = QLabel("")
        self.topup_status_label.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 500;")
        topup_row.addWidget(self.topup_status_label)
        topup_row.addStretch()
        sc_layout.addLayout(topup_row)

        c_layout.addWidget(self.slider_container)

        # Informational box (shown when charge limiting or battery is unsupported)
        self.unsupported_notice = QFrame()
        self.unsupported_notice.setStyleSheet(
            "background-color: #171b26; border: 1px solid #283347; border-radius: 8px; padding: 14px;"
        )
        u_lay = QVBoxLayout(self.unsupported_notice)
        u_lay.setContentsMargins(14, 12, 14, 12)
        u_lay.setSpacing(4)

        self.notice_title = QLabel("ℹ️ Hardware Charge Limiting Unavailable")
        self.notice_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 600;")
        self.notice_body = QLabel(
            "Your laptop's Embedded Controller (EC) or current firmware does not expose a programmable "
            "charge threshold. Battery health monitoring and charging diagnostics remain active."
        )
        self.notice_body.setStyleSheet("color: #8896ab; font-size: 12px;")
        self.notice_body.setWordWrap(True)

        u_lay.addWidget(self.notice_title)
        u_lay.addWidget(self.notice_body)
        c_layout.addWidget(self.unsupported_notice)

        layout.addWidget(self.care_card)

        # ── Periodic Calibration (BMS Health) Card ──
        self.cal_card = QFrame()
        self.cal_card.setProperty("class", "Card")
        cal_layout = QVBoxLayout(self.cal_card)
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

        # Toggle row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        self.auto_cal_chk = QCheckBox("Enable automatic periodic calibration to 100%")
        self.auto_cal_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_cal_chk.toggled.connect(self._on_auto_cal_toggled)
        toggle_row.addWidget(self.auto_cal_chk)
        toggle_row.addStretch()
        cal_layout.addLayout(toggle_row)

        # Interval selection row
        self.interval_row_widget = QWidget()
        i_layout = QHBoxLayout(self.interval_row_widget)
        i_layout.setContentsMargins(0, 0, 0, 0)
        i_layout.setSpacing(8)

        i_label = QLabel("Calibration Interval:")
        i_label.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
        i_layout.addWidget(i_label)

        self.interval_buttons: Dict[int, QPushButton] = {}
        for days, label_text in [(14, "14 Days"), (30, "30 Days (Recommended)"), (60, "60 Days")]:
            btn = QPushButton(label_text)
            btn.setProperty("class", "PresetButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, d=days: self._set_cal_interval(d))
            self.interval_buttons[days] = btn
            i_layout.addWidget(btn)
        i_layout.addStretch()
        cal_layout.addWidget(self.interval_row_widget)

        # Telemetry info (last full charge)
        self.last_full_label = QLabel("Last Full Charge: Calculating...")
        self.last_full_label.setStyleSheet("color: #8896ab; font-size: 12px; font-weight: 500;")
        cal_layout.addWidget(self.last_full_label)

        layout.addWidget(self.cal_card)
        layout.addStretch()

        # Initial load
        self.reload_from_config()

    def reload_from_config(self) -> None:
        """Reload charge limit, calibration settings, and telemetry from saved config."""
        cfg = load_config()
        saved_limit = cfg.get("charge_limit", 80)
        self._set_slider_value(saved_limit)

        auto_cal = bool(cfg.get("charge_calibration_auto", False))
        self.auto_cal_chk.blockSignals(True)
        self.auto_cal_chk.setChecked(auto_cal)
        self.auto_cal_chk.blockSignals(False)
        self.interval_row_widget.setEnabled(auto_cal)

        interval = int(cfg.get("charge_calibration_interval_days", 30))
        self._update_interval_buttons(interval)

        self._refresh_battery_data()

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
        self.interval_row_widget.setEnabled(checked)

    def _on_topup_clicked(self) -> None:
        cfg = load_config()
        if cfg.get("charge_topup_active"):
            cancel_topup(self.battery_mgr, restore_limit=True)
        else:
            start_topup(self.battery_mgr)
        self.reload_from_config()

    def _make_chip(self, title: str, label_attr_name: str, icon_str: str) -> QFrame:
        chip = QFrame()
        chip.setProperty("class", "StatusChip")
        c_lay = QVBoxLayout(chip)
        c_lay.setContentsMargins(14, 10, 14, 10)
        c_lay.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel(icon_str)
        icon_lbl.setStyleSheet("font-size: 13px;")
        hdr.addWidget(icon_lbl)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("color: #718096; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;")
        hdr.addWidget(t_lbl)
        hdr.addStretch()
        c_lay.addLayout(hdr)

        val_lbl = QLabel("Detecting...")
        val_lbl.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 600; margin-top: 2px;")
        c_lay.addWidget(val_lbl)
        setattr(self, label_attr_name, val_lbl)
        return chip

    def _set_slider_value(self, val: int) -> None:
        self.limit_slider.setValue(val)
        self.slider_label.setText(f"Limit: {val}%")

    def _on_slider_changed(self, val: int) -> None:
        self.slider_label.setText(f"Limit: {val}%")

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
                if enabled:
                    self.limit_status_label.setText(f"✓ Battery charge threshold successfully set to {val}%")
                else:
                    self.limit_status_label.setText("✓ Charge limiting disabled (charges to 100%)")
                self.limit_status_label.setStyleSheet("color: #38a169; font-size: 12px;")
            else:
                self.limit_status_label.setText("⚠ Unable to write charge limit (unsupported on this firmware)")
                self.limit_status_label.setStyleSheet("color: #e53e3e; font-size: 12px;")
        except Exception as exc:
            self.limit_status_label.setText(f"Error: {exc}")
            self.limit_status_label.setStyleSheet("color: #e53e3e; font-size: 12px;")

    def _refresh_battery_data(self) -> None:
        info = self.battery_mgr.get_battery_info()
        is_supported = self.battery_mgr.is_charge_limit_supported()

        if not info.present:
            self.progress_bar.setVisible(False)
            self.status_label.setText("Status: Not Installed")
            self.ac_label.setText(f"Power: {'⚡ AC Mains Online' if info.ac_online else 'Unknown'}")
            self.health_label.setText("Health: Continuous AC")

            # Update care card for desktop / no-battery mode
            self.slider_container.setVisible(False)
            self.care_sub.setVisible(False)
            self.cal_card.setVisible(False)
            self.unsupported_notice.setVisible(True)
            self.notice_title.setText("⚡ Continuous AC Mains Power Mode")
            self.notice_body.setText(
                "No internal battery was detected on this system. Operating directly on continuous AC mains power. "
                "Battery charge threshold limiting is not applicable."
            )
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(info.capacity)
        self.status_label.setText(f"Status: {info.status} ({info.capacity}%)")
        self.ac_label.setText(f"Power: {'⚡ AC Connected' if info.ac_online else '🔋 On Battery'}")

        if info.health_percent is not None:
            cycles_str = f" ({info.cycle_count} cycles)" if info.cycle_count else ""
            self.health_label.setText(f"Health: {info.health_percent:.1f}%{cycles_str}")
        else:
            self.health_label.setText("Health: Available")

        cfg = load_config()
        try:
            cal = evaluate_calibration_state(self.battery_mgr, cfg)
            if cal.get("event") in ("completed", "timeout", "auto_started"):
                cfg = load_config()
        except Exception:
            pass

        topup_active = bool(cfg.get("charge_topup_active", False))
        if topup_active:
            self.topup_btn.setText("✕ Cancel 100% Top-Up")
            self.topup_btn.setProperty("class", "DangerButton")
            self.topup_status_label.setText("● Top-Up Active (Will restore to limit once full)")
        else:
            self.topup_btn.setText("⚡ Charge to 100% Once")
            self.topup_btn.setProperty("class", "PrimaryButton")
            self.topup_status_label.setText("")

        self.topup_btn.style().unpolish(self.topup_btn)
        self.topup_btn.style().polish(self.topup_btn)

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
            self.unsupported_notice.setVisible(True)
            self.notice_title.setText("ℹ️ Hardware Charge Limiting Unavailable")
            self.notice_body.setText(
                "Your laptop's Embedded Controller (EC) or current firmware does not expose a programmable "
                "charge threshold. Battery health monitoring and charging diagnostics remain active."
            )
