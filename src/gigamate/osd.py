"""GigaMate — On-Screen Display (OSD) overlay.

Provides lightweight visual overlays when hotkeys are pressed or profiles change.
Supports:
1. Native KDE Plasma OSD (via org.kde.osdService)
2. Universal FreeDesktop notifications (via org.freedesktop.Notifications with synchronous/transient hints)
"""

import os
from typing import Optional

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    _HAS_GIO = True
except Exception:
    _HAS_GIO = False


def _show_kde_osd(name: str, desc: str = "", icon: str = "preferences-system-power-management") -> bool:
    """Attempt to show OSD via KDE Plasma's native osdService."""
    if not _HAS_GIO:
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            "org.kde.plasmashell",
            "/org/kde/osdService",
            "org.kde.osdService",
            None,
        )
        text = f"Power Profile: {name}"
        if desc:
            text = f"{name} ({desc})"
        proxy.call_sync(
            "showText",
            GLib.Variant("(ss)", (icon, text)),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return True
    except Exception:
        return False


def _show_freedesktop_notification(name: str, desc: str = "", icon: str = "preferences-system-power-management") -> bool:
    """Attempt to show OSD via org.freedesktop.Notifications with synchronous/transient hints."""
    if not _HAS_GIO:
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            None,
        )
        hints = {
            "x-canonical-private-synchronous": GLib.Variant("s", "gigamate-profile-osd"),
            "transient": GLib.Variant("b", True),
            "urgency": GLib.Variant("y", 1),
        }
        body = desc if desc else f"Active mode: {name}"
        proxy.call_sync(
            "Notify",
            GLib.Variant(
                "(susssasa{sv}i)",
                (
                    "GigaMate",
                    0,
                    icon,
                    f"Power Profile: {name}",
                    body,
                    [],
                    hints,
                    1500,
                ),
            ),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        return True
    except Exception:
        return False


def show_profile_osd(name: str, desc: str = "", icon: str = "preferences-system-power-management") -> bool:
    """Show an on-screen display overlay indicating the active profile.

    Tries KDE Plasma native OSD first, then falls back to universal desktop notifications.

    Args:
        name: Name of the profile (e.g. "Gaming", "Quiet", "Balanced", "Performance")
        desc: Optional description of the profile mode
        icon: Icon name to display

    Returns:
        True if an OSD or notification was successfully sent, False otherwise.
    """
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    
    # If in KDE Plasma, prefer native KDE OSD
    if "kde" in desktop or "plasma" in desktop:
        if _show_kde_osd(name, desc, icon):
            return True
        if _show_freedesktop_notification(name, desc, icon):
            return True
    else:
        # Other desktops (GNOME, Sway, Hyprland, XFCE, etc.)
        if _show_freedesktop_notification(name, desc, icon):
            return True
        if _show_kde_osd(name, desc, icon):
            return True

    return False
