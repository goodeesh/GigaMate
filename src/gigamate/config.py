import json
from pathlib import Path

from .paths import CONFIG_DIR
from .profiles import detect_device, resolve_profile

CONFIG_FILE = CONFIG_DIR / "config.json"

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


def load():
    # Migrate from old config path if needed
    _migrate_old_config()

    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            _migrate_profile_id(data)
            if "brightness" in data:
                data["brightness"] = _migrate_brightness(data["brightness"])
            if "colour" in data:
                data["colour"] = _migrate_colour(data["colour"])
            config.update(data)
        except (json.JSONDecodeError, OSError):
            pass
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
    }
    CONFIG_FILE.write_text(json.dumps(safe, indent=2) + "\n")


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
