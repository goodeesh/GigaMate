import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from gigamate.battery import BatteryManager, BatteryInfo, is_effectively_full, start_topup, cancel_topup, evaluate_calibration_state


def test_battery_detection_and_status(tmp_path: Path):
    psy = tmp_path / "power_supply"
    bat = psy / "BAT1"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "capacity").write_text("85\n")
    (bat / "status").write_text("Discharging\n")
    (bat / "cycle_count").write_text("42\n")
    (bat / "charge_full").write_text("4000000\n")
    (bat / "charge_full_design").write_text("5000000\n")

    acad = psy / "ACAD"
    acad.mkdir(parents=True)
    (acad / "type").write_text("Mains\n")
    (acad / "online").write_text("0\n")

    acpi_dir = tmp_path / "gigamate_acpi"
    acpi_dir.mkdir(parents=True)

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=acpi_dir)
    assert mgr.is_available is True
    assert mgr.is_ac_online() is False

    info = mgr.get_battery_info()
    assert info.present is True
    assert info.name == "BAT1"
    assert info.capacity == 85
    assert info.status == "Discharging"
    assert info.cycle_count == 42
    assert info.health_percent == 80.0
    assert info.ac_online is False


def test_charge_limit_via_gigamate_acpi(tmp_path: Path):
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")

    acpi_dir = tmp_path / "gigamate_acpi"
    acpi_dir.mkdir(parents=True)
    charge_limit_file = acpi_dir / "charge_limit"
    charge_limit_file.write_text("100\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=acpi_dir)
    assert mgr.is_charge_limit_supported() is True
    assert mgr.get_charge_limit() == 100

    # Set to 80%
    assert mgr.set_charge_limit(80) is True
    assert charge_limit_file.read_text().strip() == "80"
    assert mgr.get_charge_limit() == 80

    info = mgr.get_battery_info()
    assert info.charge_limit == 80
    assert info.backend == "gigamate_acpi"


def test_charge_limit_via_kernel_sysfs(tmp_path: Path):
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    std_limit_file = bat / "charge_control_end_threshold"
    std_limit_file.write_text("80\n")

    acpi_dir = tmp_path / "gigamate_acpi"
    # acpi_dir has no charge_limit file

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=acpi_dir)
    assert mgr.is_charge_limit_supported() is True
    assert mgr.get_charge_limit() == 80

    assert mgr.set_charge_limit(60) is True
    assert std_limit_file.read_text().strip() == "60"

    info = mgr.get_battery_info()
    assert info.backend == "sysfs"


def test_invalid_charge_limits(tmp_path: Path):
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "charge_control_end_threshold").write_text("100\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    with pytest.raises(ValueError):
        mgr.set_charge_limit(20)

    with pytest.raises(ValueError):
        mgr.set_charge_limit(105)


def test_battery_missing(tmp_path: Path):
    mgr = BatteryManager(power_supply_dir=tmp_path / "empty", acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_available is False
    info = mgr.get_battery_info()
    assert info.present is False


def test_battery_without_present_attribute(tmp_path: Path):
    """Some batteries omit the `present` file entirely; treat them as present."""
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "capacity").write_text("72\n")
    (bat / "status").write_text("Charging\n")
    # Note: no `present` file

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_available is True
    info = mgr.get_battery_info()
    assert info.present is True
    assert info.capacity == 72


def test_battery_marked_absent(tmp_path: Path):
    """An explicit `present=0` must still report the battery as unavailable."""
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("0\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_available is False
    assert mgr.get_battery_info().present is False


def test_multi_battery_prefers_present_pack(tmp_path: Path):
    """When BAT0 reports absent, the present BAT1 must be selected."""
    psy = tmp_path / "power_supply"
    for name, present in (("BAT0", "0"), ("BAT1", "1")):
        bat = psy / name
        bat.mkdir(parents=True)
        (bat / "type").write_text("Battery\n")
        (bat / "present").write_text(present + "\n")
        (bat / "capacity").write_text("77\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_available is True
    assert mgr.get_battery_info().name == "BAT1"


def test_ac_online_ignores_unrelated_supply(tmp_path: Path):
    psy = tmp_path / "power_supply"
    # A battery with an `online` file must not be mistaken for mains.
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "online").write_text("1\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_ac_online() is False


def test_ac_online_accepts_usb_pd(tmp_path: Path):
    psy = tmp_path / "power_supply"
    usb = psy / "UC0"
    usb.mkdir(parents=True)
    (usb / "type").write_text("USB_PD\n")
    (usb / "online").write_text("1\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.is_ac_online() is True


def test_charge_limit_unsupported_when_unreadable(tmp_path: Path):
    acpi = tmp_path / "acpi"
    acpi.mkdir()
    (acpi / "charge_limit").mkdir()  # exists but unreadable
    mgr = BatteryManager(power_supply_dir=tmp_path / "psy", acpi_sysfs_dir=acpi)
    assert mgr.is_charge_limit_supported() is False


def test_set_charge_limit_rejects_zero_and_out_of_range(tmp_path: Path):
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "charge_control_end_threshold").write_text("100\n")
    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    for bad in (0, 39, 101, None):
        with pytest.raises(ValueError):
            mgr.set_charge_limit(bad)


def test_charge_limit_requires_write_access(tmp_path: Path, monkeypatch):
    """Standard sysfs nodes that are root-only must report unsupported."""
    import gigamate.battery as battery_module
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "charge_control_end_threshold").write_text("80\n")
    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")

    monkeypatch.setattr(battery_module.os, "access", lambda p, m: True)
    assert mgr.is_charge_limit_supported() is True
    monkeypatch.setattr(battery_module.os, "access", lambda p, m: False)
    assert mgr.is_charge_limit_supported() is False


def test_battery_health_unit_matching(tmp_path: Path):
    """Health calculation must strictly match charge (µAh) or energy (µWh) units."""
    psy = tmp_path / "power_supply"
    bat = psy / "BAT0"
    bat.mkdir(parents=True)
    (bat / "type").write_text("Battery\n")
    (bat / "present").write_text("1\n")
    (bat / "charge_full").write_text("4000000\n")
    (bat / "charge_full_design").write_text("5000000\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    info = mgr.get_battery_info()
    assert info.health_percent == 80.0

    # Switch to energy units
    (bat / "charge_full").unlink()
    (bat / "charge_full_design").unlink()
    (bat / "energy_full").write_text("45000000\n")
    (bat / "energy_full_design").write_text("50000000\n")

    info2 = mgr.get_battery_info()
    assert info2.health_percent == 90.0

    # Mixed units should NOT be divided into each other
    (bat / "energy_full_design").unlink()
    (bat / "charge_full_design").write_text("5000000\n")
    info3 = mgr.get_battery_info()
    assert info3.health_percent is None


def test_dual_battery_charge_limit(tmp_path: Path):
    """Charge limits are applied to all present batteries in a dual-battery system."""
    psy = tmp_path / "power_supply"
    bat0 = psy / "BAT0"
    bat0.mkdir(parents=True)
    (bat0 / "type").write_text("Battery\n")
    (bat0 / "present").write_text("1\n")
    (bat0 / "charge_control_end_threshold").write_text("100\n")

    bat1 = psy / "BAT1"
    bat1.mkdir(parents=True)
    (bat1 / "type").write_text("Battery\n")
    (bat1 / "present").write_text("1\n")
    (bat1 / "charge_control_end_threshold").write_text("100\n")

    mgr = BatteryManager(power_supply_dir=psy, acpi_sysfs_dir=tmp_path / "empty")
    assert mgr.set_charge_limit(80) is True
    assert (bat0 / "charge_control_end_threshold").read_text().strip() == "80"
    assert (bat1 / "charge_control_end_threshold").read_text().strip() == "80"


def test_is_effectively_full():
    """is_effectively_full returns True for fully charged batteries."""
    # Full status with cap >= 99
    info = BatteryInfo(present=True, status="Full", capacity=100)
    assert is_effectively_full(info) is True

    # Not charging with capacity >= 99
    info = BatteryInfo(present=True, status="Not charging", capacity=99)
    assert is_effectively_full(info) is True

    # Unknown status with capacity >= 99
    info = BatteryInfo(present=True, status="Unknown", capacity=100)
    assert is_effectively_full(info) is True

    # Not present
    info = BatteryInfo(present=False, status="Full", capacity=100)
    assert is_effectively_full(info) is False

    # Charging status - should not be effectively full
    info = BatteryInfo(present=True, status="Charging", capacity=100)
    assert is_effectively_full(info) is False

    # Capacity just below threshold
    info = BatteryInfo(present=True, status="Full", capacity=98)
    assert is_effectively_full(info) is False

    # Not charging with capacity < 99
    info = BatteryInfo(present=True, status="Not charging", capacity=98)
    assert is_effectively_full(info) is False


def test_start_topup_no_manager(tmp_path: Path):
    """start_topup uses get_battery_manager when no mgr provided."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = True
    mock_instance.set_charge_limit.return_value = True

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config") as mock_update:
            result = start_topup()
            assert result is True
            mock_instance.set_charge_limit.assert_called_once_with(100)
            mock_update.assert_called_once()


def test_start_topup_not_supported(tmp_path: Path):
    """start_topup returns False when charge limit not supported."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = False

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config"):
            result = start_topup()
            assert result is False
            mock_instance.set_charge_limit.assert_not_called()


def test_start_topup_set_fails(tmp_path: Path):
    """start_topup returns False when set_charge_limit fails."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = True
    mock_instance.set_charge_limit.return_value = False

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config"):
            result = start_topup()
            assert result is False


def test_cancel_topup_with_restore(tmp_path: Path):
    """cancel_topup restores charge limit when restore_limit=True."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = True

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config") as mock_update:
            with patch("gigamate.config.load") as mock_load:
                mock_load.return_value = {"charge_limit_enabled": True, "charge_limit": 80}

                result = cancel_topup(restore_limit=True)
                assert result is True
                mock_instance.set_charge_limit.assert_called_once_with(80)


def test_cancel_topup_without_restore(tmp_path: Path):
    """cancel_topup does not restore charge limit when restore_limit=False."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = True

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config") as mock_update:
            with patch("gigamate.config.load") as mock_load:
                mock_load.return_value = {"charge_limit_enabled": True, "charge_limit": 80}

                result = cancel_topup(restore_limit=False)
                assert result is True
                mock_instance.set_charge_limit.assert_not_called()


def test_cancel_topup_no_limit_enabled(tmp_path: Path):
    """cancel_topup skips restore when charge_limit_enabled is False."""
    mock_instance = MagicMock()
    mock_instance.is_charge_limit_supported.return_value = True

    with patch("gigamate.battery.get_battery_manager", return_value=mock_instance):
        with patch("gigamate.config.update_config") as mock_update:
            with patch("gigamate.config.load") as mock_load:
                mock_load.return_value = {"charge_limit_enabled": False, "charge_limit": 80}

                result = cancel_topup(restore_limit=True)
                assert result is True
                mock_instance.set_charge_limit.assert_not_called()


def test_evaluate_calibration_state_no_battery(tmp_path: Path):
    """evaluate_calibration_state returns no_battery when no battery."""
    mgr = MagicMock()
    mgr.is_available = False

    result = evaluate_calibration_state(mgr=mgr)
    assert result == {"active": False, "reason": "no_battery"}


def test_evaluate_calibration_state_completed(tmp_path: Path):
    """evaluate_calibration_state completes top-up when battery is full."""
    mgr = MagicMock()
    mgr.is_available = True

    info = BatteryInfo(present=True, status="Full", capacity=100, ac_online=False)
    mgr.get_battery_info.return_value = info

    with patch("gigamate.config.load") as mock_load:
        with patch("gigamate.config.update_config") as mock_update:
            mock_load.return_value = {
                "charge_topup_active": True,
                "charge_topup_started_ts": 1000,
                "last_full_charge_ts": 0,
            }

            result = evaluate_calibration_state(mgr=mgr, cfg=mock_load.return_value)
            assert result == {"active": False, "event": "completed"}
            assert mock_update.call_count >= 1


def test_evaluate_calibration_state_timeout(tmp_path: Path):
    """evaluate_calibration_state times out after 24h."""
    mgr = MagicMock()
    mgr.is_available = True

    info = BatteryInfo(present=True, status="Not charging", capacity=95, ac_online=False)
    mgr.get_battery_info.return_value = info

    with patch("gigamate.config.load") as mock_load:
        with patch("gigamate.config.update_config") as mock_update:
            mock_load.return_value = {
                "charge_topup_active": True,
                "charge_topup_started_ts": 100,  # old timestamp
                "last_full_charge_ts": 0,
            }

            result = evaluate_calibration_state(mgr=mgr, cfg=mock_load.return_value)
            assert result == {"active": False, "event": "timeout"}
            mock_update.assert_called_once()


def test_evaluate_calibration_state_auto_started(tmp_path: Path):
    """evaluate_calibration_state auto-starts calibration when conditions met."""
    mgr = MagicMock()
    mgr.is_available = True
    mgr.is_charge_limit_supported.return_value = True
    mgr.set_charge_limit.return_value = True

    info = BatteryInfo(present=True, status="Not charging", capacity=85, ac_online=True)
    mgr.get_battery_info.return_value = info

    with patch("gigamate.config.load") as mock_load:
        with patch("gigamate.config.update_config") as mock_update:
            # 35 days since last full charge (> 30 days interval)
            mock_load.return_value = {
                "charge_limit_enabled": True,
                "charge_limit": 80,
                "charge_calibration_auto": True,
                "charge_calibration_interval_days": 30,
                "charge_topup_active": False,
                "last_full_charge_ts": int(time.time()) - (35 * 86400),
            }

            result = evaluate_calibration_state(mgr=mgr, cfg=mock_load.return_value)
            assert result == {"active": True, "event": "auto_started"}
            mock_update.assert_called_once()


def test_evaluate_calibration_state_first_run_seed(tmp_path: Path):
    """evaluate_calibration_state seeds last_full_ts on first run when not set."""
    mgr = MagicMock()
    mgr.is_available = True

    info = BatteryInfo(present=True, status="Not charging", capacity=85, ac_online=True)
    mgr.get_battery_info.return_value = info

    with patch("gigamate.config.load") as mock_load:
        with patch("gigamate.config.update_config") as mock_update:
            mock_load.return_value = {
                "charge_limit_enabled": True,
                "charge_limit": 80,
                "charge_calibration_auto": True,
                "charge_topup_active": False,
                "last_full_charge_ts": 0,
            }

            result = evaluate_calibration_state(mgr=mgr, cfg=mock_load.return_value)
            # Seeds to now on first run, does not start immediately
            assert result == {"active": False, "event": "idle"}
            mock_update.assert_called_once()


