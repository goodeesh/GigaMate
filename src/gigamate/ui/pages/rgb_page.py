"""GigaMate Center — Keyboard RGB Lighting & Effects Page."""

from typing import Dict, Optional, Tuple
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...capabilities import detect_system_capabilities
from ...config import DEFAULT_CONFIG, load as load_config, resolve_active_profile, save as save_config
from ...protocol import get_keyboard, set_off, set_static

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


def make_color_swatch_icon(hex_color: str, size: int = 14) -> QIcon:
    """Generate a crisp circular antialiased color swatch icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


class RgbPage(QWidget):
    """Keyboard RGB backlight colours, brightness, and idle sleep timeout."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cfg = load_config()
        self._palette_info: Dict[str, Tuple[str, str]] = {}
        self.color_buttons: Dict[str, QPushButton] = {}
        self.brightness_buttons: Dict[int, QPushButton] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Colour Palette Card ──
        colour_card = QFrame()
        colour_card.setProperty("class", "Card")
        c_layout = QVBoxLayout(colour_card)
        c_layout.setSpacing(14)

        # Header with Title and Active Badge
        c_header = QHBoxLayout()
        c_title = QLabel("Keyboard Backlight Colour")
        c_title.setProperty("class", "CardTitle")
        c_header.addWidget(c_title)
        c_header.addStretch()

        self.active_color_badge = QLabel("")
        self.active_color_badge.setStyleSheet(
            "font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 12px; "
            "background-color: #171b25; border: 1px solid #364157;"
        )
        c_header.addWidget(self.active_color_badge)
        c_layout.addLayout(c_header)

        # ── Notice 1: Unsupported Keyboard (non-Gigabyte) ──
        self.kbd_unsupported_notice = QFrame()
        self.kbd_unsupported_notice.setStyleSheet(
            "background-color: #171b26; border: 1px solid #283347; border-radius: 8px; padding: 14px;"
        )
        u_lay = QVBoxLayout(self.kbd_unsupported_notice)
        u_lay.setContentsMargins(14, 12, 14, 12)
        u_lay.setSpacing(4)
        u_title = QLabel("ℹ️ No Compatible RGB Keyboard Detected")
        u_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: 600;")
        u_body = QLabel(
            "GigaMate did not detect a supported Gigabyte ITE 829x USB keyboard controller on this system."
        )
        u_body.setStyleSheet("color: #8896ab; font-size: 12px;")
        u_lay.addWidget(u_title)
        u_lay.addWidget(u_body)
        c_layout.addWidget(self.kbd_unsupported_notice)

        # ── Notice 2: Uncalibrated Gigabyte Keyboard (profile missing) ──
        self.kbd_uncalibrated_notice = QFrame()
        self.kbd_uncalibrated_notice.setStyleSheet(
            "background-color: #2c1b12; border: 1px solid #744210; border-radius: 8px; padding: 14px;"
        )
        c_lay2 = QVBoxLayout(self.kbd_uncalibrated_notice)
        c_lay2.setContentsMargins(14, 12, 14, 12)
        c_lay2.setSpacing(8)

        self.uncalibrated_title = QLabel("⚠️ Uncalibrated Gigabyte Keyboard Detected")
        self.uncalibrated_title.setStyleSheet("color: #f6ad55; font-size: 13px; font-weight: 600;")
        self.uncalibrated_body = QLabel(
            "Your keyboard hardware was detected, but a color profile has not been generated for this model yet. "
            "Each Gigabyte generation maps colors to distinct USB payload bytes."
        )
        self.uncalibrated_body.setStyleSheet("color: #cbd5e0; font-size: 12px;")
        self.uncalibrated_body.setWordWrap(True)
        c_lay2.addWidget(self.uncalibrated_title)
        c_lay2.addWidget(self.uncalibrated_body)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        cmd_hint = QLabel("💡 Run 'gigamate calibrate' in your terminal to create a profile.")
        cmd_hint.setStyleSheet("color: #ecc94b; font-size: 11px;")
        action_row.addWidget(cmd_hint)
        action_row.addStretch()

        btn_safe_off = QPushButton("Turn Off Backlight")
        btn_safe_off.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_safe_off.clicked.connect(self._turn_off_backlight)
        action_row.addWidget(btn_safe_off)
        c_lay2.addLayout(action_row)

        c_layout.addWidget(self.kbd_uncalibrated_notice)

        # ── Palette Container (shown when profile is mapped) ──
        self.palette_container = QWidget()
        pal_layout = QVBoxLayout(self.palette_container)
        pal_layout.setContentsMargins(0, 0, 0, 0)
        pal_layout.setSpacing(10)

        # Dynamically load all colours from active hardware profile (with fallback)
        profile = resolve_active_profile()
        if profile is not None and profile.colour_names:
            colour_keys = profile.colour_names
        else:
            colour_keys = list(COLOR_HEX_MAP.keys())

        grid = QGridLayout()
        grid.setSpacing(10)

        self._palette_info = {}
        self.color_buttons = {}
        for idx, col_key in enumerate(colour_keys):
            label = col_key.replace("_", " ").title()
            hex_color = COLOR_HEX_MAP.get(col_key, "#a0aec0")
            self._palette_info[col_key] = (label, hex_color)
            btn = QPushButton(f"  {label}")
            btn.setIcon(make_color_swatch_icon(hex_color, 14))
            btn.setIconSize(QSize(14, 14))
            btn.setProperty("class", "ColorPaletteBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(44)
            btn.clicked.connect(lambda _, c=col_key: self._set_colour(c))
            self.color_buttons[col_key] = btn
            grid.addWidget(btn, idx // 4, idx % 4)

        # 12th Slot: Quick "Turn Off" button to complete symmetrical 4x3 grid
        btn_off = QPushButton("  Turn Off")
        btn_off.setIcon(make_color_swatch_icon("#4a5568", 14))
        btn_off.setIconSize(QSize(14, 14))
        btn_off.setProperty("class", "ColorPaletteBtn")
        btn_off.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_off.setMinimumHeight(44)
        btn_off.clicked.connect(self._turn_off_backlight)
        self.color_buttons["off"] = btn_off
        self._palette_info["off"] = ("Turn Off", "#4a5568")
        grid.addWidget(btn_off, 11 // 4, 11 % 4)

        pal_layout.addLayout(grid)
        c_layout.addWidget(self.palette_container)
        layout.addWidget(colour_card)

        # ── Brightness & Energy Saver Card ──
        self.opts_card = QFrame()
        self.opts_card.setProperty("class", "Card")
        o_layout = QVBoxLayout(self.opts_card)
        o_layout.setSpacing(16)

        o_title = QLabel("Brightness & Energy Saver")
        o_title.setProperty("class", "CardTitle")
        o_layout.addWidget(o_title)

        # Brightness segmented buttons
        b_row = QHBoxLayout()
        b_label = QLabel("Backlight Brightness:")
        b_label.setStyleSheet("color: #cbd5e0; font-weight: 500; min-width: 160px;")
        b_row.addWidget(b_label)

        self.brightness_group = QButtonGroup(self)
        self.brightness_group.setExclusive(True)
        self.brightness_buttons = {}

        b_btns_layout = QHBoxLayout()
        b_btns_layout.setSpacing(8)
        for b_val, label in enumerate(("Off", "Dim (50%)", "Full (100%)")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("class", "SegmentButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.brightness_group.addButton(btn, b_val)
            self.brightness_buttons[b_val] = btn
            btn.clicked.connect(lambda _, v=b_val: self._set_brightness(v))
            b_btns_layout.addWidget(btn)

        b_row.addLayout(b_btns_layout)
        b_row.addStretch()
        o_layout.addLayout(b_row)

        # Idle Sleep Timeout
        idle_row = QHBoxLayout()
        idle_label = QLabel("Keyboard Idle Sleep:")
        idle_label.setStyleSheet("color: #cbd5e0; font-weight: 500; min-width: 160px;")
        idle_row.addWidget(idle_label)

        self.idle_combo = QComboBox()
        self.idle_combo.setFixedWidth(200)
        self.idle_options = [
            ("15 seconds", 15),
            ("30 seconds", 30),
            ("1 minute", 60),
            ("2 minutes", 120),
            ("5 minutes", 300),
            ("15 minutes", 900),
            ("Never (Always On)", 0),
        ]
        for text, sec in self.idle_options:
            self.idle_combo.addItem(text, sec)

        current_timeout = self.cfg.get("idle_timeout_sec", 60)
        if not self.cfg.get("idle_off_enabled", True):
            current_timeout = 0

        for i, (_, sec) in enumerate(self.idle_options):
            if sec == current_timeout:
                self.idle_combo.setCurrentIndex(i)
                break

        self.idle_combo.currentIndexChanged.connect(self._on_idle_changed)
        idle_row.addWidget(self.idle_combo)
        idle_row.addStretch()
        o_layout.addLayout(idle_row)

        layout.addWidget(self.opts_card)
        layout.addStretch()

        self._refresh_keyboard_support()
        self._highlight_active()

    def _refresh_keyboard_support(self) -> None:
        """Inspect hardware capabilities and display appropriate notices."""
        caps = detect_system_capabilities()
        if not caps.keyboard_detected:
            self.palette_container.setVisible(False)
            self.kbd_uncalibrated_notice.setVisible(False)
            self.kbd_unsupported_notice.setVisible(True)
            self.opts_card.setVisible(False)
            self.active_color_badge.setVisible(False)
        elif not caps.keyboard_profile_loaded:
            self.palette_container.setVisible(False)
            self.kbd_unsupported_notice.setVisible(False)
            self.kbd_uncalibrated_notice.setVisible(True)
            vid, pid = caps.keyboard_vid_pid or (0x0414, 0x0000)
            self.uncalibrated_title.setText(f"⚠️ Uncalibrated Gigabyte Keyboard (VID 0x{vid:04X} PID 0x{pid:04X})")
            self.opts_card.setVisible(False)
            self.active_color_badge.setText("Uncalibrated")
            self.active_color_badge.setStyleSheet(
                "color: #ecc94b; font-size: 12px; font-weight: 600; padding: 4px 12px; "
                "border-radius: 12px; background-color: #171b25; border: 1px solid #744210;"
            )
            self.active_color_badge.setVisible(True)
        else:
            self.palette_container.setVisible(True)
            self.kbd_unsupported_notice.setVisible(False)
            self.kbd_uncalibrated_notice.setVisible(False)
            self.opts_card.setVisible(True)
            self.active_color_badge.setVisible(True)

    def _set_colour(self, colour: str) -> None:
        if self.cfg.get("brightness", 2) == 0:
            restored = self.cfg.get("last_brightness", 2)
            if restored == 0:
                restored = 2
            self.cfg["brightness"] = restored
        self.cfg["colour"] = colour
        save_config(self.cfg)
        self._apply_hardware()
        self._highlight_active()

    def _set_brightness(self, level: int) -> None:
        if level > 0:
            self.cfg["last_brightness"] = level
        self.cfg["brightness"] = level
        save_config(self.cfg)
        self._apply_hardware()
        self._highlight_active()

    def _on_idle_changed(self, index: int) -> None:
        sec = self.idle_combo.currentData()
        if sec == 0:
            self.cfg["idle_off_enabled"] = False
        else:
            self.cfg["idle_off_enabled"] = True
            self.cfg["idle_timeout_sec"] = sec
        save_config(self.cfg)

    def _apply_hardware(self) -> None:
        dev = get_keyboard()
        if dev is not None:
            profile = resolve_active_profile()
            brightness = self.cfg.get("brightness", 2)
            colour = self.cfg.get("colour", "light_purple")
            if brightness == 0:
                set_off(dev, profile)
            else:
                set_static(dev, colour, brightness, profile)

    def _turn_off_backlight(self) -> None:
        curr = self.cfg.get("brightness", 2)
        if curr > 0:
            self.cfg["last_brightness"] = curr
        self._set_brightness(0)

    def _highlight_active(self) -> None:
        b_level = self.cfg.get("brightness", 2)
        active_col = self.cfg.get("colour", "light_purple")
        active_label, active_hex = self._palette_info.get(
            active_col, (active_col.replace("_", " ").title(), "#b794f4")
        )

        # Update Header Badge
        if b_level == 0:
            self.active_color_badge.setText(f"Active: {active_label} (Off)")
            self.active_color_badge.setStyleSheet(
                "color: #718096; font-size: 12px; font-weight: 600; padding: 4px 12px; "
                "border-radius: 12px; background-color: #171c26; border: 1px solid #4a5568;"
            )
        else:
            self.active_color_badge.setText(f"Active: {active_label}")
            self.active_color_badge.setStyleSheet(
                f"color: {active_hex}; font-size: 12px; font-weight: 600; padding: 4px 12px; "
                f"border-radius: 12px; background-color: #171c26; border: 1px solid {active_hex};"
            )

        for col_key, btn in self.color_buttons.items():
            label, hex_color = self._palette_info.get(col_key, (col_key, "#ffffff"))
            if b_level == 0 and col_key == "off":
                btn.setText(f"  {label}   ✓")
                btn.setStyleSheet(
                    "background-color: #242c3d; "
                    "border: 2px solid #718096; "
                    "color: #ffffff; "
                    "font-weight: 700; "
                    "border-radius: 8px; "
                    "padding: 8px 14px; "
                    "text-align: left;"
                )
            elif b_level > 0 and col_key == active_col:
                btn.setText(f"  {label}   ✓")
                btn.setStyleSheet(
                    f"background-color: #242c3d; "
                    f"border: 2px solid {hex_color}; "
                    f"color: #ffffff; "
                    f"font-weight: 700; "
                    f"border-radius: 8px; "
                    f"padding: 8px 14px; "
                    f"text-align: left;"
                )
            else:
                btn.setText(f"  {label}")
                btn.setStyleSheet(
                    "background-color: #1c212d; "
                    "border: 1px solid #2f3a4e; "
                    "color: #cbd5e0; "
                    "font-weight: 500; "
                    "border-radius: 8px; "
                    "padding: 8px 14px; "
                    "text-align: left;"
                )

        b_level = self.cfg.get("brightness", 2)
        if b_level in self.brightness_buttons:
            self.brightness_buttons[b_level].setChecked(True)

    def reload_from_config(self) -> None:
        """Synchronize UI with latest on-disk config without hardware writes."""
        self.cfg = load_config()
        self._refresh_keyboard_support()

        # Update Colour buttons and header badge
        self._highlight_active()

        # Update Brightness buttons
        b_level = self.cfg.get("brightness", 2)
        if b_level in self.brightness_buttons:
            self.brightness_buttons[b_level].setChecked(True)

        # Update Idle Timeout without re-firing signal
        current_timeout = self.cfg.get("idle_timeout_sec", 60)
        if not self.cfg.get("idle_off_enabled", True):
            current_timeout = 0

        self.idle_combo.blockSignals(True)
        for i, (_, sec) in enumerate(self.idle_options):
            if sec == current_timeout:
                self.idle_combo.setCurrentIndex(i)
                break
        self.idle_combo.blockSignals(False)
