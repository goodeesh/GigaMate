"""GigaMate Center — Settings & System Preferences Page.

Allows configuring:
- Visible Power Profiles (which ACPI profiles appear in Dashboard / Tray)
- Input & Desktop Shortcuts (hardware hotkey capture + KDE/GNOME registration)
- Background user systemd service management (gigamate.service) & driver repair
- System diagnostics, Doctor report & updates
"""

import contextlib
import io
import json
import subprocess
import time
from typing import Dict, Optional

from PyQt6.QtCore import QProcess, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import __version__, dgpu_gate, doctor, hardware, updates
from ...capabilities import detect_system_capabilities, invalidate_capabilities
from ...config import load as load_config, update_config
from ...hotkeys import (
    ACTION_MODE_SWITCH,
    ACTION_OPEN_CENTER,
    capture_hardware_hotkey,
    desktop_shortcut_command,
    register_desktop_shortcut,
)
from ...power_limits import discover_acpi_profiles
from ...updates import build_terminal_repair_command, repair_command
from .. import icons
from ..theme import THEME
from ..widgets import (
    M3Card,
    M3IconHelpButton,
    M3PageHeader,
    M3ResponsiveGrid,
    M3StatusPill,
)

_PROFILE_NAMES = (
    (0, "Quiet"),
    (1, "Balanced"),
    (2, "Performance"),
    (3, "Gaming"),
)

_SHORTCUT_ACTIONS = (
    (ACTION_OPEN_CENTER, "Open GigaMate Center", "Meta+G"),
    (ACTION_MODE_SWITCH, "Cycle Power Profile", "Meta+Shift+M"),
)


class _HotkeyCaptureThread(QThread):
    """Capture a single hardware hotkey press off the UI thread."""

    captured = pyqtSignal(object)

    def __init__(self, timeout_sec: float = 5.0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.timeout_sec = timeout_sec

    def run(self) -> None:
        try:
            spec = capture_hardware_hotkey(timeout_sec=self.timeout_sec)
        except Exception:
            spec = None
        self.captured.emit(spec)


class _UpdateCheckThread(QThread):
    """Check for upstream GigaMate releases off the UI thread."""

    finished_check = pyqtSignal(object)

    def run(self) -> None:
        try:
            res = updates.check_for_updates(force=True)
        except Exception as exc:
            res = {"error": str(exc)}
        self.finished_check.emit(res)


class SettingsPage(QWidget):
    """Settings page for visible profiles, shortcuts, daemon, diagnostics & updates."""

    navigate_requested = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_config()
        self._profile_enable_checks: Dict[int, QCheckBox] = {}
        self._hotkey_labels: Dict[str, QLabel] = {}
        self._record_buttons: Dict[str, QPushButton] = {}
        self._capture_thread: Optional[_HotkeyCaptureThread] = None
        self._update_thread: Optional[_UpdateCheckThread] = None
        self._service_status_cache = None
        self._init_ui()

    @staticmethod
    def _make_divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {THEME.divider}; border: none;")
        return line

    @staticmethod
    def _make_card_header(title: str, help_tooltip: str) -> QHBoxLayout:
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl = QLabel(title)
        lbl.setProperty("class", "CardTitle")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(M3IconHelpButton(help_tooltip))
        return hdr

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(
            M3PageHeader(
                "Settings",
                "Visible profiles, shortcuts, background daemon, diagnostics & updates",
            )
        )

        # ── Card 1: Visible Power Profiles ──
        self.profiles_card = M3Card()
        p_layout = QVBoxLayout(self.profiles_card)
        p_layout.setContentsMargins(20, 18, 20, 18)
        p_layout.setSpacing(10)
        p_layout.addLayout(
            self._make_card_header(
                "Visible Power Profiles",
                "Choose which ACPI power profiles are shown in Dashboard, Tray, and Automation.",
            )
        )
        p_sub = QLabel(
            "Disable profiles you don't use. At least one profile must remain enabled."
        )
        p_sub.setProperty("class", "CardSubtitle")
        p_sub.setWordWrap(True)
        p_layout.addWidget(p_sub)

        checks_row = QHBoxLayout()
        checks_row.setSpacing(16)
        for pid, name in self._all_discovered_profiles():
            chk = QCheckBox(name)
            chk.toggled.connect(lambda checked, p=pid: self._on_profile_enable_toggled(p, checked))
            self._profile_enable_checks[pid] = chk
            checks_row.addWidget(chk)
        checks_row.addStretch()
        p_layout.addLayout(checks_row)
        layout.addWidget(self.profiles_card)
        self._sections: Dict[str, QWidget] = {"visible_profiles": self.profiles_card}

        # ── Card 2: Input & Desktop Shortcuts ──
        shortcuts_card = M3Card()
        sh_layout = QVBoxLayout(shortcuts_card)
        sh_layout.setContentsMargins(20, 18, 20, 18)
        sh_layout.setSpacing(10)
        sh_layout.addLayout(
            self._make_card_header(
                "Input & Desktop Shortcuts",
                "Bind hardware keys or register desktop environment shortcuts.",
            )
        )

        for action, title, default_key in _SHORTCUT_ACTIONS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 4)
            row.setSpacing(10)

            info_col = QVBoxLayout()
            info_col.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(
                f"color: {THEME.on_surface}; font-size: 13px; font-weight: 500; background: transparent;"
            )
            info_col.addWidget(title_lbl)

            binding_lbl = QLabel(self._format_hotkey_binding(action, default_key))
            binding_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
            )
            self._hotkey_labels[action] = binding_lbl
            info_col.addWidget(binding_lbl)
            row.addLayout(info_col)
            row.addStretch()

            rec_btn = QPushButton("Record Hardware Key")
            rec_btn.setProperty("class", "TonalButton")
            rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rec_btn.clicked.connect(lambda _checked=False, a=action: self._start_hotkey_capture(a))
            self._record_buttons[action] = rec_btn
            row.addWidget(rec_btn)

            rst_btn = QPushButton("Reset")
            rst_btn.setProperty("class", "OutlinedButton")
            rst_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            rst_btn.clicked.connect(
                lambda _checked=False, a=action, dk=default_key: self._reset_hotkey(a, dk)
            )
            row.addWidget(rst_btn)

            sh_layout.addLayout(row)

        sh_layout.addWidget(self._make_divider())

        helper_title = QLabel("Desktop Shortcut Helper")
        helper_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        sh_layout.addWidget(helper_title)

        for action, title, default_key in _SHORTCUT_ACTIONS:
            cmd = desktop_shortcut_command(action)
            cmd_row = QHBoxLayout()
            cmd_row.setSpacing(8)
            cmd_lbl = QLabel(f"{title}: <code>{cmd}</code>")
            cmd_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
            )
            cmd_row.addWidget(cmd_lbl)
            cmd_row.addStretch()

            copy_btn = QPushButton("Copy")
            copy_btn.setProperty("class", "OutlinedButton")
            copy_btn.setFixedWidth(72)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(lambda _checked=False, c=cmd: self._copy_command(c))
            cmd_row.addWidget(copy_btn)

            reg_btn = QPushButton(f"Register ({default_key})")
            reg_btn.setProperty("class", "TonalButton")
            reg_btn.setFixedWidth(188)
            reg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            reg_btn.clicked.connect(
                lambda _checked=False, a=action, dk=default_key: self._register_desktop_shortcut(a, dk)
            )
            cmd_row.addWidget(reg_btn)
            sh_layout.addLayout(cmd_row)

        self.shortcut_status_lbl = QLabel("")
        self.shortcut_status_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        sh_layout.addWidget(self.shortcut_status_lbl)
        layout.addWidget(shortcuts_card)

        # ── Card 3: Background Daemon & Driver ──
        service_card = M3Card()
        svc_layout = QVBoxLayout(service_card)
        svc_layout.setContentsMargins(20, 18, 20, 18)
        svc_layout.setSpacing(14)

        svc_hdr = QHBoxLayout()
        svc_hdr.setSpacing(12)
        svc_title = QLabel("Background Daemon & Driver")
        svc_title.setProperty("class", "CardTitle")
        svc_hdr.addWidget(svc_title)
        svc_hdr.addStretch()

        self.service_status_label = M3StatusPill("Daemon: Checking...", tone="neutral")
        svc_hdr.addWidget(self.service_status_label)
        svc_layout.addLayout(svc_hdr)

        svc_sub = QLabel(
            "GigaMate runs a lightweight user daemon (gigamate.service) for hardware hotkeys and tray integration."
        )
        svc_sub.setProperty("class", "CardSubtitle")
        svc_sub.setWordWrap(True)
        svc_layout.addWidget(svc_sub)

        svc_row = QHBoxLayout()
        svc_row.setSpacing(10)

        self.btn_restart_service = QPushButton("Restart Daemon")
        self.btn_restart_service.setIcon(icons.make_icon("refresh", color=THEME.on_surface, size=15))
        self.btn_restart_service.setIconSize(QSize(15, 15))
        self.btn_restart_service.setMinimumHeight(38)
        self.btn_restart_service.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart_service.clicked.connect(self._on_restart_service_clicked)
        svc_row.addWidget(self.btn_restart_service, stretch=1)

        self.btn_repair_driver = QPushButton("Repair Driver")
        self.btn_repair_driver.setIcon(icons.make_icon("wrench", color=THEME.on_surface, size=15))
        self.btn_repair_driver.setIconSize(QSize(15, 15))
        self.btn_repair_driver.setMinimumHeight(38)
        self.btn_repair_driver.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_repair_driver.clicked.connect(self._on_repair_driver_clicked)
        svc_row.addWidget(self.btn_repair_driver, stretch=1)

        self.btn_setup = QPushButton("Run Setup Again")
        self.btn_setup.setIcon(icons.make_icon("sparkles", color=THEME.on_surface, size=15))
        self.btn_setup.setIconSize(QSize(15, 15))
        self.btn_setup.setMinimumHeight(38)
        self.btn_setup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_setup.clicked.connect(self._on_run_setup_clicked)
        svc_row.addWidget(self.btn_setup, stretch=1)

        svc_layout.addLayout(svc_row)
        layout.addWidget(service_card)

        # ── Card 4: Hardware Diagnostics & Updates ──
        diag_card = M3Card()
        d_layout = QVBoxLayout(diag_card)
        d_layout.setContentsMargins(20, 18, 20, 18)
        d_layout.setSpacing(14)

        d_hdr = QHBoxLayout()
        d_hdr.setSpacing(8)
        d_title = QLabel("Hardware Diagnostics & Updates")
        d_title.setProperty("class", "CardTitle")
        d_hdr.addWidget(d_title)
        d_hdr.addStretch()

        self.btn_copy_doctor = QPushButton("Copy Doctor Report")
        self.btn_copy_doctor.setProperty("class", "OutlinedButton")
        self.btn_copy_doctor.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_doctor.clicked.connect(self._on_copy_doctor_clicked)
        d_hdr.addWidget(self.btn_copy_doctor)
        d_layout.addLayout(d_hdr)

        self._diag_grid = M3ResponsiveGrid(
            wide_cols=3,
            narrow_cols=2,
            breakpoint=620,
            h_spacing=12,
            v_spacing=12,
        )
        d_layout.addWidget(self._diag_grid)

        d_layout.addWidget(self._make_divider())

        upd_row = QHBoxLayout()
        upd_row.setSpacing(10)
        ver_lbl = QLabel(f"GigaMate v{__version__}")
        ver_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        upd_row.addWidget(ver_lbl)

        self.update_status_lbl = QLabel("")
        self.update_status_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        upd_row.addWidget(self.update_status_lbl)
        upd_row.addStretch()

        self.btn_check_updates = QPushButton("Check for Updates")
        self.btn_check_updates.setProperty("class", "TonalButton")
        self.btn_check_updates.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_updates.clicked.connect(self._on_check_updates_clicked)
        upd_row.addWidget(self.btn_check_updates)
        d_layout.addLayout(upd_row)

        layout.addWidget(diag_card)
        layout.addStretch()

        self.reload_from_config()

    def _all_discovered_profiles(self) -> list[tuple[int, str]]:
        try:
            discovered = discover_acpi_profiles()
        except Exception:
            discovered = {}
        if discovered:
            return [
                (int(pid), (meta or {}).get("name", f"Profile {pid}"))
                for pid, meta in sorted(discovered.items())
            ]
        return list(_PROFILE_NAMES)

    def _sync_profile_enable_checks(self) -> None:
        disabled = {int(x) for x in (self.cfg.get("disabled_profiles") or [])}
        enabled_pids = [pid for pid in self._profile_enable_checks if pid not in disabled]
        for pid, chk in self._profile_enable_checks.items():
            is_enabled = pid not in disabled
            chk.blockSignals(True)
            chk.setChecked(is_enabled)
            is_last = is_enabled and len(enabled_pids) <= 1
            chk.setEnabled(not is_last)
            chk.setToolTip("At least one profile must remain enabled" if is_last else "")
            chk.blockSignals(False)

    def _on_profile_enable_toggled(self, pid: int, checked: bool) -> None:
        disabled = {int(x) for x in (self.cfg.get("disabled_profiles") or [])}
        if checked:
            disabled.discard(pid)
        else:
            all_pids = set(self._profile_enable_checks.keys())
            if len(all_pids - disabled) <= 1:
                self._sync_profile_enable_checks()
                return
            disabled.add(pid)

        new_disabled = sorted(disabled)
        self.cfg["disabled_profiles"] = new_disabled
        update_config(lambda c: {**c, "disabled_profiles": new_disabled})
        self._sync_profile_enable_checks()

        # If active profile was just disabled, fall back to Balanced (1) or first enabled
        active_pid = int(self.cfg.get("acpi_profile", 1))
        if active_pid in disabled:
            enabled_pids = [p for p in self._profile_enable_checks if p not in disabled]
            fallback = 1 if 1 in enabled_pids else (enabled_pids[0] if enabled_pids else 1)
            try:
                hardware.apply_profile(fallback)
            except Exception:
                pass

    def _format_hotkey_binding(self, action: str, default_key: str) -> str:
        hotkeys = self.cfg.get("custom_hotkeys") or {}
        spec = hotkeys.get(action)
        if isinstance(spec, dict) and spec.get("key_name"):
            return f"Custom: {spec['key_name']}"
        return f"Default: {default_key}"

    def _start_hotkey_capture(self, action: str) -> None:
        if self._capture_thread is not None and self._capture_thread.isRunning():
            return
        btn = self._record_buttons.get(action)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Press key…")
        self._capture_thread = _HotkeyCaptureThread(timeout_sec=5.0, parent=self)
        self._capture_thread.captured.connect(lambda spec, a=action: self._on_hotkey_captured(a, spec))
        self._capture_thread.start()

    def _on_hotkey_captured(self, action: str, spec: Optional[dict]) -> None:
        btn = self._record_buttons.get(action)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText("Record Hardware Key")
        if spec:
            hotkeys = dict(self.cfg.get("custom_hotkeys") or {})
            hotkeys[action] = spec
            self.cfg["custom_hotkeys"] = hotkeys
            update_config(lambda c: {**c, "custom_hotkeys": {**(c.get("custom_hotkeys") or {}), action: spec}})
            lbl = self._hotkey_labels.get(action)
            if lbl is not None:
                lbl.setText(self._format_hotkey_binding(action, ""))

    def _reset_hotkey(self, action: str, default_key: str) -> None:
        hotkeys = dict(self.cfg.get("custom_hotkeys") or {})
        hotkeys.pop(action, None)
        self.cfg["custom_hotkeys"] = hotkeys
        update_config(lambda c: {
            **c,
            "custom_hotkeys": {k: v for k, v in (c.get("custom_hotkeys") or {}).items() if k != action},
        })
        lbl = self._hotkey_labels.get(action)
        if lbl is not None:
            lbl.setText(self._format_hotkey_binding(action, default_key))

    def _copy_command(self, cmd: str) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(cmd)
        self.shortcut_status_lbl.setText(f"Copied: {cmd}")

    def _register_desktop_shortcut(self, action: str = ACTION_OPEN_CENTER, default_key: str = "Meta+G") -> None:
        ok, msg = register_desktop_shortcut(action, default_key)
        self.shortcut_status_lbl.setText(msg if msg else ("Shortcut registered." if ok else "Registration failed."))

    def _make_chip(self, title: str, val_text: str, icon_name: str, sub_text: str = "") -> QFrame:
        chip = QFrame()
        chip.setProperty("class", "StatusChip")
        c_lay = QVBoxLayout(chip)
        c_lay.setContentsMargins(16, 12, 16, 12)
        c_lay.setSpacing(3)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(15, 15)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(
            icons.render_icon_pixmap(icon_name, color=THEME.on_surface_variant, size=15)
        )
        hdr.addWidget(icon_lbl)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 10px; font-weight: 700; "
            f"text-transform: uppercase; letter-spacing: 0.5px; background: transparent;"
        )
        hdr.addWidget(t_lbl)
        hdr.addStretch()
        c_lay.addLayout(hdr)

        val_lbl = QLabel(val_text)
        val_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 600; "
            f"margin-top: 2px; background: transparent;"
        )
        c_lay.addWidget(val_lbl)

        if sub_text:
            sub_lbl = QLabel(sub_text)
            if "Active" in sub_text or "Supported" in sub_text or "Mapped" in sub_text:
                sub_color = THEME.success
                sub_weight = 600
            elif "Required" in sub_text or "Uncalibrated" in sub_text:
                sub_color = THEME.warning
                sub_weight = 600
            else:
                sub_color = THEME.on_surface_muted
                sub_weight = 500
            sub_lbl.setStyleSheet(
                f"color: {sub_color}; font-size: 11px; font-weight: {sub_weight}; "
                f"background: transparent;"
            )
            c_lay.addWidget(sub_lbl)

        return chip

    def _populate_diag_chips(self) -> None:
        """(Re)build the 5 hardware diagnostic chips from a fresh capability probe."""
        self._diag_grid.clear()
        caps = detect_system_capabilities()

        # 1. Device Model
        model_sub = "(Supported)" if caps.is_gigabyte_laptop else "(Generic)"
        self._diag_grid.addWidget(
            self._make_chip("Device Model", caps.product_name, "laptop", model_sub)
        )

        # 2. Keyboard HID
        if caps.keyboard_detected and caps.keyboard_vid_pid:
            vid, pid = caps.keyboard_vid_pid
            kbd_val = f"0x{vid:04X}:0x{pid:04X}"
            kbd_sub = "(HID LampArray)" if caps.keyboard_lamparray_supported else "(Unsupported)"
        else:
            kbd_val = "Not Detected"
            kbd_sub = "(No RGB Controller)"
        self._diag_grid.addWidget(
            self._make_chip("Keyboard HID", kbd_val, "keyboard", kbd_sub)
        )

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
        self._diag_grid.addWidget(
            self._make_chip("ACPI Driver", acpi_val, "bolt", acpi_sub)
        )

        # 4. Discrete GPU
        if dgpu_gate.is_supported():
            dgpu_mode = str(self.cfg.get("dgpu_power_mode", "optimus"))
            dgpu_val = dgpu_mode.capitalize()
            dgpu_sub = "(Gate Supported)"
        else:
            dgpu_val = "Standard"
            dgpu_sub = "(No ACPI Gate)"
        self._diag_grid.addWidget(
            self._make_chip("Discrete GPU", dgpu_val, "gpu", dgpu_sub)
        )

        # 5. Charge Limiter
        if caps.charge_limit_supported:
            bat_val = "Supported"
            bat_sub = "(EC / sysfs)"
        elif caps.battery_present:
            bat_val = "Unsupported"
            bat_sub = "(Firmware Limited)"
        else:
            bat_val = "No Battery"
            bat_sub = "(Continuous AC)"
        self._diag_grid.addWidget(
            self._make_chip("Charge Limiter", bat_val, "battery", bat_sub)
        )

    def _on_copy_doctor_clicked(self) -> None:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = doctor.run_doctor()
            report = buf.getvalue().strip() or f"gigamate doctor exited with code {rc}"
        except Exception as exc:
            report = f"gigamate doctor error: {exc}"
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(report)
        self.update_status_lbl.setText("Doctor report copied to clipboard.")

    def _on_check_updates_clicked(self) -> None:
        if self._update_thread is not None and self._update_thread.isRunning():
            return
        self.btn_check_updates.setEnabled(False)
        self.update_status_lbl.setText("Checking for updates…")
        self._update_thread = _UpdateCheckThread(parent=self)
        self._update_thread.finished_check.connect(self._on_update_check_finished)
        self._update_thread.start()

    def _on_update_check_finished(self, res: object) -> None:
        self.btn_check_updates.setEnabled(True)
        if not isinstance(res, dict):
            self.update_status_lbl.setText("Update check failed.")
            return
        if not res.get("reachable", True):
            self.update_status_lbl.setText("Offline — could not check for updates.")
            return
        if res.get("error"):
            self.update_status_lbl.setText(f"Update check failed: {res['error']}")
            return
        if res.get("update_available"):
            latest = res.get("latest_version") or res.get("latest") or "new version"
            self.update_status_lbl.setText(f"Update available: {latest}")
        else:
            self.update_status_lbl.setText(f"Up to date (v{__version__})")

    def _on_run_setup_clicked(self) -> None:
        from ..onboarding import OnboardingWizard

        OnboardingWizard(self).exec()
        self.reload_from_config()

    def _on_restart_service_clicked(self) -> None:
        self.btn_restart_service.setEnabled(False)
        self.btn_restart_service.setCursor(Qt.CursorShape.ArrowCursor)
        self.btn_restart_service.setText("Restarting…")
        try:
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
        self.btn_restart_service.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_restart_service.setText("Restart Daemon")
        self._service_status_cache = None
        self._refresh_service_status()

    def _on_repair_driver_clicked(self) -> None:
        """Rebuild + reload the ACPI kernel driver (privileged), then restart the tray."""
        cmd = repair_command()
        if cmd:
            self.btn_repair_driver.setEnabled(False)
            self.btn_repair_driver.setCursor(Qt.CursorShape.ArrowCursor)
            self.btn_repair_driver.setText("Repairing…")
            self._repair_output = []
            proc = QProcess(self)
            self._repair_proc = proc
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.readyReadStandardOutput.connect(self._on_repair_read)
            proc.finished.connect(self._on_repair_finished)
            proc.start(cmd[0], cmd[1:])
            return
        term_cmd = build_terminal_repair_command()
        if term_cmd:
            try:
                subprocess.Popen(term_cmd, start_new_session=True)
                self._show_repair_info(
                    "Repair running in terminal",
                    "The ACPI driver repair is running in a terminal window (sudo "
                    "will ask for your password there). Restart the daemon afterwards "
                    "if the driver does not appear loaded.",
                )
                return
            except Exception:
                pass
        self._show_repair_info(
            "Manual repair",
            "Run the following in a terminal (sudo will ask for your password):\n\n"
            "  sudo dkms install gigamate_acpi/<version> -k $(uname -r) --force\n"
            "  sudo modprobe gigamate_acpi",
        )

    def _on_repair_read(self) -> None:
        data = bytes(self._repair_proc.readAll()).decode("utf-8", "replace")
        self._repair_output.append(data)

    def _on_repair_finished(self, code: int, _status) -> None:
        out = "".join(getattr(self, "_repair_output", []))
        ok = False
        detail = out.strip()
        try:
            result = json.loads(detail.splitlines()[-1]) if detail else {}
            ok = bool(result.get("ok"))
            if not ok and result.get("error"):
                detail = result["error"]
        except Exception:
            ok = code == 0
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "--no-block", "gigamate.service"],
                check=False,
                timeout=5,
            )
        except Exception:
            pass
        invalidate_capabilities()
        self.reload_from_config()
        self.btn_repair_driver.setEnabled(True)
        self.btn_repair_driver.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_repair_driver.setText("Repair Driver")
        if ok:
            self._show_repair_info(
                "Repair complete", "The ACPI kernel driver was rebuilt and reloaded."
            )
        else:
            self._show_repair_info("Repair incomplete", detail or "See the output above.")

    def _show_repair_info(self, title: str, text: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _update_repair_button_state(self) -> None:
        try:
            caps = detect_system_capabilities()
            broken = bool(caps.is_gigabyte_laptop and not caps.acpi_driver_loaded)
        except Exception:
            broken = False
        self.btn_repair_driver.setProperty("class", "TonalButton" if broken else "")
        self.btn_repair_driver.setIcon(
            icons.make_icon(
                "wrench",
                color=THEME.on_primary_container if broken else THEME.on_surface,
                size=15,
            )
        )
        style = self.btn_repair_driver.style()
        if style is not None:
            style.unpolish(self.btn_repair_driver)
            style.polish(self.btn_repair_driver)
        self.btn_repair_driver.setToolTip(
            "The ACPI kernel driver (gigamate_acpi) is not loaded — rebuild and "
            "reload it for the running kernel."
            if broken
            else "Rebuild and reload the ACPI kernel driver (gigamate_acpi) for the running kernel."
        )

    def _refresh_service_status(self) -> None:
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
            self.service_status_label.setText("Daemon Active · gigamate.service")
            self.service_status_label.set_tone("success")
        else:
            self.service_status_label.setText("Daemon Inactive · service stopped")
            self.service_status_label.set_tone("error")

    def reload_from_config(self) -> None:
        """Reload and update settings widgets from on-disk config."""
        self.cfg = load_config()
        self._sync_profile_enable_checks()
        for action, _title, default_key in _SHORTCUT_ACTIONS:
            lbl = self._hotkey_labels.get(action)
            if lbl is not None:
                lbl.setText(self._format_hotkey_binding(action, default_key))
        self._populate_diag_chips()
        self._update_repair_button_state()
        QTimer.singleShot(0, self._refresh_service_status)

    def resolve_section(self, key: str) -> Optional[QWidget]:
        """Return the target card widget for a deep-link section key."""
        return self._sections.get(key)
