from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from gigamate.acpi import AcpiController
from gigamate.battery import BatteryManager
from gigamate.power_automation import (
    DisplayManager,
    DisplayMode,
    DisplayPanel,
    PowerAutomationEngine,
)


def test_display_mode_selection():
    mgr = DisplayManager()
    panel = DisplayPanel(
        name="eDP-1",
        connected=True,
        enabled=True,
        modes=[
            DisplayMode(id="111", resolution="2560x1600", rate=165.0),
            DisplayMode(id="112", resolution="2560x1600", rate=60.0),
            DisplayMode(id="113", resolution="1920x1080", rate=165.0),
        ],
    )
    with patch.object(mgr, "find_internal_panel", return_value=panel):
        with patch("subprocess.run") as mock_run:
            mgr._kscreen_doctor = "/usr/bin/kscreen-doctor"

            # 60Hz selection
            assert mgr.set_refresh_rate(60, max_rate=False) is True
            mock_run.assert_called_with(
                ["/usr/bin/kscreen-doctor", "output.eDP-1.mode.112"],
                check=True,
                stdout=-3,  # DEVNULL
                stderr=-3,
            )

            # Max rate (165Hz) selection
            assert mgr.set_refresh_rate(60, max_rate=True) is True
            mock_run.assert_called_with(
                ["/usr/bin/kscreen-doctor", "output.eDP-1.mode.111"],
                check=True,
                stdout=-3,
                stderr=-3,
            )


def test_power_automation_ac_to_battery_transition():
    mock_battery = MagicMock(spec=BatteryManager)
    mock_acpi = MagicMock(spec=AcpiController)
    mock_acpi.available = True
    mock_display = MagicMock(spec=DisplayManager)

    engine = PowerAutomationEngine(
        battery_mgr=mock_battery,
        acpi_ctrl=mock_acpi,
        display_mgr=mock_display,
    )

    # Initial state: AC online
    mock_battery.is_ac_online.return_value = True
    assert engine.poll() is None  # Initial sync

    # Unplug AC -> Battery
    mock_battery.is_ac_online.return_value = False
    with patch("gigamate.power_automation.load_config", return_value={
        "power_automation_enabled": True,
        "battery_profile": 0,  # Quiet
        "display_refresh_auto": True,
        "battery_refresh_rate": 60,
    }):
        res = engine.poll()
        assert res is False  # Switched to battery
        mock_acpi.set_profile.assert_called_with(0)
        mock_display.set_refresh_rate.assert_called_with(60, max_rate=False)


def test_power_automation_battery_to_ac_transition():
    mock_battery = MagicMock(spec=BatteryManager)
    mock_acpi = MagicMock(spec=AcpiController)
    mock_acpi.available = True
    mock_display = MagicMock(spec=DisplayManager)

    engine = PowerAutomationEngine(
        battery_mgr=mock_battery,
        acpi_ctrl=mock_acpi,
        display_mgr=mock_display,
    )

    # Initial state: on Battery
    mock_battery.is_ac_online.return_value = False
    engine.poll()

    # Plug in AC
    mock_battery.is_ac_online.return_value = True
    with patch("gigamate.power_automation.load_config", return_value={
        "power_automation_enabled": True,
        "ac_profile": 1,  # Balanced
        "display_refresh_auto": True,
    }):
        res = engine.poll()
        assert res is True  # Switched to AC
        mock_acpi.set_profile.assert_called_with(1)
        mock_display.set_refresh_rate.assert_called_with(60, max_rate=True)
