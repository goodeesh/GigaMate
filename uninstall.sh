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

echo
info "Uninstall complete."
info "Config file left at: ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb/config.json"
info "  Delete it manually if desired: rm -r ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb"
