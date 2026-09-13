"""Unit tests for GigaMate hardware capabilities and subsystem detection."""

import pytest
from unittest.mock import MagicMock, patch
from gigamate.capabilities import (
    detect_system_capabilities,
    invalidate_capabilities,
    HardwareCapabilities,
)
from gigamate.acpi import AcpiCapabilities
from gigamate.battery import BatteryInfo
from gigamate.gpu import GpuState
from gigamate.profiles import DeviceProfile


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
    mock_bat = BatteryInfo(
        present=True,
        name="BAT1",
        charge_limit_supported=True,
    )
    mock_gpu = GpuState(
        present=True,
        vendor="nvidia",
    )
    mock_profile = DeviceProfile(
        vid=0x0414,
        pid=0x8105,
        name="Gigabyte Aero 16",
    )

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="GIGABYTE AERO X16"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=(0x0414, 0x8105)), \
         patch("gigamate.capabilities.resolve_profile", return_value=mock_profile), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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

        assert caps.is_gigabyte_laptop is True
        assert caps.acpi_available is True
        assert caps.acpi_driver_loaded is True
        assert caps.acpi_driver_missing is False
        assert caps.fan_count == 2
        assert caps.battery_present is True
        assert caps.charge_limit_supported is True
        assert caps.keyboard_detected is True
        assert caps.keyboard_profile_loaded is True
        assert caps.keyboard_profile_name == "Gigabyte Aero 16"
        assert caps.has_dgpu is True


def test_detect_system_capabilities_uncalibrated_keyboard():
    """Test detection when a Gigabyte keyboard is plugged in but has no profile."""
    mock_caps = AcpiCapabilities(backend="module", has_power_profiles=True)
    mock_bat = BatteryInfo(present=True, charge_limit_supported=True)
    mock_gpu = GpuState(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="AORUS 15X"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=(0x0414, 0x9999)), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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

        assert caps.is_gigabyte_laptop is True
        assert caps.keyboard_detected is True
        assert caps.keyboard_profile_loaded is False
        assert caps.keyboard_vid_pid == (0x0414, 0x9999)


def test_detect_system_capabilities_missing_acpi_driver():
    """Test detection when Gigabyte laptop has no ACPI driver loaded."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=True, charge_limit_supported=False)
    mock_gpu = GpuState(present=True, vendor="nvidia")

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="GIGABYTE G5 KF"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=None), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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
        assert caps.acpi_driver_loaded is False
        assert caps.acpi_driver_missing is True
        assert caps.charge_limit_supported is False


def test_detect_system_capabilities_generic_desktop():
    """Test detection on a generic non-Gigabyte desktop PC."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=False)
    mock_gpu = GpuState(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="Custom Desktop"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="ASUSTeK COMPUTER INC."), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=None), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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
        assert caps.acpi_available is False
        assert caps.acpi_driver_missing is False
        assert caps.battery_present is False
        assert caps.keyboard_detected is False


def test_detect_system_capabilities_rejects_lookalike_model_name():
    """A Dell G5 must not be misclassified as Gigabyte despite the 'G5' token."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=True, charge_limit_supported=False)
    mock_gpu = GpuState(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="Dell G5 5500"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="Dell Inc."), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=None), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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

        assert caps.is_gigabyte_laptop is False
        assert caps.acpi_driver_missing is False


def test_detect_system_capabilities_uses_dmi_vendor():
    """A brand-less Gigabyte model number is identified via the DMI vendor string."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=True, charge_limit_supported=False)
    mock_gpu = GpuState(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="G5 KF"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="GIGABYTE"), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=None), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

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
        assert caps.acpi_driver_missing is True


def test_detect_system_capabilities_is_cached_until_invalidated():
    """Repeated detection reuses the cached snapshot; invalidate forces a re-probe."""
    mock_caps = AcpiCapabilities(backend="none")
    mock_bat = BatteryInfo(present=False)
    mock_gpu = GpuState(present=False)

    with patch("gigamate.capabilities.get_dmi_product_name", return_value="Custom Desktop"), \
         patch("gigamate.capabilities.get_dmi_vendor", return_value="ASUSTeK COMPUTER INC."), \
         patch("gigamate.capabilities.AcpiController") as mock_ctrl_cls, \
         patch("gigamate.capabilities.get_battery_manager") as mock_bat_mgr_getter, \
         patch("gigamate.capabilities.detect_device", return_value=None), \
         patch("gigamate.capabilities.resolve_profile", return_value=None), \
         patch("gigamate.capabilities.get_gpu_state", return_value=mock_gpu):

        mock_ctrl = MagicMock()
        mock_ctrl.available = False
        mock_ctrl.capabilities = mock_caps
        mock_ctrl_cls.return_value = mock_ctrl

        mock_bat_mgr = MagicMock()
        mock_bat_mgr.is_available = False
        mock_bat_mgr.is_charge_limit_supported.return_value = False
        mock_bat_mgr.get_battery_info.return_value = mock_bat
        mock_bat_mgr_getter.return_value = mock_bat_mgr

        first = detect_system_capabilities()
        second = detect_system_capabilities()
        assert first is second
        assert mock_ctrl_cls.call_count == 1

        invalidate_capabilities()
        third = detect_system_capabilities()
        assert third is not first
        assert mock_ctrl_cls.call_count == 2
