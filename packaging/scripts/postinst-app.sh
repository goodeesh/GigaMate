#!/usr/bin/env bash
# Post-install lifecycle hook for the gigamate app package (deb/rpm/pacman).
# Best-effort throughout: never fails installation in containers without systemd/udev.
# NEVER modifies or deletes ~/.config/gigamate/.
set -u

cleanup_etc_with_header() {
    local file="$1"
    local header_regex="$2"
    if [[ -f "$file" && ! -L "$file" ]]; then
        if head -n 5 "$file" 2>/dev/null | grep -qE "$header_regex"; then
            rm -f "$file" || true
        else
            mv -f "$file" "${file}.pre-pkg.bak" || true
        fi
    fi
}

cleanup_etc_modules_load() {
    local file="$1"
    if [[ -f "$file" && ! -L "$file" ]]; then
        if grep -qE '^[[:space:]]*gigamate_acpi[[:space:]]*$' "$file" 2>/dev/null \
           && ! grep -qvE '^[[:space:]]*(#.*|gigamate_acpi)?[[:space:]]*$' "$file" 2>/dev/null; then
            rm -f "$file" || true
        else
            mv -f "$file" "${file}.pre-pkg.bak" || true
        fi
    fi
}

# ---------------------------------------------------------------------------
# 1. Guarded cleanup of legacy /etc/ files installed by install.sh
# ---------------------------------------------------------------------------
for f in \
    /etc/udev/rules.d/70-gigamate.rules \
    /etc/udev/rules.d/99-gigamate-dgpu-ac.rules \
    /etc/udev/rules.d/99-gigamate-smartshift.rules; do
    cleanup_etc_with_header "$f" '^#[[:space:]]*(GigaMate|Notify GigaMate)'
done

for f in \
    /etc/polkit-1/rules.d/50-gigamate-dgpu.rules \
    /etc/polkit-1/rules.d/50-gigamate-dgpu-gate.rules \
    /etc/polkit-1/rules.d/50-gigamate-repair.rules \
    /etc/polkit-1/rules.d/50-gigamate-powerd.rules; do
    cleanup_etc_with_header "$f" '^//[[:space:]]*GigaMate'
done

cleanup_etc_modules_load /etc/modules-load.d/gigamate_acpi.conf

# ---------------------------------------------------------------------------
# 2. Guarded $SUDO_USER legacy user-level cleanup (never touches ~/.config/gigamate/)
# ---------------------------------------------------------------------------
cleanup_sudo_user_legacy() {
    local user="${SUDO_USER:-}"
    if [[ -z "$user" || "$user" == "root" ]]; then
        return 0
    fi

    local user_home=""
    if command -v getent >/dev/null 2>&1; then
        user_home="$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)"
    fi
    if [[ -z "$user_home" || ! -d "$user_home" ]]; then
        return 0
    fi

    # Legacy pip/setuptools entry-point scripts in ~/.local/bin/
    local bin_dir="$user_home/.local/bin"
    for cmd in gigamate gigamate-tray gigamate-center gigamate-dgpu-watch; do
        local script="$bin_dir/$cmd"
        if [[ -f "$script" ]]; then
            if [[ ! -L "$script" ]] || [[ "$(readlink -f "$script" 2>/dev/null || true)" == */pipx/venvs/gigamate/bin/* ]]; then
                if head -n 1 "$script" 2>/dev/null | grep -qE '^#!.*python' \
                   && grep -qE 'from[[:space:]]+gigamate\.|import[[:space:]]+gigamate|load_entry_point\(.*gigamate|gigamate\.(cli|tray|ui|dgpu_tune)' "$script" 2>/dev/null; then
                    rm -f "$script" || true
                fi
            fi
        fi
    done

    # Legacy systemd user units referencing .local/bin/gigamate + wants symlinks
    local systemd_user_dir="$user_home/.config/systemd/user"
    local wants_dir="$systemd_user_dir/graphical-session.target.wants"
    for unit in gigamate.service gigamate-dgpu.service; do
        local unit_file="$systemd_user_dir/$unit"
        local removed_unit=0
        if [[ -f "$unit_file" && ! -L "$unit_file" ]]; then
            if grep -q '\.local/bin/gigamate' "$unit_file" 2>/dev/null; then
                rm -f "$unit_file" || true
                removed_unit=1
            fi
        fi
        local want_link="$wants_dir/$unit"
        if [[ -L "$want_link" ]]; then
            if [[ ! -e "$want_link" || "$removed_unit" -eq 1 ]]; then
                rm -f "$want_link" || true
            fi
        fi
    done

    # Legacy desktop entries and icons in ~/.local/share/ (guarded against user customizations/symlinks)
    shopt -s nullglob
    for desktop in "$user_home"/.local/share/applications/gigamate*.desktop; do
        if [[ -f "$desktop" && ! -L "$desktop" ]]; then
            local base=""
            base="$(basename "$desktop")"
            local pkg_desktop="/usr/share/applications/$base"
            if { [[ -f "$pkg_desktop" ]] && cmp -s "$desktop" "$pkg_desktop" 2>/dev/null; } \
               || { grep -q '^StartupWMClass=gigamate-center' "$desktop" 2>/dev/null \
                    && grep -q '\.local/bin/gigamate' "$desktop" 2>/dev/null; }; then
                rm -f "$desktop" || true
            fi
        fi
    done
    for icon in \
        "$user_home"/.local/share/icons/hicolor/scalable/apps/gigamate*.svg \
        "$user_home"/.local/share/icons/hicolor/scalable/apps/checkbox-checked.svg; do
        if [[ -f "$icon" && ! -L "$icon" ]]; then
            local base=""
            base="$(basename "$icon")"
            local pkg_icon="/usr/share/icons/hicolor/scalable/apps/$base"
            if [[ -f "$pkg_icon" ]] && cmp -s "$icon" "$pkg_icon" 2>/dev/null; then
                rm -f "$icon" || true
            fi
        fi
    done
    shopt -u nullglob

    # Best-effort user systemd reload & restart if user D-Bus session is active
    local uid=""
    uid="$(id -u "$user" 2>/dev/null || true)"
    if [[ -n "$uid" && -S "/run/user/$uid/bus" ]] && command -v systemctl >/dev/null 2>&1; then
        su -s /bin/sh "$user" -c \
            "XDG_RUNTIME_DIR='/run/user/$uid' DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/$uid/bus' systemctl --user daemon-reload" \
            >/dev/null 2>&1 || true
        su -s /bin/sh "$user" -c \
            "XDG_RUNTIME_DIR='/run/user/$uid' DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/$uid/bus' systemctl --user try-restart gigamate.service gigamate-dgpu.service" \
            >/dev/null 2>&1 || true
    fi
}

cleanup_sudo_user_legacy

# ---------------------------------------------------------------------------
# 3. Best-effort udev, systemd global preset/enable, and desktop/icon caches
# ---------------------------------------------------------------------------
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=hidraw --subsystem-match=usb >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --global enable gigamate.service gigamate-dgpu.service >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || true
fi

exit 0
