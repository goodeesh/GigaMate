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
        pytest.importorskip("gi")
        mgr = SystemPowerManager()
        with patch("gigamate.system_power._HAS_GIO", True), \
             patch("gi.repository.Gio.bus_get_sync") as mock_bus, \
             patch("gi.repository.Gio.DBusProxy.new_sync") as mock_proxy:
            
            proxy_instance = MagicMock()
            proxy_instance.call_sync.return_value = ["balanced"]
            mock_proxy.return_value = proxy_instance

            assert mgr.is_available is True

    def test_set_system_profile_dbus_success(self):
        pytest.importorskip("gi")
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

    def test_sync_system_power_delegates_to_gpu(self):
        with patch("gigamate.system_power._manager.sync_from_fan_profile", return_value=True), \
             patch("gigamate.gpu.sync_gpu_power") as mock_gpu_sync:
            res = sync_system_power(3)
            assert res is True
            mock_gpu_sync.assert_called_once_with(3)


def test_detect_power_managers_with_tlp_and_powerdevil(tmp_path, monkeypatch):
    """detect_power_managers detects active systemd services and PowerDevil AC/Battery rules."""
    import subprocess
    import gigamate.system_power as sp

    assert sp.SYSTEM_TO_GIGAMATE_PROFILE == {"power-saver": 0, "balanced": 1, "performance": 2}

    config_dir = tmp_path / ".config"
    config_dir.mkdir(parents=True)
    (config_dir / "powerdevilrc").write_text("[AC]\nPowerProfile=performance\n", encoding="utf-8")

    monkeypatch.setattr(sp.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)

    def fake_run(cmd, **kwargs):
        svc = cmd[-1]
        rc = 0 if svc == "tlp.service" else 3
        return subprocess.CompletedProcess(cmd, rc)

    monkeypatch.setattr(sp.subprocess, "run", fake_run)

    res = sp.detect_power_managers()
    assert "tlp.service" in res["managers"]
    assert "powerdevil" in res["managers"]
    assert res["has_external_ac_switching"] is True
    assert res["recommended_mode"] == "follow_system"


def test_follow_system_ignores_stale_tlp_pd_during_settle_window(monkeypatch):
    """After GigaMate switches profile, follow_system must not revert on stale tlp-pd Get()."""
    from unittest.mock import MagicMock, patch
    from gigamate import daemon as daemon_mod

    d = daemon_mod.GigaMateDaemon()
    d._config = {"ac_profile_mode": "follow_system", "sync_system_power": True}
    d._current_acpi_profile = 1
    d._last_ac_online = True
    d._last_seen_system_profile = "balanced"
    d._profile_sync_until = 0.0
    d._acpi_controller = MagicMock(available=True)

    applied = []

    def fake_apply(pid, cfg=None, sync_system=True, acpi=None):
        applied.append((pid, sync_system))
        return True

    with patch.object(daemon_mod.dgpu_gate, "high_power_profiles_allowed", return_value=True), \
         patch.object(daemon_mod.hardware, "apply_profile", side_effect=fake_apply), \
         patch.object(daemon_mod, "load_config", return_value=dict(d._config)), \
         patch.object(daemon_mod.SystemPowerManager, "get_active_profile", return_value="balanced"):
        # User selects Performance (2) while tlp-pd still reports stale "balanced"
        assert d.set_profile(2) is True
        assert d._current_acpi_profile == 2
        assert applied == [(2, True)]

        # A timer tick arrives immediately while tlp-pd is still applying detached TLP
        d._run_profile_automation(True)
        # Must NOT revert back to Balanced (1)!
        assert d._current_acpi_profile == 2
        assert applied == [(2, True)]


def test_read_tlp_effective_config_lexical_override(tmp_path, monkeypatch):
    """Later drop-in 99-gigamate.conf setting TLP_AUTO_SWITCH=0 overrides tlp.conf and clears conflict."""
    import subprocess
    import gigamate.system_power as sp

    tlp_conf = tmp_path / "tlp.conf"
    tlp_dir = tmp_path / "tlp.d"
    tlp_dir.mkdir()

    tlp_conf.write_text('TLP_AUTO_SWITCH=2\nPLATFORM_PROFILE_ON_AC="performance"\n', encoding="utf-8")
    (tlp_dir / "10-custom.conf").write_text("PLATFORM_PROFILE_ON_BAT=balanced\n", encoding="utf-8")

    cfg = sp._read_tlp_effective_config(tlp_conf=tlp_conf, tlp_dir=tlp_dir)
    assert cfg["TLP_AUTO_SWITCH"] == "2"
    assert cfg["PLATFORM_PROFILE_ON_AC"] == "performance"
    assert cfg["PLATFORM_PROFILE_ON_BAT"] == "balanced"

    (tlp_dir / "99-gigamate.conf").write_text("# Managed by GigaMate\nTLP_AUTO_SWITCH=0\n", encoding="utf-8")
    cfg2 = sp._read_tlp_effective_config(tlp_conf=tlp_conf, tlp_dir=tlp_dir)
    assert cfg2["TLP_AUTO_SWITCH"] == "0"

    monkeypatch.setattr(sp, "_read_tlp_effective_config", lambda: cfg2)
    monkeypatch.setattr(sp, "_detect_powerdevil_ac_profiles", lambda: {})
    monkeypatch.setattr(sp, "TLP_HANDOFF_PATH", tlp_dir / "99-gigamate.conf")
    monkeypatch.setattr(sp, "POWER_HANDOFF_BACKUP", tmp_path / "backup.json")
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(
        sp.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0 if cmd[-1] == "tlp.service" else 3),
    )

    info = sp.detect_power_managers()
    assert info["conflicts"] == []
    assert info["has_external_ac_switching"] is False
    assert "paused by GigaMate" in info["tlp_summary"]


def test_powerdevil_deactivate_and_restore_round_trip(tmp_path, monkeypatch):
    """PowerDevil deactivate/restore removes and restores only AC/Battery PowerProfile= lines."""
    import gigamate.system_power as sp

    cfg_dir = tmp_path / ".config"
    cfg_dir.mkdir(parents=True)
    rc_path = cfg_dir / "powerdevilrc"
    original = (
        "[AC][Performance]\n"
        "PowerProfile=performance\n\n"
        "[AC][SuspendAndShutdown]\n"
        "AutoSuspendAction=1\n\n"
        "[Battery][Performance]\n"
        "PowerProfile=power-saver\n"
    )
    rc_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(sp.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sp, "POWER_HANDOFF_BACKUP", cfg_dir / "gigamate/power_handoff_backup.json")
    monkeypatch.setattr(sp, "TLP_HANDOFF_PATH", tmp_path / "nonexistent-99-gigamate.conf")
    monkeypatch.setattr(sp, "_reload_powerdevil", lambda: None)

    ok, msg = sp.deactivate_ac_conflicts([
        {"id": "powerdevil", "name": "KDE PowerDevil", "scope": "user", "detail": ""}
    ])
    assert ok is True
    assert "KDE PowerDevil" in msg
    assert sp.POWER_HANDOFF_BACKUP.exists()

    after_deact = rc_path.read_text(encoding="utf-8")
    assert "PowerProfile=" not in after_deact
    assert "[AC][SuspendAndShutdown]\nAutoSuspendAction=1\n" in after_deact

    ok_r, msg_r = sp.restore_ac_conflicts()
    assert ok_r is True
    assert "KDE PowerDevil" in msg_r
    assert not sp.POWER_HANDOFF_BACKUP.exists()

    restored_profiles = sp._detect_powerdevil_ac_profiles(rc_path)
    assert restored_profiles == {
        "[AC][Performance]": "performance",
        "[Battery][Performance]": "power-saver",
    }
    assert "[AC][SuspendAndShutdown]\nAutoSuspendAction=1\n" in rc_path.read_text(encoding="utf-8")


def test_tlp_deactivate_and_restore_pkexec(tmp_path, monkeypatch):
    """TLP deactivate/restore invokes pkexec with batched shell script."""
    import subprocess
    import gigamate.system_power as sp

    handoff = tmp_path / "99-gigamate.conf"
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(sp, "TLP_HANDOFF_PATH", handoff)
    monkeypatch.setattr(sp, "POWER_HANDOFF_BACKUP", backup)
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/pkexec" if name == "pkexec" else None)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sp.subprocess, "run", fake_run)

    ok, _ = sp.deactivate_ac_conflicts([
        {"id": "tlp", "name": "TLP", "scope": "system", "detail": ""}
    ])
    assert ok is True
    assert len(calls) == 1
    assert calls[0][0] == "/usr/bin/pkexec"
    assert "TLP_AUTO_SWITCH=0" in calls[0][3]

    handoff.write_text("TLP_AUTO_SWITCH=0\n", encoding="utf-8")
    ok_r, _ = sp.restore_ac_conflicts()
    assert ok_r is True
    assert len(calls) == 2
    assert f"rm -f {handoff}" in calls[1][3]


def test_tray_dgpu_gate_and_one_shot_drift_notification():
    """Daemon _run_profile_automation blocks high-power profile when dGPU gated and notifies once on drift."""
    from unittest.mock import patch
    from gigamate import daemon as daemon_mod

    d = daemon_mod.GigaMateDaemon()
    d._config = {
        "ac_profile_mode": "switch",
        "ac_profile_id": 2,
        "battery_profile_id": 0,
    }
    d._current_acpi_profile = 1
    d._last_ac_online = False

    conflict_info = {
        "conflicts": [{"id": "tlp", "name": "TLP", "scope": "system", "detail": "TLP_AUTO_SWITCH=2"}],
        "has_external_ac_switching": True,
    }

    with patch.object(daemon_mod, "load_config", return_value=dict(d._config)), \
         patch.object(daemon_mod, "power_config_mtime_token", return_value=(("a", 1),)), \
         patch.object(daemon_mod, "detect_power_managers", return_value=conflict_info), \
         patch.object(daemon_mod.dgpu_gate, "high_power_profiles_allowed", return_value=False), \
         patch.object(daemon_mod.hardware, "apply_profile") as mock_apply, \
         patch.object(daemon_mod.shutil, "which", return_value="/usr/bin/notify-send"), \
         patch.object(daemon_mod.subprocess, "Popen") as mock_popen:
        # First tick: AC plugged in -> target=2 blocked by dgpu_gate; notification sent once
        d._run_profile_automation(True)
        mock_apply.assert_not_called()
        assert mock_popen.call_count == 1

        # Second tick with same token: no duplicate notification
        d._run_profile_automation(True)
        assert mock_popen.call_count == 1
