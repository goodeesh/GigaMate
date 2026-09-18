"""GigaMate Center — UI Stylesheet and Themes.

Engineered to look cohesive and native across KDE Plasma, GNOME, and Hyprland
by using neutral dark slate tones, sleek cards, rounded corners, and crisp typography.
"""

DARK_THEME = """
/* ────────────────────────────────────────────
 * Base Window & Controls
 * ──────────────────────────────────────────── */

QMainWindow, QWidget#CentralWidget {
    background-color: #12151c;
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

/* ────────────────────────────────────────────
 * Sidebar Navigation
 * ──────────────────────────────────────────── */

QWidget#Sidebar {
    background-color: #161922;
    border-right: 1px solid #232938;
}

QWidget#BrandHeader {
    padding: 18px 16px 10px 16px;
}

QLabel#AppTitle {
    color: #ffffff;
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 0.5px;
}

QLabel#AppSubtitle {
    color: #64748b;
    font-size: 11px;
    font-weight: 500;
    margin-top: 1px;
}

QPushButton.NavButton {
    background-color: transparent;
    color: #94a3b8;
    text-align: left;
    padding: 11px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    margin: 3px 10px;
    border: none;
    border-left: 3px solid transparent;
}

QPushButton.NavButton:hover {
    background-color: #1e2432;
    color: #ffffff;
}

QPushButton.NavButton:checked {
    background-color: #242c3d;
    color: #ff8c42;
    font-weight: 600;
    border-left: 3px solid #ff6b35;
}

QWidget#SidebarFooter {
    border-top: 1px solid #232938;
    padding: 14px 16px;
    background-color: #131620;
}

/* ────────────────────────────────────────────
 * Elevated Cards & Tiles
 * ──────────────────────────────────────────── */

QFrame.Card {
    background-color: #181d28;
    border: 1px solid #263042;
    border-radius: 12px;
    padding: 18px;
}

QFrame.Card:hover {
    border-color: #36435c;
}

QFrame.MetricTile {
    background-color: #131720;
    border: 1px solid #232b3d;
    border-radius: 10px;
}

QFrame.MetricTile:hover {
    border-color: #384660;
    background-color: #161b26;
}

QFrame.StatusChip {
    background-color: #131720;
    border: 1px solid #232b3d;
    border-radius: 8px;
}

QLabel.CardTitle {
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
    padding-bottom: 6px;
}

QLabel.CardSubtitle {
    color: #8896ab;
    font-size: 12px;
    padding-bottom: 12px;
}

/* ────────────────────────────────────────────
 * Buttons
 * ──────────────────────────────────────────── */

QPushButton {
    background-color: #252c3c;
    color: #f7fafc;
    border: 1px solid #364157;
    border-radius: 7px;
    padding: 8px 16px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #2f384d;
    border-color: #485675;
}

QPushButton:pressed {
    background-color: #1c222f;
}

QPushButton.PrimaryButton {
    background-color: #ff6b35;
    color: #ffffff;
    border: none;
    font-weight: 600;
}

QPushButton.PrimaryButton:hover {
    background-color: #ff7d4d;
}

QPushButton.DangerButton {
    background-color: #e53e3e;
    color: #ffffff;
    border: none;
    font-weight: 600;
}

QPushButton.DangerButton:hover {
    background-color: #f56565;
}

QPushButton.SuccessButton {
    background-color: #38a169;
    color: #ffffff;
    border: none;
    font-weight: 600;
}

QPushButton.SuccessButton:hover {
    background-color: #48bb78;
}

QPushButton.ProfileButton {
    background-color: #1d2331;
    color: #cbd5e0;
    border: 1px solid #2d384d;
    border-radius: 10px;
    padding: 8px 6px;
    font-size: 13px;
    font-weight: 500;
    text-align: center;
}

QPushButton.ProfileButton:hover {
    background-color: #252e40;
    border-color: #ff8c42;
    color: #ffffff;
}

QPushButton.ProfileButton:checked {
    background-color: #242d3e;
    color: #ffffff;
    border: 2px solid #ff6b35;
    font-weight: 700;
}

QPushButton.PresetButton {
    background-color: #1d2331;
    color: #cbd5e0;
    border: 1px solid #2d384d;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12px;
    font-weight: 500;
    text-align: center;
}

QPushButton.PresetButton:hover {
    background-color: #252e40;
    border-color: #ff8c42;
    color: #ffffff;
}

QPushButton.PresetButton:checked {
    background-color: #242d3e;
    color: #ffffff;
    border: 2px solid #ff6b35;
    font-weight: 700;
}

QPushButton.SegmentButton {
    background-color: #232a3b;
    color: #cbd5e0;
    border: 1px solid #364157;
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton.SegmentButton:hover {
    background-color: #2c364b;
    color: #ffffff;
}

QPushButton.SegmentButton:checked {
    background-color: #ff6b35;
    color: #ffffff;
    border: 2px solid #ffa066;
    font-weight: 700;
}

QPushButton.ColorPaletteBtn {
    background-color: #202634;
    color: #f7fafc;
    border: 1px solid #333d52;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 500;
    text-align: left;
}

QPushButton.ColorPaletteBtn:hover {
    background-color: #293245;
    border-color: #4a5875;
}

QPushButton.ColorPaletteBtn:checked {
    background-color: #2b3345;
    border: 2px solid #ff6b35;
    color: #ffffff;
    font-weight: 700;
}

/* ────────────────────────────────────────────
 * Sliders & Progress Bars
 * ──────────────────────────────────────────── */

QSlider {
    min-height: 24px;
}

QSlider::groove:horizontal {
    height: 8px;
    background: #272f40;
    border-radius: 4px;
}

QSlider::sub-page:horizontal {
    background: #ff6b35;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #ff6b35;
    width: 18px;
    height: 18px;
    margin: -7px 0;
    border-radius: 10px;
}

QProgressBar {
    background-color: #252c3c;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    font-weight: 600;
    height: 16px;
}

QProgressBar::chunk {
    background-color: #38a169;
    border-radius: 6px;
}

/* ────────────────────────────────────────────
 * Tables & Lists
 * ──────────────────────────────────────────── */

QTableWidget {
    background-color: #181c25;
    border: 1px solid #283040;
    border-radius: 8px;
    gridline-color: #232936;
    color: #e2e8f0;
    selection-background-color: #2d3748;
}

QHeaderView::section {
    background-color: #1f2533;
    color: #94a3b8;
    padding: 8px;
    border: none;
    font-weight: 600;
}

/* ────────────────────────────────────────────
 * Checkboxes & Comboboxes
 * ──────────────────────────────────────────── */

QCheckBox {
    color: #cbd5e0;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #4a5568;
    background-color: #1a202c;
}

QCheckBox::indicator:checked {
    background-color: #ff6b35;
    border: 1px solid #ff6b35;
    image: url(__CHECKBOX_ICON_URL__);
}

QComboBox {
    background-color: #252c3c;
    color: #f7fafc;
    border: 1px solid #364157;
    border-radius: 6px;
    padding: 6px 12px;
}

QComboBox::drop-down {
    border: none;
}
"""

from ..paths import resolve_icon
DARK_THEME = DARK_THEME.replace(
    "__CHECKBOX_ICON_URL__",
    resolve_icon("checkbox-checked").replace("\\", "/")
)
