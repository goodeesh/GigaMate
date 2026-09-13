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

        c_layout.addLayout(grid)
        layout.addWidget(colour_card)

        # ── Brightness & Energy Saver Card ──
        opts_card = QFrame()
        opts_card.setProperty("class", "Card")
        o_layout = QVBoxLayout(opts_card)
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

        # Startup Integration Checkbox
        self.chk_startup_apply = QCheckBox("Apply RGB and profile settings automatically on login / startup")
        self.chk_startup_apply.setChecked(self.cfg.get("startup_apply", True))
        self.chk_startup_apply.toggled.connect(self._on_startup_apply_changed)
        o_layout.addWidget(self.chk_startup_apply)

        layout.addWidget(opts_card)
        layout.addStretch()

        self._highlight_active()

    def _set_colour(self, colour: str) -> None:
        self.cfg["colour"] = colour
        save_config(self.cfg)
        self._apply_hardware()
        self._highlight_active()

    def _set_brightness(self, level: int) -> None:
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

    def _on_startup_apply_changed(self, checked: bool) -> None:
        self.cfg["startup_apply"] = checked
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
        self._set_brightness(0)

    def _highlight_active(self) -> None:
        active_col = self.cfg.get("colour", "light_purple")
        active_label, active_hex = self._palette_info.get(
            active_col, (active_col.replace("_", " ").title(), "#b794f4")
        )

        # Update Header Badge
        self.active_color_badge.setText(f"Active: {active_label}")
        self.active_color_badge.setStyleSheet(
            f"color: {active_hex}; font-size: 12px; font-weight: 600; padding: 4px 12px; "
            f"border-radius: 12px; background-color: #171c26; border: 1px solid {active_hex};"
        )

        for col_key, btn in self.color_buttons.items():
            label, hex_color = self._palette_info.get(col_key, (col_key, "#ffffff"))
            if col_key == active_col:
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
                    f"background-color: #1c212d; "
                    f"border: 1px solid #2f3a4e; "
                    f"color: #cbd5e0; "
                    f"font-weight: 500; "
                    f"border-radius: 8px; "
                    f"padding: 8px 14px; "
                    f"text-align: left;"
                )

        b_level = self.cfg.get("brightness", 2)
        if b_level in self.brightness_buttons:
            self.brightness_buttons[b_level].setChecked(True)

    def reload_from_config(self) -> None:
        """Synchronize UI with latest on-disk config without hardware writes."""
        self.cfg = load_config()

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

        # Update Startup Apply
        self.chk_startup_apply.blockSignals(True)
        self.chk_startup_apply.setChecked(self.cfg.get("startup_apply", True))
        self.chk_startup_apply.blockSignals(False)
