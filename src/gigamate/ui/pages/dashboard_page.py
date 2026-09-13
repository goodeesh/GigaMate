"""GigaMate Center — Performance & Hardware Dashboard."""

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
from ...config import load as load_config, save as save_config
from ...gpu import get_gpu_state, gpu_status_text, sync_gpu_power
from ...system_power import sync_system_power


class DashboardPage(QWidget):
    """Performance profile controls and live thermal/fan monitoring."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.acpi_ctrl = AcpiController()
        self._profile_buttons = {}
        self._init_ui()

        # Telemetry update timer (every 1.5s)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_telemetry)
        self.timer.start(1500)

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

        # Dynamic Boost status banner
        self.boost_label = QLabel("GPU Boost: Checking...")
        self.boost_label.setStyleSheet("color: #a0aec0; font-size: 12px; margin-top: 6px;")
        prof_layout.addWidget(self.boost_label)
        layout.addWidget(prof_card)

        # ── Live Telemetry Cards ──
        telemetry_card = QFrame()
        telemetry_card.setProperty("class", "Card")
        t_layout = QVBoxLayout(telemetry_card)
        t_layout.setSpacing(14)
        t_title = QLabel("Real-Time Hardware Telemetry")
        t_title.setProperty("class", "CardTitle")
        t_layout.addWidget(t_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        # Row 0: Thermals & GPU (CPU Temp & Discrete GPU)
        grid.addWidget(self._make_stat_tile("CPU Temperature", "stat_cpu_temp", "🌡️", "stat_cpu_sub"), 0, 0)
        grid.addWidget(self._make_stat_tile("Discrete GPU State", "stat_gpu", "⚡", "stat_gpu_sub"), 0, 1, 1, 2)

        # Row 1: Cooling Fans & Total Duty
        grid.addWidget(self._make_stat_tile("CPU Fan Speed", "stat_fan1_rpm", "🌀", "stat_fan1_sub"), 1, 0)
        grid.addWidget(self._make_stat_tile("GPU Fan Speed", "stat_fan2_rpm", "🌀", "stat_fan2_sub"), 1, 1)
        grid.addWidget(self._make_stat_tile("Total Fan Duty", "stat_duty", "📊", "stat_duty_sub"), 1, 2)

        t_layout.addLayout(grid)
        layout.addWidget(telemetry_card)

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

    def _select_profile(self, profile: FanProfile) -> None:
        """Switch ACPI profile and sync Dynamic Boost."""
        if not self.acpi_ctrl.available:
            self.acpi_ctrl = AcpiController()

        if self.acpi_ctrl.available:
            self.acpi_ctrl.set_profile(profile)
            cfg = load_config()
            cfg["acpi_profile"] = profile.value
            save_config(cfg)
            sync_system_power(profile.value)
            sync_gpu_power(profile.value)

        if profile in self._profile_buttons:
            self._profile_buttons[profile].setChecked(True)

        self._refresh_telemetry()

    def _refresh_telemetry(self) -> None:
        """Update sensor numbers and highlight active profile."""
        current_prof_id: Optional[int] = None
        if not self.acpi_ctrl.available:
            self.acpi_ctrl = AcpiController()

        if self.acpi_ctrl.available:
            current_prof_id = self.acpi_ctrl.get_profile()
            state: FanState = self.acpi_ctrl.read_state()

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

        # Dynamic Boost / GPU Power telemetry
        if not gpu.present:
            self.boost_label.setText("⚡ Discrete GPU: Not Present (Integrated graphics only)")
        elif gpu.status == "suspended" or gpu.power_state in ("D3hot", "D3cold"):
            self.boost_label.setText(f"⚡ Discrete GPU: Asleep in {gpu.power_state or 'D3cold'} (0W)")
        elif gpu.vendor == "nvidia":
            if gpu.dynamic_boost_supported:
                status_str = "Active (Dynamic Boost up to ~80W)" if gpu.dynamic_boost_active else "Standby (nvidia-powerd)"
                self.boost_label.setText(f"⚡ NVIDIA Dynamic Boost: {status_str}")
            else:
                self.boost_label.setText(f"⚡ NVIDIA Discrete GPU: Active ({gpu.power_state or 'D0'})")
        elif gpu.vendor == "amd":
            bias = f"Bias {gpu.smartshift_bias}" if gpu.smartshift_bias is not None else "Enabled"
            self.boost_label.setText(f"⚡ AMD SmartShift: {bias}")
        else:
            self.boost_label.setText(f"⚡ Discrete GPU: Active ({gpu.power_state or 'D0'})")

        # Update profile buttons state
        if current_prof_id is None:
            cfg = load_config()
            current_prof_id = cfg.get("acpi_profile", 1)

        for prof, btn in self._profile_buttons.items():
            btn.setChecked(current_prof_id == prof.value)
