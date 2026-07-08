import json
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


@dataclass
class DeviceProfile:
    vid: int
    pid: int
    name: str
    interfaces: List[int] = field(default_factory=lambda: [1, 3])
    control_interface: int = 3
    colour_map: Dict[str, Dict[int, Tuple[int, int]]] = field(default_factory=dict)
    acpi: Optional[AcpiConfig] = None
    version: int = 1

    @property
    def id(self) -> Tuple[int, int]:
        return (self.vid, self.pid)

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
            "vid": f"0x{self.vid:04X}",
            "pid": f"0x{self.pid:04X}",
            "interfaces": list(self.interfaces),
            "control_interface": self.control_interface,
            "colour_map": cmap,
        }
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
            }
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceProfile":
        vid = d["vid"] if isinstance(d["vid"], int) else int(d["vid"], 16)
        pid = d["pid"] if isinstance(d["pid"], int) else int(d["pid"], 16)
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
        )


def load_builtin_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    profiles = {}
    if not BUILTIN_DATA_DIR.is_dir():
        return profiles
    for path in sorted(BUILTIN_DATA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            profile = DeviceProfile.from_dict(data)
            profiles[profile.id] = profile
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            print(f"Warning: skipping built-in profile {path.name}: {exc}", file=sys.stderr)
    return profiles


def load_user_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    profiles = {}
    if not USER_PROFILES_DIR.is_dir():
        return profiles
    for path in sorted(USER_PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            profile = DeviceProfile.from_dict(data)
            profiles[profile.id] = profile
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            print(f"Warning: skipping user profile {path.name}: {exc}", file=sys.stderr)
    return profiles


def all_profiles() -> Dict[Tuple[int, int], DeviceProfile]:
    profiles = load_builtin_profiles()
    for key, profile in load_user_profiles().items():
        profiles[key] = profile
    return profiles


def detect_device() -> Optional[Tuple[int, int]]:
    try:
        for dev in usb.core.find(find_all=True):
            if dev is None:
                continue
            try:
                vid = dev.idVendor
                pid = dev.idProduct
            except (AttributeError, usb.core.USBError):
                continue
            if vid in GIGABYTE_VIDS:
                return (vid, pid)
        for dev in usb.core.find(find_all=True):
            if dev is None:
                continue
            try:
                mfr = (dev.manufacturer or "").upper()
                if "GIGABYTE" in mfr:
                    return (dev.idVendor, dev.idProduct)
            except (AttributeError, usb.core.USBError):
                continue
    except usb.core.USBError:
        pass
    return None


def resolve_profile(vid: Optional[int] = None, pid: Optional[int] = None) -> Optional[DeviceProfile]:
    if vid is None or pid is None:
        detected = detect_device()
        if detected is None:
            return None
        vid, pid = detected
    return all_profiles().get((vid, pid))


def save_user_profile(profile: DeviceProfile) -> Path:
    USER_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_PROFILES_DIR / f"{profile.vid:04X}_{profile.pid:04X}.json"
    path.write_text(json.dumps(profile.to_dict(), indent=2) + "\n")
    return path


def validate_profile(profile: DeviceProfile) -> List[str]:
    """Validate a device profile and return a list of warnings/errors.

    Returns an empty list if the profile is valid.
    """
    errors: List[str] = []

    if not profile.name:
        errors.append("Profile name is empty")

    if not (0x0000 <= profile.vid <= 0xFFFF):
        errors.append(f"Invalid VID: {profile.vid:04X}")

    if not (0x0000 <= profile.pid <= 0xFFFF):
        errors.append(f"Invalid PID: {profile.pid:04X}")

    if not profile.interfaces:
        errors.append("No USB interfaces specified")

    if profile.has_rgb:
        for colour_name, levels in profile.colour_map.items():
            if not colour_name:
                errors.append("Colour name is empty")
            for level_key in (0, 1, 2):
                if level_key not in levels:
                    errors.append(f"Colour '{colour_name}' missing brightness level {level_key}")
                else:
                    byte5, byte4 = levels[level_key]
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
        if acpi.backend not in ("module", "acpi_call"):
            errors.append(f"Unknown ACPI backend: {acpi.backend}")

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
