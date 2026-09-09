"""Tests for battery-efficient update checking (updates.py)."""

import json

import pytest

from gigamate import updates
from gigamate.updates import (
    build_update_command,
    check_for_updates,
    dismiss_version,
    fetch_latest_version,
    is_newer,
    is_valid_tag,
    parse_version,
    should_check,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_factory(payload):
    def fake(url_or_req, timeout=None):
        return _Resp(payload)
    return fake


def _failing_urlopen(url_or_req, timeout=None):
    raise OSError("offline")


class TestParseVersion:
    def test_plain(self):
        assert parse_version("2.0.1") == (2, 0, 1)

    def test_v_prefix(self):
        assert parse_version("v2.0.1") == (2, 0, 1)

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_version("main")
        with pytest.raises(ValueError):
            parse_version("2.0")

    def test_is_newer(self):
        assert is_newer("v2.0.1", "2.0.0") is True
        assert is_newer("2.0.0", "2.0.0") is False
        assert is_newer("2.0.0", "2.1.0") is False
        assert is_newer("10.0.0", "9.9.9") is True
        assert is_newer("bogus", "2.0.0") is False

    def test_valid_tag_only_maintainer_format(self):
        assert is_valid_tag("v2.0.1") is True
        assert is_valid_tag("2.0.1") is False
        assert is_valid_tag("v2.0") is False
        assert is_valid_tag("main") is False
        assert is_valid_tag("") is False


class TestShouldCheck:
    def test_first_run(self):
        assert should_check(1000.0, None) is True

    def test_interval(self):
        assert should_check(2000.0, 1000.0, interval_sec=3600) is False
        assert should_check(5000.0, 1000.0, interval_sec=3600) is True

    def test_bad_cache_forces_check(self):
        assert should_check(1000.0, "bogus") is True  # type: ignore[arg-type]


class TestFetchLatest:
    def test_release_tag(self):
        fn = _urlopen_factory({"tag_name": "v2.0.1"})
        assert fetch_latest_version(urlopen=fn) == "v2.0.1"

    def test_invalid_tag_ignored_then_tags_fallback(self):
        calls = {"n": 0}

        def fake(url_or_req, timeout=None):
            calls["n"] += 1
            url = getattr(url_or_req, "full_url", str(url_or_req))
            if "releases" in url:
                return _Resp({"tag_name": "not-a-release"})
            return _Resp([{"name": "v2.0.2"}])

        assert fetch_latest_version(urlopen=fake) == "v2.0.2"

    def test_offline_returns_none(self):
        assert fetch_latest_version(urlopen=_failing_urlopen) is None


class TestCheckCaching:
    def test_uses_cache_within_interval(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        state.write_text(json.dumps({"last_check_ts": 1000.0,
                                     "latest_known": "v9.9.9"}))
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")

        def boom(url_or_req, timeout=None):
            raise AssertionError("should not hit network")

        res = check_for_updates(force=False, now_ts=1001.0,
                                state_file=state, urlopen=boom)
        assert res["checked_now"] is False
        assert res["latest"] == "v9.9.9"
        assert res["update_available"] is True

    def test_force_hits_network_and_saves(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")
        fn = _urlopen_factory({"tag_name": "v2.0.1"})
        res = check_for_updates(force=True, now_ts=5000.0,
                                state_file=state, urlopen=fn)
        assert res["checked_now"] is True
        assert res["update_available"] is True
        saved = json.loads(state.read_text())
        assert saved["latest_known"] == "v2.0.1"

    def test_dismissed_version_hides_badge(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")
        fn = _urlopen_factory({"tag_name": "v2.0.1"})
        check_for_updates(force=True, now_ts=5000.0,
                          state_file=state, urlopen=fn)
        dismiss_version("v2.0.1", state_file=state)
        res = check_for_updates(force=False, now_ts=5001.0,
                                state_file=state, urlopen=_failing_urlopen)
        # Offline + cached: network must not be touched; dismissed hides badge.
        assert res["update_available"] is False

    def test_offline_keeps_cached_latest(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        state.write_text(json.dumps({"last_check_ts": 0,
                                     "latest_known": "v2.0.5"}))
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")
        res = check_for_updates(force=True, now_ts=99999.0,
                                state_file=state, urlopen=_failing_urlopen)
        assert res["latest"] == "v2.0.5"
        assert res["checked_now"] is False
        assert res["reachable"] is False

    def test_reachable_but_no_releases(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")
        fn = _urlopen_factory([])  # no tags, no releases -> like fresh repo
        res = check_for_updates(force=True, now_ts=99999.0,
                                state_file=state, urlopen=fn)
        assert res["latest"] is None
        assert res["reachable"] is True
        assert res["update_available"] is False


class TestIconMatrix:
    def test_six_icons_registered(self):
        from gigamate.paths import ICON_NAMES
        assert set(ICON_NAMES) == {"gigamate", "gigamate-nvidia",
                                   "gigamate-amd", "gigamate-update",
                                   "gigamate-nvidia-update",
                                   "gigamate-amd-update"}

    def test_svg_files_exist(self):
        from pathlib import Path
        data = Path(__file__).resolve().parent.parent / "data"
        for name in ("gigamate-update", "gigamate-nvidia-update",
                     "gigamate-amd-update"):
            assert (data / f"{name}.svg").exists(), name

    def test_combination_rule(self):
        # Mirrors GigaMateTrayApp._tray_icon_key without importing Gtk.
        def key(base, update):
            if update:
                return f"{base}-update" if base != "gigamate" else "gigamate-update"
            return base

        assert key("gigamate", True) == "gigamate-update"
        assert key("gigamate-nvidia", True) == "gigamate-nvidia-update"
        assert key("gigamate-amd", True) == "gigamate-amd-update"
        assert key("gigamate-nvidia", False) == "gigamate-nvidia"


class TestBuildCommand:
    def test_update_command_targets_install_sh(self):
        cmd = build_update_command()
        assert "--update" in cmd[-1] and "--yes" in cmd[-1]
        assert "install.sh" in cmd[-1]


class TestEndpointUrls:
    def test_github_api_urls_hit_repos_namespace(self):
        # Regression: missing /repos/ made every check 404 (mocks hid it).
        assert updates.RELEASE_LATEST_URL.startswith(
            "https://api.github.com/repos/goodeesh/GigaMate/")
        assert updates.TAGS_URL.startswith(
            "https://api.github.com/repos/goodeesh/GigaMate/")

    def test_fetch_uses_repos_url(self):
        seen = []

        def fake(url_or_req, timeout=None):
            seen.append(getattr(url_or_req, "full_url", str(url_or_req)))
            return _Resp({"tag_name": "v2.0.1"})

        assert fetch_latest_version(urlopen=fake) == "v2.0.1"
        assert seen and all("/repos/" in u for u in seen)


class TestMenuItemLabel:
    def test_idle(self):
        assert updates.menu_item_label(False) == "Check for updates"

    def test_available_has_no_version(self):
        assert updates.menu_item_label(True) == "Update available"
