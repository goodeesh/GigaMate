"""Lightweight sanity checks for install.sh — the release hardening changes."""

import re
import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "install.sh"
INIT_PY = Path(__file__).resolve().parent.parent / "src" / "gigamate" / "__init__.py"


def _extract_function(name):
    lines = INSTALL.read_text().splitlines()
    out = []
    capture = False
    for ln in lines:
        if ln.startswith(f"{name}()"):
            capture = True
        if capture:
            out.append(ln)
            if ln == "}":
                break
    return "\n".join(out)


def test_app_version_function_returns_version():
    """The released module-version derivation must actually parse __version__."""
    expected = re.search(
        r'__version__\s*=\s*["\']([^"\']+)["\']', INIT_PY.read_text()
    ).group(1)
    script = f"set -euo pipefail\n{_extract_function('app_version')}\napp_version {INIT_PY}\n"
    res = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == expected


def test_lock_serializes_updates():
    text = INSTALL.read_text()
    assert "gigamate-update.lock" in text
    assert "flock -n 9" in text


def test_module_version_derived_from_app():
    text = INSTALL.read_text()
    assert "app_version()" in text
    assert 'mod_version="${app_ver:-$mod_version}"' in text
    # The copied source gets the release version baked in.
    assert 's/^PACKAGE_VERSION=.*/PACKAGE_VERSION=' in text
    assert 's/#define DRIVER_VERSION .*/' in text


def test_dkms_force_and_running_kernel_first():
    text = INSTALL.read_text()
    assert "dkms install" in text and "--force" in text
    # Running kernel must be the first build target (B3).
    assert '"$running_kver" $(ls -d /lib/modules/*/build' in text


def test_repair_helper_installed():
    text = INSTALL.read_text()
    assert "install_repair_helper" in text
    assert "gigamate-repair" in text
    assert "org.gigamate.repair.policy" in text
    assert "50-gigamate-repair.rules" in text


def test_install_and_uninstall_bash_syntax():
    uninstall = Path(__file__).resolve().parent.parent / "uninstall.sh"
    for script in (INSTALL, uninstall):
        res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert res.returncode == 0, f"{script.name} failed bash -n:\n{res.stderr}"


def test_v5_install_uses_pyqt6_drops_gtk3_and_includes_compat_guard():
    text = INSTALL.read_text()
    assert "python-pyqt6" in text
    assert "python3-pyqt6" in text
    assert "python3-PyQt6" in text
    assert "libappindicator-gtk3" not in text
    assert "gir1.2-gtk-3.0" not in text
    assert "python3-usb" not in text
    assert "gigabyte-rgb-tray" not in text
    assert "gigamate profile contribute" not in text
    assert '<<<"$status_out"' in text
    assert '<<<"$status_out}"' not in text
    assert "check_v5_upgrade_compat" in text
    assert "--tag v4.5.3" in text


def test_uninstall_cleans_up_repair_helper_and_polkit():
    uninstall = Path(__file__).resolve().parent.parent / "uninstall.sh"
    text = uninstall.read_text()
    assert "/usr/lib/gigamate/gigamate-repair" in text
    assert "50-gigamate-repair.rules" in text
    assert "org.gigamate.repair.policy" in text


def test_doctor_accepts_vendor_udev_rule_path(tmp_path, monkeypatch):
    from unittest import mock
    from gigamate import doctor

    assert doctor.VENDOR_RULE_PATH == Path("/usr/lib/udev/rules.d/70-gigamate.rules")

    vendor_rule = tmp_path / "usr_lib_70-gigamate.rules"
    vendor_rule.write_text('SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0414", TAG+="uaccess"\n')
    etc_rule = tmp_path / "etc_70-gigamate.rules"
    legacy_rule = tmp_path / "etc_99-gigamate.rules"

    monkeypatch.setattr(doctor, "VENDOR_RULE_PATH", vendor_rule)
    monkeypatch.setattr(doctor, "RULE_PATH", etc_rule)
    monkeypatch.setattr(doctor, "LEGACY_RULE_PATH", legacy_rule)

    assert doctor.rule_state() == "present"

    with mock.patch.object(doctor, "node_state", side_effect=OSError("no dev")), \
         mock.patch.object(doctor, "open_error", return_value=None):
        lines = doctor.permission_lines("/dev/hidraw0")
    assert f"({vendor_rule})" in lines[0]
    assert "present" in lines[0]


def test_find_shadowed_legacy_files(tmp_path):
    from gigamate import doctor

    home = tmp_path / "home"
    etc_polkit = tmp_path / "etc_polkit"
    etc_udev = tmp_path / "etc_udev"

    expected_paths = [
        home / ".local" / "bin" / "gigamate",
        home / ".local" / "bin" / "gigamate-dgpu-watch",
        home / ".config" / "systemd" / "user" / "gigamate.service",
        etc_polkit / "50-gigamate-dgpu.rules",
        etc_polkit / "50-gigamate-repair.rules",
        etc_udev / "70-gigamate.rules",
        etc_udev / "99-gigamate-smartshift.rules",
    ]
    for p in expected_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("legacy\n")

    # Unrelated file must not be reported.
    unrelated = home / ".local" / "bin" / "other-tool"
    unrelated.write_text("keep\n")

    # Script-managed install returns empty list even when files exist.
    assert doctor.find_shadowed_legacy_files(
        home=home,
        etc_polkit_dir=etc_polkit,
        etc_udev_dir=etc_udev,
        method="script",
    ) == []

    # Package-managed install returns all matching shadowed legacy paths.
    found = doctor.find_shadowed_legacy_files(
        home=home,
        etc_polkit_dir=etc_polkit,
        etc_udev_dir=etc_udev,
        method="apt",
    )
    assert set(found) == {str(p) for p in expected_paths}


def test_doctor_surfaces_shadowed_legacy_files_warning(monkeypatch, capsys):
    from unittest import mock
    from gigamate import doctor

    shadowed = ["/home/user/.local/bin/gigamate", "/etc/udev/rules.d/70-gigamate.rules"]
    fake_acpi = mock.MagicMock(running_kernel="6.12", summary="loaded", hint="ok")

    with mock.patch.object(doctor, "find_keyboard_hidraws", return_value=[]), \
         mock.patch.object(doctor, "session_details", return_value=({}, "active")), \
         mock.patch.object(doctor, "acpi_module_status", return_value=fake_acpi), \
         mock.patch.object(doctor, "find_shadowed_legacy_files", return_value=shadowed):
        rc = doctor.run_doctor()

    assert rc == 0
    out = capsys.readouterr().out
    assert "warning" in out.lower() or "shadow" in out.lower()
    for path in shadowed:
        assert path in out


def test_consolidated_polkit_rule():
    repo_root = Path(__file__).resolve().parent.parent
    rule = repo_root / "data" / "50-gigamate.rules"
    assert rule.exists(), "data/50-gigamate.rules must exist"
    text = rule.read_text()
    assert text.startswith("// GigaMate —")
    assert text.count("polkit.addRule(") == 1
    assert 'subject.isInGroup("wheel")' in text
    assert 'subject.isInGroup("sudo")' in text
    for action in (
        "org.gigamate.dgpu.control",
        "org.gigamate.dgpu-gate.control",
        "org.gigamate.repair.driver",
        "org.freedesktop.systemd1.manage-units",
        "nvidia-powerd.service",
    ):
        assert action in text, f"Missing {action} in data/50-gigamate.rules"
    # Legacy per-feature rules must remain intact for install.sh compatibility.
    for legacy in (
        "50-gigamate-dgpu.rules",
        "50-gigamate-dgpu-gate.rules",
        "50-gigamate-repair.rules",
        "50-gigamate-powerd.rules",
    ):
        assert (repo_root / "data" / legacy).exists()


def test_systemd_user_preset():
    preset = Path(__file__).resolve().parent.parent / "data" / "90-gigamate.preset"
    assert preset.exists(), "data/90-gigamate.preset must exist"
    lines = [ln.strip() for ln in preset.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert "enable gigamate.service" in lines
    assert "enable gigamate-dgpu.service" in lines


def test_smartshift_udev_rule_has_dmi_vendor_guard():
    rule = Path(__file__).resolve().parent.parent / "data" / "99-gigamate-smartshift.rules"
    text = rule.read_text()
    assert text.startswith("# GigaMate")
    assert "dmi/id" in text
    assert 'ATTR{[dmi/id]sys_vendor}=="*[Gg][Ii][Gg][Aa][Bb][Yy][Tt][Ee]*|*[Aa][Oo][Rr][Uu][Ss]*|*[Gg][Ii][Gg][Aa]-[Bb][Yy][Tt][Ee]*"' in text
    assert 'SUBSYSTEM=="pci"' in text
    assert 'ATTRS{vendor}=="0x1002"' in text
    assert 'TEST=="smartshift_bias"' in text
    assert "smartshift_bias" in text


def test_uninstall_help_and_keep_config_flag():
    uninstall = Path(__file__).resolve().parent.parent / "uninstall.sh"
    res = subprocess.run(["bash", str(uninstall), "--help"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "--keep-config" in res.stdout
    res_short = subprocess.run(["bash", str(uninstall), "-h"], capture_output=True, text=True)
    assert res_short.returncode == 0, res_short.stderr
    assert "--keep-config" in res_short.stdout
    # Also ensure bash -n passes.
    res_syntax = subprocess.run(["bash", "-n", str(uninstall)], capture_output=True, text=True)
    assert res_syntax.returncode == 0, res_syntax.stderr
