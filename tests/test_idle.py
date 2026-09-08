"""Tests for keyboard idle auto-off (idle.py + config keys)."""

import time

from gigamate import idle as idle_module
from gigamate.idle import (
    IDLE_STEP_OFF,
    IDLE_TIMEOUT_STEPS,
    IdleMonitor,
    clamp_timeout,
    idle_step_label,
    nearest_idle_step,
    DEFAULT_TIMEOUT_SEC,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
)


def test_clamp_timeout_bounds():
    assert clamp_timeout(60) == 60
    assert clamp_timeout(10) == 10
    assert clamp_timeout(5) == MIN_TIMEOUT_SEC
    assert clamp_timeout(0) == MIN_TIMEOUT_SEC
    assert clamp_timeout(-5) == MIN_TIMEOUT_SEC
    assert clamp_timeout(99999) == MAX_TIMEOUT_SEC
    assert clamp_timeout("bogus") == DEFAULT_TIMEOUT_SEC
    assert clamp_timeout(None) == DEFAULT_TIMEOUT_SEC
    assert MIN_TIMEOUT_SEC == 10


def test_idle_fires_after_timeout():
    fired = []
    active = []
    mon = IdleMonitor(
        on_idle=lambda: fired.append(1),
        on_active=lambda: active.append(1),
        timeout_sec=60,
        enabled=True,
    )
    t0 = time.monotonic()
    mon._last_activity = t0
    # Before deadline: no dispatch.
    assert mon._handle_timeout(t0 + 10) is False
    assert fired == []
    # After deadline: exactly one dispatch.
    assert mon._handle_timeout(t0 + 61) is True
    assert mon._handle_timeout(t0 + 62) is False  # already idle


def test_activity_clears_idle_with_debounce():
    fired = []
    mon = IdleMonitor(
        on_idle=lambda: None,
        on_active=lambda: fired.append(1),
        timeout_sec=60,
        enabled=True,
        debounce_sec=60,
    )
    t0 = time.monotonic()
    mon._last_activity = t0
    assert mon._handle_timeout(t0 + 61) is True
    # First activity after idle → dispatch due.
    assert mon._handle_activity(t0 + 62) is True
    # Immediate second burst → debounced, no dispatch.
    assert mon._handle_activity(t0 + 63) is False


def test_disabled_monitor_never_fires():
    mon = IdleMonitor(
        on_idle=lambda: (_ for _ in ()).throw(AssertionError("must not fire")),
        on_active=lambda: None,
        timeout_sec=60,
        enabled=False,
    )
    t0 = time.monotonic()
    mon._last_activity = t0
    assert mon._handle_timeout(t0 + 3600) is False


def test_set_timeout_clamps_and_resets_idle():
    mon = IdleMonitor(on_idle=lambda: None, on_active=lambda: None,
                      timeout_sec=60, enabled=True)
    mon.set_timeout(99999)
    assert mon.timeout_sec == MAX_TIMEOUT_SEC
    assert mon.is_idle is False
    mon.set_enabled(False)
    assert mon.enabled is False


def test_idle_step_values():
    assert [s for s, _ in IDLE_TIMEOUT_STEPS] == [0, 10, 30, 60, 120]
    assert IDLE_STEP_OFF == 0
    assert idle_step_label(0) == "Off"
    assert idle_step_label(10) == "10 seconds"
    assert idle_step_label(60) == "1 minute"


def test_nearest_idle_step():
    assert nearest_idle_step(10) == 10
    assert nearest_idle_step(30) == 30
    assert nearest_idle_step(300) == 120  # legacy 5m maps to nearest
    assert nearest_idle_step(45) == 30  # tie prefers smaller
    assert nearest_idle_step(50) == 60


def test_device_filter_denylist(tmp_path, monkeypatch):
    """Lid/power/video/speaker/audio nodes are excluded, keyboards kept."""
    import os
    # Fake sysfs tree: /sys/class/input/<ev>/device/{name,uevent}
    root = tmp_path / "sys" / "class" / "input"
    cases = {
        "event0": ("Lid Switch", "EV=21\n", False),
        "event1": ("Power Button", "EV=3\n", False),
        "event2": ("AT Translated Set 2 keyboard", "EV=120013\n", True),
        "event3": ("ELAN0A05:00 Touchpad", "EV=1b\n", True),
        "event4": ("HD-Audio Generic Headphone", "EV=3\n", False),
        "event5": ("PC Speaker", "EV=40001\n", False),
    }
    for ev, (name, uevent, _keep) in cases.items():
        d = root / ev / "device"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        (d / "uevent").write_text(f'PRODUCT=00/00/00/00\nNAME="{name}"\n{uevent}')
    monkeypatch.setattr(idle_module, "_device_name",
                        lambda s: open(os.path.join(s, "device/name")).read().strip()
                        if os.path.exists(os.path.join(s, "device/name")) else "")
    orig = idle_module._device_ev_mask
    monkeypatch.setattr(idle_module, "_device_ev_mask",
                        lambda s: orig(s))
    # Point glob at real /dev nodes is unsafe; instead check predicate
    # directly against the fake tree.
    for ev, (_name, _uev, keep) in cases.items():
        got = idle_module._is_input_device(str(root / ev))
        assert got is keep, f"{ev} ({_name}) -> {got}, expected {keep}"


def test_fallback_helpers_never_raise():    # Must degrade to None on systems without the backend (e.g. KDE
    # Wayland has no Mutter IdleMonitor / GetSessionIdleTime).
    for fn in (idle_module.mutter_idle_ms,
               idle_module.screensaver_idle_ms,
               idle_module.x11_idle_ms):
        try:
            val = fn()
        except Exception as exc:  # pragma: no cover
            raise AssertionError(f"{fn.__name__} raised {exc!r}")
        assert val is None or isinstance(val, int)
    ms, name = idle_module.fallback_idle_ms()
    assert (ms is None and name == "none") or isinstance(ms, int)
