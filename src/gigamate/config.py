import fcntl
import json
import logging
import os
from pathlib import Path

from .paths import CONFIG_DIR
from .profiles import detect_device, resolve_profile

logger = logging.getLogger(__name__)

CONFIG_FILE = CONFIG_DIR / "config.json"

# Legacy path for migration
_OLD_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gigabyte-keyboard-rgb"
_OLD_CONFIG_FILE = _OLD_CONFIG_DIR / "config.json"


def _migrate_old_config():
    """Migrate config from old gigabyte-keyboard-rgb location to new gigamate location."""
    try:
        if _OLD_CONFIG_FILE.exists() and not CONFIG_FILE.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(_OLD_CONFIG_FILE.read_text(), encoding="utf-8")
            logger.info("Migrated settings from %s to %s", _OLD_CONFIG_DIR, CONFIG_DIR)
    except (OSError, IOError) as exc:
        logger.warning("Could not migrate legacy config: %s", exc)

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


def _coerce_profile_id(value):
    """Return a valid [vid, pid] pair of ints, or the default."""
    try:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return [int(value[0]), int(value[1])]
    except (TypeError, ValueError):
        pass
    return list(DEFAULT_CONFIG["profile_id"])


def _migrate_profile_id(data):
    if "profile_id" in data:
        return
    vid = data.get("vid")
    pid = data.get("pid")
    if vid is None or pid is None:
        return
    try:
        data["profile_id"] = [
            vid if isinstance(vid, int) else int(vid, 16),
            pid if isinstance(pid, int) else int(pid, 16),
        ]
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed legacy vid/pid in config")


def _migrate_idle_timeout(val):
    from .idle import clamp_timeout, DEFAULT_TIMEOUT_SEC
    try:
        return clamp_timeout(int(val))
    except (ValueError, TypeError):
        return DEFAULT_TIMEOUT_SEC


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load():
    # Migrate from old config path if needed
    _migrate_old_config()

    config = dict(DEFAULT_CONFIG)
    # Fall back to the backup when the primary is missing or corrupt.
    data = _read_json(CONFIG_FILE)
    if data is None:
        data = _read_json(CONFIG_DIR / "config.json.bak")

    if data:
        if not isinstance(data, dict):
            logger.warning("config.json is not a JSON object; using defaults")
            return config
        _migrate_profile_id(data)
        if "profile_id" in data:
            data["profile_id"] = _coerce_profile_id(data["profile_id"])
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
                data["charge_limit"] = cl if 40 <= cl <= 100 else DEFAULT_CONFIG["charge_limit"]
            except (ValueError, TypeError):
                data["charge_limit"] = DEFAULT_CONFIG["charge_limit"]
        if "charge_limit_enabled" in data:
            data["charge_limit_enabled"] = bool(data["charge_limit_enabled"])
        if "acpi_profile" in data:
            try:
                prof = int(data["acpi_profile"])
                if 0 <= prof <= 3:
                    data["acpi_profile"] = prof
                else:
                    data.pop("acpi_profile", None)
            except (ValueError, TypeError):
                data.pop("acpi_profile", None)
        config.update(data)

    if not config.get("profile_id"):
        detected = detect_device()
        if detected is not None:
            config["profile_id"] = list(detected)
    return config


def _coerce_charge_limit(value) -> int:
    """Return a valid 40..100 charge limit, falling back to the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["charge_limit"]
    return v if 40 <= v <= 100 else DEFAULT_CONFIG["charge_limit"]


def _open_config_lock():
    """Open (creating as needed) the cross-process config lock, 0600, no symlink follow."""
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(CONFIG_DIR / "config.lock", flags, 0o600)
    return os.fdopen(fd, "a+")


def save(config):
    """Persist configuration atomically, serialized across processes."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _open_config_lock()
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        _write_config(config)
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def update_config(mutator):
    """Atomically read-modify-write the config under the cross-process lock.

    ``mutator`` receives the currently-loaded config dict and may mutate it
    in place (or return a replacement). This closes the read-modify-write race
    between the tray, Center and CLI.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _open_config_lock()
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        cfg = load()
        result = mutator(cfg)
        if isinstance(result, dict):
            cfg = result
        _write_config(cfg)
        return cfg
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_config(config):
    safe = {
        "colour": config.get("colour", DEFAULT_CONFIG["colour"]),
        "brightness": _migrate_brightness(config.get("brightness", DEFAULT_CONFIG["brightness"])),
        "startup_apply": bool(config.get("startup_apply", True)),
        "profile_id": _coerce_profile_id(config.get("profile_id", DEFAULT_CONFIG["profile_id"])),
        "idle_off_enabled": bool(config.get("idle_off_enabled", True)),
        "idle_timeout_sec": _migrate_idle_timeout(
            config.get("idle_timeout_sec", DEFAULT_CONFIG["idle_timeout_sec"])),
        "charge_limit": _coerce_charge_limit(config.get("charge_limit", DEFAULT_CONFIG["charge_limit"])),
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

    # Preserve keys written by a newer version so a downgrade-then-save does
    # not silently discard them.
    existing = _read_json(CONFIG_FILE)
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key not in safe:
                safe[key] = value

    # Surface caller-supplied keys we deliberately do not persist.
    dropped = sorted(str(k) for k in set(config) - set(safe) - {"acpi_profile"})
    if dropped:
        logger.warning("config.save() ignored unknown keys: %s", ", ".join(dropped))

    _atomic_write_bytes(CONFIG_FILE, json.dumps(safe, indent=2).encode("utf-8") + b"\n")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
    # Keep a durable, atomic, 0600 backup of the previous good file.
    _atomic_write_bytes(CONFIG_DIR / "config.json.bak", CONFIG_FILE.read_bytes())
    try:
        dir_fd = os.open(CONFIG_DIR, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
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
    if not cfg.get("startup_apply", DEFAULT_CONFIG["startup_apply"]):
        return False
    from .protocol import set_static, set_off
    profile = resolve_active_profile()
    brightness = cfg.get("brightness", 2)
    if brightness == 0:
        return set_off(dev, profile)
    colour = cfg.get("colour", DEFAULT_CONFIG["colour"])
    if profile is not None and profile.colour_map and colour not in profile.colour_map:
        colour = next(iter(profile.colour_map))
    return set_static(dev, colour, brightness, profile)
