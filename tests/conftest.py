"""Shared pytest configuration for the GigaMate test-suite.

The test-suite must never mutate the real machine. UI pages and the hardware
orchestration layers are patched here at their entry points so that merely
constructing a window or triggering a profile switch cannot write to ACPI,
USB, or power-supply sysfs.

Only the high-level entry points used by the UI/orchestration layers are
patched. Dedicated unit tests that exercise the underlying primitives
(``gigamate.protocol``, ``gigamate.battery.BatteryManager``,
``gigamate.acpi.AcpiController``) import those names directly and are therefore
unaffected; tests that patch the same UI-layer names override these defaults.
"""

from unittest.mock import MagicMock

import pytest


def _safe_apply_hardware_settings(*args, **kwargs):
    return {"profile": False, "battery": False, "keyboard": False}


@pytest.fixture(autouse=True)
def _no_real_hardware(monkeypatch, tmp_path):
    """Replace hardware side-effect entry points with inert doubles."""
    # High-level reapplication used lazily by MainWindow and imported directly
    # by the sleep handler / tray.
    noop_apply = MagicMock(side_effect=_safe_apply_hardware_settings)
    monkeypatch.setattr("gigamate.hardware.apply_hardware_settings", noop_apply, raising=False)
    monkeypatch.setattr("gigamate.sleep_handler.apply_hardware_settings", noop_apply, raising=False)

    # Never migrate from a real legacy config file into the isolated config.
    monkeypatch.setattr(
        "gigamate.config._OLD_CONFIG_FILE",
        tmp_path / "nonexistent-legacy-config.json",
        raising=False,
    )

    # UI-layer patches require PyQt6; skip them when the GUI stack is unavailable
    # so pure-python test modules can still run.
    try:
        import gigamate.ui.pages.dashboard_page  # noqa: F401
        import gigamate.ui.pages.rgb_page  # noqa: F401
        import gigamate.ui.pages.battery_page  # noqa: F401
        import gigamate.ui.main_window  # noqa: F401
        import gigamate.ui.pages.settings_page  # noqa: F401
    except Exception:
        yield
        return

    # ── Power/GPU synchronization: must not touch real D-Bus/systemctl/sysfs ──
    monkeypatch.setattr(
        "gigamate.ui.pages.dashboard_page.sync_system_power", MagicMock(return_value=True), raising=False
    )
    monkeypatch.setattr(
        "gigamate.ui.pages.settings_page.sync_system_power", MagicMock(return_value=True), raising=False
    )

    # ── IPC: use an isolated per-test socket/lock, never the live instance ──
    sock = str(tmp_path / "gigamate-center-test.sock")
    lock = str(tmp_path / "gigamate-center-test.lock")
    monkeypatch.setattr("gigamate.ui.main_window.IPC_SOCKET_PATH", sock, raising=False)
    monkeypatch.setattr("gigamate.ui.main_window.IPC_LOCK_PATH", lock, raising=False)
    monkeypatch.setattr("gigamate.ui.main_window.IPC_SOCKET_NAME", sock, raising=False)

    # ── Dashboard ACPI controller: populated, side-effect-free fake ──
    from gigamate.acpi import AcpiCapabilities, FanProfile, FanState

    fake_ctrl = MagicMock()
    fake_ctrl.available = True
    fake_ctrl.capabilities = AcpiCapabilities(
        has_temperature=False,
        has_fan_rpm=False,
        has_fan_duty=False,
        has_power_profiles=False,
        fan_count=2,
        backend="mock",
    )
    fake_ctrl.read_state.return_value = FanState(
        temp_cpu=None,
        temp_socket=None,
        fan1_rpm=None,
        fan2_rpm=None,
        duty_total=None,
        duty_cpu=None,
        duty_gpu=None,
        profile=FanProfile.BALANCED,
    )
    fake_ctrl.get_profile.return_value = FanProfile.BALANCED.value
    fake_ctrl.set_profile.return_value = True

    # Emulate a stateful hardware profile so UI refresh observes the last set value.
    profile_state = {"value": FanProfile.BALANCED.value}

    def _set_profile(profile):
        profile_state["value"] = int(profile)
        return True

    fake_ctrl.set_profile.side_effect = _set_profile
    fake_ctrl.get_profile.side_effect = lambda: profile_state["value"]
    monkeypatch.setattr(
        "gigamate.ui.pages.dashboard_page.AcpiController",
        lambda *args, **kwargs: fake_ctrl,
        raising=False,
    )

    # ── Keyboard RGB: never open the USB device or write lighting ──
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.get_keyboard", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.set_static", lambda *a, **k: True, raising=False)
    monkeypatch.setattr("gigamate.ui.pages.rgb_page.set_off", lambda *a, **k: True, raising=False)

    # ── Battery: read-only fake, charge limiter reported unsupported ──
    from gigamate.battery import BatteryInfo

    fake_battery = MagicMock()
    fake_battery.is_available = True
    fake_battery.is_charge_limit_supported.return_value = False
    fake_battery.get_battery_info.return_value = BatteryInfo(
        present=True,
        name="BAT0",
        capacity=80,
        status="Charging",
        ac_online=True,
    )
    monkeypatch.setattr(
        "gigamate.ui.pages.battery_page.get_battery_manager",
        lambda *args, **kwargs: fake_battery,
        raising=False,
    )

    # ── GTK tray (optional): no real network check, system bus, or USB writes ──
    try:
        import gigamate.tray  # noqa: F401

        monkeypatch.setattr(
            "gigamate.tray.update_checker.check_for_updates",
            MagicMock(return_value={"current": "0.0.0", "latest": None,
                                    "update_available": False, "reachable": True}),
            raising=False,
        )
        monkeypatch.setattr("gigamate.tray.get_keyboard", lambda *a, **k: None, raising=False)
        monkeypatch.setattr("gigamate.tray.set_static", lambda *a, **k: True, raising=False)
        monkeypatch.setattr("gigamate.tray.set_off", lambda *a, **k: True, raising=False)
        monkeypatch.setattr(
            "gigamate.tray.apply_hardware_settings", MagicMock(return_value={}), raising=False
        )
        # Do not open a real system-bus sleep listener.
        monkeypatch.setattr(
            "gigamate.tray.get_sleep_handler", lambda *a, **k: MagicMock(), raising=False
        )
        tray_battery = MagicMock()
        tray_battery.is_available = False
        monkeypatch.setattr(
            "gigamate.tray.get_battery_manager", lambda *a, **k: tray_battery, raising=False
        )
    except Exception:
        pass

    yield
