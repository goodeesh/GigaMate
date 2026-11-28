#!/usr/bin/env bash
# Pre-remove lifecycle hook for the gigamate-dkms kernel driver package (deb/rpm/pacman).
# Best-effort throughout: never fails in containers without kernel/dkms.
set -u

ACTION="${1:-remove}"

# Run on remove/upgrade/deconfigure (Debian: remove|upgrade|deconfigure; RPM: 0).
# Skip on RPM upgrade ($1 == "1") where the new package's postinst handles DKMS.
case "$ACTION" in
    remove|upgrade|deconfigure|0)
        ;;
    *)
        exit 0
        ;;
esac

if command -v modprobe >/dev/null 2>&1; then
    modprobe -r gigamate_acpi >/dev/null 2>&1 || true
elif command -v rmmod >/dev/null 2>&1; then
    rmmod gigamate_acpi >/dev/null 2>&1 || true
fi

if command -v dkms >/dev/null 2>&1; then
    versions="$(
        find /usr/src -maxdepth 1 -mindepth 1 -type d -name 'gigamate_acpi-*' 2>/dev/null \
        | sed 's|.*/gigamate_acpi-||' \
        | sort -Vu
    )"
    for ver in $versions; do
        [[ -n "$ver" ]] || continue
        dkms remove "gigamate_acpi/${ver}" --all >/dev/null 2>&1 || true
    done
fi

exit 0
