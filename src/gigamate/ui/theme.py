"""Aero Instrument design tokens (Dark & Light) for GigaMate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class M3Tokens:
    """Immutable Aero Instrument design tokens."""

    # Surfaces (precision dark zinc elevation)
    surface: str = "#0A0A0C"
    surface_container_low: str = "#0F0F13"
    surface_container: str = "#131317"
    surface_container_high: str = "#1A1A20"
    surface_container_highest: str = "#23232B"
    surface_disabled: str = "#101014"

    # Brand & Accent
    primary: str = "#14B8A6"
    on_primary: str = "#042F2E"
    primary_container: str = "#112A2A"
    on_primary_container: str = "#5EEAD4"
    primary_hover: str = "#163838"
    accent_cta: str = "#14B8A6"
    accent_cta_hover: str = "#2DD4BF"
    accent_cta_pressed: str = "#0D9488"
    on_accent_cta: str = "#042F2E"

    # Text & Outlines
    on_surface: str = "#FAFAFA"
    on_surface_variant: str = "#A1A1AA"
    on_surface_muted: str = "#71717A"
    on_surface_disabled: str = "#52525B"
    outline: str = "#3F3F4A"
    outline_variant: str = "#27272F"
    divider: str = "#27272F"

    # Semantic fg / container / border pairs
    success: str = "#10B981"
    success_container: str = "#0E261E"
    success_border: str = "#1F4D3D"

    gated: str = "#2DD4BF"
    gated_container: str = "#0E2626"
    gated_border: str = "#1F4D4A"

    warning: str = "#F59E0B"
    warning_container: str = "#2A1F0C"
    warning_border: str = "#523C14"

    error: str = "#EF4444"
    error_container: str = "#2E1315"
    error_border: str = "#5C2326"

    danger_cta: str = "#DC2626"
    danger_cta_hover: str = "#EF4444"

    info: str = "#38BDF8"
    info_container: str = "#0F2233"
    info_border: str = "#1F4260"

    # Typography
    font_family: str = (
        '"Inter", "Adwaita Sans", "Noto Sans", "Roboto", "DejaVu Sans", sans-serif'
    )
    mono_family: str = (
        '"Adwaita Mono", "JetBrains Mono", "DejaVu Sans Mono", monospace'
    )
    text_display: int = 24
    text_page_title: int = 22
    text_card_title: int = 15
    text_section: int = 13
    text_body: int = 13
    text_caption: int = 12
    text_overline: int = 11

    # Radii & Spacing
    radius_card: int = 14
    radius_tile: int = 11
    radius_control: int = 10
    radius_chip: int = 8
    radius_pill: int = 999
    page_margin: int = 20
    card_spacing: int = 14
    card_padding: int = 16


DARK_TOKENS = M3Tokens()

LIGHT_TOKENS = M3Tokens(
    # Surfaces (crisp porcelain & cool slate elevation)
    surface="#F4F5F7",
    surface_container_low="#ECEEF2",
    surface_container="#FFFFFF",
    surface_container_high="#EEF1F5",
    surface_container_highest="#E2E6EC",
    surface_disabled="#F1F3F5",

    # Brand & Accent (deepened teal for WCAG AA contrast on white)
    primary="#0D9488",
    on_primary="#FFFFFF",
    primary_container="#CCFBF1",
    on_primary_container="#115E59",
    primary_hover="#99F6E4",
    accent_cta="#0D9488",
    accent_cta_hover="#0F766E",
    accent_cta_pressed="#115E59",
    on_accent_cta="#FFFFFF",

    # Text & Outlines
    on_surface="#0F172A",
    on_surface_variant="#475569",
    on_surface_muted="#64748B",
    on_surface_disabled="#94A3B8",
    outline="#CBD5E1",
    outline_variant="#E2E8F0",
    divider="#E2E8F0",

    # Semantic fg / container / border pairs
    success="#059669",
    success_container="#ECFDF5",
    success_border="#A7F3D0",

    gated="#0D9488",
    gated_container="#F0FDFA",
    gated_border="#99F6E4",

    warning="#D97706",
    warning_container="#FFFBEB",
    warning_border="#FDE68A",

    error="#DC2626",
    error_container="#FEF2F2",
    error_border="#FECACA",

    danger_cta="#DC2626",
    danger_cta_hover="#B91C1C",

    info="#0284C7",
    info_container="#F0F9FF",
    info_border="#BAE6FD",
)

_ACTIVE_SCHEME: str = "dark"
_ACTIVE_TOKENS: M3Tokens = DARK_TOKENS


class _ThemeProxy:
    """Dynamic proxy so all ``THEME.*`` lookups reflect the active Dark/Light scheme."""

    def __getattr__(self, name: str):
        return getattr(_ACTIVE_TOKENS, name)

    def __repr__(self) -> str:
        return f"<ThemeProxy scheme={_ACTIVE_SCHEME!r}>"


THEME = _ThemeProxy()


def get_active_scheme() -> str:
    """Return the currently active color scheme (``'dark'`` or ``'light'``)."""
    return _ACTIVE_SCHEME


def is_light_theme() -> bool:
    """Return True when the active color scheme is Light."""
    return _ACTIVE_SCHEME == "light"


def set_active_scheme(scheme: str) -> str:
    """Switch the active ``THEME`` token set to ``'dark'`` or ``'light'``."""
    global _ACTIVE_SCHEME, _ACTIVE_TOKENS
    norm = "light" if str(scheme or "").strip().lower() == "light" else "dark"
    _ACTIVE_SCHEME = norm
    _ACTIVE_TOKENS = LIGHT_TOKENS if norm == "light" else DARK_TOKENS
    return norm


def _probe_portal_color_scheme() -> Optional[str]:
    """Query XDG Desktop Portal ``org.freedesktop.appearance.color-scheme``."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Settings",
            None,
        )
        res = proxy.call_sync(
            "Read",
            GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
            Gio.DBusCallFlags.NONE,
            250,
            None,
        )
        # Unpack (v) -> inner variant -> uint32 (1=prefer-dark, 2=prefer-light)
        val = res.unpack()[0]
        if hasattr(val, "unpack"):
            val = val.unpack()
        if val == 1:
            return "dark"
        if val == 2:
            return "light"
    except Exception:
        pass
    return None


def detect_system_color_scheme(app=None) -> str:
    """Detect the desktop OS color scheme (``'dark'`` or ``'light'``)."""
    # 1. Qt6 native styleHints().colorScheme() (KDE Plasma 6, GNOME, etc.)
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication

        qt_app = app or QApplication.instance()
        if qt_app is not None:
            hints = qt_app.styleHints()
            cs_fn = getattr(hints, "colorScheme", None)
            if cs_fn is not None:
                cs = cs_fn()
                if cs == Qt.ColorScheme.Dark:
                    return "dark"
                if cs == Qt.ColorScheme.Light:
                    return "light"
    except Exception:
        pass

    # 2. XDG Desktop Portal fallback (works across KDE/GNOME/Hyprland/Sway)
    portal = _probe_portal_color_scheme()
    if portal in ("dark", "light"):
        return portal

    # 3. Default to dark (Aero Instrument default)
    return "dark"


def resolve_color_scheme(preference: str, app=None) -> str:
    """Resolve user preference (``'system' | 'dark' | 'light'``) to ``'dark' | 'light'``."""
    pref = str(preference or "system").strip().lower()
    if pref == "light":
        return "light"
    if pref == "dark":
        return "dark"
    return detect_system_color_scheme(app)


PROFILE_ACCENTS: dict[int, str] = {
    0: "#3B82F6",  # Silent
    1: "#14B8A6",  # Balanced
    2: "#F59E0B",  # Performance
    3: "#EF4444",  # Turbo
}


def _parse_hex(hex_color: str) -> tuple[int, int, int]:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Expected #RRGGBB hex color, got {hex_color!r}")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def _blend_hex(fg_hex: str, bg_hex: str, alpha: float) -> str:
    fr, fg, fb = _parse_hex(fg_hex)
    br, bg, bb = _parse_hex(bg_hex)
    a = max(0.0, min(1.0, float(alpha)))
    r = round(fr * a + br * (1.0 - a))
    g = round(fg * a + bg * (1.0 - a))
    b = round(fb * a + bb * (1.0 - a))
    return f"#{r:02X}{g:02X}{b:02X}"


def _relative_luminance(hex_color: str) -> float:
    r, g, b = _parse_hex(hex_color)

    def _lin(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def accent_tokens(accent_hex: str) -> dict[str, str]:
    """Derive a cohesive accent palette from a single ``#RRGGBB`` color."""
    accent = accent_hex.strip()
    if not accent.startswith("#"):
        accent = f"#{accent}"
    _parse_hex(accent)
    on_accent = "#09090B" if _relative_luminance(accent) > 0.32 else "#FAFAFA"
    if is_light_theme():
        # Deepen very bright accents slightly for legible borders/text on light surfaces
        light_accent = (
            _blend_hex("#000000", accent, 0.14)
            if _relative_luminance(accent) > 0.35
            else accent
        )
        return {
            "accent": light_accent,
            "accent_hover": _blend_hex("#000000", light_accent, 0.14),
            "accent_pressed": _blend_hex("#000000", light_accent, 0.26),
            "accent_container": _blend_hex(light_accent, THEME.surface_container, 0.14),
            "on_accent": "#FFFFFF",
            "on_accent_container": _blend_hex("#000000", light_accent, 0.36),
            "accent_soft": _blend_hex(light_accent, THEME.surface_container_high, 0.16),
        }
    return {
        "accent": accent,
        "accent_hover": _blend_hex("#FFFFFF", accent, 0.16),
        "accent_pressed": _blend_hex("#000000", accent, 0.18),
        "accent_container": _blend_hex(accent, THEME.surface_container, 0.16),
        "on_accent": on_accent,
        "on_accent_container": _blend_hex("#FFFFFF", accent, 0.45),
        "accent_soft": _blend_hex(accent, THEME.surface_container_high, 0.22),
    }


def rgba(hex_color: str, alpha: float) -> str:
    """Convert a ``#RRGGBB`` hex color and alpha [0.0, 1.0] into CSS ``rgba(...)``."""
    r, g, b = _parse_hex(hex_color)
    clamped = max(0.0, min(1.0, float(alpha)))
    return f"rgba({r}, {g}, {b}, {clamped:.3g})"


def tone_colors(tone: str) -> tuple[str, str, str]:
    """Return ``(fg, container, border)`` hex colors for a semantic tone."""
    tones: dict[str, tuple[str, str, str]] = {
        "neutral": (
            THEME.on_surface_variant,
            THEME.surface_container_high,
            THEME.outline_variant,
        ),
        "primary": (
            THEME.on_primary_container,
            THEME.primary_container,
            THEME.primary,
        ),
        "success": (
            THEME.success,
            THEME.success_container,
            THEME.success_border,
        ),
        "gated": (
            THEME.gated,
            THEME.gated_container,
            THEME.gated_border,
        ),
        "warning": (
            THEME.warning,
            THEME.warning_container,
            THEME.warning_border,
        ),
        "error": (
            THEME.error,
            THEME.error_container,
            THEME.error_border,
        ),
        "info": (
            THEME.info,
            THEME.info_container,
            THEME.info_border,
        ),
    }
    return tones.get(tone, tones["neutral"])
