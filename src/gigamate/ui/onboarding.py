"""GigaMate Center — First-run setup wizard.

Two perspectives share one dialog:
- **new**: the user has no existing config; every hardware-mutating behaviour
  is opt-in and nothing is applied until chosen.
- **upgrade**: the user already had a 2.0.x config; existing settings are
  preserved and only the new features (battery care, system power sync) are
  offered.

The wizard never nags: both Finish and Skip record ``onboarding_complete``.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..capabilities import detect_system_capabilities
from ..config import CONFIG_FILE, load as load_config, update_config
from ..hardware import apply_hardware_settings
from ..idle import IDLE_TIMEOUT_STEPS, nearest_idle_step
from ..protocol import COLOUR_MAP
from ..config import resolve_active_profile


def has_user_config() -> bool:
    """True when a config already exists (i.e. this is an upgrade, not fresh)."""
    try:
        from ..config import _OLD_CONFIG_FILE  # legacy 2.0.x-era path
    except Exception:
        _OLD_CONFIG_FILE = None
    return CONFIG_FILE.exists() or (_OLD_CONFIG_FILE is not None and _OLD_CONFIG_FILE.exists())


def _colour_keys() -> list:
    profile = resolve_active_profile()
    if profile is not None and profile.colour_names:
        return list(profile.colour_names)
    return list(COLOUR_MAP.keys())


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to GigaMate")
        layout = QVBoxLayout(self)
        self.body = QLabel()
        self.body.setWordWrap(True)
        layout.addWidget(self.body)

    def initializePage(self) -> None:
        w = self.wizard()
        caps = w.caps
        if w.rerun:
            self.setTitle("GigaMate Setup")
            intro = ("Update your GigaMate preferences. Existing settings are kept "
                     "and everything below is optional.")
        elif w.mode == "upgrade":
            self.setTitle("GigaMate 3.0 — what's new")
            intro = ("Your existing settings have been kept. GigaMate 3.0 adds "
                     "GigaMate Center (this app), battery charge limits, and "
                     "cleaner suspend/resume. Everything below is optional.")
        else:
            intro = ("This is a one-time setup. Nothing on your hardware is "
                     "changed until you choose it here.")
        summary = [
            f"Device: {caps.product_name}",
            "Gigabyte laptop: yes" if caps.is_gigabyte_laptop
            else "Gigabyte laptop: no — fan/power control is unavailable",
            "Keyboard RGB: detected" if caps.keyboard_detected
            else "Keyboard RGB: not detected",
            "ACPI driver (gigamate_acpi): loaded" if caps.acpi_driver_loaded
            else "ACPI driver (gigamate_acpi): not loaded",
            "Battery charge limit: supported" if caps.charge_limit_supported
            else "Battery charge limit: unsupported on this hardware",
        ]
        self.body.setText(intro + "\n\n" + "\n".join(summary))


class _RgbPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Keyboard lighting")
        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.apply_chk = QCheckBox("Apply my keyboard lighting at startup")
        self.colour_combo = QComboBox()
        self.brightness_combo = QComboBox()
        for label, val in (("Off", 0), ("Dim", 1), ("Full", 2)):
            self.brightness_combo.addItem(label, val)
        form = QFormLayout()
        form.addRow("Colour", self.colour_combo)
        form.addRow("Brightness", self.brightness_combo)
        layout.addWidget(self.apply_chk)
        layout.addLayout(form)

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        supported = w.caps.keyboard_detected and w.caps.keyboard_profile_loaded
        self.colour_combo.clear()
        for key in _colour_keys():
            self.colour_combo.addItem(key.replace("_", " ").title(), key)
        # Prefill from existing config.
        cur_col = cfg.get("colour", "light_purple")
        idx = self.colour_combo.findData(cur_col)
        if idx >= 0:
            self.colour_combo.setCurrentIndex(idx)
        bidx = self.brightness_combo.findData(int(cfg.get("brightness", 2)))
        if bidx >= 0:
            self.brightness_combo.setCurrentIndex(bidx)
        self.apply_chk.setChecked(bool(cfg.get("startup_apply", False)))
        for widget in (self.apply_chk, self.colour_combo, self.brightness_combo):
            widget.setEnabled(supported)
        if supported:
            self.notice.setText("")
            self.notice.hide()
        else:
            self.notice.setText("No compatible Gigabyte RGB keyboard was detected. "
                                "This step can be configured later in Settings.")
            self.notice.show()


class _IdlePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Idle backlight")
        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.enable_chk = QCheckBox("Turn the keyboard backlight off when idle")
        self.timeout_combo = QComboBox()
        for sec, label in IDLE_TIMEOUT_STEPS:
            if sec > 0:
                self.timeout_combo.addItem(label, sec)
        form = QFormLayout()
        form.addRow("After", self.timeout_combo)
        layout.addWidget(self.enable_chk)
        layout.addLayout(form)

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        supported = w.caps.keyboard_detected and w.caps.keyboard_profile_loaded
        self.enable_chk.setChecked(bool(cfg.get("idle_off_enabled", False)))
        tidx = self.timeout_combo.findData(
            nearest_idle_step(int(cfg.get("idle_timeout_sec", 60))))
        if tidx >= 0:
            self.timeout_combo.setCurrentIndex(tidx)
        self.enable_chk.setEnabled(supported)
        self.timeout_combo.setEnabled(supported)
        if supported:
            self.notice.hide()
        else:
            self.notice.setText("No compatible RGB keyboard detected; idle "
                                "backlight control is unavailable.")
            self.notice.show()


class _BatteryPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Battery care")
        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.enable_chk = QCheckBox("Limit charging to protect battery lifespan")
        self.limit_slider = QSlider(Qt.Orientation.Horizontal)
        self.limit_slider.setRange(40, 100)
        self.limit_slider.setSingleStep(5)
        self.limit_label = QLabel("Limit: 80%")
        self.limit_slider.valueChanged.connect(
            lambda v: self.limit_label.setText(f"Limit: {v}%"))
        form = QFormLayout()
        form.addRow("Maximum charge", self.limit_slider)
        layout.addWidget(self.enable_chk)
        layout.addWidget(self.limit_label)
        layout.addLayout(form)

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        supported = w.caps.charge_limit_supported
        self.enable_chk.setChecked(bool(cfg.get("charge_limit_enabled", False)))
        self.limit_slider.setValue(int(cfg.get("charge_limit", 80)))
        self.enable_chk.setEnabled(supported)
        self.limit_slider.setEnabled(supported)
        if supported:
            self.notice.setText("Capping charge at 80% significantly reduces "
                                "lithium-ion wear.")
        elif not w.caps.battery_present:
            self.notice.setText("No battery detected (running on AC power); "
                                "charge limiting does not apply.")
        else:
            self.notice.setText("Charge limiting is not supported by this laptop's "
                                "firmware/EC. Battery health monitoring still works.")
        self.notice.show()


class _PowerPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("System power")
        layout = QVBoxLayout(self)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.sync_chk = QCheckBox(
            "Sync the Linux system power profile with the fan profile")
        layout.addWidget(self.sync_chk)

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        supported = w.caps.acpi_available
        self.sync_chk.setChecked(bool(cfg.get("sync_system_power", False)))
        self.sync_chk.setEnabled(supported)
        if supported:
            self.notice.setText("When you switch fan profiles, also switch the "
                                "system power profile (power-profiles-daemon/TLP).")
        else:
            self.notice.setText("No ACPI fan/power control on this hardware.")
        self.notice.show()


class _FinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("All set")
        layout = QVBoxLayout(self)
        self.body = QLabel()
        self.body.setWordWrap(True)
        layout.addWidget(self.body)

    def initializePage(self) -> None:
        w = self.wizard()
        if w.mode == "upgrade":
            self.body.setText("Your settings are saved. Open GigaMate Center any "
                              "time, or re-run setup from Settings.")
        else:
            self.body.setText("Setup complete. You can change any of this later "
                              "from GigaMate Center → Settings.")


class OnboardingWizard(QWizard):
    """First-run setup for new users and 2.0.x upgraders."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cfg = load_config()
        self.caps = detect_system_capabilities()
        self.mode = "upgrade" if has_user_config() else "new"
        # A deliberate re-run (already onboarded) gets neutral copy, not "what's new".
        self.rerun = bool(self.cfg.get("onboarding_complete", False))

        self.setWindowTitle("GigaMate Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        self.p_welcome = _WelcomePage()
        self.p_rgb = _RgbPage()
        self.p_idle = _IdlePage()
        self.p_battery = _BatteryPage()
        self.p_power = _PowerPage()
        self.p_finish = _FinishPage()
        for page in (self.p_welcome, self.p_rgb, self.p_idle,
                     self.p_battery, self.p_power, self.p_finish):
            self.addPage(page)

    # -- persistence -------------------------------------------------------
    def _apply_choices(self, complete: bool) -> None:
        def _mutate(cfg):
            caps = self.caps
            if caps.keyboard_detected and caps.keyboard_profile_loaded:
                cfg["colour"] = self.p_rgb.colour_combo.currentData() or cfg.get("colour")
                cfg["brightness"] = int(self.p_rgb.brightness_combo.currentData())
                cfg["startup_apply"] = self.p_rgb.apply_chk.isChecked()
                cfg["idle_off_enabled"] = self.p_idle.enable_chk.isChecked()
                cfg["idle_timeout_sec"] = int(self.p_idle.timeout_combo.currentData())
            if caps.charge_limit_supported:
                cfg["charge_limit_enabled"] = self.p_battery.enable_chk.isChecked()
                cfg["charge_limit"] = int(self.p_battery.limit_slider.value())
            if caps.acpi_available:
                cfg["sync_system_power"] = self.p_power.sync_chk.isChecked()
                if cfg["sync_system_power"] and cfg.get("acpi_profile") is None:
                    # Sync needs a fan profile to map from; Balanced by default.
                    cfg["acpi_profile"] = 1
            cfg["onboarding_complete"] = True
            return cfg

        update_config(_mutate)

    def accept(self) -> None:
        was_battery_enabled = bool(self.cfg.get("charge_limit_enabled", False))
        self._apply_choices(complete=True)
        results = {}
        try:
            results = apply_hardware_settings() or {}
        except Exception:
            results = {}

        # Disabling the cap must actually uncap the EC (otherwise the setting
        # says "off" while the battery stays limited).
        if (was_battery_enabled and self.caps.charge_limit_supported
                and not self.p_battery.enable_chk.isChecked()):
            try:
                from ..battery import get_battery_manager

                if get_battery_manager().set_charge_limit(100):
                    results["battery"] = True
            except Exception:
                pass

        # Surface a requested-but-failed apply instead of silently ignoring it.
        problems = []
        if (self.caps.keyboard_detected and self.caps.keyboard_profile_loaded
                and self.p_rgb.apply_chk.isChecked() and not results.get("keyboard")):
            problems.append("keyboard lighting")
        if self.caps.charge_limit_supported and self.p_battery.enable_chk.isChecked() \
                and not results.get("battery"):
            problems.append("battery charge limit")
        if problems:
            QMessageBox.warning(
                self, "Some settings could not be applied",
                "GigaMate could not apply: " + ", ".join(problems) +
                ".\nYou can retry later from Settings or the tray.",
            )
        super().accept()

    def reject(self) -> None:
        # Skip / Esc / close: record completion only (change nothing), so it
        # never nags again.
        def _mutate(cfg):
            cfg["onboarding_complete"] = True
            return cfg

        try:
            update_config(_mutate)
        finally:
            super().reject()
