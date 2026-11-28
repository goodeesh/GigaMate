#!/usr/bin/env bash
# Canonical FHS staging script for GigaMate native packages (deb, rpm, pacman, aur).
# Usage: packaging/stage.sh <version> <install-method> <out-dir>
set -euo pipefail

usage() {
    echo "Usage: $0 <version> <install-method> <out-dir>" >&2
    echo "  <install-method>: apt | dnf | zypper | pacman | aur" >&2
}

if [[ $# -ne 3 ]]; then
    usage
    exit 2
fi

VERSION="$1"
INSTALL_METHOD="$2"
OUT_DIR="$3"

if [[ -z "$VERSION" ]]; then
    echo "Error: <version> must be non-empty" >&2
    usage
    exit 2
fi

case "$INSTALL_METHOD" in
    apt|dnf|zypper|pacman|aur) ;;
    *)
        echo "Error: invalid <install-method>: '$INSTALL_METHOD'" >&2
        usage
        exit 2
        ;;
esac

if [[ -z "$OUT_DIR" ]]; then
    echo "Error: <out-dir> must be non-empty" >&2
    usage
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$OUT_DIR/app"
DKMS="$OUT_DIR/dkms"

rm -rf "$APP" "$DKMS"
mkdir -p "$APP" "$DKMS"

# ---------------------------------------------------------------------------
# App tree (<out-dir>/app)
# ---------------------------------------------------------------------------

# 1. Python package tree (immune to Python 3.x minor upgrades)
mkdir -p "$APP/usr/lib/gigamate/py"
cp -a "$REPO_ROOT/src/gigamate" "$APP/usr/lib/gigamate/py/"
find "$APP/usr/lib/gigamate/py" -type d -name '__pycache__' -exec rm -rf {} +
find "$APP/usr/lib/gigamate/py" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$APP/usr/lib/gigamate/py" -type d -exec chmod 0755 {} +
find "$APP/usr/lib/gigamate/py" -type f -exec chmod 0644 {} +

# 2. Install-method marker
printf '%s\n' "$INSTALL_METHOD" > "$APP/usr/lib/gigamate/install-method"
chmod 0644 "$APP/usr/lib/gigamate/install-method"

# 3. Executable Python launchers in /usr/bin/
write_launcher() {
    local name="$1"
    local module="$2"
    local func="$3"
    local target="$APP/usr/bin/$name"
    mkdir -p "$(dirname "$target")"
    cat > "$target" <<EOF
#!/usr/bin/python3
import os
import sys

sys.path.insert(0, "/usr/lib/gigamate/py")
_rel_py = os.path.abspath(
    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib", "gigamate", "py")
)
if _rel_py != "/usr/lib/gigamate/py" and os.path.isdir(_rel_py):
    sys.path.insert(0, _rel_py)

from ${module} import ${func}

raise SystemExit(${func}())
EOF
    chmod 0755 "$target"
}

write_launcher "gigamate"            "gigamate.cli"       "main"
write_launcher "gigamate-tray"       "gigamate.tray"      "main"
write_launcher "gigamate-center"     "gigamate.ui"        "run_gui"
write_launcher "gigamate-dgpu-watch" "gigamate.dgpu_tune" "run_watcher"

# 4. Privileged helpers in /usr/lib/gigamate/
for helper in gigamate-dgpu-gate gigamate-dgpu-nvml gigamate-repair; do
    install -Dm0755 "$REPO_ROOT/data/$helper" "$APP/usr/lib/gigamate/$helper"
done

# 5. Systemd user units (rewrite ExecStart to /usr/bin/*)
mkdir -p "$APP/usr/lib/systemd/user"
sed 's|^ExecStart=.*|ExecStart=/usr/bin/gigamate-tray|' \
    "$REPO_ROOT/data/gigamate.service" \
    > "$APP/usr/lib/systemd/user/gigamate.service"
chmod 0644 "$APP/usr/lib/systemd/user/gigamate.service"

sed 's|^ExecStart=.*|ExecStart=/usr/bin/gigamate-dgpu-watch|' \
    "$REPO_ROOT/data/gigamate-dgpu.service" \
    > "$APP/usr/lib/systemd/user/gigamate-dgpu.service"
chmod 0644 "$APP/usr/lib/systemd/user/gigamate-dgpu.service"

# 6. Systemd user preset
install -Dm0644 "$REPO_ROOT/data/90-gigamate.preset" \
    "$APP/usr/lib/systemd/user-preset/90-gigamate.preset"

# 7. Udev rules
for rule in 70-gigamate.rules 99-gigamate-dgpu-ac.rules 99-gigamate-smartshift.rules; do
    install -Dm0644 "$REPO_ROOT/data/$rule" "$APP/usr/lib/udev/rules.d/$rule"
done

# 8. Polkit actions
for policy in org.gigamate.dgpu.policy org.gigamate.dgpu-gate.policy org.gigamate.repair.policy; do
    install -Dm0644 "$REPO_ROOT/data/$policy" "$APP/usr/share/polkit-1/actions/$policy"
done

# 9. Consolidated Polkit rule
install -Dm0644 "$REPO_ROOT/data/50-gigamate.rules" \
    "$APP/usr/share/polkit-1/rules.d/50-gigamate.rules"

# 10. Desktop entries
for desktop in gigamate.desktop gigamate-center.desktop; do
    install -Dm0644 "$REPO_ROOT/data/$desktop" "$APP/usr/share/applications/$desktop"
done

# 11. SVG icons (including checkbox-checked.svg)
mkdir -p "$APP/usr/share/icons/hicolor/scalable/apps"
shopt -s nullglob
for svg in "$REPO_ROOT"/data/*.svg; do
    install -Dm0644 "$svg" "$APP/usr/share/icons/hicolor/scalable/apps/$(basename "$svg")"
done
shopt -u nullglob

# ---------------------------------------------------------------------------
# DKMS tree (<out-dir>/dkms)
# ---------------------------------------------------------------------------
DKMS_SRC="$DKMS/usr/src/gigamate_acpi-${VERSION}"
install -Dm0644 "$REPO_ROOT/src/gigamate_acpi/gigamate_acpi.c" "$DKMS_SRC/gigamate_acpi.c"
install -Dm0644 "$REPO_ROOT/src/gigamate_acpi/Makefile" "$DKMS_SRC/Makefile"
sed "s|^PACKAGE_VERSION=.*|PACKAGE_VERSION=\"${VERSION}\"|" \
    "$REPO_ROOT/src/gigamate_acpi/dkms.conf" \
    > "$DKMS_SRC/dkms.conf"
chmod 0644 "$DKMS_SRC/dkms.conf"

mkdir -p "$DKMS/usr/lib/modules-load.d"
printf 'gigamate_acpi\n' > "$DKMS/usr/lib/modules-load.d/gigamate_acpi.conf"
chmod 0644 "$DKMS/usr/lib/modules-load.d/gigamate_acpi.conf"
