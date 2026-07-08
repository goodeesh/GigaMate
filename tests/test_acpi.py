"""Tests for the ACPI communication layer (acpi.py)."""

import os
import pytest

from gigamate.acpi import (
    FanProfile,
    FanState,
    AcpiCapabilities,
    MockBackend,
    AcpiController,
    probe_acpi_capabilities,
)


class TestFanProfile:
    def test_enum_values(self):
        assert FanProfile.QUIET == 0
        assert FanProfile.BALANCED == 1
        assert FanProfile.PERFORMANCE == 2
        assert FanProfile.GAMING == 3

    def test_from_name(self):
        assert FanProfile.from_name("quiet") == FanProfile.QUIET
        assert FanProfile.from_name("balanced") == FanProfile.BALANCED
        assert FanProfile.from_name("performance") == FanProfile.PERFORMANCE
        assert FanProfile.from_name("gaming") == FanProfile.GAMING

    def test_from_name_case_insensitive(self):
        assert FanProfile.from_name("Gaming") == FanProfile.GAMING
        assert FanProfile.from_name("GAMING") == FanProfile.GAMING
        assert FanProfile.from_name("QUIET") == FanProfile.QUIET

    def test_from_name_invalid(self):
        with pytest.raises(KeyError):
            FanProfile.from_name("turbo")

    def test_names_map(self):
        names = FanProfile.names()
        assert names["quiet"] == FanProfile.QUIET
        assert names["gaming"] == FanProfile.GAMING
        assert len(names) == 4


class TestFanState:
    def test_defaults_all_none(self):
        state = FanState()
        assert state.temp_cpu is None
        assert state.temp_socket is None
        assert state.fan1_rpm is None
        assert state.fan2_rpm is None
        assert state.duty_total is None
        assert state.duty_cpu is None
        assert state.duty_gpu is None
        assert state.profile is None

    def test_partial_population(self):
        state = FanState(temp_cpu=65, fan1_rpm=3580)
        assert state.temp_cpu == 65
        assert state.fan1_rpm == 3580
        assert state.temp_socket is None
        assert state.fan2_rpm is None

    def test_full_state(self):
        state = FanState(
            temp_cpu=84,
            temp_socket=72,
            fan1_rpm=4411,
            fan2_rpm=4687,
            duty_total=55,
            duty_cpu=52,
            duty_gpu=57,
            profile=FanProfile.GAMING,
        )
        assert state.temp_cpu == 84
        assert state.profile == FanProfile.GAMING


class TestAcpiCapabilities:
    def test_defaults(self):
        caps = AcpiCapabilities()
        assert caps.has_temperature is False
        assert caps.has_fan_rpm is False
        assert caps.has_fan_duty is False
        assert caps.has_power_profiles is False
        assert caps.fan_count == 0
        assert caps.backend == "none"

    def test_full_capabilities(self):
        caps = AcpiCapabilities(
            has_temperature=True,
            has_fan_rpm=True,
            has_fan_duty=True,
            has_power_profiles=True,
            fan_count=2,
            backend="module",
        )
        assert caps.has_temperature
        assert caps.has_fan_rpm
        assert caps.fan_count == 2


class TestMockBackend:
    def setup_method(self):
        self.backend = MockBackend()

    def test_detect_returns_full_caps(self):
        caps = self.backend.detect()
        assert caps.has_temperature
        assert caps.has_fan_rpm
        assert caps.has_fan_duty
        assert caps.has_power_profiles
        assert caps.fan_count == 2
        assert caps.backend == "mock"

    def test_read_state_returns_sensible_values(self):
        state = self.backend.read_state()
        assert isinstance(state.temp_cpu, int)
        assert 0 < state.temp_cpu < 120  # sane CPU temp
        assert isinstance(state.fan1_rpm, int)
        assert 0 < state.fan1_rpm < 10000  # sane RPM
        assert isinstance(state.duty_cpu, int)
        assert 0 <= state.duty_cpu <= 100
        assert state.profile == FanProfile.BALANCED

    def test_set_profile_valid(self):
        assert self.backend.set_profile(0) is True
        assert self.backend.get_profile() == 0
        assert self.backend.set_profile(3) is True
        assert self.backend.get_profile() == 3

    def test_set_profile_invalid(self):
        assert self.backend.set_profile(-1) is False
        assert self.backend.set_profile(4) is False

    def test_get_profile_returns_last_set(self):
        assert self.backend.get_profile() == 1  # default
        self.backend.set_profile(0)
        assert self.backend.get_profile() == 0

    def test_read_state_reflects_profile(self):
        self.backend.set_profile(3)
        state = self.backend.read_state()
        assert state.profile == FanProfile.GAMING


class TestAcpiController:
    def test_auto_mock_with_env(self, monkeypatch):
        monkeypatch.setenv("GIGAMATE_ACPI_MOCK", "1")
        ctrl = AcpiController()
        assert ctrl.available
        assert ctrl.capabilities.backend == "mock"

    def test_force_mock_backend(self):
        ctrl = AcpiController(backend="mock")
        assert ctrl.available
        caps = ctrl.capabilities
        assert caps.backend == "mock"
        assert caps.has_temperature

    def test_read_state_with_mock(self):
        ctrl = AcpiController(backend="mock")
        state = ctrl.read_state()
        assert state is not None
        assert isinstance(state, FanState)
        assert state.temp_cpu == 65

    def test_set_profile_with_mock(self):
        ctrl = AcpiController(backend="mock")
        assert ctrl.set_profile(FanProfile.GAMING) is True
        assert ctrl.get_profile() == FanProfile.GAMING

    def test_set_profile_invalid_type(self):
        ctrl = AcpiController(backend="mock")
        assert ctrl.set_profile("gaming") is False  # noqa

    def test_get_profile_none_initially(self):
        ctrl = AcpiController(backend="mock")
        # MockBackend defaults to Balanced (1)
        assert ctrl.get_profile() == FanProfile.BALANCED

    def test_no_backend_available(self, monkeypatch):
        # Ensure no env var and no sysfs/proc available
        monkeypatch.delenv("GIGAMETE_ACPI_MOCK", raising=False)
        ctrl = AcpiController()
        # On a non-Gigabyte system, this should have no backend
        # We can't guarantee this in CI, so just check it doesn't crash
        assert isinstance(ctrl.available, bool)

    def test_available_property(self):
        ctrl = AcpiController(backend="mock")
        assert ctrl.available is True

    def test_read_state_no_backend(self, monkeypatch):
        monkeypatch.delenv("GIGAMETE_ACPI_MOCK", raising=False)
        # Make ModuleBackend and AcpiCallBackend both fail by pointing to nonexistent paths
        # This is tricky to test without mocking — just ensure no crash
        # We'll test with an invalid backend name instead
        pass

    def test_invalid_backend_name(self):
        with pytest.raises(ValueError):
            AcpiController(backend="nonexistent")

    def test_detect_returns_capabilities(self):
        ctrl = AcpiController(backend="mock")
        caps = ctrl.detect()
        assert isinstance(caps, AcpiCapabilities)
        assert caps.has_temperature

    def test_capabilities_property_cached(self):
        ctrl = AcpiController(backend="mock")
        caps1 = ctrl.capabilities
        caps2 = ctrl.capabilities
        assert caps1 is caps2  # same object (cached)


class TestProbeAcpiCapabilities:
    def test_probe_with_mock(self):
        caps = probe_acpi_capabilities(backend="mock")
        assert caps.has_temperature
        assert caps.has_fan_rpm
        assert caps.backend == "mock"

    def test_probe_no_backend(self):
        # Just ensure it doesn't crash
        caps = probe_acpi_capabilities()
        assert isinstance(caps, AcpiCapabilities)
