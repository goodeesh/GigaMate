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
    background-color: #171b24;
    border-right: 1px solid #232938;
}

QLabel#AppTitle {
    color: #ffffff;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 18px 12px 6px 12px;
}

QLabel#AppSubtitle {
    color: #718096;
    font-size: 11px;
    font-weight: 500;
    padding: 0px 12px 18px 12px;
}

QPushButton.NavButton {
    background-color: transparent;
    color: #a0aec0;
    text-align: left;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    margin: 3px 10px;
    border: none;
}

QPushButton.NavButton:hover {
    background-color: #202634;
    color: #ffffff;
}

QPushButton.NavButton:checked {
    background-color: #ff6b35;
    color: #ffffff;
    font-weight: 600;
}

/* ────────────────────────────────────────────
 * Elevated Cards
 * ──────────────────────────────────────────── */

QFrame.Card {
    background-color: #1c212d;
    border: 1px solid #293042;
    border-radius: 12px;
    padding: 18px;
}

QFrame.Card:hover {
    border-color: #38425b;
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

/* ────────────────────────────────────────────
 * Sliders & Progress Bars
 * ──────────────────────────────────────────── */

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
    margin: -6px 0;
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
    border-color: #ff6b35;
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
