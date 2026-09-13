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
            (FanProfile.QUIET, "Quiet", "Silent acoustics, capped power"),
            (FanProfile.BALANCED, "Balanced", "Daily multi-tasking default"),
            (FanProfile.PERFORMANCE, "Performance", "Aggressive cooling curves"),
            (FanProfile.GAMING, "Gaming", "Maximum GPU TGP & Boost"),
        ]

        for prof, name, desc in profiles:
            btn = QPushButton(f"{name}\n{desc}")
            btn.setProperty("class", "ProfileButton")
            btn.setCheckable(True)
            btn.setMinimumHeight(64)
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
        t_title = QLabel("Real-Time Hardware Telemetry")
        t_title.setProperty("class", "CardTitle")
        t_layout.addWidget(t_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)

        # CPU Temp
        grid.addWidget(self._make_stat_box("CPU Temperature", "stat_cpu_temp"), 0, 0)
        # Socket Temp
        grid.addWidget(self._make_stat_box("Socket Temperature", "stat_socket_temp"), 0, 1)
        # Fan 1 RPM
        grid.addWidget(self._make_stat_box("CPU Fan Speed", "stat_fan1_rpm"), 1, 0)
        # Fan 2 RPM
        grid.addWidget(self._make_stat_box("GPU Fan Speed", "stat_fan2_rpm"), 1, 1)
        # Duty Cycle
        grid.addWidget(self._make_stat_box("Total Fan Duty", "stat_duty"), 2, 0)
        # Discrete GPU Power
        grid.addWidget(self._make_stat_box("Discrete GPU State", "stat_gpu"), 2, 1)

        t_layout.addLayout(grid)
        layout.addWidget(telemetry_card)

        layout.addStretch()
        self._refresh_telemetry()

    def _make_stat_box(self, title: str, object_name: str) -> QWidget:
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(4)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("color: #718096; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        lbl_val = QLabel("--")
        lbl_val.setObjectName(object_name)
        lbl_val.setStyleSheet("color: #ffffff; font-size: 20px; font-weight: 700;")

        box_layout.addWidget(lbl_title)
        box_layout.addWidget(lbl_val)
        return box

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
            if cpu_lbl:
                cpu_lbl.setText(f"{state.temp_cpu} °C" if state.temp_cpu is not None else "--")

            # Socket Temp
            sock_lbl = self.findChild(QLabel, "stat_socket_temp")
            if sock_lbl:
                sock_lbl.setText(f"{state.temp_socket} °C" if state.temp_socket is not None else "--")

            # Fans
            f1_lbl = self.findChild(QLabel, "stat_fan1_rpm")
            if f1_lbl:
                f1_lbl.setText(f"{state.fan1_rpm} RPM" if state.fan1_rpm is not None else "--")

            f2_lbl = self.findChild(QLabel, "stat_fan2_rpm")
            if f2_lbl:
                f2_lbl.setText(f"{state.fan2_rpm} RPM" if state.fan2_rpm is not None else "--")

            duty_lbl = self.findChild(QLabel, "stat_duty")
            if duty_lbl:
                duty_lbl.setText(f"{state.duty_total}%" if state.duty_total is not None else "--")

        # GPU State
        gpu = get_gpu_state()
        gpu_lbl = self.findChild(QLabel, "stat_gpu")
        if gpu_lbl:
            if not gpu.present:
                gpu_lbl.setText("Not Present")
            elif gpu.status == "suspended" or gpu.power_state in ("D3hot", "D3cold"):
                gpu_lbl.setText(f"Asleep ({gpu.power_state or 'D3cold'})")
                gpu_lbl.setStyleSheet("color: #48bb78; font-size: 20px; font-weight: 700;")
            else:
                gpu_lbl.setText(f"Active ({gpu.power_state or 'D0'})")
                gpu_lbl.setStyleSheet("color: #ed8936; font-size: 20px; font-weight: 700;")

        # Dynamic Boost info
        if gpu.vendor == "nvidia":
            if gpu.dynamic_boost_supported:
                status_str = "Active (Dynamic Boost up to ~80W)" if gpu.dynamic_boost_active else "Standby (nvidia-powerd)"
                self.boost_label.setText(f"⚡ NVIDIA Dynamic Boost: {status_str}")
            else:
                self.boost_label.setText("⚡ NVIDIA Discrete GPU: Baseline TGP")
        elif gpu.vendor == "amd":
            bias = f"Bias {gpu.smartshift_bias}" if gpu.smartshift_bias is not None else "Enabled"
            self.boost_label.setText(f"⚡ AMD SmartShift: {bias}")
        else:
            self.boost_label.setText("")

        # Update profile buttons state
        if current_prof_id is None:
            cfg = load_config()
            current_prof_id = cfg.get("acpi_profile", 1)

        for prof, btn in self._profile_buttons.items():
            btn.setChecked(current_prof_id == prof.value)
