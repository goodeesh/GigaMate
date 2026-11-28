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
            # Both the comparison matrix and active power envelope bar are always visible
            assert not page.power_limits_container.isHidden()
            assert not page.power_envelope_bar.isHidden()
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


def test_discover_acpi_profiles_2_3_4_modes_and_missing_marker():
    """Verify discover_acpi_profiles() handles 2/3/4-mode AML tables and missing 0x0AED."""
    from gigamate.power_limits import discover_acpi_profiles

    def _make_aml(modes: list[int]) -> bytes:
        # 0x0AED followed by LEqual(Local0, mode) opcodes matching _MODE_MARKER_RES
        opcodes = {
            0: b"\x93\x60\x00",
            1: b"\x93\x60\x01",
            2: b"\x93\x60\x0a\x02",
            3: b"\x93\x60\x0a\x03",
        }
        entries = b"".join(opcodes[m] for m in modes)
        return b"\x00\x00\x0a\xed\x00" + entries + b"\x00\x00"

    p2 = discover_acpi_profiles(aml_bytes=_make_aml([0, 1]))
    assert set(p2.keys()) == {0, 1}

    p3 = discover_acpi_profiles(aml_bytes=_make_aml([0, 1, 2]))
    assert set(p3.keys()) == {0, 1, 2}
    assert p3[0]["name"] == "Eco"
    assert p3[1]["name"] == "Balanced"
    assert p3[2]["name"] == "Boost"

    p4 = discover_acpi_profiles(aml_bytes=_make_aml([0, 1, 2, 3]))
    assert set(p4.keys()) == {0, 1, 2, 3}
    assert p4[0]["name"] == "Quiet"
    assert p4[1]["name"] == "Balanced"
    assert p4[2]["name"] == "Performance"
    assert p4[3]["name"] == "Gaming"

    assert discover_acpi_profiles(aml_bytes=b"\x00\x01\x02\x03") == {}


def _mk_power_supply(tmp_path, entries):
    ps = tmp_path / "power_supply"
    ps.mkdir(parents=True, exist_ok=True)
    for name, files in entries.items():
        d = ps / name
        d.mkdir(parents=True)
        for fname, val in files.items():
            (d / fname).write_text(val, encoding="utf-8")
    return ps


def test_usbc_detect_power_source_variants(tmp_path):
    from gigamate.usbc_power import detect_power_source

    acpi_dir = tmp_path / "gigamate_acpi"
    acpi_dir.mkdir()
    (acpi_dir / "usbc_adapter").write_text("1\n", encoding="utf-8")

    # 1. AERO X16 ucsi_acpi behavior: voltage_* == 0, current_now == 5 A -> 100 W PD
    ps_100 = _mk_power_supply(
        tmp_path / "c100",
        {
            "ACAD": {"type": "Mains\n", "online": "1\n"},
            "ucsi-source-psy-USBC000:001": {
                "type": "USB\n",
                "online": "1\n",
                "usb_type": "C [PD] PD_PPS\n",
                "voltage_max": "0\n",
                "voltage_now": "0\n",
                "current_max": "0\n",
                "current_now": "5000000\n",
            },
            "BAT1": {
                "type": "Battery\n",
                "status": "Discharging\n",
                "capacity": "82\n",
                "voltage_now": "16000000\n",
                "current_now": "250000\n",
            },
        },
    )
    ps_100.parent.mkdir(exist_ok=True)
    snap = detect_power_source(power_supply_dir=ps_100, acpi_sysfs_dir=acpi_dir)
    assert snap.kind == "usbc_pd"
    assert snap.detected_wattage_w == 100
    assert snap.wattage_source == "pd_current_inferred"
    assert snap.negotiated_current_a == 5.0
    assert snap.firmware_usbc_flag is True
    assert snap.bat_power_w == -4.0

    # 2. Exact V * I reporting (140 W EPR)
    ps_140 = _mk_power_supply(
        tmp_path / "c140",
        {
            "ACAD": {"type": "Mains\n", "online": "1\n"},
            "ucsi-source-psy-USBC000:001": {
                "type": "USB\n",
                "online": "1\n",
                "usb_type": "C [PD]\n",
                "voltage_max": "28000000\n",
                "current_max": "5000000\n",
            },
        },
    )
    snap140 = detect_power_source(power_supply_dir=ps_140, acpi_sysfs_dir=acpi_dir)
    assert snap140.kind == "usbc_pd"
    assert snap140.detected_wattage_w == 140
    assert snap140.wattage_source == "exact"

    # 3. 3 A PD -> 60 W (< 90 W)
    ps_60 = _mk_power_supply(
        tmp_path / "c60",
        {
            "ACAD": {"type": "Mains\n", "online": "1\n"},
            "ucsi-source-psy-USBC000:001": {
                "type": "USB\n",
                "online": "1\n",
                "usb_type": "C [PD]\n",
                "current_now": "3000000\n",
            },
        },
    )
    snap60 = detect_power_source(power_supply_dir=ps_60, acpi_sysfs_dir=acpi_dir)
    assert snap60.kind == "usbc_pd"
    assert snap60.detected_wattage_w == 60

    # 4. Barrel AC
    (acpi_dir / "usbc_adapter").write_text("0\n", encoding="utf-8")
    ps_ac = _mk_power_supply(
        tmp_path / "cac",
        {
            "ACAD": {"type": "Mains\n", "online": "1\n"},
            "ucsi-source-psy-USBC000:001": {"type": "USB\n", "online": "0\n"},
        },
    )
    snap_ac = detect_power_source(power_supply_dir=ps_ac, acpi_sysfs_dir=acpi_dir)
    assert snap_ac.kind == "barrel_ac"

    # 5. Battery
    ps_bat = _mk_power_supply(
        tmp_path / "cbat",
        {
            "ACAD": {"type": "Mains\n", "online": "0\n"},
            "BAT1": {"type": "Battery\n", "status": "Discharging\n", "capacity": "55\n"},
        },
    )
    snap_bat = detect_power_source(power_supply_dir=ps_bat, acpi_sysfs_dir=acpi_dir)
    assert snap_bat.kind == "battery"


def test_usbc_compute_budget_envelope_and_split():
    from gigamate.usbc_power import clamp_split, compute_budget_envelope

    env100 = compute_budget_envelope(
        100, 50, 85, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True
    )
    assert env100.supported is True
    assert (env100.buffer_w, env100.silicon_budget_w) == (25, 75)
    assert (env100.gpu_min_w, env100.gpu_max_w, env100.default_gpu_w) == (50, 60, 55)
    assert clamp_split(99, env100) == (60, 15)
    assert clamp_split(55, env100) == (55, 20)

    env140 = compute_budget_envelope(
        140, 80, 115, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True
    )
    assert env140.supported is True
    assert (env140.buffer_w, env140.silicon_budget_w) == (35, 105)
    assert (env140.gpu_min_w, env140.gpu_max_w) == (80, 90)

    small = compute_budget_envelope(
        65, 50, 85, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True
    )
    assert small.supported is False and small.reason == "charger_too_small"

    big_gpu = compute_budget_envelope(
        100, 115, 150, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True
    )
    assert big_gpu.supported is False and big_gpu.reason == "gpu_tgp_exceeds_budget"

    no_ctgp = compute_budget_envelope(
        100, 50, 85, has_ctgp_ctrl=False, has_cpu_ppt_ctrl=False, has_dyn_boost_ctrl=False
    )
    assert no_ctgp.supported is True
    assert (no_ctgp.gpu_min_w, no_ctgp.gpu_max_w) == (50, 50)
    assert clamp_split(60, no_ctgp) == (50, 25)

    # Custom configurable safety buffer (15%..25%)
    env15 = compute_budget_envelope(
        100, 50, 85, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True, buffer_pct=15
    )
    assert env15.supported is True
    assert (env15.buffer_pct, env15.buffer_w, env15.silicon_budget_w) == (15, 15, 85)
    assert (env15.gpu_min_w, env15.gpu_max_w) == (50, 60)
    assert clamp_split(60, env15) == (60, 25)

    env20 = compute_budget_envelope(
        100, 50, 85, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True, buffer_pct=20
    )
    assert (env20.buffer_pct, env20.buffer_w, env20.silicon_budget_w) == (20, 20, 80)
    assert clamp_split(55, env20) == (55, 25)

    big_at_15 = compute_budget_envelope(
        100, 115, 150, has_ctgp_ctrl=True, has_cpu_ppt_ctrl=True, has_dyn_boost_ctrl=True, buffer_pct=15
    )
    assert big_at_15.supported is False and big_at_15.reason == "gpu_tgp_exceeds_budget"


def test_usbc_aux_power_state_sleep_safety():
    from gigamate.usbc_power import AUX_P0, set_nvidia_aux_power_state

    with patch("gigamate.usbc_power.os.open") as mock_open:
        for state in (
            GpuState(present=True, vendor="nvidia", status="suspended", power_state="D3cold"),
            GpuState(present=True, vendor="nvidia", status="gated_off", gated_off=True),
            GpuState(present=True, vendor="amd", status="active", power_state="D0"),
            GpuState(present=False),
        ):
            ok, reason = set_nvidia_aux_power_state(AUX_P0, gpu_state=state)
            assert ok is False
            assert reason == "gpu_not_active"
        mock_open.assert_not_called()


def test_usbc_controller_settle_enforce_trim_recover_and_restore(tmp_path):
    from gigamate.acpi import FanProfile
    from gigamate.power_limits import GpuPowerCaps
    from gigamate.usbc_power import AUX_P0, AUX_P1, AUX_P2, PowerSourceSnapshot, UsbcPowerBudgetController

    clock = [0.0]
    calls = []

    class FakeAcpi:
        available = True

        def __init__(self, *, has_ppt=True, has_ctgp=True, has_db=True):
            self._ppt = has_ppt
            self._ctgp = has_ctgp
            self._db = has_db
            self.profile = FanProfile.PERFORMANCE
            self.live_db = False
            self.live_ctgp = 0

        def has_cpu_ppt_control(self):
            return self._ppt

        def has_gpu_ctgp_control(self):
            return self._ctgp

        def has_gpu_dynamic_boost_control(self):
            return self._db

        def get_profile(self):
            return self.profile

        def set_profile(self, prof):
            self.profile = prof
            calls.append(("profile", prof.value))
            return True

        def set_gpu_dynamic_boost(self, en):
            self.live_db = en
            calls.append(("dyn_boost", en))
            return True

        def set_gpu_ctgp_offset(self, off):
            self.live_ctgp = off
            calls.append(("ctgp", off))
            return True

        def set_cpu_ppt_limits(self, s, l, f):
            calls.append(("cpu_ppt", (s, l, f)))
            return True

        def read_npcf_dynamic_boost(self):
            return self.live_db

        def read_npcf_ctgp_offset_w(self):
            return self.live_ctgp

    acpi = FakeAcpi()
    gpu_awake = GpuState(present=True, vendor="nvidia", status="active", power_state="D0")
    src = PowerSourceSnapshot(
        kind="usbc_pd", ac_online=True, usbc_online=True, is_pd=True,
        detected_wattage_w=100, wattage_source="pd_current_inferred",
        negotiated_current_a=5.0, negotiated_voltage_v=None, firmware_usbc_flag=True,
        bat_present=True, bat_status="Charging", bat_capacity=80, bat_power_w=3.0,
    )
    caps = GpuPowerCaps(default_tgp_w=50, max_constraint_w=85)
    cur_src = [src]
    cur_gpu = [gpu_awake]

    def fake_aux(state, gpu_state=None):
        calls.append(("aux", state))
        return True, ""

    ctrl = UsbcPowerBudgetController(
        acpi_getter=lambda: acpi,
        power_detector=lambda: cur_src[0],
        gpu_state_getter=lambda: cur_gpu[0],
        gpu_caps_getter=lambda **kw: caps,
        aux_setter=fake_aux,
        clock=lambda: clock[0],
        state_path=tmp_path / "usbc_state.json",
    )
    cfg = {"usbc_power_budget_enabled": True, "usbc_gpu_budget_w": 55, "acpi_profile": 2}

    # 1. t=0.5s: settling (< 1.5s), zero hardware mutation
    clock[0] = 0.5
    st = ctrl.tick(cfg, active_profile=2)
    assert st["mode"] == "settling"
    assert calls == []

    # 2. t=2.0s: constrains ACPI before unlocking NVRM P0
    clock[0] = 2.0
    st = ctrl.tick(cfg, active_profile=2)
    assert st["mode"] == "active"
    assert (st["applied_gpu_w"], st["applied_cpu_w"], st["aux_state"]) == (55, 20, AUX_P0)
    assert calls == [
        ("dyn_boost", False),
        ("ctgp", 5),
        ("cpu_ppt", (20, 20, 20)),
        ("aux", AUX_P0),
    ]
    calls.clear()

    # 3. t=4.0s: single post-attach re-assert, then steady no-op at t=5.0s
    clock[0] = 4.0
    ctrl.tick(cfg, active_profile=2)
    assert ("aux", AUX_P0) in calls
    calls.clear()
    clock[0] = 5.0
    ctrl.tick(cfg, active_profile=2)
    assert calls == []

    # 3b. Changing safety buffer 25% -> 20% expands silicon budget 75 -> 80 W & re-applies CPU PPT 20 -> 25 W
    st_buf = ctrl.tick({**cfg, "usbc_buffer_pct": 20}, active_profile=2)
    assert (st_buf["buffer_pct"], st_buf["buffer_w"], st_buf["applied_gpu_w"], st_buf["applied_cpu_w"]) == (20, 20, 55, 25)
    assert ("cpu_ppt", (25, 25, 25)) in calls
    calls.clear()
    st = ctrl.tick(cfg, active_profile=2)
    assert (st["applied_gpu_w"], st["applied_cpu_w"]) == (55, 20)
    calls.clear()

    # 4. Sustained -5 W drain steps down: CPU -> GPU cTGP -> P1 -> P2
    cur_src[0] = PowerSourceSnapshot(
        kind="usbc_pd", ac_online=True, usbc_online=True, is_pd=True,
        detected_wattage_w=100, wattage_source="pd_current_inferred",
        negotiated_current_a=5.0, negotiated_voltage_v=None, firmware_usbc_flag=True,
        bat_present=True, bat_status="Discharging", bat_capacity=80, bat_power_w=-5.0,
    )
    clock[0] = 6.0
    ctrl.tick(cfg, active_profile=2)
    assert st["trim_level"] == 0

    for t, expected in (
        (11.0, (55, 15, AUX_P0)),
        (16.0, (50, 15, AUX_P0)),
        (21.0, (30, 15, AUX_P1)),
        (26.0, (20, 15, AUX_P2)),
    ):
        clock[0] = t
        st = ctrl.tick(cfg, active_profile=2)
        assert st["mode"] == "auto_trimmed"
        assert (st["applied_gpu_w"], st["applied_cpu_w"], st["aux_state"]) == expected

    # 5. Recovery (+4 W charging for 15s) steps back one trim level
    cur_src[0] = src
    clock[0] = 27.0
    ctrl.tick(cfg, active_profile=2)
    clock[0] = 43.0
    st = ctrl.tick(cfg, active_profile=2)
    assert st["trim_level"] == 3 and st["aux_state"] == AUX_P1

    # 6. Transition to barrel AC restores stock profile + P0
    calls.clear()
    cur_src[0] = PowerSourceSnapshot(
        kind="barrel_ac", ac_online=True, usbc_online=False, is_pd=False,
        detected_wattage_w=None, wattage_source="unknown",
        negotiated_current_a=None, negotiated_voltage_v=None, firmware_usbc_flag=False,
        bat_present=True, bat_status="Charging", bat_capacity=82, bat_power_w=15.0,
    )
    clock[0] = 45.0
    st = ctrl.tick(cfg, active_profile=2)
    assert st["mode"] == "standby_barrel"
    assert ("profile", 2) in calls and ("aux", AUX_P0) in calls

    # 7. Shutdown / toggle off on USB-C reverts P1 + stock profile; no-cTGP fallback switches to Quiet
    calls.clear()
    fallback_acpi = FakeAcpi(has_ppt=False, has_ctgp=False, has_db=False)
    cur_src[0] = src
    ctrl2 = UsbcPowerBudgetController(
        acpi_getter=lambda: fallback_acpi,
        power_detector=lambda: cur_src[0],
        gpu_state_getter=lambda: cur_gpu[0],
        gpu_caps_getter=lambda **kw: caps,
        aux_setter=fake_aux,
        clock=lambda: clock[0],
        publish_state=False,
    )
    clock[0] = 50.0
    ctrl2.tick(cfg, active_profile=2)
    clock[0] = 52.0
    st2 = ctrl2.tick(cfg, active_profile=2)
    assert st2["mode"] == "active"
    assert ("profile", 0) in calls and ("aux", AUX_P0) in calls
    calls.clear()
    st2_off = ctrl2.tick({**cfg, "usbc_power_budget_enabled": False}, active_profile=2)
    assert st2_off["mode"] == "off"
    assert ("profile", 2) in calls and ("aux", AUX_P1) in calls
