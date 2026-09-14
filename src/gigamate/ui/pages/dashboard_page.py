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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...acpi import AcpiController, FanProfile, FanState
from ...capabilities import detect_system_capabilities
from ...config import load as load_config, update_config
from ...gpu import get_gpu_state, gpu_status_text
from ...system_power import sync_system_power


class DashboardPage(QWidget):
    """Performance profile controls and live thermal/fan monitoring."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.acpi_ctrl = AcpiController()
        self._profile_buttons = {}
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
        p_sub = QLabel("Select hardware performance and acoustic mode. Dynamic Boost & SmartShift auto-scale.")
        p_sub.setProperty("class", "CardSubtitle")
        prof_layout.addWidget(p_title)
        prof_layout.addWidget(p_sub)

        # Warning box for uninstalled/unloaded ACPI driver or non-Gigabyte hardware
        self.acpi_warning_box = QFrame()
        self.acpi_warning_box.setStyleSheet(
            "background-color: #2c1b12; border: 1px solid #744210; border-radius: 8px; padding: 12px;"
        )
        wb_lay = QVBoxLayout(self.acpi_warning_box)
        wb_lay.setContentsMargins(12, 10, 12, 10)
        wb_lay.setSpacing(4)
        self.wb_title = QLabel("⚠️ ACPI Kernel Driver Not Loaded")
        self.wb_title.setStyleSheet("color: #f6ad55; font-size: 13px; font-weight: 600;")
        self.wb_desc = QLabel(
            "Hardware fan control and live telemetry require the gigamate_acpi kernel driver. "
            "Re-run install.sh (it installs the module for every kernel), or try: sudo modprobe gigamate_acpi."
        )
        self.wb_desc.setStyleSheet("color: #cbd5e0; font-size: 12px;")
        self.wb_desc.setWordWrap(True)
        wb_lay.addWidget(self.wb_title)
        wb_lay.addWidget(self.wb_desc)
        prof_layout.addWidget(self.acpi_warning_box)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.prof_group = QButtonGroup(self)
        self.prof_group.setExclusive(True)

        profiles = [
            (FanProfile.QUIET, "Quiet"),
            (FanProfile.BALANCED, "Balanced"),
            (FanProfile.PERFORMANCE, "Performance"),
            (FanProfile.GAMING, "Gaming"),
        ]

        for prof, name in profiles:
            btn = QPushButton(name)
            btn.setProperty("class", "ProfileButton")
            btn.setCheckable(True)
            btn.setMinimumHeight(48)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, p=prof: self._select_profile(p))
            self._profile_buttons[prof] = btn
            self.prof_group.addButton(btn, prof.value)
            btn_layout.addWidget(btn)

        prof_layout.addLayout(btn_layout)
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
        t_layout.setContentsMargins(14, 12, 14, 12)
        t_layout.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel(icon_str)
        icon_lbl.setStyleSheet("font-size: 13px;")
        hdr.addWidget(icon_lbl)

        lbl_title = QLabel(title)
        lbl_title.setObjectName(f"{object_name}_title" if object_name else "")
        lbl_title.setStyleSheet("color: #718096; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;")
        hdr.addWidget(lbl_title)
        hdr.addStretch()
        t_layout.addLayout(hdr)

        lbl_val = QLabel("--")
        lbl_val.setObjectName(object_name)
        lbl_val.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 700; margin-top: 2px;")
        t_layout.addWidget(lbl_val)

        if subtext_obj:
            lbl_sub = QLabel("")
            lbl_sub.setObjectName(subtext_obj)
            lbl_sub.setStyleSheet("color: #8896ab; font-size: 11px;")
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

    def _select_profile(self, profile: FanProfile) -> None:
        """Switch ACPI profile and sync system/GPU power per user preference."""
        self._ensure_acpi_controller()

        def _mutate(cfg):
            cfg["acpi_profile"] = profile.value
            return cfg

        cfg = update_config(_mutate)

        if self.acpi_ctrl.available:
            self.acpi_ctrl.set_profile(profile)

        # sync_system_power() also synchronizes GPU power (Dynamic Boost /
        # SmartShift); honour the user's preference like the tray and Settings.
        if cfg.get("sync_system_power", False):
            sync_system_power(profile.value)

        if profile in self._profile_buttons:
            self._profile_buttons[profile].setChecked(True)

        self._refresh_telemetry()

    def _refresh_telemetry(self) -> None:
        """Update sensor numbers and highlight active profile."""
        current_prof_id: Optional[int] = None
        self._ensure_acpi_controller()

        if self.acpi_ctrl.available:
            self.acpi_warning_box.setVisible(False)
            self.telemetry_card.setVisible(True)
            self.cpu_tile.setVisible(True)
            self.duty_tile.setVisible(True)

            # Fan count awareness
            caps = self.acpi_ctrl.capabilities
            # Only offer profile switching when the backend actually exposes it.
            for btn in self._profile_buttons.values():
                btn.setEnabled(bool(caps.has_power_profiles))
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
                    if cpu_sub:
                        cpu_sub.setText("Optimal Thermal" if state.temp_cpu < 65 else "Active Load")
                else:
                    cpu_lbl.setText("--")
                    if cpu_sub:
                        cpu_sub.setText("Monitoring")

            # Fans
            f1_lbl = self.findChild(QLabel, "stat_fan1_rpm")
            f1_sub = self.findChild(QLabel, "stat_fan1_sub")
            if f1_lbl:
                rpm1 = state.fan1_rpm if state.fan1_rpm is not None else 0
                f1_lbl.setText(f"{rpm1} RPM")
                if f1_sub:
                    f1_sub.setText("Silent / Stopped" if rpm1 == 0 else "Active Exhaust")

            f2_lbl = self.findChild(QLabel, "stat_fan2_rpm")
            f2_sub = self.findChild(QLabel, "stat_fan2_sub")
            if f2_lbl:
                rpm2 = state.fan2_rpm if state.fan2_rpm is not None else 0
                f2_lbl.setText(f"{rpm2} RPM")
                if f2_sub:
                    f2_sub.setText("Silent / Stopped" if rpm2 == 0 else "Active Exhaust")

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
                    gpu_sub.setText("0W")
            else:
                gpu_lbl.setText(f"Active ({gpu.power_state or 'D0'})")
                gpu_lbl.setStyleSheet("color: #ed8936; font-size: 20px; font-weight: 700;")
                if gpu_sub:
                    gpu_sub.setText("High Performance")

        # When ACPI telemetry is unavailable the card only carries the GPU
        # tile; hide it entirely if there is no discrete GPU either.
        if not self.acpi_ctrl.available:
            self.telemetry_card.setVisible(gpu.present)

        # Update profile buttons state
        if current_prof_id is None:
            cfg = load_config()
            current_prof_id = cfg.get("acpi_profile")

        for prof, btn in self._profile_buttons.items():
            btn.setChecked(current_prof_id == prof.value)
