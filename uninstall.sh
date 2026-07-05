#!/usr/bin/env bash
set -euo pipefail

NAME="gigabyte-keyboard-rgb"

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; }

info "Stopping and disabling systemd user service..."
systemctl --user disable --now gigabyte-keyboard-rgb.service 2>/dev/null || true

SERVICE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigabyte-keyboard-rgb.service"
if [ -f "$SERVICE" ]; then
    rm -f "$SERVICE"
    info "Removed systemd unit: $SERVICE"
    systemctl --user daemon-reload 2>/dev/null || true
fi

info "Removing udev rule (needs sudo)..."
if [ -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules ]; then
    sudo rm -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    info "udev rule removed."
fi

info "Uninstalling Python package..."
pip uninstall -y gigabyte-keyboard-rgb 2>/dev/null || \
pipx uninstall gigabyte-keyboard-rgb 2>/dev/null || true

info "Removing desktop entry and icon..."
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigabyte-keyboard-rgb-tray.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/gigabyte-keyboard-rgb.svg"
rm -f "$DESKTOP_FILE" 2>/dev/null && info "  Removed: $DESKTOP_FILE"
rm -f "$ICON_FILE" 2>/dev/null && info "  Removed: $ICON_FILE"
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true
fi

echo
info "Uninstall complete."
info "Config file left at: ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb/config.json"
info "  Delete it manually if desired: rm -r ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb"
