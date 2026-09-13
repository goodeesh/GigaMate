"""GigaMate Center — Battery Care & Health Page."""

from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...battery import BatteryManager, get_battery_manager
from ...config import load as load_config, save as save_config


class BatteryPage(QWidget):
    """Battery health diagnostics and charge threshold limiter."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.battery_mgr = get_battery_manager()
        self._init_ui()

        # Periodic refresh timer (every 3 seconds)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_battery_data)
        self.timer.start(3000)

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

        # 3 Status Chips
        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(12)

        chips_layout.addWidget(self._make_chip("Power Source", "ac_label", "⚡"))
        chips_layout.addWidget(self._make_chip("Battery Status", "status_label", "🔋"))
        chips_layout.addWidget(self._make_chip("Battery Health", "health_label", "❤️"))
        s_layout.addLayout(chips_layout)
        layout.addWidget(status_card)

        # ── Battery Care & Charge Threshold Card ──
        care_card = QFrame()
        care_card.setProperty("class", "Card")
        c_layout = QVBoxLayout(care_card)
        c_layout.setSpacing(14)

        c_title = QLabel("Battery Care & Charge Limiter")
        c_title.setProperty("class", "CardTitle")
        c_sub = QLabel(
            "Capping maximum charge at 80% significantly reduces chemical wear on lithium-ion "
            "cells, prolonging battery lifespan."
        )
        c_sub.setProperty("class", "CardSubtitle")
        c_layout.addWidget(c_title)
        c_layout.addWidget(c_sub)

        # Slider row
        slider_layout = QHBoxLayout()
        slider_layout.setSpacing(14)
        self.slider_label = QLabel("Limit: 80%")
        self.slider_label.setStyleSheet("color: #ffffff; font-weight: 600; font-size: 14px; min-width: 90px;")

        self.limit_slider = QSlider(Qt.Orientation.Horizontal)
        self.limit_slider.setRange(40, 100)
        self.limit_slider.setValue(80)
        self.limit_slider.setSingleStep(5)
        self.limit_slider.valueChanged.connect(self._on_slider_changed)

        self.apply_limit_btn = QPushButton("Apply Limit")
        self.apply_limit_btn.setProperty("class", "PrimaryButton")
        self.apply_limit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_limit_btn.clicked.connect(self._apply_charge_limit)

        slider_layout.addWidget(self.slider_label)
        slider_layout.addWidget(self.limit_slider)
        slider_layout.addWidget(self.apply_limit_btn)
        c_layout.addLayout(slider_layout)

        self.limit_status_label = QLabel("")
        self.limit_status_label.setStyleSheet("color: #38a169; font-size: 12px;")
        c_layout.addWidget(self.limit_status_label)
        layout.addWidget(care_card)

        layout.addStretch()

        # Initial load
        cfg = load_config()
        active_limit = self.battery_mgr.get_charge_limit() or cfg.get("charge_limit", 80)
        self._set_slider_value(active_limit)
        self._refresh_battery_data()

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
        try:
            success = self.battery_mgr.set_charge_limit(val)
            if success:
                cfg = load_config()
                cfg["charge_limit"] = val
                cfg["charge_limit_enabled"] = True
                save_config(cfg)
                self.limit_status_label.setText(f"✓ Battery charge threshold successfully set to {val}%")
                self.limit_status_label.setStyleSheet("color: #38a169; font-size: 12px;")
            else:
                self.limit_status_label.setText("⚠ Unable to write charge limit (unsupported on this firmware)")
                self.limit_status_label.setStyleSheet("color: #e53e3e; font-size: 12px;")
        except Exception as exc:
            self.limit_status_label.setText(f"Error: {exc}")
            self.limit_status_label.setStyleSheet("color: #e53e3e; font-size: 12px;")

    def _refresh_battery_data(self) -> None:
        info = self.battery_mgr.get_battery_info()
        if not info.present:
            self.status_label.setText("Status: No battery found")
            self.ac_label.setText(f"Power: {'⚡ AC Connected' if info.ac_online else 'Unknown'}")
            self.health_label.setText("Health: N/A")
            self.progress_bar.setValue(0)
            return

        self.progress_bar.setValue(info.capacity)
        self.status_label.setText(f"Status: {info.status} ({info.capacity}%)")
        self.ac_label.setText(f"Power: {'⚡ AC Connected' if info.ac_online else '🔋 On Battery'}")

        if info.health_percent is not None:
            cycles_str = f" ({info.cycle_count} cycles)" if info.cycle_count else ""
            self.health_label.setText(f"Health: {info.health_percent:.1f}%{cycles_str}")
        else:
            self.health_label.setText("Health: Available")
