#!/usr/bin/env bash
# Post-install lifecycle hook for the gigamate-dkms kernel driver package (deb/rpm/pacman).
# Best-effort in containers without kernel headers/modules: prints a warning and exits 0.
set -u

ver="$(
    find /usr/src -maxdepth 1 -mindepth 1 -type d -name 'gigamate_acpi-*' 2>/dev/null \
    | sed 's|.*/gigamate_acpi-||' \
    | sort -V \
    | tail -n 1
)"

if [[ -z "$ver" ]]; then
    echo "gigamate-dkms: warning: no /usr/src/gigamate_acpi-* source directory found; skipping DKMS build." >&2
    exit 0
fi

if command -v dkms >/dev/null 2>&1; then
    # Ignore already-added error
    dkms add -m gigamate_acpi -v "$ver" >/dev/null 2>&1 || true

    if ! dkms install -m gigamate_acpi -v "$ver" --force; then
        echo "gigamate-dkms: default compiler failed; retrying DKMS build with CC=clang LLVM=1..." >&2
        if ! CC=clang LLVM=1 dkms install -m gigamate_acpi -v "$ver" --force; then
            echo "gigamate-dkms: warning: DKMS build/install failed for gigamate_acpi/$ver (missing kernel headers or container environment?)." >&2
        fi
    fi
else
    echo "gigamate-dkms: warning: 'dkms' command not found; skipping kernel module build." >&2
fi

if command -v depmod >/dev/null 2>&1; then
    depmod -a >/dev/null 2>&1 || true
fi

if command -v modprobe >/dev/null 2>&1; then
    modprobe gigamate_acpi >/dev/null 2>&1 || true
fi

exit 0
