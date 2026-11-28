"""GigaMate Center — Material Design 3 (Warm Obsidian) Stylesheet and Themes.

Engineered to look cohesive and native across KDE Plasma, GNOME, and Hyprland
using Warm Obsidian tonal surfaces, pill controls, and crisp typography.
"""

from __future__ import annotations

from typing import Optional

from ..paths import resolve_icon
from .theme import THEME, accent_tokens, rgba

_CHECKBOX_ICON_URL = resolve_icon("checkbox-checked").replace("\\", "/")


def build_stylesheet(accent_hex: Optional[str] = None) -> str:
    """Return the Aero Instrument QSS stylesheet for the given accent color."""
    t = accent_tokens(accent_hex or THEME.primary)
    return f"""
/* ────────────────────────────────────────────
 * Base Window, Dialogs & Controls
 * ──────────────────────────────────────────── */

QMainWindow, QDialog, QWizard, QMessageBox, QWidget#CentralWidget {{
    background-color: {THEME.surface};
    color: {THEME.on_surface};
    font-family: {THEME.font_family};
    font-size: {THEME.text_body}px;
}}

QStackedWidget#ContentStack {{
    background-color: {THEME.surface};
}}

QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget#qt_scrollarea_viewport {{
    background-color: {THEME.surface};
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

/* ────────────────────────────────────────────
 * Scrollbars (thin, pill handles, no arrows)
 * ──────────────────────────────────────────── */

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {THEME.outline};
    min-height: 32px;
    margin: 2px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical:hover,
QScrollBar::handle:vertical:pressed {{
    background-color: {THEME.on_surface_muted};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {THEME.outline};
    min-width: 32px;
    margin: 2px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover,
QScrollBar::handle:horizontal:pressed {{
    background-color: {THEME.on_surface_muted};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ────────────────────────────────────────────
 * Sidebar Navigation
 * ──────────────────────────────────────────── */

QWidget#Sidebar {{
    background-color: {THEME.surface_container_low};
    border-right: 1px solid {THEME.outline_variant};
}}

QFrame#TopInstrumentBar {{
    background-color: {THEME.surface_container_low};
    border-bottom: 1px solid {THEME.outline_variant};
}}

QWidget#BrandHeader {{
    padding: 20px 16px 14px 16px;
}}

QLabel#AppTitle {{
    color: {THEME.on_surface};
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 0.4px;
}}

QLabel#AppSubtitle {{
    color: {t['accent']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    margin-top: 1px;
}}

QPushButton.NavButton {{
    background-color: transparent;
    color: {THEME.on_surface_variant};
    text-align: center;
    min-height: 54px;
    padding: 0px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    margin: 2px 2px;
    border: 1px solid transparent;
}}

QPushButton.NavButton:hover {{
    background-color: {rgba(THEME.on_surface, 0.06)};
    border: 1px solid {THEME.outline_variant};
    color: {THEME.on_surface};
}}

QPushButton.NavButton:checked {{
    background-color: {t['accent_container']};
    color: {t['on_accent_container']};
    border: 1px solid {t['accent']};
    font-weight: 700;
}}

QWidget#SidebarFooter {{
    border-top: 1px solid {THEME.divider};
    padding: 8px 0px 0px 0px;
    background-color: transparent;
}}

/* ────────────────────────────────────────────
 * Elevated Cards, Tiles & Headers
 * ──────────────────────────────────────────── */

QFrame.Card, QFrame.M3Card {{
    background-color: {THEME.surface_container};
    border: 1px solid {THEME.outline_variant};
    border-radius: {THEME.radius_card}px;
    padding: 0px;
}}

QFrame.MetricTile, QFrame.StatusChip {{
    background-color: {THEME.surface_container_high};
    border: 1px solid {THEME.outline_variant};
    border-radius: {THEME.radius_tile}px;
}}

QPushButton.LinkPillButton {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface_variant};
    border: 1px solid {THEME.outline_variant};
    border-radius: 8px;
    padding: 4px 10px;
    font-family: {THEME.mono_family};
    font-size: 11px;
    font-weight: 600;
}}

QPushButton.LinkPillButton:hover {{
    background-color: {THEME.surface_container_highest};
    color: {THEME.on_surface};
    border-color: {t['accent']};
}}

QLabel.CardTitle {{
    color: {THEME.on_surface};
    font-size: {THEME.text_card_title}px;
    font-weight: 700;
    letter-spacing: 0.2px;
    padding-bottom: 2px;
}}

QLabel.CardSubtitle {{
    color: {THEME.on_surface_variant};
    font-size: {THEME.text_caption}px;
    line-height: 1.4;
    padding-bottom: 8px;
}}

QLabel.SectionLabel {{
    color: {THEME.on_surface_variant};
    font-size: {THEME.text_overline}px;
    font-weight: 700;
    letter-spacing: 0.6px;
}}

/* ────────────────────────────────────────────
 * M3SwitchRow
 * ──────────────────────────────────────────── */

QFrame.M3SwitchRow {{
    background-color: transparent;
    border-radius: {THEME.radius_control}px;
}}

QFrame.M3SwitchRow[clickable="true"]:hover {{
    background-color: {rgba(THEME.on_surface, 0.04)};
}}

/* ────────────────────────────────────────────
 * Connected M3SegmentedBar & M3SegmentButton
 * ──────────────────────────────────────────── */

QFrame.M3SegmentedBar {{
    background-color: {THEME.surface_container_high};
    border: 1px solid {THEME.outline_variant};
    border-radius: 18px;
}}

QPushButton.M3SegmentButton {{
    background-color: transparent;
    color: {THEME.on_surface_variant};
    border: none;
    border-right: 1px solid {THEME.outline_variant};
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    border-radius: 0px;
}}

QPushButton.M3SegmentButton[segPos="only"] {{
    border-right: none;
    border-radius: 17px;
}}

QPushButton.M3SegmentButton[segPos="first"] {{
    border-top-left-radius: 17px;
    border-bottom-left-radius: 17px;
}}

QPushButton.M3SegmentButton[segPos="middle"] {{
    border-radius: 0px;
}}

QPushButton.M3SegmentButton[segPos="last"] {{
    border-right: none;
    border-top-right-radius: 17px;
    border-bottom-right-radius: 17px;
}}

QPushButton.M3SegmentButton:hover {{
    background-color: {rgba(THEME.on_surface, 0.06)};
    color: {THEME.on_surface};
}}

QPushButton.M3SegmentButton:checked {{
    background-color: {t['accent_container']};
    color: {t['on_accent_container']};
    font-weight: 600;
}}

QPushButton.M3SegmentButton:checked:hover {{
    background-color: {t['accent_soft']};
    color: {t['on_accent_container']};
}}

QFrame.M3SegmentedBar:disabled {{
    background-color: {THEME.surface_disabled};
    border-color: {THEME.divider};
}}

QPushButton.M3SegmentButton:disabled,
QPushButton.M3SegmentButton[locked="true"] {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
    font-weight: 600;
}}

QPushButton.M3SegmentButton:checked:disabled {{
    background-color: {THEME.surface_container_highest};
    color: {THEME.on_surface_disabled};
}}

/* ────────────────────────────────────────────
 * Standard & Semantic Buttons
 * ──────────────────────────────────────────── */

QPushButton {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface};
    border: none;
    min-height: 20px;
    border-radius: 16px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: {THEME.surface_container_highest};
}}

QPushButton:pressed {{
    background-color: {THEME.surface_container};
}}

QPushButton:disabled {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
}}

QPushButton.PrimaryButton {{
    background-color: {t['accent']};
    color: {t['on_accent']};
}}

QPushButton.PrimaryButton:hover {{
    background-color: {t['accent_hover']};
}}

QPushButton.PrimaryButton:pressed {{
    background-color: {t['accent_pressed']};
}}

QPushButton.PrimaryButton:disabled {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.divider};
}}

QPushButton.TonalButton {{
    background-color: {t['accent_container']};
    color: {t['on_accent_container']};
}}

QPushButton.TonalButton:hover {{
    background-color: {t['accent_soft']};
}}

QPushButton.TonalButton:disabled {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.divider};
}}

QPushButton.OutlinedButton {{
    background-color: transparent;
    color: {THEME.on_surface};
    border: 1px solid {THEME.outline};
}}

QPushButton.OutlinedButton:hover {{
    background-color: {rgba(THEME.on_surface, 0.06)};
}}

QPushButton.OutlinedButton:disabled {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.outline_variant};
}}

QPushButton.DangerButton {{
    background-color: {THEME.danger_cta};
    color: #FFFFFF;
}}

QPushButton.DangerButton:hover {{
    background-color: {THEME.danger_cta_hover};
}}

QPushButton.DangerButton:disabled {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.divider};
}}

QPushButton.DangerTonalButton {{
    background-color: {THEME.error_container};
    color: {THEME.error};
    border: 1px solid {THEME.error_border};
}}

QPushButton.DangerTonalButton:hover {{
    background-color: {THEME.error_border};
}}

QPushButton.DangerTonalButton:disabled {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.outline_variant};
}}

QPushButton.SuccessButton {{
    background-color: {THEME.success_container};
    color: {THEME.success};
    border: 1px solid {THEME.success_border};
}}

QPushButton.SuccessButton:hover {{
    background-color: {THEME.success_border};
}}

QPushButton.SuccessButton:disabled {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.outline_variant};
}}

QPushButton.PresetButton,
QPushButton.ProfileButton,
QPushButton.SegmentButton {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface_variant};
    border: 1px solid transparent;
    border-radius: 16px;
    padding: 8px 14px;
    font-weight: 600;
}}

QPushButton.PresetButton:hover,
QPushButton.ProfileButton:hover,
QPushButton.SegmentButton:hover {{
    background-color: {THEME.surface_container_highest};
    color: {THEME.on_surface};
}}

QPushButton.PresetButton:checked,
QPushButton.ProfileButton:checked,
QPushButton.SegmentButton:checked {{
    background-color: {t['accent_container']};
    color: {t['on_accent_container']};
    border: 1px solid {t['accent']};
    font-weight: 600;
}}

QPushButton.PresetButton:disabled {{
    background-color: {THEME.surface_disabled};
    color: {THEME.on_surface_disabled};
    border: 1px solid {THEME.outline_variant};
}}

QPushButton.PresetButton:checked:disabled {{
    background-color: {THEME.surface_container_highest};
    color: {THEME.on_surface_disabled};
    border-color: {THEME.divider};
}}

QPushButton.IconHelpBtn {{
    background-color: {THEME.surface_container_high};
    border: none;
    min-height: 24px;
    max-height: 24px;
    border-radius: 12px;
    padding: 0px;
}}

QPushButton.IconHelpBtn:hover {{
    background-color: {THEME.surface_container_highest};
}}

QPushButton.ColorPaletteBtn {{
    border-radius: 12px;
    border: 1px solid {THEME.outline_variant};
    padding: 0;
}}

/* ────────────────────────────────────────────
 * Sliders (Warm Obsidian M3)
 * ──────────────────────────────────────────── */

QSlider::groove:horizontal {{
    background: {THEME.surface_container_highest};
    height: 6px;
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {t['accent']};
    height: 6px;
    border-radius: 3px;
}}

QSlider::sub-page:horizontal:disabled {{
    background: {THEME.outline_variant};
}}

QSlider::handle:horizontal {{
    background: {t['accent']};
    border: none;
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}

QSlider::handle:horizontal:hover {{
    background: {t['accent_hover']};
}}

QSlider::handle:horizontal:disabled {{
    background: {THEME.on_surface_disabled};
}}

/* ────────────────────────────────────────────
 * ComboBox, CheckBox, ToolTip
 * ──────────────────────────────────────────── */

QComboBox {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface};
    border: 1px solid {THEME.outline_variant};
    border-radius: {THEME.radius_control}px;
    padding: 7px 28px 7px 12px;
    min-height: 22px;
}}

QComboBox:hover {{
    border-color: {THEME.outline};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
}}

QLabel.ValueReadoutPill {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface};
    border: 1px solid {THEME.outline_variant};
    border-radius: 10px;
    padding: 4px 10px;
    font-family: {THEME.mono_family};
    font-size: 11px;
    font-weight: 700;
}}

QComboBox QAbstractItemView {{
    background-color: {THEME.surface_container_high};
    color: {THEME.on_surface};
    border: 1px solid {THEME.outline_variant};
    border-radius: {THEME.radius_control}px;
    selection-background-color: {t['accent_container']};
    selection-color: {t['on_accent_container']};
    padding: 4px;
}}

QCheckBox {{
    color: {THEME.on_surface};
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1.5px solid {THEME.outline};
    background-color: {THEME.surface_container_highest};
}}

QCheckBox::indicator:checked {{
    background-color: {t['accent']};
    border-color: {t['accent']};
    image: url("{_CHECKBOX_ICON_URL}");
}}

QCheckBox::indicator:disabled {{
    background-color: {THEME.surface_disabled};
    border-color: {THEME.outline_variant};
}}

QToolTip {{
    background-color: {THEME.surface_container_highest};
    color: {THEME.on_surface};
    border: 1px solid {THEME.outline_variant};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 12px;
}}
"""


DARK_THEME = build_stylesheet()


def dialog_stylesheet() -> str:
    """Return the active Aero Instrument stylesheet for standalone dialogs."""
    return build_stylesheet()


def build_palette(accent_hex: Optional[str] = None):
    """Return the active Aero Instrument :class:`QPalette` (Dark or Light)."""
    from PyQt6.QtGui import QColor, QPalette

    t = accent_tokens(accent_hex or THEME.primary)
    bg = QColor(THEME.surface)
    surface = QColor(THEME.surface_container)
    text = QColor(THEME.on_surface)
    muted = QColor(THEME.on_surface_disabled)

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: bg,
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: QColor(THEME.surface_container_low),
        QPalette.ColorRole.AlternateBase: surface,
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: QColor(THEME.surface_container_high),
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.BrightText: QColor(t['accent']),
        QPalette.ColorRole.Link: QColor(t['accent']),
        QPalette.ColorRole.Highlight: QColor(t['accent']),
        QPalette.ColorRole.HighlightedText: QColor("#FFFFFF"),
        QPalette.ColorRole.ToolTipBase: QColor(THEME.surface_container_highest),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: QColor(THEME.on_surface_muted),
        QPalette.ColorRole.Light: QColor(THEME.surface_container_highest),
        QPalette.ColorRole.Midlight: QColor(THEME.surface_container_high),
        QPalette.ColorRole.Mid: QColor(THEME.outline_variant),
        QPalette.ColorRole.Dark: surface,
        QPalette.ColorRole.Shadow: QColor(THEME.outline_variant),
    }
    for role, color in roles.items():
        palette.setColor(QPalette.ColorGroup.Normal, role, color)
        palette.setColor(QPalette.ColorGroup.Inactive, role, color)

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, muted)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, bg)
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Button,
        QColor(THEME.surface_disabled),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Highlight,
        QColor(THEME.outline_variant),
    )
    return palette


build_dark_palette = build_palette


def apply_theme(
    app,
    accent_hex: Optional[str] = None,
    *,
    scheme: Optional[str] = None,
    follow_system: bool = False,
) -> None:
    """Apply GigaMate's Aero Instrument theme (Dark or Light)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QStyleFactory
    from .theme import get_active_scheme, set_active_scheme

    if scheme is not None:
        set_active_scheme(scheme)

    style = QStyleFactory.create("Fusion")
    if style is not None:
        app.setStyle(style)

    hints = app.styleHints()
    if follow_system:
        unset_cs = getattr(hints, "unsetColorScheme", None)  # Qt 6.8+
        if unset_cs is not None:
            unset_cs()
    else:
        set_cs = getattr(hints, "setColorScheme", None)  # Qt 6.8+
        if set_cs is not None:
            qt_scheme = (
                Qt.ColorScheme.Light
                if get_active_scheme() == "light"
                else Qt.ColorScheme.Dark
            )
            set_cs(qt_scheme)

    app.setPalette(build_palette(accent_hex))
    app.setStyleSheet(build_stylesheet(accent_hex))
