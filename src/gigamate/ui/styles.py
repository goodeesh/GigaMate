"""GigaMate Center — UI Stylesheet and Themes.

Engineered to look cohesive and native across KDE Plasma, GNOME, and Hyprland
by using neutral dark slate tones, sleek cards, rounded corners, and crisp typography.
"""

DARK_THEME = """
/* ────────────────────────────────────────────
 * Base Window & Controls
 * ──────────────────────────────────────────── */

QMainWindow, QWidget#CentralWidget {
    background-color: #0f1218;
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QStackedWidget#ContentStack {
    background-color: #0f1218;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

/* Scroll areas and the pages inside them must not paint the desktop palette's
 * base colour. Under a light palette (e.g. GNOME with a light GTK theme) an
 * unstyled viewport renders white behind every card, so pin the colours here
 * instead of trusting the system palette. */
QScrollArea > QWidget#qt_scrollarea_viewport {
    background-color: #0f1218;
}

QScrollArea > QWidget > QWidget {
    background-color: transparent;
}

/* ────────────────────────────────────────────
 * Scrollbars (GNOME-style: thin, no arrow buttons)
 * ──────────────────────────────────────────── */

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
    border: none;
}

QScrollBar::handle:vertical {
    background-color: #2b364c;
    min-height: 32px;
    margin: 2px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover,
QScrollBar::handle:vertical:pressed {
    background-color: #465873;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
    border: none;
}

QScrollBar::handle:horizontal {
    background-color: #2b364c;
    min-width: 32px;
    margin: 2px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover,
QScrollBar::handle:horizontal:pressed {
    background-color: #465873;
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
    border: none;
    background: none;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ────────────────────────────────────────────
 * Sidebar Navigation
 * ──────────────────────────────────────────── */

QWidget#Sidebar {
    background-color: #131720;
    border-right: 1px solid #1e2636;
}

QWidget#BrandHeader {
    padding: 20px 16px 14px 16px;
}

QLabel#AppTitle {
    color: #ffffff;
    font-size: 19px;
    font-weight: 800;
    letter-spacing: 0.5px;
}

QLabel#AppSubtitle {
    color: #ff8c42;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    margin-top: 1px;
}

QPushButton.NavButton {
    background-color: transparent;
    color: #94a3b8;
    text-align: left;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    margin: 2px 10px;
    border: none;
    border-left: 3px solid transparent;
}

QPushButton.NavButton:hover {
    background-color: #1a202c;
    color: #ffffff;
}

QPushButton.NavButton:checked {
    background-color: #21293a;
    color: #ff8c42;
    font-weight: 600;
    border-left: 3px solid #ff6b35;
}

QWidget#SidebarFooter {
    border-top: 1px solid #1e2636;
    padding: 14px 16px;
    background-color: #10141d;
}

/* ────────────────────────────────────────────
 * Elevated Cards, Tiles & Headers
 * ──────────────────────────────────────────── */

QFrame.Card {
    background-color: #171b26;
    border: 1px solid #232c3d;
    border-radius: 12px;
    padding: 20px;
}

QFrame.Card:hover {
    border-color: #2e3a50;
}

QFrame.MetricTile {
    background-color: #121620;
    border: 1px solid #202838;
    border-radius: 10px;
}

QFrame.MetricTile:hover {
    border-color: #313e56;
    background-color: #151a26;
}

QFrame.StatusChip {
    background-color: #121620;
    border: 1px solid #202838;
    border-radius: 8px;
}

QFrame.StatusChip:hover {
    border-color: #2b364c;
}

QLabel.CardTitle {
    color: #ffffff;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.2px;
    padding-bottom: 2px;
}

QLabel.CardSubtitle {
    color: #8896ab;
    font-size: 12px;
    line-height: 1.4;
    padding-bottom: 8px;
}

QLabel.SectionHeader {
    color: #e2e8f0;
    font-size: 13px;
    font-weight: 600;
}

QLabel.SectionSubtext {
    color: #718096;
    font-size: 11px;
    line-height: 1.3;
}

/* Status Badges */
QLabel.BadgeSuccess {
    color: #48bb78;
    background-color: #0f2d1e;
    border: 1px solid #1c5236;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

QLabel.BadgeWarning {
    color: #ed8936;
    background-color: #361e0e;
    border: 1px solid #6b3a16;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

QLabel.BadgeNeutral {
    color: #94a3b8;
    background-color: #161b26;
    border: 1px solid #293448;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
}

QLabel.ValueReadoutPill {
    color: #ffffff;
    background-color: #121620;
    border: 1px solid #252f42;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 700;
}

/* ────────────────────────────────────────────
 * Buttons
 * ──────────────────────────────────────────── */

QPushButton {
    background-color: #202736;
    color: #f7fafc;
    border: 1px solid #2f3a50;
    border-radius: 7px;
    padding: 8px 16px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #293347;
    border-color: #425270;
}

QPushButton:pressed {
    background-color: #171d28;
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
    background-color: #1a202c;
    color: #cbd5e0;
    border: 1px solid #2b3548;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 13px;
    font-weight: 500;
    text-align: center;
}

QPushButton.ProfileButton:hover {
    background-color: #232c3d;
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
    background-color: #1a202c;
    color: #cbd5e0;
    border: 1px solid #2b3548;
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 500;
    text-align: center;
}

QPushButton.PresetButton:hover {
    background-color: #232c3d;
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
    background-color: #1e2534;
    color: #cbd5e0;
    border: 1px solid #2d384d;
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: 500;
}

QPushButton.SegmentButton:hover {
    background-color: #283246;
    color: #ffffff;
}

QPushButton.SegmentButton:checked {
    background-color: #ff6b35;
    color: #ffffff;
    border: 1px solid #ffa066;
    font-weight: 700;
}

QPushButton.ColorPaletteBtn {
    background-color: #1c222f;
    color: #f7fafc;
    border: 1px solid #2d374a;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 500;
    text-align: left;
}

QPushButton.ColorPaletteBtn:hover {
    background-color: #262f40;
    border-color: #42526e;
}

QPushButton.ColorPaletteBtn:checked {
    background-color: #273042;
    border: 2px solid #ff6b35;
    color: #ffffff;
    font-weight: 700;
}

/* ────────────────────────────────────────────
 * Sliders & Progress Bars
 * ──────────────────────────────────────────── */

QSlider {
    min-height: 26px;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #252e3e;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff8c42, stop:1 #ff6b35);
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #ff6b35;
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background: #fff5f0;
    border: 2px solid #ff8c42;
}

QProgressBar {
    background-color: #1b212d;
    border: 1px solid #283244;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    font-size: 11px;
    font-weight: 600;
    height: 16px;
}

QProgressBar::chunk {
    background-color: #38a169;
    border-radius: 5px;
}

/* ────────────────────────────────────────────
 * Tables & Lists
 * ──────────────────────────────────────────── */

QTableWidget {
    background-color: #161a24;
    border: 1px solid #242c3d;
    border-radius: 8px;
    gridline-color: #1e2636;
    color: #e2e8f0;
    selection-background-color: #293448;
}

QHeaderView::section {
    background-color: #1a202c;
    color: #94a3b8;
    padding: 8px;
    border: none;
    font-weight: 600;
}

/* ────────────────────────────────────────────
 * Checkboxes & Comboboxes
 * ──────────────────────────────────────────── */

QCheckBox {
    color: #e2e8f0;
    font-weight: 500;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #3c4960;
    background-color: #161b26;
}

QCheckBox::indicator:hover {
    border-color: #556685;
}

QCheckBox::indicator:checked {
    background-color: #ff6b35;
    border: 1px solid #ff6b35;
    image: url(__CHECKBOX_ICON_URL__);
}

QComboBox {
    background-color: #1f2635;
    color: #f7fafc;
    border: 1px solid #313d54;
    border-radius: 6px;
    padding: 6px 12px;
}

QComboBox:hover {
    border-color: #455574;
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


def build_dark_palette():
    """Return the dark :class:`QPalette` that backs the stylesheet.

    The stylesheet only paints the widgets it names. Everything else — scroll
    area viewports, scrollbars, menus, tooltips, the setup wizard — is drawn
    from the application palette, which Qt takes from the desktop (GTK theme
    on GNOME). A light desktop palette therefore leaked white panels into a
    dark app when moving from KDE to GNOME, so the palette is pinned here.
    """
    from PyQt6.QtGui import QColor, QPalette

    bg = QColor("#0f1218")
    surface = QColor("#171b26")
    text = QColor("#e2e8f0")
    muted = QColor("#64748b")

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: bg,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: QColor("#121620"),
        QPalette.ColorRole.AlternateBase: surface,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: QColor("#202736"),
        QPalette.ColorRole.ButtonText: QColor("#f7fafc"),
        QPalette.ColorRole.BrightText: QColor("#ff6b35"),
        QPalette.ColorRole.Link: QColor("#ff8c42"),
        QPalette.ColorRole.Highlight: QColor("#ff6b35"),
        QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
        QPalette.ColorRole.ToolTipBase: QColor("#1b212d"),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: muted,
        QPalette.ColorRole.Light: QColor("#2a3345"),
        QPalette.ColorRole.Midlight: QColor("#232c3d"),
        QPalette.ColorRole.Mid: QColor("#1e2636"),
        QPalette.ColorRole.Dark: surface,
        QPalette.ColorRole.Shadow: QColor("#10141d"),
    }
    for role, color in roles.items():
        palette.setColor(QPalette.ColorGroup.Normal, role, color)
        # Keep inactive identical (no washed-out text on unfocused windows).
        palette.setColor(QPalette.ColorGroup.Inactive, role, color)

    # Disabled controls: dimmed but readable.
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, bg)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, surface)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,
                     QColor("#2b364c"))
    return palette


def apply_theme(app) -> None:
    """Apply GigaMate's dark theme independently of the desktop's settings.

    Must run before any window is created. Forcing the Fusion style keeps
    widget metrics identical on KDE, GNOME and Hyprland, and declaring a dark
    colour scheme asks the platform theme (window decorations, native file
    dialogs) to follow the app instead of a desktop that reports light mode.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QStyleFactory

    style = QStyleFactory.create("Fusion")
    if style is not None:
        app.setStyle(style)

    hints = app.styleHints()
    set_color_scheme = getattr(hints, "setColorScheme", None)  # Qt 6.8+
    if set_color_scheme is not None:
        set_color_scheme(Qt.ColorScheme.Dark)

    app.setPalette(build_dark_palette())
    app.setStyleSheet(DARK_THEME)
