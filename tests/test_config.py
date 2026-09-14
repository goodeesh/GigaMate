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
