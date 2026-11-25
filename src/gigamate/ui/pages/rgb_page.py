"""GigaMate Center — Keyboard RGB Lighting & Effects Page."""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
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
    M3Banner,
    M3Card,
    M3ComboBox,
    M3PageHeader,
    M3ResponsiveGrid,
    M3SegmentedBar,
    M3Slider,
    M3SwitchRow,
)

# Color swatch hex codes (display names match tray.py: cname.replace('_', ' ').title())
COLOR_HEX_MAP: Dict[str, str] = {
    "red": "#fc8181",
    "green": "#68d391",
    "yellow": "#f6e05e",
    "blue": "#63b3ed",
    "orange": "#f6ad55",
    "dark_yellow": "#ecc94b",
    "purple": "#b794f4",
    "light_purple": "#d6bcfa",
    "white": "#f7fafc",
    "light_blue": "#90cdf4",
    "blush_pink": "#f687b3",
}


def make_color_swatch_icon(hex_color: str, size: int = 16) -> QIcon:
    """Generate a crisp circular antialiased color swatch icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QPen(QColor(255, 255, 255, 68), 1))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class RgbColorPad(QWidget):
    """2D Hue × Saturation colour pad (Value fixed at 255)."""

    colorChanged = pyqtSignal(str)
    colorCommitted = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hue: float = 0.72  # 0.0 .. 1.0
        self._sat: float = 0.40  # 0.0 .. 1.0
        self._gradient_img: Optional[QImage] = None
        self.setMinimumHeight(148)
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
    """Keyboard RGB backlight colours, brightness, and idle sleep timeout."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cfg = load_config()
        self._full_rgb: bool = False
        self._palette_info: Dict[str, Tuple[str, str]] = {}
        self.color_buttons: Dict[str, QPushButton] = {}
        self.brightness_buttons: Dict[int, QPushButton] = {}
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

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)

        header = M3PageHeader(
            "RGB Lighting",
            "Keyboard backlight colour, brightness & idle energy saver",
        )
        layout.addWidget(header)

        # ── Colour Palette Card ──
        colour_card = M3Card()
        c_layout = QVBoxLayout(colour_card)
        c_layout.setContentsMargins(20, 18, 20, 18)
        c_layout.setSpacing(14)

        # Header with Title and Active Badge
        c_header = QHBoxLayout()
        c_title = QLabel("Keyboard Backlight Colour")
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

        self.power_toggle_btn = QPushButton("Turn Off")
        self.power_toggle_btn.setProperty("class", "OutlinedButton")
        self.power_toggle_btn.setIcon(
            icons.make_icon("power_off", color=THEME.on_surface, size=15)
        )
        self.power_toggle_btn.setIconSize(QSize(15, 15))
        self.power_toggle_btn.setMinimumHeight(32)
        self.power_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_toggle_btn.clicked.connect(self._toggle_rgb_power)
        c_header.addWidget(self.power_toggle_btn)
        c_layout.addLayout(c_header)

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

        # ── Notice 4: LampArray Unsupported Firmware/Layout Caption ──
        self.lamparray_unsupported_caption = QLabel("")
        self.lamparray_unsupported_caption.setWordWrap(True)
        self.lamparray_unsupported_caption.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
        )
        self.lamparray_unsupported_caption.setVisible(False)
        c_layout.addWidget(self.lamparray_unsupported_caption)

        # ── Palette Container (shown when profile is mapped) ──
        self.palette_container = QWidget()
        palette_outer = QVBoxLayout(self.palette_container)
        palette_outer.setContentsMargins(0, 0, 0, 0)
        palette_outer.setSpacing(12)

        # Full 24-bit RGB Pad + Compact Control Strip (shown when self._full_rgb is True)
        self.full_rgb_container = QWidget()
        fr_layout = QVBoxLayout(self.full_rgb_container)
        fr_layout.setContentsMargins(0, 0, 0, 0)
        fr_layout.setSpacing(12)

        self.color_pad = RgbColorPad()
        self.color_pad.colorChanged.connect(self._on_pad_color_changed)
        self.color_pad.colorCommitted.connect(self._on_pad_color_committed)
        fr_layout.addWidget(self.color_pad)

        cc_row = QHBoxLayout()
        cc_row.setContentsMargins(0, 0, 0, 0)
        cc_row.setSpacing(10)

        self.custom_colour_swatch = QPushButton()
        self.custom_colour_swatch.setFixedSize(28, 28)
        self.custom_colour_swatch.setEnabled(False)
        cc_row.addWidget(self.custom_colour_swatch)

        self.hex_input = QLineEdit("#b794f4")
        self.hex_input.setMaximumWidth(104)
        self.hex_input.setMaxLength(7)
        self.hex_input.setStyleSheet(
            f"font-family: {THEME.mono_family}; font-size: 12px; font-weight: 600; "
            f"color: {THEME.on_surface}; background-color: {THEME.surface_container_high}; "
            f"border: 1px solid {THEME.outline_variant}; border-radius: 8px; padding: 4px 10px;"
        )
        self.hex_input.editingFinished.connect(self._on_hex_input_committed)
        cc_row.addWidget(self.hex_input)

        cc_row.addStretch()

        self.white_btn = QPushButton("White")
        self.white_btn.setProperty("class", "OutlinedButton")
        self.white_btn.setMinimumHeight(34)
        self.white_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.white_btn.clicked.connect(lambda: self._commit_rgb_hex("#ffffff"))
        cc_row.addWidget(self.white_btn)

        fr_layout.addLayout(cc_row)
        palette_outer.addWidget(self.full_rgb_container)
        c_layout.addWidget(self.palette_container)
        layout.addWidget(colour_card)

        # ── Brightness & Energy Saver Card ──
        self.opts_card = M3Card()
        o_layout = QVBoxLayout(self.opts_card)
        o_layout.setContentsMargins(20, 18, 20, 18)
        o_layout.setSpacing(16)

        o_title = QLabel("Brightness & Energy Saver")
        o_title.setProperty("class", "CardTitle")
        o_layout.addWidget(o_title)

        # Continuous 1–100% Brightness Slider + 4 presets (LampArray / full RGB)
        self.full_brightness_container = QWidget()
        fb_layout = QVBoxLayout(self.full_brightness_container)
        fb_layout.setContentsMargins(0, 0, 0, 0)
        fb_layout.setSpacing(10)

        fb_slider_row = QHBoxLayout()
        fb_slider_row.setSpacing(12)
        fb_label = QLabel("Backlight Brightness:")
        fb_label.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-weight: 500; min-width: 145px;"
        )
        fb_slider_row.addWidget(fb_label)

        self.brightness_slider = M3Slider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(5, 100)
        self.brightness_slider.setSingleStep(1)
        self.brightness_slider.setPageStep(5)
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        self.brightness_slider.sliderReleased.connect(self._on_brightness_slider_released)
        fb_slider_row.addWidget(self.brightness_slider, stretch=1)

        self.brightness_readout = QLabel("100%")
        self.brightness_readout.setProperty("class", "ValueReadoutPill")
        self.brightness_readout.setMinimumWidth(54)
        self.brightness_readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fb_slider_row.addWidget(self.brightness_readout)
        fb_layout.addLayout(fb_slider_row)
        o_layout.addWidget(self.full_brightness_container)

        # Idle Sleep Timeout
        idle_row = QHBoxLayout()
        idle_row.setSpacing(12)
        idle_label = QLabel("Backlight Auto-Off (Idle):")
        idle_label.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-weight: 500; min-width: 145px;"
        )
        idle_row.addWidget(idle_label)

        self.idle_combo = M3ComboBox()
        self.idle_combo.setMinimumWidth(210)
        # Single source of truth shared with the tray (gigamate.idle).
        self.idle_options = [(label, sec) for sec, label in IDLE_TIMEOUT_STEPS]
        for text, sec in self.idle_options:
            self.idle_combo.addItem(text, sec)

        self.idle_combo.currentIndexChanged.connect(self._on_idle_changed)
        idle_row.addWidget(self.idle_combo)
        idle_row.addStretch()
        o_layout.addLayout(idle_row)

        idle_caption = QLabel(
            "Turns off the keyboard backlight after inactivity — the keyboard stays active."
        )
        idle_caption.setWordWrap(True)
        idle_caption.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; margin-left: 157px;"
        )
        o_layout.addWidget(idle_caption)

        self.keep_on_ac_row = M3SwitchRow(
            "Keep backlight active on AC",
            "Pause idle auto-off while plugged into AC power",
        )
        self.keep_on_ac_row.switch.toggled.connect(self._on_keep_on_ac_toggled)
        o_layout.addWidget(self.keep_on_ac_row)

        self.unlink_status_label = QLabel("")
        self.unlink_status_label.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; font-style: italic;"
        )
        self.unlink_status_label.setVisible(False)
        o_layout.addWidget(self.unlink_status_label)

        layout.addWidget(self.opts_card)
        layout.addStretch()

        self._sync_idle_combo()
        self._refresh_keyboard_support()
        self._highlight_active()

    def _sync_idle_combo(self) -> None:
        """Select the combo entry matching the stored timeout (nearest step)."""
        idle_enabled = bool(self.cfg.get("idle_off_enabled", False))
        if not idle_enabled:
            target = IDLE_STEP_OFF
        else:
            target = nearest_idle_step(self.cfg.get("idle_timeout_sec", 60))

        for i, (_, sec) in enumerate(self.idle_options):
            if sec == target:
                self.idle_combo.blockSignals(True)
                self.idle_combo.setCurrentIndex(i)
                self.idle_combo.blockSignals(False)
                break

        if hasattr(self, "keep_on_ac_row"):
            self.keep_on_ac_row.switch.blockSignals(True)
            self.keep_on_ac_row.switch.setChecked(bool(self.cfg.get("idle_keep_on_ac", False)))
            self.keep_on_ac_row.switch.blockSignals(False)
            self.keep_on_ac_row.setEnabled(idle_enabled)

    def _refresh_keyboard_support(self) -> None:
        """Inspect hardware capabilities and display appropriate notices."""
        caps = detect_system_capabilities()
        diag_status = "not_detected"
        diag_reason = ""
        if caps.keyboard_detected and caps.keyboard_vid_pid is not None:
            diag = diagnose_lamparray(
                caps.keyboard_vid_pid[0],
                caps.keyboard_vid_pid[1],
            )
            diag_status = str(diag.get("status", "not_detected"))
            diag_reason = str(diag.get("reason", ""))

        if not caps.keyboard_detected:
            self._kbd_state = "unsupported"
            self._full_rgb = False
            self.palette_container.setVisible(False)
            self.legacy_v4_banner.setVisible(False)
            self.kbd_unsupported_notice.setVisible(True)
            self.opts_card.setVisible(False)
            self.active_color_badge.setVisible(False)
            self.power_toggle_btn.setVisible(False)
        elif not caps.keyboard_lamparray_supported or diag_status in (
            "unsupported_firmware",
            "unsupported_layout",
        ):
            self._kbd_state = "legacy_v4"
            self._full_rgb = False
            self.palette_container.setVisible(False)
            self.kbd_unsupported_notice.setVisible(False)
            self.legacy_v4_banner.setVisible(True)
            self.opts_card.setVisible(False)
            self.active_color_badge.setVisible(False)
            self.power_toggle_btn.setVisible(False)
        else:
            self._kbd_state = "supported"
            self._full_rgb = True
            self.palette_container.setVisible(True)
            self.full_rgb_container.setVisible(True)
            self.kbd_unsupported_notice.setVisible(False)
            self.legacy_v4_banner.setVisible(False)
            self.opts_card.setVisible(True)
            self.full_brightness_container.setVisible(True)
            self.active_color_badge.setVisible(True)
            self.power_toggle_btn.setVisible(True)

        if diag_status == "permission_denied":
            self.lamparray_permission_banner.body_lbl.setText(diag_reason)
            self.lamparray_permission_banner.setVisible(True)
        else:
            self.lamparray_permission_banner.setVisible(False)
        self.lamparray_unsupported_caption.setVisible(False)

    def _set_colour(self, colour: str) -> None:
        # Atomic read-modify-write so concurrent changes from tray/CLI are not
        # clobbered by a stale in-memory snapshot.
        def _mutate(cfg):
            if self._full_rgb:
                if effective_brightness_pct(cfg) == 0:
                    restored_pct = int(cfg.get("last_brightness_pct", 25) or 25)
                    cfg["brightness_pct"] = restored_pct
                    cfg["brightness"] = pct_to_legacy_brightness(restored_pct)
                cfg["colour"] = colour
                cfg["rgb_hex"] = None
            else:
                if cfg.get("brightness", 2) == 0:
                    restored = cfg.get("last_brightness", 2)
                    if restored == 0:
                        restored = 2
                    cfg["brightness"] = restored
                cfg["colour"] = colour
            return cfg

        self.cfg = update_config(_mutate)
        self._unlink_profile_rgb_if_needed()
        self._apply_hardware()
        self._highlight_active()

    def _on_pad_color_changed(self, rgb_hex: str) -> None:
        self._pending_pad_hex = rgb_hex
        self.custom_colour_swatch.setStyleSheet(
            f"background-color: {rgb_hex}; border: 2px solid {THEME.outline}; "
            f"border-radius: 14px; min-width: 0px; min-height: 0px; padding: 0px;"
        )
        self.hex_input.blockSignals(True)
        self.hex_input.setText(rgb_hex.lower())
        self.hex_input.blockSignals(False)
        self._color_debounce.start()

    def _on_color_debounce(self) -> None:
        if not self._pending_pad_hex:
            return
        dev = get_keyboard()
        if dev is not None:
            pct = max(1, effective_brightness_pct(self.cfg) or int(self.cfg.get("brightness_pct", 100) or 100))
            set_rgb(
                dev,
                self._pending_pad_hex,
                pct,
            )

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

    def _set_brightness(self, level: int) -> None:
        def _mutate(cfg):
            if level > 0:
                cfg["last_brightness"] = level
            cfg["brightness"] = level
            return cfg

        self.cfg = update_config(_mutate)
        self._apply_hardware()
        self._highlight_active()

    def _set_brightness_pct(self, pct: int) -> None:
        pct = max(1, min(100, int(pct)))
        self._brightness_debounce.stop()

        def _mutate(cfg):
            cfg["rgb_enabled"] = True
            cfg["last_brightness_pct"] = pct
            cfg["last_brightness"] = pct_to_legacy_brightness(pct)
            cfg["brightness_pct"] = pct
            cfg["brightness"] = pct_to_legacy_brightness(pct)
            return cfg

        self.cfg = update_config(_mutate)
        self._apply_hardware()
        self._highlight_active()

    def _on_brightness_slider_changed(self, pct: int) -> None:
        self.brightness_readout.setText(f"{pct}%")
        self._brightness_debounce.start()

    def _on_brightness_debounce(self) -> None:
        pct = max(5, self.brightness_slider.value())
        dev = get_keyboard()
        if dev is not None:
            set_rgb(
                dev,
                effective_rgb_hex(self.cfg),
                pct,
            )

    def _on_brightness_slider_released(self) -> None:
        self._set_brightness_pct(self.brightness_slider.value())

    def _on_idle_changed(self, index: int) -> None:
        sec = self.idle_combo.currentData()

        def _mutate(cfg):
            if sec == 0:
                cfg["idle_off_enabled"] = False
            else:
                cfg["idle_off_enabled"] = True
                cfg["idle_timeout_sec"] = sec
            return cfg

        self.cfg = update_config(_mutate)
        if hasattr(self, "keep_on_ac_row"):
            self.keep_on_ac_row.setEnabled(bool(self.cfg.get("idle_off_enabled", False)))

    def _on_keep_on_ac_toggled(self, checked: bool) -> None:
        def _mutate(cfg):
            cfg["idle_keep_on_ac"] = bool(checked)
            return cfg

        self.cfg = update_config(_mutate)

    def _unlink_profile_rgb_if_needed(self) -> None:
        if not self.cfg.get("profile_rgb_enabled", False):
            return

        def _mutate(cfg):
            cfg["profile_rgb_enabled"] = False
            return cfg

        self.cfg = update_config(_mutate)
        if hasattr(self, "unlink_status_label"):
            self.unlink_status_label.setText("Unlinked from Power Profile colour")
            self.unlink_status_label.setVisible(True)
            QTimer.singleShot(3000, lambda: self.unlink_status_label.setVisible(False))

    def _apply_hardware(self) -> None:
        dev = get_keyboard()
        if dev is not None:
            pct = effective_brightness_pct(self.cfg)
            if not is_rgb_enabled(self.cfg) or pct == 0:
                set_off(dev)
            else:
                set_rgb(
                    dev,
                    effective_rgb_hex(self.cfg),
                    pct,
                )

    def _turn_off_backlight(self) -> None:
        def _mutate(cfg):
            curr_pct = effective_brightness_pct(cfg)
            if curr_pct > 0:
                cfg["last_brightness_pct"] = curr_pct
                cfg["last_brightness"] = pct_to_legacy_brightness(curr_pct)
            cfg["brightness_pct"] = 0
            cfg["brightness"] = 0
            return cfg

        self.cfg = update_config(_mutate)
        self._apply_hardware()
        self._highlight_active()

    def _highlight_active(self) -> None:
        b_level = self.cfg.get("brightness", 2)
        active_col = self.cfg.get("colour", "light_purple")
        custom_hex = self.cfg.get("rgb_hex")
        if self._full_rgb and custom_hex:
            active_hex = effective_rgb_hex(self.cfg)
            active_label = active_hex.upper()
        else:
            active_label, active_hex = self._palette_info.get(
                active_col, (active_col.replace("_", " ").title(), "#b794f4")
            )

        if self._full_rgb:
            enabled = is_rgb_enabled(self.cfg)
            is_off = not enabled
            pct = max(5, int(self.cfg.get("brightness_pct", 100) or 100))
            swatch_hex = effective_rgb_hex(self.cfg)
            self.custom_colour_swatch.setStyleSheet(
                f"background-color: {swatch_hex}; border: 2px solid {THEME.outline}; "
                f"border-radius: 14px; min-width: 0px; min-height: 0px; padding: 0px;"
            )
            self.color_pad.set_hex(swatch_hex)
            self.hex_input.blockSignals(True)
            self.hex_input.setText(swatch_hex.lower())
            self.hex_input.blockSignals(False)
            self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(pct)
            self.brightness_slider.blockSignals(False)
            self.brightness_readout.setText(f"{pct}%")

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
        else:
            is_off = b_level == 0

        # Update Header Badge (keep the "Uncalibrated" badge when applicable)
        if getattr(self, "_kbd_state", "supported") != "uncalibrated":
            if is_off:
                self.active_color_badge.setText(f"Active: {active_label} (Off)")
                self.active_color_badge.setStyleSheet(
                    f"color: {THEME.on_surface_muted}; font-size: 12px; font-weight: 600; "
                    f"padding: 4px 12px; border-radius: 12px; "
                    f"background-color: {THEME.surface_container_high}; "
                    f"border: 1px solid {THEME.outline_variant};"
                )
            else:
                self.active_color_badge.setText(f"Active: {active_label}")
                self.active_color_badge.setStyleSheet(
                    f"color: {active_hex}; font-size: 12px; font-weight: 600; "
                    f"padding: 4px 12px; border-radius: 12px; "
                    f"background-color: {THEME.surface_container_high}; "
                    f"border: 1px solid {active_hex};"
                )

        for col_key, btn in self.color_buttons.items():
            label, hex_color = self._palette_info.get(col_key, (col_key, "#ffffff"))
            if is_off and col_key == "off":
                btn.setText(f"  {label}   ✓")
                btn.setStyleSheet(
                    "QPushButton { background-color: #36302B; border: 2px solid #857A72; "
                    "color: #F2ECE7; font-weight: 600; border-radius: 12px; "
                    "padding: 10px 14px; text-align: left; }"
                )
            elif not is_off and not (self._full_rgb and custom_hex) and col_key == active_col:
                btn.setText(f"  {label}   ✓")
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: #36302B; border: 2px solid {hex_color}; "
                    f"color: #F2ECE7; font-weight: 600; border-radius: 12px; "
                    f"padding: 10px 14px; text-align: left; }}"
                )
            else:
                btn.setText(f"  {label}")
                btn.setStyleSheet(
                    "QPushButton { background-color: #2C2723; border: 1px solid #423a35; "
                    "color: #B9AEA6; font-weight: 500; border-radius: 12px; "
                    "padding: 10px 14px; text-align: left; } "
                    "QPushButton:hover { background-color: #36302B; border-color: #6E635B; "
                    "color: #F2ECE7; }"
                )

        if b_level in self.brightness_buttons:
            self.brightness_buttons[b_level].setChecked(True)

    def reload_from_config(self) -> None:
        """Synchronize UI with latest on-disk config without hardware writes."""
        self.cfg = load_config()
        self._refresh_keyboard_support()

        # Update Colour controls and header badge
        self._highlight_active()

        # Update Idle Timeout without re-firing signal
        self._sync_idle_combo()

    def sync_from_config(self) -> None:
        """Alias for reload_from_config()."""
        self.reload_from_config()
