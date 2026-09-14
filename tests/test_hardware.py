"""Tests for hardware state synchronization and reapplication (hardware.py)."""

from unittest.mock import MagicMock, patch
import pytest

from gigamate.hardware import apply_hardware_settings
from gigamate.acpi import FanProfile


def test_apply_hardware_settings_full():
    cfg = {
        "acpi_profile": 2,
        "sync_system_power": True,
        "charge_limit_enabled": True,
        "charge_limit": 80,
        "startup_apply": True,
        "brightness": 1,
        "colour": "light_blue",
    }

    with patch("gigamate.acpi.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.system_power.sync_system_power") as mock_sync_sys, \
         patch("gigamate.gpu.sync_gpu_power") as mock_sync_gpu, \
         patch("gigamate.battery.get_battery_manager") as mock_get_bat, \
         patch("gigamate.protocol.get_keyboard") as mock_get_kb, \
         patch("gigamate.protocol.set_static") as mock_set_static, \
         patch("gigamate.hardware.resolve_active_profile", return_value=None):

        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        mock_ctrl.set_profile.return_value = True
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat = MagicMock()
        mock_bat.is_charge_limit_supported.return_value = True
        mock_bat.set_charge_limit.return_value = True
        mock_get_bat.return_value = mock_bat

        mock_kb = MagicMock()
        mock_get_kb.return_value = mock_kb

        results = apply_hardware_settings(cfg)

        assert results["profile"] is True
        assert results["battery"] is True
        assert results["keyboard"] is True

        mock_ctrl.set_profile.assert_called_once_with(FanProfile.PERFORMANCE)
        # sync_system_power() is the single entry point and also performs the
        # GPU power sync; it must not be duplicated here.
        mock_sync_sys.assert_called_once_with(2)
        mock_sync_gpu.assert_not_called()
        mock_bat.set_charge_limit.assert_called_once_with(80)
        mock_set_static.assert_called_once()


def test_apply_hardware_settings_subsystem_isolation():
    """If keyboard fails or is missing, battery limit and ACPI profile must still succeed."""
    cfg = {
        "acpi_profile": 0,
        "sync_system_power": False,
        "charge_limit_enabled": True,
        "charge_limit": 60,
        "startup_apply": True,
        "brightness": 2,
        "colour": "red",
    }

    with patch("gigamate.acpi.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.system_power.sync_system_power") as mock_sync_sys, \
         patch("gigamate.gpu.sync_gpu_power"), \
         patch("gigamate.battery.get_battery_manager") as mock_get_bat, \
         patch("gigamate.protocol.get_keyboard", side_effect=RuntimeError("USB error")):

        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        mock_ctrl.set_profile.return_value = True
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat = MagicMock()
        mock_bat.is_charge_limit_supported.return_value = True
        mock_bat.set_charge_limit.return_value = True
        mock_get_bat.return_value = mock_bat

        results = apply_hardware_settings(cfg)

        assert results["profile"] is True
        assert results["battery"] is True
        assert results["keyboard"] is False

        mock_ctrl.set_profile.assert_called_once_with(FanProfile.QUIET)
        mock_sync_sys.assert_not_called()
        mock_bat.set_charge_limit.assert_called_once_with(60)


def test_apply_hardware_settings_skips_when_disabled():
    cfg = {
        "acpi_profile": None,
        "charge_limit_enabled": False,
        "startup_apply": False,
    }

    with patch("gigamate.acpi.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.battery.get_battery_manager") as mock_get_bat, \
         patch("gigamate.protocol.get_keyboard") as mock_get_kb:

        results = apply_hardware_settings(cfg)

        assert results["profile"] is False
        assert results["battery"] is False
        assert results["keyboard"] is False
        mock_ctrl_cls.assert_not_called()
        mock_get_bat.assert_not_called()
        mock_get_kb.assert_not_called()


def test_force_keyboard_applies_when_startup_disabled():
    """After resume the backlight must be restored even if startup_apply is off."""
    cfg = {
        "acpi_profile": None,
        "charge_limit_enabled": False,
        "startup_apply": False,
        "brightness": 2,
        "colour": "red",
    }

    with patch("gigamate.protocol.get_keyboard") as mock_get_kb, \
         patch("gigamate.protocol.set_static") as mock_set_static, \
         patch("gigamate.hardware.resolve_active_profile", return_value=None):
        mock_get_kb.return_value = MagicMock()

        results = apply_hardware_settings(cfg, force_keyboard=True)

        assert results["keyboard"] is True
        mock_set_static.assert_called_once()


def test_acpi_set_failure_does_not_block_power_sync():
    """A failing ACPI profile write must not suppress system/GPU power sync."""
    cfg = {"acpi_profile": 2, "sync_system_power": True}

    with patch("gigamate.acpi.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.system_power.sync_system_power") as mock_sync_sys:
        mock_ctrl = mock_ctrl_cls.return_value
        mock_ctrl.available = True
        mock_ctrl.set_profile.side_effect = RuntimeError("boom")

        results = apply_hardware_settings(cfg)

        assert results["profile"] is False
        mock_sync_sys.assert_called_once_with(2)


def test_invalid_acpi_profile_does_not_crash_or_sync():
    cfg = {"acpi_profile": "gaming", "sync_system_power": True}
    with patch("gigamate.acpi.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.system_power.sync_system_power") as mock_sync_sys:
        results = apply_hardware_settings(cfg)
        assert results["profile"] is False
        mock_ctrl_cls.assert_not_called()
        mock_sync_sys.assert_not_called()
