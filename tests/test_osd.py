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
