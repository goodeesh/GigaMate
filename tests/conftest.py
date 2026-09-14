"""Shared pytest configuration for the GigaMate test-suite.

The test-suite must never mutate the real machine or read the developer's real
config. We isolate config/profiles and replace every hardware/network/system
entry point with inert doubles.

Only the high-level entry points used by the UI/orchestration layers are
patched. Dedicated unit tests that exercise primitives (``gigamate.protocol``,
``gigamate.battery.BatteryManager``, ``gigamate.acpi.AcpiController``) import
those names directly and are unaffected; tests that patch the same UI-layer
names override these defaults.
"""

from unittest.mock import MagicMock

import pytest


def _safe_apply_hardware_settings(*args, **kwargs):
    return {"profile": False, "battery": False, "keyboard": False}


@pytest.fixture(autouse=True)
def _no_real_hardware(monkeypatch, tmp_path):
    """Replace hardware/config side-effect entry points with inert doubles."""
    # ── Config isolation (all tests) ──
    import gigamate.config as config_mod

    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_mod, "_OLD_CONFIG_FILE", tmp_path / "no-legacy.json", raising=False)
    config_mod.save(dict(config_mod.DEFAULT_CONFIG))
    # Names bound at import time in UI modules must be redirected too.
    monkeypatch.setattr("gigamate.ui.main_window.CONFIG_FILE", cfg_file, raising=False)
    monkeypatch.setattr("gigamate.ui.onboarding.CONFIG_FILE", cfg_file, raising=False)

    # ── User profiles isolation ──
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", tmp_path / "profiles", raising=False)

    # ── High-level hardware reapplication ──
    noop_apply = MagicMock(side_effect=_safe_apply_hardware_settings)
    monkeypatch.setattr("gigamate.hardware.apply_hardware_settings", noop_apply, raising=False)
    monkeypatch.setattr("gigamate.sleep_handler.apply_hardware_settings", noop_apply, raising=False)
    monkeypatch.setattr(
        "gigamate.ui.onboarding.apply_hardware_settings",
        MagicMock(return_value={"profile": True, "battery": True, "keyboard": True}),
        raising=False,
    )

    # ── Battery singleton (BatteryManager direct tests are unaffected) ──
    from gigamate.battery import BatteryInfo

    fake_battery = MagicMock()
    fake_battery.is_available = True
    fake_battery.is_charge_limit_supported.return_value = False
    fake_battery.get_battery_info.return_value = BatteryInfo(
        present=True, name="BAT0", capacity=80, status="Charging", ac_online=True,
    )
    monkeypatch.setattr(
        "gigamate.battery.get_battery_manager", lambda *a, **k: fake_battery, raising=False
    )

    # ── UI-layer patches require PyQt6 ──
    try:
        import gigamate.ui.main_window  # noqa: F401
        import gigamate.ui.pages.dashboard_page  # noqa: F401
        import gigamate.ui.pages.rgb_page  # noqa: F401
        import gigamate.ui.pages.battery_page  # noqa: F401
        import gigamate.ui.pages.settings_page  # noqa: F401
    except Exception:
        yield
        return

    from gigamate.acpi import AcpiCapabilities, FanProfile, FanState
    from gigamate.capabilities import HardwareCapabilities
    from gigamate.gpu import GpuState

    generic_caps = HardwareCapabilities(
        product_name="Generic PC",
        is_gigabyte_laptop=False,
        acpi_available=False,
        acpi_backend="none",
        acpi_driver_loaded=False,
        acpi_driver_missing=False,
        has_power_profiles=False,
        has_temperature=False,
        has_fan_rpm=False,
        fan_count=0,
        battery_present=True,
        charge_limit_supported=False,
        battery_name="BAT0",
        keyboard_detected=False,
        keyboard_profile_loaded=False,
        keyboard_profile_name=None,
        keyboard_vid_pid=None,
        has_dgpu=False,
        gpu_name="Integrated Only",
    )
    for target in (
        "gigamate.ui.pages.settings_page.detect_system_capabilities",
        "gigamate.ui.main_window.detect_system_capabilities",
        "gigamate.ui.pages.dashboard_page.detect_system_capabilities",
        "gigamate.ui.pages.rgb_page.detect_system_capabilities",
    ):
        monkeypatch.setattr(target, MagicMock(return_value=generic_caps), raising=False)

    monkeypatch.setattr(
        "gigamate.ui.pages.dashboard_page.get_gpu_state",
        MagicMock(return_value=GpuState(present=False)),
        raising=False,
    )
    monkeypatch.setattr(
        "gigamate.ui.pages.dashboard_page.sync_system_power", MagicMock(return_value=True), raising=False
    )
    monkeypatch.setattr(
        "gigamate.ui.pages.settings_page.sync_system_power", MagicMock(return_value=True), raising=False
    )

    # ── Dashboard ACPI controller fake ──
    fake_ctrl = MagicMock()
    fake_ctrl.available = True
    fake_ctrl.capabilities = AcpiCapabilities(
        has_temperature=False, has_fan_rpm=False, has_fan_duty=False,
        has_power_profiles=False, fan_count=2, backend="mock",
    )
    fake_ctrl.read_state.return_value = FanState(profile=FanProfile.BALANCED)
    profile_state = {"value": FanProfile.BALANCED.value}
    fake_ctrl.set_profile.side_effect = lambda p: (profile_state.__setitem__("value", int(p)), True)[1]
    fake_ctrl.get_profile.side_effect = lambda: profile_state["value"]
    monkeypatch.setattr(
        "gigamate.ui.pages.dashboard_page.AcpiController",
        lambda *a, **k: fake_ctrl,
        raising=False,
    )

    # ── Keyboard RGB writes ──
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.get_keyboard", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.set_static", lambda *a, **k: True, raising=False)
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.set_off", lambda *a, **k: True, raising=False)

    # ── Battery page manager ──
    monkeypatch.setattr(
        "gigamate.ui.pages.battery_page.get_battery_manager",
        lambda *a, **k: fake_battery,
        raising=False,
    )

    # ── GTK tray (optional) ──
    try:
        import gigamate.tray  # noqa: F401

        monkeypatch.setattr("gigamate.tray.detect_device", lambda *a, **k: None, raising=False)
        monkeypatch.setattr("gigamate.tray.AcpiController", lambda *a, **k: fake_ctrl, raising=False)
        monkeypatch.setattr("gigamate.tray.get_gpu_state", lambda *a, **k: GpuState(present=False), raising=False)
        monkeypatch.setattr("gigamate.tray.HotkeyListener", MagicMock(), raising=False)
        monkeypatch.setattr("gigamate.tray.sync_system_power", MagicMock(return_value=True), raising=False)
        monkeypatch.setattr(
            "gigamate.tray.update_checker.check_for_updates",
            MagicMock(return_value={"current": "0.0.0", "latest": None,
                                    "update_available": False, "reachable": True}),
            raising=False,
        )
        monkeypatch.setattr("gigamate.tray.get_keyboard", lambda *a, **k: None, raising=False)
        monkeypatch.setattr("gigamate.tray.set_static", lambda *a, **k: True, raising=False)
        monkeypatch.setattr("gigamate.tray.set_off", lambda *a, **k: True, raising=False)
        monkeypatch.setattr("gigamate.tray.apply_hardware_settings", MagicMock(return_value={}), raising=False)
        monkeypatch.setattr("gigamate.tray.get_sleep_handler", lambda *a, **k: MagicMock(), raising=False)
        monkeypatch.setattr("gigamate.tray.get_battery_manager", lambda *a, **k: fake_battery, raising=False)
    except Exception:
        pass

    yield
