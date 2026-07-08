#!/usr/bin/env bash
set -euo pipefail

NAME="gigamate"

COLOUR_GREEN='\033[0;32m'
COLOUR_YELLOW='\033[1;33m'
COLOUR_RED='\033[0;31m'
COLOUR_RESET='\033[0m'

info() { echo -e "${COLOUR_GREEN}[INFO]${COLOUR_RESET} $*"; }
warn() { echo -e "${COLOUR_YELLOW}[WARN]${COLOUR_RESET} $*"; }

header() { echo -e "\n[ $* ]\n"; }

# --- Stop and disable systemd user service ---
header "Systemd user service"

info "Stopping and disabling gigamate.service..."
systemctl --user disable --now gigamate.service 2>/dev/null || true

SERVICE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigamate.service"
if [ -f "$SERVICE" ]; then
    rm -f "$SERVICE"
    info "Removed: $SERVICE"
fi

# Also clean up old service name
OLD_SERVICE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigabyte-keyboard-rgb.service"
if [ -f "$OLD_SERVICE" ]; then
    systemctl --user disable --now gigabyte-keyboard-rgb.service 2>/dev/null || true
    rm -f "$OLD_SERVICE"
    info "Removed old service: $OLD_SERVICE"
fi

systemctl --user daemon-reload 2>/dev/null || true

# --- Remove kernel module ---
header "Kernel module"

if lsmod 2>/dev/null | grep -q gigamate_acpi; then
    info "Unloading gigamate_acpi kernel module (needs sudo)..."
    sudo modprobe -r gigamate_acpi 2>/dev/null || true
fi

MODULE_FILE="/lib/modules/$(uname -r)/extra/gigamate_acpi.ko"
if [ -f "$MODULE_FILE" ]; then
    sudo rm -f "$MODULE_FILE"
    sudo depmod -a 2>/dev/null || true
    info "Removed kernel module: $MODULE_FILE"
fi

# Remove auto-load config
LOAD_CONF="/etc/modules-load.d/gigamate_acpi.conf"
if [ -f "$LOAD_CONF" ]; then
    sudo rm -f "$LOAD_CONF"
    info "Removed auto-load config: $LOAD_CONF"
fi

# --- Remove udev rule ---
header "udev rule"

if [ -f /etc/udev/rules.d/99-gigamate.rules ]; then
    info "Removing udev rule (needs sudo)..."
    sudo rm -f /etc/udev/rules.d/99-gigamate.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    info "udev rule removed."
fi

# Also clean up old udev rule
if [ -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules ]; then
    sudo rm -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    info "Removed old udev rule (99-gigabyte-keyboard-rgb.rules)."
fi

# --- Uninstall Python package ---
header "Python package"

info "Uninstalling gigamate..."
pip uninstall -y gigamate 2>/dev/null || \
pipx uninstall gigamate 2>/dev/null || true

# Also uninstall old package
pip uninstall -y gigabyte-keyboard-rgb 2>/dev/null || \
pipx uninstall gigabyte-keyboard-rgb 2>/dev/null || true

# --- Remove desktop entry and icon ---
header "Desktop entry and icon"

DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigamate.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/gigamate.svg"
OLD_DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigabyte-keyboard-rgb-tray.desktop"
OLD_ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/gigabyte-keyboard-rgb.svg"

rm -f "$DESKTOP_FILE" 2>/dev/null && info "Removed: $DESKTOP_FILE"
rm -f "$ICON_FILE" 2>/dev/null && info "Removed: $ICON_FILE"
rm -f "$OLD_DESKTOP" 2>/dev/null && info "Removed old: $OLD_DESKTOP"
rm -f "$OLD_ICON" 2>/dev/null && info "Removed old: $OLD_ICON"

if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true
fi

# --- Config cleanup ---
header "Configuration"

echo
info "Uninstall complete."
echo
info "Configuration files left at:"
info "  ${XDG_CONFIG_HOME:-$HOME/.config}/gigamate/"
info "  ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb/ (old, if migrated)"
echo
warn "To remove configuration, run:"
warn "  rm -r ${XDG_CONFIG_HOME:-$HOME/.config}/gigamate"
warn "  rm -r ${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb"
echo
