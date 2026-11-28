"""Tests for canonical FHS staging script and package lifecycle hooks."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_SH = REPO_ROOT / "packaging" / "stage.sh"
SCRIPTS_DIR = REPO_ROOT / "packaging" / "scripts"
LIFECYCLE_SCRIPTS = [
    SCRIPTS_DIR / "postinst-app.sh",
    SCRIPTS_DIR / "postrm-app.sh",
    SCRIPTS_DIR / "postinst-dkms.sh",
    SCRIPTS_DIR / "prerm-dkms.sh",
]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_stage_fhs_layout_and_permissions(tmp_path: Path) -> None:
    res = subprocess.run(
        [str(STAGE_SH), "5.0.0", "pacman", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"stage.sh failed: {res.stderr}"

    app = tmp_path / "app"
    dkms = tmp_path / "dkms"

    # Never stage anything under /etc
    assert not (app / "etc").exists()
    assert not (dkms / "etc").exists()

    # Python package staged under /usr/lib/gigamate/py/gigamate/ without bytecode
    py_pkg = app / "usr" / "lib" / "gigamate" / "py" / "gigamate"
    assert (py_pkg / "__init__.py").is_file()
    assert (py_pkg / "cli.py").is_file()
    assert (py_pkg / "ui" / "__init__.py").is_file()
    assert list(py_pkg.rglob("__pycache__")) == []
    assert list(py_pkg.rglob("*.pyc")) == []
    assert list(py_pkg.rglob("*.pyo")) == []

    # install-method marker
    method_file = app / "usr" / "lib" / "gigamate" / "install-method"
    assert method_file.read_text(encoding="utf-8") == "pacman\n"
    assert _mode(method_file) == 0o644

    # 4 executable launchers in /usr/bin/
    expected_launchers = {
        "gigamate": ("gigamate.cli", "main"),
        "gigamate-tray": ("gigamate.tray", "main"),
        "gigamate-center": ("gigamate.ui", "run_gui"),
        "gigamate-dgpu-watch": ("gigamate.dgpu_tune", "run_watcher"),
    }
    for name, (mod, fn) in expected_launchers.items():
        launcher = app / "usr" / "bin" / name
        assert launcher.is_file(), f"Missing launcher {name}"
        assert _mode(launcher) == 0o755
        text = launcher.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/python3\n")
        assert "/usr/lib/gigamate/py" in text
        assert f"from {mod} import {fn}" in text
        assert f"raise SystemExit({fn}())" in text

    # 3 privileged helpers in /usr/lib/gigamate/
    for helper in ("gigamate-dgpu-gate", "gigamate-dgpu-nvml", "gigamate-repair"):
        p = app / "usr" / "lib" / "gigamate" / helper
        assert p.is_file(), f"Missing helper {helper}"
        assert _mode(p) == 0o755

    # Systemd user units with rewritten ExecStart
    tray_unit = app / "usr" / "lib" / "systemd" / "user" / "gigamate.service"
    dgpu_unit = app / "usr" / "lib" / "systemd" / "user" / "gigamate-dgpu.service"
    assert _mode(tray_unit) == 0o644
    assert _mode(dgpu_unit) == 0o644
    assert "ExecStart=/usr/bin/gigamate-tray" in tray_unit.read_text(encoding="utf-8")
    assert ".local/bin" not in tray_unit.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/gigamate-dgpu-watch" in dgpu_unit.read_text(encoding="utf-8")
    assert ".local/bin" not in dgpu_unit.read_text(encoding="utf-8")

    # Systemd user preset
    preset = app / "usr" / "lib" / "systemd" / "user-preset" / "90-gigamate.preset"
    assert preset.is_file()
    assert _mode(preset) == 0o644

    # Udev rules
    for rule in (
        "70-gigamate.rules",
        "99-gigamate-dgpu-ac.rules",
        "99-gigamate-smartshift.rules",
    ):
        p = app / "usr" / "lib" / "udev" / "rules.d" / rule
        assert p.is_file(), f"Missing udev rule {rule}"
        assert _mode(p) == 0o644

    # Polkit actions and consolidated rule
    for policy in (
        "org.gigamate.dgpu.policy",
        "org.gigamate.dgpu-gate.policy",
        "org.gigamate.repair.policy",
    ):
        p = app / "usr" / "share" / "polkit-1" / "actions" / policy
        assert p.is_file(), f"Missing polkit policy {policy}"
        assert _mode(p) == 0o644

    polkit_rule = app / "usr" / "share" / "polkit-1" / "rules.d" / "50-gigamate.rules"
    assert polkit_rule.is_file()
    assert _mode(polkit_rule) == 0o644

    # Desktop entries
    for desktop in ("gigamate.desktop", "gigamate-center.desktop"):
        p = app / "usr" / "share" / "applications" / desktop
        assert p.is_file(), f"Missing desktop file {desktop}"
        assert _mode(p) == 0o644

    # All SVG icons including checkbox-checked.svg
    src_svgs = sorted(p.name for p in (REPO_ROOT / "data").glob("*.svg"))
    assert "checkbox-checked.svg" in src_svgs
    icons_dir = app / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    for svg in src_svgs:
        p = icons_dir / svg
        assert p.is_file(), f"Missing icon {svg}"
        assert _mode(p) == 0o644

    # DKMS tree
    dkms_src = dkms / "usr" / "src" / "gigamate_acpi-5.0.0"
    for fname in ("gigamate_acpi.c", "Makefile", "dkms.conf"):
        p = dkms_src / fname
        assert p.is_file(), f"Missing DKMS file {fname}"
        assert _mode(p) == 0o644
    assert 'PACKAGE_VERSION="5.0.0"' in (dkms_src / "dkms.conf").read_text(encoding="utf-8")

    modload = dkms / "usr" / "lib" / "modules-load.d" / "gigamate_acpi.conf"
    assert modload.read_text(encoding="utf-8") == "gigamate_acpi\n"
    assert _mode(modload) == 0o644


def test_staged_launcher_executes_cli_help(tmp_path: Path) -> None:
    subprocess.run(
        [str(STAGE_SH), "5.0.0", "pacman", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    launcher = tmp_path / "app" / "usr" / "bin" / "gigamate"
    env = dict(os.environ)
    env["PYTHONPATH"] = ""
    res = subprocess.run(
        ["python3", str(launcher), "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"launcher --help failed: {res.stderr}"
    assert "gigamate" in res.stdout.lower()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["5.0.0"],
        ["5.0.0", "pacman"],
        ["", "pacman", "/tmp/out"],
        ["5.0.0", "invalid-method", "/tmp/out"],
        ["5.0.0", "pip", "/tmp/out"],
        ["5.0.0", "pacman", ""],
    ],
)
def test_stage_invalid_args_exit_nonzero(args: list[str]) -> None:
    res = subprocess.run(
        [str(STAGE_SH), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0


def test_shell_scripts_syntax_and_executable() -> None:
    all_scripts = [STAGE_SH, *LIFECYCLE_SCRIPTS]
    for script in all_scripts:
        assert script.is_file(), f"Missing script {script}"
        assert os.access(script, os.X_OK), f"Script not executable: {script}"
        res = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"bash -n failed for {script}: {res.stderr}"


def _make_minimal_bin(tmp_path: Path) -> Path:
    """Create a minimal PATH containing only basic shell/coreutils (no systemd/udev/dkms)."""
    bin_dir = tmp_path / "minbin"
    bin_dir.mkdir(exist_ok=True)
    for tool in ("env", "bash", "sh", "find", "sed", "sort", "tail", "basename"):
        src = shutil.which(tool)
        if src:
            dst = bin_dir / tool
            if not dst.exists():
                dst.symlink_to(src)
    return bin_dir


def _make_recording_stub(bin_dir: Path, name: str, log_file: Path) -> None:
    stub = bin_dir / name
    stub.write_text(
        f"#!/bin/sh\necho \"{name} $*\" >> \"{log_file}\"\nexit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


@pytest.mark.parametrize(
    "script_name",
    ["postrm-app.sh", "postinst-dkms.sh", "prerm-dkms.sh"],
)
def test_lifecycle_scripts_container_safe_minimal_path(
    tmp_path: Path, script_name: str
) -> None:
    min_bin = _make_minimal_bin(tmp_path)
    res = subprocess.run(
        [str(SCRIPTS_DIR / script_name)],
        env={"PATH": str(min_bin)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"{script_name} failed under minimal PATH: {res.stderr}"


@pytest.mark.parametrize(
    ("action", "expect_disable"),
    [
        ("remove", True),
        ("purge", True),
        ("0", True),
        ("upgrade", False),
        ("1", False),
    ],
)
def test_postrm_app_action_branching(
    tmp_path: Path, action: str, expect_disable: bool
) -> None:
    min_bin = _make_minimal_bin(tmp_path)
    log_file = tmp_path / "calls.log"
    _make_recording_stub(min_bin, "systemctl", log_file)
    _make_recording_stub(min_bin, "udevadm", log_file)

    res = subprocess.run(
        [str(SCRIPTS_DIR / "postrm-app.sh"), action],
        env={"PATH": str(min_bin)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    calls = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    disable_line = "systemctl --global disable gigamate.service gigamate-dgpu.service"
    assert (disable_line in calls) is expect_disable
    assert "udevadm control --reload-rules" in calls


@pytest.mark.parametrize(
    ("action", "expect_unload"),
    [
        ("remove", True),
        ("upgrade", True),
        ("deconfigure", True),
        ("0", True),
        ("1", False),
    ],
)
def test_prerm_dkms_action_branching(
    tmp_path: Path, action: str, expect_unload: bool
) -> None:
    min_bin = _make_minimal_bin(tmp_path)
    log_file = tmp_path / "calls.log"
    _make_recording_stub(min_bin, "modprobe", log_file)

    res = subprocess.run(
        [str(SCRIPTS_DIR / "prerm-dkms.sh"), action],
        env={"PATH": str(min_bin)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    calls = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert ("modprobe -r gigamate_acpi" in calls) is expect_unload

