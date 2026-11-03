import json
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gigamate.gpu import GpuState
from gigamate.power_limits import (
    GpuPowerCaps,
    ProfilePowerLimits,
    RawProfileAml,
    SystemPowerLimits,
    build_and_validate_limits,
    discover_power_limits,
    parse_wmbd_0xed_aml,
    read_gpu_power_caps,
)

# Real WMBD(0xED) AML byte patterns extracted from Gigabyte Aero X16 (EG61VH) DSDT
AERO_X16_WMBD_AML = (
    b"WMBD\x03"
    + b"\x0a\xed\x70\x99\x6a\x00\x61"
    # Mode 0 (Quiet): DBAC=0, ATPP=0xA0 (20W), ACBT=0 (0W), AMAT=0x78 (15W), ACST==1 -> ECPT(30,20), ECPT(32,65), ECPT(34,65), Else ECPT(30,15)...
    + b"\xa0\x4c\x0a\x93\x61\x00"
    + b"\x70\x00^^^.NPCFDBAC"
    + b"\x70\x0a\xa0^^^.NPCFATPP"
    + b"\x70\x00^^^.NPCFACBT"
    + b"\x70\x0a\x78^^^.NPCFAMAT"
    + b"\xa0\x2b\x93^^/\x03SBRGEC0_ACST\x01"
    + b"ECPT\x0a\x30\x0a\x14ECPT\x0a\x32\x0a\x41ECPT\x0a\x34\x0a\x41"
    + b"\xa1\x19ECPT\x0a\x30\x0a\x0fECPT\x0a\x32\x0a\x1eECPT\x0a\x34\x0a\x1e"
    # Mode 1 (Balanced): ATPP=0xC8 (25W), ACBT=0x50 (10W), AMAT=0x78 (15W), ACST==1 -> ECPT(30,25), ECPT(32,65), ECPT(34,80)
    + b"\xa0\x47\x0d\x93\x61\x01"
    + b"\x70\x00^^^.NPCFDBAC"
    + b"\x70\x0a\xc8^^^.NPCFATPP"
    + b"\x70\x0a\x50^^^.NPCFACBT"
    + b"\x70\x0a\x78^^^.NPCFAMAT"
    + b"\xa0\x2b\x93^^/\x03SBRGEC0_ACST\x01"
    + b"ECPT\x0a\x30\x0a\x19ECPT\x0a\x32\x0a\x41ECPT\x0a\x34\x0a\x50"
    + b"\xa1\x21ECPT\x0a\x30\x0a\x14ECPT\x0a\x32\x0a\x36ECPT\x0a\x34\x0a\x36"
    # Mode 2 (Performance): ATPP=0xF0 (30W), ACBT=0xA0 (20W), AMAT=0x78 (15W), ACST==1 -> ECPT(30,19) then ECPT(30,30), ECPT(32,80), ECPT(34,80)
    + b"\xa0\x49\x15\x93\x61\x0a\x02"
    + b"\x70\x00^^^.NPCFDBAC"
    + b"\x70\x0a\xf0^^^.NPCFATPP"
    + b"\x70\x0a\xa0^^^.NPCFACBT"
    + b"\x70\x0a\x78^^^.NPCFAMAT"
    + b"\xa0\x33\x93^^/\x03SBRGEC0_ACST\x01"
    + b"ECPT\x0a\x30\x0a\x13ECPT\x0a\x30\x0a\x1eECPT\x0a\x32\x0a\x50ECPT\x0a\x34\x0a\x50"
    + b"\xa1\x21ECPT\x0a\x30\x0a\x14ECPT\x0a\x32\x0a\x36ECPT\x0a\x34\x0a\x36"
    # Mode 3 (Gaming): ATPP=0xC8 (25W), ACBT=0xA0 (20W), AMAT=0x78 (15W), ACST==1 -> ECPT(30,25), ECPT(32,80), ECPT(34,80)
    + b"\xa0\x48\x0d\x93\x61\x0a\x03"
    + b"\x70\x00^^^.NPCFDBAC"
    + b"\x70\x0a\xc8^^^.NPCFATPP"
    + b"\x70\x0a\xa0^^^.NPCFACBT"
    + b"\x70\x0a\x78^^^.NPCFAMAT"
    + b"\xa0\x2b\x93^^/\x03SBRGEC0_ACST\x01"
    + b"ECPT\x0a\x30\x0a\x19ECPT\x0a\x32\x0a\x50ECPT\x0a\x34\x0a\x50"
    + b"\xa1\x21ECPT\x0a\x30\x0a\x14ECPT\x0a\x32\x0a\x36ECPT\x0a\x34\x0a\x36"
)


def test_parse_wmbd_0xed_aml_extracts_all_four_profiles():
    raw = parse_wmbd_0xed_aml(AERO_X16_WMBD_AML)
    assert set(raw.keys()) == {0, 1, 2, 3}

    assert raw[0].atpp_raw == 0xA0
    assert raw[0].acbt_raw == 0
    assert raw[0].amat_raw == 0x78
    assert raw[0].ecpt_30_w == 20
    assert raw[0].ecpt_32_w == 65
    assert raw[0].ecpt_34_w == 65

    assert raw[2].atpp_raw == 0xF0
    assert raw[2].acbt_raw == 0xA0
    assert raw[2].ecpt_30_w == 30
    assert raw[2].ecpt_32_w == 80
    assert raw[2].ecpt_34_w == 80


def test_parse_wmbd_0xed_aml_supports_alternate_local_registers_and_scopes():
    # Simulate a BIOS that uses Local0 (\x60) instead of Local1 (\x61) and ^^.NPCF (\x5e\x5e) instead of ^^^.NPCF
    alt_aml = AERO_X16_WMBD_AML.replace(b"\x93\x61", b"\x93\x60").replace(b"\x5e\x5e\x5e\x2eNPCF", b"\x5e\x5e\x2eNPCF")
    raw = parse_wmbd_0xed_aml(alt_aml)
    assert set(raw.keys()) == {0, 1, 2, 3}
    assert raw[0].atpp_raw == 0xA0
    assert raw[2].atpp_raw == 0xF0
    assert raw[2].ecpt_30_w == 30


def test_build_and_validate_limits_computes_exact_aero_x16_watts():
    raw = parse_wmbd_0xed_aml(AERO_X16_WMBD_AML)
    limits = build_and_validate_limits(raw, default_tgp_w=50, max_constraint_w=85)
    assert limits[0] == ProfilePowerLimits(
        profile_id=0, combined_w=70, cpu_shared_w=20, cpu_sustained_w=65, cpu_burst_w=65, gpu_fixed_w=50, gpu_boost_w=50
    )
    assert limits[1] == ProfilePowerLimits(
        profile_id=1, combined_w=85, cpu_shared_w=25, cpu_sustained_w=65, cpu_burst_w=80, gpu_fixed_w=60, gpu_boost_w=75
    )
    assert limits[2] == ProfilePowerLimits(
        profile_id=2, combined_w=100, cpu_shared_w=30, cpu_sustained_w=80, cpu_burst_w=80, gpu_fixed_w=70, gpu_boost_w=85
    )
    assert limits[3] == ProfilePowerLimits(
        profile_id=3, combined_w=95, cpu_shared_w=25, cpu_sustained_w=80, cpu_burst_w=80, gpu_fixed_w=70, gpu_boost_w=85
    )


def test_sanity_validator_rejects_out_of_bounds_and_non_monotonic_values():
    bad_raw = {
        0: RawProfileAml(0, atpp_raw=160, acbt_raw=240, amat_raw=120, dbac_raw=0, ecpt_30_w=20, ecpt_32_w=65, ecpt_34_w=65),
        1: RawProfileAml(1, atpp_raw=200, acbt_raw=80, amat_raw=120, dbac_raw=0, ecpt_30_w=25, ecpt_32_w=65, ecpt_34_w=80),
        2: RawProfileAml(2, atpp_raw=240, acbt_raw=160, amat_raw=120, dbac_raw=0, ecpt_30_w=30, ecpt_32_w=80, ecpt_34_w=80),
    }
    limits = build_and_validate_limits(bad_raw, default_tgp_w=50, max_constraint_w=85)
    for pid in (0, 1, 2):
        assert limits[pid].gpu_fixed_w is None
        assert limits[pid].gpu_boost_w is None
        assert limits[pid].combined_w is None
        assert limits[pid].cpu_sustained_w is not None


def test_sanity_validator_rejects_gpu_fixed_exceeding_vbios_max_constraint():
    raw = parse_wmbd_0xed_aml(AERO_X16_WMBD_AML)
    # If VBIOS max constraint is 60W, a formula producing 70W fixed GPU is impossible
    limits = build_and_validate_limits(raw, default_tgp_w=50, max_constraint_w=60)
    for pid in (0, 1, 2, 3):
        assert limits[pid].gpu_fixed_w is None
        assert limits[pid].gpu_boost_w is None
        assert limits[pid].combined_w is None


def test_read_gpu_power_caps_never_calls_nvml_when_suspended(tmp_path):
    cache_file = tmp_path / "gpu_power_caps.json"
    cache_file.write_text(json.dumps({"default_tgp_w": 50, "max_constraint_w": 85}))
    sleeping_gpu = GpuState(present=True, vendor="nvidia", status="suspended", power_state="D3cold")

    with patch("gigamate.power_limits._query_nvml_power_caps") as mock_nvml:
        caps = read_gpu_power_caps(gpu_state=sleeping_gpu, cache_file=cache_file)
        mock_nvml.assert_not_called()
        assert caps.default_tgp_w == 50
        assert caps.max_constraint_w == 85


def test_read_gpu_power_caps_uses_cache_even_when_awake_to_avoid_polling_nvml(tmp_path):
    cache_file = tmp_path / "gpu_power_caps.json"
    awake_gpu = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")

    with patch(
        "gigamate.power_limits._query_nvml_power_caps",
        return_value=GpuPowerCaps(default_tgp_w=50, max_constraint_w=85),
    ) as mock_nvml:
        # First call populates cache
        caps1 = read_gpu_power_caps(gpu_state=awake_gpu, cache_file=cache_file)
        assert caps1.default_tgp_w == 50
        assert mock_nvml.call_count == 1

        # Second call uses cached VBIOS constants without polling NVML again
        caps2 = read_gpu_power_caps(gpu_state=awake_gpu, cache_file=cache_file)
        assert caps2.default_tgp_w == 50
        assert caps2.max_constraint_w == 85
        assert mock_nvml.call_count == 1


def test_discover_power_limits_combines_sysfs_wmbd_and_cached_gpu(tmp_path):
    sysfs_dir = tmp_path / "gigamate_acpi"
    sysfs_dir.mkdir()
    (sysfs_dir / "wmbd_aml").write_bytes(AERO_X16_WMBD_AML)
    (sysfs_dir / "profile").write_text("2\n")

    cache_file = tmp_path / "gpu_power_caps.json"
    cache_file.write_text(json.dumps({"default_tgp_w": 50, "max_constraint_w": 85}))

    psu_dir = tmp_path / "power_supply"
    ac_dir = psu_dir / "ADP1"
    ac_dir.mkdir(parents=True)
    (ac_dir / "type").write_text("Mains\n")
    (ac_dir / "online").write_text("1\n")

    sleeping_gpu = GpuState(
        present=True,
        vendor="nvidia",
        status="suspended",
        power_state="D3cold",
        dynamic_boost_supported=True,
        dynamic_boost_active=True,
    )

    sys_limits = discover_power_limits(
        active_profile_id=2,
        gpu_state=sleeping_gpu,
        sysfs_dir=sysfs_dir,
        cache_file=cache_file,
        power_supply_dir=psu_dir,
    )
    assert not sys_limits.on_battery
    assert sys_limits.dynamic_boost_supported is True
    assert sys_limits.dynamic_boost_active is True
    assert sys_limits.profiles[2].combined_w == 100
    assert sys_limits.profiles[2].gpu_fixed_w == 70
    assert sys_limits.profiles[2].gpu_boost_w == 85
    assert sys_limits.profiles[2].cpu_sustained_w == 80


def test_discover_power_limits_uses_intel_rapl_for_active_profile_when_ecpt_absent(tmp_path):
    intel_aml = (
        b"WMBD\x03\x0a\xed"
        + b"\x93\x61\x00\x70\x0a\xa0^^^.NPCFATPP\x70\x00^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
        + b"\x93\x61\x01\x70\x0a\xc8^^^.NPCFATPP\x70\x0a\x50^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
        + b"\x93\x61\x0a\x02\x70\x0a\xf0^^^.NPCFATPP\x70\x0a\xa0^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
    )
    sysfs_dir = tmp_path / "gigamate_acpi"
    sysfs_dir.mkdir()
    (sysfs_dir / "wmbd_aml").write_bytes(intel_aml)

    cache_file = tmp_path / "gpu_power_caps.json"
    cache_file.write_text(json.dumps({"default_tgp_w": 50, "max_constraint_w": 85}))

    rapl_dir = tmp_path / "intel-rapl:0"
    rapl_dir.mkdir()
    (rapl_dir / "constraint_0_power_limit_uw").write_text("55000000\n")
    (rapl_dir / "constraint_1_power_limit_uw").write_text("115000000\n")

    sleeping_gpu = GpuState(present=True, vendor="nvidia", status="suspended", power_state="D3cold")
    sys_limits = discover_power_limits(
        active_profile_id=1,
        gpu_state=sleeping_gpu,
        sysfs_dir=sysfs_dir,
        cache_file=cache_file,
        rapl_dir=rapl_dir,
    )
    assert sys_limits.profiles[1].combined_w == 85
    assert sys_limits.profiles[1].cpu_sustained_w == 55
    assert sys_limits.profiles[1].cpu_burst_w == 115


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_dashboard_shows_comparison_matrix_by_default_without_confusing_single_boxes(qapp):
    from PyQt6.QtWidgets import QLabel
    from gigamate.ui.pages.dashboard_page import DashboardPage

    fake_limits = SystemPowerLimits(
        profiles={
            0: ProfilePowerLimits(0, combined_w=70, cpu_shared_w=20, cpu_sustained_w=65, cpu_burst_w=65, gpu_fixed_w=50, gpu_boost_w=50),
            1: ProfilePowerLimits(1, combined_w=85, cpu_shared_w=25, cpu_sustained_w=65, cpu_burst_w=80, gpu_fixed_w=60, gpu_boost_w=75),
            2: ProfilePowerLimits(2, combined_w=100, cpu_shared_w=30, cpu_sustained_w=80, cpu_burst_w=80, gpu_fixed_w=70, gpu_boost_w=85),
            3: ProfilePowerLimits(3, combined_w=95, cpu_shared_w=25, cpu_sustained_w=80, cpu_burst_w=80, gpu_fixed_w=70, gpu_boost_w=85),
        },
        dynamic_boost_supported=True,
        dynamic_boost_active=True,
        on_battery=False,
        source="acpi+nvml",
    )

    with patch("gigamate.ui.pages.dashboard_page.discover_power_limits", return_value=fake_limits):
        page = DashboardPage()
        page.timer.stop()
        try:
            page._update_power_limits_ui(active_prof_id=2, usable=True)
            assert not page.power_limits_container.isHidden()
            # Comparison table is visible immediately by default
            assert not page.compare_limits_frame.isHidden()
            # Ensure all 4 profiles and key wattages are rendered in the comparison grid
            texts = [lbl.text() for lbl in page.compare_limits_frame.findChildren(QLabel)]
            assert any("Quiet" in t for t in texts)
            assert any("Balanced" in t for t in texts)
            assert any("Performance" in t for t in texts)
            assert any("Gaming" in t for t in texts)
            assert "100 W" in texts
            assert "Up to 85 W" in texts
        finally:
            page.close()
            page.deleteLater()


def test_dashboard_clean_absence_hides_missing_or_empty_power_limits(qapp):
    from PyQt6.QtWidgets import QLabel
    from gigamate.ui.pages.dashboard_page import DashboardPage

    empty_limits = SystemPowerLimits(profiles={}, source="none")
    with patch("gigamate.ui.pages.dashboard_page.discover_power_limits", return_value=empty_limits):
        page = DashboardPage()
        page.timer.stop()
        try:
            page._update_power_limits_ui(active_prof_id=1, usable=True)
            assert page.power_limits_container.isHidden()
        finally:
            page.close()
            page.deleteLater()

    cpu_only_limits = SystemPowerLimits(
        profiles={
            0: ProfilePowerLimits(0, combined_w=None, cpu_shared_w=20, cpu_sustained_w=65, cpu_burst_w=65, gpu_fixed_w=None, gpu_boost_w=None),
            2: ProfilePowerLimits(2, combined_w=None, cpu_shared_w=30, cpu_sustained_w=80, cpu_burst_w=80, gpu_fixed_w=None, gpu_boost_w=None),
        },
        source="acpi-only",
    )
    with patch("gigamate.ui.pages.dashboard_page.discover_power_limits", return_value=cpu_only_limits):
        page = DashboardPage()
        page.timer.stop()
        try:
            page._update_power_limits_ui(active_prof_id=2, usable=True)
            assert not page.power_limits_container.isHidden()
            assert not page.compare_limits_frame.isHidden()
            texts = [lbl.text() for lbl in page.compare_limits_frame.findChildren(QLabel)]
            assert any("CPU" in t for t in texts)
            assert not any("GPU Fixed" in t for t in texts)
            assert not any("Combined" in t for t in texts)
        finally:
            page.close()
            page.deleteLater()
