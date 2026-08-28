"""Tests for system power profile integration (system_power.py)."""

import pytest
from unittest.mock import patch, MagicMock

from gigamate.system_power import (
    SystemPowerManager,
    sync_system_power,
    is_system_power_available,
    DEFAULT_PROFILE_MAP,
)


class TestSystemPowerManager:
    def test_default_mappings(self):
        mgr = SystemPowerManager()
        assert mgr._profile_map[0] == "power-saver"
        assert mgr._profile_map[1] == "balanced"
        assert mgr._profile_map[2] == "performance"
        assert mgr._profile_map[3] == "performance"

    def test_custom_mappings(self):
        custom = {0: "power-saver", 1: "balanced", 2: "balanced", 3: "performance"}
        mgr = SystemPowerManager(profile_map=custom)
        assert mgr._profile_map[2] == "balanced"

    def test_is_available_dbus_success(self):
        mgr = SystemPowerManager()
        with patch("gigamate.system_power._HAS_GIO", True), \
             patch("gi.repository.Gio.bus_get_sync") as mock_bus, \
             patch("gi.repository.Gio.DBusProxy.new_sync") as mock_proxy:
            
            proxy_instance = MagicMock()
            proxy_instance.call_sync.return_value = ["balanced"]
            mock_proxy.return_value = proxy_instance

            assert mgr.is_available is True

    def test_set_system_profile_dbus_success(self):
        mgr = SystemPowerManager()
        with patch("gigamate.system_power._HAS_GIO", True), \
             patch("gi.repository.Gio.bus_get_sync") as mock_bus, \
             patch("gi.repository.Gio.DBusProxy.new_sync") as mock_proxy:
            
            proxy_instance = MagicMock()
            proxy_instance.call_sync.return_value = None
            mock_proxy.return_value = proxy_instance

            res = mgr.set_system_profile("power-saver")
            assert res is True
            proxy_instance.call_sync.assert_called_once()

    def test_sync_from_fan_profile(self):
        mgr = SystemPowerManager()
        with patch.object(mgr, "set_system_profile", return_value=True) as mock_set:
            assert mgr.sync_from_fan_profile(0) is True
            mock_set.assert_called_with("power-saver")

            assert mgr.sync_from_fan_profile(1) is True
            mock_set.assert_called_with("balanced")

            assert mgr.sync_from_fan_profile(2) is True
            mock_set.assert_called_with("performance")

            assert mgr.sync_from_fan_profile(3) is True
            mock_set.assert_called_with("performance")

    def test_sync_from_invalid_fan_profile(self):
        mgr = SystemPowerManager()
        with patch.object(mgr, "set_system_profile") as mock_set:
            assert mgr.sync_from_fan_profile(99) is False
            mock_set.assert_not_called()
