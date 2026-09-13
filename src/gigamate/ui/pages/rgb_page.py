"""GigaMate Center — Keyboard RGB Lighting & Effects Page."""

from typing import Optional
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
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
from ...protocol import COLOUR_MAP, get_keyboard, set_off, set_static


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

        grid = QGridLayout()
        grid.setSpacing(10)

        palette = [
            ("Light Purple", "light_purple", "#b794f4"),
            ("Blush Pink", "blush_pink", "#f687b3"),
            ("Cyan", "cyan", "#4fd1c5"),
            ("Sky Blue", "blue", "#63b3ed"),
            ("Spring Green", "green", "#68d391"),
            ("Sunset Orange", "orange", "#f6ad55"),
            ("Crimson Red", "red", "#fc8181"),
            ("Pure White", "white", "#f7fafc"),
        ]

        self._palette_info = {}
        self.color_buttons = {}
        for idx, (label, col_key, hex_color) in enumerate(palette):
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

        c_layout.addLayout(grid)
        layout.addWidget(colour_card)

        # ── Brightness & Idle Sleep Card ──
        opts_card = QFrame()
        opts_card.setProperty("class", "Card")
        o_layout = QVBoxLayout(opts_card)
        o_layout.setSpacing(14)

        o_title = QLabel("Brightness & Energy Saver")
        o_title.setProperty("class", "CardTitle")
        o_layout.addWidget(o_title)

        # Brightness buttons
        b_row = QHBoxLayout()
        b_label = QLabel("Backlight Brightness:")
        b_label.setStyleSheet("color: #cbd5e0; font-weight: 500; min-width: 160px;")
        b_row.addWidget(b_label)

        self.btn_b_off = QPushButton("Off")
        self.btn_b_dim = QPushButton("Dim (50%)")
        self.btn_b_full = QPushButton("Full (100%)")

        for b_val, btn in enumerate((self.btn_b_off, self.btn_b_dim, self.btn_b_full)):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, v=b_val: self._set_brightness(v))
            b_row.addWidget(btn)

        o_layout.addLayout(b_row)

        # Idle Sleep Timeout
        idle_row = QHBoxLayout()
        idle_label = QLabel("Keyboard Idle Sleep:")
        idle_label.setStyleSheet("color: #cbd5e0; font-weight: 500; min-width: 160px;")
        idle_row.addWidget(idle_label)

        self.idle_combo = QComboBox()
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

        # Match combo
        for i, (_, sec) in enumerate(self.idle_options):
            if sec == current_timeout:
                self.idle_combo.setCurrentIndex(i)
                break

        self.idle_combo.currentIndexChanged.connect(self._on_idle_changed)
        idle_row.addWidget(self.idle_combo)
        o_layout.addLayout(idle_row)

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

    def _highlight_active(self) -> None:
        active_col = self.cfg.get("colour", "light_purple")
        active_label, active_hex = self._palette_info.get(active_col, (active_col.capitalize(), "#b794f4"))

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
        for idx, btn in enumerate((self.btn_b_off, self.btn_b_dim, self.btn_b_full)):
            if idx == b_level:
                btn.setStyleSheet("background-color: #ff6b35; color: #ffffff; font-weight: bold; border: 2px solid #ffa066;")
            else:
                btn.setStyleSheet("")
