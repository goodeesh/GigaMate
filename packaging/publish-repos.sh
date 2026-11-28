#!/usr/bin/env bash
# Build the GPG-signed static package repository tree for GitHub Pages.
# Supports APT (Ubuntu/Debian), RPM (Fedora & openSUSE), and Pacman (Arch).
#
# Usage:
#   packaging/publish-repos.sh --pkg-dir <dir> --out-dir <dir> --gpg-key <key> [--gpg-passphrase-env VAR]
set -euo pipefail

usage() {
    printf '%s\n' \
        "Usage: packaging/publish-repos.sh [OPTIONS]" \
        "" \
        "Generate GPG-signed APT, RPM (Fedora/openSUSE), and Arch repositories" \
        "under <out-dir>/repo/ for static hosting on GitHub Pages." \
        "" \
        "Required options:" \
        "  --pkg-dir <dir>              Directory containing built packages (deb/, fedora/, opensuse/, arch/)" \
        "  --out-dir <dir>              Output directory for the static site (repo/, index.html, .nojekyll)" \
        "  --gpg-key <fingerprint|id>   GPG key fingerprint or ID present in the active keyring" \
        "" \
        "Optional:" \
        "  --gpg-passphrase-env <VAR>   Environment variable holding the GPG key passphrase (if any)" \
        "  -h, --help                   Show this help message and exit"
}

PKG_DIR=""
OUT_DIR=""
GPG_KEY=""
GPG_PASSPHRASE_ENV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --out-dir)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --out-dir requires a value" >&2
                usage >&2
                exit 2
            fi
            OUT_DIR="$2"
            shift 2
            ;;
        --out-dir=*)
            OUT_DIR="${1#*=}"
            shift
            ;;
        --gpg-key)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --gpg-key requires a value" >&2
                usage >&2
                exit 2
            fi
            GPG_KEY="$2"
            shift 2
            ;;
        --gpg-key=*)
            GPG_KEY="${1#*=}"
            shift
            ;;
        --gpg-passphrase-env)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "Error: --gpg-passphrase-env requires a value" >&2
                usage >&2
                exit 2
            fi
            GPG_PASSPHRASE_ENV="$2"
            shift 2
            ;;
        --gpg-passphrase-env=*)
            GPG_PASSPHRASE_ENV="${1#*=}"
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

if [[ -z "$PKG_DIR" || -z "$OUT_DIR" || -z "$GPG_KEY" ]]; then
    echo "Error: --pkg-dir, --out-dir, and --gpg-key are required" >&2
    usage >&2
    exit 2
fi

if [[ ! -d "$PKG_DIR" ]]; then
    echo "Error: package directory does not exist: '$PKG_DIR'" >&2
    exit 1
fi

if [[ -n "$GPG_PASSPHRASE_ENV" && ! "$GPG_PASSPHRASE_ENV" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Error: invalid environment variable name for --gpg-passphrase-env: '$GPG_PASSPHRASE_ENV'" >&2
    exit 2
fi

if ! command -v gpg >/dev/null 2>&1; then
    echo "Error: gpg is required" >&2
    exit 1
fi

PKG_DIR_ABS="$(cd "$PKG_DIR" && pwd)"
mkdir -p "$OUT_DIR"
OUT_DIR_ABS="$(cd "$OUT_DIR" && pwd)"
REPO_DIR="${OUT_DIR_ABS}/repo"

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

PASS_FILE=""
if [[ -n "$GPG_PASSPHRASE_ENV" ]]; then
    pass_val="${!GPG_PASSPHRASE_ENV:-}"
    if [[ -n "$pass_val" ]]; then
        PASS_FILE="${TMP_DIR}/gpg-pass"
        (
            umask 077
            printf '%s' "$pass_val" > "$PASS_FILE"
        )
    fi
fi

gpg_sign() {
    local -a args=(
        --batch
        --yes
        --pinentry-mode loopback
        --local-user "$GPG_KEY"
    )
    if [[ -n "$PASS_FILE" ]]; then
        args+=(--passphrase-file "$PASS_FILE")
    fi
    gpg "${args[@]}" "$@"
}

# ---------------------------------------------------------------------------
# 1. Public GPG keys (ASCII-armored + binary)
# ---------------------------------------------------------------------------
rm -rf "$REPO_DIR"
mkdir -p "$REPO_DIR"

echo "==> Exporting GPG public key (${GPG_KEY})..."
gpg --batch --yes --armor --export "$GPG_KEY" > "${REPO_DIR}/gigamate.asc"
gpg --batch --yes --export "$GPG_KEY" > "${REPO_DIR}/gigamate.gpg"

if [[ ! -s "${REPO_DIR}/gigamate.asc" || ! -s "${REPO_DIR}/gigamate.gpg" ]]; then
    echo "Error: failed to export public key for '${GPG_KEY}' (key not found in keyring?)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. APT repository (repo/apt/)
# ---------------------------------------------------------------------------
echo "==> Building APT repository..."
APT_DIR="${REPO_DIR}/apt"
mkdir -p "${APT_DIR}/pool/main" "${APT_DIR}/dists/stable/main/binary-all"

shopt -s nullglob
deb_files=("${PKG_DIR_ABS}"/deb/gigamate_*.deb "${PKG_DIR_ABS}"/deb/gigamate-dkms_*.deb)
shopt -u nullglob
if [[ ${#deb_files[@]} -eq 0 ]]; then
    echo "Error: no gigamate .deb packages found in '${PKG_DIR_ABS}/deb/'" >&2
    exit 1
fi
cp -f "${deb_files[@]}" "${APT_DIR}/pool/main/"

PACKAGES_REL="dists/stable/main/binary-all/Packages"
(
    cd "$APT_DIR"
    if command -v dpkg-scanpackages >/dev/null 2>&1; then
        dpkg-scanpackages --arch all pool/main > "$PACKAGES_REL"
    elif command -v apt-ftparchive >/dev/null 2>&1; then
        apt-ftparchive packages pool/main > "$PACKAGES_REL"
    else
        echo "Error: neither dpkg-scanpackages nor apt-ftparchive is installed" >&2
        exit 1
    fi
    gzip -9c "$PACKAGES_REL" > "${PACKAGES_REL}.gz"

    if ! command -v apt-ftparchive >/dev/null 2>&1; then
        echo "Error: apt-ftparchive is required to generate APT Release metadata" >&2
        exit 1
    fi
    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin="GigaMate" \
        -o APT::FTPArchive::Release::Label="GigaMate" \
        -o APT::FTPArchive::Release::Suite="stable" \
        -o APT::FTPArchive::Release::Codename="stable" \
        -o APT::FTPArchive::Release::Architectures="all" \
        -o APT::FTPArchive::Release::Components="main" \
        release dists/stable > "${TMP_DIR}/Release"
    mv "${TMP_DIR}/Release" dists/stable/Release
)

gpg_sign --detach-sign --armor --output "${APT_DIR}/dists/stable/Release.gpg" "${APT_DIR}/dists/stable/Release"
gpg_sign --clearsign --output "${APT_DIR}/dists/stable/InRelease" "${APT_DIR}/dists/stable/Release"

# ---------------------------------------------------------------------------
# 3. RPM repositories (repo/rpm/fedora/ and repo/rpm/opensuse/)
# ---------------------------------------------------------------------------
if ! command -v createrepo_c >/dev/null 2>&1; then
    echo "Error: createrepo_c is required to build RPM repositories" >&2
    exit 1
fi
if ! command -v rpmsign >/dev/null 2>&1; then
    echo "Error: rpmsign is required to sign RPM packages" >&2
    exit 1
fi

for distro in fedora opensuse; do
    echo "==> Building RPM repository (${distro})..."
    RPM_DIR="${REPO_DIR}/rpm/${distro}"
    mkdir -p "$RPM_DIR"

    shopt -s nullglob
    rpm_files=("${PKG_DIR_ABS}/${distro}"/*.rpm)
    shopt -u nullglob
    if [[ ${#rpm_files[@]} -eq 0 ]]; then
        echo "Error: no .rpm packages found in '${PKG_DIR_ABS}/${distro}/'" >&2
        exit 1
    fi
    cp -f "${rpm_files[@]}" "$RPM_DIR/"

    if [[ -n "$PASS_FILE" ]]; then
        gpg_sign_macro="%{__gpg} gpg --batch --no-verbose --no-armor --pinentry-mode loopback --passphrase-file ${PASS_FILE} --no-secmem-warning -u %{_gpg_name} -sbo %{__signature_filename} %{__plaintext_filename}"
    else
        gpg_sign_macro="%{__gpg} gpg --batch --no-verbose --no-armor --pinentry-mode loopback --no-secmem-warning -u %{_gpg_name} -sbo %{__signature_filename} %{__plaintext_filename}"
    fi
    rpmsign \
        -D "_gpg_name ${GPG_KEY}" \
        -D "__gpg_sign_cmd ${gpg_sign_macro}" \
        --addsign "$RPM_DIR"/*.rpm

    createrepo_c "$RPM_DIR"
    gpg_sign --detach-sign --armor \
        --output "${RPM_DIR}/repodata/repomd.xml.asc" \
        "${RPM_DIR}/repodata/repomd.xml"
done

# ---------------------------------------------------------------------------
# 4. Arch Linux repository (repo/arch/any/)
# ---------------------------------------------------------------------------
echo "==> Building Arch repository..."
ARCH_DIR="${REPO_DIR}/arch/any"
mkdir -p "$ARCH_DIR"

shopt -s nullglob
arch_pkgs=("${PKG_DIR_ABS}/arch"/*.pkg.tar.zst)
shopt -u nullglob
if [[ ${#arch_pkgs[@]} -eq 0 ]]; then
    echo "Error: no .pkg.tar.zst packages found in '${PKG_DIR_ABS}/arch/'" >&2
    exit 1
fi
cp -f "${arch_pkgs[@]}" "$ARCH_DIR/"

for pkg in "$ARCH_DIR"/*.pkg.tar.zst; do
    gpg_sign --detach-sign --output "${pkg}.sig" "$pkg"
done

ARCH_GPG_DIR="${TMP_DIR}/arch-gpg"
(
    umask 077
    mkdir -p "$ARCH_GPG_DIR"
    if [[ -n "$PASS_FILE" ]]; then
        gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASS_FILE" \
            --armor --export-secret-keys "$GPG_KEY" > "${ARCH_GPG_DIR}/sec.asc"
        cp "$PASS_FILE" "${ARCH_GPG_DIR}/pass"
    else
        gpg --batch --yes --pinentry-mode loopback \
            --armor --export-secret-keys "$GPG_KEY" > "${ARCH_GPG_DIR}/sec.asc"
    fi
)

if command -v repo-add >/dev/null 2>&1; then
    HOST_GNUPGHOME="${TMP_DIR}/host-gnupg"
    (
        umask 077
        mkdir -p "$HOST_GNUPGHOME"
        export GNUPGHOME="$HOST_GNUPGHOME"
        printf "allow-loopback-pinentry\n" > "${GNUPGHOME}/gpg-agent.conf"
        printf "batch\npinentry-mode loopback\n" > "${GNUPGHOME}/gpg.conf"
        if [[ -f "${ARCH_GPG_DIR}/pass" ]]; then
            gpg --batch --yes --pinentry-mode loopback --passphrase-file "${ARCH_GPG_DIR}/pass" --import "${ARCH_GPG_DIR}/sec.asc"
            printf "passphrase-file %s\n" "${ARCH_GPG_DIR}/pass" >> "${GNUPGHOME}/gpg.conf"
        else
            gpg --batch --yes --pinentry-mode loopback --import "${ARCH_GPG_DIR}/sec.asc"
        fi
        repo-add --sign --key "$GPG_KEY" "${ARCH_DIR}/gigamate.db.tar.gz" "${ARCH_DIR}"/*.pkg.tar.zst
    )
else
    CONTAINER_RT=""
    if command -v docker >/dev/null 2>&1; then
        CONTAINER_RT="docker"
    elif command -v podman >/dev/null 2>&1; then
        CONTAINER_RT="podman"
    else
        echo "Error: repo-add not found in PATH and neither docker nor podman is available" >&2
        exit 1
    fi

    "$CONTAINER_RT" run --rm \
        -v "${ARCH_DIR}:/arch" \
        -v "${ARCH_GPG_DIR}:/gpg:ro" \
        -e "GPG_KEY=${GPG_KEY}" \
        archlinux:latest \
        bash -euo pipefail -c '
            export GNUPGHOME="/tmp/gnupg"
            mkdir -p -m 700 "$GNUPGHOME"
            printf "allow-loopback-pinentry\n" > "$GNUPGHOME/gpg-agent.conf"
            printf "batch\npinentry-mode loopback\n" > "$GNUPGHOME/gpg.conf"
            if [[ -f /gpg/pass ]]; then
                gpg --batch --yes --pinentry-mode loopback --passphrase-file /gpg/pass --import /gpg/sec.asc
                printf "passphrase-file /gpg/pass\n" >> "$GNUPGHOME/gpg.conf"
            else
                gpg --batch --yes --pinentry-mode loopback --import /gpg/sec.asc
            fi
            repo-add --sign --key "$GPG_KEY" /arch/gigamate.db.tar.gz /arch/*.pkg.tar.zst
        '
fi

# Replace repo-add symlinks with regular files for reliable static hosting on GitHub Pages
for db_link in gigamate.db gigamate.db.sig gigamate.files gigamate.files.sig; do
    if [[ -L "${ARCH_DIR}/${db_link}" ]]; then
        target="$(readlink -f "${ARCH_DIR}/${db_link}")"
        rm -f "${ARCH_DIR}/${db_link}"
        cp -f "$target" "${ARCH_DIR}/${db_link}"
    fi
done

# ---------------------------------------------------------------------------
# 5. GitHub Pages root files (index.html + .nojekyll)
# ---------------------------------------------------------------------------
cat > "${OUT_DIR_ABS}/index.html" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url=https://github.com/goodeesh/GigaMate">
  <title>GigaMate Package Repository</title>
</head>
<body>
  <p>Redirecting to <a href="https://github.com/goodeesh/GigaMate">GigaMate on GitHub</a>…</p>
</body>
</html>
EOF
: > "${OUT_DIR_ABS}/.nojekyll"

echo "==> Repository tree ready in ${REPO_DIR}"
