"""Tests for nFPM package definitions, Arch split PKGBUILD, and local build script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - fallback when PyYAML is unavailable
    yaml = None

REPO_ROOT = Path(__file__).resolve().parent.parent
NFPM_DIR = REPO_ROOT / "packaging" / "nfpm"
ARCH_DIR = REPO_ROOT / "packaging" / "arch"
PKGBUILD_TMPL = ARCH_DIR / "PKGBUILD.tmpl"
ARCH_INSTALL = ARCH_DIR / "gigamate.install"
BUILD_LOCAL_ARCH = REPO_ROOT / "packaging" / "build-local-arch.sh"


def _parse_yaml_fallback(text: str) -> dict[str, Any]:
    """Minimal YAML subset parser for nFPM files when PyYAML is not installed."""
    data: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            current_item = None
            key, _, val = stripped.partition(":")
            val = val.strip()
            if not val:
                current_key = key
                data[key] = None
            else:
                current_key = None
                data[key] = val.strip('"').strip("'")
        elif current_key is not None:
            if stripped.startswith("- "):
                if data[current_key] is None:
                    data[current_key] = []
                item_str = stripped[2:].strip()
                if ":" in item_str and not (item_str.startswith('"') or item_str.startswith("'")):
                    k, _, v = item_str.partition(":")
                    current_item = {k.strip(): v.strip().strip('"').strip("'")}
                    data[current_key].append(current_item)
                else:
                    current_item = None
                    data[current_key].append(item_str.strip('"').strip("'"))
            elif current_item is not None:
                k, _, v = stripped.partition(":")
                current_item[k.strip()] = v.strip().strip('"').strip("'")
            else:
                if data[current_key] is None:
                    data[current_key] = {}
                k, _, v = stripped.partition(":")
                data[current_key][k.strip()] = v.strip().strip('"').strip("'")
    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(text)
        assert isinstance(loaded, dict), f"Expected YAML mapping in {path}"
        return loaded
    return _parse_yaml_fallback(text)


EXPECTED_NFPM: dict[str, dict[str, Any]] = {
    "gigamate-deb.yaml": {
        "name": "gigamate",
        "arch": "all",
        "section": "utils",
        "stage_src": "${STAGE_DIR}/app/usr/",
        "depends": [
            "python3 (>= 3.10)",
            "python3-pyqt6",
            "python3-evdev",
            "polkitd | policykit-1",
            "gigamate-dkms",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-app.sh",
            "postremove": "packaging/scripts/postrm-app.sh",
        },
    },
    "gigamate-dkms-deb.yaml": {
        "name": "gigamate-dkms",
        "arch": "all",
        "section": "kernel",
        "stage_src": "${STAGE_DIR}/dkms/usr/",
        "depends": [
            "dkms",
            "make",
            "gcc | clang",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-dkms.sh",
            "preremove": "packaging/scripts/prerm-dkms.sh",
        },
    },
    "gigamate-fedora.yaml": {
        "name": "gigamate",
        "arch": "noarch",
        "section": "utils",
        "stage_src": "${STAGE_DIR}/app/usr/",
        "depends": [
            "python3 >= 3.10",
            "python3-pyqt6",
            "python3-evdev",
            "polkit",
            "gigamate-dkms",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-app.sh",
            "postremove": "packaging/scripts/postrm-app.sh",
        },
    },
    "gigamate-dkms-fedora.yaml": {
        "name": "gigamate-dkms",
        "arch": "noarch",
        "section": "kernel",
        "stage_src": "${STAGE_DIR}/dkms/usr/",
        "depends": [
            "dkms",
            "kernel-devel",
            "make",
            "gcc",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-dkms.sh",
            "preremove": "packaging/scripts/prerm-dkms.sh",
        },
    },
    "gigamate-opensuse.yaml": {
        "name": "gigamate",
        "arch": "noarch",
        "section": "utils",
        "stage_src": "${STAGE_DIR}/app/usr/",
        "depends": [
            "python3 >= 3.10",
            "(python3-qt6 or python3-PyQt6)",
            "python3-evdev",
            "polkit",
            "gigamate-dkms",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-app.sh",
            "postremove": "packaging/scripts/postrm-app.sh",
        },
    },
    "gigamate-dkms-opensuse.yaml": {
        "name": "gigamate-dkms",
        "arch": "noarch",
        "section": "kernel",
        "stage_src": "${STAGE_DIR}/dkms/usr/",
        "depends": [
            "dkms",
            "kernel-default-devel",
            "make",
            "gcc",
        ],
        "scripts": {
            "postinstall": "packaging/scripts/postinst-dkms.sh",
            "preremove": "packaging/scripts/prerm-dkms.sh",
        },
    },
}


@pytest.mark.parametrize("filename", sorted(EXPECTED_NFPM))
def test_nfpm_yaml_definition(filename: str) -> None:
    path = NFPM_DIR / filename
    assert path.is_file(), f"Missing nFPM definition: {path}"
    spec = EXPECTED_NFPM[filename]
    data = _load_yaml(path)

    for key in (
        "name",
        "arch",
        "version",
        "maintainer",
        "homepage",
        "license",
        "section",
        "depends",
        "contents",
        "scripts",
    ):
        assert key in data, f"{filename} missing required key '{key}'"

    assert data["name"] == spec["name"]
    assert data["arch"] == spec["arch"]
    assert data["version"] == "${VERSION}"
    assert data["section"] == spec["section"]
    assert data["maintainer"] == "adriansc <goodeesh@users.noreply.github.com>"
    assert data["homepage"] == "https://github.com/goodeesh/GigaMate"
    assert data["license"] == "MIT"
    assert data["depends"] == spec["depends"]

    contents = data["contents"]
    assert isinstance(contents, list) and len(contents) == 1
    entry = contents[0]
    assert entry["src"] == spec["stage_src"]
    assert entry["dst"] == "/usr"
    assert entry["type"] == "tree"

    scripts = data["scripts"]
    assert scripts == spec["scripts"]
    for script_rel in scripts.values():
        script_path = REPO_ROOT / script_rel
        assert script_path.is_file(), f"{filename} references missing script: {script_rel}"


def test_arch_pkgbuild_template() -> None:
    assert PKGBUILD_TMPL.is_file(), f"Missing {PKGBUILD_TMPL}"
    text = PKGBUILD_TMPL.read_text(encoding="utf-8")

    assert "pkgbase=gigamate" in text
    assert "pkgname=('gigamate' 'gigamate-dkms')" in text
    assert "arch=('any')" in text
    assert "license=('MIT')" in text
    assert "url='https://github.com/goodeesh/GigaMate'" in text
    assert "package_gigamate()" in text
    assert "package_gigamate-dkms()" in text
    assert "install=gigamate.install" in text
    assert "packaging/stage.sh" in text

    for placeholder in (
        "@PKGVER@",
        "@SOURCE_URL@",
        "@SOURCE_SHA256@",
        "@SRC_SUBDIR@",
        "@INSTALL_METHOD@",
    ):
        assert placeholder in text, f"Missing placeholder {placeholder} in PKGBUILD.tmpl"


def test_arch_install_hook_syntax_and_safety() -> None:
    assert ARCH_INSTALL.is_file(), f"Missing {ARCH_INSTALL}"
    res = subprocess.run(
        ["bash", "-n", str(ARCH_INSTALL)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"bash -n failed for gigamate.install: {res.stderr}"

    text = ARCH_INSTALL.read_text(encoding="utf-8")
    for fn in ("post_install()", "post_upgrade()", "post_remove()"):
        assert fn in text, f"Missing {fn} in gigamate.install"

    assert ".pre-pkg.bak" in text
    assert "SUDO_USER" in text
    assert "gigamate.service" in text
    assert "gigamate-dgpu.service" in text
    assert "rm -rf" not in text
    # Must never touch ~/.config/gigamate/
    assert ".config/gigamate" not in text


def test_build_local_arch_script_syntax_and_executable() -> None:
    assert BUILD_LOCAL_ARCH.is_file(), f"Missing {BUILD_LOCAL_ARCH}"
    assert os.access(BUILD_LOCAL_ARCH, os.X_OK), f"Not executable: {BUILD_LOCAL_ARCH}"
    res = subprocess.run(
        ["bash", "-n", str(BUILD_LOCAL_ARCH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"bash -n failed for build-local-arch.sh: {res.stderr}"


def test_gitignore_ignores_dist() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "dist/" in gitignore
