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
    assert loaded["idle_off_enabled"] is True
    assert loaded["idle_timeout_sec"] == 60


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

