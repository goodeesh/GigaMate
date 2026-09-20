"""Tests for the privileged ACPI driver repair helper (data/gigamate-repair).

The helper is a standalone stdlib-only script (it runs under the system
python3 via pkexec), loaded here through importlib for unit testing.
"""

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

from unittest.mock import patch

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_helper():
    path = str(DATA_DIR / "gigamate-repair")
    loader = importlib.machinery.SourceFileLoader("gigamate_repair_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


def _make_source(root, version):
    src = root / f"gigamate_acpi-{version}"
    src.mkdir(parents=True)
    (src / "Makefile").write_text("all:\n\t@echo ok\n")
    (src / "dkms.conf").write_text(f'PACKAGE_NAME="gigamate_acpi"\nPACKAGE_VERSION="{version}"\n')
    return src


class TestFindSource:
    def test_empty_usr_src(self, tmp_path):
        mod = _load_helper()
        assert mod.find_source(str(tmp_path)) == (None, None)

    def test_picks_newest(self, tmp_path):
        mod = _load_helper()
        _make_source(tmp_path, "3.0.0")
        newer = _make_source(tmp_path, "3.1.0")
        src, ver = mod.find_source(str(tmp_path))
        assert src == str(newer)
        assert ver == "3.1.0"

    def test_ignores_other_packages(self, tmp_path):
        mod = _load_helper()
        (tmp_path / "nvidia.ko").write_text("x")
        (tmp_path / "gigamate_acpi-foo").mkdir()
        assert mod.find_source(str(tmp_path)) == (None, None)


class TestDkmsRegistered:
    def test_registered(self):
        mod = _load_helper()

        def fake_run(argv, **kw):
            assert argv[0] == "dkms"
            return type("P", (), {"returncode": 0, "stdout": (
                "gigamate_acpi/3.0.0, 6.18.52-1-cachyos-lts, x86_64: installed\n"
                "gigamate_acpi/3.0.0, 7.2.6-1-cachyos, x86_64: installed\n"),
                "stderr": ""})()

        assert mod.dkms_registered("3.0.0", run=fake_run) is True

    def test_not_registered(self):
        mod = _load_helper()
        assert mod.dkms_registered("3.1.0",
                                   run=lambda argv, **kw: type(
                                       "P", (), {"returncode": 0, "stdout": "", "stderr": ""})()) is False


class TestRepairFlow:
    def _run(self, capsys):
        """Capture the JSON the helper emits and the exit code."""
        mod = _load_helper()
        code = mod.main(["gigamate-repair"])
        out = capsys.readouterr().out
        return code, json.loads(out.strip())

    def test_success(self, capsys):
        mod = _load_helper()
        with patch.object(mod, "find_source",
                          return_value=("/usr/src/gigamate_acpi-3.1.0", "3.1.0")), \
             patch.object(mod, "dkms_available", return_value=True), \
             patch.object(mod, "dkms_registered", return_value=False), \
             patch.object(mod, "dkms_add", return_value=(True, "")), \
             patch.object(mod, "dkms_install", return_value=(True, "")), \
             patch.object(mod, "reload_module", return_value=(True, "")), \
             patch.object(mod, "module_loaded", return_value=True):
            code = mod.main(["gigamate-repair"])
            out = capsys.readouterr().out
            result = json.loads(out.strip())
        assert code == 0
        assert result["ok"] is True
        assert result["version"] == "3.1.0"

    def test_dkms_add_fails_falls_back_to_manual(self, capsys):
        mod = _load_helper()
        with patch.object(mod, "find_source",
                          return_value=("/usr/src/gigamate_acpi-3.1.0", "3.1.0")), \
             patch.object(mod, "dkms_available", return_value=True), \
             patch.object(mod, "dkms_registered", return_value=False), \
             patch.object(mod, "dkms_add", return_value=(False, "stale tree")), \
             patch.object(mod, "manual_install", return_value=(True, "")), \
             patch.object(mod, "reload_module", return_value=(True, "")), \
             patch.object(mod, "module_loaded", return_value=True):
            code = mod.main(["gigamate-repair"])
            out = capsys.readouterr().out
            result = json.loads(out.strip())
        assert code == 0
        assert result["ok"] is True
        assert any("manual build+install OK" in s for s in result["steps"])

    def test_no_source_reports_error(self, capsys):
        mod = _load_helper()
        with patch.object(mod, "find_source", return_value=(None, None)):
            code = mod.main(["gigamate-repair"])
            out = capsys.readouterr().out
            result = json.loads(out.strip())
        assert code == 2
        assert result["ok"] is False
        assert "not found" in result["error"]


class TestPolkit:
    def test_policy_is_valid_xml(self):
        import xml.etree.ElementTree as ET
        ET.parse(DATA_DIR / "org.gigamate.repair.policy")

    def test_rule_references_action(self):
        rule = (DATA_DIR / "50-gigamate-repair.rules").read_text()
        assert "org.gigamate.repair.driver" in rule