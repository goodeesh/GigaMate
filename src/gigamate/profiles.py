import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import usb.core

from .paths import CONFIG_DIR

BUILTIN_DATA_DIR = Path(__file__).parent / "profile_data"
USER_PROFILES_DIR = CONFIG_DIR / "profiles"

GIGABYTE_VIDS = {0x0414, 0x1044, 0x04D9}

OFF_CMD = bytes([0x08, 0x00, 0x01, 0x06, 0x00, 0x01, 0x01, 0xF2])


def _parse_hex_id(value) -> int:
    """Parse a VID/PID from int or hex string; 0 when absent/invalid."""
    if value is None or value == "":
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return 0


@dataclass
class AcpiConfig:
    """ACPI capabilities for a specific laptop model.

    Describes what ACPI/WMI features the laptop supports.
    All fields have safe defaults — only set what you know works.
    """
    has_fan_control: bool = False
    has_temperature: bool = False
    has_power_profiles: bool = False
    fan_count: int = 0
    fan_labels: List[str] = field(default_factory=list)
    sensor_labels: Dict[str, str] = field(default_factory=dict)
    profiles: Dict[str, Dict[str, str]] = field(default_factory=dict)
    backend: str = "module"
    # "verified" (confirmed on real hardware) or "experimental" (community
    # evidence, unconfirmed — shown only after the user opts in).
    confidence: str = "verified"

    @property
    def is_experimental(self) -> bool:
        return self.confidence == "experimental"

    @property
    def declares_profiles(self) -> bool:
        """Whether this model declares a working profile set."""
        return bool(self.has_power_profiles and self.profiles)


@dataclass
class DmiConfig:
    """DMI-based model key for laptops without a Gigabyte USB keyboard.

    Matching is exact on ``product_names``/``product_families`` and
    prefix-based on ``product_name_prefixes`` (e.g. all ``GIGABYTE AERO X16``
    variants). All matching is additionally gated on the DMI vendor being
    Gigabyte and the chassis being a laptop.
    """
    product_names: List[str] = field(default_factory=list)
    product_name_prefixes: List[str] = field(default_factory=list)
    product_families: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.product_names or self.product_name_prefixes
                    or self.product_families)


@dataclass
class DeviceProfile:
    vid: int = 0
    pid: int = 0
    name: str = ""
    interfaces: List[int] = field(default_factory=lambda: [1, 3])
    control_interface: int = 3
    colour_map: Dict[str, Dict[int, Tuple[int, int]]] = field(default_factory=dict)
    acpi: Optional[AcpiConfig] = None
    hotkeys: Dict[str, dict] = field(default_factory=dict)
    dmi: Optional[DmiConfig] = None
    version: int = 1

    @property
    def id(self) -> Tuple[int, int]:
        return (self.vid, self.pid)

    @property
    def has_dmi(self) -> bool:
        return self.dmi is not None and not self.dmi.empty

    @property
    def colour_names(self) -> List[str]:
        return list(self.colour_map.keys())

    def colour_byte(self, name: str, level: int) -> Tuple[int, int]:
        return self.colour_map[name][level]

    @property
    def full_map(self) -> Dict[str, int]:
        return {name: mapping[2][0] for name, mapping in self.colour_map.items()}

    @property
    def reverse_map(self) -> Dict[int, str]:
        return {v: k for k, v in self.full_map.items()}

    @property
    def has_acpi(self) -> bool:
        """Whether this profile has ACPI capabilities defined."""
        return self.acpi is not None

    @property
    def has_rgb(self) -> bool:
        """Whether this profile has keyboard RGB colour mapping."""
        return bool(self.colour_map)

    def to_dict(self) -> dict:
        cmap = {}
        for colour, levels in self.colour_map.items():
            cmap[colour] = {str(k): list(v) for k, v in levels.items()}
        result = {
            "version": self.version,
            "name": self.name,
        }
        if self.vid or self.pid:
            result["vid"] = f"0x{self.vid:04X}"
            result["pid"] = f"0x{self.pid:04X}"
        if self.has_dmi:
            result["dmi"] = {
                "product_names": list(self.dmi.product_names),
                "product_name_prefixes": list(self.dmi.product_name_prefixes),
                "product_families": list(self.dmi.product_families),
            }
        result["interfaces"] = list(self.interfaces)
        result["control_interface"] = self.control_interface
        result["colour_map"] = cmap
        if self.acpi is not None:
            result["acpi"] = {
                "has_fan_control": self.acpi.has_fan_control,
                "has_temperature": self.acpi.has_temperature,
                "has_power_profiles": self.acpi.has_power_profiles,
                "fan_count": self.acpi.fan_count,
                "fan_labels": list(self.acpi.fan_labels),
                "sensor_labels": dict(self.acpi.sensor_labels),
                "profiles": dict(self.acpi.profiles),
                "backend": self.acpi.backend,
                "confidence": self.acpi.confidence,
            }
        if self.hotkeys:
            result["hotkeys"] = dict(self.hotkeys)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceProfile":
        vid = _parse_hex_id(d.get("vid"))
        pid = _parse_hex_id(d.get("pid"))
        cmap = {}
        for colour, levels in d.get("colour_map", {}).items():
            cmap[colour] = {int(k): tuple(v) for k, v in levels.items()}

        acpi = None
        if "acpi" in d:
            a = d["acpi"]
            acpi = AcpiConfig(
                has_fan_control=a.get("has_fan_control", False),
                has_temperature=a.get("has_temperature", False),
                has_power_profiles=a.get("has_power_profiles", False),
                fan_count=int(a.get("fan_count", 0)),
                fan_labels=list(a.get("fan_labels", [])),
                sensor_labels=dict(a.get("sensor_labels", {})),
                profiles=dict(a.get("profiles", {})),
                backend=str(a.get("backend", "module")),
                confidence=str(a.get("confidence", "verified")),
            )

        dmi = None
        if "dmi" in d and isinstance(d["dmi"], dict):
            dm = d["dmi"]
            dmi = DmiConfig(
                product_names=list(dm.get("product_names", [])),
                product_name_prefixes=list(dm.get("product_name_prefixes", [])),
                product_families=list(dm.get("product_families", [])),
            )

        return cls(
            vid=vid,
            pid=pid,
            name=d.get("name", f"{vid:04X}:{pid:04X}"),
            version=d.get("version", 1),
            interfaces=list(d.get("interfaces", [1, 3])),
            control_interface=int(d.get("control_interface", 3)),
            colour_map=cmap,
            acpi=acpi,
            hotkeys=dict(d.get("hotkeys", {})),
            dmi=dmi,
        )


def _load_profiles_from(directory: Path, kind: str,
                        dmi: bool) -> List[DeviceProfile]:
    """Load profiles from a directory. When ``dmi`` is False, USB profiles only
    (``dmi_*.json`` files are skipped); when True, DMI profiles only."""
    found: List[DeviceProfile] = []
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.json")):
        is_dmi_file = path.name.startswith("dmi_")
        if is_dmi_file != dmi:
            continue
        try:
            data = json.loads(path.read_text())
            profile = DeviceProfile.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError) as exc:
            print(f"Warning: skipping {kind} profile {path.name}: {exc}", file=sys.stderr)
            continue
        found.append(profile)
    return found


def _usb_dict(profiles: List[DeviceProfile]) -> Dict[Tuple[int, int], DeviceProfile]:
    """Key USB profiles by (vid, pid), skipping keyless/DMI-only entries."""
    out: Dict[Tuple[int, int], DeviceProfile] = {}
    for profile in profiles:
        if profile.vid or profile.pid:
            out[profile.id] = profile
    return out


def load_builtin_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    return _usb_dict(_load_profiles_from(BUILTIN_DATA_DIR, "built-in", dmi=False))


def load_user_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    return _usb_dict(_load_profiles_from(USER_PROFILES_DIR, "user", dmi=False))


def load_builtin_dmi_profiles() -> List[DeviceProfile]:
    return _load_profiles_from(BUILTIN_DATA_DIR, "built-in", dmi=True)


def load_user_dmi_profiles() -> List[DeviceProfile]:
    return _load_profiles_from(USER_PROFILES_DIR, "user", dmi=True)


def all_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    profiles = load_builtin_profiles()
    for key, profile in load_user_profiles().items():
        profiles[key] = profile
    return profiles


def detect_device() -> Optional[Tuple[int, int]]:
    try:
        known = all_profiles()
        for dev in usb.core.find(find_all=True):
            if dev is None:
                continue
            try:
                vid = dev.idVendor
                pid = dev.idProduct
            except (AttributeError, usb.core.USBError, ValueError):
                continue
            if vid == 0x0414:
                return (vid, pid)
            if vid in (0x1044, 0x04D9):
                # 0x1044 (Chu Yuen) and 0x04D9 (Holtek) are shared ODM VIDs also
                # used by non-Gigabyte peripherals. Only treat as Gigabyte when
                # the (vid, pid) is a known profile or the device reports a
                # Gigabyte manufacturer string.
                if (vid, pid) in known:
                    return (vid, pid)
                try:
                    mfr = (dev.manufacturer or "").upper()
                    if "GIGABYTE" in mfr or "AORUS" in mfr:
                        return (vid, pid)
                except Exception:
                    pass
        for dev in usb.core.find(find_all=True):
            if dev is None:
                continue
            try:
                mfr = (dev.manufacturer or "").upper()
                if "GIGABYTE" in mfr:
                    return (dev.idVendor, dev.idProduct)
            except Exception:
                continue
    except Exception:
        pass
    return None


def get_dmi_product_name() -> Optional[str]:
    """Read laptop model name from sysfs DMI tables if available."""
    dmi_path = Path("/sys/class/dmi/id")
    try:
        product_file = dmi_path / "product_name"
        if product_file.is_file():
            name = product_file.read_text().strip()
            if name and name.lower() not in ("", "none", "to be filled by o.e.m.", "default string"):
                return name
    except (OSError, IOError, PermissionError):
        pass
    return None


def get_dmi_vendor() -> Optional[str]:
    """Read the system vendor (OEM) string from sysfs DMI tables if available."""
    dmi_path = Path("/sys/class/dmi/id")
    try:
        vendor_file = dmi_path / "sys_vendor"
        if vendor_file.is_file():
            vendor = vendor_file.read_text().strip()
            if vendor and vendor.lower() not in ("", "none", "to be filled by o.e.m.", "default string"):
                return vendor
    except (OSError, IOError, PermissionError):
        pass
    return None


def get_dmi_chassis_type() -> Optional[int]:
    """Read the SMBIOS chassis type from sysfs, or None on failure."""
    chassis_file = Path("/sys/class/dmi/id/chassis_type")
    try:
        if chassis_file.is_file():
            return int(chassis_file.read_text().strip())
    except (OSError, ValueError, PermissionError):
        pass
    return None


def get_dmi_product_family() -> Optional[str]:
    """Read the product family string from sysfs DMI tables if available."""
    dmi_path = Path("/sys/class/dmi/id")
    try:
        family_file = dmi_path / "product_family"
        if family_file.is_file():
            family = family_file.read_text().strip()
            if family and family.lower() not in (
                "", "none", "unknown", "to be filled by o.e.m.", "default string"
            ):
                return family
    except (OSError, IOError, PermissionError):
        pass
    return None



def resolve_profile(vid: Optional[int] = None, pid: Optional[int] = None) -> Optional[DeviceProfile]:
    if vid is None or pid is None:
        detected = detect_device()
        if detected is None:
            return None
        vid, pid = detected
    return all_profiles().get((vid, pid))


def has_verified_profiles(profile: Optional[DeviceProfile]) -> bool:
    """Whether a device profile authoritatively declares working power profiles.

    Profile switching is only exposed to users when a matching model profile
    (built-in or user-supplied) declares ``acpi.has_power_profiles`` **and** a
    non-empty ``acpi.profiles`` set. Backend detection alone ("the AMW0 WMI
    interface answers sensor reads") never enables the profile UI: different
    Gigabyte EC generations implement different commands, and an EC can accept
    a profile write without acting on it. ``has_power_profiles`` is therefore a
    per-model contract, not something a probe can prove.
    """
    return bool(
        profile is not None
        and profile.has_acpi
        and profile.acpi is not None
        and profile.acpi.has_power_profiles
        and bool(profile.acpi.profiles)
    )


# Gigabyte DMI vendor tokens and desktop chassis types (mirrors capabilities.py,
# which imports from this module — keep in sync).
_GIGABYTE_VENDOR_TOKENS = ("GIGABYTE", "GIGA-BYTE", "AORUS")
_DESKTOP_CHASSIS = {3, 4, 5, 6, 7, 17, 23, 24}


def is_gigabyte_laptop_dmi() -> bool:
    """Whether DMI identifies a Gigabyte *laptop* (not a desktop board)."""
    vendor = (get_dmi_vendor() or "").upper()
    if not any(token in vendor for token in _GIGABYTE_VENDOR_TOKENS):
        return False
    return get_dmi_chassis_type() not in _DESKTOP_CHASSIS


def _dmi_name_match(profile: DeviceProfile, product_name: str) -> bool:
    dmi = profile.dmi
    if dmi is None:
        return False
    if product_name and product_name in dmi.product_names:
        return True
    return bool(product_name) and any(
        product_name.startswith(prefix) for prefix in dmi.product_name_prefixes)


def _dmi_family_match(profile: DeviceProfile, product_family: str) -> bool:
    dmi = profile.dmi
    if dmi is None or not product_family:
        return False
    return product_family in dmi.product_families


def match_dmi_profile(profiles: Optional[List[DeviceProfile]] = None,
                      product_name: Optional[str] = None,
                      product_family: Optional[str] = None) -> Optional[DeviceProfile]:
    """Return the best DMI-keyed profile match, or None.

    Specificity first: an exact ``product_name`` / prefix match beats a
    ``product_family`` match. ``product_name``/``product_family`` default to the
    sysfs values.
    """
    profiles = profiles or []
    if product_name is None:
        product_name = get_dmi_product_name() or ""
    if product_family is None:
        product_family = get_dmi_product_family() or ""
    for profile in profiles:
        if _dmi_name_match(profile, product_name):
            return profile
    for profile in profiles:
        if _dmi_family_match(profile, product_family):
            return profile
    return None


def _best_dmi_profile(profiles: List[DeviceProfile]) -> Optional[DeviceProfile]:
    """Most specific DMI match within a candidate group (see match_dmi_profile)."""
    return match_dmi_profile(profiles)


@dataclass
class ModelMatch:
    """Resolved model profile that declares a working profile set."""
    profile: DeviceProfile
    source: str        # "user-usb" | "builtin-usb" | "user-dmi" | "builtin-dmi"
    experimental: bool

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def clean_name(self) -> str:
        """Profile name without a trailing "— experimental" suffix."""
        return self.profile.name.split("—")[0].strip()


def resolve_model(vid: Optional[int] = None,
                  pid: Optional[int] = None) -> Optional[ModelMatch]:
    """Resolve the model profile that gates power-profile switching.

    Precedence: user USB → built-in USB → user DMI → built-in DMI. The first
    entry that *declares* a profile set wins for ACPI; a USB-keyed profile with
    no declared ACPI never suppresses a later DMI profile's profiles. RGB
    resolution is separate (``resolve_profile``). Returns None when no matching
    profile declares profiles.
    """
    if vid is None or pid is None:
        detected = detect_device()
        if detected is not None:
            vid, pid = detected

    candidates: List[Tuple[str, DeviceProfile]] = []
    if vid and pid:
        user_usb = load_user_profiles().get((vid, pid))
        if user_usb is not None:
            candidates.append(("user-usb", user_usb))
        builtin_usb = load_builtin_profiles().get((vid, pid))
        if builtin_usb is not None:
            candidates.append(("builtin-usb", builtin_usb))
    if is_gigabyte_laptop_dmi():
        user_dmi = _best_dmi_profile(load_user_dmi_profiles())
        if user_dmi is not None:
            candidates.append(("user-dmi", user_dmi))
        builtin_dmi = _best_dmi_profile(load_builtin_dmi_profiles())
        if builtin_dmi is not None:
            candidates.append(("builtin-dmi", builtin_dmi))

    for source, profile in candidates:
        if has_verified_profiles(profile):
            return ModelMatch(profile=profile, source=source,
                              experimental=bool(profile.acpi and profile.acpi.is_experimental))
    return None


def experimental_profiles_enabled(cfg: Optional[dict]) -> bool:
    """Whether the user has opted into experimental (unconfirmed) profiles."""
    try:
        return bool((cfg or {}).get("experimental_profiles_enabled", False))
    except Exception:
        return False


def profile_usable(match: Optional[ModelMatch], cfg: Optional[dict]) -> bool:
    """Whether profile switching may be offered for this match under ``cfg``."""
    if match is None:
        return False
    if not match.experimental:
        return True
    return experimental_profiles_enabled(cfg)


def _profile_filename(profile: DeviceProfile) -> str:
    """Filename for a user profile: DMI-keyed files use the dmi_ prefix."""
    if profile.has_dmi and not (profile.vid or profile.pid):
        slug = "".join(
            c if c.isalnum() else "_"
            for c in (profile.name or "model").lower()
        ).strip("_")
        return f"dmi_{slug or 'model'}.json"
    return f"{profile.vid:04X}_{profile.pid:04X}.json"


def save_user_profile(profile: DeviceProfile) -> Path:
    """Persist a user profile atomically after validation."""
    errors = validate_profile(profile)
    if errors:
        raise ValueError("Invalid profile: " + "; ".join(errors))
    USER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_PROFILES_DIR / _profile_filename(profile)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(profile.to_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def validate_profile(profile: DeviceProfile) -> List[str]:
    """Validate a device profile and return a list of warnings/errors.

    Returns an empty list if the profile is valid.
    """
    errors: List[str] = []

    if not profile.name:
        errors.append("Profile name is empty")

    has_usb_key = bool(profile.vid or profile.pid)
    if not has_usb_key and not profile.has_dmi:
        errors.append("Profile needs a USB (vid/pid) or a DMI key")

    if not (0x0000 <= profile.vid <= 0xFFFF):
        errors.append(f"Invalid VID: {profile.vid:04X}")

    if not (0x0000 <= profile.pid <= 0xFFFF):
        errors.append(f"Invalid PID: {profile.pid:04X}")

    if profile.has_rgb and not has_usb_key:
        errors.append("RGB profiles require a USB vid/pid")

    if profile.has_rgb and not profile.interfaces:
        errors.append("No USB interfaces specified")

    if profile.has_rgb:
        for colour_name, levels in profile.colour_map.items():
            if not colour_name:
                errors.append("Colour name is empty")
            if not isinstance(levels, dict):
                errors.append(f"Colour '{colour_name}' levels are not a mapping")
                continue
            for level_key in (0, 1, 2):
                if level_key not in levels:
                    errors.append(f"Colour '{colour_name}' missing brightness level {level_key}")
                    continue
                try:
                    byte5, byte4 = levels[level_key]
                    byte5 = int(byte5)
                    byte4 = int(byte4)
                except (TypeError, ValueError):
                    errors.append(f"Colour '{colour_name}' level {level_key}: invalid byte pair")
                    continue
                if not (0x00 <= byte5 <= 0xFF):
                    errors.append(f"Colour '{colour_name}' level {level_key}: byte5 out of range")
                if not (0x00 <= byte4 <= 0xFF):
                    errors.append(f"Colour '{colour_name}' level {level_key}: byte4 out of range")

    if profile.has_acpi:
        acpi = profile.acpi
        if acpi.has_power_profiles:
            for pid_str in acpi.profiles:
                try:
                    p = int(pid_str)
                    if not (0 <= p <= 3):
                        errors.append(f"ACPI profile ID {pid_str} out of range (0-3)")
                except ValueError:
                    errors.append(f"ACPI profile ID '{pid_str}' is not a valid integer")
                if "name" not in acpi.profiles[pid_str]:
                    errors.append(f"ACPI profile {pid_str} missing 'name' field")
        if acpi.confidence not in ("verified", "experimental"):
            errors.append(f"Unknown ACPI confidence: {acpi.confidence}")
        if acpi.is_experimental and not acpi.declares_profiles:
            errors.append("Experimental profiles require has_power_profiles and a profiles set")
        if acpi.backend not in ("module", "acpi_call"):
            errors.append(f"Unknown ACPI backend: {acpi.backend}")

    if profile.hotkeys:
        from .hotkeys import KNOWN_ACTIONS, HotkeySpec

        for action, entry in profile.hotkeys.items():
            if action not in KNOWN_ACTIONS:
                errors.append(f"Unknown hotkey action: '{action}'")
                continue
            if not isinstance(entry, dict):
                errors.append(f"Invalid hotkey '{action}': spec must be a mapping")
                continue
            key_name = entry.get("key_name")
            if key_name is not None and (
                    not isinstance(key_name, str) or not key_name.strip()):
                errors.append(f"Invalid hotkey '{action}': key_name must be a non-empty string")
            try:
                HotkeySpec.from_dict(action, entry)
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"Invalid hotkey '{action}': {exc}")

    return errors


def _test_interface(dev, iface: int) -> bool:
    from .protocol import make_command
    try:
        cmd = make_command(0x01, 0x06, 0x00, 0x01)
        dev.ctrl_transfer(0x21, 0x09, 0x0300, iface, cmd)
        return True
    except usb.core.USBError:
        return False


def _send_raw(dev, byte5: int, byte4: int, iface: int):
    from .protocol import make_command
    cmd = make_command(0x01, 0x06, byte4, byte5)
    dev.ctrl_transfer(0x21, 0x09, 0x0300, iface, cmd)


def calibrate(dev, vid: int, pid: int) -> Optional[DeviceProfile]:
    from .protocol import make_command

    print()
    print("=" * 60)
    print("  GigaMate — Keyboard RGB Calibration")
    print("=" * 60)
    print()
    print(f"Detected: VID={vid:04X} PID={pid:04X}")
    print()

    interfaces_to_detach = [1]
    working_iface = None

    print("Step 0: Finding control interface")
    for iface in [3, 0, 1, 2]:
        if _test_interface(dev, iface):
            print(f"  Interface {iface}: responds ✓")
            working_iface = iface
            break
        print(f"  Interface {iface}: no response")
        time.sleep(0.05)

    if working_iface is None:
        print("\nNo USB interface responded. Calibration aborted.")
        return None

    if working_iface not in interfaces_to_detach:
        interfaces_to_detach.append(working_iface)
    if 3 not in interfaces_to_detach and 3 != working_iface:
        interfaces_to_detach.append(3)

    for iface in interfaces_to_detach:
        try:
            if dev.is_kernel_driver_active(iface):
                dev.detach_kernel_driver(iface)
                print(f"  Detached kernel driver from interface {iface}")
        except (usb.core.USBError, NotImplementedError):
            pass

    time.sleep(0.2)
    print()

    PHASE1_BRIGHTNESS = 0x32
    found = {}

    print("Step 1: Identifying visible colours at medium brightness")
    print("  For each sample, type the colour name (lowercase, underscore for")
    print("  multi-word, e.g. 'light_purple').")
    print("  Enter = skip this byte, 'q' = quit, 'done' = finish early")
    print()

    for byte5 in range(0x01, 0x09):
        try:
            _send_raw(dev, byte5, PHASE1_BRIGHTNESS, working_iface)
        except usb.core.USBError:
            print(f"  byte5=0x{byte5:02X}: send failed, skipping")
            continue

        time.sleep(0.3)
        prompt = f"  Colour at byte5=0x{byte5:02X} (medium)? "
        ans = input(prompt).strip().lower().replace(" ", "_")

        if ans in ("q", "quit"):
            _send_raw(dev, 0x01, 0x00, working_iface)
            return None
        if ans in ("done", "d"):
            break
        if not ans or ans in ("skip", "none", "n"):
            continue

        found[byte5] = ans

    if not found:
        print("\nNo colours were identified. Calibration aborted.")
        _send_raw(dev, 0x01, 0x00, working_iface)
        return None

    print()
    print(f"  Found {len(found)} colour(s): {', '.join(found.values())}")
    print()

    colour_map: Dict[str, Dict[int, Tuple[int, int]]] = {}

    print("Step 2: Checking for hue variation at dim (0x19) and full (0x64)")
    print("  For each colour, we'll send dim then full and ask if it's the same.")
    print()

    for byte5, base_name in sorted(found.items()):
        dim_name = base_name
        full_name = base_name

        try:
            _send_raw(dev, byte5, 0x19, working_iface)
        except usb.core.USBError:
            continue
        time.sleep(0.3)
        ans = input(f"  '{base_name}' at dim (0x19) — same colour? [Y/n] ").strip().lower()
        if ans == "n":
            dim_name = input("    Name this dim colour: ").strip().lower().replace(" ", "_")
            if not dim_name:
                dim_name = base_name

        try:
            _send_raw(dev, byte5, 0x64, working_iface)
        except usb.core.USBError:
            continue
        time.sleep(0.3)
        ans = input(f"  '{base_name}' at full (0x64) — same colour? [Y/n] ").strip().lower()
        if ans == "n":
            full_name = input("    Name this full colour: ").strip().lower().replace(" ", "_")
            if not full_name:
                full_name = base_name

        colour_map.setdefault(base_name, {})[1] = (byte5, PHASE1_BRIGHTNESS)
        colour_map.setdefault(base_name, {})[0] = (byte5, 0x00)

        if dim_name != base_name:
            colour_map.setdefault(dim_name, {})[1] = (byte5, 0x19)
            colour_map.setdefault(dim_name, {})[0] = (byte5, 0x00)

        if full_name != base_name:
            colour_map.setdefault(full_name, {})[2] = (byte5, 0x64)
            colour_map.setdefault(full_name, {})[0] = (byte5, 0x00)

            probe_points = [0x4B, 0x5A]
            found_dim = False
            for bp in probe_points:
                try:
                    _send_raw(dev, byte5, bp, working_iface)
                except usb.core.USBError:
                    continue
                time.sleep(0.3)
                ans = input(f"    At 0x{bp:02X} — same as '{full_name}'? [Y/n] ").strip().lower()
                if ans != "n":
                    colour_map.setdefault(full_name, {})[1] = (byte5, bp)
                    found_dim = True
                    break
            if not found_dim:
                colour_map.setdefault(full_name, {})[1] = (byte5, 0x64)

        if dim_name == base_name and full_name == base_name:
            colour_map[base_name][2] = (byte5, 0x64)
            colour_map[base_name][1] = (byte5, 0x19)

    for name in list(colour_map):
        levels = colour_map[name]
        if 2 not in levels:
            any_byte5 = next(iter(levels.values()))[0]
            levels[2] = (any_byte5, 0x64)
        if 1 not in levels:
            any_byte5 = next(iter(levels.values()))[0]
            levels[1] = (any_byte5, 0x19)
        if 0 not in levels:
            any_byte5 = next(iter(levels.values()))[0]
            levels[0] = (any_byte5, 0x00)

    _send_raw(dev, 0x01, 0x00, working_iface)

    print()
    print("Step 3: Model name")
    default_name = f"Custom {vid:04X}:{pid:04X}"
    model_name = input(f"  Model name (e.g. 'Gigabyte Aorus 15BKF')\n  [{default_name}]: ").strip()
    if not model_name:
        model_name = default_name

    profile = DeviceProfile(
        vid=vid,
        pid=pid,
        name=model_name,
        interfaces=sorted(set(interfaces_to_detach)),
        control_interface=working_iface,
        colour_map=colour_map,
    )

    print()
    print("=" * 60)
    print("  Calibration complete!")
    print("=" * 60)
    print(f"\n  {len(colour_map)} colour(s) mapped: {', '.join(sorted(colour_map.keys()))}")
    print()
    print(f"  Saved to: ~/.config/gigamate/profiles/")
    print(f"  To contribute: run 'gigamate profile contribute'")
    print()

    return profile
