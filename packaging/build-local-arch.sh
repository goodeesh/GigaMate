#!/usr/bin/env bash
# Build Arch Linux split packages (gigamate + gigamate-dkms) locally into dist/arch/.
# Usage: packaging/build-local-arch.sh [version]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -ge 1 && -n "$1" ]]; then
    VERSION="$1"
else
    VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*["'\'']([^"'\'']+)["'\'']/\1/p' "$REPO_ROOT/src/gigamate/__init__.py" | head -n 1)"
fi

if [[ -z "$VERSION" ]]; then
    echo "Error: could not determine version from src/gigamate/__init__.py" >&2
    exit 1
fi

if ! command -v makepkg >/dev/null 2>&1; then
    echo "Error: makepkg is required to build Arch packages" >&2
    exit 1
fi

BUILD_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

SRC_SUBDIR="GigaMate-${VERSION}"
TARBALL_NAME="${SRC_SUBDIR}.tar.gz"
TARBALL_PATH="${BUILD_DIR}/${TARBALL_NAME}"

# Create a clean source tarball from the working tree
tar \
    --exclude='.git' \
    --exclude='./dist' \
    --exclude='./build' \
    --exclude='./.venv' \
    --exclude='./venv' \
    --exclude='./env' \
    --exclude='./.pytest_cache' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --transform="s,^\.,${SRC_SUBDIR}," \
    -czf "$TARBALL_PATH" \
    -C "$REPO_ROOT" .

SHA256="$(sha256sum "$TARBALL_PATH" | awk '{print $1}')"

sed \
    -e "s|@PKGVER@|${VERSION}|g" \
    -e "s|@SOURCE_URL@|${TARBALL_NAME}|g" \
    -e "s|@SOURCE_SHA256@|${SHA256}|g" \
    -e "s|@SRC_SUBDIR@|${SRC_SUBDIR}|g" \
    -e "s|@INSTALL_METHOD@|pacman|g" \
    "$REPO_ROOT/packaging/arch/PKGBUILD.tmpl" > "$BUILD_DIR/PKGBUILD"

cp "$REPO_ROOT/packaging/arch/gigamate.install" "$BUILD_DIR/gigamate.install"

(
    cd "$BUILD_DIR"
    PKGDEST="$BUILD_DIR" SRCDEST="$BUILD_DIR" PKGEXT='.pkg.tar.zst' makepkg -sf --noconfirm
)

OUT_DIR="$REPO_ROOT/dist/arch"
mkdir -p "$OUT_DIR"
cp -f "$BUILD_DIR"/gigamate-*.pkg.tar.zst "$OUT_DIR/"

echo "Built Arch packages in $OUT_DIR:"
ls -la "$OUT_DIR"/gigamate-*.pkg.tar.zst
