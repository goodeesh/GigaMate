#!/usr/bin/env bash
set -euo pipefail

NAME="gigamate"
DESCRIPTION="GigaMate — Gigabyte laptop management for Linux"

COLOUR_GREEN='\033[0;32m'
COLOUR_YELLOW='\033[1;33m'
COLOUR_RED='\033[0;31m'
COLOUR_BOLD='\033[1m'
COLOUR_RESET='\033[0m'

info() { echo -e "${COLOUR_GREEN}[INFO]${COLOUR_RESET} $*"; }
warn() { echo -e "${COLOUR_YELLOW}[WARN]${COLOUR_RESET} $*"; }
error() { echo -e "${COLOUR_RED}[ERROR]${COLOUR_RESET} $*"; }
header() { echo -e "\n${COLOUR_BOLD}--- $* ---${COLOUR_RESET}\n"; }

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

    header "Installing system dependencies"

    case "$distro" in
        arch|archlinux|endeavouros|cachyos)
            info "Installing: python-pyusb python-gobject gtk3 libappindicator-gtk3"
            sudo pacman -S --needed python-pyusb python-gobject gtk3 libappindicator-gtk3
            info "Installing kernel headers (for module build)..."
            sudo pacman -S --needed linux-headers 2>/dev/null || warn "Could not install linux-headers"
            ;;
        debian|ubuntu|pop|mint)
            info "Installing: python3-usb python3-gi python3-gi-cairo gir1.2-appindicator3-0.1 gir1.2-gtk-3.0"
            sudo apt update
            sudo apt install -y python3-usb python3-gi python3-gi-cairo gir1.2-appindicator3-0.1 gir1.2-gtk-3.0
            info "Installing kernel headers..."
            sudo apt install -y linux-headers-$(uname -r) 2>/dev/null || warn "Could not install linux-headers"
            ;;
        fedora|rhel|centos)
            info "Installing: python3-pyusb python3-gobject gtk3 libappindicator-gtk3"
            sudo dnf install -y python3-pyusb python3-gobject gtk3 libappindicator-gtk3
            info "Installing kernel headers..."
            sudo dnf install -y kernel-devel 2>/dev/null || warn "Could not install kernel-devel"
            ;;
        suse|opensuse|sles)
            info "Installing: python3-pyusb python3-gobject gtk3 libappindicator3"
            sudo zypper install -y python3-pyusb python3-gobject gtk3 libappindicator3
            info "Installing kernel headers..."
            sudo zypper install -y kernel-devel 2>/dev/null || warn "Could not install kernel-devel"
            ;;
        *)
            warn "Unsupported distro: $distro"
            warn "You must manually install:"
            warn "  - pyusb (Python USB library)"
            warn "  - PyGObject + Gtk 3.0 + AppIndicator3"
            warn "  - Linux kernel headers (for ACPI kernel module)"
            warn "  - Python 3.8+"
            echo
            read -rp "Continue with pip install anyway? [y/N] " ans
            if [[ ! "$ans" =~ ^[yY] ]]; then
                exit 1
            fi
            ;;
    esac
}

# --- Build and install kernel module ---
build_kernel_module() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local mod_src="$script_dir/src/gigamate_acpi"

    header "Building ACPI kernel module"

    if [ ! -f "$mod_src/Makefile" ]; then
        warn "Kernel module source not found at $mod_src — skipping"
        return
    fi

    if [ ! -d "/lib/modules/$(uname -r)/build" ]; then
        warn "Kernel headers not found at /lib/modules/$(uname -r)/build"
        warn "Cannot build kernel module. Fan/power features will be disabled."
        warn "Install linux-headers and re-run install.sh to enable them."
        return
    fi

    info "Building gigamate_acpi.ko..."
    make -C "$mod_src" clean 2>/dev/null || true
    # Try with clang first (modern kernels), fall back to default compiler
    if make -C "$mod_src" CC=clang LLVM=1 2>/dev/null; then
        info "Built with clang"
    else
        make -C "$mod_src" || {
            warn "Kernel module build failed."
            warn "Fan/power features will be disabled."
            return
        }
        info "Built with default compiler"
    fi

    info "Installing kernel module (needs sudo)..."
    sudo make -C "$mod_src" install 2>/dev/null || \
    sudo make -C "$mod_src" CC=clang LLVM=1 install
    sudo depmod -a

    info "Loading module..."
    sudo modprobe gigamate_acpi 2>/dev/null || warn "Module load failed — is this a Gigabyte laptop with AMW0 ACPI?"

    # Auto-load on boot
    local load_conf="/etc/modules-load.d/gigamate_acpi.conf"
    if [ ! -f "$load_conf" ]; then
        echo "gigamate_acpi" | sudo tee "$load_conf" > /dev/null
        info "Module will auto-load on boot."
    fi

    info "Kernel module installed successfully."
}

# --- Install Python package ---
install_python_pkg() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    header "Installing Python package"

    # Uninstall old package first if present
    pip uninstall -y gigabyte-keyboard-rgb 2>/dev/null || true

    info "Installing gigamate with pip..."
    if command -v pipx &>/dev/null; then
        pipx install "$script_dir" --force
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
    local rules_src="$script_dir/data/99-gigamate.rules"
    local rules_dst="/etc/udev/rules.d/99-gigamate.rules"

    header "Installing udev rule"

    # Remove old rule if present
    if [ -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules ]; then
        sudo rm -f /etc/udev/rules.d/99-gigabyte-keyboard-rgb.rules
        info "Removed old udev rule (99-gigabyte-keyboard-rgb.rules)."
    fi

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
    local service_src="$script_dir/data/gigamate.service"
    local service_dst="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigamate.service"

    header "Installing systemd user service"

    # Remove old service if present
    local old_service="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/gigabyte-keyboard-rgb.service"
    if [ -f "$old_service" ]; then
        systemctl --user disable --now gigabyte-keyboard-rgb.service 2>/dev/null || true
        rm -f "$old_service"
        info "Removed old service (gigabyte-keyboard-rgb.service)."
    fi

    mkdir -p "$(dirname "$service_dst")"
    cp "$service_src" "$service_dst"

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable --now gigamate.service 2>/dev/null || true
    info "systemd user service installed and started."
    info "  Status: systemctl --user status gigamate.service"
}

# --- Install desktop entry + icon ---
install_desktop_entry() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local desktop_src="$script_dir/data/gigamate.desktop"
    local icon_src="$script_dir/data/gigamate.svg"

    header "Installing desktop entry and icon"

    # App menu entry
    local apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$apps_dir"
    cp "$desktop_src" "$apps_dir/gigamate.desktop"
    info "App menu entry: $apps_dir/gigamate.desktop"

    # Remove old desktop entry
    rm -f "$apps_dir/gigabyte-keyboard-rgb-tray.desktop" 2>/dev/null || true

    # Icon
    local icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
    mkdir -p "$icon_dir"
    cp "$icon_src" "$icon_dir/gigamate.svg"
    rm -f "$icon_dir/gigabyte-keyboard-rgb.svg" 2>/dev/null || true

    # Refresh caches
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
    fi
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$apps_dir" 2>/dev/null || true
    fi
    info "Tray icon installed to icon theme."
}

# --- Migrate old config ---
migrate_config() {
    local old_config="${XDG_CONFIG_HOME:-$HOME/.config}/gigabyte-keyboard-rgb"
    local new_config="${XDG_CONFIG_HOME:-$HOME/.config}/gigamate"

    header "Migrating configuration"

    if [ -d "$old_config" ] && [ ! -d "$new_config" ]; then
        info "Migrating settings from $old_config to $new_config..."
        mkdir -p "$new_config"
        cp -r "$old_config"/* "$new_config"/ 2>/dev/null || true
        info "Settings migrated. Old config left in place for safety."
        info "  Remove it later: rm -r $old_config"
    elif [ -d "$old_config" ] && [ -d "$new_config" ]; then
        info "Both old and new config found. Using new config."
    else
        info "No migration needed."
    fi
}

main() {
    echo "============================================"
    echo "  $DESCRIPTION"
    echo "============================================"
    echo

    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required but not found."
        exit 1
    fi

    install_system_deps
    install_python_pkg
    build_kernel_module
    install_udev
    install_service
    install_desktop_entry
    migrate_config

    echo
    echo "============================================"
    echo -e "${COLOUR_GREEN}  ✅ GigaMate installed successfully!${COLOUR_RESET}"
    echo "============================================"
    echo

    # Check what features are available
    local has_module=false
    local has_acpi_call=false
    if lsmod 2>/dev/null | grep -q gigamate_acpi; then
        has_module=true
        echo "  ✅ Keyboard RGB     — gigamate rgb"
        echo "  ✅ Fan & Power      — gigamate status, gigamate profile"
    elif [ -e /proc/acpi/call ]; then
        has_acpi_call=true
        echo "  ✅ Keyboard RGB     — gigamate rgb"
        echo "  ⚠️  Fan & Power      — using acpi_call backend"
    else
        echo "  ✅ Keyboard RGB     — gigamate rgb"
        echo "  ⚠️  Fan & Power      — not available (no ACPI kernel module)"
        echo "      Install linux-headers and re-run install.sh to enable."
    fi

    echo
    echo "  Commands:"
    echo "    gigamate status                  Show hardware status"
    echo "    gigamate rgb static purple       Set keyboard colour"
    echo "    gigamate profile gaming          Set Gaming mode"
    echo "    gigamate-tray                    Launch system tray app"
    echo ""
    echo "  Legacy commands (still work):"
    echo "    gigabyte-rgb static purple       Same as gigamate rgb static purple"
    echo "    gigabyte-rgb-tray                Same as gigamate-tray"
    echo
    echo "  To contribute your profile:"
    echo "    gigamate calibrate --all         Generate your model's profile"
    echo "    gigamate profile contribute      Guide to create a Pull Request"
    echo
    echo "  Tray app auto-starts on login via systemd."
    echo "  Manage: systemctl --user status gigamate.service"
    echo
    echo "  To uninstall:"
    echo "    ./uninstall.sh"
    echo
}

main "$@"
