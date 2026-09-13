import json
import os
import shutil
from pathlib import Path

from .paths import CONFIG_DIR
from .profiles import detect_device, resolve_profile

CONFIG_FILE = CONFIG_DIR / "config.json"
_CONFIG_BAK_FILE = CONFIG_DIR / "config.json.bak"

# Legacy path for migration
_OLD_CONFIG_DIR = Path.home() / ".config" / "gigabyte-keyboard-rgb"
_OLD_CONFIG_FILE = _OLD_CONFIG_DIR / "config.json"


def _migrate_old_config():
    """Migrate config from old gigabyte-keyboard-rgb location to new gigamate location."""
    if _OLD_CONFIG_FILE.exists() and not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(_OLD_CONFIG_FILE.read_text())
        import sys
        print("info: Migrated settings from ~/.config/gigabyte-keyboard-rgb/ to ~/.config/gigamate/", file=sys.stderr)

DEFAULT_CONFIG = {
    "colour": "light_purple",
    "brightness": 2,
    "startup_apply": True,
    "profile_id": [0x0414, 0x8105],
    "idle_off_enabled": True,
    "idle_timeout_sec": 60,
    "charge_limit": 80,
    "charge_limit_enabled": True,
    "sync_system_power": True,
    "last_brightness": 2,
}

_BRIGHTNESS_LEGACY_MAP = {
    (0, 12): 0,
    (13, 62): 1,
    (63, 100): 2,
}


def _migrate_brightness(val):
    if isinstance(val, str):
        if val in ("off", "0"):
            return 0
        if val in ("dim", "1"):
            return 1
        if val in ("full", "2"):
            return 2
        try:
            val = int(val)
        except (ValueError, TypeError):
            return 2
    if isinstance(val, int):
        if val in (0, 1, 2):
            return val
        for (lo, hi), mapped in _BRIGHTNESS_LEGACY_MAP.items():
            if lo <= val <= hi:
                return mapped
    return 2


_LEGACY_COLOUR_MAP = {
    "blush_pink_dim": "blush_pink",
}


def _migrate_colour(col):
    if not col:
        return DEFAULT_CONFIG["colour"]
    col = col.lower().replace(" ", "_")
    if col in _LEGACY_COLOUR_MAP:
        return _LEGACY_COLOUR_MAP[col]
    from .protocol import COLOUR_MAP
    if col in COLOUR_MAP:
        return col
    old_to_new = {
        "rainbow": DEFAULT_CONFIG["colour"],
    }
    return old_to_new.get(col, col)


def _migrate_profile_id(data):
    if "profile_id" in data:
        return
    vid = data.get("vid")
    pid = data.get("pid")
    if vid is not None and pid is not None:
        data["profile_id"] = [int(vid) if isinstance(vid, int) else int(vid, 16),
                              int(pid) if isinstance(pid, int) else int(pid, 16)]


def _migrate_idle_timeout(val):
    from .idle import clamp_timeout, DEFAULT_TIMEOUT_SEC
    try:
        return clamp_timeout(int(val))
    except (ValueError, TypeError):
        return DEFAULT_TIMEOUT_SEC


def load():
    # Migrate from old config path if needed
    _migrate_old_config()

    config = dict(DEFAULT_CONFIG)
    data = None
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            bak_file = CONFIG_DIR / "config.json.bak"
            if bak_file.exists():
                try:
                    data = json.loads(bak_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

    if data:
        _migrate_profile_id(data)
        if "brightness" in data:
            data["brightness"] = _migrate_brightness(data["brightness"])
        if "last_brightness" in data:
            data["last_brightness"] = _migrate_brightness(data["last_brightness"])
        if "colour" in data:
            data["colour"] = _migrate_colour(data["colour"])
        if "idle_timeout_sec" in data:
            data["idle_timeout_sec"] = _migrate_idle_timeout(data["idle_timeout_sec"])
        if "idle_off_enabled" in data:
            data["idle_off_enabled"] = bool(data["idle_off_enabled"])
        if "sync_system_power" in data:
            data["sync_system_power"] = bool(data["sync_system_power"])
        if "startup_apply" in data:
            data["startup_apply"] = bool(data["startup_apply"])
        if "charge_limit" in data:
            try:
                cl = int(data["charge_limit"])
                if 40 <= cl <= 100:
                    data["charge_limit"] = cl
            except (ValueError, TypeError):
                pass
        if "charge_limit_enabled" in data:
            data["charge_limit_enabled"] = bool(data["charge_limit_enabled"])
        if "acpi_profile" in data:
            try:
                prof = int(data["acpi_profile"])
                if 0 <= prof <= 3:
                    data["acpi_profile"] = prof
            except (ValueError, TypeError):
                pass
        config.update(data)

    if not config.get("profile_id"):
        detected = detect_device()
        if detected is not None:
            config["profile_id"] = list(detected)
    return config


def save(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    safe = {
        "colour": config.get("colour", DEFAULT_CONFIG["colour"]),
        "brightness": int(config.get("brightness", 2)),
        "startup_apply": bool(config.get("startup_apply", True)),
        "profile_id": list(config.get("profile_id", DEFAULT_CONFIG["profile_id"])),
        "idle_off_enabled": bool(config.get("idle_off_enabled", True)),
        "idle_timeout_sec": _migrate_idle_timeout(
            config.get("idle_timeout_sec", DEFAULT_CONFIG["idle_timeout_sec"])),
        "charge_limit": int(config.get("charge_limit", DEFAULT_CONFIG["charge_limit"])),
        "charge_limit_enabled": bool(config.get("charge_limit_enabled", DEFAULT_CONFIG["charge_limit_enabled"])),
        "sync_system_power": bool(config.get("sync_system_power", DEFAULT_CONFIG["sync_system_power"])),
        "last_brightness": _migrate_brightness(config.get("last_brightness", DEFAULT_CONFIG["last_brightness"])),
    }
    acpi_profile = config.get("acpi_profile")
    if acpi_profile is not None:
        try:
            acpi_profile = int(acpi_profile)
            if 0 <= acpi_profile <= 3:
                safe["acpi_profile"] = acpi_profile
        except (ValueError, TypeError):
            pass

    # Atomic write with backup fallback
    tmp_file = CONFIG_DIR / f"config.json.tmp.{os.getpid()}"
    try:
        content = json.dumps(safe, indent=2) + "\n"
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, CONFIG_FILE)
        try:
            bak_file = CONFIG_DIR / "config.json.bak"
            shutil.copyfile(CONFIG_FILE, bak_file)
        except OSError:
            pass
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


def resolve_active_profile():
    cfg = load()
    pid = cfg.get("profile_id")
    if pid and len(pid) == 2:
        return resolve_profile(int(pid[0]), int(pid[1]))
    return None


def apply_from_config(dev):
    cfg = load()
    if not cfg.get("startup_apply", False):
        return False
    from .protocol import set_static, set_off
    profile = resolve_active_profile()
    brightness = cfg.get("brightness", 2)
    if brightness == 0:
        return set_off(dev, profile)
    colour = cfg.get("colour", DEFAULT_CONFIG["colour"])
    if profile is not None and colour not in profile.colour_map:
        colour = next(iter(profile.colour_map))
    return set_static(dev, colour, brightness, profile)
