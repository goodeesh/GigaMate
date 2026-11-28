"""Tests for battery-efficient update checking (updates.py)."""

import json

import pytest
from unittest.mock import patch

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
        assert "rc=1" in cmd[-1]

    def test_update_command_logs_to_file(self, tmp_path):
        log = tmp_path / "update.log"
        cmd = updates.build_update_command(log_file=log)
        assert str(log) in cmd[-1] and "2>&1" in cmd[-1]

    def test_fetch_latest_version_raw_fallback(self):
        import urllib.error
        def fake_urlopen(req, timeout=5):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "raw.githubusercontent.com" in url:
                class Resp:
                    def __enter__(self): return self
                    def __exit__(self, *a): pass
                    def read(self): return b'__version__ = "3.1.0"\n'
                return Resp()
            raise urllib.error.HTTPError(url, 403, "rate limited", {}, None)

        ver = updates.fetch_latest_version(urlopen=fake_urlopen)
        assert ver == "v3.1.0"


class TestDetachedUpdate:
    def test_systemd_run_wraps_when_available(self):
        with patch("gigamate.updates.shutil.which", return_value="/usr/bin/systemd-run"):
            argv = updates.build_update_command()
        assert argv[0] == "systemd-run"
        assert "--scope" in argv
        assert argv[-3] == "bash" and argv[-2] == "-c"
        assert "--update" in argv[-1]

    def test_plain_shell_fallback(self):
        with patch("gigamate.updates.shutil.which", return_value=None):
            argv = updates.build_update_command()
        assert argv[0] == "bash"
        assert argv[1] == "-c"


class TestRepairHelpers:
    def test_repair_command_none_when_helper_missing(self):
        with patch("gigamate.updates.os.path.exists", return_value=False), \
             patch("gigamate.updates.shutil.which", return_value="/usr/bin/pkexec"):
            assert updates.repair_command() is None
            assert updates.repair_available() is False

    def test_repair_command_when_available(self):
        with patch("gigamate.updates.os.path.exists", return_value=True), \
             patch("gigamate.updates.shutil.which", return_value="/usr/bin/pkexec"):
            cmd = updates.repair_command()
            assert cmd == ["pkexec", updates.REPAIR_HELPER_PATH]
            assert updates.repair_available() is True

    def test_terminal_repair_snippet(self):
        snippet = updates.terminal_repair_snippet()
        assert "dkms install" in snippet
        assert "dkms remove" in snippet  # prunes stale registrations (B3-protected)
        assert "modprobe gigamate_acpi" in snippet
        assert "uname -r" in snippet

    def test_build_terminal_repair_prefers_detected(self):
        argv = updates.build_terminal_repair_command(
            which=lambda name: "/usr/bin/kitty" if name == "kitty" else None,
            detect=lambda: ("/usr/bin/kitty", "-e"))
        assert argv[0] == "/usr/bin/kitty"
        assert "dkms install" in " ".join(argv)


class TestAdminStatus:
    def _proc(self, rc, err=b""):
        class P:
            returncode = rc
            stderr = err
        return P()

    def test_ok_passwordless(self):
        assert updates.admin_status(
            run=lambda *a, **k: self._proc(0)) == "ok"

    def test_password_required(self):
        assert updates.admin_status(
            run=lambda *a, **k: self._proc(
                1, b"sudo: a password is required")) == "password"

    def test_denied_not_in_sudoers(self):
        assert updates.admin_status(
            run=lambda *a, **k: self._proc(
                1, b"adriansc is not in the sudoers file.")) == "denied"

    def test_no_sudo_binary(self):
        def boom(*a, **k):
            raise FileNotFoundError("sudo")
        assert updates.admin_status(run=boom) == "no-sudo"


class TestUndismiss:
    def test_undismiss_restores_badge(self, tmp_path, monkeypatch):
        state = tmp_path / "update_state.json"
        monkeypatch.setattr(updates, "get_installed_version", lambda: "2.0.0")
        fn = _urlopen_factory({"tag_name": "v2.0.1"})
        check_for_updates(force=True, now_ts=5000.0,
                          state_file=state, urlopen=fn)
        dismiss_version("v2.0.1", state_file=state)
        updates.undismiss(state_file=state)
        res = check_for_updates(force=False, now_ts=5001.0,
                                state_file=state, urlopen=_failing_urlopen)
        assert res["update_available"] is True


class TestDetectDefaultTerminal:
    def _run(self, outputs):
        class P:
            def __init__(self, out):
                self.returncode = 0
                self.stdout = out
        def fake(argv, **kwargs):
            key = " ".join(argv[:2]) if len(argv) > 1 else argv[0]
            if key in outputs:
                return P(outputs[key])
            raise FileNotFoundError(argv[0])
        return fake

    def _appdir(self, tmp_path, name="ghostty.desktop", body=None):
        d = tmp_path / "apps"
        d.mkdir(exist_ok=True)
        (d / name).write_text(body or (
            "[Desktop Entry]\nName=Ghostty\nType=Application\n"
            "TryExec=ghostty\nExec=ghostty\nX-TerminalArgExec=-e\n"))
        return d

    def test_kde_setting(self, tmp_path):
        run = self._run({"kreadconfig6 --file": "ghostty\n"})
        got = updates.detect_default_terminal(
            env={"XDG_CURRENT_DESKTOP": "KDE", "HOME": "x"},
            run=run, which=lambda n: "/usr/bin/ghostty",
            app_dirs=[self._appdir(tmp_path)])
        assert got == ("/usr/bin/ghostty", "-e")

    def test_gnome_setting(self, tmp_path):
        run = self._run({"gsettings get": "'ptyxis'\n"})
        got = updates.detect_default_terminal(
            env={"XDG_CURRENT_DESKTOP": "GNOME", "HOME": "x"},
            run=run, which=lambda n: "/usr/bin/ptyxis",
            app_dirs=[tmp_path])
        assert got == ("/usr/bin/ptyxis", "-e")

    def test_terminal_env(self, tmp_path):
        got = updates.detect_default_terminal(
            env={"TERMINAL": "kitty", "HOME": "x"},
            run=self._run({}), which=lambda n: "/usr/bin/kitty",
            app_dirs=[tmp_path])
        assert got == ("/usr/bin/kitty", "-e")

    def test_alternatives(self, tmp_path):
        run = self._run({"update-alternatives --query":
                         "Name: x-terminal-emulator\nValue: /usr/bin/xterm\n"})
        got = updates.detect_default_terminal(
            env={"HOME": "x"}, run=run,
            which=lambda n: "/usr/bin/xterm" if n == "xterm" else None,
            app_dirs=[tmp_path])
        assert got == ("/usr/bin/xterm", "-e")

    def test_nothing_detected(self, tmp_path):
        got = updates.detect_default_terminal(
            env={"HOME": "x"}, run=self._run({}),
            which=lambda n: None, app_dirs=[tmp_path])
        assert got is None

    def test_exec_arg_defaults_to_dash_e(self, tmp_path):
        d = tmp_path / "apps"
        d.mkdir(exist_ok=True)
        (d / "myterm.desktop").write_text(
            "[Desktop Entry]\nName=MyTerm\nExec=myterm\n")
        run = self._run({"kreadconfig6 --file": "myterm\n"})
        got = updates.detect_default_terminal(
            env={"XDG_CURRENT_DESKTOP": "KDE", "HOME": "x"},
            run=run, which=lambda n: "/usr/bin/myterm",
            app_dirs=[d])
        assert got == ("/usr/bin/myterm", "-e")


class TestTerminalCommand:
    def test_prefers_detected_default(self):
        argv = updates.build_terminal_update_command(
            desktop="KDE",
            which=lambda n: "/usr/bin/konsole",
            detect=lambda: ("/usr/bin/ghostty", "-e"))
        assert argv is not None
        assert argv[0] == "/usr/bin/ghostty"
        assert "--update" in argv[-1] and "--yes" in argv[-1]

    def test_dashdash_family_separator(self):
        argv = updates.build_terminal_update_command(
            which=lambda n: None,
            detect=lambda: ("/usr/bin/gnome-terminal", "--"))
        assert argv is not None
        assert argv[:2] == ["/usr/bin/gnome-terminal", "--"]

    def test_falls_back_to_scan(self):
        fake_which = lambda name: "/usr/bin/xterm" if name == "xterm" else None
        argv = updates.build_terminal_update_command(
            desktop="KDE", which=fake_which, detect=lambda: None)
        assert argv is not None
        assert argv[0] == "/usr/bin/xterm"

    def test_none_when_no_terminal(self):
        assert updates.build_terminal_update_command(
            desktop="", which=lambda name: None,
            detect=lambda: None) is None


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


class TestTagValidationAndPinning:
    def test_prerelease_tags_are_valid(self):
        assert is_valid_tag("v3.0.0b1") is True
        assert is_valid_tag("v3.0.0rc1") is True
        assert is_valid_tag("3.0.0b1") is False  # tags require a leading 'v'

    def test_prerelease_ordering(self):
        assert is_newer("v3.0.0", "3.0.0b1") is True
        assert is_newer("v3.0.0b2", "3.0.0b1") is True
        assert is_newer("v3.0.0b1", "3.0.0") is False
        assert is_newer("v3.0.0-rc.2", "3.0.0-rc.1") is True
        assert is_newer("v3.0.0", "3.0.0-rc.2") is True
        assert is_newer("v3.0.0-beta.1", "2.0.8") is True

    def test_install_script_url_pins_tag(self):
        url = updates.install_script_url("v1.2.3")
        assert "/v1.2.3/install.sh" in url
        # Invalid/empty tag falls back to main (documented last resort).
        assert "/main/install.sh" in updates.install_script_url(None)

    def test_update_command_uses_pinned_url_and_safe_curl(self):
        cmd = build_update_command(tag="v1.2.3")
        body = cmd[-1]
        assert "/v1.2.3/install.sh" in body
        assert "-fL" in body and '--proto "=https"' in body
        assert "sha256sum" in body

    def test_update_command_never_pipes_to_shell(self):
        body = build_update_command(tag="v1.2.3")[-1]
        assert "| bash" not in body


def test_manual_update_instructions_not_piped():
    text = updates.manual_update_instructions("v1.2.3")
    assert "| bash" not in text
    assert "v1.2.3" in text


def test_install_script_sha256_url():
    assert updates.install_script_sha256_url("v1.2.3").endswith("/v1.2.3/install.sh.sha256")
    assert updates.install_script_sha256_url("main") == ""


def test_save_state_is_atomic_and_locked(tmp_path):
    state_file = tmp_path / "update_state.json"
    updates.save_state({"last_check_ts": 123, "latest_known": "v1.2.3"}, state_file)
    assert updates.load_state(state_file)["latest_known"] == "v1.2.3"
    # No leftover temp files.
    assert not list(tmp_path.glob("*.tmp.*"))


def test_update_state_merge_preserves_dismissal(tmp_path):
    state_file = tmp_path / "update_state.json"
    updates.save_state({"dismissed_version": "v1.0.0"}, state_file)

    def _merge(state):
        state["latest_known"] = "v2.0.0"
        return state

    merged = updates.update_update_state(_merge, state_file)
    assert merged["dismissed_version"] == "v1.0.0"
    assert merged["latest_known"] == "v2.0.0"
    assert updates.load_state(state_file)["dismissed_version"] == "v1.0.0"


class TestDetectInstallMethod:
    def _proc(self, rc: int):
        class P:
            returncode = rc
        return P()

    def test_install_method_file_constant(self):
        from pathlib import Path
        assert updates.INSTALL_METHOD_FILE == Path("/usr/lib/gigamate/install-method")

    @pytest.mark.parametrize("raw,expected", [
        ("apt", "apt"),
        ("  APT\n", "apt"),
        ("dnf\n", "dnf"),
        ("zypper", "zypper"),
        ("pacman", "pacman"),
        ("AUR\n", "aur"),
    ])
    def test_marker_file_supported_methods(self, tmp_path, raw, expected):
        marker = tmp_path / "install-method"
        marker.write_text(raw, encoding="utf-8")
        assert updates.detect_install_method(
            marker_path=marker,
            which=lambda _: None,
        ) == expected

    def test_marker_file_script_returns_immediately_without_probing(self, tmp_path):
        marker = tmp_path / "install-method"
        marker.write_text("script\n", encoding="utf-8")

        def fail_probe(*a, **k):
            raise AssertionError("must not probe when marker explicitly says 'script'")

        assert updates.detect_install_method(
            marker_path=marker,
            which=fail_probe,
            run=fail_probe,
        ) == "script"

    def test_fallback_pacman_probe(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {"gigamate": "/usr/bin/gigamate", "pacman": "/usr/bin/pacman"}
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(argv)
            return self._proc(0 if argv[:2] == ["pacman", "-Qo"] else 1)

        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=fake_run,
        )
        assert got == "pacman"
        assert ["pacman", "-Qo", "/usr/bin/gigamate"] in seen

    def test_fallback_dpkg_probe(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {"gigamate": "/usr/bin/gigamate", "dpkg": "/usr/bin/dpkg"}

        def fake_run(argv, **kwargs):
            return self._proc(0 if argv[:2] == ["dpkg", "-S"] else 1)

        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=fake_run,
        )
        assert got == "apt"

    def test_fallback_rpm_dnf_probe(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {
            "gigamate": "/usr/bin/gigamate",
            "rpm": "/usr/bin/rpm",
            "dnf": "/usr/bin/dnf",
        }

        def fake_run(argv, **kwargs):
            return self._proc(0 if argv[:2] == ["rpm", "-qf"] else 1)

        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=fake_run,
        )
        assert got == "dnf"

    def test_fallback_rpm_zypper_probe(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {
            "gigamate": "/usr/bin/gigamate",
            "rpm": "/usr/bin/rpm",
            "zypper": "/usr/bin/zypper",
        }

        def fake_run(argv, **kwargs):
            return self._proc(0 if argv[:2] == ["rpm", "-qf"] else 1)

        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=fake_run,
        )
        assert got == "zypper"

    def test_fallback_rpm_both_zypper_and_dnf_returns_dnf(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {
            "gigamate": "/usr/bin/gigamate",
            "rpm": "/usr/bin/rpm",
            "zypper": "/usr/bin/zypper",
            "dnf": "/usr/bin/dnf",
        }

        def fake_run(argv, **kwargs):
            return self._proc(0 if argv[:2] == ["rpm", "-qf"] else 1)

        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=fake_run,
        )
        assert got == "dnf"

    def test_fallback_no_gigamate_binary_returns_script(self, tmp_path):
        marker = tmp_path / "missing"
        assert updates.detect_install_method(
            marker_path=marker,
            which=lambda _: None,
        ) == "script"

    def test_fallback_probes_fail_returns_script(self, tmp_path):
        marker = tmp_path / "missing"
        bins = {
            "gigamate": "/usr/local/bin/gigamate",
            "pacman": "/usr/bin/pacman",
            "dpkg": "/usr/bin/dpkg",
            "rpm": "/usr/bin/rpm",
        }
        got = updates.detect_install_method(
            marker_path=marker,
            which=bins.get,
            run=lambda *a, **k: self._proc(1),
        )
        assert got == "script"

    def test_never_raises_on_errors(self, tmp_path):
        marker = tmp_path / "missing"

        def boom(*a, **k):
            raise RuntimeError("boom")

        assert updates.detect_install_method(
            marker_path=marker,
            which=boom,
            run=boom,
        ) == "script"


class TestIsPackageManaged:
    @pytest.mark.parametrize("method,expected", [
        ("apt", True),
        ("dnf", True),
        ("zypper", True),
        ("pacman", True),
        ("aur", True),
        ("script", False),
        ("", False),
        ("unknown", False),
    ])
    def test_explicit_methods(self, method, expected):
        assert updates.is_package_managed(method) is expected

    def test_defaults_to_detect_install_method(self, monkeypatch):
        monkeypatch.setattr(updates, "detect_install_method", lambda: "apt")
        assert updates.is_package_managed() is True
        monkeypatch.setattr(updates, "detect_install_method", lambda: "script")
        assert updates.is_package_managed() is False


class TestPackageUpgradeCommand:
    def test_apt(self):
        assert updates.package_upgrade_command("apt") == (
            "sudo apt update && sudo apt upgrade gigamate gigamate-dkms"
        )

    def test_dnf(self):
        assert updates.package_upgrade_command("dnf") == (
            "sudo dnf upgrade gigamate gigamate-dkms"
        )

    def test_zypper(self):
        assert updates.package_upgrade_command("zypper") == (
            "sudo zypper update gigamate gigamate-dkms"
        )

    def test_pacman(self):
        assert updates.package_upgrade_command("pacman") == (
            "sudo pacman -Syu gigamate gigamate-dkms"
        )

    def test_aur_prefers_paru(self):
        bins = {"paru": "/usr/bin/paru", "yay": "/usr/bin/yay"}
        assert updates.package_upgrade_command("aur", which=bins.get) == (
            "paru -Syu gigamate gigamate-dkms"
        )

    def test_aur_falls_back_to_yay(self):
        bins = {"yay": "/usr/bin/yay"}
        assert updates.package_upgrade_command("aur", which=bins.get) == (
            "yay -Syu gigamate gigamate-dkms"
        )

    def test_aur_defaults_to_paru_when_neither_found(self):
        assert updates.package_upgrade_command("aur", which=lambda _: None) == (
            "paru -Syu gigamate gigamate-dkms"
        )

    def test_script_and_unknown_return_none(self):
        assert updates.package_upgrade_command("script") is None
        assert updates.package_upgrade_command("unknown") is None
        assert updates.package_upgrade_command("") is None


class TestBuildPackageTerminalUpdateCommand:
    def test_builds_terminal_argv(self):
        argv = updates.build_package_terminal_update_command(
            "apt",
            which=lambda n: "/usr/bin/kitty" if n == "kitty" else None,
            detect=lambda: ("/usr/bin/kitty", "-e"),
        )
        assert argv is not None
        assert argv[0] == "/usr/bin/kitty"
        assert argv[-1] == (
            "sudo apt update && sudo apt upgrade gigamate gigamate-dkms; exec bash"
        )

    def test_returns_none_for_script(self):
        assert updates.build_package_terminal_update_command(
            "script",
            which=lambda n: "/usr/bin/kitty",
            detect=lambda: ("/usr/bin/kitty", "-e"),
        ) is None

    def test_returns_none_when_no_terminal(self):
        assert updates.build_package_terminal_update_command(
            "dnf",
            which=lambda _: None,
            detect=lambda: None,
        ) is None

    def test_aur_passes_which_for_yay_fallback(self):
        bins = {"yay": "/usr/bin/yay", "xterm": "/usr/bin/xterm"}
        argv = updates.build_package_terminal_update_command(
            "aur",
            which=bins.get,
            detect=lambda: None,
        )
        assert argv is not None
        assert argv[0] == "/usr/bin/xterm"
        assert "yay -Syu gigamate gigamate-dkms; exec bash" == argv[-1]


class TestCliPackageManagedUpdate:
    def test_cmd_update_package_managed_prints_command_and_exits_cleanly(
        self, monkeypatch, capsys
    ):
        from types import SimpleNamespace
        from gigamate import cli

        monkeypatch.setattr(
            updates,
            "check_for_updates",
            lambda force=True: {
                "current": "5.0.0",
                "latest": "v5.1.0",
                "update_available": True,
                "checked_now": True,
                "reachable": True,
            },
        )
        monkeypatch.setattr(updates, "detect_install_method", lambda: "apt")

        def boom(*a, **k):
            raise AssertionError("install.sh path must not be called")

        monkeypatch.setattr(updates, "admin_status", boom)
        monkeypatch.setattr(updates, "build_update_command", boom)

        cli.cmd_update(SimpleNamespace(check=False, yes=True))
        out = capsys.readouterr().out
        assert "GigaMate is installed via apt. Update using your package manager:" in out
        assert "sudo apt update && sudo apt upgrade gigamate gigamate-dkms" in out


class TestMigrateLegacyUserInstall:
    def _write(self, path, content: str = ""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_package_managed_removes_legacy_files_and_preserves_custom(self, tmp_path):
        home = tmp_path / "home"
        sys_share = tmp_path / "usr_share"
        marker = tmp_path / "install-method"
        marker.write_text("apt\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        # User config must never be touched
        user_cfg = self._write(
            home / ".config" / "gigamate" / "config.json",
            '{"charge_limit": 80}\n',
        )

        # Legacy pip console scripts
        legacy_bins = [
            self._write(
                home / ".local" / "bin" / name,
                f"#!/usr/bin/python3\nfrom gigamate.{mod} import main\nmain()\n",
            )
            for name, mod in [
                ("gigamate", "cli"),
                ("gigamate-tray", "tray"),
                ("gigamate-center", "center"),
                ("gigamate-dgpu-watch", "dgpu_watch"),
            ]
        ]
        custom_bin = self._write(
            home / ".local" / "bin" / "my-custom-script",
            "#!/bin/sh\necho hello\n",
        )

        # Legacy systemd units
        legacy_units = [
            self._write(
                home / ".config" / "systemd" / "user" / "gigamate.service",
                "[Service]\nExecStart=%h/.local/bin/gigamate-tray\n",
            ),
            self._write(
                home / ".config" / "systemd" / "user" / "gigamate-dgpu.service",
                "[Service]\nExecStart=/home/u/.local/bin/gigamate-dgpu-watch\n",
            ),
        ]
        custom_unit = self._write(
            home / ".config" / "systemd" / "user" / "custom-user.service",
            "[Service]\nExecStart=/usr/bin/true\n",
        )

        # Legacy desktop files: one byte-matches packaged desktop, one has legacy install.sh signature
        pkg_desktop_content = "[Desktop Entry]\nName=GigaMate\nExec=/usr/bin/gigamate-center\n"
        self._write(sys_share / "applications" / "gigamate.desktop", pkg_desktop_content)
        legacy_desktop = [
            self._write(
                home / ".local" / "share" / "applications" / "gigamate.desktop",
                pkg_desktop_content,
            ),
            self._write(
                home / ".local" / "share" / "applications" / "gigamate-center.desktop",
                "[Desktop Entry]\nExec=/home/u/.local/bin/gigamate-center\nStartupWMClass=gigamate-center\n",
            ),
        ]
        other_desktop = self._write(
            home / ".local" / "share" / "applications" / "other.desktop",
            "[Desktop Entry]\n",
        )

        # Legacy icons (byte-matching packaged icons under system_share_dir)
        icon_names = (
            "gigamate.svg",
            "gigamate-nvidia.svg",
            "gigamate-amd.svg",
            "gigamate-update.svg",
            "gigamate-nvidia-update.svg",
            "gigamate-amd-update.svg",
            "checkbox-checked.svg",
        )
        legacy_icons = []
        for name in icon_names:
            svg_content = f"<svg data-name='{name}'/>"
            self._write(
                sys_share / "icons" / "hicolor" / "scalable" / "apps" / name,
                svg_content,
            )
            legacy_icons.append(
                self._write(
                    home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / name,
                    svg_content,
                )
            )
        other_icon = self._write(
            home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "other.svg",
            "<svg/>",
        )

        run_calls = []

        def fake_run(argv, **kwargs):
            run_calls.append(argv)

            class P:
                returncode = 0

            return P()

        fake_which = lambda name: "/usr/bin/systemctl" if name == "systemctl" else None

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=fake_run,
            which=fake_which,
            system_share_dir=sys_share,
        )

        expected_removed = {
            str(p) for p in (*legacy_bins, *legacy_units, *legacy_desktop, *legacy_icons)
        }
        assert set(removed) == expected_removed
        for p in (*legacy_bins, *legacy_units, *legacy_desktop, *legacy_icons):
            assert not p.exists()

        # Preserved files
        assert user_cfg.read_text(encoding="utf-8") == '{"charge_limit": 80}\n'
        assert custom_bin.exists()
        assert custom_unit.exists()
        assert other_desktop.exists()
        assert other_icon.exists()

        # systemctl daemon-reload called once
        assert run_calls == [["systemctl", "--user", "daemon-reload"]]

        # State persisted
        assert updates.load_state(state_file).get("migrated_to_pkg") is True

        # Second call is a complete no-op and never invokes detect_install_method/subprocesses
        run_calls.clear()

        def fail_if_called(*a, **k):
            raise AssertionError("post-migration startup must not probe or spawn subprocesses")

        second = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=tmp_path / "missing-marker",
            run=fail_if_called,
            which=fail_if_called,
            system_share_dir=sys_share,
        )
        assert second == []
        assert run_calls == []

    def test_preserves_custom_gigamate_wrapper_and_non_local_unit(self, tmp_path):
        home = tmp_path / "home"
        marker = tmp_path / "install-method"
        marker.write_text("dnf\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        # Custom user wrapper with same filename, no pip console-script signature
        custom_wrapper = self._write(
            home / ".local" / "bin" / "gigamate",
            "#!/bin/sh\nexec /usr/bin/gigamate --verbose \"$@\"\n",
        )
        # Custom user systemd unit not referencing .local/bin/gigamate
        custom_unit = self._write(
            home / ".config" / "systemd" / "user" / "gigamate.service",
            "[Service]\nExecStart=/usr/bin/gigamate-tray\n",
        )

        run_calls = []

        def fake_run(argv, **kwargs):
            run_calls.append(argv)

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=fake_run,
            which=lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
        )

        assert removed == []
        assert custom_wrapper.exists()
        assert custom_unit.exists()
        assert run_calls == []
        assert updates.load_state(state_file).get("migrated_to_pkg") is True

    def test_script_install_is_complete_noop(self, tmp_path):
        home = tmp_path / "home"
        marker = tmp_path / "missing-marker"
        state_file = home / ".config" / "gigamate" / "update_state.json"

        legacy_bin = self._write(
            home / ".local" / "bin" / "gigamate",
            "#!/usr/bin/python3\nfrom gigamate.cli import main\n",
        )
        legacy_unit = self._write(
            home / ".config" / "systemd" / "user" / "gigamate.service",
            "[Service]\nExecStart=%h/.local/bin/gigamate-tray\n",
        )
        legacy_desktop = self._write(
            home / ".local" / "share" / "applications" / "gigamate.desktop",
            "[Desktop Entry]\n",
        )
        legacy_icon = self._write(
            home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "gigamate.svg",
            "<svg/>",
        )

        run_calls = []
        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=lambda argv, **k: run_calls.append(argv),
            which=lambda _: None,
        )

        assert removed == []
        assert legacy_bin.exists()
        assert legacy_unit.exists()
        assert legacy_desktop.exists()
        assert legacy_icon.exists()
        assert not state_file.exists()
        assert run_calls == []

    def test_preserves_existing_state_and_handles_systemctl_failure(self, tmp_path):
        home = tmp_path / "home"
        marker = tmp_path / "install-method"
        marker.write_text("pacman\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"
        updates.save_state({"dismissed_version": "v5.0.0", "last_check_ts": 42.0}, state_file)

        legacy_unit = self._write(
            home / ".config" / "systemd" / "user" / "gigamate.service",
            "[Service]\nExecStart=%h/.local/bin/gigamate-tray\n",
        )

        def boom(*a, **k):
            raise OSError("systemctl failed")

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=boom,
            which=lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
        )

        assert removed == [str(legacy_unit)]
        assert not legacy_unit.exists()
        state = updates.load_state(state_file)
        assert state["migrated_to_pkg"] is True
        assert state["dismissed_version"] == "v5.0.0"
        assert state["last_check_ts"] == 42.0

    def test_removes_symlink_to_pip_script_and_swallows_state_oserror(
        self, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        marker = tmp_path / "install-method"
        marker.write_text("zypper\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        target = self._write(
            tmp_path / "venv_bin_gigamate",
            "#!/usr/bin/python3\nfrom gigamate.cli import main\n",
        )
        link = home / ".local" / "bin" / "gigamate"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

        def fail_state(*a, **k):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(updates, "update_update_state", fail_state)

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=lambda *a, **k: None,
            which=lambda _: None,
        )

        assert removed == [str(link)]
        assert not link.exists() and not link.is_symlink()
        assert target.exists()

    def test_preserves_shell_wrapper_mentioning_gigamate_dot_log(self, tmp_path):
        home = tmp_path / "home"
        marker = tmp_path / "install-method"
        marker.write_text("apt\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        # Custom shell wrapper containing the substring "gigamate." (gigamate.log)
        shell_wrapper = self._write(
            home / ".local" / "bin" / "gigamate",
            "#!/bin/sh\necho starting >> ~/gigamate.log\nexec /usr/bin/gigamate \"$@\"\n",
        )
        # Legacy setuptools load_entry_point script should still be removed
        setuptools_script = self._write(
            home / ".local" / "bin" / "gigamate-tray",
            "#!/usr/bin/env python3\n"
            "from pkg_resources import load_entry_point\n"
            "load_entry_point('gigamate==1.0.0', 'console_scripts', 'gigamate-tray')()\n",
        )

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=lambda *a, **k: None,
            which=lambda _: None,
        )

        assert removed == [str(setuptools_script)]
        assert shell_wrapper.exists()
        assert not setuptools_script.exists()

    def test_removes_dangling_wants_symlink_and_preserves_unrelated_wants(
        self, tmp_path
    ):
        home = tmp_path / "home"
        marker = tmp_path / "install-method"
        marker.write_text("apt\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        unit_dir = home / ".config" / "systemd" / "user"
        wants_dir = unit_dir / "graphical-session.target.wants"
        wants_dir.mkdir(parents=True, exist_ok=True)

        legacy_unit = self._write(
            unit_dir / "gigamate.service",
            "[Service]\nExecStart=%h/.local/bin/gigamate-tray\n",
        )
        legacy_wants = wants_dir / "gigamate.service"
        legacy_wants.symlink_to(legacy_unit)

        other_unit = self._write(
            unit_dir / "other.service",
            "[Service]\nExecStart=/usr/bin/other\n",
        )
        other_wants = wants_dir / "other.service"
        other_wants.symlink_to(other_unit)

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=lambda *a, **k: None,
            which=lambda _: None,
        )

        assert set(removed) == {str(legacy_unit), str(legacy_wants)}
        assert not legacy_unit.exists()
        assert not legacy_wants.exists() and not legacy_wants.is_symlink()
        assert other_unit.exists()
        assert other_wants.is_symlink() and other_wants.exists()

    def test_preserves_unrelated_checkbox_icon_custom_desktop_and_symlinks(self, tmp_path):
        home = tmp_path / "home"
        sys_share = tmp_path / "usr_share"
        marker = tmp_path / "install-method"
        marker.write_text("pacman\n", encoding="utf-8")
        state_file = home / ".config" / "gigamate" / "update_state.json"

        # Packaged files under system_share_dir
        self._write(
            sys_share / "applications" / "gigamate.desktop",
            "[Desktop Entry]\nName=GigaMate\nExec=/usr/bin/gigamate-center\n",
        )
        self._write(
            sys_share / "icons" / "hicolor" / "scalable" / "apps" / "checkbox-checked.svg",
            "<svg>gigamate-packaged-checkbox</svg>",
        )
        self._write(
            sys_share / "icons" / "hicolor" / "scalable" / "apps" / "gigamate.svg",
            "<svg>gigamate-packaged-icon</svg>",
        )

        # User's custom desktop override (differs from packaged and no .local/bin/gigamate)
        custom_desktop = self._write(
            home / ".local" / "share" / "applications" / "gigamate.desktop",
            "[Desktop Entry]\nName=My Custom GigaMate\nExec=env QT_SCALE_FACTOR=1.5 /usr/bin/gigamate-center\n",
        )
        # Symlinked desktop entry (even if target matches packaged) must be preserved
        sym_desktop_target = self._write(
            tmp_path / "real-center.desktop",
            "[Desktop Entry]\nExec=/home/u/.local/bin/gigamate-center\nStartupWMClass=gigamate-center\n",
        )
        sym_desktop = home / ".local" / "share" / "applications" / "gigamate-center.desktop"
        sym_desktop.symlink_to(sym_desktop_target)

        # Unrelated user checkbox-checked.svg (different bytes than packaged)
        unrelated_checkbox = self._write(
            home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "checkbox-checked.svg",
            "<svg>unrelated-theme-checkbox</svg>",
        )
        # Symlinked icon (even if target matches packaged) must be preserved
        sym_icon_target = self._write(
            tmp_path / "real-gigamate.svg",
            "<svg>gigamate-packaged-icon</svg>",
        )
        sym_icon = (
            home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "gigamate.svg"
        )
        sym_icon.symlink_to(sym_icon_target)

        removed = updates.migrate_legacy_user_install(
            home=home,
            state_file=state_file,
            marker_path=marker,
            run=lambda *a, **k: None,
            which=lambda _: None,
            system_share_dir=sys_share,
        )

        assert removed == []
        assert custom_desktop.exists()
        assert sym_desktop.is_symlink() and sym_desktop.exists()
        assert unrelated_checkbox.exists()
        assert sym_icon.is_symlink() and sym_icon.exists()
        assert updates.load_state(state_file).get("migrated_to_pkg") is True
