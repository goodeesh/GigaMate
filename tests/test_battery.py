import pytest
from pathlib import Path
from gigamate.battery import BatteryManager, BatteryInfo


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
