import json
import pytest

from gigamate.profiles import (
    DeviceProfile,
    AcpiConfig,
    load_builtin_profiles,
    load_user_profiles,
    all_profiles,
    resolve_profile,
    save_user_profile,
    validate_profile,
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
