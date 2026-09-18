"""Tests for the config persistence layer (config.py)."""

import json

import pytest

from gigamate import config as config_module


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the config module at a temporary file (no real user config touched)."""
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    old_file = tmp_path / "old-config.json"
    monkeypatch.setattr(config_module, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_module, "_OLD_CONFIG_FILE", old_file)
    return cfg_file


def test_acpi_profile_roundtrip(isolated_config):
    config_module.save({"acpi_profile": 3})
    loaded = config_module.load()
    assert loaded["acpi_profile"] == 3


def test_save_omits_acpi_profile_when_none(isolated_config):
    config_module.save({})
    data = json.loads(isolated_config.read_text())
    assert "acpi_profile" not in data
    assert config_module.load().get("acpi_profile") is None


def test_save_ignores_invalid_acpi_profile(isolated_config):
    config_module.save({"acpi_profile": 7})
    data = json.loads(isolated_config.read_text())
    assert "acpi_profile" not in data
    config_module.save({"acpi_profile": "gaming"})
    data = json.loads(isolated_config.read_text())
    assert "acpi_profile" not in data


def test_idle_defaults(isolated_config):
    loaded = config_module.load()
    # Hardware-mutating behaviours are opt-in by default.
    assert loaded["idle_off_enabled"] is False
    assert loaded["idle_timeout_sec"] == 60
    assert loaded["charge_limit_enabled"] is False
    assert loaded["startup_apply"] is False
    assert loaded["sync_system_power"] is False
    assert loaded["onboarding_complete"] is False


def test_idle_roundtrip(isolated_config):
    config_module.save({"idle_off_enabled": False, "idle_timeout_sec": 120})
    loaded = config_module.load()
    assert loaded["idle_off_enabled"] is False
    assert loaded["idle_timeout_sec"] == 120


def test_idle_timeout_clamped(isolated_config):
    config_module.save({"idle_timeout_sec": 10})
    assert config_module.load()["idle_timeout_sec"] == 10
    config_module.save({"idle_timeout_sec": 5})
    assert config_module.load()["idle_timeout_sec"] == 10
    config_module.save({"idle_timeout_sec": 99999})
    assert config_module.load()["idle_timeout_sec"] == 1800
    config_module.save({"idle_timeout_sec": "bogus"})
    assert config_module.load()["idle_timeout_sec"] == 60


def test_idle_off_preserves_timeout(isolated_config):
    """Off flips the boolean; the stored timeout survives re-enable."""
    config_module.save({"idle_off_enabled": False, "idle_timeout_sec": 30})
    loaded = config_module.load()
    assert loaded["idle_off_enabled"] is False
    assert loaded["idle_timeout_sec"] == 30


def test_sync_system_power_roundtrip(isolated_config):
    config_module.save({"sync_system_power": False})
    assert config_module.load()["sync_system_power"] is False
    config_module.save({"sync_system_power": True})
    assert config_module.load()["sync_system_power"] is True


def test_last_brightness_roundtrip(isolated_config):
    config_module.save({"last_brightness": 1})
    assert config_module.load()["last_brightness"] == 1
    config_module.save({"last_brightness": 2})
    assert config_module.load()["last_brightness"] == 2


def test_charge_limit_roundtrip(isolated_config):
    config_module.save({"charge_limit": 65, "charge_limit_enabled": True})
    loaded = config_module.load()
    assert loaded["charge_limit"] == 65
    assert loaded["charge_limit_enabled"] is True


def test_config_backup_recovery_on_corrupt_file(isolated_config, monkeypatch):
    """If config.json is corrupted (e.g. truncated on crash), recover from .bak file."""
    config_module.save({"colour": "red", "brightness": 1})
    bak_file = config_module.CONFIG_DIR / "config.json.bak"
    assert bak_file.exists()

    # Corrupt primary config
    isolated_config.write_text("{corrupt json...")
    recovered = config_module.load()
    assert recovered["colour"] == "red"
    assert recovered["brightness"] == 1


def test_all_default_config_keys_roundtrip(isolated_config):
    """Guard against save() silently dropping a known DEFAULT_CONFIG key."""
    config_module.save(dict(config_module.DEFAULT_CONFIG))
    loaded = config_module.load()
    for key, expected in config_module.DEFAULT_CONFIG.items():
        assert key in loaded, f"key '{key}' was dropped by save()/load()"
        assert loaded[key] == expected, f"key '{key}' changed: {loaded[key]!r} != {expected!r}"


def test_load_handles_non_dict_json(isolated_config):
    """A valid-JSON non-object file must not crash load()."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("[1, 2, 3]")
    loaded = config_module.load()
    assert loaded["colour"] == config_module.DEFAULT_CONFIG["colour"]


def test_invalid_charge_limit_falls_back_to_default(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text('{"charge_limit": 10}')
    assert config_module.load()["charge_limit"] == config_module.DEFAULT_CONFIG["charge_limit"]


def test_invalid_acpi_profile_is_dropped(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text('{"acpi_profile": 9}')
    assert config_module.load().get("acpi_profile") is None


def test_dgpu_max_clock_roundtrip(isolated_config):
    config_module.save({
        "dgpu_max_clock_enabled": True,
        "dgpu_max_clock_mhz": 2100,
    })
    loaded = config_module.load()
    assert loaded["dgpu_max_clock_enabled"] is True
    assert loaded["dgpu_max_clock_mhz"] == 2100


def test_invalid_dgpu_max_clock_falls_back_to_default(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text('{"dgpu_max_clock_mhz": 99999}')
    assert config_module.load()["dgpu_max_clock_mhz"] == \
        config_module.DEFAULT_CONFIG["dgpu_max_clock_mhz"]


def test_save_warns_on_unknown_keys(isolated_config, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="gigamate.config"):
        config_module.save({"colour": "red", "future_key": 1})
    assert any("future_key" in rec.message for rec in caplog.records)


def test_save_handles_bogus_brightness(isolated_config):
    """save() must not raise on non-numeric brightness values."""
    config_module.save({"brightness": "off"})
    loaded = config_module.load()
    assert loaded["brightness"] in (0, 1, 2)



def test_save_handles_null_profile_id(isolated_config):
    config_module.save({"profile_id": None})
    loaded = config_module.load()
    assert loaded["profile_id"] == config_module.DEFAULT_CONFIG["profile_id"]


def test_update_config_is_read_modify_write(isolated_config):
    config_module.save({"colour": "red", "idle_timeout_sec": 60})

    def mutate(cfg):
        cfg["colour"] = "blue"
        return cfg

    result = config_module.update_config(mutate)
    assert result["colour"] == "blue"
    reloaded = config_module.load()
    assert reloaded["colour"] == "blue"
    assert reloaded["idle_timeout_sec"] == 60


def test_save_preserves_unknown_on_disk_keys(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text('{"colour": "red", "future_key": 42}')
    config_module.save({"colour": "blue"})
    data = json.loads(isolated_config.read_text())
    assert data["colour"] == "blue"
    assert data["future_key"] == 42


def test_bak_recovery_when_primary_missing(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    (config_module.CONFIG_DIR / "config.json.bak").write_text('{"colour": "green"}')
    # Remove the primary so load() must fall back to the backup.
    if isolated_config.exists():
        isolated_config.unlink()
    assert config_module.load()["colour"] == "green"


def test_write_failure_does_not_raise(isolated_config, monkeypatch):
    monkeypatch.setattr(config_module, "_atomic_write_bytes", lambda *a, **k: False)
    cfg = config_module.update_config(lambda c: {**c, "colour": "red"})
    assert cfg["colour"] == "red"


def test_clean_install_no_config_file(tmp_path, monkeypatch):
    """On a clean install where no config file exists, load() succeeds without UnboundLocalError."""
    fresh_dir = tmp_path / "fresh_user" / "gigamate"
    monkeypatch.setattr(config_module, "CONFIG_DIR", fresh_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", fresh_dir / "config.json")
    monkeypatch.setattr(config_module, "_OLD_CONFIG_FILE", tmp_path / "nonexistent.json")
    monkeypatch.setattr(config_module, "_OLD_CONFIG_DIR", tmp_path / "nonexistent_dir")
    monkeypatch.setattr(config_module, "detect_device", lambda: None)

    cfg = config_module.load()
    assert cfg["colour"] == config_module.DEFAULT_CONFIG["colour"]
    assert cfg["brightness"] == config_module.DEFAULT_CONFIG["brightness"]
    assert cfg["profile_id"] == list(config_module.DEFAULT_CONFIG["profile_id"])
    assert cfg["onboarding_complete"] is False


def test_empty_dict_config(isolated_config):
    """Loading a config file containing empty dict '{}' falls back gracefully."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("{}")
    cfg = config_module.load()
    assert cfg["colour"] == config_module.DEFAULT_CONFIG["colour"]


def test_profile_id_coercion_formats():
    """Verify _coerce_profile_id handles hex strings, colon notation, and lists."""
    assert config_module._coerce_profile_id(["0x0414", "0x8105"]) == [1044, 33029]
    assert config_module._coerce_profile_id(["0414", "8105"]) == [1044, 33029]
    assert config_module._coerce_profile_id("0414:8105") == [1044, 33029]
    assert config_module._coerce_profile_id("0x0414:0x8105") == [1044, 33029]
    assert config_module._coerce_profile_id([1044, 33029]) == [1044, 33029]
    # Invalid fallback
    assert config_module._coerce_profile_id("invalid") == list(config_module.DEFAULT_CONFIG["profile_id"])
    assert config_module._coerce_profile_id([123]) == list(config_module.DEFAULT_CONFIG["profile_id"])


def test_brightness_migration_variants():
    """Verify _migrate_brightness handles textual, percentage, and float values."""
    assert config_module._migrate_brightness("off") == 0
    assert config_module._migrate_brightness("OFF") == 0
    assert config_module._migrate_brightness("0") == 0
    assert config_module._migrate_brightness("dim") == 1
    assert config_module._migrate_brightness("1") == 1
    assert config_module._migrate_brightness("full") == 2
    assert config_module._migrate_brightness("2") == 2
    assert config_module._migrate_brightness("50%") == 1
    assert config_module._migrate_brightness("10%") == 0
    assert config_module._migrate_brightness("100%") == 2
    assert config_module._migrate_brightness(0.0) == 0
    assert config_module._migrate_brightness(1.5) == 1
    assert config_module._migrate_brightness(True) == 1
    assert config_module._migrate_brightness(False) == 0


def test_legacy_profile_directory_migration(tmp_path, monkeypatch):
    """Custom profiles from legacy directory are migrated into new config directory."""
    old_dir = tmp_path / "old_config"
    old_profiles = old_dir / "profiles"
    old_profiles.mkdir(parents=True)
    (old_profiles / "my_custom.json").write_text('{"name": "custom"}')

    new_dir = tmp_path / "new_config"
    monkeypatch.setattr(config_module, "_OLD_CONFIG_DIR", old_dir)
    monkeypatch.setattr(config_module, "_OLD_CONFIG_FILE", old_dir / "config.json")
    monkeypatch.setattr(config_module, "CONFIG_DIR", new_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", new_dir / "config.json")

    config_module._migrate_old_config()
    target = new_dir / "profiles" / "my_custom.json"
    assert target.exists()
    assert json.loads(target.read_text()) == {"name": "custom"}


def test_non_string_colour_gracefully_handled(isolated_config):
    """Non-string colour types in config file do not crash load()."""
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    for bad_colour in (123, True, False, ["red"], {"col": "red"}, None):
        isolated_config.write_text(json.dumps({"colour": bad_colour}))
        cfg = config_module.load()
        assert cfg["colour"] == config_module.DEFAULT_CONFIG["colour"]




def test_hotkey_overrides_default_empty(isolated_config):
    assert config_module.load()["hotkey_overrides"] == {}


def test_hotkey_overrides_roundtrip(isolated_config):
    config_module.save({
        "hotkey_overrides": {
            "open_center": {"interface": 2, "report_id": 4, "payload": "000091", "key_name": "GigaMate"},
        }
    })
    loaded = config_module.load()
    assert loaded["hotkey_overrides"] == {
        "open_center": {"interface": 2, "report_id": 4, "payload": "000091", "key_name": "GigaMate"},
    }


def test_hotkey_overrides_sanitized(isolated_config):
    config_module.save({
        "hotkey_overrides": {
            "open_center": {"interface": 2, "report_id": 4, "payload": "00 00 91"},
            "self_destruct": {"interface": 2, "report_id": 4, "payload": "00"},
            "mode_switch": {"interface": 2},
        }
    })
    loaded = config_module.load()
    assert set(loaded["hotkey_overrides"]) == {"open_center"}
    assert loaded["hotkey_overrides"]["open_center"]["payload"] == "000091"
