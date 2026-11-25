"""Aero Instrument design tokens for GigaMate."""

from __future__ import annotations

from dataclasses import dataclass


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


THEME = M3Tokens()

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


_TONE_MAP: dict[str, tuple[str, str, str]] = {
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


def tone_colors(tone: str) -> tuple[str, str, str]:
    """Return ``(fg, container, border)`` hex colors for a semantic tone."""
    return _TONE_MAP.get(tone, _TONE_MAP["neutral"])
