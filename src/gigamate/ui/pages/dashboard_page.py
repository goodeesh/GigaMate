"""GigaMate Center — Performance & Hardware Dashboard."""

import time
from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...acpi import AcpiController, FanProfile, FanState
from ...capabilities import detect_system_capabilities
from ...config import load as load_config, update_config
from ...gpu import GpuState, get_gpu_state, gpu_status_text
from ...power_limits import SystemPowerLimits, discover_power_limits
from ...profiles import profile_usable, resolve_model
from ...system_power import sync_system_power


class DashboardPage(QWidget):
    """Performance profile controls and live thermal/fan monitoring."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.acpi_ctrl = AcpiController()
        self._profile_buttons = {}
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
        self._cached_profile = None
        self._refresh_telemetry()

    def _model_profile(self):
        """Resolve the matching model profile (cached; refreshed on reload)."""
        if self._cached_profile is None:
            try:
                self._cached_profile = resolve_model()
            except Exception:
                self._cached_profile = None
        return self._cached_profile

    def _config(self) -> dict:
        try:
            return load_config()
        except Exception:
            return {}

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Power & Fan Profile Selector Card ──
        prof_card = QFrame()
        prof_card.setProperty("class", "Card")
        prof_layout = QVBoxLayout(prof_card)
        prof_layout.setSpacing(12)

        p_title = QLabel("Power & Thermal Profiles")
        p_title.setProperty("class", "CardTitle")
        p_sub = QLabel(
            "Controls hardware power and thermal policy. Effects vary by laptop model and firmware, "
            "ranging from fan response curves to TDP/TGP power limits."
        )
        p_sub.setProperty("class", "CardSubtitle")
        p_sub.setWordWrap(True)
        prof_layout.addWidget(p_title)
        prof_layout.addWidget(p_sub)

        # Warning box for uninstalled/unloaded ACPI driver or non-Gigabyte hardware
        self.acpi_warning_box = QFrame()
        self.acpi_warning_box.setStyleSheet(
            "background-color: #26160c; border: 1px solid #6b3512; border-radius: 10px; padding: 14px;"
        )
        wb_lay = QVBoxLayout(self.acpi_warning_box)
        wb_lay.setContentsMargins(14, 12, 14, 12)
        wb_lay.setSpacing(6)
        self.wb_title = QLabel("⚠️ ACPI Kernel Driver Not Loaded")
        self.wb_title.setStyleSheet("color: #f6ad55; font-size: 13px; font-weight: 700;")
        self.wb_desc = QLabel(
            "Hardware fan control and live telemetry require the gigamate_acpi kernel driver. "
            "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
        )
        self.wb_desc.setStyleSheet("color: #e2e8f0; font-size: 12px; line-height: 1.4;")
        self.wb_desc.setWordWrap(True)
        wb_lay.addWidget(self.wb_title)
        wb_lay.addWidget(self.wb_desc)
        prof_layout.addWidget(self.acpi_warning_box)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.prof_group = QButtonGroup(self)
        self.prof_group.setExclusive(True)
        self.prof_layout = btn_layout
        self._btn_ids: list = []

        # Shown only for experimental (unconfirmed) models before opt-in.
        self.btn_enable_experimental = QPushButton("Enable Experimental Profiles")
        self.btn_enable_experimental.setProperty("class", "ProfileButton")
        self.btn_enable_experimental.setMinimumHeight(40)
        self.btn_enable_experimental.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_enable_experimental.clicked.connect(self._on_enable_experimental)
        self.btn_enable_experimental.setVisible(False)
        prof_layout.addWidget(self.btn_enable_experimental)

        prof_layout.addLayout(btn_layout)

        # ── Discovered Profile Power Limits Comparison Matrix ──
        self.power_limits_container = QFrame()
        pl_layout = QVBoxLayout(self.power_limits_container)
        pl_layout.setContentsMargins(0, 6, 0, 0)
        pl_layout.setSpacing(8)

        self.power_battery_notice = QLabel(
            "⚡ Running on battery — firmware applies reduced battery power limits."
        )
        self.power_battery_notice.setStyleSheet(
            "color: #f6ad55; font-size: 12px; font-weight: 600; padding: 4px 2px;"
        )
        self.power_battery_notice.setVisible(False)
        pl_layout.addWidget(self.power_battery_notice)

        self.compare_limits_frame = QFrame()
        self.compare_limits_frame.setProperty("class", "MetricTile")
        self.compare_grid = QGridLayout(self.compare_limits_frame)
        self.compare_grid.setContentsMargins(16, 12, 16, 12)
        self.compare_grid.setHorizontalSpacing(18)
        self.compare_grid.setVerticalSpacing(6)
        pl_layout.addWidget(self.compare_limits_frame)

        self._last_limits_key = None
        self.power_limits_container.setVisible(False)
        prof_layout.addWidget(self.power_limits_container)
        layout.addWidget(prof_card)

        # ── Live Telemetry Cards ──
        self.telemetry_card = QFrame()
        self.telemetry_card.setProperty("class", "Card")
        t_layout = QVBoxLayout(self.telemetry_card)
        t_layout.setSpacing(14)
        t_title = QLabel("Real-Time Hardware Telemetry")
        t_title.setProperty("class", "CardTitle")
        t_layout.addWidget(t_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        # Row 0: Thermals & GPU (CPU Temp & Discrete GPU)
        self.cpu_tile = self._make_stat_tile("CPU Temperature", "stat_cpu_temp", "🌡️", "stat_cpu_sub")
        grid.addWidget(self.cpu_tile, 0, 0)
        grid.addWidget(self._make_stat_tile("Discrete GPU State", "stat_gpu", "⚡", "stat_gpu_sub"), 0, 1, 1, 2)

        # Row 1: Cooling Fans & Total Duty
        self.fan1_tile = self._make_stat_tile("CPU Fan Speed", "stat_fan1_rpm", "🌀", "stat_fan1_sub")
        self.fan2_tile = self._make_stat_tile("GPU Fan Speed", "stat_fan2_rpm", "🌀", "stat_fan2_sub")
        self.duty_tile = self._make_stat_tile("Total Fan Duty", "stat_duty", "📊", "stat_duty_sub")

        grid.addWidget(self.fan1_tile, 1, 0)
        grid.addWidget(self.fan2_tile, 1, 1)
        grid.addWidget(self.duty_tile, 1, 2)

        t_layout.addLayout(grid)
        layout.addWidget(self.telemetry_card)

        layout.addStretch()
        self._refresh_telemetry()

    def _make_stat_tile(self, title: str, object_name: str, icon_str: str, subtext_obj: str = "") -> QFrame:
        tile = QFrame()
        tile.setProperty("class", "MetricTile")
        t_layout = QVBoxLayout(tile)
        t_layout.setContentsMargins(16, 14, 16, 14)
        t_layout.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel(icon_str)
        icon_lbl.setStyleSheet("font-size: 14px;")
        hdr.addWidget(icon_lbl)

        lbl_title = QLabel(title)
        lbl_title.setObjectName(f"{object_name}_title" if object_name else "")
        lbl_title.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;")
        hdr.addWidget(lbl_title)
        hdr.addStretch()
        t_layout.addLayout(hdr)

        lbl_val = QLabel("--")
        lbl_val.setObjectName(object_name)
        lbl_val.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: 800; margin-top: 2px;")
        t_layout.addWidget(lbl_val)

        if subtext_obj:
            lbl_sub = QLabel("")
            lbl_sub.setObjectName(subtext_obj)
            lbl_sub.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500;")
            t_layout.addWidget(lbl_sub)

        return tile

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
        if not profile_usable(self._model_profile(), self._config()):
            return

        def _mutate(cfg):
            cfg["acpi_profile"] = profile_id
            return cfg

        cfg = update_config(_mutate)

        if self.acpi_ctrl.available:
            self.acpi_ctrl.set_profile(FanProfile(profile_id))

        # sync_system_power() also synchronizes GPU power (Dynamic Boost /
        # SmartShift); honour the user's preference like the tray and Settings.
        if cfg.get("sync_system_power", False):
            sync_system_power(profile_id)

        btn = self._profile_buttons.get(profile_id)
        if btn is not None:
            btn.setChecked(True)

        self._refresh_telemetry()

    def _rebuild_profile_buttons(self, match, usable: bool) -> None:
        """(Re)build the profile buttons from the model's declared profile set."""
        names = match.profile.acpi.profiles if (match is not None and match.profile.acpi) else {}
        ids = sorted(int(k) for k in names.keys())

        if ids != self._btn_ids:
            for btn in self._profile_buttons.values():
                self.prof_group.removeButton(btn)
                self.prof_layout.removeWidget(btn)
                btn.deleteLater()
            self._profile_buttons = {}
            self._btn_ids = []
            for pid in ids:
                label = (names.get(str(pid)) or {}).get("name", f"Profile {pid}")
                btn = QPushButton(label)
                btn.setProperty("class", "ProfileButton")
                btn.setCheckable(True)
                btn.setMinimumHeight(48)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda _, p=pid: self._select_profile(p))
                self.prof_group.addButton(btn, pid)
                self.prof_layout.addWidget(btn)
                self._profile_buttons[pid] = btn
                self._btn_ids.append(pid)

        for btn in self._profile_buttons.values():
            btn.setEnabled(usable)
        self.btn_enable_experimental.setVisible(
            bool(match is not None and match.experimental and not usable))

    def _on_enable_experimental(self) -> None:
        """One-time consent prompt for experimental (unconfirmed) profiles."""
        box = QMessageBox(self)
        box.setWindowTitle("Enable experimental profiles?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Profile switching for this model is community-evidenced but "
                    "unconfirmed — it may do nothing.")
        box.setInformativeText("Enable it anyway? You can report results with "
                               "'gigamate profile report'.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            update_config(lambda c: {**c, "experimental_profiles_enabled": True})
        except Exception:
            pass
        self._cached_profile = None
        self._refresh_telemetry()

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
            # Profile switching is a per-model contract: build the buttons from
            # the model's declared set and gate them on consent.
            match = self._model_profile()
            usable = profile_usable(match, self._config())
            self._rebuild_profile_buttons(match, usable)
            if match is not None:
                self.acpi_warning_box.setVisible(False)
            else:
                self.acpi_warning_box.setVisible(True)
                self.wb_title.setText("ℹ️ No profile support for this model")
                self.wb_desc.setText(
                    "Profile switching is only exposed on models with a matching "
                    "profile. Run 'gigamate calibrate acpi' to generate one.")
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
                        cpu_lbl.setStyleSheet("color: #fc8181; font-size: 22px; font-weight: 800; margin-top: 2px;")
                    elif state.temp_cpu >= 75:
                        cpu_lbl.setStyleSheet("color: #f6ad55; font-size: 22px; font-weight: 800; margin-top: 2px;")
                    else:
                        cpu_lbl.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: 800; margin-top: 2px;")
                    if cpu_sub:
                        cpu_sub.setText("Optimal Thermal" if state.temp_cpu < 65 else "Active Load")
                else:
                    cpu_lbl.setText("--")
                    cpu_lbl.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: 800; margin-top: 2px;")
                    if cpu_sub:
                        cpu_sub.setText("Monitoring")

            # Fans (a failed read is unknown, not "stopped")
            f1_lbl = self.findChild(QLabel, "stat_fan1_rpm")
            f1_sub = self.findChild(QLabel, "stat_fan1_sub")
            if f1_lbl:
                if state.fan1_rpm is None:
                    f1_lbl.setText("--")
                    if f1_sub:
                        f1_sub.setText("No data")
                else:
                    f1_lbl.setText(f"{state.fan1_rpm} RPM")
                    if f1_sub:
                        f1_sub.setText("Silent / Stopped" if state.fan1_rpm == 0 else "Active Exhaust")

            f2_lbl = self.findChild(QLabel, "stat_fan2_rpm")
            f2_sub = self.findChild(QLabel, "stat_fan2_sub")
            if f2_lbl:
                if state.fan2_rpm is None:
                    f2_lbl.setText("--")
                    if f2_sub:
                        f2_sub.setText("No data")
                else:
                    f2_lbl.setText(f"{state.fan2_rpm} RPM")
                    if f2_sub:
                        f2_sub.setText("Silent / Stopped" if state.fan2_rpm == 0 else "Active Exhaust")

            duty_lbl = self.findChild(QLabel, "stat_duty")
            duty_sub = self.findChild(QLabel, "stat_duty_sub")
            if duty_lbl:
                duty_lbl.setText(f"{state.duty_total}%" if state.duty_total is not None else "--")
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
                self.wb_title.setText("⚠️ ACPI Kernel Driver (gigamate_acpi) Not Loaded")
                self.wb_desc.setText(
                    "Hardware fan profiles and live telemetry require the gigamate_acpi kernel module. "
                    "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
                )
                reason_str = "Driver Required"
            else:
                self.wb_title.setText("ℹ️ Non-Gigabyte Hardware Detected")
                self.wb_desc.setText(
                    "Hardware thermal profiles and Embedded Controller sensor telemetry are only available "
                    "on supported Gigabyte Aero / AORUS laptops."
                )
                reason_str = "Unavailable"

            for btn in self._profile_buttons.values():
                btn.setEnabled(False)
            self.btn_enable_experimental.setVisible(False)

            for sub_name in ("stat_cpu_sub", "stat_fan1_sub", "stat_fan2_sub", "stat_duty_sub"):
                lbl = self.findChild(QLabel, sub_name)
                if lbl:
                    lbl.setText(reason_str)

        # GPU State
        gpu = get_gpu_state()
        gpu_lbl = self.findChild(QLabel, "stat_gpu")
        gpu_sub = self.findChild(QLabel, "stat_gpu_sub")
        if gpu_lbl:
            if not gpu.present:
                gpu_lbl.setText("Not Present")
                gpu_lbl.setStyleSheet("color: #718096; font-size: 20px; font-weight: 700;")
                if gpu_sub:
                    gpu_sub.setText("iGPU Only")
            elif gpu.status == "suspended" or gpu.power_state in ("D3hot", "D3cold"):
                gpu_lbl.setText(f"Asleep ({gpu.power_state or 'D3cold'})")
                gpu_lbl.setStyleSheet("color: #48bb78; font-size: 20px; font-weight: 700;")
                if gpu_sub:
                    gpu_sub.setText("Power gate off")
            else:
                gpu_lbl.setText(f"Active ({gpu.power_state or 'D0'})")
                gpu_lbl.setStyleSheet("color: #ed8936; font-size: 20px; font-weight: 700;")
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
        valid_pids = [k for k, p in sorted(sys_limits.profiles.items()) if p.has_any]
        if not valid_pids:
            self.power_limits_container.setVisible(False)
            return

        pid = active_prof_id if active_prof_id in sys_limits.profiles else valid_pids[0]

        self.power_limits_container.setVisible(True)
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
        match = self._model_profile()
        decl_profiles = match.profile.acpi.profiles if (match is not None and match.profile.acpi) else {}

        hdr_metric = QLabel("Power Limit")
        hdr_metric.setStyleSheet(
            "color: #8896ab; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;"
        )
        self.compare_grid.addWidget(hdr_metric, 0, 0)

        for col_idx, pid in enumerate(valid_pids, start=1):
            pname = (decl_profiles.get(str(pid)) or {}).get("name", default_names.get(pid, f"Mode {pid}"))
            prefix = "● " if pid == active_pid else ""
            lbl = QLabel(f"{prefix}{pname}")
            color = "#63b3ed" if pid == active_pid else "#94a3b8"
            lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700;")
            self.compare_grid.addWidget(lbl, 0, col_idx)

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
            r_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 500;")
            self.compare_grid.addWidget(r_lbl, row_idx, 0)
            for col_idx, (pid, val_str) in enumerate(zip(valid_pids, vals), start=1):
                v_lbl = QLabel(val_str or "")
                weight = "800" if pid == active_pid else "500"
                color = "#ffffff" if pid == active_pid else "#94a3b8"
                v_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: {weight};")
                self.compare_grid.addWidget(v_lbl, row_idx, col_idx)
            row_idx += 1
