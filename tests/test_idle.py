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
    assert [s for s, _ in IDLE_TIMEOUT_STEPS] == [0, 10, 30, 60, 120, 300, 900]
    assert IDLE_STEP_OFF == 0
    assert idle_step_label(0) == "Off"
    assert idle_step_label(10) == "10 seconds"
    assert idle_step_label(60) == "1 minute"
    assert idle_step_label(900) == "15 minutes"


def test_nearest_idle_step():
    assert nearest_idle_step(10) == 10
    assert nearest_idle_step(30) == 30
    assert nearest_idle_step(300) == 300
    assert nearest_idle_step(200) == 120  # legacy 3m20s maps to nearest
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


def test_is_internal_input_device_classification(tmp_path):
    """Internal buses & fixed USB pass; external USB, Bluetooth, and virtual fail."""
    from gigamate import idle
    def _make_dev(tag, bus, vid, pid, name, phys, usb_removable=None):
        base = tmp_path / tag
        dev = base / "device"
        if usb_removable is not None:
            usb_parent = tmp_path / f"{tag}_usb"
            usb_parent.mkdir(parents=True)
            (usb_parent / "removable").write_text(usb_removable, encoding="utf-8")
            real_dev = usb_parent / "iface"
            real_dev.mkdir()
            base.mkdir(parents=True)
            dev.symlink_to(real_dev)
        else:
            dev.mkdir(parents=True)
        (dev / "id").mkdir()
        (dev / "id" / "bustype").write_text(bus, encoding="utf-8")
        (dev / "id" / "vendor").write_text(vid, encoding="utf-8")
        (dev / "id" / "product").write_text(pid, encoding="utf-8")
        (dev / "name").write_text(name, encoding="utf-8")
        (dev / "phys").write_text(phys, encoding="utf-8")
        return str(base)

    # Built-in PS/2 keyboard (i8042)
    ps2 = _make_dev("ps2", "0011", "0001", "0001", "AT Translated Set 2 keyboard", "isa0060/serio0/input0")
    assert idle._is_internal_input_device(ps2) is True

    # Built-in I2C touchpad
    i2c = _make_dev("i2c", "0018", "04f3", "3337", "ELAN0A05:00 Touchpad", "i2c-ELAN0A05:00")
    assert idle._is_internal_input_device(i2c) is True

    # Built-in Gigabyte USB keyboard (removable=fixed)
    usb_fixed = _make_dev("usb_fixed", "0003", "0414", "8105", "GIGABYTE USB-HID Keyboard", "usb-0000:67:00.0-4/input0", usb_removable="fixed")
    assert idle._is_internal_input_device(usb_fixed) is True

    # Built-in USB keyboard matched by VID:PID even if removable=unknown
    usb_vidpid = _make_dev("usb_vidpid", "0003", "0414", "8105", "GIGABYTE USB-HID Keyboard", "usb-0000:67:00.0-4/input0", usb_removable="unknown")
    assert idle._is_internal_input_device(usb_vidpid, internal_usb_ids={(0x0414, 0x8105)}) is True
    assert idle._is_internal_input_device(usb_vidpid) is False

    # External USB mouse & keyboard
    ext_mouse = _make_dev("ext_mouse", "0003", "046d", "c084", "Logitech G203", "usb-0000:67:00.3-1.4.1/input0", usb_removable="unknown")
    ext_kb = _make_dev("ext_kb", "0003", "046d", "408a", "Logitech MX Keys", "usb-0000:67:00.3-1.4.2/input2:1", usb_removable="removable")
    assert idle._is_internal_input_device(ext_mouse, internal_usb_ids={(0x0414, 0x8105)}) is False
    assert idle._is_internal_input_device(ext_kb, internal_usb_ids={(0x0414, 0x8105)}) is False

    # Bluetooth device
    bt = _make_dev("bt", "0005", "046d", "b023", "MX Master 3", "aa:bb:cc:dd:ee:ff")
    assert idle._is_internal_input_device(bt) is False

    # Virtual uinput / XWayKeyz keyboard
    virt = _make_dev("virt", "0003", "0001", "0001", "XWayKeyz (virtual) Keyboard", "py-evdev-uinput")
    assert idle._is_internal_input_device(virt, internal_usb_ids={(0x0001, 0x0001)}) is False


def test_list_internal_hidraw_nodes_selects_only_builtin_input0(tmp_path):
    """Only readable input0 hidraw node of the built-in keyboard is returned."""
    from gigamate import idle
    sysfs = tmp_path / "sys_hidraw"
    dev_dir = tmp_path / "dev"
    sysfs.mkdir()
    dev_dir.mkdir()

    def _add_hidraw(name, hid_id, hid_name, hid_phys, removable="unknown"):
        usb = tmp_path / f"usb_{name}"
        usb.mkdir()
        (usb / "removable").write_text(removable, encoding="utf-8")
        iface = usb / "iface"
        iface.mkdir()
        (iface / "uevent").write_text(
            f"HID_ID={hid_id}\nHID_NAME={hid_name}\nHID_PHYS={hid_phys}\n",
            encoding="utf-8",
        )
        entry = sysfs / name
        entry.mkdir()
        (entry / "device").symlink_to(iface)
        (dev_dir / name).write_bytes(b"")

    _add_hidraw("hidraw0", "0003:00000414:00008105", "GIGABYTE USB-HID Keyboard", "usb-0000:67:00.0-4/input0", removable="fixed")
    _add_hidraw("hidraw1", "0003:00000414:00008105", "GIGABYTE USB-HID Keyboard", "usb-0000:67:00.0-4/input2", removable="fixed")
    _add_hidraw("hidraw7", "0003:0000046D:0000C084", "Logitech G203", "usb-0000:67:00.3-1.4.1/input0", removable="unknown")

    found = idle.list_internal_hidraw_nodes(
        internal_usb_ids={(0x0414, 0x8105)},
        sysfs_root=str(sysfs),
        dev_root=str(dev_dir),
    )
    assert found == [str(dev_dir / "hidraw0")]
