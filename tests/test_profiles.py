import json
import pytest
from unittest.mock import patch

from gigamate.profiles import (
    DeviceProfile,
    AcpiConfig,
    load_builtin_profiles,
    load_user_profiles,
    all_profiles,
    resolve_profile,
    save_user_profile,
    validate_profile,
    has_verified_profiles,
    BUILTIN_DATA_DIR,
    USER_PROFILES_DIR,
)


def test_builtin_profile_exists():
    profiles = load_builtin_profiles()
    assert (0x0414, 0x8105) in profiles


def test_builtin_profile_aero_x16():
    profile = load_builtin_profiles()[(0x0414, 0x8105)]
    assert profile.name == "Gigabyte Aero X16 (EG61VH)"
    assert profile.vid == 0x0414
    assert profile.pid == 0x8105
    assert profile.interfaces == [1, 3]
    assert profile.control_interface == 3
    assert len(profile.colour_map) == 11
    expected = {
        "red", "green", "yellow", "blue", "orange", "dark_yellow",
        "purple", "light_purple", "white", "light_blue", "blush_pink",
    }
    assert set(profile.colour_map.keys()) == expected


def test_resolve_known():
    profile = resolve_profile(0x0414, 0x8105)
    assert profile is not None
    assert profile.name == "Gigabyte Aero X16 (EG61VH)"


def test_resolve_unknown():
    profile = resolve_profile(0x0414, 0x7A3C)
    assert profile is None


def test_user_profile_overrides_builtin(tmp_path, monkeypatch):
    user_dir = tmp_path / "profiles"
    user_dir.mkdir(parents=True)
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", user_dir)
    override = {
        "name": "Custom Override",
        "vid": "0x0414",
        "pid": "0x8105",
        "interfaces": [1, 3],
        "control_interface": 3,
        "colour_map": {
            "x": {"0": [1, 0], "1": [1, 50], "2": [1, 100]},
        },
    }
    (user_dir / "0414_8105.json").write_text(json.dumps(override))
    profile = resolve_profile(0x0414, 0x8105)
    assert profile is not None
    assert profile.name == "Custom Override"
    assert set(profile.colour_map.keys()) == {"x"}


def test_json_roundtrip():
    original = DeviceProfile(
        vid=0x0414,
        pid=0x8105,
        name="Test Model",
        interfaces=[1, 3],
        control_interface=3,
        colour_map={
            "red": {0: (1, 0), 1: (1, 25), 2: (1, 100)},
            "green": {0: (2, 0), 1: (2, 50), 2: (2, 100)},
        },
    )
    restored = DeviceProfile.from_dict(original.to_dict())
    assert restored.vid == original.vid
    assert restored.pid == original.pid
    assert restored.name == original.name
    assert restored.interfaces == original.interfaces
    assert restored.control_interface == original.control_interface
    assert restored.colour_map == original.colour_map


def test_save_user_profile(tmp_path, monkeypatch):
    user_dir = tmp_path / "profiles"
    user_dir.mkdir(parents=True)
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", user_dir)
    profile = DeviceProfile(
        vid=0x0414, pid=0x7A3C, name="Test",
        colour_map={"x": {0: (1, 0), 1: (1, 50), 2: (1, 100)}},
    )
    path = save_user_profile(profile)
    assert path.exists()
    assert path.name == "0414_7A3C.json"
    data = json.loads(path.read_text())
    assert data["name"] == "Test"
    assert data["vid"] == "0x0414"
    assert data["pid"] == "0x7A3C"


def test_colour_properties():
    profile = DeviceProfile(
        vid=0x0414, pid=0x8105, name="T",
        colour_map={"a": {0: (1, 0), 1: (1, 25), 2: (1, 100)}},
    )
    assert profile.colour_names == ["a"]
    assert profile.colour_byte("a", 2) == (1, 100)
    assert profile.full_map == {"a": 1}
    assert profile.reverse_map == {1: "a"}
    assert profile.id == (0x0414, 0x8105)


def test_profile_from_dict_hex():
    d = {
        "name": "HexTest",
        "vid": "0x0414",
        "pid": "0x8105",
        "interfaces": [1, 3],
        "control_interface": 3,
        "colour_map": {},
    }
    p = DeviceProfile.from_dict(d)
    assert p.vid == 0x0414
    assert p.pid == 0x8105


def test_profile_from_dict_int():
    d = {
        "name": "IntTest",
        "vid": 1044,
        "pid": 33029,
        "interfaces": [1, 3],
        "control_interface": 3,
        "colour_map": {},
    }
    p = DeviceProfile.from_dict(d)
    assert p.vid == 0x0414
    assert p.pid == 0x8105


def test_user_profiles_empty_by_default():
    profiles = load_user_profiles()
    assert isinstance(profiles, dict)


def test_all_profiles_includes_builtin():
    profiles = all_profiles()
    assert (0x0414, 0x8105) in profiles


def test_no_user_profiles_dir(tmp_path, monkeypatch):
    non_existent = tmp_path / "nope"
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", non_existent)
    assert load_user_profiles() == {}


class TestAcpiConfig:
    def test_defaults(self):
        cfg = AcpiConfig()
        assert cfg.has_fan_control is False
        assert cfg.has_temperature is False
        assert cfg.has_power_profiles is False
        assert cfg.fan_count == 0
        assert cfg.fan_labels == []
        assert cfg.backend == "module"

    def test_roundtrip(self):
        cfg = AcpiConfig(
            has_fan_control=True,
            has_temperature=True,
            has_power_profiles=True,
            fan_count=2,
            fan_labels=["CPU Fan", "GPU Fan"],
            sensor_labels={"temp_cpu": "CPU"},
            profiles={"0": {"name": "Quiet"}, "3": {"name": "Gaming"}},
            backend="module",
        )
        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="Test", acpi=cfg,
        )
        d = profile.to_dict()
        assert "acpi" in d
        assert d["acpi"]["has_fan_control"] is True
        assert d["acpi"]["fan_count"] == 2
        assert d["acpi"]["profiles"]["0"]["name"] == "Quiet"

        # Roundtrip
        restored = DeviceProfile.from_dict(d)
        assert restored.has_acpi is True
        assert restored.acpi is not None
        assert restored.acpi.has_fan_control is True
        assert restored.acpi.fan_labels == ["CPU Fan", "GPU Fan"]
        assert restored.acpi.profiles["0"]["name"] == "Quiet"

    def test_missing_acpi_is_backward_compat(self):
        d = {
            "name": "Old", "vid": "0x0414", "pid": "0x8105",
            "interfaces": [1, 3], "control_interface": 3,
            "colour_map": {},
        }
        profile = DeviceProfile.from_dict(d)
        assert profile.has_acpi is False
        assert profile.acpi is None


class TestValidateProfile:
    def test_valid_full_profile(self):
        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="Valid",
            interfaces=[1, 3], control_interface=3,
            colour_map={"red": {0: (1, 0), 1: (1, 25), 2: (1, 100)}},
            acpi=AcpiConfig(
                has_power_profiles=True,
                profiles={"0": {"name": "Quiet"}, "3": {"name": "Gaming"}},
            ),
        )
        errors = validate_profile(profile)
        assert errors == []

    def test_invalid_profile_id(self):
        profile = DeviceProfile(
            vid=0xFFFF, pid=0xFFFF, name="Bad",
            acpi=AcpiConfig(
                has_power_profiles=True,
                profiles={"5": {"name": "Turbo"}},
            ),
        )
        errors = validate_profile(profile)
        # Should warn about profile ID 5 out of range
        profile_ids = [e for e in errors if "profile ID" in e]
        assert len(profile_ids) >= 1

    def test_invalid_backend(self):
        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="Test",
            acpi=AcpiConfig(backend="nonexistent"),
        )
        errors = validate_profile(profile)
        backends = [e for e in errors if "backend" in e]
        assert len(backends) >= 1


class TestDetectDevice:
    def test_detect_device_with_no_langid_value_error(self, monkeypatch):
        """Devices raising ValueError on string descriptors (e.g. no langid) shouldn't crash detection."""
        from gigamate.profiles import detect_device

        class BrokenDescriptorDevice:
            idVendor = 0x1234
            idProduct = 0x5678

            @property
            def manufacturer(self):
                raise ValueError("The device has no langid (permission issue, no string descriptors supported or device error)")

        class ValidGigabyteDevice:
            idVendor = 0x0414
            idProduct = 0x8105
            manufacturer = "GIGABYTE"

        monkeypatch.setattr("usb.core.find", lambda find_all=True: [BrokenDescriptorDevice(), ValidGigabyteDevice()])
        detected = detect_device()
        assert detected == (0x0414, 0x8105)

    def test_detect_device_no_match_with_broken_descriptor(self, monkeypatch):
        """When no Gigabyte device exists and a device raises ValueError, returns None without error."""
        from gigamate.profiles import detect_device

        class BrokenDescriptorDevice:
            idVendor = 0x1234
            idProduct = 0x5678

            @property
            def manufacturer(self):
                raise ValueError("The device has no langid")

        monkeypatch.setattr("usb.core.find", lambda find_all=True: [BrokenDescriptorDevice()])
        detected = detect_device()
        assert detected is None


class TestGetDmiProductName:
    def test_dmi_product_name_valid(self, tmp_path, monkeypatch):
        from gigamate import profiles
        from pathlib import Path

        dmi_dir = tmp_path / "dmi" / "id"
        dmi_dir.mkdir(parents=True)
        (dmi_dir / "product_name").write_text("GIGABYTE Gaming A16\n")

        # Monkeypatch Path in profiles or monkeypatch the path directly
        orig_get_dmi = profiles.get_dmi_product_name
        monkeypatch.setattr(
            profiles,
            "get_dmi_product_name",
            lambda: (dmi_dir / "product_name").read_text().strip(),
        )
        assert profiles.get_dmi_product_name() == "GIGABYTE Gaming A16"

    def test_dmi_product_name_empty_or_generic(self, tmp_path, monkeypatch):
        from gigamate.profiles import get_dmi_product_name

        # Test real function with monkeypatched dmi path
        fake_dmi = tmp_path / "sys" / "class" / "dmi" / "id"
        fake_dmi.mkdir(parents=True)
        (fake_dmi / "product_name").write_text("Default string\n")

        import gigamate.profiles as prof_mod
        orig_path = prof_mod.Path

        def mock_path(p):
            if str(p) == "/sys/class/dmi/id":
                return fake_dmi
            return orig_path(p)

        monkeypatch.setattr(prof_mod, "Path", mock_path)
        assert get_dmi_product_name() is None

    def test_dmi_product_name_real_path(self, tmp_path, monkeypatch):
        from gigamate.profiles import get_dmi_product_name
        import gigamate.profiles as prof_mod

        fake_dmi = tmp_path / "sys" / "class" / "dmi" / "id"
        fake_dmi.mkdir(parents=True)
        (fake_dmi / "product_name").write_text("GIGABYTE AERO X16\n")

        orig_path = prof_mod.Path

        def mock_path(p):
            if str(p) == "/sys/class/dmi/id":
                return fake_dmi
            return orig_path(p)

        monkeypatch.setattr(prof_mod, "Path", mock_path)
        assert get_dmi_product_name() == "GIGABYTE AERO X16"

    def test_cmd_status_dmi_fallback(self, monkeypatch, capsys):
        import argparse
        from gigamate import cli

        monkeypatch.setattr(cli, "detect_device", lambda: None)
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        monkeypatch.setattr(cli, "get_dmi_product_name", lambda: "GIGABYTE Gaming A16")

        args = argparse.Namespace(vid=None, pid=None)
        cli.cmd_status(args)
        captured = capsys.readouterr().out
        assert "Model: GIGABYTE Gaming A16" in captured
        assert "Keyboard: Not detected (no USB RGB)" in captured

    def test_cmd_contribute_acpi_only(self, monkeypatch, capsys):
        import argparse
        from gigamate import cli
        from gigamate.acpi import AcpiController

        monkeypatch.setattr(cli, "detect_device", lambda: None)
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        monkeypatch.setattr(cli, "get_dmi_product_name", lambda: "GIGABYTE Gaming A16")
        monkeypatch.setattr(AcpiController, "available", property(lambda self: True))

        args = argparse.Namespace(vid=None, pid=None)
        cli.cmd_profile_contribute(args)
        captured = capsys.readouterr().out
        assert "Model: GIGABYTE Gaming A16" in captured
        assert "power-profile support is unverified" in captured
        assert "gigamate calibrate acpi" in captured
        assert "fully supported out-of-the-box" not in captured





def test_malformed_user_profile_is_skipped(tmp_path, monkeypatch):
    from gigamate.profiles import load_user_profiles
    user_dir = tmp_path / "profiles"
    user_dir.mkdir()
    (user_dir / "bad.json").write_text('{"vid": null, "pid": "0x8105"}')
    (user_dir / "good.json").write_text('{"vid": "0x0414", "pid": "0x8105", "name": "T"}')
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", user_dir)
    profs = load_user_profiles()
    assert (0x0414, 0x8105) in profs


def test_save_user_profile_rejects_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr("gigamate.profiles.USER_PROFILES_DIR", tmp_path / "profiles")
    bad = DeviceProfile(vid=0x0414, pid=0x8105, name="", colour_map={})
    with pytest.raises(ValueError):
        save_user_profile(bad)


def test_shared_vid_requires_gigabyte_evidence(monkeypatch):
    from gigamate import profiles as profiles_mod

    class FakeDev:
        idVendor = 0x1044
        idProduct = 0x1234
        manufacturer = "Some Other Brand"
        bDeviceClass = 0

    monkeypatch.setattr(profiles_mod.usb.core, "find", lambda find_all=False: [FakeDev()])
    assert profiles_mod.detect_device() is None


def test_shared_vid_accepted_with_gigabyte_manufacturer(monkeypatch):
    from gigamate import profiles as profiles_mod

    class FakeDev:
        idVendor = 0x1044
        idProduct = 0x1234
        manufacturer = "GIGABYTE"
        bDeviceClass = 0

    monkeypatch.setattr(profiles_mod.usb.core, "find", lambda find_all=False: [FakeDev()])
    assert profiles_mod.detect_device() == (0x1044, 0x1234)


class TestHasVerifiedProfiles:
    def _profile(self, **kw):
        return DeviceProfile(vid=0x0414, pid=0x8105, name="Test", **kw)

    def test_none_is_unverified(self):
        assert has_verified_profiles(None) is False

    def test_no_acpi_is_unverified(self):
        assert has_verified_profiles(self._profile()) is False

    def test_acpi_without_profiles_is_unverified(self):
        profile = self._profile(acpi=AcpiConfig(has_power_profiles=True))
        assert has_verified_profiles(profile) is False

    def test_acpi_but_not_declared_is_unverified(self):
        profile = self._profile(acpi=AcpiConfig(profiles={"0": {"name": "Quiet"}}))
        assert has_verified_profiles(profile) is False

    def test_declared_profiles_is_verified(self):
        profile = self._profile(acpi=AcpiConfig(
            has_power_profiles=True, profiles={"0": {"name": "Quiet"}},
        ))
        assert has_verified_profiles(profile) is True

    def test_builtin_aero_x16_is_verified(self):
        assert has_verified_profiles(load_builtin_profiles()[(0x0414, 0x8105)]) is True


class TestGetDmiProductFamily:
    def _patch_dmi(self, tmp_path, monkeypatch):
        import gigamate.profiles as p

        dmi = tmp_path / "dmi"
        dmi.mkdir()

        def fake_path(arg):
            if str(arg).startswith("/sys/class/dmi/id"):
                return dmi
            return p.Path(arg)

        monkeypatch.setattr(p, "Path", fake_path)
        return dmi

    def test_reads_family(self, tmp_path, monkeypatch):
        dmi = self._patch_dmi(tmp_path, monkeypatch)
        (dmi / "product_family").write_text("GIGABYTE GAMING\n")
        import gigamate.profiles as p

        assert p.get_dmi_product_family() == "GIGABYTE GAMING"

    def test_missing_family_is_none(self, tmp_path, monkeypatch):
        self._patch_dmi(tmp_path, monkeypatch)
        import gigamate.profiles as p

        assert p.get_dmi_product_family() is None


class TestValidateAcpiOnlyProfile:
    def test_empty_interfaces_allowed_without_rgb(self):
        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="ACPI Only",
            interfaces=[], control_interface=0,
            acpi=AcpiConfig(
                has_fan_control=True, has_temperature=True, has_power_profiles=True,
                fan_count=2,
                profiles={"0": {"name": "Quiet"}, "1": {"name": "Balanced"}},
            ),
        )
        assert validate_profile(profile) == []


class TestValidateHotkeys:
    def _profile(self, hotkeys):
        return DeviceProfile(
            vid=0x0414, pid=0x8105, name="Hotkeys",
            interfaces=[1, 3], control_interface=3,
            colour_map={"red": {0: (1, 0), 1: (1, 25), 2: (1, 100)}},
            hotkeys=hotkeys,
        )

    def test_valid_hotkeys(self):
        profile = self._profile({
            "mode_switch": {"interface": 2, "report_id": 4, "payload": "000084", "key_name": "Mode"},
            "open_center": {"interface": 2, "report_id": 4, "payload": "000091"},
        })
        assert validate_profile(profile) == []

    def test_unknown_action(self):
        profile = self._profile({
            "self_destruct": {"interface": 2, "report_id": 4, "payload": "00"},
        })
        errors = validate_profile(profile)
        assert any("Unknown hotkey action" in e for e in errors)

    def test_invalid_spec(self):
        profile = self._profile({
            "mode_switch": {"interface": 2, "report_id": 4, "payload": "zz"},
        })
        errors = validate_profile(profile)
        assert any("Invalid hotkey" in e for e in errors)

    def test_invalid_key_name(self):
        profile = self._profile({
            "mode_switch": {"interface": 2, "report_id": 4, "payload": "00", "key_name": ""},
        })
        errors = validate_profile(profile)
        assert any("key_name" in e for e in errors)


class TestProfileCliGating:
    """Power-profile CLI commands must refuse to act on unverified models."""

    @staticmethod
    def _args():
        import argparse

        return argparse.Namespace(vid=None, pid=None)

    def test_set_refuses_unverified(self, monkeypatch, capsys):
        from gigamate import cli

        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_set(self._args(), "performance")
        captured = capsys.readouterr().out
        assert "not enabled for this model" in captured

    def test_cycle_refuses_unverified(self, monkeypatch, capsys):
        from gigamate import cli

        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_cycle(self._args())
        captured = capsys.readouterr().out
        assert "not enabled for this model" in captured

    def test_show_refuses_unverified(self, monkeypatch, capsys):
        from gigamate import cli

        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_show(self._args())
        captured = capsys.readouterr().out
        assert "not enabled for this model" in captured

    def test_set_rejects_profile_out_of_model_set(self, monkeypatch, capsys):
        from gigamate import cli

        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="Test",
            acpi=AcpiConfig(has_power_profiles=True, profiles={
                "0": {"name": "Quiet"}, "1": {"name": "Balanced"},
            }),
        )
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: profile)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_set(self._args(), "3")
        captured = capsys.readouterr().out
        assert "not available on this model" in captured

    def test_set_allows_in_model_profile(self, monkeypatch, capsys):
        from gigamate import cli

        profile = DeviceProfile(
            vid=0x0414, pid=0x8105, name="Test",
            acpi=AcpiConfig(has_power_profiles=True, profiles={
                "0": {"name": "Quiet"}, "1": {"name": "Balanced"},
            }),
        )
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: profile)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls, \
             patch("gigamate.cli._record_profile_and_sync") as mock_record:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.available = True
            mock_ctrl.set_profile.return_value = True
            cli.cmd_profile_set(self._args(), "balanced")
        captured = capsys.readouterr().out
        assert "Power profile set to: Balanced  (1)" in captured
        mock_record.assert_called_once_with(1)


class TestCalibrateAcpi:
    def _caps(self, **kw):
        from gigamate.acpi import AcpiCapabilities

        defaults = dict(
            has_temperature=True, has_fan_rpm=True, has_fan_duty=True,
            has_power_profiles=True, fan_count=2, backend="module",
        )
        defaults.update(kw)
        return AcpiCapabilities(**defaults)

    def test_requires_detected_keyboard(self, monkeypatch, capsys):
        from gigamate import cli

        monkeypatch.setattr(cli, "detect_device", lambda: None)
        cli.cmd_calibrate_acpi(self._args())
        captured = capsys.readouterr().out
        assert "nothing can be saved" in captured

    def test_profiles_off_without_consent(self, monkeypatch, capsys):
        from unittest.mock import MagicMock

        from gigamate import cli

        monkeypatch.setattr(cli, "detect_device", lambda: (0x0414, 0x9999))
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        monkeypatch.setattr(cli, "get_dmi_product_name", lambda: "Test Laptop")
        monkeypatch.setattr(cli, "probe_acpi_capabilities", lambda: self._caps())
        monkeypatch.setattr(cli, "get_dmi_product_family", lambda: "GIGABYTE GAMING")
        mock_save = MagicMock(return_value=object())
        monkeypatch.setattr(cli, "save_user_profile", mock_save)
        monkeypatch.setattr("builtins.input", lambda *a, **k: "n")  # decline

        cli.cmd_calibrate_acpi(self._args())
        captured = capsys.readouterr().out
        assert "stays disabled for this model until verified" in captured
        saved = mock_save.call_args[0][0]
        assert saved.acpi.has_power_profiles is False
        assert saved.acpi.profiles == {}
        assert saved.acpi.has_fan_control is True
        assert saved.acpi.has_temperature is True

    def test_profiles_enabled_with_consent(self, monkeypatch, capsys):
        from unittest.mock import MagicMock

        from gigamate import cli

        monkeypatch.setattr(cli, "detect_device", lambda: (0x0414, 0x9999))
        monkeypatch.setattr(cli, "resolve_profile", lambda vid=None, pid=None: None)
        monkeypatch.setattr(cli, "get_dmi_product_name", lambda: "Test Laptop")
        monkeypatch.setattr(cli, "probe_acpi_capabilities", lambda: self._caps())
        monkeypatch.setattr(cli, "get_dmi_product_family", lambda: "GIGABYTE GAMING")
        mock_save = MagicMock(return_value=object())
        monkeypatch.setattr(cli, "save_user_profile", mock_save)
        monkeypatch.setattr("builtins.input", lambda *a, **k: "y")  # accept

        cli.cmd_calibrate_acpi(self._args())
        captured = capsys.readouterr().out
        assert "now enabled for this model (experimental)" in captured
        saved = mock_save.call_args[0][0]
        assert saved.acpi.has_power_profiles is True
        assert saved.acpi.profiles == {
            "0": {"name": "eco", "desc": ""},
            "1": {"name": "balanced", "desc": ""},
            "2": {"name": "boost", "desc": ""},
        }

    @staticmethod
    def _args():
        import argparse

        return argparse.Namespace(vid=None, pid=None)
