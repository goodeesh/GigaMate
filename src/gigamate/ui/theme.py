"""Material Design 3 (Warm Obsidian) design tokens for GigaMate."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class M3Tokens:
    """Immutable Warm Obsidian Material Design 3 design tokens."""

    # Surfaces (5-level borderless tonal elevation)
    surface: str = "#141210"
    surface_container_low: str = "#1B1816"
    surface_container: str = "#221E1B"
    surface_container_high: str = "#2C2723"
    surface_container_highest: str = "#36302B"
    surface_disabled: str = "#181513"

    # Brand & Accent
    primary: str = "#FFB598"
    on_primary: str = "#4E1E08"
    primary_container: str = "#5C2A14"
    on_primary_container: str = "#FFDBCD"
    primary_hover: str = "#6E3319"
    accent_cta: str = "#E8612C"
    accent_cta_hover: str = "#F4723D"
    accent_cta_pressed: str = "#CC5120"
    on_accent_cta: str = "#FFFFFF"

    # Text & Outlines
    on_surface: str = "#F2ECE7"
    on_surface_variant: str = "#B9AEA6"
    on_surface_muted: str = "#857A72"
    on_surface_disabled: str = "#635B55"
    outline: str = "#6E635B"
    outline_variant: str = "#423A35"
    divider: str = "#312B27"

    # Semantic fg / container / border pairs
    success: str = "#7FD8A8"
    success_container: str = "#182E23"
    success_border: str = "#28503B"

    gated: str = "#70D6CC"
    gated_container: str = "#152D2C"
    gated_border: str = "#245250"

    warning: str = "#F5BD6E"
    warning_container: str = "#332412"
    warning_border: str = "#5E411D"

    error: str = "#FFB4AB"
    error_container: str = "#3D1C1A"
    error_border: str = "#6E2E2A"

    danger_cta: str = "#C93B32"
    danger_cta_hover: str = "#DC4B42"

    info: str = "#9AC8F5"
    info_container: str = "#182534"
    info_border: str = "#2B435E"

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
    radius_card: int = 18
    radius_tile: int = 14
    radius_control: int = 12
    radius_chip: int = 10
    radius_pill: int = 999
    page_margin: int = 24
    card_spacing: int = 16
    card_padding: int = 20


THEME = M3Tokens()


def rgba(hex_color: str, alpha: float) -> str:
    """Convert a ``#RRGGBB`` hex color and alpha [0.0, 1.0] into CSS ``rgba(...)``."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Expected #RRGGBB hex color, got {hex_color!r}")
    r = int(cleaned[0:2], 16)
    g = int(cleaned[2:4], 16)
    b = int(cleaned[4:6], 16)
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
