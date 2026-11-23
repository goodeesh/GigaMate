"""GigaMate Center — First-run setup wizard.

Two perspectives share one dialog:
- **new**: the user has no existing config; every hardware-mutating behaviour
  is opt-in and nothing is applied until chosen.
- **upgrade**: the user already had a 2.0.x config; existing settings are
  preserved and only the new features (battery care, system power sync) are
  offered.

The wizard never nags: both Finish and Skip record ``onboarding_complete``.
"""

import json
import os
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..capabilities import detect_system_capabilities
from ..config import CONFIG_FILE, load as load_config, update_config
from ..hardware import apply_hardware_settings
from ..idle import IDLE_TIMEOUT_STEPS, nearest_idle_step
from ..paths import ICON_PATHS
from ..protocol import COLOUR_MAP
from ..config import resolve_active_profile
from . import icons
from .styles import dialog_stylesheet
from .theme import THEME
from .widgets import M3Card, M3ComboBox, M3Slider, M3Switch


def has_user_config() -> bool:
    """True when a *pre-3.0* config exists (i.e. this is an upgrade, not fresh).

    A config created by 3.0 itself (the CLI or tray running before the first-run
    wizard) must not count as an upgrade. 2.0.x ``save()`` never wrote
    ``onboarding_complete``, whereas 3.0 always does, so the presence of that
    key distinguishes a genuine legacy config from one 3.0 just created.
    """
    try:
        from ..config import _OLD_CONFIG_FILE  # legacy 2.0.x-era path
    except Exception:
        _OLD_CONFIG_FILE = None
    if _OLD_CONFIG_FILE is not None and _OLD_CONFIG_FILE.exists():
        return True
    if not CONFIG_FILE.exists():
        return False
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "onboarding_complete" not in data


def _colour_keys() -> list:
    profile = resolve_active_profile()
    if profile is not None and profile.colour_names:
        return list(profile.colour_names)
    return list(COLOUR_MAP.keys())


def _style_notice(label: QLabel) -> None:
    label.setWordWrap(True)
    label.setStyleSheet(
        f"background-color: {THEME.surface_container}; "
        f"color: {THEME.on_surface_variant}; "
        f"border-radius: {THEME.radius_control}px; "
        f"padding: 10px 14px; font-size: 12px;"
    )


class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to GigaMate")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; background: transparent;"
        )
        layout.addWidget(self.body)

        self._summary_card = M3Card()
        self._summary_layout = QVBoxLayout(self._summary_card)
        self._summary_layout.setContentsMargins(16, 14, 16, 14)
        self._summary_layout.setSpacing(10)
        layout.addWidget(self._summary_card)
        layout.addStretch()

    def _add_summary_row(self, icon_name: str, text: str, tone_color: str) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(16, 16)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(icons.render_icon_pixmap(icon_name, color=tone_color, size=16))
        row.addWidget(icon_lbl)

        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {tone_color}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        row.addWidget(lbl, stretch=1)
        self._summary_layout.addLayout(row)

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
        self.body.setText(intro)

        while self._summary_layout.count():
            item = self._summary_layout.takeAt(0)
            if item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()

        self._add_summary_row(
            "laptop",
            f"Device: {caps.product_name} "
            + ("(Gigabyte laptop)" if caps.is_gigabyte_laptop else "(Generic — fan/power control unavailable)"),
            THEME.success if caps.is_gigabyte_laptop else THEME.on_surface_variant,
        )
        self._add_summary_row(
            "keyboard",
            "Keyboard RGB: detected" if caps.keyboard_detected else "Keyboard RGB: not detected",
            THEME.success if caps.keyboard_detected else THEME.on_surface_muted,
        )
        self._add_summary_row(
            "bolt",
            "ACPI fan/power control: loaded" if caps.acpi_available else "ACPI fan/power control: not available",
            THEME.success if caps.acpi_available else THEME.warning,
        )
        self._add_summary_row(
            "battery",
            "Battery charge limit: supported" if caps.charge_limit_supported else "Battery charge limit: unsupported on this hardware",
            THEME.success if caps.charge_limit_supported else THEME.on_surface_muted,
        )


class _RgbPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self._supported = True
        self.setTitle("Keyboard lighting")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        self.notice = QLabel()
        _style_notice(self.notice)
        layout.addWidget(self.notice)

        card = M3Card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(14)

        self.apply_chk = M3Switch("Apply my keyboard lighting at startup")
        self.apply_chk.toggled.connect(self._sync_child_enabled)
        self.colour_combo = M3ComboBox()
        self.brightness_combo = M3ComboBox()
        for label, val in (("Off", 0), ("Dim", 1), ("Full", 2)):
            self.brightness_combo.addItem(label, val)
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Colour", self.colour_combo)
        form.addRow("Brightness", self.brightness_combo)
        card_lay.addWidget(self.apply_chk)
        card_lay.addLayout(form)
        layout.addWidget(card)
        layout.addStretch()

    def _sync_child_enabled(self) -> None:
        enabled = self._supported and self.apply_chk.isChecked()
        self.colour_combo.setEnabled(enabled)
        self.brightness_combo.setEnabled(enabled)

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        self._supported = w.caps.keyboard_detected and w.caps.keyboard_profile_loaded
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
        self.apply_chk.setEnabled(self._supported)
        self._sync_child_enabled()
        if self._supported:
            self.notice.setText("")
            self.notice.hide()
        else:
            self.notice.setText("No compatible Gigabyte RGB keyboard was detected. You can set this up "
                                "later from the RGB Lighting page in GigaMate Center.")
            self.notice.show()


class _IdlePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self._supported = True
        self.setTitle("Idle backlight")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        self.notice = QLabel()
        _style_notice(self.notice)
        layout.addWidget(self.notice)

        card = M3Card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(14)

        self.enable_chk = M3Switch("Turn the keyboard backlight off when idle")
        self.enable_chk.toggled.connect(self._sync_child_enabled)
        self.timeout_combo = M3ComboBox()
        for sec, label in IDLE_TIMEOUT_STEPS:
            if sec > 0:
                self.timeout_combo.addItem(label, sec)
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("After", self.timeout_combo)
        card_lay.addWidget(self.enable_chk)
        card_lay.addLayout(form)
        layout.addWidget(card)
        layout.addStretch()

    def _sync_child_enabled(self) -> None:
        self.timeout_combo.setEnabled(self._supported and self.enable_chk.isChecked())

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        self._supported = w.caps.keyboard_detected and w.caps.keyboard_profile_loaded
        self.enable_chk.setChecked(bool(cfg.get("idle_off_enabled", False)))
        tidx = self.timeout_combo.findData(
            nearest_idle_step(int(cfg.get("idle_timeout_sec", 60))))
        if tidx >= 0:
            self.timeout_combo.setCurrentIndex(tidx)
        self.enable_chk.setEnabled(self._supported)
        self._sync_child_enabled()
        if self._supported:
            self.notice.hide()
        else:
            self.notice.setText("No compatible RGB keyboard detected; idle backlight control is unavailable.")
            self.notice.show()


class _BatteryPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self._supported = True
        self.setTitle("Battery care")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        self.notice = QLabel()
        _style_notice(self.notice)
        layout.addWidget(self.notice)

        card = M3Card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(14)

        self.enable_chk = M3Switch("Limit charging to protect battery lifespan")
        self.enable_chk.toggled.connect(self._sync_child_enabled)
        self.limit_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=False)
        self.limit_slider.setRange(40, 100)
        self.limit_slider.setSingleStep(5)
        self.limit_label = QLabel("Limit: 80%")
        self.limit_label.setProperty("class", "ValueReadoutPill")
        self.limit_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self.limit_slider.valueChanged.connect(
            lambda v: self.limit_label.setText(f"Limit: {v}%"))
        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(12)
        slider_row.addWidget(self.limit_slider, stretch=1)
        slider_row.addWidget(self.limit_label)
        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Maximum charge", slider_row)
        card_lay.addWidget(self.enable_chk)
        card_lay.addLayout(form)
        layout.addWidget(card)
        layout.addStretch()

    def _sync_child_enabled(self) -> None:
        self.limit_slider.setEnabled(self._supported and self.enable_chk.isChecked())

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        self._supported = w.caps.charge_limit_supported
        self.enable_chk.setChecked(bool(cfg.get("charge_limit_enabled", False)))
        self.limit_slider.setValue(int(cfg.get("charge_limit", 80)))
        self.enable_chk.setEnabled(self._supported)
        self._sync_child_enabled()
        if self._supported:
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
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        self.notice = QLabel()
        _style_notice(self.notice)
        layout.addWidget(self.notice)

        card = M3Card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(18, 16, 18, 16)
        card_lay.setSpacing(12)

        self.sync_chk = M3Switch(
            "Sync the Linux system power profile with the fan profile")
        card_lay.addWidget(self.sync_chk)
        layout.addWidget(card)
        layout.addStretch()

    def initializePage(self) -> None:
        w = self.wizard()
        cfg = w.cfg
        # System power-profile sync only makes sense when a usable model
        # profile exists to map from; experimental (unconfirmed) models are not
        # offered here (they require explicit opt-in).
        supported = bool(w.caps.profile_verified and not w.caps.profile_experimental)
        self.sync_chk.setChecked(bool(cfg.get("sync_system_power", False)))
        self.sync_chk.setEnabled(supported)
        if supported:
            self.notice.setText("When you switch fan profiles, also switch the "
                                "system power profile (power-profiles-daemon/TLP).")
        else:
            self.notice.setText("No verified power profiles on this hardware, so "
                                "system power-profile sync is not available.")
        self.notice.show()


class _FinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("All set")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        card = M3Card()
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(20, 20, 20, 20)
        card_lay.setSpacing(14)

        badge = QLabel()
        badge.setFixedSize(32, 32)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background-color: {THEME.success_container}; "
            f"border: 1px solid {THEME.success_border}; "
            f"border-radius: 16px;"
        )
        badge.setPixmap(icons.render_icon_pixmap("check", color=THEME.success, size=18))
        card_lay.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; background: transparent;"
        )
        card_lay.addWidget(self.body, stretch=1)
        layout.addWidget(card)
        layout.addStretch()

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
        icon_path = ICON_PATHS.get("gigamate")
        if icon_path and Path(icon_path).exists():
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)
            self.setPixmap(QWizard.WizardPixmap.LogoPixmap, app_icon.pixmap(QSize(36, 36)))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setMinimumSize(560, 440)
        self.setStyleSheet(dialog_stylesheet())

        self.p_welcome = _WelcomePage()
        self.p_rgb = _RgbPage()
        self.p_idle = _IdlePage()
        self.p_battery = _BatteryPage()
        self.p_power = _PowerPage()
        self.p_finish = _FinishPage()
        for page in (self.p_welcome, self.p_rgb, self.p_idle,
                     self.p_battery, self.p_power, self.p_finish):
            self.addPage(page)

        for btn_role in (
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
        ):
            btn = self.button(btn_role)
            if btn is not None:
                btn.setProperty("class", "PrimaryButton")
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        for btn_role in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
        ):
            btn = self.button(btn_role)
            if btn is not None:
                btn.setCursor(Qt.CursorShape.PointingHandCursor)

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
            if caps.profile_verified and not caps.profile_experimental:
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
