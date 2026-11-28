#!/usr/bin/env bash
# 4-distro container smoke test suite for GigaMate native packages.
# Installs built packages inside clean Ubuntu, Fedora, openSUSE, and Arch
# containers, verifies legacy /etc/ migration, installed files, CLI commands,
# install-method marker, and clean removal.
set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: packaging/test-containers.sh [OPTIONS]" \
        "" \
        "Run container smoke tests for GigaMate native packages." \
        "" \
        "Options:" \
        "  --distro <name>   Distro to test: ubuntu | fedora | opensuse | arch | all (default: all)" \
        "  --pkg-dir <dir>   Directory containing built packages (default: dist)" \
        "  -h, --help        Show this help message and exit" \
        "" \
        "Environment:" \
        "  CONTAINER_RUNTIME Override container runtime (e.g. docker or podman)." \
        "                    Auto-detected (docker, then podman) when unset."
}

DISTRO="all"
PKG_DIR="dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --distro)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --distro requires a value" >&2
                usage >&2
                exit 2
            fi
            DISTRO="$2"
            shift 2
            ;;
        --distro=*)
            DISTRO="${1#*=}"
            shift
            ;;
        --pkg-dir)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --pkg-dir requires a value" >&2
                usage >&2
                exit 2
            fi
            PKG_DIR="$2"
            shift 2
            ;;
        --pkg-dir=*)
            PKG_DIR="${1#*=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option '$1'" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$DISTRO" in
    ubuntu|fedora|opensuse|arch|all) ;;
    *)
        echo "Error: invalid --distro '$DISTRO' (expected: ubuntu, fedora, opensuse, arch, or all)" >&2
        exit 2
        ;;
esac

detect_runtime() {
    if [[ -n "${CONTAINER_RUNTIME:-}" ]]; then
        if ! command -v "$CONTAINER_RUNTIME" >/dev/null 2>&1; then
            echo "Error: CONTAINER_RUNTIME='$CONTAINER_RUNTIME' not found in PATH" >&2
            exit 1
        fi
        printf '%s\n' "$CONTAINER_RUNTIME"
        return 0
    fi

    if command -v docker >/dev/null 2>&1; then
        printf 'docker\n'
        return 0
    fi

    if command -v podman >/dev/null 2>&1; then
        printf 'podman\n'
        return 0
    fi

    echo "Error: neither 'docker' nor 'podman' was found in PATH. Install one or set CONTAINER_RUNTIME." >&2
    exit 1
}

RUNTIME="$(detect_runtime)"

if [[ ! -d "$PKG_DIR" ]]; then
    echo "Error: package directory does not exist: '$PKG_DIR'" >&2
    exit 1
fi
PKG_DIR_ABS="$(cd "$PKG_DIR" && pwd)"

build_container_script() {
    local install_cmd="$1"
    local remove_cmd="$2"
    local expected_method="$3"

    cat <<EOF
set -euo pipefail

# Pre-install: create legacy /etc/ files to verify migration behavior
mkdir -p /etc/udev/rules.d /etc/polkit-1/rules.d
printf '# GigaMate legacy\n' > /etc/udev/rules.d/70-gigamate.rules
printf '// custom sysadmin rule\n' > /etc/polkit-1/rules.d/50-gigamate-repair.rules

# Install built packages
${install_cmd}

# Post-install assertions
test ! -e /etc/udev/rules.d/70-gigamate.rules
test -f /etc/polkit-1/rules.d/50-gigamate-repair.rules.pre-pkg.bak
test -f /usr/lib/udev/rules.d/70-gigamate.rules
test -f /usr/share/polkit-1/rules.d/50-gigamate.rules
test -f /usr/lib/systemd/user/gigamate.service
test "\$(cat /usr/lib/gigamate/install-method)" = "${expected_method}"
/usr/bin/gigamate --help >/dev/null
/usr/bin/gigamate version

# Clean uninstall
${remove_cmd}

# Post-remove assertions
test ! -e /usr/bin/gigamate
test ! -e /usr/lib/gigamate/install-method
EOF
}

run_distro() {
    local target="$1"
    local image=""
    local install_cmd=""
    local remove_cmd=""
    local expected_method=""

    case "$target" in
        ubuntu)
            image="ubuntu:24.04"
            install_cmd="export DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get install -y /pkgs/deb/gigamate-dkms_*.deb /pkgs/deb/gigamate_*.deb"
            remove_cmd="export DEBIAN_FRONTEND=noninteractive && apt-get remove -y gigamate gigamate-dkms"
            expected_method="apt"
            ;;
        fedora)
            image="fedora:41"
            install_cmd="dnf install -y /pkgs/fedora/gigamate-dkms-*.rpm /pkgs/fedora/gigamate-*.rpm"
            remove_cmd="dnf remove -y gigamate gigamate-dkms"
            expected_method="dnf"
            ;;
        opensuse)
            image="opensuse/tumbleweed"
            install_cmd="zypper --non-interactive --no-gpg-checks install /pkgs/opensuse/gigamate-dkms-*.rpm /pkgs/opensuse/gigamate-*.rpm"
            remove_cmd="zypper --non-interactive remove gigamate gigamate-dkms"
            expected_method="zypper"
            ;;
        arch)
            image="archlinux:latest"
            install_cmd="pacman -Sy --noconfirm && pacman -U --noconfirm /pkgs/arch/gigamate-dkms-*.pkg.tar.zst /pkgs/arch/gigamate-*.pkg.tar.zst"
            remove_cmd="pacman -Rns --noconfirm gigamate gigamate-dkms"
            expected_method="pacman"
            ;;
        *)
            echo "Error: unsupported distro '$target'" >&2
            exit 2
            ;;
    esac

    local script
    script="$(build_container_script "$install_cmd" "$remove_cmd" "$expected_method")"

    echo "==> Running smoke test for ${target} (${image}) using ${RUNTIME}..."
    "$RUNTIME" run --rm \
        -v "${PKG_DIR_ABS}:/pkgs:ro" \
        "$image" \
        bash -c "$script"
    echo "==> PASS: ${target}"
}

if [[ "$DISTRO" == "all" ]]; then
    for d in ubuntu fedora opensuse arch; do
        run_distro "$d"
    done
else
    run_distro "$DISTRO"
fi
