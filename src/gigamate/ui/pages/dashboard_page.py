"""GigaMate Center — Performance & Hardware Dashboard."""

import time
from typing import Dict, Optional
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import dgpu_gate, hardware
from ...acpi import AcpiController, FanProfile, FanState
from ...capabilities import detect_system_capabilities
from ...config import enabled_acpi_profiles, load as load_config, profile_rgb_for, update_config
from ...gpu import GpuState, get_gpu_state
from ...power_limits import SystemPowerLimits, discover_acpi_profiles, discover_power_limits
from ...system_power import sync_system_power
from .. import icons
from ..theme import THEME
from ..widgets import (
    M3Banner,
    M3Card,
    M3MetricTile,
    M3PageHeader,
    M3SegmentedBar,
    M3StatusPill,
)


class DashboardPage(QWidget):
    """Performance profile controls and live thermal/fan monitoring."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.acpi_ctrl = AcpiController()
        self._profile_buttons = {}
        self._profile_enable_checks: Dict[int, QCheckBox] = {}
        self._enable_check_ids: list[int] = []
        self._cached_profile = None
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
        """Public page-lifecycle hook: refresh telemetry/highlight."""
        self._refresh_telemetry()

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
            "Dashboard",
            "Power policy, thermal limits & live hardware telemetry",
        )
        self.gpu_header_pill = M3StatusPill("")
        self.gpu_header_pill.setVisible(False)
        header.set_trailing_widget(self.gpu_header_pill)
        layout.addWidget(header)

        # ── Power & Fan Profile Selector Card ──
        prof_card = M3Card()
        prof_layout = QVBoxLayout(prof_card)
        prof_layout.setContentsMargins(20, 18, 20, 18)
        prof_layout.setSpacing(12)

        p_title = QLabel("Power & Thermal Profiles")
        p_title.setProperty("class", "CardTitle")
        p_sub = QLabel(
            "Controls hardware power and thermal policy, from fan response curves to TDP/TGP limits."
        )
        p_sub.setProperty("class", "CardSubtitle")
        p_sub.setWordWrap(True)
        prof_layout.addWidget(p_title)
        prof_layout.addWidget(p_sub)

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
        self._btn_ids: list = []
        self._profile_base_labels: dict = {}

        prof_layout.addWidget(self.prof_bar)

        self.dgpu_profile_lock_notice = M3Banner(
            title="Performance & Gaming are locked while the discrete GPU is powered off (ECO / gated).",
            tone="warning",
            icon="lock",
            compact=True,
        )
        self.dgpu_profile_lock_notice.setVisible(False)
        prof_layout.addWidget(self.dgpu_profile_lock_notice)

        # ── Discovered Profile Power Limits Comparison Matrix ──
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
            f"color: {THEME.warning}; font-size: 12px; font-weight: 600; "
            f"background: transparent;"
        )
        bn_layout.addWidget(self.power_battery_notice, stretch=1)
        self._battery_notice_row.setVisible(False)
        self.power_battery_notice.setVisible(False)
        pl_layout.addWidget(self._battery_notice_row)

        self.compare_limits_frame = QFrame()
        self.compare_limits_frame.setStyleSheet(
            f"background-color: {THEME.surface_container_high}; "
            f"border-radius: 14px; border: none;"
        )
        self.compare_grid = QGridLayout(self.compare_limits_frame)
        self.compare_grid.setContentsMargins(18, 14, 18, 14)
        self.compare_grid.setHorizontalSpacing(18)
        self.compare_grid.setVerticalSpacing(6)
        pl_layout.addWidget(self.compare_limits_frame)

        self._last_limits_key = None
        self.power_limits_container.setVisible(False)
        prof_layout.addWidget(self.power_limits_container)

        # ── Active / Visible Profiles Strip ──
        self.visible_profiles_row = QWidget()
        self.visible_profiles_row.setStyleSheet("background: transparent;")
        vp_layout = QHBoxLayout(self.visible_profiles_row)
        vp_layout.setContentsMargins(2, 4, 2, 0)
        vp_layout.setSpacing(10)
        vp_lbl = QLabel("Visible profiles:")
        vp_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        vp_layout.addWidget(vp_lbl)
        self._visible_profiles_checks_layout = QHBoxLayout()
        self._visible_profiles_checks_layout.setContentsMargins(0, 0, 0, 0)
        self._visible_profiles_checks_layout.setSpacing(10)
        vp_layout.addLayout(self._visible_profiles_checks_layout)
        vp_layout.addStretch()
        self.visible_profiles_row.setVisible(False)
        prof_layout.addWidget(self.visible_profiles_row)

        layout.addWidget(prof_card)

        # ── Live Telemetry Cards ──
        self.telemetry_card = M3Card()
        t_layout = QVBoxLayout(self.telemetry_card)
        t_layout.setContentsMargins(20, 18, 20, 18)
        t_layout.setSpacing(14)
        t_title = QLabel("Real-Time Hardware Telemetry")
        t_title.setProperty("class", "CardTitle")
        t_layout.addWidget(t_title)

        grid = QGridLayout()
        grid.setSpacing(12)

        # Row 0: Thermals & GPU (CPU Temp & Discrete GPU)
        self.cpu_tile = M3MetricTile(
            "CPU Temperature", "stat_cpu_temp", "thermometer", "stat_cpu_sub", show_bar=True
        )
        self.gpu_tile = M3MetricTile(
            "Discrete GPU State", "stat_gpu", "gpu", "stat_gpu_sub", show_bar=False
        )
        grid.addWidget(self.cpu_tile, 0, 0)
        grid.addWidget(self.gpu_tile, 0, 1, 1, 2)

        # Row 1: Cooling Fans & Total Duty
        self.fan1_tile = M3MetricTile(
            "CPU Fan Speed", "stat_fan1_rpm", "fan", "stat_fan1_sub", show_bar=True
        )
        self.fan2_tile = M3MetricTile(
            "GPU Fan Speed", "stat_fan2_rpm", "fan", "stat_fan2_sub", show_bar=True
        )
        self.duty_tile = M3MetricTile(
            "Total Fan Duty", "stat_duty", "gauge", "stat_duty_sub", show_bar=True
        )

        grid.addWidget(self.fan1_tile, 1, 0)
        grid.addWidget(self.fan2_tile, 1, 1)
        grid.addWidget(self.duty_tile, 1, 2)

        t_layout.addLayout(grid)
        layout.addWidget(self.telemetry_card)

        layout.addStretch()
        self._refresh_telemetry()

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

        self._refresh_telemetry()

    def _sync_visible_profile_checks(self, all_profiles: dict, cfg: dict) -> None:
        """Build/update the compact Visible Profiles toggle strip."""
        all_ids = sorted(int(k) for k in all_profiles.keys())
        self.visible_profiles_row.setVisible(len(all_ids) >= 2)
        if len(all_ids) < 2:
            return

        if all_ids != self._enable_check_ids:
            while self._visible_profiles_checks_layout.count():
                item = self._visible_profiles_checks_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
            self._profile_enable_checks = {}
            self._enable_check_ids = list(all_ids)
            for pid in all_ids:
                label = (all_profiles.get(pid) or all_profiles.get(str(pid)) or {}).get(
                    "name", f"Profile {pid}"
                )
                chk = QCheckBox(label)
                chk.setCursor(Qt.CursorShape.PointingHandCursor)
                chk.setStyleSheet(
                    f"QCheckBox {{ color: {THEME.on_surface_variant}; font-size: 12px; background: transparent; spacing: 5px; }}"
                )
                chk.toggled.connect(lambda checked, p=pid: self._on_profile_enabled_toggled(p, checked))
                self._visible_profiles_checks_layout.addWidget(chk)
                self._profile_enable_checks[pid] = chk

        enabled_map = enabled_acpi_profiles(all_profiles, cfg)
        enabled_ids = set(enabled_map.keys())
        only_one_left = len(enabled_ids) <= 1
        for pid, chk in self._profile_enable_checks.items():
            is_enabled = pid in enabled_ids
            chk.blockSignals(True)
            chk.setChecked(is_enabled)
            chk.blockSignals(False)
            if only_one_left and is_enabled:
                chk.setEnabled(False)
                chk.setToolTip("At least one profile must remain enabled")
            else:
                chk.setEnabled(True)
                chk.setToolTip("")

    def _on_profile_enabled_toggled(self, pid: int, checked: bool) -> None:
        """Persist disabled_profiles and switch away if the active profile was disabled."""
        all_profiles = self._discovered_profiles()

        def _mutate(c: dict) -> dict:
            cur = {int(x) for x in (c.get("disabled_profiles") or [])}
            if checked:
                cur.discard(int(pid))
            else:
                # Never allow disabling the last remaining profile
                remaining = [int(k) for k in all_profiles.keys() if int(k) not in cur and int(k) != int(pid)]
                if remaining or not all_profiles:
                    cur.add(int(pid))
            return {**c, "disabled_profiles": sorted(cur)}

        update_config(_mutate)
        fresh_cfg = self._config()

        if not checked:
            self._ensure_acpi_controller()
            current_pid = self._cached_profile
            if current_pid is None and self.acpi_ctrl.available:
                current_pid = self.acpi_ctrl.get_profile()
            if current_pid == pid:
                enabled = enabled_acpi_profiles(all_profiles, fresh_cfg)
                if enabled:
                    fallback_pid = 1 if 1 in enabled else sorted(enabled.keys())[0]
                    hardware.apply_profile(
                        fallback_pid, cfg=fresh_cfg, sync_system=True, acpi=self.acpi_ctrl
                    )

        self._refresh_telemetry()

    def _rebuild_profile_buttons(self, profiles: dict, usable: bool) -> None:
        """(Re)build the profile buttons from discovered & enabled ACPI profiles."""
        cfg = self._config()
        all_profiles = dict(profiles or {})
        self._sync_visible_profile_checks(all_profiles, cfg)

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
                btn.setIconSize(QSize(10, 10))
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
        """Update sensor numbers and highlight active profile."""
        current_prof_id: Optional[int] = None
        usable = False
        self._ensure_acpi_controller()

        if self.acpi_ctrl.available:
            self.telemetry_card.setVisible(True)
            self.cpu_tile.setVisible(True)
            self.duty_tile.setVisible(True)

            # Fan count awareness
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
            fan1_title = self.findChild(QLabel, "stat_fan1_rpm_title")
            fan2_title = self.findChild(QLabel, "stat_fan2_rpm_title")
            if not caps.has_fan_rpm:
                # No fan telemetry: hide the tiles rather than fabricate 0 RPM.
                self.fan1_tile.setVisible(False)
                self.fan2_tile.setVisible(False)
            elif caps.fan_count == 1:
                self.fan1_tile.setVisible(True)
                self.fan2_tile.setVisible(False)
                if fan1_title:
                    fan1_title.setText("System Fan Speed")
            else:
                self.fan1_tile.setVisible(True)
                self.fan2_tile.setVisible(True)
                if fan1_title:
                    fan1_title.setText("CPU Fan Speed")
                if fan2_title:
                    fan2_title.setText("GPU Fan Speed")

            current_prof_id = self.acpi_ctrl.get_profile()
            state: FanState = self.acpi_ctrl.read_state() or FanState()

            # CPU Temp
            cpu_lbl = self.findChild(QLabel, "stat_cpu_temp")
            cpu_sub = self.findChild(QLabel, "stat_cpu_sub")
            if cpu_lbl:
                if state.temp_cpu is not None:
                    cpu_lbl.setText(f"{state.temp_cpu} °C")
                    if state.temp_cpu >= 85:
                        temp_color = THEME.error
                    elif state.temp_cpu >= 65:
                        temp_color = THEME.warning
                    else:
                        temp_color = THEME.success
                    self.cpu_tile.set_value_color(temp_color)
                    self.cpu_tile.set_icon("thermometer", temp_color)
                    ratio = max(0.05, min(1.0, (state.temp_cpu - 30.0) / 65.0))
                    self.cpu_tile.set_bar(ratio, temp_color)
                    if cpu_sub:
                        cpu_sub.setText("Optimal Thermal" if state.temp_cpu < 65 else "Active Load")
                else:
                    cpu_lbl.setText("--")
                    self.cpu_tile.set_value_color(THEME.on_surface)
                    self.cpu_tile.set_icon("thermometer", THEME.on_surface_variant)
                    self.cpu_tile.set_bar(0.0, THEME.primary)
                    if cpu_sub:
                        cpu_sub.setText("Monitoring")

            # Fans (a failed read is unknown, not "stopped")
            f1_lbl = self.findChild(QLabel, "stat_fan1_rpm")
            f1_sub = self.findChild(QLabel, "stat_fan1_sub")
            if f1_lbl:
                if state.fan1_rpm is None:
                    f1_lbl.setText("--")
                    self.fan1_tile.set_value_color(THEME.on_surface_muted)
                    self.fan1_tile.set_bar(0.0, THEME.primary)
                    if f1_sub:
                        f1_sub.setText("No data")
                else:
                    f1_lbl.setText(f"{state.fan1_rpm} RPM")
                    f1_color = THEME.primary if state.fan1_rpm > 0 else THEME.on_surface_muted
                    self.fan1_tile.set_value_color(THEME.on_surface if state.fan1_rpm > 0 else THEME.on_surface_muted)
                    self.fan1_tile.set_bar(max(0.0, min(1.0, state.fan1_rpm / 5800.0)), f1_color)
                    if f1_sub:
                        f1_sub.setText("Silent / Stopped" if state.fan1_rpm == 0 else "Active Exhaust")

            f2_lbl = self.findChild(QLabel, "stat_fan2_rpm")
            f2_sub = self.findChild(QLabel, "stat_fan2_sub")
            if f2_lbl:
                if state.fan2_rpm is None:
                    f2_lbl.setText("--")
                    self.fan2_tile.set_value_color(THEME.on_surface_muted)
                    self.fan2_tile.set_bar(0.0, THEME.primary)
                    if f2_sub:
                        f2_sub.setText("No data")
                else:
                    f2_lbl.setText(f"{state.fan2_rpm} RPM")
                    f2_color = THEME.primary if state.fan2_rpm > 0 else THEME.on_surface_muted
                    self.fan2_tile.set_value_color(THEME.on_surface if state.fan2_rpm > 0 else THEME.on_surface_muted)
                    self.fan2_tile.set_bar(max(0.0, min(1.0, state.fan2_rpm / 5800.0)), f2_color)
                    if f2_sub:
                        f2_sub.setText("Silent / Stopped" if state.fan2_rpm == 0 else "Active Exhaust")

            duty_lbl = self.findChild(QLabel, "stat_duty")
            duty_sub = self.findChild(QLabel, "stat_duty_sub")
            if duty_lbl:
                if state.duty_total is not None:
                    duty_lbl.setText(f"{state.duty_total}%")
                    duty_color = THEME.accent_cta if state.duty_total >= 70 else THEME.primary
                    self.duty_tile.set_value_color(THEME.on_surface)
                    self.duty_tile.set_bar(max(0.0, min(1.0, state.duty_total / 100.0)), duty_color)
                else:
                    duty_lbl.setText("--")
                    self.duty_tile.set_value_color(THEME.on_surface_muted)
                    self.duty_tile.set_bar(0.0, THEME.primary)
                if duty_sub:
                    duty_sub.setText("Hardware Auto Curve")
        else:
            # ACPI unavailable: hide the ACPI-dependent tiles instead of
            # rendering a wall of "--"/"Unavailable" metrics.
            self.cpu_tile.setVisible(False)
            self.fan1_tile.setVisible(False)
            self.fan2_tile.setVisible(False)
            self.duty_tile.setVisible(False)

            # ACPI unavailable — show warning box and disable buttons
            sys_caps = detect_system_capabilities()
            self.acpi_warning_box.setVisible(True)
            if sys_caps.is_gigabyte_laptop:
                self.acpi_warning_box.set_tone("warning", "warning")
                self.wb_title.setText("ACPI Kernel Driver (gigamate_acpi) Not Loaded")
                self.wb_desc.setText(
                    "Hardware fan profiles and live telemetry require the gigamate_acpi kernel module. "
                    "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
                )
                reason_str = "Driver Required"
            else:
                self.acpi_warning_box.set_tone("info", "info")
                self.wb_title.setText("Non-Gigabyte Hardware Detected")
                self.wb_desc.setText(
                    "Hardware thermal profiles and Embedded Controller sensor telemetry are only available "
                    "on supported Gigabyte Aero / AORUS laptops."
                )
                reason_str = "Unavailable"

            for btn in self._profile_buttons.values():
                btn.setEnabled(False)

            for sub_name in ("stat_cpu_sub", "stat_fan1_sub", "stat_fan2_sub", "stat_duty_sub"):
                lbl = self.findChild(QLabel, sub_name)
                if lbl:
                    lbl.setText(reason_str)

        # GPU State
        gpu = get_gpu_state()
        gpu_lbl = self.findChild(QLabel, "stat_gpu")
        gpu_sub = self.findChild(QLabel, "stat_gpu_sub")
        if not gpu.present:
            self.gpu_header_pill.setVisible(False)
            if gpu_lbl:
                gpu_lbl.setText("Not Present")
                self.gpu_tile.set_value_color(THEME.on_surface_muted)
                self.gpu_tile.set_icon("gpu", THEME.on_surface_muted)
                if gpu_sub:
                    gpu_sub.setText("iGPU Only")
        elif gpu.gated_off or gpu.status == "gated_off":
            self.gpu_header_pill.setText("dGPU Gated · 0 W")
            self.gpu_header_pill.set_tone("gated")
            self.gpu_header_pill.setVisible(True)
            if gpu_lbl:
                gpu_lbl.setText("Off (gated)")
                self.gpu_tile.set_value_color(THEME.gated)
                self.gpu_tile.set_icon("gpu", THEME.gated)
                if gpu_sub:
                    gpu_sub.setText("Power rail cut (0 W)")
        elif gpu.status == "suspended" or gpu.power_state in ("D3hot", "D3cold"):
            self.gpu_header_pill.setText("dGPU Asleep")
            self.gpu_header_pill.set_tone("success")
            self.gpu_header_pill.setVisible(True)
            if gpu_lbl:
                gpu_lbl.setText(f"Asleep ({gpu.power_state or 'D3cold'})")
                self.gpu_tile.set_value_color(THEME.success)
                self.gpu_tile.set_icon("gpu", THEME.success)
                if gpu_sub:
                    gpu_sub.setText("Power gate off")
        else:
            self.gpu_header_pill.setText("dGPU Active")
            self.gpu_header_pill.set_tone("warning")
            self.gpu_header_pill.setVisible(True)
            if gpu_lbl:
                gpu_lbl.setText(f"Active ({gpu.power_state or 'D0'})")
                self.gpu_tile.set_value_color(THEME.warning)
                self.gpu_tile.set_icon("gpu", THEME.warning)
                if gpu_sub:
                    gpu_sub.setText("In use")

        # When ACPI telemetry is unavailable the card only carries the GPU
        # tile; hide it entirely if there is no discrete GPU either.
        if not self.acpi_ctrl.available:
            self.telemetry_card.setVisible(gpu.present)

        # Update profile buttons state
        if current_prof_id is None:
            cfg = load_config()
            current_prof_id = cfg.get("acpi_profile")

        for pid, btn in self._profile_buttons.items():
            btn.setChecked(current_prof_id == pid)

        self._update_power_limits_ui(
            active_prof_id=current_prof_id,
            usable=usable and self.acpi_ctrl.available,
            gpu=gpu,
        )

    def _update_power_limits_ui(
        self,
        active_prof_id: Optional[int],
        usable: bool = True,
        gpu: Optional[GpuState] = None,
    ) -> None:
        """Render the per-profile power comparison matrix, hiding any absent/invalid metrics."""
        if not usable:
            self.power_limits_container.setVisible(False)
            return

        sys_limits = discover_power_limits(active_profile_id=active_prof_id, gpu_state=gpu)
        enabled_pids = set(enabled_acpi_profiles(self._discovered_profiles(), self._config()).keys())
        valid_pids = [
            k
            for k, p in sorted(sys_limits.profiles.items())
            if p.has_any and (not enabled_pids or k in enabled_pids)
        ]
        if not valid_pids:
            self.power_limits_container.setVisible(False)
            return

        pid = active_prof_id if active_prof_id in sys_limits.profiles else valid_pids[0]

        self.power_limits_container.setVisible(True)
        self._battery_notice_row.setVisible(sys_limits.on_battery)
        self.power_battery_notice.setVisible(sys_limits.on_battery)
        self.compare_limits_frame.setVisible(True)

        cache_key = (
            tuple((k, sys_limits.profiles[k]) for k in valid_pids),
            pid,
            sys_limits.dynamic_boost_active,
        )
        if cache_key != self._last_limits_key:
            self._last_limits_key = cache_key
            self._rebuild_compare_matrix(sys_limits, valid_pids, pid)

    def _rebuild_compare_matrix(
        self, sys_limits: SystemPowerLimits, valid_pids: list[int], active_pid: int
    ) -> None:
        while self.compare_grid.count():
            item = self.compare_grid.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        default_names = {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"}
        decl_profiles = self._discovered_profiles()

        hdr_metric = QLabel("Power Limit")
        hdr_metric.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr_metric.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; font-weight: 700; "
            f"text-transform: uppercase; letter-spacing: 0.5px; background: transparent;"
        )
        self.compare_grid.addWidget(hdr_metric, 0, 0)

        for col_idx, pid in enumerate(valid_pids, start=1):
            pname = (decl_profiles.get(pid) or decl_profiles.get(str(pid)) or {}).get(
                "name", default_names.get(pid, f"Mode {pid}")
            )
            hdr_cell = QWidget()
            hdr_cell.setStyleSheet("background: transparent;")
            hdr_lay = QHBoxLayout(hdr_cell)
            hdr_lay.setContentsMargins(0, 0, 0, 0)
            hdr_lay.setSpacing(5)
            hdr_lay.addStretch()
            if pid == active_pid:
                dot = QLabel()
                dot.setFixedSize(8, 8)
                dot.setStyleSheet("background: transparent;")
                dot.setPixmap(icons.render_icon_pixmap("dot", color=THEME.primary, size=8))
                hdr_lay.addWidget(dot)
            lbl = QLabel(pname)
            color = THEME.primary if pid == active_pid else THEME.on_surface_variant
            lbl.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: 700; background: transparent;"
            )
            hdr_lay.addWidget(lbl)
            self.compare_grid.addWidget(hdr_cell, 0, col_idx)

        rows = [
            (
                "Combined (CPU + GPU)",
                lambda p: f"{p.combined_w} W" if p.combined_w is not None else None,
            ),
            (
                "CPU Shared (w/ GPU)",
                lambda p: f"{p.cpu_shared_w} W" if p.cpu_shared_w is not None else None,
            ),
            (
                "Max CPU (Sustained / Burst)",
                lambda p: (
                    f"{p.cpu_sustained_w}W / {p.cpu_burst_w}W"
                    if p.cpu_sustained_w is not None and p.cpu_burst_w is not None and p.cpu_burst_w > p.cpu_sustained_w
                    else (f"{p.cpu_sustained_w} W" if p.cpu_sustained_w is not None else None)
                ),
            ),
            (
                "GPU Fixed (no Boost)",
                lambda p: f"{p.gpu_fixed_w} W" if p.gpu_fixed_w is not None else None,
            ),
            (
                "GPU w/ Dynamic Boost",
                lambda p: (
                    f"Up to {p.gpu_boost_w} W"
                    if p.gpu_boost_w is not None and p.gpu_fixed_w is not None and p.gpu_boost_w > p.gpu_fixed_w
                    else (f"{p.gpu_boost_w} W" if p.gpu_boost_w is not None else None)
                ),
            ),
        ]

        row_idx = 1
        for label_text, extractor in rows:
            vals = [extractor(sys_limits.profiles[pid]) for pid in valid_pids]
            if all(v is None for v in vals):
                continue
            r_lbl = QLabel(label_text)
            r_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            r_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: 12px; font-weight: 500; background: transparent;"
            )
            self.compare_grid.addWidget(r_lbl, row_idx, 0)
            for col_idx, (pid, val_str) in enumerate(zip(valid_pids, vals), start=1):
                v_lbl = QLabel(val_str or "")
                v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                weight = "700" if pid == active_pid else "500"
                color = THEME.on_surface if pid == active_pid else THEME.on_surface_variant
                v_lbl.setStyleSheet(
                    f"color: {color}; font-family: {THEME.mono_family}; "
                    f"font-size: 13px; font-weight: {weight}; background: transparent;"
                )
                self.compare_grid.addWidget(v_lbl, row_idx, col_idx)
            row_idx += 1
