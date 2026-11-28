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
TEST_CONTAINERS = REPO_ROOT / "packaging" / "test-containers.sh"
PUBLISH_REPOS = REPO_ROOT / "packaging" / "publish-repos.sh"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


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
    assert "build()" in text
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


def test_arch_install_cleanup_etc_helpers_behavior(tmp_path: Path) -> None:
    # 1. _gigamate_cleanup_etc_with_header
    matched_udev = tmp_path / "70-gigamate.rules"
    matched_udev.write_text("# GigaMate HID permissions\nSUBSYSTEM==\"hidraw\"\n", encoding="utf-8")

    custom_udev = tmp_path / "99-custom.rules"
    custom_udev.write_text("# Sysadmin custom udev rule\nACTION==\"add\"\n", encoding="utf-8")

    sym_target = tmp_path / "real-rule.rules"
    sym_target.write_text("# GigaMate symlinked rule\n", encoding="utf-8")
    sym_udev = tmp_path / "sym-gigamate.rules"
    sym_udev.symlink_to(sym_target)

    # 2. _gigamate_cleanup_etc_modules_load
    matched_mod = tmp_path / "gigamate_acpi.conf"
    matched_mod.write_text("# Auto-load\ngigamate_acpi\n", encoding="utf-8")

    custom_mod = tmp_path / "custom_mod.conf"
    custom_mod.write_text("gigamate_acpi\nother_module\n", encoding="utf-8")

    sym_mod_target = tmp_path / "real-mod.conf"
    sym_mod_target.write_text("gigamate_acpi\n", encoding="utf-8")
    sym_mod = tmp_path / "sym-mod.conf"
    sym_mod.symlink_to(sym_mod_target)

    script = f"""
set -euo pipefail
source "{ARCH_INSTALL}"
_gigamate_cleanup_etc_with_header "{matched_udev}" '^#[[:space:]]*(GigaMate|Notify GigaMate)'
_gigamate_cleanup_etc_with_header "{custom_udev}" '^#[[:space:]]*(GigaMate|Notify GigaMate)'
_gigamate_cleanup_etc_with_header "{sym_udev}" '^#[[:space:]]*(GigaMate|Notify GigaMate)'
_gigamate_cleanup_etc_modules_load "{matched_mod}"
_gigamate_cleanup_etc_modules_load "{custom_mod}"
_gigamate_cleanup_etc_modules_load "{sym_mod}"
"""
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"gigamate.install helper test failed: {res.stderr}"

    # Recognized header / modules-load -> removed
    assert not matched_udev.exists()
    assert not (tmp_path / "70-gigamate.rules.pre-pkg.bak").exists()
    assert not matched_mod.exists()
    assert not (tmp_path / "gigamate_acpi.conf.pre-pkg.bak").exists()

    # Unrecognized sysadmin file -> moved to *.pre-pkg.bak
    assert not custom_udev.exists()
    assert (tmp_path / "99-custom.rules.pre-pkg.bak").read_text(encoding="utf-8") == (
        "# Sysadmin custom udev rule\nACTION==\"add\"\n"
    )
    assert not custom_mod.exists()
    assert (tmp_path / "custom_mod.conf.pre-pkg.bak").read_text(encoding="utf-8") == (
        "gigamate_acpi\nother_module\n"
    )

    # Symlinks -> untouched
    assert sym_udev.is_symlink()
    assert sym_target.is_file()
    assert not (tmp_path / "sym-gigamate.rules.pre-pkg.bak").exists()
    assert sym_mod.is_symlink()
    assert sym_mod_target.is_file()
    assert not (tmp_path / "sym-mod.conf.pre-pkg.bak").exists()


def test_container_smoke_script_syntax_and_executable() -> None:
    assert TEST_CONTAINERS.is_file(), f"Missing {TEST_CONTAINERS}"
    assert os.access(TEST_CONTAINERS, os.X_OK), f"Not executable: {TEST_CONTAINERS}"
    res = subprocess.run(
        ["bash", "-n", str(TEST_CONTAINERS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"bash -n failed for test-containers.sh: {res.stderr}"


def test_container_smoke_script_help_and_invalid_distro(tmp_path: Path) -> None:
    # --help must work even without docker or podman on PATH
    clean_env = {"PATH": str(tmp_path)}
    res_help = subprocess.run(
        ["/usr/bin/env", "bash", str(TEST_CONTAINERS), "--help"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert res_help.returncode == 0, f"--help failed: {res_help.stderr}"
    assert "--distro" in res_help.stdout
    assert "--pkg-dir" in res_help.stdout

    # Also verify --help works with no container runtime in PATH at all
    res_help_no_rt = subprocess.run(
        ["/bin/bash", str(TEST_CONTAINERS), "--help"],
        capture_output=True,
        text=True,
        env=clean_env,
        check=False,
    )
    assert res_help_no_rt.returncode == 0, f"--help without runtime failed: {res_help_no_rt.stderr}"
    assert "--distro" in res_help_no_rt.stdout
    assert "--pkg-dir" in res_help_no_rt.stdout

    # Invalid --distro must exit non-zero
    res_invalid = subprocess.run(
        ["/bin/bash", str(TEST_CONTAINERS), "--distro", "gentoo"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert res_invalid.returncode != 0
    assert "gentoo" in res_invalid.stderr or "distro" in res_invalid.stderr.lower()

    # Missing container runtime must exit non-zero with a clear error message
    res_no_rt = subprocess.run(
        ["/bin/bash", str(TEST_CONTAINERS), "--distro", "ubuntu", "--pkg-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        env=clean_env,
        check=False,
    )
    assert res_no_rt.returncode != 0
    assert "docker" in res_no_rt.stderr.lower() or "podman" in res_no_rt.stderr.lower()


def test_container_smoke_script_invokes_all_four_distros(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "pkgs"
    pkg_dir.mkdir()
    log_dir = tmp_path / "invocations"
    log_dir.mkdir()

    fake_rt = tmp_path / "fake-runtime"
    fake_rt.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
idx="$(find "{log_dir}" -maxdepth 1 -name 'call-*.txt' | wc -l)"
out="{log_dir}/call-${{idx}}.txt"
printf '%s\\n' "$@" > "$out"
# The last argument is the in-container bash script; syntax-check it
bash -n <<< "${{@: -1}}"
""",
        encoding="utf-8",
    )
    fake_rt.chmod(0o755)

    env = dict(os.environ)
    env["CONTAINER_RUNTIME"] = str(fake_rt)

    res = subprocess.run(
        [str(TEST_CONTAINERS), "--distro", "all", "--pkg-dir", str(pkg_dir)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, f"test-containers.sh failed: {res.stderr}\n{res.stdout}"

    calls = sorted(log_dir.glob("call-*.txt"))
    assert len(calls) == 4

    expected = [
        ("ubuntu:24.04", "apt", "apt-get install -y /pkgs/deb/gigamate-dkms_*.deb /pkgs/deb/gigamate_*.deb", "apt-get remove -y gigamate gigamate-dkms"),
        ("fedora:41", "dnf", "dnf install -y /pkgs/fedora/gigamate-dkms-*.rpm /pkgs/fedora/gigamate-[0-9]*.rpm", "dnf remove -y gigamate gigamate-dkms"),
        ("opensuse/tumbleweed", "zypper", "zypper --non-interactive --no-gpg-checks install /pkgs/opensuse/gigamate-dkms-*.rpm /pkgs/opensuse/gigamate-[0-9]*.rpm", "zypper --non-interactive remove gigamate gigamate-dkms"),
        ("archlinux:latest", "pacman", "pacman -Syu --noconfirm && pacman -U --noconfirm /pkgs/arch/gigamate-dkms-*.pkg.tar.zst /pkgs/arch/gigamate-[0-9]*.pkg.tar.zst", "pacman -Rns --noconfirm gigamate gigamate-dkms"),
    ]

    for call_file, (image, method, install_cmd, remove_cmd) in zip(calls, expected):
        text = call_file.read_text(encoding="utf-8")
        assert f"{pkg_dir.resolve()}:/pkgs:ro" in text
        assert image in text
        assert install_cmd in text
        assert remove_cmd in text
        assert f'test "$(cat /usr/lib/gigamate/install-method)" = "{method}"' in text
        assert "test ! -e /etc/udev/rules.d/70-gigamate.rules" in text
        assert "test -f /etc/polkit-1/rules.d/50-gigamate-repair.rules.pre-pkg.bak" in text
        assert "test -f /usr/lib/udev/rules.d/70-gigamate.rules" in text
        assert "test -f /usr/share/polkit-1/rules.d/50-gigamate.rules" in text
        assert "test -f /usr/lib/systemd/user/gigamate.service" in text
        assert "/usr/bin/gigamate --help >/dev/null" in text
        assert "/usr/bin/gigamate version" in text
        assert "test ! -e /usr/bin/gigamate" in text
        assert "test ! -e /usr/lib/gigamate/install-method" in text


def test_publish_repos_script_syntax_and_executable() -> None:
    assert PUBLISH_REPOS.is_file(), f"Missing {PUBLISH_REPOS}"
    assert os.access(PUBLISH_REPOS, os.X_OK), f"Not executable: {PUBLISH_REPOS}"
    res = subprocess.run(
        ["bash", "-n", str(PUBLISH_REPOS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"bash -n failed for publish-repos.sh: {res.stderr}"


def test_publish_repos_script_help_and_missing_args() -> None:
    res_help = subprocess.run(
        ["/bin/bash", str(PUBLISH_REPOS), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_help.returncode == 0, f"--help failed: {res_help.stderr}"
    for flag in ("--pkg-dir", "--out-dir", "--gpg-key", "--gpg-passphrase-env"):
        assert flag in res_help.stdout

    res_missing = subprocess.run(
        ["/bin/bash", str(PUBLISH_REPOS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_missing.returncode != 0


def test_release_workflow_structure_and_gating() -> None:
    assert RELEASE_WORKFLOW.is_file(), f"Missing {RELEASE_WORKFLOW}"
    assert yaml is not None, "PyYAML is required to validate release.yml"
    raw = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert isinstance(data, dict)

    # PyYAML 1.1 parses unquoted 'on:' as boolean True
    triggers = data.get("on") if "on" in data else data.get(True)
    assert isinstance(triggers, dict), "Missing 'on' triggers in release.yml"
    assert "v*" in triggers.get("push", {}).get("tags", [])
    assert "workflow_dispatch" in triggers
    dry_run_input = triggers["workflow_dispatch"]["inputs"]["dry_run"]
    assert dry_run_input["type"] == "boolean"
    assert dry_run_input["default"] is True

    jobs = data.get("jobs", {})
    expected_jobs = {
        "build-packages",
        "verify-packages",
        "publish-release",
        "publish-repos",
        "publish-aur",
    }
    assert expected_jobs.issubset(jobs.keys()), f"Missing jobs: {expected_jobs - set(jobs.keys())}"

    # verify-packages must run all 4 distros and depend on build-packages
    verify = jobs["verify-packages"]
    verify_needs = verify["needs"] if isinstance(verify["needs"], list) else [verify["needs"]]
    assert "build-packages" in verify_needs
    assert set(verify["strategy"]["matrix"]["distro"]) == {
        "ubuntu",
        "fedora",
        "opensuse",
        "arch",
    }

    # All publish jobs must be gated by verify-packages
    for pub_job in ("publish-release", "publish-repos", "publish-aur"):
        needs = jobs[pub_job]["needs"]
        needs_list = needs if isinstance(needs, list) else [needs]
        assert "verify-packages" in needs_list, f"{pub_job} must depend on verify-packages"

    aur_needs = jobs["publish-aur"]["needs"]
    assert "publish-release" in (aur_needs if isinstance(aur_needs, list) else [aur_needs])

    # Secrets isolation: GPG_PRIVATE_KEY only in publish-repos, AUR_SSH_PRIVATE_KEY only in publish-aur
    for job_name, job_cfg in jobs.items():
        job_yaml = yaml.safe_dump(job_cfg)
        if job_name == "publish-repos":
            assert "GPG_PRIVATE_KEY" in job_yaml
        else:
            assert "GPG_PRIVATE_KEY" not in job_yaml, f"GPG_PRIVATE_KEY leaked into {job_name}"

        if job_name == "publish-aur":
            assert "AUR_SSH_PRIVATE_KEY" in job_yaml
        else:
            assert "AUR_SSH_PRIVATE_KEY" not in job_yaml, f"AUR_SSH_PRIVATE_KEY leaked into {job_name}"

    # Preserve install.sh fallback checksum assets
    assert "install.sh.sha256" in raw
    assert "gigamate.tar.gz.sha256" in raw
