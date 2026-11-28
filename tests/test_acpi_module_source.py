import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_DIR = REPO_ROOT / "src" / "gigamate_acpi"
MODULE_SRC = MODULE_DIR / "gigamate_acpi.c"
DKMS_CONF = MODULE_DIR / "dkms.conf"


def test_gigamate_acpi_exposes_npcf_and_wmbd_aml_attributes():
    src = MODULE_SRC.read_text(encoding="utf-8")
    dkms = DKMS_CONF.read_text(encoding="utf-8")
    for attr in ("npcf_atpp", "npcf_acbt", "npcf_amat", "npcf_dbac", "wmbd_aml"):
        assert attr in src, f"Missing sysfs attribute {attr} in gigamate_acpi.c"
    assert "\\\\_SB.NPCF.%s" in src
    assert "ACPI_SIG_DSDT" in src
    assert "gigamate_recover_pg00_on_timeout" in src
    assert "AE_AML_LOOP_TIMEOUT" in src
    assert "\\\\_SB.ALIB" in src
    assert "<linux/dmi.h>" in src
    assert "gigamate_is_aero_x16_ec_dsmd" in src
    assert "\\\\_SB.PCI0.SBRG.EC0.DSMD" in src
    assert "AERO X16" in src
    assert '#define DRIVER_VERSION "5.0.0"' in src
    assert 'PACKAGE_VERSION="5.0.0"' in dkms


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


def test_gigamate_acpi_exposes_usbc_power_budget_attributes_and_gating():
    src = MODULE_SRC.read_text(encoding="utf-8")
    for attr in (
        "usbc_adapter",
        "cpu_stapm_w",
        "cpu_slow_ppt_w",
        "cpu_fast_ppt_w",
        "gpu_ctgp_offset_w",
        "gpu_dynamic_boost",
    ):
        assert attr in src, f"Missing sysfs attribute {attr} in gigamate_acpi.c"
    for sym in (
        "acpi_wmbd_exec_locked",
        "gigamate_wmbd_body_len",
        "gigamate_wmbd_has_ecpt_mw_opcode",
        "gigamate_wmbd_has_ctgp_offset",
        "gigamate_wmbd_has_dyn_boost",
    ):
        assert sym in src, f"Missing helper {sym} in gigamate_acpi.c"
    assert "gigamate_wmbd_body_len" in src
    assert "'W', 'M', 'B', 'C'" in src
