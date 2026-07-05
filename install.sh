#!/usr/bin/env bash
set -euo pipefail

NAME="gigabyte-keyboard-rgb"
DESCRIPTION="Gigabyte Keyboard RGB Control - installer"

COLOUR_GREEN='\033[0;32m'
COLOUR_YELLOW='\033[1;33m'
COLOUR_RED='\033[0;31m'
COLOUR_RESET='\033[0m'

info() { echo -e "${COLOUR_GREEN}[INFO]${COLOUR_RESET} $*"; }
warn() { echo -e "${COLOUR_YELLOW}[WARN]${COLOUR_RESET} $*"; }
error() { echo -e "${COLOUR_RED}[ERROR]${COLOUR_RESET} $*"; }

# --- Detect distro ---
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        ID_LIKE="${ID_LIKE:-$ID}"
        echo "$ID_LIKE" | tr '[:upper:]' '[:lower:]'
    elif command -v pacman &>/dev/null; then
        echo "arch"
    elif command -v apt &>/dev/null; then
        echo "debian"
    elif command -v dnf &>/dev/null; then
        echo "fedora"
    elif command -v zypper &>/dev/null; then
        echo "suse"
    else
        echo "unknown"
    fi
}

# --- Install system dependencies ---
install_system_deps() {
    local distro
    distro=$(detect_distro)
    info "Detected distro: $distro"

    case "$distro" in
        arch|archlinux|endeavouros|cachyos)
            info "Installing Arch packages: python-pyusb python-gobject gtk3 libappindicator-gtk3"
            sudo pacman -S --needed python-pyusb python-gobject gtk3 libappindicator-gtk3
            ;;
        debian|ubuntu|pop|mint)
            info "Installing Debian/Ubuntu packages: python3-usb python3-gi python3-gi-cairo gir1.2-appindicator3-0.1 gir1.2-gtk-3.0"
            sudo apt update
            sudo apt install -y python3-usb python3-gi python3-gi-cairo gir1.2-appindicator3-0.1 gir1.2-gtk-3.0
            ;;
        fedora|rhel|centos)
            info "Installing Fedora packages: python3-pyusb python3-gobject gtk3 libappindicator-gtk3"
            sudo dnf install -y python3-pyusb python3-gobject gtk3 libappindicator-gtk3
            ;;
        suse|opensuse|sles)
            info "Installing openSUSE packages: python3-pyusb python3-gobject gtk3 libappindicator3"
            sudo zypper install -y python3-pyusb python3-gobject gtk3 libappindicator3
            ;;
        *)
            warn "Unsupported distro: $distro"
            warn "You must manually install:"
            warn "  - pyusb (Python USB library)"
            warn "  - PyGObject + Gtk 3.0 + AppIndicator3"
            warn "  - Python 3.8+"
            echo
            read -rp "Continue with pip install anyway? [y/N] " ans
            if [[ ! "$ans" =~ ^[yY] ]]; then
                exit 1
            fi
            ;;
    esac
}

# --- Install Python package ---
install_python_pkg() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    info "Installing Python package with pip..."
    if command -v pipx &>/dev/null; then
        pipx install "$script_dir"
        info "Installed via pipx"
    else
        pip install --user --break-system-packages "$script_dir" 2>/dev/null || \
        pip install --user "$script_dir"
        info "Installed via pip --user"
    fi
}

# --- Install udev rule ---
install_udev() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local rules_src="$script_dir/data/99-gigabyte-keyboard-rgb.rules"
    local rules_dst="/etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules"

    if [ -f "$rules_dst" ]; then
        info "udev rule already exists: $rules_dst"
        return
    fi

    info "Installing udev rule (needs sudo)..."
    sudo cp "$rules_src" "$rules_dst"
    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
    info "udev rule installed. You may need to unplug/replug the keyboard."
}

# --- Install systemd user service ---
install_service() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local service_src="$script_dir/data/gigabyte-keyboard-rgb.service"
    local service_dst="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigabyte-keyboard-rgb.service"

    mkdir -p "$(dirname "$service_dst")"
    cp "$service_src" "$service_dst"

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now gigabyte-keyboard-rgb.service 2>/dev/null || true
    info "systemd user service installed and started."
    info "  Status: systemctl --user status gigabyte-keyboard-rgb.service"
}

main() {
    echo "=== $DESCRIPTION ==="
    echo

    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required but not found."
        exit 1
    fi

    install_system_deps
    install_python_pkg
    install_udev
    install_service

    echo
    info "Installation complete!"
    echo
    echo "Commands:"
    echo "  gigabyte-rgb static purple    Set static purple colour"
    echo "  gigabyte-rgb detect          Scan for compatible keyboards"
    echo "  gigabyte-rgb-tray            Launch tray icon app"
    echo "  gigabyte-rgb --help          Full CLI help"
    echo
    echo "The tray app will start automatically on next login."
    echo "To start it now:"
    echo "  systemctl --user start gigabyte-keyboard-rgb.service"
    echo
    echo "If you use GNOME and don't see the tray icon, install:"
    echo "  gnome-shell-extension-appindicator"
    echo "  (usually available in your distro's package manager)"
}

main "$@"
