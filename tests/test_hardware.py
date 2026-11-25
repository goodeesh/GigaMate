"""Unit tests for gigamate.hardware high-level orchestration."""

from unittest.mock import MagicMock, patch

from gigamate.acpi import FanProfile
from gigamate.hardware import (
    apply_hardware_settings,
    apply_profile,
)


def test_apply_profile_succeeds_with_discovered_profile():
    mock_ctrl = MagicMock()
    mock_ctrl.available = True
    mock_ctrl.get_profile.return_value = None
    mock_ctrl.set_profile.return_value = True

    with patch("gigamate.acpi.AcpiController", return_value=mock_ctrl), \
         patch("gigamate.hardware.load_config", return_value={}), \
         patch("gigamate.hardware.update_config"):
        assert apply_profile(1, sync_system=False) is True
        mock_ctrl.set_profile.assert_called_once_with(FanProfile(1))


def test_apply_profile_returns_false_when_acpi_unavailable():
    mock_ctrl = MagicMock()
    mock_ctrl.available = False

    with patch("gigamate.acpi.AcpiController", return_value=mock_ctrl), \
         patch("gigamate.hardware.load_config", return_value={}), \
         patch("gigamate.hardware.update_config"):
        assert apply_profile(1, sync_system=False) is False


def test_apply_hardware_settings_applies_all_subsystems():
    cfg = {
        "acpi_profile": 1,
        "charge_limit_enabled": True,
        "charge_limit": 80,
        "startup_apply": True,
        "rgb_hex": "#00b4a0",
        "brightness_pct": 75,
        "rgb_enabled": True,
    }
    mock_ctrl = MagicMock()
    mock_ctrl.available = True
    mock_ctrl.get_profile.return_value = None
    mock_ctrl.set_profile.return_value = True

    mock_bat = MagicMock()
    mock_bat.is_charge_limit_supported.return_value = True
    mock_bat.set_charge_limit.return_value = True

    mock_dev = MagicMock()
    with patch("gigamate.hardware.load_config", return_value=cfg), \
         patch("gigamate.acpi.AcpiController", return_value=mock_ctrl), \
         patch("gigamate.power_limits.discover_acpi_profiles", return_value={0: {"name": "Quiet"}, 1: {"name": "Balanced"}}), \
         patch("gigamate.battery.get_battery_manager", return_value=mock_bat), \
         patch("gigamate.protocol.get_keyboard", return_value=mock_dev), \
         patch("gigamate.protocol.set_rgb", return_value=True) as mock_rgb:
        res = apply_hardware_settings()
        assert res == {"profile": True, "battery": True, "keyboard": True, "dgpu": False}
        mock_ctrl.set_profile.assert_called_once_with(FanProfile(1))
        mock_bat.set_charge_limit.assert_called_once_with(80)
        mock_rgb.assert_called_once()

