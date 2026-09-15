#!/usr/bin/env bash
set -euo pipefail

NAME="gigamate"
DESCRIPTION="GigaMate — Gigabyte laptop management for Linux"
REPO="goodeesh/GigaMate"
INSTALL_URL="https://raw.githubusercontent.com/${REPO}/main/install.sh"

DO_UPDATE=false
ASSUME_YES=false
REQUESTED_TAG=""
DO_CHECK=false

COLOUR_GREEN='\033[0;32m'
COLOUR_YELLOW='\033[1;33m'
COLOUR_RED='\033[0;31m'
COLOUR_BOLD='\033[1m'
COLOUR_RESET='\033[0m'

info() { echo -e "${COLOUR_GREEN}[INFO]${COLOUR_RESET} $*"; }
warn() { echo -e "${COLOUR_YELLOW}[WARN]${COLOUR_RESET} $*"; }
error() { echo -e "${COLOUR_RED}[ERROR]${COLOUR_RESET} $*"; }
header() { echo -e "\n${COLOUR_BOLD}--- $* ---${COLOUR_RESET}\n"; }

usage() {
    echo "Usage: install.sh [--update] [--yes] [--tag vX.Y.Z] [--check]"
    echo "  --update   Re-install latest tagged release (preserves ~/.config/gigamate)"
    echo "  --yes      Non-interactive (assume yes, for background updates)"
    echo "  --tag TAG  Install a specific tag (default: latest release tag)"
    echo "  --check    Print installed vs latest version and exit"
    echo "Env: GIGAMATE_REF=vX.Y.Z forces a ref (tag or branch)."
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --update) DO_UPDATE=true; shift ;;
            --yes|-y) ASSUME_YES=true; shift ;;
            --tag) REQUESTED_TAG="${2:-}"; shift 2 ;;
            --tag=*) REQUESTED_TAG="${1#--tag=}"; shift ;;
            --check) DO_CHECK=true; shift ;;
            -h|--help) usage; exit 0 ;;
            *) shift ;;
        esac
    done
}

latest_github_tag() {
    local tag=""
    if command -v curl &>/dev/null; then
        # Prefer the latest *stable* release; fall back to the newest tag.
        tag="$(curl -sSL --max-time 10 "https://api.github.com/repos/${REPO}/releases/latest" \
            | grep -o '"tag_name": *"v[0-9][^"]*"' | head -1 | cut -d'"' -f4 || true)"
        if [ -z "$tag" ]; then
            tag="$(curl -sSL --max-time 10 "https://api.github.com/repos/${REPO}/tags?per_page=1" \
                | grep -o '"name": *"v[0-9][^"]*"' | head -1 | cut -d'"' -f4 || true)"
        fi
    elif command -v python3 &>/dev/null; then
        tag="$(python3 -c "
import json, urllib.request
for url in ('https://api.github.com/repos/${REPO}/releases/latest',
            'https://api.github.com/repos/${REPO}/tags?per_page=1'):
    try:
        r = urllib.request.urlopen(url, timeout=10)
        data = json.load(r)
        name = data.get('tag_name') if isinstance(data, dict) else (data[0]['name'] if data else '')
        if name:
            print(name); break
    except Exception:
        continue
" 2>/dev/null || true)"
    fi
    # Accept stable tags; a prerelease only when it is the explicitly
    # requested tag (never silently picked up for stable users).
    if [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$tag"
    elif [[ -n "${REQUESTED_TAG:-}" && "$tag" == "$REQUESTED_TAG" && "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
        echo "$tag"
    fi
}

installed_version() {
    if command -v gigamate &>/dev/null; then
        gigamate version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?' | head -1
    elif python3 -c "import gigamate" 2>/dev/null; then
        python3 -c "import gigamate; print(gigamate.__version__)" 2>/dev/null || true
    fi
}

do_check() {
    local installed latest
    installed="$(installed_version || true)"
    latest="$(latest_github_tag || true)"
    echo "Installed: ${installed:-unknown}"
    echo "Latest:    ${latest:-unknown (offline?)}"
}

# When run via `curl .../install.sh | bash` there is no local checkout
# (no src/, no data/). Fetch the requested tag tarball and re-exec from it
# so pipe-install and self-update share one code path.
ensure_checkout() {
    local script_dir="$1"
    if [ -f "$script_dir/src/gigamate_acpi/Makefile" ] && \
       [ -f "$script_dir/data/gigamate.service" ]; then
        return 0
    fi
    if [ "${GIGAMATE_BOOTSTRAPPED:-}" = "1" ]; then
        error "Bootstrapped checkout still missing src/ and data/ — aborting."
        exit 1
    fi
    local ref="${REQUESTED_TAG:-${GIGAMATE_REF:-}}"
    if [ -z "$ref" ]; then
        ref="$(latest_github_tag || true)"
        [ -n "$ref" ] || ref="main"
    fi
    info "No local checkout detected (pipe mode) — fetching ${REPO} @ ${ref}..."
    local tmpdir tarball top
    tmpdir="$(mktemp -d)"
    tarball="$tmpdir/gigamate.tar.gz"
    local url
    if [[ "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
        url="https://codeload.github.com/${REPO}/tar.gz/refs/tags/${ref}"
    else
        url="https://codeload.github.com/${REPO}/tar.gz/refs/heads/${ref}"
    fi
    if ! curl -fL --proto "=https" --max-time 60 -o "$tarball" "$url"; then
        if [[ ! "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
            url="https://github.com/${REPO}/archive/refs/heads/${ref}.tar.gz"
            curl -fL --proto "=https" --max-time 60 -o "$tarball" "$url" || {
                error "Download failed: $url"; exit 1; }
        else
            error "Download failed: $url"; exit 1
        fi
    fi

    # Best-effort integrity check against the release-published checksum.
    if [[ "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
        local tarball_sha_url="https://github.com/${REPO}/releases/download/${ref}/gigamate.tar.gz.sha256"
        if curl -fL --proto "=https" --max-time 30 -o "$tmpdir/gigamate.tar.gz.sha256" "$tarball_sha_url" 2>/dev/null; then
            local expected actual
            expected="$(cut -d ' ' -f1 "$tmpdir/gigamate.tar.gz.sha256" | head -n1)"
            actual="$(sha256sum "$tarball" | cut -d ' ' -f1)"
            if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
                error "Tarball checksum verification FAILED"; exit 1
            fi
            info "Verified tarball checksum."
        else
            warn "No tarball checksum available; proceeding unverified."
        fi
    fi
    tar -xzf "$tarball" -C "$tmpdir" || { error "Extract failed"; exit 1; }
    top="$(find "$tmpdir" -maxdepth 1 -name 'GigaMate-*' | head -1)"
    if [ -z "$top" ] || [ ! -f "$top/install.sh" ]; then
        error "Fetched archive has no install.sh — aborting."
        exit 1
    fi
    info "Re-executing from $top ..."
    local args=()
    $DO_UPDATE && args+=(--update)
    $ASSUME_YES && args+=(--yes)
    [ -n "$REQUESTED_TAG" ] && args+=(--tag "$REQUESTED_TAG")
    $DO_CHECK && args+=(--check)
    export GIGAMATE_BOOTSTRAPPED=1
    exec bash "$top/install.sh" "${args[@]}"
}

# --- Detect distro ---
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        ID_LIKE="${ID_LIKE:-$ID}"
        # ID_LIKE may list multiple tokens (e.g. "ubuntu debian"); use the first.
        echo "$ID_LIKE" | tr '[:upper:]' '[:lower:]' | awk '{print $1}'
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

# --- Map running kernel to the matching Arch-family headers package ---
arch_headers_pkg() {
    local kr
    kr="$(uname -r)"
    case "$kr" in
        *cachyos-lts*)  echo "linux-cachyos-lts-headers" ;;
        *cachyos-bore*) echo "linux-cachyos-bore-headers" ;;
        *cachyos*)      echo "linux-cachyos-headers" ;;
        *zen*)          echo "linux-zen-headers" ;;
        *hardened*)     echo "linux-hardened-headers" ;;
        *lts*)          echo "linux-lts-headers" ;;
        *)              echo "linux-headers" ;;
    esac
}

# --- Install system dependencies ---
install_system_deps() {
    local distro
    distro=$(detect_distro)
    info "Detected distro: $distro"

    header "Installing system dependencies"

    case "$distro" in
        arch|archlinux|endeavouros|cachyos)
            info "Installing: python-pyusb python-gobject gtk3 libappindicator-gtk3 dkms python-evdev python-pip base-devel"
            sudo pacman -S --needed ${ASSUME_YES:+--noconfirm} python-pyusb python-gobject gtk3 libappindicator-gtk3 dkms python-evdev python-pip base-devel
            sudo pacman -S --needed ${ASSUME_YES:+--noconfirm} python-pyqt6 2>/dev/null || \
                warn "python-pyqt6 unavailable — GigaMate Center will use the pip-installed PyQt6"
            if [ ! -d "/lib/modules/$(uname -r)/build" ]; then
                local headers_pkg
                headers_pkg="$(arch_headers_pkg)"
                info "Installing kernel headers for $(uname -r): $headers_pkg"
                sudo pacman -S --needed ${ASSUME_YES:+--noconfirm} "$headers_pkg" 2>/dev/null || \
                    warn "Could not install $headers_pkg — install headers matching: $(uname -r)"
            else
                info "Kernel headers for $(uname -r) already present."
            fi
            ;;
        debian|ubuntu|pop|mint)
            info "Installing: python3-usb python3-gi python3-gi-cairo gir1.2-gtk-3.0 dkms python3-evdev python3-pip build-essential"
            sudo apt update
            sudo apt install -y python3-usb python3-gi python3-gi-cairo gir1.2-gtk-3.0 dkms python3-evdev python3-pip build-essential
            sudo apt install -y gir1.2-ayatanaappindicator3-0.1 2>/dev/null || \
                sudo apt install -y gir1.2-appindicator3-0.1 2>/dev/null || true
            sudo apt install -y python3-pyqt6 2>/dev/null || \
                warn "python3-pyqt6 unavailable — GigaMate Center will use the pip-installed PyQt6"
            info "Installing kernel headers..."
            sudo apt install -y linux-headers-$(uname -r) 2>/dev/null || warn "Could not install linux-headers"
            ;;
        fedora|rhel|centos)
            info "Installing: python3-pyusb python3-gobject gtk3 dkms python3-evdev python3-pip"
            sudo dnf install -y python3-pyusb python3-gobject gtk3 dkms python3-evdev python3-pip
            sudo dnf install -y libayatana-appindicator-gtk3 2>/dev/null || \
                sudo dnf install -y libappindicator-gtk3 2>/dev/null || true
            sudo dnf install -y python3-pyqt6 2>/dev/null || \
                warn "python3-pyqt6 unavailable — GigaMate Center will use the pip-installed PyQt6"
            info "Installing kernel headers..."
            sudo dnf install -y kernel-devel 2>/dev/null || warn "Could not install kernel-devel"
            ;;
        suse|opensuse*|sles)
            info "Installing: python3-pyusb python3-gobject gtk3 dkms python3-evdev python3-pip"
            sudo zypper install -y python3-pyusb python3-gobject gtk3 dkms python3-evdev python3-pip
            sudo zypper install -y typelib-1_0-AyatanaAppIndicator3-0_1 2>/dev/null || \
                sudo zypper install -y libappindicator3 2>/dev/null || true
            sudo zypper install -y python3-PyQt6 2>/dev/null || \
                warn "python3-PyQt6 unavailable — GigaMate Center will use the pip-installed PyQt6"
            info "Installing kernel headers..."
            sudo zypper install -y kernel-devel 2>/dev/null || warn "Could not install kernel-devel"
            ;;
        *)
            warn "Unsupported distro: $distro"
            warn "You must manually install:"
            warn "  - pyusb (Python USB library)"
            warn "  - evdev (Python input monitoring, for keyboard idle auto-off)"
            warn "  - PyGObject + Gtk 3.0 + AppIndicator3"
            warn "  - Linux kernel headers (for ACPI kernel module)"
            warn "  - Python 3.8+"
            echo
            if [ "$ASSUME_YES" = true ]; then
                info "--yes given, continuing non-interactively."
                return 0
            fi
            read -rp "Continue with pip install anyway? [y/N] " ans || ans=""
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
    local mod_version
    mod_version="$(grep -E '^PACKAGE_VERSION=' "$mod_src/dkms.conf" | head -1 | cut -d= -f2 | tr -d '"')"
    mod_version="${mod_version:-1.0.0}"

    header "Building ACPI kernel module"

    if [ ! -f "$mod_src/Makefile" ]; then
        warn "Kernel module source not found at $mod_src — skipping"
        return
    fi

    local running_kver
    running_kver="$(uname -r)"
    local running_headers_found=false
    local any_headers_found=false

    if [ -d "/lib/modules/${running_kver}/build" ]; then
        running_headers_found=true
        any_headers_found=true
    fi

    # Check for any installed kernel headers (e.g. pending reboot into newer kernel)
    for kdir in /lib/modules/*/build; do
        if [ -d "$kdir" ]; then
            any_headers_found=true
            break
        fi
    done

    if [ "$any_headers_found" = false ]; then
        warn "No kernel headers found under /lib/modules/*/build"
        warn "Cannot build kernel module. Fan/power features will be disabled."
        warn "Install kernel headers for your kernel and re-run install.sh to enable them."
        return
    fi

    # Preferred: DKMS (auto-rebuilds on kernel updates). Requires dkms from
    # the distro's official repos — never from AUR.
    if command -v dkms &>/dev/null; then
        info "Using DKMS to build and install the module..."

        # Remove any existing registration so re-installs and upgrades work cleanly
        local entry
        for entry in $(dkms status 2>/dev/null | grep '^gigamate_acpi/' | cut -d, -f1 | sort -u); do
            info "Removing existing DKMS entry: $entry"
            sudo dkms remove "$entry" --all 2>/dev/null || true
        done

        # Portable across dkms 2.x and 3.x: source must live under /usr/src
        make -C "$mod_src" clean 2>/dev/null || true
        sudo rm -rf "/usr/src/gigamate_acpi-$mod_version"
        sudo cp -r "$mod_src" "/usr/src/gigamate_acpi-$mod_version"
        sudo find "/usr/src/gigamate_acpi-$mod_version" -name '*.o' -o -name '*.ko*' -o -name '*.mod*' -o -name 'Module.symvers' -o -name 'modules.order' | sudo xargs -r rm -f

        if ! sudo dkms add -m gigamate_acpi -v "$mod_version"; then
            warn "DKMS add failed."
            warn "Fan/power features will be disabled."
            return
        fi

        # Build and install for each installed kernel with headers present
        for kdir in /lib/modules/*/build; do
            [ -d "$kdir" ] || continue
            local kver
            kver="$(basename "$(dirname "$kdir")")"
            info "Building gigamate_acpi for kernel $kver..."
            if sudo dkms build -m gigamate_acpi -v "$mod_version" -k "$kver" 2>/dev/null ||
               sudo env CC=clang LLVM=1 dkms build -m gigamate_acpi -v "$mod_version" -k "$kver" 2>/dev/null; then
                info "DKMS build succeeded for $kver"
                if sudo dkms install -m gigamate_acpi -v "$mod_version" -k "$kver" --force 2>/dev/null; then
                    info "DKMS install succeeded for $kver"
                else
                    warn "DKMS install failed for kernel $kver"
                fi
            else
                warn "DKMS build failed for kernel $kver"
            fi
        done
    else
        warn "dkms not found — falling back to a manual build."
        warn "The module will NOT auto-rebuild after kernel updates."
        warn "Install dkms from your distro's official repos and re-run install.sh."
        if [ "$running_headers_found" = true ]; then
            info "Building gigamate_acpi.ko..."
            make -C "$mod_src" clean 2>/dev/null || true
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
            sudo make -C "$mod_src" install 2>/dev/null || \
            sudo make -C "$mod_src" CC=clang LLVM=1 install
            sudo depmod -a
        fi
    fi

    if [ "$running_headers_found" = true ]; then
        info "Loading module for running kernel ($running_kver)..."
        sudo modprobe -r gigamate_acpi 2>/dev/null || true
        if ! sudo modprobe gigamate_acpi 2>/dev/null; then
            warn "Module load failed — is this a Gigabyte laptop with AMW0 ACPI?"
        fi
    else
        info "gigamate_acpi installed for installed kernel(s)."
        info "Reboot your laptop to boot into the updated kernel and activate new module features."
    fi

    # Verify the sysfs interface actually appeared with control files
    if [ -f /sys/devices/platform/gigamate_acpi/profile ] || [ -f /sys/devices/platform/gigamate_acpi/fan1_input ]; then
        info "Module loaded, sysfs interface ready: /sys/devices/platform/gigamate_acpi"
        if [ -f /sys/devices/platform/gigamate_acpi/charge_limit ]; then
            info "Battery charge limit supported: /sys/devices/platform/gigamate_acpi/charge_limit"
        fi
    elif [ -d /sys/devices/platform/gigamate_acpi ]; then
        warn "Module loaded but sysfs control files not found."
        warn "Your laptop may not expose the AMW0 ACPI device — fan/power features will not work."
    else
        warn "Module failed to bind to an AMW0 ACPI device — fan/power features will not work."
    fi

    # Secure Boot: DKMS signs the module with its own key, which must be
    # enrolled once or the module will not load after a reboot.
    local sb_enabled=false
    if command -v mokutil &>/dev/null && [[ "$(mokutil --sb-state 2>/dev/null || true)" == *nabled* ]]; then
        sb_enabled=true
    elif [ -f /sys/kernel/security/lockdown ] && grep -q '\[\(integrity\|confidentiality\)\]' /sys/kernel/security/lockdown 2>/dev/null; then
        sb_enabled=true
    fi

    if [ "$sb_enabled" = true ]; then
        warn "Secure Boot appears to be enabled."
        warn "Enroll the DKMS signing key once so the module survives reboot:"
        warn "  sudo mokutil --import /var/lib/dkms/mok.pub"
        warn "Reboot, choose 'Enroll key from disk' in the MOK manager, and select mok.pub."
    fi

    # Auto-load on boot only when the module actually bound to an AMW0 device;
    # otherwise it would fail on every boot on unsupported hardware.
    local load_conf="/etc/modules-load.d/gigamate_acpi.conf"
    if [ -d /sys/devices/platform/gigamate_acpi ]; then
        if [ ! -f "$load_conf" ]; then
            echo "gigamate_acpi" | sudo tee "$load_conf" > /dev/null
            info "Module will auto-load on boot."
        fi
        info "Kernel module installed and bound successfully."
    elif [ -n "$(find "/lib/modules/$(uname -r)" -name 'gigamate_acpi.ko*' -print -quit 2>/dev/null)" ]; then
        info "Kernel module installed."
        warn "Module did not bind to an AMW0 device — this laptop may be unsupported."
        warn "Skipping boot auto-load so modprobe does not fail on every boot."
        [ -f "$load_conf" ] && sudo rm -f "$load_conf" 2>/dev/null || true
    else
        warn "Kernel module was NOT installed for the running kernel."
        warn "Fan/power features will be unavailable until a build succeeds."
    fi
}

# --- Install Python package ---
install_python_pkg() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    header "Installing Python package"

    # Uninstall old package first if present
    pip uninstall -y gigabyte-keyboard-rgb 2>/dev/null || true
    # Remove an earlier pip --user install of gigamate so its ~/.local/bin
    # scripts cannot conflict with the pipx symlinks we are about to create
    # (a previous version may have fallen back to pip --user).
    pip uninstall -y --break-system-packages gigamate 2>/dev/null || \
    pip uninstall -y gigamate 2>/dev/null || true

    info "Installing gigamate with pip..."
    if command -v pipx &>/dev/null; then
        # The tray needs system packages (PyGObject/gi), which are not
        # installable into an isolated venv, so install with
        # --system-site-packages. pipx backed by uv cannot recreate an
        # existing venv for `install --force` ("A virtual environment
        # already exists"), so remove any existing venv first — this makes
        # every upgrade install cleanly via pipx instead of silently
        # falling back to pip --user.
        local venv_dir="${PIPX_HOME:-$HOME/.local/share/pipx}/venvs/gigamate"
        if [ -d "$venv_dir" ]; then
            info "Removing existing pipx venv for a clean reinstall..."
            pipx uninstall gigamate >/dev/null 2>&1 || rm -rf "$venv_dir"
        fi
        if pipx install "$script_dir" --system-site-packages; then
            # pipx can report success yet produce a venv that cannot run
            # the tray (e.g. isolated from system PyGObject). Verify the
            # venv interpreter imports the system-bound modules before
            # trusting it; otherwise fall back to pip --user.
            local venv_py="${PIPX_HOME:-$HOME/.local/share/pipx}/venvs/gigamate/bin/python"
            if [ -x "$venv_py" ] && "$venv_py" -c \
                "import gi, usb; import gigamate.tray, gigamate.idle, gigamate.ui" 2>/dev/null; then
                info "Installed via pipx (verified: gi + tray + Center imports OK)"
            else
                warn "pipx venv verification failed (gi/tray/Center import) — falling back to pip --user"
                pipx uninstall gigamate 2>/dev/null || true
                pip install --user --break-system-packages "$script_dir" 2>/dev/null || \
                pip install --user "$script_dir"
                info "Installed via pip --user"
            fi
        else
            warn "pipx install failed — falling back to pip --user"
            pip install --user --break-system-packages "$script_dir" 2>/dev/null || \
            pip install --user "$script_dir"
            info "Installed via pip --user"
        fi
    else
        pip install --user --break-system-packages "$script_dir" 2>/dev/null || \
        pip install --user "$script_dir"
        info "Installed via pip --user"
    fi

    # Verify the Center (PyQt6) imports; a broken Qt install should be loud.
    # NOTE: ``import gigamate.ui`` alone no longer implies Qt (the GUI entry
    # point is lazily imported), so resolve ``run_gui`` to exercise Qt.
    if ! python3 -c "from gigamate.ui import run_gui" 2>/dev/null && \
       ! "${PIPX_HOME:-$HOME/.local/share/pipx}/venvs/gigamate/bin/python" -c "from gigamate.ui import run_gui" 2>/dev/null; then
        warn "GigaMate Center (PyQt6) failed to import."
        warn "Tray and CLI still work; run 'pip install PyQt6' (or install your"
        warn "distro's PyQt6 package) to enable the graphical Center."
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

    if [ -f "$rules_dst" ] && cmp -s "$rules_src" "$rules_dst"; then
        info "udev rule up to date: $rules_dst"
        return
    fi

    info "Installing udev rule (needs sudo)..."
    sudo cp "$rules_src" "$rules_dst"
    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
    info "udev rule installed. You may need to unplug/replug the keyboard."
}

# --- Configure GPU power management (Dynamic Boost / SmartShift) ---
configure_gpu_power() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    header "Configuring GPU power management"

    # Only relevant on genuine Gigabyte laptops (DMI vendor).
    local is_gigabyte=false
    if [ -r /sys/class/dmi/id/sys_vendor ] && \
       grep -qiE "gigabyte|aorus|giga-byte" /sys/class/dmi/id/sys_vendor; then
        is_gigabyte=true
    fi
    if [ "$is_gigabyte" != true ]; then
        info "Non-Gigabyte hardware; skipping GPU power configuration."
        return
    fi

    # AMD SmartShift bias access is Gigabyte-only too.
    local ss_src="$script_dir/data/99-gigamate-smartshift.rules"
    local ss_dst="/etc/udev/rules.d/99-gigamate-smartshift.rules"
    if [ -f "$ss_src" ]; then
        sudo cp "$ss_src" "$ss_dst" 2>/dev/null || true
        sudo udevadm control --reload-rules 2>/dev/null || true
        sudo udevadm trigger --subsystem-match=pci 2>/dev/null || true
        info "Installed SmartShift udev rule: $ss_dst"
    fi

    # Install polkit rule for nvidia-powerd if polkit rules dir exists
    local polkit_dir="/etc/polkit-1/rules.d"
    local polkit_src="$script_dir/data/50-gigamate-powerd.rules"
    local polkit_dst="$polkit_dir/50-gigamate-powerd.rules"

    if [ -d "$polkit_dir" ] && [ -f "$polkit_src" ]; then
        info "Installing polkit rule for nvidia-powerd management (needs sudo)..."
        sudo cp "$polkit_src" "$polkit_dst"
        sudo chmod 644 "$polkit_dst" 2>/dev/null || true
        info "Polkit rule installed: $polkit_dst"
    fi

    # Check for discrete NVIDIA GPU
    local has_nvidia=false
    if [ -d /sys/bus/pci/devices ]; then
        for v in /sys/bus/pci/devices/*/vendor; do
            if [ -f "$v" ] && grep -qi "0x10de" "$v"; then
                has_nvidia=true
                break
            fi
        done
    fi

    if [ "$has_nvidia" = true ]; then
        if systemctl list-unit-files nvidia-powerd.service &>/dev/null; then
            info "NVIDIA GPU detected. Enabling nvidia-powerd for Dynamic Boost..."
            sudo systemctl enable --now nvidia-powerd.service 2>/dev/null || true
            info "nvidia-powerd service enabled and active."
        fi
    fi
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

    # Point the unit at the actual installed tray binary.
    local tray_bin
    tray_bin="$(command -v gigamate-tray 2>/dev/null || true)"
    [ -n "$tray_bin" ] || tray_bin="$HOME/.local/bin/gigamate-tray"
    sed -i "s|^ExecStart=.*|ExecStart=${tray_bin}|" "$service_dst" 2>/dev/null || true

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable gigamate.service 2>/dev/null || true
    # restart (not enable --now): also restarts a stale already-running tray
    # so re-installs can never leave old code in memory. restart starts an
    # inactive service too, so first-time installs are unaffected.
    systemctl --user restart gigamate.service 2>/dev/null || true
    info "systemd user service installed and (re)started."
    info "  Status: systemctl --user status gigamate.service"
}

# --- Install desktop entry + icon ---
install_desktop_entry() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local desktop_src="$script_dir/data/gigamate.desktop"

    header "Installing desktop entry and icon"

    # App menu entries
    local apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$apps_dir"
    cp "$desktop_src" "$apps_dir/gigamate.desktop"
    if [ -f "$script_dir/data/gigamate-center.desktop" ]; then
        cp "$script_dir/data/gigamate-center.desktop" "$apps_dir/gigamate-center.desktop"
    fi

    # Point launchers at the actual installed binaries (PATH may not include
    # ~/.local/bin when the session launches a .desktop file).
    local center_bin cli_bin
    center_bin="$(command -v gigamate-center 2>/dev/null || true)"
    cli_bin="$(command -v gigamate 2>/dev/null || true)"
    if [ -n "$center_bin" ] && [ -f "$apps_dir/gigamate-center.desktop" ]; then
        sed -i "s|^Exec=.*|Exec=${center_bin}|" "$apps_dir/gigamate-center.desktop" 2>/dev/null || true
    fi
    if [ -n "$cli_bin" ]; then
        sed -i "s|^Exec=.*|Exec=${cli_bin} center|" "$apps_dir/gigamate.desktop" 2>/dev/null || true
    fi
    info "App menu entries: $apps_dir/gigamate.desktop & gigamate-center.desktop"

    # Remove old desktop entry
    rm -f "$apps_dir/gigabyte-keyboard-rgb-tray.desktop" 2>/dev/null || true

    # Icons (base + dGPU dot + update badge variants: gigamate*.svg)
    local icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
    mkdir -p "$icon_dir"
    cp "$script_dir"/data/gigamate*.svg "$icon_dir/"
    if [ -f "$script_dir/data/checkbox-checked.svg" ]; then
        cp "$script_dir/data/checkbox-checked.svg" "$icon_dir/"
    fi
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
        # The new dir may have been created by a running tray (config.json is
        # auto-migrated in Python), so still copy any un-migrated profiles.
        if [ -d "$old_config/profiles" ]; then
            mkdir -p "$new_config/profiles"
            cp -n "$old_config/profiles"/*.json "$new_config/profiles"/ 2>/dev/null || true
        fi
        info "Both old and new config found. Using new config."
    else
        info "No migration needed."
    fi
}

main() {
    parse_args "$@"

    if [ "$(id -u)" = "0" ]; then
        if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then
            info "Re-running install as ${SUDO_USER} (per-user install)..."
            exec sudo -u "$SUDO_USER" -H bash "${BASH_SOURCE[0]}" "$@"
        fi
        error "Do not run install.sh as root / via sudo."
        error "Run it as your normal user — it elevates only the steps that need root."
        exit 1
    fi

    if [ "$DO_CHECK" = true ]; then
        do_check
        exit 0
    fi

    # Pipe mode (curl | bash) has no checkout - fetch tag tarball first.
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" 2>/dev/null && pwd || pwd)"
    ensure_checkout "$script_dir"
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [ "$DO_UPDATE" = true ]; then
        echo "============================================"
        echo "  $DESCRIPTION - Update"
        echo "============================================"
        echo
    else
        echo "============================================"
        echo "  $DESCRIPTION"
        echo "============================================"
        echo
    fi

    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required but not found."
        exit 1
    fi

    install_system_deps
    install_python_pkg
    build_kernel_module
    install_udev
    configure_gpu_power
    # Migrate the old config/profiles *before* starting the tray: the tray
    # creates ~/.config/gigamate on launch, which would otherwise make
    # migrate_config take the "both exist" branch and skip user profiles.
    migrate_config
    install_service
    install_desktop_entry

    if [ "$DO_UPDATE" = true ]; then
        # install_service already restarted the tray; just drop the cached
        # update state so the new version re-checks fresh tomorrow.
        rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/gigamate/update_state.json" 2>/dev/null || true
        info "Cleared cached update state."
    fi

    echo
    echo "============================================"
    if [ "$DO_UPDATE" = true ]; then
        echo -e "${COLOUR_GREEN}  Updated successfully!${COLOUR_RESET}"
    else
    echo -e "${COLOUR_GREEN}  ✅ GigaMate installed successfully!${COLOUR_RESET}"
    fi
    echo "============================================"
    echo

    # Check what features are available
    local has_module=false
    local has_acpi_call=false
    if [ -f /sys/devices/platform/gigamate_acpi/profile ] || [ -f /sys/devices/platform/gigamate_acpi/fan1_input ]; then
        has_module=true
        echo "  ✅ Keyboard RGB     — gigamate rgb"
        echo "  ✅ Fan & Power      — gigamate status, gigamate profile"
    elif [ -e /proc/acpi/call ]; then
        has_acpi_call=true
        echo "  ✅ Keyboard RGB     — gigamate rgb"
        echo "  ⚠️  Fan & Power      — using acpi_call backend"
    elif [ -n "$(find "/lib/modules/$(uname -r)" -name 'gigamate_acpi.ko*' -print -quit 2>/dev/null)" ]; then
        echo "  ⚠️  Fan & Power      — unavailable on this hardware (no AMW0 device)"
        echo "      The kernel module built, but this is not a supported Gigabyte model."
    else
        echo "  ⚠️  Fan & Power      — not available (kernel headers/module missing)"
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
    echo "    gigamate calibrate all           Generate your model's profile"
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
