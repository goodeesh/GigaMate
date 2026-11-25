"""Unit tests for GigaMate hardware capabilities and subsystem detection."""

from unittest.mock import MagicMock, patch
import pytest

from gigamate.acpi import AcpiCapabilities
from gigamate.battery import BatteryInfo
from gigamate.capabilities import (
    HardwareCapabilities,
    detect_system_capabilities,
    invalidate_capabilities,
)
from gigamate.gpu import GpuState


@pytest.fixture(autouse=True)
def _clear_capabilities_cache():
    """Ensure each test probes fresh hardware instead of a cached snapshot."""
    invalidate_capabilities()
    yield
    invalidate_capabilities()


def test_detect_system_capabilities_full_gigabyte():
    """Test capability detection on a fully-supported Gigabyte laptop."""
    mock_caps = AcpiCapabilities(
        has_temperature=True,
        has_fan_rpm=True,
        has_fan_duty=True,
        has_power_profiles=True,
        fan_count=2,
        backend="module",
    )
    mock_bat = BatteryInfo(present=True, name="BAT1", charge_limit_supported=True)
    mock_gpu = GpuState(present=True, vendor="nvidia")
    discovered = {
        0: {"name": "Quiet", "desc": ""},
        1: {"name": "Balanced", "desc": ""},
        2: {"name": "Performance", "desc": ""},
        3: {"name": "Gaming", "desc": ""},
    }

    mock_lamp_dev = MagicMock()
    mock_lamp_dev.lamp_count.return_value = 96

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="GIGABYTE AERO X16"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.get_dmi_chassis_type", return_value=10), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.discover_acpi_profiles", return_value=discovered), \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.lamparray.detect_gigabyte_hidraw_keyboard",
               return_value=(0x0414, 0x8105, "Gigabyte Aero X16")), \
         patch("gigamate.capabilities.lamparray.detect_lamparray_keyboard",
               return_value=(0x0414, 0x8105, "/dev/hidraw4", 1)), \
         patch("gigamate.capabilities.lamparray.LampArrayDevice", return_value=mock_lamp_dev), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu), \
         patch("gigamate.dgpu_gate.is_supported", return_value=True):

        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = True
        mock_bat_mgr.is_charge_limit_supported.return_value = True
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        caps = detect_system_capabilities()

        assert isinstance(caps, HardwareCapabilities)
        assert caps.is_gigabyte_laptop is True
        assert caps.acpi_available is True
        assert caps.acpi_driver_loaded is True
        assert caps.acpi_driver_missing is False
        assert caps.has_power_profiles is True
        assert caps.discovered_profiles == discovered
        assert caps.fan_count == 2
        assert caps.battery_present is True
        assert caps.charge_limit_supported is True
        assert caps.keyboard_detected is True
        assert caps.keyboard_lamparray_supported is True
        assert caps.keyboard_name == "Gigabyte Aero X16"
        assert caps.keyboard_vid_pid == (0x0414, 0x8105)
        assert caps.has_dgpu is True
        assert caps.dgpu_gate_supported is True


def test_detect_system_capabilities_no_lamparray_keyboard():
    """Test detection when a Gigabyte keyboard is present without LampArray."""
    mock_caps = AcpiCapabilities(backend="module", has_power_profiles=True)
    mock_bat = BatteryInfo(present=True, charge_limit_supported=True)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="GIGABYTE G5"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.get_dmi_chassis_type", return_value=10), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.discover_acpi_profiles", return_value={0: {"name": "Quiet"}, 1: {"name": "Balanced"}}), \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.lamparray.detect_gigabyte_hidraw_keyboard",
               return_value=(0x1044, 0x7A42, "Gigabyte G5")), \
         patch("gigamate.capabilities.lamparray.detect_lamparray_keyboard", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=GpuState(present=False)), \
         patch("gigamate.dgpu_gate.is_supported", return_value=False):

        mock_ctrl = MagicMock()
        mock_ctrl.available = True
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = True
        mock_bat_mgr.is_charge_limit_supported.return_value = True
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        caps = detect_system_capabilities()

        assert caps.keyboard_detected is True
        assert caps.keyboard_lamparray_supported is False
        assert caps.keyboard_name == "Gigabyte G5"
        assert caps.keyboard_vid_pid == (0x1044, 0x7A42)
        assert caps.dgpu_gate_supported is False


def test_detect_system_capabilities_missing_acpi_driver():
    """Test that a Gigabyte laptop without ACPI driver flags acpi_driver_missing."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=True, charge_limit_supported=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="AORUS 15"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.get_dmi_chassis_type", return_value=10), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.lamparray.detect_gigabyte_hidraw_keyboard", return_value=None), \
         patch("gigamate.capabilities.lamparray.detect_lamparray_keyboard", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=GpuState(present=True, vendor="nvidia")), \
         patch("gigamate.dgpu_gate.is_supported", return_value=False):

        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = True
        mock_bat_mgr.is_charge_limit_supported.return_value = False
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        caps = detect_system_capabilities()

        assert caps.is_gigabyte_laptop is True
        assert caps.acpi_available is False
        assert caps.acpi_driver_missing is True
        assert caps.has_power_profiles is False
        assert caps.discovered_profiles == {}


def test_detect_system_capabilities_generic_desktop():
    """Test that a non-Gigabyte desktop returns generic capabilities without missing-driver warning."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="Custom Desktop"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="ASUS"), \
         patch("gigamate.capabilities.get_dmi_chassis_type", return_value=3), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.lamparray.detect_gigabyte_hidraw_keyboard", return_value=None), \
         patch("gigamate.capabilities.lamparray.detect_lamparray_keyboard", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=GpuState(present=False)), \
         patch("gigamate.dgpu_gate.is_supported", return_value=False):

        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = False
        mock_bat_mgr.is_charge_limit_supported.return_value = False
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        caps = detect_system_capabilities()

        assert caps.is_gigabyte_laptop is False
        assert caps.acpi_driver_missing is False
        assert caps.keyboard_detected is False
        assert caps.battery_present is False


def test_detect_system_capabilities_is_cached_until_invalidated():
    """Repeated calls reuse the cached snapshot until refresh=True or invalidated."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="Test PC") as mock_dmi, \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="Generic"), \
         patch("gigamate.capabilities.get_dmi_chassis_type", return_value=3), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.lamparray.detect_gigabyte_hidraw_keyboard", return_value=None), \
         patch("gigamate.capabilities.lamparray.detect_lamparray_keyboard", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=GpuState(present=False)), \
         patch("gigamate.dgpu_gate.is_supported", return_value=False):

        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = False
        mock_bat_mgr.is_charge_limit_supported.return_value = False
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        detect_system_capabilities()
        detect_system_capabilities()
        assert mock_dmi.call_count == 1

        detect_system_capabilities(refresh=True)
        assert mock_dmi.call_count == 2

        invalidate_capabilities()
        detect_system_capabilities()
        assert mock_dmi.call_count == 3
