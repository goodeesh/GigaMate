"""GigaMate Center — Settings & System Preferences Page.

Allows configuring:
- Hardware settings reapplication on login / startup (power profile, battery threshold, RGB)
- Linux system power & dGPU Dynamic Boost profile synchronization
- Background user systemd service management (gigamate.service)
- System diagnostics & hardware info
"""

import subprocess
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...capabilities import detect_system_capabilities
from ...config import load as load_config, update_config
from ...profiles import has_verified_profiles, resolve_profile
from ...system_power import sync_system_power


class SettingsPage(QWidget):
    """Configuration page for startup behavior, power sync, and service controls."""

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Card 1: Startup & Hardware Preferences ──
        startup_card = QFrame()
        startup_card.setProperty("class", "Card")
        s_layout = QVBoxLayout(startup_card)
        s_layout.setSpacing(14)

        s_title = QLabel("Startup & Hardware Preferences")
        s_title.setProperty("class", "CardTitle")
        s_sub = QLabel("Configure which preferences are automatically restored on login and profile changes.")
        s_sub.setProperty("class", "CardSubtitle")
        s_layout.addWidget(s_title)
        s_layout.addWidget(s_sub)

        # Startup Apply Checkbox
        chk_box1 = QVBoxLayout()
        chk_box1.setSpacing(4)
        self.chk_startup_apply = QCheckBox("Apply hardware preferences automatically on login / startup")
        self.chk_startup_apply.setChecked(self.cfg.get("startup_apply", False))
        self.chk_startup_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_startup_apply.toggled.connect(self._on_startup_apply_toggled)

        subtext1 = QLabel("Re-applies your saved keyboard backlight when you log in. "
                          "The battery limit and power sync have their own toggles below.")
        subtext1.setStyleSheet("color: #8896ab; font-size: 12px; margin-left: 26px;")
        chk_box1.addWidget(self.chk_startup_apply)
        chk_box1.addWidget(subtext1)
        s_layout.addLayout(chk_box1)

        s_layout.addSpacing(6)

        # System Power Sync Checkbox
        chk_box2 = QVBoxLayout()
        chk_box2.setSpacing(4)
        self.chk_sync_power = QCheckBox("Synchronize system power profile with fan profile")
        self.chk_sync_power.setChecked(self.cfg.get("sync_system_power", False))
        self.chk_sync_power.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_sync_power.toggled.connect(self._on_sync_power_toggled)

        # System power sync is only meaningful with a verified model profile.
        _sync_enabled = bool(has_verified_profiles(self._resolved_model_profile()))
        self.chk_sync_power.setEnabled(_sync_enabled)

        subtext2 = QLabel("Aligns Linux power-profiles-daemon and NVIDIA/AMD Dynamic Boost with your active fan mode.")
        subtext2.setStyleSheet("color: #8896ab; font-size: 12px; margin-left: 26px;")
        chk_box2.addWidget(self.chk_sync_power)
        chk_box2.addWidget(subtext2)
        s_layout.addLayout(chk_box2)

        s_layout.addSpacing(6)

        # dGPU Auto-Apply Checkbox
        chk_box3 = QVBoxLayout()
        chk_box3.setSpacing(4)
        self.chk_dgpu_auto = QCheckBox("Apply dGPU undervolt / clock cap automatically on boot and resume")
        self.chk_dgpu_auto.setChecked(self.cfg.get("dgpu_undervolt_auto", True))
        self.chk_dgpu_auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_dgpu_auto.toggled.connect(self._on_dgpu_auto_toggled)

        subtext3 = QLabel("When off, the discrete GPU is left untouched on boot and resume; "
                          "use Apply on the GPU page (or the CLI) each session. Disabling does "
                          "not clear anything already applied.")
        subtext3.setStyleSheet("color: #8896ab; font-size: 12px; margin-left: 26px;")
        subtext3.setWordWrap(True)
        chk_box3.addWidget(self.chk_dgpu_auto)
        chk_box3.addWidget(subtext3)
        s_layout.addLayout(chk_box3)

        layout.addWidget(startup_card)

        # ── Card 2: Background Service Management ──
        service_card = QFrame()
        service_card.setProperty("class", "Card")
        svc_layout = QVBoxLayout(service_card)
        svc_layout.setSpacing(14)

        svc_title = QLabel("Background System Daemon")
        svc_title.setProperty("class", "CardTitle")
        svc_sub = QLabel("GigaMate runs a lightweight user daemon (gigamate.service) for hardware hotkeys and tray integration.")
        svc_sub.setProperty("class", "CardSubtitle")
        svc_layout.addWidget(svc_title)
        svc_layout.addWidget(svc_sub)

        svc_row = QHBoxLayout()
        svc_row.setSpacing(16)

        self.service_status_label = QLabel("Daemon: Checking...")
        self.service_status_label.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 600;")
        svc_row.addWidget(self.service_status_label)
        svc_row.addStretch()

        self.btn_restart_service = QPushButton("Restart Daemon")
        self.btn_restart_service.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart_service.clicked.connect(self._on_restart_service_clicked)
        svc_row.addWidget(self.btn_restart_service)

        self.btn_setup = QPushButton("Run Setup Again")
        self.btn_setup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_setup.clicked.connect(self._on_run_setup_clicked)
        svc_row.addWidget(self.btn_setup)

        svc_layout.addLayout(svc_row)
        layout.addWidget(service_card)

        # ── Card 3: Hardware Diagnostics & Environment ──
        diag_card = QFrame()
        diag_card.setProperty("class", "Card")
        d_layout = QVBoxLayout(diag_card)
        d_layout.setSpacing(14)

        d_title = QLabel("Hardware Diagnostics & Environment")
        d_title.setProperty("class", "CardTitle")
        d_layout.addWidget(d_title)

        self._diag_grid = QHBoxLayout()
        self._diag_grid.setSpacing(12)
        d_layout.addLayout(self._diag_grid)
        self._populate_diag_chips()

        layout.addWidget(diag_card)

        layout.addStretch()

        # Update service status
        self._refresh_service_status()

    def _make_chip(self, title: str, val_text: str, icon_str: str, sub_text: str = "") -> QFrame:
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

        val_lbl = QLabel(val_text)
        val_lbl.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 600; margin-top: 2px;")
        c_lay.addWidget(val_lbl)

        if sub_text:
            sub_lbl = QLabel(sub_text)
            sub_lbl.setStyleSheet("color: #8896ab; font-size: 10px; font-weight: 500;")
            c_lay.addWidget(sub_lbl)

        return chip

    def _populate_diag_chips(self) -> None:
        """(Re)build the hardware diagnostic chips from a fresh capability probe."""
        while self._diag_grid.count():
            item = self._diag_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        caps = detect_system_capabilities()

        # 1. Device Model
        model_sub = "(Supported)" if caps.is_gigabyte_laptop else "(Generic)"
        self._diag_grid.addWidget(self._make_chip("Device Model", caps.product_name, "💻", model_sub))

        # 2. Keyboard HID
        if caps.keyboard_detected and caps.keyboard_vid_pid:
            vid, pid = caps.keyboard_vid_pid
            kbd_val = f"0x{vid:04X}:0x{pid:04X}"
            kbd_sub = "(Profile Mapped)" if caps.keyboard_profile_loaded else "(Uncalibrated)"
        else:
            kbd_val = "Not Detected"
            kbd_sub = "(No RGB Controller)"
        self._diag_grid.addWidget(self._make_chip("Keyboard HID", kbd_val, "⌨️", kbd_sub))

        # 3. ACPI Driver
        if caps.acpi_driver_loaded:
            acpi_val = "gigamate_acpi"
            acpi_sub = "(Active / Loaded)"
        elif caps.is_gigabyte_laptop:
            acpi_val = "Not Loaded"
            acpi_sub = "(DKMS Required)"
        else:
            acpi_val = "Standard ACPI"
            acpi_sub = "(Generic Linux)"
        self._diag_grid.addWidget(self._make_chip("ACPI Driver", acpi_val, "⚡", acpi_sub))

        # 4. Battery Limiter
        if caps.charge_limit_supported:
            bat_val = "Supported"
            bat_sub = "(EC / sysfs)"
        elif caps.battery_present:
            bat_val = "Unsupported"
            bat_sub = "(Firmware Limited)"
        else:
            bat_val = "No Battery"
            bat_sub = "(Continuous AC)"
        self._diag_grid.addWidget(self._make_chip("Charge Limiter", bat_val, "🔋", bat_sub))

    def _resolved_model_profile(self):
        """Resolve the matching model profile (None when unmapped)."""
        try:
            return resolve_profile()
        except Exception:
            return None

    def _on_startup_apply_toggled(self, checked: bool) -> None:
        def _mutate(cfg):
            cfg["startup_apply"] = checked
            return cfg
        self.cfg = update_config(_mutate)

    def _on_sync_power_toggled(self, checked: bool) -> None:
        def _mutate(cfg):
            cfg["sync_system_power"] = checked
            # Choosing to sync implies a profile to sync from; default to
            # Balanced if the user has never picked one.
            if checked and cfg.get("acpi_profile") is None:
                cfg["acpi_profile"] = 1
            return cfg
        self.cfg = update_config(_mutate)
        if checked:
            sync_system_power(self.cfg.get("acpi_profile", 1))

    def _on_dgpu_auto_toggled(self, checked: bool) -> None:
        try:
            from ... import dgpu_tune
            dgpu_tune.set_auto_config(checked)
        except Exception:
            pass
        self.cfg = load_config()

    def _on_run_setup_clicked(self) -> None:
        """Re-open the first-run setup wizard."""
        try:
            from ..onboarding import OnboardingWizard
            OnboardingWizard(self).exec()
        except Exception:
            pass
        self.reload_from_config()

    def _on_restart_service_clicked(self) -> None:
        self.btn_restart_service.setEnabled(False)
        self.btn_restart_service.setText("Restarting...")
        try:
            # --no-block returns immediately so the GUI thread never waits for
            # the unit to fully restart (which can take many seconds).
            subprocess.run(
                ["systemctl", "--user", "restart", "--no-block", "gigamate.service"],
                check=False,
                timeout=5,
            )
        except Exception:
            pass
        finally:
            QTimer.singleShot(1500, self._finish_restart_service)

    def _finish_restart_service(self) -> None:
        self.btn_restart_service.setEnabled(True)
        self.btn_restart_service.setText("Restart Daemon")
        self._refresh_service_status()

    def _refresh_service_status(self) -> None:
        # Cache for 30 s so window activation does not spawn systemctl each time.
        now = time.monotonic()
        cache = getattr(self, "_service_status_cache", None)
        if cache is not None and (now - cache[0]) < 30.0:
            is_active = cache[1]
        else:
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", "gigamate.service"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
                is_active = res.stdout.strip() == "active"
            except Exception:
                is_active = False
            self._service_status_cache = (now, is_active)

        if is_active:
            self.service_status_label.setText("Daemon: ● Active (gigamate.service running)")
            self.service_status_label.setStyleSheet("color: #48bb78; font-size: 13px; font-weight: 600;")
        else:
            self.service_status_label.setText("Daemon: ○ Inactive (service stopped)")
            self.service_status_label.setStyleSheet("color: #e53e3e; font-size: 13px; font-weight: 600;")

    def reload_from_config(self) -> None:
        """Reload and update settings widgets from on-disk config."""
        self.cfg = load_config()

        self.chk_startup_apply.blockSignals(True)
        self.chk_startup_apply.setChecked(self.cfg.get("startup_apply", False))
        self.chk_startup_apply.blockSignals(False)

        self.chk_sync_power.blockSignals(True)
        self.chk_sync_power.setChecked(self.cfg.get("sync_system_power", False))
        self.chk_sync_power.blockSignals(False)

        self.chk_dgpu_auto.blockSignals(True)
        self.chk_dgpu_auto.setChecked(self.cfg.get("dgpu_undervolt_auto", True))
        self.chk_dgpu_auto.blockSignals(False)

        # Refresh the diagnostic chips too (driver/keyboard/battery hotplug).
        self._populate_diag_chips()

        # Defer the (blocking) systemctl probe so window activation stays snappy.
        QTimer.singleShot(0, self._refresh_service_status)
