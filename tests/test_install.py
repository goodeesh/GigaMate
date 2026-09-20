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