"""GigaMate Center — Keyboard Studio (24-bit HID LampArray RGB & Brightness)."""

from __future__ import annotations

import re
from typing import Dict, Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...capabilities import detect_system_capabilities
from ...config import (
    effective_brightness_pct,
    effective_rgb_hex,
    is_rgb_enabled,
    load as load_config,
    pct_to_legacy_brightness,
    update_config,
)
from ...idle import IDLE_STEP_OFF, IDLE_TIMEOUT_STEPS, nearest_idle_step
from ...lamparray import diagnose_lamparray
from ...protocol import (
    get_keyboard,
    set_off,
    set_rgb,
)
from .. import icons
from ..theme import THEME
from ..widgets import (
    KeyboardDeckWidget,
    LinkPillButton,
    M3Banner,
    M3Card,
    M3PageHeader,
    M3SegmentedBar,
    M3Slider,
)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

_QUICK_SWATCHES = [
    "#FFFFFF",
    "#14B8A6",
    "#38BDF8",
    "#3B82F6",
    "#8B5CF6",
    "#FF6B2B",
    "#F59E0B",
    "#EF4444",
]

_BRIGHTNESS_PRESETS = [15, 40, 75, 100]


class RgbColorPad(QWidget):
    """2D Hue × Saturation colour pad (Value fixed at 255)."""

    colorChanged = pyqtSignal(str)
    colorCommitted = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hue: float = 0.72  # 0.0 .. 1.0
        self._sat: float = 0.40  # 0.0 .. 1.0
        self._gradient_img: Optional[QImage] = None
        self.setMinimumHeight(164)
        self.setMaximumHeight(196)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def current_hex(self) -> str:
        c = QColor.fromHsvF(self._hue, self._sat, 1.0)
        return c.name().lower()

    def set_hex(self, hex_str: str) -> None:
        c = QColor(hex_str)
        if not c.isValid():
            return
        h, s, _, _ = c.getHsvF()
        if h >= 0.0:
            self._hue = min(1.0, max(0.0, h))
        self._sat = min(1.0, max(0.0, s))
        self.update()

    def _rebuild_gradient(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            self._gradient_img = None
            return
        img = QImage(width, height, QImage.Format.Format_RGB32)
        for y in range(height):
            sat = 1.0 - (y / max(1, height - 1))
            for x in range(width):
                hue = x / max(1, width - 1)
                if hue >= 1.0:
                    hue = 0.9999
                img.setPixelColor(x, y, QColor.fromHsvF(hue, sat, 1.0))
        self._gradient_img = img

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild_gradient(self.width(), self.height())

    def paintEvent(self, event) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if self._gradient_img is None or self._gradient_img.size() != QSize(w, h):
            self._rebuild_gradient(w, h)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 12.0, 12.0)
        painter.setClipPath(path)

        if self._gradient_img is not None:
            painter.drawImage(0, 0, self._gradient_img)

        painter.setClipping(False)
        painter.setPen(QPen(QColor(THEME.outline_variant), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 12.0, 12.0)

        hx = min(max(9.0, self._hue * max(1, w - 1)), max(9.0, w - 9.0))
        hy = min(max(9.0, (1.0 - self._sat) * max(1, h - 1)), max(9.0, h - 9.0))
        center = QPointF(hx, hy)

        painter.setBrush(QColor(self.current_hex()))
        painter.setPen(QPen(QColor("#141210"), 2.0))
        painter.drawEllipse(center, 8.0, 8.0)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
        painter.drawEllipse(center, 6.0, 6.0)
        painter.end()

    def _pick_at(self, pos: QPointF) -> str:
        w = max(1, self.width() - 1)
        h = max(1, self.height() - 1)
        x = min(float(w), max(0.0, pos.x()))
        y = min(float(h), max(0.0, pos.y()))
        self._hue = min(0.9999, x / w)
        self._sat = 1.0 - (y / h)
        self.update()
        return self.current_hex()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.colorChanged.emit(self._pick_at(event.position()))

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.colorChanged.emit(self._pick_at(event.position()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.colorCommitted.emit(self._pick_at(event.position()))


class RgbPage(QWidget):
    """Keyboard Studio — 24-bit HID LampArray colour, brightness & live deck preview."""

    navigate_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cfg = load_config()
        self._full_rgb: bool = False
        self._kbd_state: str = "supported"
        self._accent_hex: str = THEME.primary
        self.swatch_buttons: Dict[str, QPushButton] = {}
        self._brightness_preset_buttons: Dict[int, QPushButton] = {}
        self._brightness_debounce = QTimer(self)
        self._brightness_debounce.setSingleShot(True)
        self._brightness_debounce.setInterval(45)
        self._brightness_debounce.timeout.connect(self._on_brightness_debounce)
        self._color_debounce = QTimer(self)
        self._color_debounce.setSingleShot(True)
        self._color_debounce.setInterval(45)
        self._color_debounce.timeout.connect(self._on_color_debounce)
        self._pending_pad_hex: Optional[str] = None
        self._init_ui()

    def set_accent(self, accent_hex: str) -> None:
        """Accept profile accent updates from MainWindow."""
        self._accent_hex = accent_hex or THEME.primary

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)

        header = M3PageHeader(
            "Keyboard",
            "24-bit HID LampArray colour, brightness & live deck preview",
        )
        trailing = QWidget()
        tr_layout = QHBoxLayout(trailing)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(8)

        self.profile_link_pill = LinkPillButton("Profile Link: Off ↗", "automation:profile_rgb")
        self.profile_link_pill.navigate_requested.connect(self.navigate_requested.emit)
        tr_layout.addWidget(self.profile_link_pill)

        self.idle_link_pill = LinkPillButton("Idle: Off ↗", "automation:idle_sleep")
        self.idle_link_pill.navigate_requested.connect(self.navigate_requested.emit)
        tr_layout.addWidget(self.idle_link_pill)
        self.auto_link_pill = self.idle_link_pill

        self.power_toggle_btn = QPushButton("Turn Off")
        self.power_toggle_btn.setProperty("class", "OutlinedButton")
        self.power_toggle_btn.setIcon(
            icons.make_icon("power_off", color=THEME.on_surface, size=15)
        )
        self.power_toggle_btn.setIconSize(QSize(15, 15))
        self.power_toggle_btn.setMinimumHeight(32)
        self.power_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_toggle_btn.clicked.connect(self._toggle_rgb_power)
        tr_layout.addWidget(self.power_toggle_btn)

        header.set_trailing_widget(trailing)
        layout.addWidget(header)

        # ── Hero Card: Live Keyboard Deck Preview ──
        deck_card = M3Card()
        deck_layout = QVBoxLayout(deck_card)
        deck_layout.setContentsMargins(16, 14, 16, 14)
        deck_layout.setSpacing(8)

        self.keyboard_deck = KeyboardDeckWidget()
        deck_layout.addWidget(self.keyboard_deck)
        layout.addWidget(deck_card)

        # ── Colour & Brightness Studio Card ──
        colour_card = M3Card()
        c_layout = QVBoxLayout(colour_card)
        c_layout.setContentsMargins(18, 16, 18, 16)
        c_layout.setSpacing(12)

        c_header = QHBoxLayout()
        c_title = QLabel("Colour & Brightness Studio")
        c_title.setProperty("class", "CardTitle")
        c_header.addWidget(c_title)
        c_header.addStretch()

        self.active_color_badge = QLabel("")
        self.active_color_badge.setStyleSheet(
            f"font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 12px; "
            f"background-color: {THEME.surface_container_high}; border: 1px solid {THEME.outline_variant}; "
            f"color: {THEME.on_surface_variant};"
        )
        c_header.addWidget(self.active_color_badge)
        c_layout.addLayout(c_header)

        # Profile link / unlink status pill near colour controls
        self.unlink_status_label = QLabel("")
        self.unlink_status_label.setStyleSheet(
            f"color: {THEME.info}; font-size: 11px; font-weight: 600; "
            f"background-color: {THEME.info_container}; border: 1px solid {THEME.info_border}; "
            f"border-radius: 8px; padding: 4px 10px;"
        )
        self.unlink_status_label.setWordWrap(True)
        self.unlink_status_label.setVisible(False)
        c_layout.addWidget(self.unlink_status_label)

        # ── Notice 1: Unsupported Keyboard (non-Gigabyte) ──
        self.kbd_unsupported_notice = M3Banner(
            title="No Compatible RGB Keyboard Detected",
            body=(
                "GigaMate did not detect a supported Gigabyte ITE 829x USB keyboard "
                "controller on this system."
            ),
            tone="info",
            icon="keyboard",
        )
        c_layout.addWidget(self.kbd_unsupported_notice)

        # ── Notice 2: Pre-2025 Legacy Keyboard Detected (no LampArray) ──
        self.legacy_v4_banner = M3Banner(
            title="Pre-2025 Legacy Keyboard Detected",
            body=(
                "GigaMate 5.0+ supports modern 24-bit HID LampArray keyboards. "
                "For older pre-2025 models, install GigaMate v4.5.x from GitHub Releases."
            ),
            tone="warning",
            compact=True,
        )
        self.legacy_v4_banner.setVisible(False)
        c_layout.addWidget(self.legacy_v4_banner)

        # ── Notice 3: LampArray HID Permission Denied ──
        self.lamparray_permission_banner = M3Banner(
            title="24-bit RGB Permission Denied",
            body="",
            tone="warning",
            icon="warning",
        )
        self.lamparray_permission_banner.setVisible(False)
        c_layout.addWidget(self.lamparray_permission_banner)

        # ── Studio Controls Container ──
        self.studio_container = QWidget()
        fr_layout = QVBoxLayout(self.studio_container)
        fr_layout.setContentsMargins(0, 0, 0, 0)
        fr_layout.setSpacing(12)

        self.color_pad = RgbColorPad()
        self.color_pad.colorChanged.connect(self._on_pad_color_changed)
        self.color_pad.colorCommitted.connect(self._on_pad_color_committed)
        fr_layout.addWidget(self.color_pad)

        # Unified controls row: circular quick-color swatches (left) + hex/Pure White (right)
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 2, 0, 2)
        controls_row.setSpacing(8)
        for hex_val in _QUICK_SWATCHES:
            sw = QPushButton()
            sw.setFixedSize(26, 26)
            sw.setCursor(Qt.CursorShape.PointingHandCursor)
            sw.setToolTip(hex_val)
            sw.setStyleSheet(
                f"QPushButton {{ background-color: {hex_val}; border: 2px solid {THEME.outline_variant}; "
                f"border-radius: 13px; min-width: 22px; max-width: 22px; "
                f"min-height: 22px; max-height: 22px; padding: 0px; }}"
                f"QPushButton:hover {{ border: 2px solid {THEME.on_surface}; }}"
            )
            sw.clicked.connect(lambda _, h=hex_val: self._commit_rgb_hex(h))
            controls_row.addWidget(sw)
            self.swatch_buttons[hex_val.lower()] = sw

        controls_row.addStretch(1)

        self.custom_colour_swatch = QPushButton()
        self.custom_colour_swatch.setFixedSize(28, 28)
        self.custom_colour_swatch.setEnabled(False)
        controls_row.addWidget(self.custom_colour_swatch)

        self.hex_input = QLineEdit("#b794f4")
        self.hex_input.setMaxLength(7)
        self.hex_input.setFixedWidth(104)
        self.hex_input.setStyleSheet(
            f"font-family: {THEME.mono_family}; font-size: 13px; padding: 5px 8px; "
            f"background-color: {THEME.surface_container_high}; "
            f"border: 1px solid {THEME.outline_variant}; border-radius: 8px; "
            f"color: {THEME.on_surface};"
        )
        self.hex_input.editingFinished.connect(self._on_hex_input_committed)
        controls_row.addWidget(self.hex_input)

        self.white_btn = QPushButton("Pure White")
        self.white_btn.setProperty("class", "TonalButton")
        self.white_btn.setMinimumHeight(32)
        self.white_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.white_btn.clicked.connect(lambda: self._commit_rgb_hex("#ffffff"))
        controls_row.addWidget(self.white_btn)
        fr_layout.addLayout(controls_row)

        # Brightness slider + readout
        br_row = QHBoxLayout()
        br_row.setContentsMargins(0, 0, 0, 0)
        br_row.setSpacing(10)

        br_lbl = QLabel("Brightness")
        br_lbl.setProperty("class", "SectionLabel")
        br_row.addWidget(br_lbl)

        self.brightness_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=True)
        self.brightness_slider.setRange(5, 100)
        self.brightness_slider.setSingleStep(5)
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        self.brightness_slider.sliderReleased.connect(self._on_brightness_slider_released)
        br_row.addWidget(self.brightness_slider, stretch=1)

        self.brightness_readout = QLabel("100%")
        self.brightness_readout.setProperty("class", "ValueReadoutPill")
        self.brightness_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brightness_readout.setMinimumWidth(56)
        br_row.addWidget(self.brightness_readout)
        fr_layout.addLayout(br_row)

        # 4 quick brightness preset pills
        self._brightness_presets_bar = M3SegmentedBar()
        for pct in _BRIGHTNESS_PRESETS:
            btn = self._brightness_presets_bar.add_segment(
                f"{pct}%", checkable=True, min_height=34
            )
            btn.clicked.connect(lambda _, p=pct: self._set_brightness_pct(p))
            self._brightness_preset_buttons[pct] = btn
        fr_layout.addWidget(self._brightness_presets_bar)

        c_layout.addWidget(self.studio_container)
        self.palette_container = self.studio_container
        self.full_rgb_container = self.studio_container
        self.opts_card = self.studio_container
        layout.addWidget(colour_card)
        layout.addStretch()

        self.reload_from_config()

    def _sync_auto_link_pill(self) -> None:
        """Update header link pills for profile-link and idle-timeout rules."""
        enabled = bool(self.cfg.get("idle_off_enabled", False))
        sec = nearest_idle_step(int(self.cfg.get("idle_timeout_sec", IDLE_STEP_OFF)))
        if not enabled or sec == 0:
            idle_label = "Idle: Off"
        else:
            step_lbl = next(
                (lbl for s, lbl in IDLE_TIMEOUT_STEPS if s == sec),
                "Off",
            )
            idle_label = f"Idle: {step_lbl}"
        self.idle_link_pill.setText(f"{idle_label} ↗")
        if bool(self.cfg.get("profile_rgb_enabled", False)):
            self.profile_link_pill.setText("Profile Link: ON ↗")
        else:
            self.profile_link_pill.setText("Profile Link: Off ↗")

    def _refresh_keyboard_support(self) -> None:
        """Show/hide RGB controls based on detected HID LampArray keyboard."""
        caps = detect_system_capabilities()
        lamp_diag: dict = {}
        if caps.keyboard_detected:
            vid_pid = caps.keyboard_vid_pid
            if not vid_pid:
                dev = get_keyboard()
                if dev is not None:
                    vid_pid = (int(dev.idVendor), int(dev.idProduct))
            if vid_pid:
                try:
                    lamp_diag = diagnose_lamparray(int(vid_pid[0]), int(vid_pid[1]))
                except Exception:
                    lamp_diag = {}

        if not caps.keyboard_detected:
            self._kbd_state = "unsupported"
        elif not caps.keyboard_lamparray_supported or lamp_diag.get("status") in (
            "unsupported_firmware",
            "unsupported_layout",
        ):
            self._kbd_state = "legacy"
        else:
            self._kbd_state = "supported"

        self._full_rgb = self._kbd_state == "supported"

        self.kbd_unsupported_notice.setVisible(self._kbd_state == "unsupported")
        self.legacy_v4_banner.setVisible(self._kbd_state == "legacy")
        self.studio_container.setVisible(self._kbd_state == "supported")
        self.power_toggle_btn.setVisible(self._kbd_state == "supported")

        if lamp_diag.get("status") == "permission_denied":
            self.lamparray_permission_banner.title_lbl.setText("24-bit RGB Permission Denied")
            self.lamparray_permission_banner.set_body(
                str(
                    lamp_diag.get("reason")
                    or "GigaMate detected a LampArray RGB keyboard, but permission to its HID node was denied."
                )
            )
            self.lamparray_permission_banner.setVisible(True)
        else:
            self.lamparray_permission_banner.setVisible(False)

    def _update_deck_preview(
        self,
        hex_color: Optional[str] = None,
        brightness_pct: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """Update the hero KeyboardDeckWidget preview."""
        c = hex_color or effective_rgb_hex(self.cfg)
        b = (
            int(brightness_pct)
            if brightness_pct is not None
            else max(5, int(self.cfg.get("brightness_pct", 100) or 100))
        )
        en = is_rgb_enabled(self.cfg) if enabled is None else bool(enabled)
        self.keyboard_deck.set_lighting(c, b, enabled=en)

    def _on_pad_color_changed(self, rgb_hex: str) -> None:
        self._pending_pad_hex = rgb_hex
        self.custom_colour_swatch.setStyleSheet(
            f"QPushButton {{ background-color: {rgb_hex}; border: 2px solid {THEME.outline}; "
            f"border-radius: 14px; min-width: 24px; max-width: 24px; "
            f"min-height: 24px; max-height: 24px; padding: 0px; }}"
        )
        self.hex_input.blockSignals(True)
        self.hex_input.setText(rgb_hex.lower())
        self.hex_input.blockSignals(False)
        self._update_deck_preview(hex_color=rgb_hex, enabled=True)
        self._color_debounce.start()

    def _on_color_debounce(self) -> None:
        if not self._pending_pad_hex:
            return
        dev = get_keyboard()
        if dev is not None:
            pct = max(
                1,
                effective_brightness_pct(self.cfg)
                or int(self.cfg.get("brightness_pct", 100) or 100),
            )
            set_rgb(dev, self._pending_pad_hex, pct)

    def _on_pad_color_committed(self, rgb_hex: str) -> None:
        self._color_debounce.stop()
        self._pending_pad_hex = None
        self._commit_rgb_hex(rgb_hex)

    def _on_hex_input_committed(self) -> None:
        raw = self.hex_input.text().strip()
        if not _HEX_RE.match(raw):
            self.hex_input.blockSignals(True)
            self.hex_input.setText(effective_rgb_hex(self.cfg).lower())
            self.hex_input.blockSignals(False)
            return
        rgb_hex = raw if raw.startswith("#") else f"#{raw}"
        self._commit_rgb_hex(rgb_hex.lower())

    def _commit_rgb_hex(self, rgb_hex: str) -> None:
        rgb_hex = rgb_hex.lower()

        def _mutate(cfg):
            cfg["rgb_enabled"] = True
            pct = int(cfg.get("brightness_pct", 100) or 100)
            if pct <= 0:
                pct = int(cfg.get("last_brightness_pct", 25) or 25)
                cfg["brightness_pct"] = pct
            cfg["brightness"] = pct_to_legacy_brightness(pct)
            cfg["rgb_hex"] = rgb_hex
            return cfg

        self.cfg = update_config(_mutate)
        self._unlink_profile_rgb_if_needed()
        self._apply_hardware()
        self._highlight_active()

    def _toggle_rgb_power(self) -> None:
        def _mutate(cfg):
            new_enabled = not is_rgb_enabled(cfg)
            cfg["rgb_enabled"] = new_enabled
            if new_enabled:
                pct = int(cfg.get("brightness_pct", 100) or 100)
                if pct <= 0:
                    pct = int(cfg.get("last_brightness_pct", 25) or 25)
                    cfg["brightness_pct"] = pct
                cfg["brightness"] = pct_to_legacy_brightness(pct)
            else:
                cfg["brightness"] = 0
            return cfg

        self.cfg = update_config(_mutate)
        self._apply_hardware()
        self._highlight_active()

    def _set_brightness_pct(self, pct: int) -> None:
        pct = max(5, min(100, int(pct)))
        self._brightness_debounce.stop()

        def _mutate(cfg):
            cfg["rgb_enabled"] = True
            cfg["last_brightness"] = pct_to_legacy_brightness(pct)
            cfg["brightness_pct"] = pct
            cfg["brightness"] = pct_to_legacy_brightness(pct)
            return cfg

        self.cfg = update_config(_mutate)
        self._apply_hardware()
        self._highlight_active()

    def _on_brightness_slider_changed(self, pct: int) -> None:
        self.brightness_readout.setText(f"{pct}%")
        self._update_deck_preview(brightness_pct=pct, enabled=True)
        self._brightness_debounce.start()

    def _on_brightness_debounce(self) -> None:
        pct = max(5, self.brightness_slider.value())

        def _mutate(cfg):
            cfg["rgb_enabled"] = True
            cfg["brightness_pct"] = pct
            cfg["brightness"] = pct_to_legacy_brightness(pct)
            return cfg

        self.cfg = update_config(_mutate)
        dev = get_keyboard()
        if dev is not None:
            set_rgb(dev, effective_rgb_hex(self.cfg), pct)
        self._highlight_active()

    def _on_brightness_slider_released(self) -> None:
        self._set_brightness_pct(self.brightness_slider.value())

    def _unlink_profile_rgb_if_needed(self) -> None:
        if not self.cfg.get("profile_rgb_enabled", False):
            return

        def _mutate(cfg):
            cfg["profile_rgb_enabled"] = False
            return cfg

        self.cfg = update_config(_mutate)
        self.unlink_status_label.setText(
            "Unlinked from Power Profile — manual colour active."
        )
        self.unlink_status_label.setVisible(True)
        self._sync_auto_link_pill()

    def _apply_hardware(self) -> None:
        dev = get_keyboard()
        if dev is None:
            return
        if not is_rgb_enabled(self.cfg):
            set_off(dev)
            return
        pct = effective_brightness_pct(self.cfg)
        if pct <= 0:
            set_off(dev)
            return
        set_rgb(dev, effective_rgb_hex(self.cfg), pct)

    @staticmethod
    def _lifted_hex(hex_color: str) -> str:
        c = QColor(hex_color)
        if not c.isValid():
            return THEME.primary
        if c.lightnessF() < 0.55:
            h, s, _, a = c.getHslF()
            c.setHslF(max(0.0, h), s, 0.62, a)
        return c.name()

    def _highlight_active(self) -> None:
        enabled = is_rgb_enabled(self.cfg)
        is_off = not enabled
        pct = max(5, int(self.cfg.get("brightness_pct", 100) or 100))
        swatch_hex = effective_rgb_hex(self.cfg)
        if self.cfg.get("rgb_hex"):
            active_label = swatch_hex.upper()
        else:
            colour = str(self.cfg.get("colour", "light_purple") or "light_purple")
            active_label = colour.replace("_", " ").title()

        self.custom_colour_swatch.setStyleSheet(
            f"QPushButton {{ background-color: {swatch_hex}; border: 2px solid {THEME.outline}; "
            f"border-radius: 14px; min-width: 24px; max-width: 24px; "
            f"min-height: 24px; max-height: 24px; padding: 0px; }}"
        )
        self.color_pad.set_hex(swatch_hex)
        self.hex_input.blockSignals(True)
        self.hex_input.setText(swatch_hex.lower())
        self.hex_input.blockSignals(False)
        self.brightness_slider.blockSignals(True)
        self.brightness_slider.setValue(pct)
        self.brightness_slider.blockSignals(False)
        self.brightness_readout.setText(f"{pct}%")

        for p_val, btn in self._brightness_preset_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(pct == p_val)
            btn.blockSignals(False)

        for hex_key, sw in self.swatch_buttons.items():
            is_sel = hex_key == swatch_hex.lower() and enabled
            border = THEME.on_surface if is_sel else THEME.outline_variant
            sw.setStyleSheet(
                f"QPushButton {{ background-color: {hex_key}; border: 2px solid {border}; "
                f"border-radius: 13px; min-width: 22px; max-width: 22px; "
                f"min-height: 22px; max-height: 22px; padding: 0px; }}"
                f"QPushButton:hover {{ border: 2px solid {THEME.on_surface}; }}"
            )

        if enabled:
            self.power_toggle_btn.setText("Turn Off")
            self.power_toggle_btn.setProperty("class", "OutlinedButton")
            self.power_toggle_btn.setIcon(
                icons.make_icon("power_off", color=THEME.on_surface, size=15)
            )
        else:
            self.power_toggle_btn.setText("Turn On")
            self.power_toggle_btn.setProperty("class", "TonalButton")
            self.power_toggle_btn.setIcon(
                icons.make_icon("power_off", color=THEME.on_primary_container, size=15)
            )
        self.power_toggle_btn.style().unpolish(self.power_toggle_btn)
        self.power_toggle_btn.style().polish(self.power_toggle_btn)

        if is_off:
            self.active_color_badge.setText(f"Active: {active_label} (Off)")
            self.active_color_badge.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 600; "
                f"padding: 4px 12px; border-radius: 12px; "
                f"background-color: {THEME.surface_container_high}; "
                f"border: 1px solid {THEME.outline_variant};"
            )
        else:
            lifted = self._lifted_hex(swatch_hex)
            self.active_color_badge.setText(f"Active: {active_label}")
            self.active_color_badge.setStyleSheet(
                f"color: {THEME.on_surface}; font-family: {THEME.mono_family}; font-size: 12px; font-weight: 700; "
                f"padding: 4px 12px; border-radius: 12px; "
                f"background-color: {THEME.surface_container_high}; "
                f"border: 1.5px solid {lifted};"
            )

        if bool(self.cfg.get("profile_rgb_enabled", False)):
            self.unlink_status_label.setText(
                "Linked to Power Profile — picking a colour will unlink."
            )
            self.unlink_status_label.setVisible(True)
        elif "Unlinked" not in self.unlink_status_label.text():
            self.unlink_status_label.setVisible(False)

        self._update_deck_preview(hex_color=swatch_hex, brightness_pct=pct, enabled=enabled)
        self._sync_auto_link_pill()

    def reload_from_config(self) -> None:
        """Synchronize UI with latest on-disk config without hardware writes."""
        self.cfg = load_config()
        self._refresh_keyboard_support()
        self._highlight_active()

    def sync_from_config(self) -> None:
        """Alias for reload_from_config()."""
        self.reload_from_config()
