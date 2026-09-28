"""Tests for GigaMate 4.0 — DMI-keyed profiles and the experimental tier."""

import argparse

import pytest
from unittest.mock import MagicMock, patch

from gigamate import cli
from gigamate import profiles as P
from gigamate.profiles import (
    AcpiConfig,
    DeviceProfile,
    DmiConfig,
    ModelMatch,
    experimental_profiles_enabled,
    match_dmi_profile,
    profile_usable,
    resolve_model,
    validate_profile,
)


def _acpi(profiles=None, confidence="verified"):
    return AcpiConfig(
        has_fan_control=True,
        has_temperature=True,
        has_power_profiles=bool(profiles),
        profiles=profiles or {},
        confidence=confidence,
    )


def _dmi_profile(family=None, prefix=None, confidence="experimental"):
    return DeviceProfile(
        name="DMI Test",
        dmi=DmiConfig(product_name_prefixes=[prefix] if prefix else [],
                      product_families=[family] if family else []),
        acpi=_acpi({"0": {"name": "Eco"}, "1": {"name": "Balanced"},
                    "2": {"name": "Boost"}}, confidence),
    )


class TestDmiMatching:
    def test_family_match(self):
        prof = _dmi_profile(family="GIGABYTE GAMING")
        assert match_dmi_profile([prof], product_name="GIGABYTE GAMING A16 CMH",
                                 product_family="GIGABYTE GAMING") is prof

    def test_prefix_match(self):
        prof = _dmi_profile(prefix="GIGABYTE AERO X16")
        assert match_dmi_profile([prof], product_name="GIGABYTE AERO X16 1VH",
                                 product_family="GIGABYTE AERO") is prof

    def test_prefix_beats_family(self):
        family = _dmi_profile(family="GIGABYTE AERO")
        prefix = _dmi_profile(prefix="GIGABYTE AERO X16")
        got = match_dmi_profile([family, prefix], product_name="GIGABYTE AERO X16 1VH",
                                product_family="GIGABYTE AERO")
        assert got is prefix

    def test_no_match(self):
        prof = _dmi_profile(family="GIGABYTE GAMING")
        assert match_dmi_profile([prof], product_name="Dell XPS 15",
                                 product_family="XPS") is None


class TestResolveModel:
    def _patch(self, monkeypatch, *, user_usb=None, builtin_usb=None,
               user_dmi=(), builtin_dmi=(), gigabyte=True):
        monkeypatch.setattr(P, "is_gigabyte_laptop_dmi", lambda: gigabyte)
        # Simulate a GAMING-family machine so DMI matching is deterministic.
        monkeypatch.setattr(P, "get_dmi_product_name", lambda: "GIGABYTE GAMING A16 CMH")
        monkeypatch.setattr(P, "get_dmi_product_family", lambda: "GIGABYTE GAMING")
        monkeypatch.setattr(P, "load_user_profiles", lambda: user_usb or {})
        monkeypatch.setattr(P, "load_builtin_profiles", lambda: builtin_usb or {})
        monkeypatch.setattr(P, "load_user_dmi_profiles", lambda: list(user_dmi))
        monkeypatch.setattr(P, "load_builtin_dmi_profiles", lambda: list(builtin_dmi))

    def test_user_usb_wins(self, monkeypatch):
        usb = DeviceProfile(vid=0x0414, pid=0x9999, name="USB",
                            acpi=_acpi({"0": {"name": "Quiet"}}))
        dmi = _dmi_profile(family="GIGABYTE GAMING")
        self._patch(monkeypatch, user_usb={(0x0414, 0x9999): usb}, user_dmi=[dmi])
        m = resolve_model(0x0414, 0x9999)
        assert m.source == "user-usb" and m.profile is usb and m.experimental is False

    def test_usb_without_acpi_falls_back_to_dmi(self, monkeypatch):
        usb = DeviceProfile(vid=0x0414, pid=0x9999, name="USB",
                            colour_map={"red": {0: (1, 0), 1: (1, 25), 2: (1, 100)}})
        dmi = _dmi_profile(family="GIGABYTE GAMING")
        self._patch(monkeypatch, user_usb={(0x0414, 0x9999): usb}, user_dmi=[dmi])
        m = resolve_model(0x0414, 0x9999)
        assert m.source == "user-dmi" and m.experimental is True

    def test_builtin_usb_before_user_dmi(self, monkeypatch):
        builtin = DeviceProfile(vid=0x0414, pid=0x8105, name="Builtin",
                                acpi=_acpi({"0": {"name": "Quiet"}}))
        dmi = _dmi_profile(family="GIGABYTE GAMING")
        self._patch(monkeypatch, builtin_usb={(0x0414, 0x8105): builtin}, user_dmi=[dmi])
        m = resolve_model(0x0414, 0x8105)
        assert m.source == "builtin-usb"

    def test_dmi_only_when_no_usb(self, monkeypatch):
        dmi = _dmi_profile(family="GIGABYTE GAMING")
        self._patch(monkeypatch, builtin_dmi=[dmi])
        m = resolve_model()
        assert m.source == "builtin-dmi" and m.experimental is True

    def test_none_when_not_gigabyte(self, monkeypatch):
        dmi = _dmi_profile(family="GIGABYTE GAMING")
        self._patch(monkeypatch, user_dmi=[dmi], gigabyte=False)
        assert resolve_model() is None


class TestConsent:
    def test_verified_always_usable(self):
        m = ModelMatch(_dmi_profile(family="X", confidence="verified"), "builtin-dmi", False)
        assert profile_usable(m, {}) is True
        assert profile_usable(m, {"experimental_profiles_enabled": False}) is True

    def test_experimental_requires_consent(self):
        m = ModelMatch(_dmi_profile(family="X"), "builtin-dmi", True)
        assert profile_usable(m, {}) is False
        assert profile_usable(m, {"experimental_profiles_enabled": True}) is True

    def test_none_not_usable(self):
        assert profile_usable(None, {"experimental_profiles_enabled": True}) is False

    def test_config_default(self):
        assert experimental_profiles_enabled({}) is False


class TestSchema:
    def test_dmi_profile_valid(self):
        assert validate_profile(_dmi_profile(family="GIGABYTE GAMING")) == []

    def test_needs_usb_or_dmi_key(self):
        p = DeviceProfile(name="NoKey", acpi=_acpi({"0": {"name": "Quiet"}}))
        assert any("USB (vid/pid) or a DMI key" in e for e in validate_profile(p))

    def test_experimental_requires_profiles(self):
        p = DeviceProfile(name="X", dmi=DmiConfig(product_families=["GIGABYTE GAMING"]),
                          acpi=AcpiConfig(confidence="experimental"))
        assert any("Experimental profiles require" in e for e in validate_profile(p))

    def test_bad_confidence(self):
        p = DeviceProfile(name="X", dmi=DmiConfig(product_families=["X"]),
                          acpi=AcpiConfig(has_power_profiles=True,
                                          profiles={"0": {"name": "A"}},
                                          confidence="maybe"))
        assert any("confidence" in e for e in validate_profile(p))

    def test_dmi_filename(self):
        assert P._profile_filename(_dmi_profile(family="GIGABYTE GAMING")).startswith("dmi_")
        usb = DeviceProfile(vid=0x0414, pid=0x8105, name="USB")
        assert P._profile_filename(usb) == "0414_8105.json"

    def test_builtin_dmi_profiles_load_and_validate(self):
        dmi = P.load_builtin_dmi_profiles()
        names = {p.name for p in dmi}
        assert "Gigabyte GAMING A16 / A18 (family)" in names
        assert "Gigabyte AERO X16 (family)" in names
        for p in dmi:
            if p.name == "Gigabyte GAMING A16 / A18 (family)":
                assert p.acpi.confidence == "verified"
            else:
                assert p.acpi.confidence == "experimental"
            assert validate_profile(p) == []

    def test_builtin_usb_profiles_exclude_dmi(self):
        # USB loader must not accidentally include dmi_*.json files.
        for pid, prof in P.load_builtin_profiles().items():
            assert not prof.has_dmi or prof.vid or prof.pid


def _args(vid=None, pid=None):
    return argparse.Namespace(vid=vid, pid=pid, action=None, name=None)


def _match(profile, experimental):
    return ModelMatch(profile, "builtin-dmi" if experimental else "builtin-usb", experimental)


class TestExperimentalCli:
    def test_status_off(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_model_match", lambda a: None)
        cli.cmd_profile_experimental(_args())
        assert "enabled:  off" in capsys.readouterr().out

    def test_on_off_toggle(self, monkeypatch):
        monkeypatch.setattr(cli, "_model_match", lambda a: None)
        args = _args()
        cli.cmd_profile_experimental(type("A", (), {"value": "on"})())
        from gigamate.config import load as load_config
        assert load_config()["experimental_profiles_enabled"] is True
        cli.cmd_profile_experimental(type("A", (), {"value": "off"})())
        assert load_config()["experimental_profiles_enabled"] is False

    def test_set_blocks_unconsented_experimental(self, monkeypatch, capsys):
        prof = _dmi_profile(family="GIGABYTE GAMING")
        monkeypatch.setattr(cli, "resolve_model", lambda *a, **k: _match(prof, True))
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_set(_args(), "balanced")
        out = capsys.readouterr().out
        assert "EXPERIMENTAL and not enabled" in out
        assert "profile experimental on" in out

    def test_set_allows_consented_experimental(self, monkeypatch, capsys):
        from gigamate.config import load as load_config, save as save_config

        prof = _dmi_profile(family="GIGABYTE GAMING")
        monkeypatch.setattr(cli, "resolve_model", lambda *a, **k: _match(prof, True))
        cfg = load_config()
        cfg["experimental_profiles_enabled"] = True
        save_config(cfg)
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls, \
             patch("gigamate.cli._record_profile_and_sync") as mock_record:
            mock_ctrl = mock_ctrl_cls.return_value
            mock_ctrl.available = True
            mock_ctrl.set_profile.return_value = True
            cli.cmd_profile_set(_args(), "balanced")
        out = capsys.readouterr().out
        assert "EXPERIMENTAL on this model" in out
        assert "Power profile set to: Balanced  (1)" in out
        mock_record.assert_called_once_with(1)

    def test_three_entry_model_rejects_out_of_set(self, monkeypatch, capsys):
        from gigamate.config import load as load_config, save as save_config
        prof = _dmi_profile(family="GIGABYTE GAMING")
        cfg = load_config(); cfg["experimental_profiles_enabled"] = True; save_config(cfg)
        monkeypatch.setattr(cli, "resolve_model", lambda *a, **k: _match(prof, True))
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = True
            with pytest.raises(SystemExit):
                cli.cmd_profile_set(_args(), "3")
        assert "not available on this model" in capsys.readouterr().out

    def test_report_shape(self, monkeypatch, capsys):
        prof = _dmi_profile(family="GIGABYTE GAMING")
        monkeypatch.setattr(cli, "resolve_model", lambda *a, **k: _match(prof, True))
        monkeypatch.setattr(cli, "get_dmi_product_name", lambda: "GIGABYTE GAMING A16 CMH")
        monkeypatch.setattr(cli, "get_dmi_product_family", lambda: "GIGABYTE GAMING")
        monkeypatch.setattr(cli, "get_dmi_vendor", lambda: "GIGABYTE")
        with patch("gigamate.cli.AcpiController") as mock_ctrl_cls:
            mock_ctrl_cls.return_value.available = False
            cli.cmd_profile_report(_args())
        out = capsys.readouterr().out
        assert "GigaMate — model report" in out
        assert "GIGABYTE GAMING A16 CMH" in out
        assert "experimental (unconfirmed)" in out
        assert "github.com/goodeesh/GigaMate/issues" in out
