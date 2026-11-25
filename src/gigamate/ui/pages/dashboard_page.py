"""GigaMate Center — Aero Instrument Overview Page."""

from __future__ import annotations

import time
from typing import Optional
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import dgpu_gate, hardware
from ...acpi import AcpiController, FanState
from ...capabilities import detect_system_capabilities
from ...config import (
    effective_brightness_pct,
    effective_rgb_hex,
    enabled_acpi_profiles,
    is_rgb_enabled,
    load as load_config,
    pct_to_legacy_brightness,
    profile_rgb_for,
    update_config,
)
from ...gpu import GpuState, get_gpu_state
from ...power_limits import SystemPowerLimits, _is_on_battery, discover_acpi_profiles, discover_power_limits
from .. import icons
from ..theme import PROFILE_ACCENTS, THEME
from ..widgets import (
    ArcGaugeWidget,
    LinkPillButton,
    M3Banner,
    M3Card,
    M3PageHeader,
    M3ResponsiveGrid,
    M3SegmentedBar,
    M3Slider,
    M3StatusPill,
    PowerEnvelopeBarWidget,
    SparklineWidget,
    SpinningFanIcon,
)


class DashboardPage(QWidget):
    """Aero Instrument Overview: power profiles, live thermals, and quick hardware controls."""

    navigate_requested = pyqtSignal(str)
    profile_accent_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.acpi_ctrl = AcpiController()
        self._profile_buttons: dict = {}
        self._cached_profile: Optional[int] = None
        self._accent_hex: str = THEME.primary
        self._last_emitted_profile: Optional[int] = None
        self._cpu_history: list[float] = []
        self._init_ui()

        # Telemetry update timer (every 1.5s); only refreshes while visible.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_telemetry_tick)
        self.timer.start(1500)

    def _on_telemetry_tick(self) -> None:
        """Timer entry point that skips work while the page is hidden."""
        if self.isVisible():
            self._refresh_telemetry()

    def reload_from_config(self) -> None:
        """Public page-lifecycle hook: refresh telemetry, profiles, and quick cards."""
        self._refresh_telemetry()

    def set_accent(self, accent_hex: str) -> None:
        """Update dynamic accent colors on Overview widgets."""
        self._accent_hex = accent_hex
        if hasattr(self, "cpu_sparkline"):
            self.cpu_sparkline.set_color(accent_hex)
        if hasattr(self, "active_profile_pill"):
            self.active_profile_pill.setStyleSheet(
                f"color: {accent_hex}; background-color: {THEME.surface_container_high}; "
                f"border: 1px solid {accent_hex}; border-radius: 999px; "
                f"padding: 2px 10px; font-size: 11px; font-weight: 700;"
            )

    def _discovered_profiles(self) -> dict:
        """Discover ACPI power profiles from sysfs, falling back to controller capabilities."""
        try:
            profs = discover_acpi_profiles()
            if profs:
                return profs
        except Exception:
            pass
        if getattr(self, "acpi_ctrl", None) is not None and self.acpi_ctrl.available:
            return dict(getattr(self.acpi_ctrl.capabilities, "discovered_profiles", {}) or {})
        return {}

    def _config(self) -> dict:
        try:
            return load_config()
        except Exception:
            return {}

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        header = M3PageHeader(
            "Overview",
            "Hardware profiles, live thermals & quick controls",
        )
        layout.addWidget(header)

        # ── Hero Power Profile Card ──
        prof_card = M3Card()
        prof_layout = QVBoxLayout(prof_card)
        prof_layout.setContentsMargins(20, 16, 20, 16)
        prof_layout.setSpacing(12)

        prof_hdr = QHBoxLayout()
        prof_hdr.setSpacing(10)
        p_title = QLabel("Power Profile")
        p_title.setProperty("class", "CardTitle")
        prof_hdr.addWidget(p_title)

        self.active_profile_pill = M3StatusPill("—")
        prof_hdr.addWidget(self.active_profile_pill)
        prof_hdr.addStretch()

        self.btn_customize_profiles = QPushButton("Customize")
        self.btn_customize_profiles.setProperty("class", "TextButton")
        self.btn_customize_profiles.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_customize_profiles.clicked.connect(
            lambda: self.navigate_requested.emit("settings:visible_profiles")
        )
        prof_hdr.addWidget(self.btn_customize_profiles)

        prof_layout.addLayout(prof_hdr)

        # Warning box for uninstalled/unloaded ACPI driver or non-Gigabyte hardware
        self.acpi_warning_box = M3Banner(
            title="ACPI Kernel Driver Not Loaded",
            body=(
                "Hardware fan control and live telemetry require the gigamate_acpi kernel driver. "
                "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
            ),
            tone="warning",
            icon="warning",
        )
        self.wb_title = self.acpi_warning_box.title_lbl
        self.wb_desc = self.acpi_warning_box.body_lbl
        prof_layout.addWidget(self.acpi_warning_box)

        self.prof_group = QButtonGroup(self)
        self.prof_group.setExclusive(True)
        self.prof_bar = M3SegmentedBar()
        self._btn_ids: list[int] = []
        self._profile_base_labels: dict[int, str] = {}
        prof_layout.addWidget(self.prof_bar)

        self.dgpu_profile_lock_notice = M3Banner(
            title="Performance & Gaming are locked while the discrete GPU is powered off (ECO / gated).",
            tone="warning",
            icon="lock",
            compact=True,
        )
        self.dgpu_profile_lock_notice.setVisible(False)
        prof_layout.addWidget(self.dgpu_profile_lock_notice)

        # ── Power Limits Comparison Matrix (always visible when supported) ──
        self.power_limits_container = QFrame()
        pl_layout = QVBoxLayout(self.power_limits_container)
        pl_layout.setContentsMargins(0, 6, 0, 0)
        pl_layout.setSpacing(8)

        self._battery_notice_row = QWidget()
        self._battery_notice_row.setStyleSheet("background: transparent;")
        bn_layout = QHBoxLayout(self._battery_notice_row)
        bn_layout.setContentsMargins(2, 4, 2, 4)
        bn_layout.setSpacing(6)

        bn_icon = QLabel()
        bn_icon.setFixedSize(14, 14)
        bn_icon.setStyleSheet("background: transparent;")
        bn_icon.setPixmap(icons.render_icon_pixmap("battery", color=THEME.warning, size=14))
        bn_layout.addWidget(bn_icon)

        self.power_battery_notice = QLabel(
            "Running on battery — firmware applies reduced battery power limits."
        )
        self.power_battery_notice.setWordWrap(True)
        self.power_battery_notice.setStyleSheet(
            f"color: {THEME.warning}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        bn_layout.addWidget(self.power_battery_notice, stretch=1)
        self._battery_notice_row.setVisible(False)
        self.power_battery_notice.setVisible(False)
        pl_layout.addWidget(self._battery_notice_row)

        self.compare_limits_frame = QFrame()
        self.compare_limits_frame.setStyleSheet(
            f"background-color: {THEME.surface_container_high}; border-radius: 14px; border: none;"
        )
        self.compare_grid = QGridLayout(self.compare_limits_frame)
        self.compare_grid.setContentsMargins(18, 14, 18, 14)
        self.compare_grid.setHorizontalSpacing(18)
        self.compare_grid.setVerticalSpacing(6)
        pl_layout.addWidget(self.compare_limits_frame)

        self._last_limits_key = None
        self.power_limits_container.setVisible(False)
        prof_layout.addWidget(self.power_limits_container)

        layout.addWidget(prof_card)

        # ── Hero Telemetry Band (3-column Responsive Grid) ──
        self.telemetry_grid = M3ResponsiveGrid(wide_cols=3, narrow_cols=1, breakpoint=620)

        # 1. CPU Thermal Tile
        self.cpu_tile = M3Card()
        cpu_l = QVBoxLayout(self.cpu_tile)
        cpu_l.setContentsMargins(16, 14, 16, 14)
        cpu_l.setSpacing(10)

        cpu_hdr = QHBoxLayout()
        cpu_hdr_lbl = QLabel("CPU THERMAL")
        cpu_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        cpu_hdr.addWidget(cpu_hdr_lbl)
        cpu_hdr.addStretch()
        self.cpu_status_pill = M3StatusPill("—")
        cpu_hdr.addWidget(self.cpu_status_pill)
        cpu_l.addLayout(cpu_hdr)

        cpu_body = QHBoxLayout()
        cpu_body.setSpacing(12)
        self.cpu_gauge = ArcGaugeWidget("CPU", "°C", min_val=30.0, max_val=95.0)
        cpu_body.addWidget(self.cpu_gauge)

        cpu_right = QVBoxLayout()
        cpu_right.setSpacing(4)
        self.cpu_temp_val = QLabel("--")
        self.cpu_temp_val.setObjectName("cpu_temp_val")
        self.cpu_temp_val.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 700; "
            f"font-family: {THEME.mono_family}; background: transparent;"
        )
        cpu_right.addWidget(self.cpu_temp_val)

        self.cpu_temp_sub = QLabel("Monitoring")
        self.cpu_temp_sub.setObjectName("cpu_temp_sub")
        self.cpu_temp_sub.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
        )
        cpu_right.addWidget(self.cpu_temp_sub)

        self.cpu_sparkline = SparklineWidget(min_val=35.0, max_val=90.0)
        cpu_right.addWidget(self.cpu_sparkline)
        cpu_body.addLayout(cpu_right, stretch=1)
        cpu_l.addLayout(cpu_body)

        # 2. Cooling Fans Tile
        self.fans_card = M3Card()
        fans_l = QVBoxLayout(self.fans_card)
        fans_l.setContentsMargins(16, 14, 16, 14)
        fans_l.setSpacing(10)

        fans_hdr = QHBoxLayout()
        fans_hdr_lbl = QLabel("COOLING FANS")
        fans_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        fans_hdr.addWidget(fans_hdr_lbl)
        fans_hdr.addStretch()
        self.fan_spin_icon = SpinningFanIcon(size=20)
        fans_hdr.addWidget(self.fan_spin_icon)
        fans_l.addLayout(fans_hdr)

        fans_rows = QVBoxLayout()
        fans_rows.setSpacing(8)

        self.fan1_tile, self.fan1_rpm_val, self.fan1_title_lbl = self._make_fan_row("CPU Fan", "fan1_rpm_val", "stat_fan1_rpm_title")
        self.fan2_tile, self.fan2_rpm_val, self.fan2_title_lbl = self._make_fan_row("GPU Fan", "fan2_rpm_val", "stat_fan2_rpm_title")
        self.duty_tile, self.fan_duty_val, _ = self._make_fan_row("Total Duty", "fan_duty_val")

        fans_rows.addWidget(self.fan1_tile)
        fans_rows.addWidget(self.fan2_tile)
        fans_rows.addWidget(self.duty_tile)
        fans_l.addLayout(fans_rows)
        fans_l.addStretch()

        # 3. Power Envelope Tile
        self.power_card = M3Card()
        pwr_l = QVBoxLayout(self.power_card)
        pwr_l.setContentsMargins(16, 14, 16, 14)
        pwr_l.setSpacing(10)

        pwr_hdr = QHBoxLayout()
        pwr_hdr_lbl = QLabel("POWER ENVELOPE")
        pwr_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        pwr_hdr.addWidget(pwr_hdr_lbl)
        pwr_hdr.addStretch()
        self.power_mode_lbl = QLabel("AC")
        self.power_mode_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 10px; font-weight: 700; "
            f"font-family: {THEME.mono_family}; background: transparent;"
        )
        pwr_hdr.addWidget(self.power_mode_lbl)
        pwr_l.addLayout(pwr_hdr)

        self.power_envelope_bar = PowerEnvelopeBarWidget()
        pwr_l.addWidget(self.power_envelope_bar)

        self.power_envelope_sub = QLabel("Active profile CPU + GPU budget")
        self.power_envelope_sub.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
        )
        self.power_envelope_sub.setWordWrap(True)
        pwr_l.addWidget(self.power_envelope_sub)
        pwr_l.addStretch()

        self.telemetry_grid.addWidget(self.cpu_tile)
        self.telemetry_grid.addWidget(self.fans_card)
        self.telemetry_grid.addWidget(self.power_card)
        layout.addWidget(self.telemetry_grid)

        # ── Quick Hardware Strip (3-column Responsive Grid) ──
        self.quick_grid = M3ResponsiveGrid(wide_cols=3, narrow_cols=1, breakpoint=620)

        # 1. Discrete GPU Quick Card
        self.gpu_tile = M3Card()
        gpu_l = QVBoxLayout(self.gpu_tile)
        gpu_l.setContentsMargins(16, 14, 16, 14)
        gpu_l.setSpacing(10)

        gpu_hdr = QHBoxLayout()
        gpu_hdr_lbl = QLabel("DISCRETE GPU")
        gpu_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        gpu_hdr.addWidget(gpu_hdr_lbl)
        gpu_hdr.addStretch()
        self.gpu_auto_link_pill = LinkPillButton("Auto: OFF ↗", "automation:dgpu_gate")
        self.gpu_auto_link_pill.navigate_requested.connect(self.navigate_requested.emit)
        gpu_hdr.addWidget(self.gpu_auto_link_pill)
        gpu_l.addLayout(gpu_hdr)

        self.gpu_status_val = QLabel("Checking…")
        self.gpu_status_val.setObjectName("gpu_status_val")
        self.gpu_status_val.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        gpu_l.addWidget(self.gpu_status_val)

        gpu_btn_row = QHBoxLayout()
        gpu_btn_row.setSpacing(8)
        self.btn_gpu_on = QPushButton("On (Optimus)")
        self.btn_gpu_on.setCheckable(True)
        self.btn_gpu_on.setProperty("class", "SegmentButton")
        self.btn_gpu_on.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_gpu_on.clicked.connect(lambda: self._on_dgpu_toggle(False))

        self.btn_gpu_off = QPushButton("Off (0 W)")
        self.btn_gpu_off.setCheckable(True)
        self.btn_gpu_off.setProperty("class", "SegmentButton")
        self.btn_gpu_off.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_gpu_off.clicked.connect(lambda: self._on_dgpu_toggle(True))

        gpu_btn_row.addWidget(self.btn_gpu_on, stretch=1)
        gpu_btn_row.addWidget(self.btn_gpu_off, stretch=1)
        gpu_l.addLayout(gpu_btn_row)
        gpu_l.addStretch()

        # 2. Battery Quick Card
        self.battery_quick_card = M3Card()
        bat_l = QVBoxLayout(self.battery_quick_card)
        bat_l.setContentsMargins(16, 14, 16, 14)
        bat_l.setSpacing(10)

        bat_hdr = QHBoxLayout()
        bat_hdr_lbl = QLabel("BATTERY")
        bat_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        bat_hdr.addWidget(bat_hdr_lbl)
        bat_hdr.addStretch()
        self.btn_bat_details = QPushButton("Details →")
        self.btn_bat_details.setProperty("class", "TextButton")
        self.btn_bat_details.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bat_details.clicked.connect(lambda: self.navigate_requested.emit("battery"))
        bat_hdr.addWidget(self.btn_bat_details)
        bat_l.addLayout(bat_hdr)

        bat_top = QHBoxLayout()
        self.bat_pct_lbl = QLabel("--%")
        self.bat_pct_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 20px; font-weight: 700; "
            f"font-family: {THEME.mono_family}; background: transparent;"
        )
        bat_top.addWidget(self.bat_pct_lbl)
        bat_top.addStretch()
        self.bat_health_lbl = QLabel("Health --")
        self.bat_health_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
        )
        bat_top.addWidget(self.bat_health_lbl)
        bat_l.addLayout(bat_top)

        self.bat_bar = QProgressBar()
        self.bat_bar.setRange(0, 100)
        self.bat_bar.setTextVisible(False)
        self.bat_bar.setFixedHeight(6)
        bat_l.addWidget(self.bat_bar)

        bat_foot = QHBoxLayout()
        self.bat_cap_lbl = QLabel("Cap: --")
        self.bat_cap_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
        )
        bat_foot.addWidget(self.bat_cap_lbl)
        bat_foot.addStretch()
        self.btn_bat_topup = QPushButton("100% Once")
        self.btn_bat_topup.setProperty("class", "TonalButton")
        self.btn_bat_topup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_bat_topup.clicked.connect(self._on_bat_topup_clicked)
        bat_foot.addWidget(self.btn_bat_topup)
        bat_l.addLayout(bat_foot)

        # 3. Keyboard Quick Card
        self.keyboard_quick_card = M3Card()
        kb_l = QVBoxLayout(self.keyboard_quick_card)
        kb_l.setContentsMargins(16, 14, 16, 14)
        kb_l.setSpacing(10)

        kb_hdr = QHBoxLayout()
        kb_hdr_lbl = QLabel("KEYBOARD RGB")
        kb_hdr_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        kb_hdr.addWidget(kb_hdr_lbl)
        kb_hdr.addStretch()
        self.btn_kb_studio = QPushButton("Studio →")
        self.btn_kb_studio.setProperty("class", "TextButton")
        self.btn_kb_studio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kb_studio.clicked.connect(lambda: self.navigate_requested.emit("keyboard"))
        kb_hdr.addWidget(self.btn_kb_studio)
        kb_l.addLayout(kb_hdr)

        kb_mid = QHBoxLayout()
        kb_mid.setSpacing(10)
        self.kb_icon_lbl = QLabel()
        self.kb_icon_lbl.setFixedSize(24, 24)
        self.kb_icon_lbl.setStyleSheet("background: transparent;")
        kb_mid.addWidget(self.kb_icon_lbl)

        self.kb_readout_lbl = QLabel("#FFFFFF · 100%")
        self.kb_readout_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; "
            f"font-family: {THEME.mono_family}; background: transparent;"
        )
        kb_mid.addWidget(self.kb_readout_lbl)
        kb_mid.addStretch()

        self.btn_kb_toggle = QPushButton("ON")
        self.btn_kb_toggle.setProperty("class", "TonalButton")
        self.btn_kb_toggle.setCheckable(True)
        self.btn_kb_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kb_toggle.clicked.connect(self._on_kb_toggle_clicked)
        kb_mid.addWidget(self.btn_kb_toggle)
        kb_l.addLayout(kb_mid)

        self.kb_brightness_slider = M3Slider(Qt.Orientation.Horizontal)
        self.kb_brightness_slider.setRange(5, 100)
        self.kb_brightness_slider.valueChanged.connect(self._on_kb_brightness_changed)
        kb_l.addWidget(self.kb_brightness_slider)
        kb_l.addStretch()

        self.quick_grid.addWidget(self.gpu_tile)
        self.quick_grid.addWidget(self.battery_quick_card)
        self.quick_grid.addWidget(self.keyboard_quick_card)
        layout.addWidget(self.quick_grid)

        layout.addStretch()
        self._refresh_telemetry()

    @staticmethod
    def _make_fan_row(title: str, val_object_name: str, title_object_name: str = "") -> tuple[QFrame, QLabel, QLabel]:
        row = QFrame()
        row.setStyleSheet(
            f"background-color: {THEME.surface_container_high}; border-radius: 8px; border: none;"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(8)
        t_lbl = QLabel(title)
        if title_object_name:
            t_lbl.setObjectName(title_object_name)
        t_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; font-weight: 500; background: transparent;"
        )
        v_lbl = QLabel("--")
        v_lbl.setObjectName(val_object_name)
        v_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 12px; font-weight: 700; "
            f"font-family: {THEME.mono_family}; background: transparent;"
        )
        rl.addWidget(t_lbl)
        rl.addStretch()
        rl.addWidget(v_lbl)
        return row, v_lbl, t_lbl

    def _ensure_acpi_controller(self) -> None:
        """Re-probe for an ACPI backend at most once every 10 s when absent."""
        if self.acpi_ctrl.available:
            return
        now = time.monotonic()
        if now - getattr(self, "_acpi_retry_at", 0.0) < 10.0:
            return
        self._acpi_retry_at = now
        self.acpi_ctrl = AcpiController()

    def _select_profile(self, profile_id: int) -> None:
        """Switch ACPI profile and sync system/GPU power per user preference."""
        self._ensure_acpi_controller()
        if profile_id in (2, 3) and not dgpu_gate.high_power_profiles_allowed():
            self._refresh_telemetry()
            return

        hardware.apply_profile(profile_id, cfg=self._config(), sync_system=True, acpi=self.acpi_ctrl)

        btn = self._profile_buttons.get(profile_id)
        if btn is not None:
            btn.setChecked(True)
        self._apply_profile_rgb_dots(self._config())

        self.profile_accent_changed.emit(profile_id)
        self._last_emitted_profile = profile_id
        self._refresh_telemetry()

    def _apply_profile_rgb_dots(self, cfg: dict) -> None:
        """Apply profile RGB dot icons (called after setChecked so _sync_icon doesn't overwrite)."""
        show_rgb_dot = bool(cfg.get("profile_rgb_enabled", False))
        for pid, btn in self._profile_buttons.items():
            if show_rgb_dot:
                pm = QPixmap(10, 10)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QColor(profile_rgb_for(cfg, pid)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(1, 1, 8, 8)
                p.end()
                btn.setIcon(QIcon(pm))
                btn.setIconSize(btn.iconSize())
            else:
                btn.setIcon(QIcon())

    def _rebuild_profile_buttons(self, profiles: dict, usable: bool) -> None:
        """(Re)build the profile buttons from discovered & enabled ACPI profiles."""
        cfg = self._config()
        all_profiles = dict(profiles or {})
        enabled_profiles = enabled_acpi_profiles(all_profiles, cfg)
        ids = sorted(int(k) for k in enabled_profiles.keys())
        self.prof_bar.setVisible(bool(ids))

        if ids != self._btn_ids:
            for btn in self._profile_buttons.values():
                self.prof_group.removeButton(btn)
            self.prof_bar.clear_segments()
            self._profile_buttons = {}
            self._profile_base_labels = {}
            self._btn_ids = []
            for pid in ids:
                label = (enabled_profiles.get(pid) or enabled_profiles.get(str(pid)) or {}).get(
                    "name", f"Profile {pid}"
                )
                self._profile_base_labels[pid] = label
                btn = self.prof_bar.add_segment(label, checkable=True, min_height=42)
                btn.clicked.connect(lambda _, p=pid: self._select_profile(p))
                self.prof_group.addButton(btn, pid)
                self._profile_buttons[pid] = btn
                self._btn_ids.append(pid)

        high_ok = dgpu_gate.high_power_profiles_allowed()
        show_rgb_dot = bool(cfg.get("profile_rgb_enabled", False))
        for pid, btn in self._profile_buttons.items():
            base_label = self._profile_base_labels.get(
                pid,
                (enabled_profiles.get(pid) or enabled_profiles.get(str(pid)) or {}).get(
                    "name", f"Profile {pid}"
                ),
            )
            locked_by_gate = usable and not high_ok and pid in (2, 3)
            btn.setText(base_label)
            if show_rgb_dot:
                pm = QPixmap(10, 10)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QColor(profile_rgb_for(cfg, pid)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(1, 1, 8, 8)
                p.end()
                btn.setIcon(QIcon(pm))
                btn.setIconSize(btn.iconSize())
            else:
                btn.setIcon(QIcon())
            if locked_by_gate:
                btn.setLocked(
                    True,
                    tooltip="Unavailable while dGPU is powered off (ECO / gated)",
                )
            elif not usable:
                btn.setLocked(False)
                btn.setEnabled(False)
                btn.setCursor(Qt.CursorShape.ArrowCursor)
                btn.setToolTip("")
            else:
                btn.setLocked(False)
                btn.setEnabled(True)
                btn.setToolTip("")
        if hasattr(self, "dgpu_profile_lock_notice"):
            self.dgpu_profile_lock_notice.setVisible(bool(usable and not high_ok))

    def _refresh_telemetry(self) -> None:
        """Update sensor numbers, profile highlight, and quick hardware cards."""
        current_prof_id: Optional[int] = None
        usable = False
        self._ensure_acpi_controller()

        if self.acpi_ctrl.available:
            self.telemetry_grid.setVisible(True)
            self.cpu_tile.setVisible(True)
            self.fans_card.setVisible(True)

            caps = self.acpi_ctrl.capabilities
            profiles = self._discovered_profiles()
            usable = bool(profiles)
            self._rebuild_profile_buttons(profiles, usable)
            if profiles:
                self.acpi_warning_box.setVisible(False)
            else:
                self.acpi_warning_box.setVisible(True)
                self.acpi_warning_box.set_tone("info", "info")
                self.wb_title.setText("No Power Profiles Exposed")
                self.wb_desc.setText(
                    "BIOS does not expose WMBD(0xED) power profiles — sensors and fans remain active."
                )

            if not caps.has_fan_rpm:
                self.fan1_tile.setVisible(False)
                self.fan2_tile.setVisible(False)
            elif caps.fan_count == 1:
                self.fan1_tile.setVisible(True)
                self.fan2_tile.setVisible(False)
                self.fan1_title_lbl.setText("System Fan Speed")
            else:
                self.fan1_tile.setVisible(True)
                self.fan2_tile.setVisible(True)
                self.fan1_title_lbl.setText("CPU Fan")
                self.fan2_title_lbl.setText("GPU Fan")

            current_prof_id = self.acpi_ctrl.get_profile()
            state: FanState = self.acpi_ctrl.read_state() or FanState()

            # CPU Temp
            if state.temp_cpu is not None:
                self.cpu_temp_val.setText(f"Package {state.temp_cpu}°C")
                self.cpu_gauge.set_value(float(state.temp_cpu))
                self._cpu_history.append(float(state.temp_cpu))
                if len(self._cpu_history) > 30:
                    self._cpu_history = self._cpu_history[-30:]
                self.cpu_sparkline.set_data(self._cpu_history)
                if state.temp_cpu >= 85:
                    self.cpu_status_pill.setText("Warm")
                    self.cpu_status_pill.set_tone("error")
                elif state.temp_cpu >= 65:
                    self.cpu_status_pill.setText("Active")
                    self.cpu_status_pill.set_tone("warning")
                else:
                    self.cpu_status_pill.setText("Optimal")
                    self.cpu_status_pill.set_tone("success")
                self.cpu_temp_sub.setText(
                    "Idle / light load"
                    if state.temp_cpu < 65
                    else ("High thermal load" if state.temp_cpu >= 85 else "Active thermal load")
                )
            else:
                self.cpu_temp_val.setText("--")
                self.cpu_gauge.set_value(None)
                self.cpu_status_pill.setText("—")
                self.cpu_status_pill.set_tone("neutral")
                self.cpu_temp_sub.setText("Monitoring")

            # Fans
            if state.fan1_rpm is None:
                self.fan1_rpm_val.setText("--")
            else:
                self.fan1_rpm_val.setText(f"{state.fan1_rpm} RPM")

            if state.fan2_rpm is None:
                self.fan2_rpm_val.setText("--")
            else:
                self.fan2_rpm_val.setText(f"{state.fan2_rpm} RPM")

            if state.duty_total is not None:
                self.fan_duty_val.setText(f"{state.duty_total}%")
            else:
                self.fan_duty_val.setText("--")
            self.fan_spin_icon.set_rpm(state.fan1_rpm, color=self._accent_hex)
        else:
            self.cpu_tile.setVisible(False)
            self.fans_card.setVisible(False)

            sys_caps = detect_system_capabilities()
            self.acpi_warning_box.setVisible(True)
            if sys_caps.is_gigabyte_laptop:
                self.acpi_warning_box.set_tone("warning", "warning")
                self.wb_title.setText("ACPI Kernel Driver (gigamate_acpi) Not Loaded")
                self.wb_desc.setText(
                    "Hardware fan profiles and live telemetry require the gigamate_acpi kernel module. "
                    "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
                )
            else:
                self.acpi_warning_box.set_tone("info", "info")
                self.wb_title.setText("Non-Gigabyte Hardware Detected")
                self.wb_desc.setText(
                    "Hardware thermal profiles and Embedded Controller sensor telemetry are only available "
                    "on supported Gigabyte Aero / AORUS laptops."
                )

            for btn in self._profile_buttons.values():
                btn.setEnabled(False)

        # Update profile buttons & active pill
        if current_prof_id is None:
            current_prof_id = self._config().get("acpi_profile")

        for pid, btn in self._profile_buttons.items():
            btn.setChecked(current_prof_id == pid)
        self._apply_profile_rgb_dots(self._config())

        if current_prof_id is not None:
            prof_name = self._profile_base_labels.get(current_prof_id, f"Profile {current_prof_id}")
            self.active_profile_pill.setText(prof_name)
            if current_prof_id != self._last_emitted_profile:
                self._last_emitted_profile = current_prof_id
                self.profile_accent_changed.emit(current_prof_id)
        else:
            self.active_profile_pill.setText("—")

        # GPU State & Quick Card
        gpu = get_gpu_state()
        self._refresh_gpu_quick_card(gpu)

        # Power Envelope & Limits
        self._update_power_limits_ui(
            active_prof_id=current_prof_id,
            usable=usable and self.acpi_ctrl.available,
            gpu=gpu,
        )

        # Battery & Keyboard Quick Cards
        self._refresh_battery_quick_card()
        self._refresh_keyboard_quick_card()

    def _refresh_gpu_quick_card(self, gpu: GpuState) -> None:
        cfg = self._config()
        auto_on = str(cfg.get("dgpu_power_mode", "optimus")).lower() == dgpu_gate.MODE_AUTO
        self.gpu_auto_link_pill.setText(f"Auto: {'ON' if auto_on else 'OFF'} ↗")

        if not gpu.present:
            self.gpu_status_val.setText("Not Present (iGPU)")
            self.btn_gpu_on.setEnabled(False)
            self.btn_gpu_off.setEnabled(False)
            return

        on_ac = not _is_on_battery()
        gated = bool(gpu.gated_off or gpu.status == "gated_off")

        if gated:
            self.gpu_status_val.setText("Off (0 W · Gated)")
            self.gpu_status_val.setStyleSheet(
                f"color: {THEME.gated}; font-size: 16px; font-weight: 700; background: transparent;"
            )
        elif gpu.status == "suspended" or gpu.power_state in ("D3hot", "D3cold"):
            self.gpu_status_val.setText(f"Asleep ({gpu.power_state or 'D3cold'})")
            self.gpu_status_val.setStyleSheet(
                f"color: {THEME.success}; font-size: 16px; font-weight: 700; background: transparent;"
            )
        else:
            self.gpu_status_val.setText(f"Active ({gpu.power_state or 'D0'})")
            self.gpu_status_val.setStyleSheet(
                f"color: {THEME.warning}; font-size: 16px; font-weight: 700; background: transparent;"
            )

        self.btn_gpu_on.blockSignals(True)
        self.btn_gpu_off.blockSignals(True)
        self.btn_gpu_on.setChecked(not gated)
        self.btn_gpu_off.setChecked(gated)
        self.btn_gpu_on.blockSignals(False)
        self.btn_gpu_off.blockSignals(False)

        self.btn_gpu_on.setEnabled(True)
        if on_ac and not gated:
            self.btn_gpu_off.setEnabled(False)
            self.btn_gpu_off.setToolTip("Battery only")
        else:
            self.btn_gpu_off.setEnabled(True)
            self.btn_gpu_off.setToolTip("")

    def _on_dgpu_toggle(self, power_off: bool) -> None:
        cfg = self._config()
        if power_off:
            if not dgpu_gate.eco_allowed_on_power():
                self._refresh_telemetry()
                return
            if not cfg.get("dgpu_gate_disclaimer_accepted"):
                reply = QMessageBox.warning(
                    self,
                    "Power Off Discrete GPU",
                    "Turning off the discrete GPU cuts its power rail (~0 W) while on battery. "
                    "External displays wired to the dGPU and CUDA/Vulkan dGPU apps will be unavailable until re-enabled.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self._refresh_telemetry()
                    return
                update_config(lambda c: {**c, "dgpu_gate_disclaimer_accepted": True})

            from ..dgpu_gate_dialog import DgpuGateConfirmDialog
            dlg = DgpuGateConfirmDialog(
                trigger="eco",
                target_mode=dgpu_gate.MODE_ECO,
                prev_mode=str(cfg.get("dgpu_power_mode", "optimus")),
                parent=self,
            )
            dlg.exec()
        else:
            dgpu_gate.set_mode(dgpu_gate.MODE_OPTIMUS, apply_now=True)
        self._refresh_telemetry()

    def _refresh_battery_quick_card(self) -> None:
        caps = detect_system_capabilities()
        if not caps.battery_present:
            self.battery_quick_card.setVisible(False)
            return
        self.battery_quick_card.setVisible(True)
        try:
            from ...battery import get_battery_manager
            cfg = self._config()
            mgr = get_battery_manager()
            info = mgr.get_battery_info()
            pct = info.capacity if info.capacity is not None else 0
            self.bat_pct_lbl.setText(f"{pct}%")
            self.bat_bar.setValue(max(0, min(100, int(pct))))
            health = f"{info.health_percent:.0f}%" if info.health_percent is not None else "--"
            self.bat_health_lbl.setText(f"Health {health}")
            cap = mgr.get_charge_limit() or int(cfg.get("charge_limit", 80))
            self.bat_cap_lbl.setText(f"Cap: {cap}%")
            topup = bool(cfg.get("charge_topup_active", False))
            self.btn_bat_topup.setText("Cancel" if topup else "100% Once")
        except Exception:
            self.bat_pct_lbl.setText("--%")

    def _on_bat_topup_clicked(self) -> None:
        from ...battery import cancel_topup, get_battery_manager, start_topup
        cfg = self._config()
        mgr = get_battery_manager()
        if cfg.get("charge_topup_active"):
            cancel_topup(mgr, restore_limit=True)
        else:
            start_topup(mgr)
        self._refresh_battery_quick_card()

    def _refresh_keyboard_quick_card(self) -> None:
        caps = detect_system_capabilities()
        if not caps.keyboard_detected:
            self.keyboard_quick_card.setVisible(False)
            return
        self.keyboard_quick_card.setVisible(True)
        cfg = self._config()
        hex_col = effective_rgb_hex(cfg)
        bright = max(5, int(cfg.get("brightness_pct") or effective_brightness_pct(cfg) or 100))
        enabled = is_rgb_enabled(cfg)
        self.kb_icon_lbl.setPixmap(icons.render_icon_pixmap("keyboard", color=hex_col, size=22))
        self.kb_readout_lbl.setText(f"{hex_col.upper()} · {bright}%")
        self.btn_kb_toggle.blockSignals(True)
        self.btn_kb_toggle.setChecked(enabled)
        self.btn_kb_toggle.setText("ON" if enabled else "OFF")
        self.btn_kb_toggle.blockSignals(False)
        self.kb_brightness_slider.blockSignals(True)
        self.kb_brightness_slider.setValue(max(5, min(100, bright)))
        self.kb_brightness_slider.blockSignals(False)

    def _on_kb_toggle_clicked(self) -> None:
        enabled = self.btn_kb_toggle.isChecked()
        cfg = self._config()
        bright = max(5, int(cfg.get("brightness_pct") or effective_brightness_pct(cfg) or 100))
        hex_col = effective_rgb_hex(cfg)
        update_config(
            lambda c: {
                **c,
                "rgb_enabled": enabled,
                "brightness": pct_to_legacy_brightness(bright) if enabled else 0,
            }
        )
        try:
            from ... import protocol
            dev = protocol.get_keyboard()
            if dev is not None:
                if enabled:
                    protocol.set_rgb(dev, hex_col, bright)
                else:
                    protocol.set_off(dev)
        except Exception:
            pass
        self._refresh_keyboard_quick_card()

    def _on_kb_brightness_changed(self, value: int) -> None:
        val = max(5, min(100, int(value)))
        cfg = self._config()
        hex_col = effective_rgb_hex(cfg)
        update_config(
            lambda c: {
                **c,
                "rgb_enabled": True,
                "brightness_pct": val,
                "last_brightness_pct": val,
                "brightness": pct_to_legacy_brightness(val),
            }
        )
        try:
            from ... import protocol
            dev = protocol.get_keyboard()
            if dev is not None:
                protocol.set_rgb(dev, hex_col, val)
        except Exception:
            pass
        self.kb_readout_lbl.setText(f"{hex_col.upper()} · {val}%")

    def _update_power_limits_ui(
        self,
        active_prof_id: Optional[int],
        usable: bool = True,
        gpu: Optional[GpuState] = None,
    ) -> None:
        """Update PowerEnvelopeBarWidget and the always-visible power comparison matrix."""
        if not usable:
            self.power_card.setVisible(False)
            self._battery_notice_row.setVisible(False)
            self.power_battery_notice.setVisible(False)
            self.power_limits_container.setVisible(False)
            return

        try:
            limits = discover_power_limits()
        except Exception:
            limits = SystemPowerLimits()

        if not limits.profiles:
            self.power_card.setVisible(False)
            self._battery_notice_row.setVisible(False)
            self.power_battery_notice.setVisible(False)
            self.power_limits_container.setVisible(False)
            return

        self.power_card.setVisible(True)
        self.power_limits_container.setVisible(True)
        self.power_mode_lbl.setText("BAT" if limits.on_battery else "AC")

        # Update PowerEnvelopeBarWidget for active profile
        prof_list = list(limits.profiles.values())
        active_row = next((r for r in prof_list if r.profile_id == active_prof_id), None)
        gated = bool(gpu is not None and (gpu.gated_off or gpu.status == "gated_off"))
        max_w = max(
            (
                int(
                    r.combined_w
                    or ((r.cpu_shared_w or r.cpu_sustained_w or 0) + (r.gpu_fixed_w or 0))
                )
                for r in prof_list
            ),
            default=120,
        )
        if active_row is not None:
            cpu_solo = int(active_row.cpu_sustained_w or 0)
            cpu_shared = int(active_row.cpu_shared_w or active_row.cpu_sustained_w or 0)
            gpu_w = 0 if gated else int(active_row.gpu_fixed_w or 0)
            bar_cpu_w = cpu_solo if gated else cpu_shared
            combined_w = cpu_solo if gated else int(active_row.combined_w or (cpu_shared + gpu_w))
            accent_hex = PROFILE_ACCENTS.get(active_prof_id, THEME.primary)
            self.power_envelope_bar.set_budget(
                bar_cpu_w,
                gpu_w,
                combined_w,
                max_w=max(1, max_w),
                accent_hex=accent_hex,
                gated=gated,
            )
            if gated:
                self.power_envelope_sub.setText(f"CPU-only {cpu_solo} W · dGPU Off (0 W)")
            else:
                self.power_envelope_sub.setText(
                    f"Shared {cpu_shared} W CPU + {gpu_w} W GPU = {combined_w} W cap (CPU-only {cpu_solo} W)"
                )

        show_bat_notice = bool(limits.on_battery)
        self._battery_notice_row.setVisible(show_bat_notice)
        self.power_battery_notice.setVisible(show_bat_notice)

        gpu_gated = bool(gpu is not None and (gpu.gated_off or gpu.status == "gated_off"))
        enabled_map = enabled_acpi_profiles(self._discovered_profiles(), self._config())
        enabled_ids = tuple(sorted(enabled_map.keys()))
        matrix_key = (
            active_prof_id,
            limits.on_battery,
            gpu_gated,
            enabled_ids,
            tuple(
                (r.profile_id, r.cpu_sustained_w, r.cpu_burst_w, r.gpu_fixed_w, r.gpu_boost_w)
                for r in prof_list
            ),
        )
        if matrix_key == self._last_limits_key:
            return
        self._last_limits_key = matrix_key

        filtered_profiles = [r for r in prof_list if r.profile_id in enabled_map]
        if filtered_profiles:
            self._rebuild_compare_matrix(filtered_profiles, active_prof_id, gpu_gated=gpu_gated)

    def _rebuild_compare_matrix(
        self,
        profiles: list,
        active_prof_id: Optional[int],
        *,
        gpu_gated: bool = False,
    ) -> None:
        """Populate the power comparison matrix."""
        while self.compare_grid.count():
            item = self.compare_grid.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()

        has_combined = any(r.combined_w is not None for r in profiles)
        has_cpu_sustained = any(r.cpu_sustained_w is not None for r in profiles)
        has_cpu_boost = any(r.cpu_burst_w is not None for r in profiles)
        has_gpu_tgp = any(r.gpu_fixed_w is not None for r in profiles)
        has_gpu_boost = any(r.gpu_boost_w is not None for r in profiles)

        headers = ["Profile"]
        if has_combined:
            headers.append("Combined")
        if has_cpu_sustained:
            headers.append("CPU Sustained")
        if has_cpu_boost:
            headers.append("CPU Boost")
        if has_gpu_tgp:
            headers.append("GPU TGP")
        if has_gpu_boost:
            headers.append("GPU Dynamic Boost")

        for col, h_text in enumerate(headers):
            lbl = QLabel(h_text)
            lbl.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: 11px; font-weight: 700; "
                f"background: transparent; padding-bottom: 2px;"
            )
            self.compare_grid.addWidget(lbl, 0, col)

        for row_idx, r in enumerate(profiles, start=1):
            is_active = r.profile_id == active_prof_id
            color = self._accent_hex if is_active else THEME.on_surface_variant
            weight = "700" if is_active else "500"
            col_idx = 0

            name_lbl = QLabel(self._profile_base_labels.get(r.profile_id, f"Profile {r.profile_id}"))
            name_lbl.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: {weight}; background: transparent;"
            )
            self.compare_grid.addWidget(name_lbl, row_idx, col_idx)
            col_idx += 1

            def _add_val(val: Optional[int], gated_off: bool = False, prefix: str = "") -> None:
                nonlocal col_idx
                txt = "Off (0 W)" if gated_off else (f"{prefix}{val} W" if val is not None else "—")
                v_lbl = QLabel(txt)
                v_lbl.setStyleSheet(
                    f"color: {color}; font-size: 12px; font-weight: {weight}; "
                    f"font-family: {THEME.mono_family}; background: transparent;"
                )
                self.compare_grid.addWidget(v_lbl, row_idx, col_idx)
                col_idx += 1

            if has_combined:
                _add_val(r.combined_w)
            if has_cpu_sustained:
                _add_val(r.cpu_sustained_w)
            if has_cpu_boost:
                _add_val(r.cpu_burst_w)
            if has_gpu_tgp:
                _add_val(r.gpu_fixed_w, gated_off=gpu_gated)
            if has_gpu_boost:
                _add_val(r.gpu_boost_w, gated_off=gpu_gated, prefix="Up to ")
