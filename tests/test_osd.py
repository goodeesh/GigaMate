"""Tests for OSD overlay module (osd.py)."""

import pytest
from unittest.mock import patch, MagicMock

from gigamate.osd import show_profile_osd, _show_kde_osd, _show_freedesktop_notification


class TestOSD:
    def test_show_kde_osd_no_gio(self):
        with patch("gigamate.osd._HAS_GIO", False):
            assert _show_kde_osd("Gaming") is False

    def test_show_freedesktop_no_gio(self):
        with patch("gigamate.osd._HAS_GIO", False):
            assert _show_freedesktop_notification("Gaming") is False

    def test_show_profile_osd_on_kde(self):
        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}), \
             patch("gigamate.osd._show_kde_osd", return_value=True) as mock_kde, \
             patch("gigamate.osd._show_freedesktop_notification", return_value=False) as mock_free:
            res = show_profile_osd("Gaming", "Max GPU")
            assert res is True
            mock_kde.assert_called_once_with("Gaming", "Max GPU", "preferences-system-power-management")
            mock_free.assert_not_called()

    def test_show_profile_osd_on_gnome(self):
        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "GNOME"}), \
             patch("gigamate.osd._show_kde_osd", return_value=False) as mock_kde, \
             patch("gigamate.osd._show_freedesktop_notification", return_value=True) as mock_free:
            res = show_profile_osd("Quiet", "Silent fans")
            assert res is True
            mock_free.assert_called_once_with("Quiet", "Silent fans", "preferences-system-power-management")

    def test_show_profile_osd_fallback_when_kde_fails(self):
        with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "KDE"}), \
             patch("gigamate.osd._show_kde_osd", return_value=False) as mock_kde, \
             patch("gigamate.osd._show_freedesktop_notification", return_value=True) as mock_free:
            res = show_profile_osd("Balanced")
            assert res is True
            mock_kde.assert_called_once()
            mock_free.assert_called_once()


def test_cli_profile_cycle_passes_profile_name_and_description_to_osd(capsys):
    from gigamate.acpi import FanProfile
    from gigamate.cli import cmd_profile_cycle

    mock_ctrl = MagicMock()
    mock_ctrl.get_profile.return_value = FanProfile.QUIET
    mock_ctrl.set_profile.return_value = True
    profiles_map = {
        0: {"name": "Quiet", "description": "Low fan noise, capped GPU power"},
        1: {"name": "Balanced", "description": "Balanced performance and noise"},
    }

    with patch("gigamate.cli._open_profiles", return_value=(mock_ctrl, profiles_map, {"disabled_profiles": []})), \
         patch("gigamate.cli._record_profile_and_sync"), \
         patch("gigamate.dgpu_gate.high_power_profiles_allowed", return_value=True), \
         patch("gigamate.cli.show_profile_osd") as mock_osd:
        cmd_profile_cycle(MagicMock())

    out = capsys.readouterr().out
    assert "Power profile cycled to: Balanced  (1)" in out
    mock_osd.assert_called_once_with("Balanced", "Balanced performance and noise")
