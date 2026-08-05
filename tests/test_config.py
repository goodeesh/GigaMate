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
