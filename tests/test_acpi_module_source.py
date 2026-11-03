import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = REPO_ROOT / "src" / "gigamate_acpi"
MODULE_SRC = MODULE_DIR / "gigamate_acpi.c"


def test_gigamate_acpi_exposes_npcf_and_wmbd_aml_attributes():
    src = MODULE_SRC.read_text(encoding="utf-8")
    for attr in ("npcf_atpp", "npcf_acbt", "npcf_amat", "npcf_dbac", "wmbd_aml"):
        assert attr in src, f"Missing sysfs attribute {attr} in gigamate_acpi.c"
    assert "\\\\_SB.NPCF.%s" in src
    assert "ACPI_SIG_DSDT" in src


def test_gigamate_acpi_builds_against_running_kernel(tmp_path):
    kbuild = Path(f"/lib/modules/{Path('/proc/sys/kernel/osrelease').read_text().strip()}/build")
    if not kbuild.is_dir() or not shutil.which("make"):
        pytest.skip("Kernel headers or make not installed")
    for item in ("Makefile", "gigamate_acpi.c"):
        shutil.copy2(MODULE_DIR / item, tmp_path / item)
    res = subprocess.run(
        ["make", "-C", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if res.returncode != 0 and shutil.which("clang"):
        res = subprocess.run(
            ["make", "-C", str(tmp_path), "CC=clang", "LLVM=1"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert res.returncode == 0, f"Kernel module build failed:\n{res.stderr}"
    assert (tmp_path / "gigamate_acpi.ko").is_file()
