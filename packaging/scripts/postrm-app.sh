#!/usr/bin/env bash
# Post-remove lifecycle hook for the gigamate app package (deb/rpm/pacman).
# Best-effort throughout: never fails in containers without systemd/udev.
set -u

ACTION="${1:-remove}"

# Disable global user services only on remove/purge (not on upgrade).
# Debian passes "remove" | "purge" | "upgrade"; RPM passes "0" (remove) | "1" (upgrade).
case "$ACTION" in
    remove|purge|0)
        if command -v systemctl >/dev/null 2>&1; then
            systemctl --global disable gigamate.service gigamate-dgpu.service >/dev/null 2>&1 || true
        fi
        ;;
esac

if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true
fi

exit 0
