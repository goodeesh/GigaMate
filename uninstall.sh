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

# NOTE: avoid `producer | grep -q` under `set -o pipefail`: grep -q exits on the
# first match, the producer gets SIGPIPE (141), and the pipeline reports
# failure — so the condition is false even on a match. Read /proc/modules and
# capture command output instead.
if grep -q '^gigamate_acpi ' /proc/modules 2>/dev/null; then
    info "Unloading gigamate_acpi kernel module (needs sudo)..."
    # modprobe -r needs the .ko for the running kernel (absent when it was only
    # built for other kernels), so fall back to rmmod, which works by name.
    sudo modprobe -r gigamate_acpi 2>/dev/null || \
    sudo rmmod gigamate_acpi 2>/dev/null || true
fi

# Remove DKMS registration (auto-rebuild source)
if command -v dkms &>/dev/null; then
    dkms_entries="$(dkms status 2>/dev/null | awk -F, '/^gigamate_acpi/{print $1}' | sort -u || true)"
    if [ -n "$dkms_entries" ]; then
        info "Removing gigamate_acpi from DKMS (needs sudo)..."
        for entry in $dkms_entries; do
            sudo dkms remove "$entry" --all 2>/dev/null || true
        done
    fi
fi
# Remove DKMS build sources (left in /usr/src regardless of dkms state).
if compgen -G "/usr/src/gigamate_acpi-*" > /dev/null; then
    info "Removing DKMS sources from /usr/src (needs sudo)..."
    sudo rm -rf /usr/src/gigamate_acpi-* 2>/dev/null || true
fi

# Remove any installed module files for every kernel (manual builds too).
MODULE_FILES="$(find /lib/modules -name 'gigamate_acpi.ko*' 2>/dev/null || true)"
if [ -n "$MODULE_FILES" ]; then
    echo "$MODULE_FILES" | while IFS= read -r mod; do
        [ -n "$mod" ] && sudo rm -f "$mod"
    done
    sudo depmod -a 2>/dev/null || true
    info "Removed kernel module file(s)."
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

if [ -f /etc/udev/rules.d/99-gigamate-smartshift.rules ]; then
    sudo rm -f /etc/udev/rules.d/99-gigamate-smartshift.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    info "Removed SmartShift udev rule."
fi

# Also clean up old udev rule
if [ -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules ]; then
    sudo rm -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    info "Removed old udev rule (99-gigabyte-keyboard-rgb.rules)."
fi

# --- Remove polkit rule ---
if [ -f /etc/polkit-1/rules.d/50-gigamate-powerd.rules ]; then
    info "Removing polkit rule (needs sudo)..."
    sudo rm -f /etc/polkit-1/rules.d/50-gigamate-powerd.rules
    info "polkit rule removed."
fi

# --- Uninstall Python package ---
header "Python package"

info "Uninstalling gigamate..."

# pip --user installs need --break-system-packages on PEP 668 distros
# (Arch, recent Debian/Ubuntu); without it pip refuses and the package
# silently survives. Remove both the pip and pipx installs if present.
_uninstall_pkg() {
    pip uninstall -y --break-system-packages "$1" 2>/dev/null || \
    pip uninstall -y "$1" 2>/dev/null || true
    pipx uninstall "$1" >/dev/null 2>&1 || true
}

_uninstall_pkg gigamate
_uninstall_pkg gigabyte-keyboard-rgb

# pip does not track generated __pycache__ files, so an empty package dir can
# survive and make `import gigamate` succeed as a namespace package (breaking
# version detection). Remove any leftover tree/dist-info explicitly.
for leftover in "${HOME}/.local/lib"/python*/site-packages/gigamate \
                "${HOME}/.local/lib"/python*/site-packages/gigamate-*.dist-info; do
    if [ -e "$leftover" ]; then
        rm -rf "$leftover"
        info "Removed leftover: $leftover"
    fi
done

# --- Remove desktop entry and icon ---
header "Desktop entry and icon"

DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigamate.desktop"
CENTER_DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigamate-center.desktop"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
OLD_DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gigabyte-keyboard-rgb-tray.desktop"
OLD_ICON="${ICON_DIR}/gigabyte-keyboard-rgb.svg"

_remove_file() {
    if [ -e "$1" ]; then
        rm -f "$1" 2>/dev/null && info "Removed: $1"
    fi
}

_remove_file "$DESKTOP_FILE"
_remove_file "$CENTER_DESKTOP_FILE"
if compgen -G "$ICON_DIR/gigamate*.svg" > /dev/null; then
    rm -f "$ICON_DIR"/gigamate*.svg 2>/dev/null && info "Removed gigamate icons"
fi
_remove_file "$ICON_DIR/checkbox-checked.svg"
_remove_file "$OLD_DESKTOP"
_remove_file "$OLD_ICON"

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
