import fcntl
import json
import logging
import os
from pathlib import Path
import threading
from typing import Optional

from .paths import CONFIG_DIR

logger = logging.getLogger(__name__)

CONFIG_FILE = CONFIG_DIR / "config.json"

# Legacy path for migration
_OLD_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gigabyte-keyboard-rgb"
_OLD_CONFIG_FILE = _OLD_CONFIG_DIR / "config.json"


def _migrate_old_config():
    """Migrate config and custom profiles from old gigabyte-keyboard-rgb location to new gigamate location."""
    try:
        if _OLD_CONFIG_FILE.exists() and not CONFIG_FILE.exists():
            old_bytes = _OLD_CONFIG_FILE.read_bytes()
            if old_bytes:
                try:
                    json.loads(old_bytes.decode("utf-8"))
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    _atomic_write_bytes(CONFIG_FILE, old_bytes)
                    logger.info("Migrated settings from %s to %s", _OLD_CONFIG_DIR, CONFIG_DIR)
                except Exception:
                    logger.warning("Legacy config was malformed; ignoring")

        old_profiles = _OLD_CONFIG_DIR / "profiles"
        new_profiles = CONFIG_DIR / "profiles"
        if old_profiles.is_dir():
            new_profiles.mkdir(parents=True, exist_ok=True)
            for p in old_profiles.glob("*.json"):
                dest = new_profiles / p.name
                if not dest.exists():
                    try:
                        _atomic_write_bytes(dest, p.read_bytes())
                    except OSError:
                        pass
    except (OSError, IOError) as exc:
        logger.warning("Could not migrate legacy config: %s", exc)

DEFAULT_CONFIG = {
    "colour": "light_purple",
    "brightness": 2,
    "rgb_hex": None,
    "brightness_pct": None,
    "rgb_enabled": True,
    # Hardware-mutating behaviours are opt-in; onboarding enables them.
    "startup_apply": False,
    "idle_off_enabled": False,
    "idle_timeout_sec": 60,
    "idle_keep_on_ac": False,
    "idle_internal_only": False,
    "charge_limit": 80,
    "charge_limit_enabled": False,
    "charge_topup_active": False,
    "charge_topup_started_ts": 0,
    "charge_calibration_auto": False,
    "charge_calibration_interval_days": 30,
    "charge_calibration_baseline_ts": 0,
    "last_full_charge_ts": 0,
    "profile_rgb_enabled": False,
    "profile_rgb_map": {"0": "#2563eb", "1": "#00b4a0", "2": "#f59e0b", "3": "#ef4444"},
    "ac_profile_mode": "off",
    "ac_profile_id": 1,
    "battery_profile_id": 0,
    "disabled_profiles": [],
    # Discrete-GPU (NVIDIA) sleep-aware undervolt: V/F clock offset in MHz.
    # 0 = stock/off. Applied only while the dGPU is awake (see dgpu_tune.py).
    "dgpu_undervolt_enabled": False,
    "dgpu_undervolt_offset_mhz": 0,
    "dgpu_undervolt_auto": True,
    # Max (boost) clock cap in MHz: 0 = unlocked. Applied only while awake.
    "dgpu_max_clock_enabled": False,
    "dgpu_max_clock_mhz": 0,
    # Memory-clock V/F offset in MHz: signed, 0 = stock. Positive overclocks the
    # memory clock, negative underclocks it. Applied only while the dGPU is awake.
    "dgpu_mem_offset_enabled": False,
    "dgpu_mem_offset_mhz": 0,
    # Discrete GPU power-gate policy ("optimus" | "eco" | "auto").
    "dgpu_power_mode": "optimus",
    "dgpu_gate_disclaimer_accepted": False,
    "sync_system_power": False,
    "ui_theme": "system",
    "last_brightness": 2,
    # Set once the first-run setup has been shown (so it never nags again).
    "onboarding_complete": False,
    # Explicit per-user hotkey mappings (action -> spec), merged over the
    # device profile's hotkeys. Opt-in only; see docs/PROFILE_SCHEMA.md.
    "hotkey_overrides": {},
    "custom_hotkeys": {},
}

_BRIGHTNESS_LEGACY_MAP = {
    (0, 12): 0,
    (13, 62): 1,
    (63, 100): 2,
}

LEGACY_BRIGHTNESS_TO_PCT = {0: 0, 1: 25, 2: 100}


def pct_to_legacy_brightness(pct: int) -> int:
    """Map continuous 0..100% brightness to legacy 0/1/2 levels."""
    p = max(0, min(100, int(pct)))
    if p <= 0:
        return 0
    if p <= 60:
        return 1
    return 2


MIN_BRIGHTNESS_PCT = 5


def _sanitize_brightness_pct(val):
    if val is None or isinstance(val, bool):
        return None
    try:
        pct = max(0, min(100, int(val)))
        if 0 < pct < MIN_BRIGHTNESS_PCT:
            pct = MIN_BRIGHTNESS_PCT
        return pct
    except (TypeError, ValueError):
        return None


def _sanitize_rgb_hex(val):
    if val is None:
        return None
    from .lamparray import parse_hex_rgb
    parsed = parse_hex_rgb(val)
    if parsed is None:
        return None
    return f"#{parsed[0]:02x}{parsed[1]:02x}{parsed[2]:02x}"


def _sanitize_profile_rgb_map(value) -> dict:
    clean = dict(DEFAULT_CONFIG["profile_rgb_map"])
    if not isinstance(value, dict):
        return clean
    for k, v in value.items():
        key = str(k)
        sanitized = _sanitize_rgb_hex(v)
        if sanitized is not None:
            clean[key] = sanitized
    return clean


def profile_rgb_for(cfg: dict, profile_id: int) -> str:
    """Return sanitized #rrggbb for profile_id from cfg['profile_rgb_map']."""
    key = str(profile_id)
    raw_map = cfg.get("profile_rgb_map") if isinstance(cfg, dict) else None
    if isinstance(raw_map, dict) and key in raw_map:
        sanitized = _sanitize_rgb_hex(raw_map[key])
        if sanitized is not None:
            return sanitized
    fallback = DEFAULT_CONFIG["profile_rgb_map"].get(key)
    if fallback is not None:
        return fallback
    return "#ffffff"


def _coerce_ui_theme(value) -> str:
    """Validate UI color-scheme preference ('system' | 'dark' | 'light')."""
    s = str(value or "").strip().lower()
    return s if s in ("system", "dark", "light") else "system"


def _coerce_ac_profile_mode(value) -> str:
    if value in ("off", "switch", "follow_system"):
        return value
    return DEFAULT_CONFIG["ac_profile_mode"]


def _coerce_profile_num(value, default: int) -> int:
    try:
        v = int(value)
        return v if 0 <= v <= 3 else default
    except (ValueError, TypeError):
        return default


def _sanitize_disabled_profiles(value) -> list:
    if value is None or isinstance(value, (str, bytes, dict)):
        return []
    try:
        it = iter(value)
    except TypeError:
        return []
    seen = set()
    for item in it:
        if isinstance(item, bool):
            continue
        try:
            v = int(item)
        except (ValueError, TypeError):
            continue
        if 0 <= v <= 7:
            seen.add(v)
    return sorted(seen)


def _sanitize_custom_hotkeys(value) -> dict:
    if not isinstance(value, dict):
        return {}
    clean = {}
    for action, spec in value.items():
        if isinstance(action, str) and action and isinstance(spec, dict):
            clean[action] = dict(spec)
    return clean


def effective_brightness_pct(cfg: dict) -> int:
    """Return clamped 0..100 brightness_pct from cfg, or map legacy brightness."""
    pct = _sanitize_brightness_pct(cfg.get("brightness_pct"))
    if pct is not None:
        return pct
    try:
        legacy = int(cfg.get("brightness", 2))
    except (TypeError, ValueError):
        legacy = 2
    return LEGACY_BRIGHTNESS_TO_PCT.get(legacy, 100)


def is_rgb_enabled(cfg: dict) -> bool:
    """Return False when RGB lighting is explicitly disabled or brightness is 0."""
    if cfg.get("rgb_enabled") is False:
        return False
    if cfg.get("brightness_pct") == 0:
        return False
    if cfg.get("brightness_pct") is None:
        try:
            if int(cfg.get("brightness", 2)) == 0:
                return False
        except (TypeError, ValueError):
            pass
    return True


def effective_rgb_hex(cfg: dict) -> str:
    """Return normalized '#rrggbb' from cfg['rgb_hex'], or fallback to PALETTE_HEX."""
    norm = _sanitize_rgb_hex(cfg.get("rgb_hex"))
    if norm is not None:
        return norm
    from .lamparray import PALETTE_HEX
    return PALETTE_HEX.get(cfg.get("colour", "white"), "#ffffff")


def _migrate_brightness(val):
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, str):
        val_clean = val.strip().lower().rstrip("%")
        if val_clean in ("off", "0"):
            return 0
        if val_clean in ("dim", "1"):
            return 1
        if val_clean in ("full", "2"):
            return 2
        try:
            val = float(val_clean)
        except (ValueError, TypeError):
            return 2
    if isinstance(val, (int, float)):
        v = int(val)
        if v in (0, 1, 2):
            return v
        if v <= 12:
            return 0
        if v <= 62:
            return 1
        return 2
    return 2


_LEGACY_COLOUR_MAP = {
    "blush_pink_dim": "blush_pink",
}


def _migrate_colour(col):
    if not col or not isinstance(col, str):
        return DEFAULT_CONFIG["colour"]
    col = col.lower().replace(" ", "_")
    if col in _LEGACY_COLOUR_MAP:
        return _LEGACY_COLOUR_MAP[col]
    from .lamparray import PALETTE
    if col in PALETTE:
        return col
    old_to_new = {
        "rainbow": DEFAULT_CONFIG["colour"],
    }
    return old_to_new.get(col, col)


def _coerce_profile_id(value):
    """Return a valid [vid, pid] pair of ints, or the default."""
    if isinstance(value, str) and ":" in value:
        value = value.split(":", 1)
    try:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            v0, v1 = value[0], value[1]
            vid = v0 if isinstance(v0, int) else int(str(v0).strip(), 0 if str(v0).strip().startswith(("0x", "0X")) else 16)
            pid = v1 if isinstance(v1, int) else int(str(v1).strip(), 0 if str(v1).strip().startswith(("0x", "0X")) else 16)
            return [vid, pid]
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
        data.pop("profile_id", None)
        data.pop("rgb_legacy_mode", None)
        data.pop("experimental_profiles_enabled", None)
        if "brightness" in data:
            data["brightness"] = _migrate_brightness(data["brightness"])
        if "last_brightness" in data:
            data["last_brightness"] = _migrate_brightness(data["last_brightness"])
        if "colour" in data:
            data["colour"] = _migrate_colour(data["colour"])
        if "rgb_hex" in data:
            data["rgb_hex"] = _sanitize_rgb_hex(data["rgb_hex"])
        if "brightness_pct" in data:
            data["brightness_pct"] = _sanitize_brightness_pct(data["brightness_pct"])
        if "rgb_enabled" in data:
            data["rgb_enabled"] = bool(data["rgb_enabled"])
        if "dgpu_gate_disclaimer_accepted" in data:
            data["dgpu_gate_disclaimer_accepted"] = bool(data["dgpu_gate_disclaimer_accepted"])
        if "idle_timeout_sec" in data:
            data["idle_timeout_sec"] = _migrate_idle_timeout(data["idle_timeout_sec"])
        if "idle_off_enabled" in data:
            data["idle_off_enabled"] = bool(data["idle_off_enabled"])
        if "idle_keep_on_ac" in data:
            data["idle_keep_on_ac"] = bool(data["idle_keep_on_ac"])
        if "idle_internal_only" in data:
            data["idle_internal_only"] = bool(data["idle_internal_only"])
        if "sync_system_power" in data:
            data["sync_system_power"] = bool(data["sync_system_power"])
        if "ui_theme" in data:
            data["ui_theme"] = _coerce_ui_theme(data["ui_theme"])
        if "startup_apply" in data:
            data["startup_apply"] = bool(data["startup_apply"])
        if "charge_topup_active" in data:
            data["charge_topup_active"] = bool(data["charge_topup_active"])
        if "charge_topup_started_ts" in data:
            try:
                v = int(data["charge_topup_started_ts"])
                data["charge_topup_started_ts"] = v if v >= 0 else 0
            except (ValueError, TypeError):
                data["charge_topup_started_ts"] = 0
        if "charge_calibration_auto" in data:
            data["charge_calibration_auto"] = bool(data["charge_calibration_auto"])
        if "charge_calibration_interval_days" in data:
            try:
                v = int(data["charge_calibration_interval_days"])
                data["charge_calibration_interval_days"] = v if 7 <= v <= 90 else DEFAULT_CONFIG["charge_calibration_interval_days"]
            except (ValueError, TypeError):
                data["charge_calibration_interval_days"] = DEFAULT_CONFIG["charge_calibration_interval_days"]
        if "last_full_charge_ts" in data:
            try:
                v = int(data["last_full_charge_ts"])
                data["last_full_charge_ts"] = v if v >= 0 else 0
            except (ValueError, TypeError):
                data["last_full_charge_ts"] = 0
        if "charge_calibration_baseline_ts" in data:
            try:
                v = int(data["charge_calibration_baseline_ts"])
                data["charge_calibration_baseline_ts"] = v if v >= 0 else 0
            except (ValueError, TypeError):
                data["charge_calibration_baseline_ts"] = 0
        if "profile_rgb_enabled" in data:
            data["profile_rgb_enabled"] = bool(data["profile_rgb_enabled"])
        if "profile_rgb_map" in data:
            data["profile_rgb_map"] = _sanitize_profile_rgb_map(data["profile_rgb_map"])
        if "ac_profile_mode" in data:
            data["ac_profile_mode"] = _coerce_ac_profile_mode(data["ac_profile_mode"])
        if "ac_profile_id" in data:
            data["ac_profile_id"] = _coerce_profile_num(
                data["ac_profile_id"], DEFAULT_CONFIG["ac_profile_id"])
        if "battery_profile_id" in data:
            data["battery_profile_id"] = _coerce_profile_num(
                data["battery_profile_id"], DEFAULT_CONFIG["battery_profile_id"])
        if "disabled_profiles" in data:
            data["disabled_profiles"] = _sanitize_disabled_profiles(data["disabled_profiles"])
        if "custom_hotkeys" in data:
            data["custom_hotkeys"] = _sanitize_custom_hotkeys(data["custom_hotkeys"])
        if "charge_limit" in data:
            try:
                cl = int(data["charge_limit"])
                data["charge_limit"] = cl if 40 <= cl <= 100 else DEFAULT_CONFIG["charge_limit"]
            except (ValueError, TypeError):
                data["charge_limit"] = DEFAULT_CONFIG["charge_limit"]
        if "charge_limit_enabled" in data:
            data["charge_limit_enabled"] = bool(data["charge_limit_enabled"])
        if "dgpu_undervolt_enabled" in data:
            data["dgpu_undervolt_enabled"] = bool(data["dgpu_undervolt_enabled"])
        if "dgpu_undervolt_auto" in data:
            data["dgpu_undervolt_auto"] = bool(data["dgpu_undervolt_auto"])
        if "dgpu_undervolt_offset_mhz" in data:
            try:
                off = int(data["dgpu_undervolt_offset_mhz"])
                data["dgpu_undervolt_offset_mhz"] = off if 0 <= off <= 255 else DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"]
            except (ValueError, TypeError):
                data["dgpu_undervolt_offset_mhz"] = DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"]
        if "dgpu_max_clock_enabled" in data:
            data["dgpu_max_clock_enabled"] = bool(data["dgpu_max_clock_enabled"])
        if "dgpu_max_clock_mhz" in data:
            try:
                mc = int(data["dgpu_max_clock_mhz"])
                data["dgpu_max_clock_mhz"] = mc if 0 <= mc <= 4000 else DEFAULT_CONFIG["dgpu_max_clock_mhz"]
            except (ValueError, TypeError):
                data["dgpu_max_clock_mhz"] = DEFAULT_CONFIG["dgpu_max_clock_mhz"]
        if "dgpu_mem_offset_enabled" in data:
            data["dgpu_mem_offset_enabled"] = bool(data["dgpu_mem_offset_enabled"])
        if "dgpu_mem_offset_mhz" in data:
            data["dgpu_mem_offset_mhz"] = _coerce_dgpu_mem_offset(data["dgpu_mem_offset_mhz"])
        if "dgpu_power_mode" in data:
            data["dgpu_power_mode"] = _coerce_dgpu_power_mode(data["dgpu_power_mode"])
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

    # Prefer the actually-connected keyboard profile unless one was stored.
    return config


def _sanitize_hotkey_overrides(value):
    """Coerce hotkey_overrides to valid, known-action specs (else drop)."""
    from .hotkeys import KNOWN_ACTIONS, HotkeySpec

    if not isinstance(value, dict):
        return {}
    clean = {}
    for action, entry in value.items():
        if action not in KNOWN_ACTIONS:
            logger.warning("hotkey_overrides: unknown action %r (known: %s)",
                           action, ", ".join(KNOWN_ACTIONS))
            continue
        try:
            spec = HotkeySpec.from_dict(action, entry)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("hotkey_overrides: ignoring invalid %r spec: %s", action, exc)
            continue
        clean[action] = {
            "interface": spec.interface,
            "report_id": spec.report_id,
            "payload": spec.payload.hex(),
            "key_name": spec.key_name,
        }
    return clean


def _coerce_charge_limit(value) -> int:
    """Return a valid 40..100 charge limit, falling back to the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["charge_limit"]
    return v if 40 <= v <= 100 else DEFAULT_CONFIG["charge_limit"]


def _coerce_calibration_interval(value) -> int:
    """Return a valid 7..90 calibration interval days, falling back to the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["charge_calibration_interval_days"]
    return v if 7 <= v <= 90 else DEFAULT_CONFIG["charge_calibration_interval_days"]


def _coerce_dgpu_offset(value) -> int:
    """Return a valid 0..255 dGPU undervolt offset (MHz), else the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"]
    return v if 0 <= v <= 255 else DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"]


def _coerce_dgpu_max_clock(value) -> int:
    """Return a valid 0..4000 dGPU max-clock cap (MHz), else the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["dgpu_max_clock_mhz"]
    return v if 0 <= v <= 4000 else DEFAULT_CONFIG["dgpu_max_clock_mhz"]


def _coerce_dgpu_mem_offset(value) -> int:
    """Return a valid -1000..2000 dGPU memory-clock offset (MHz), else the default."""
    try:
        v = int(value)
    except (ValueError, TypeError):
        return DEFAULT_CONFIG["dgpu_mem_offset_mhz"]
    return v if -1000 <= v <= 2000 else DEFAULT_CONFIG["dgpu_mem_offset_mhz"]


def _coerce_dgpu_power_mode(value) -> str:
    """Return a valid dGPU power-gate mode ('optimus', 'eco', 'auto'), else default."""
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("optimus", "eco", "auto"):
            return v
    return DEFAULT_CONFIG["dgpu_power_mode"]


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
        return _write_config(config)
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


def _atomic_write_bytes(path: Path, data: bytes) -> bool:
    """Write ``data`` to ``path`` atomically. Returns True on success."""
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    ok = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        ok = True
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return ok


def _write_config(config):
    safe = {
        "colour": _migrate_colour(config.get("colour", DEFAULT_CONFIG["colour"])),
        "brightness": _migrate_brightness(config.get("brightness", DEFAULT_CONFIG["brightness"])),
        "rgb_hex": _sanitize_rgb_hex(config.get("rgb_hex")),
        "brightness_pct": _sanitize_brightness_pct(config.get("brightness_pct")),
        "rgb_enabled": bool(config.get("rgb_enabled", DEFAULT_CONFIG["rgb_enabled"])),
        "startup_apply": bool(config.get("startup_apply", DEFAULT_CONFIG["startup_apply"])),
        "idle_off_enabled": bool(config.get("idle_off_enabled", DEFAULT_CONFIG["idle_off_enabled"])),
        "idle_timeout_sec": _migrate_idle_timeout(
            config.get("idle_timeout_sec", DEFAULT_CONFIG["idle_timeout_sec"])),
        "idle_keep_on_ac": bool(config.get("idle_keep_on_ac", DEFAULT_CONFIG["idle_keep_on_ac"])),
        "idle_internal_only": bool(config.get("idle_internal_only", DEFAULT_CONFIG["idle_internal_only"])),
        "charge_limit": _coerce_charge_limit(config.get("charge_limit", DEFAULT_CONFIG["charge_limit"])),
        "charge_limit_enabled": bool(config.get("charge_limit_enabled", DEFAULT_CONFIG["charge_limit_enabled"])),
        "dgpu_undervolt_enabled": bool(config.get("dgpu_undervolt_enabled", DEFAULT_CONFIG["dgpu_undervolt_enabled"])),
        "dgpu_undervolt_offset_mhz": _coerce_dgpu_offset(
            config.get("dgpu_undervolt_offset_mhz", DEFAULT_CONFIG["dgpu_undervolt_offset_mhz"])),
        "dgpu_undervolt_auto": bool(config.get("dgpu_undervolt_auto", DEFAULT_CONFIG["dgpu_undervolt_auto"])),
        "dgpu_max_clock_enabled": bool(config.get("dgpu_max_clock_enabled", DEFAULT_CONFIG["dgpu_max_clock_enabled"])),
        "dgpu_max_clock_mhz": _coerce_dgpu_max_clock(
            config.get("dgpu_max_clock_mhz", DEFAULT_CONFIG["dgpu_max_clock_mhz"])),
        "dgpu_mem_offset_enabled": bool(config.get("dgpu_mem_offset_enabled", DEFAULT_CONFIG["dgpu_mem_offset_enabled"])),
        "dgpu_mem_offset_mhz": _coerce_dgpu_mem_offset(
            config.get("dgpu_mem_offset_mhz", DEFAULT_CONFIG["dgpu_mem_offset_mhz"])),
        "dgpu_power_mode": _coerce_dgpu_power_mode(
            config.get("dgpu_power_mode", DEFAULT_CONFIG["dgpu_power_mode"])),
        "dgpu_gate_disclaimer_accepted": bool(config.get(
            "dgpu_gate_disclaimer_accepted", DEFAULT_CONFIG["dgpu_gate_disclaimer_accepted"])),
        "sync_system_power": bool(config.get("sync_system_power", DEFAULT_CONFIG["sync_system_power"])),
        "ui_theme": _coerce_ui_theme(config.get("ui_theme", DEFAULT_CONFIG["ui_theme"])),
        "last_brightness": _migrate_brightness(config.get("last_brightness", DEFAULT_CONFIG["last_brightness"])),
        "onboarding_complete": bool(config.get("onboarding_complete", DEFAULT_CONFIG["onboarding_complete"])),
        "hotkey_overrides": _sanitize_hotkey_overrides(config.get("hotkey_overrides")),
        "custom_hotkeys": _sanitize_custom_hotkeys(config.get("custom_hotkeys")),
        "charge_topup_active": bool(config.get("charge_topup_active", DEFAULT_CONFIG["charge_topup_active"])),
        "charge_topup_started_ts": max(0, int(config.get("charge_topup_started_ts", 0) or 0)),
        "charge_calibration_auto": bool(config.get("charge_calibration_auto", DEFAULT_CONFIG["charge_calibration_auto"])),
        "charge_calibration_interval_days": _coerce_calibration_interval(
            config.get("charge_calibration_interval_days", DEFAULT_CONFIG["charge_calibration_interval_days"])),
        "charge_calibration_baseline_ts": max(0, int(config.get("charge_calibration_baseline_ts", 0) or 0)),
        "last_full_charge_ts": max(0, int(config.get("last_full_charge_ts", 0) or 0)),
        "profile_rgb_enabled": bool(config.get("profile_rgb_enabled", DEFAULT_CONFIG["profile_rgb_enabled"])),
        "profile_rgb_map": _sanitize_profile_rgb_map(config.get("profile_rgb_map")),
        "ac_profile_mode": _coerce_ac_profile_mode(config.get("ac_profile_mode")),
        "ac_profile_id": _coerce_profile_num(config.get("ac_profile_id"), DEFAULT_CONFIG["ac_profile_id"]),
        "battery_profile_id": _coerce_profile_num(
            config.get("battery_profile_id"), DEFAULT_CONFIG["battery_profile_id"]),
        "disabled_profiles": _sanitize_disabled_profiles(config.get("disabled_profiles", [])),
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
    # not silently discard them, while dropping known obsolete keys.
    # "dgpu_telemetry" belonged to the removed live-telemetry feature; dropping
    # it here cleans the key out of existing config files on the next save.
    _LEGACY_KEYS_TO_DROP = {"vid", "pid", "dgpu_telemetry",
                             "dgpu_mem_clock_enabled", "dgpu_mem_clock_mhz"}
    existing = _read_json(CONFIG_FILE)
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key not in safe and key not in _LEGACY_KEYS_TO_DROP:
                safe[key] = value

    # Surface caller-supplied keys we deliberately do not persist.
    dropped = sorted(str(k) for k in set(config) - set(safe) - {"acpi_profile"})
    if dropped:
        logger.warning("config.save() ignored unknown keys: %s", ", ".join(dropped))

    if not _atomic_write_bytes(CONFIG_FILE, json.dumps(safe, indent=2).encode("utf-8") + b"\n"):
        return False
    try:
        _atomic_write_bytes(CONFIG_DIR / "config.json.bak",
                            json.dumps(safe, indent=2).encode("utf-8") + b"\n")
    except OSError:
        pass
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
    try:
        dir_fd = os.open(CONFIG_DIR, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
    return True


def apply_from_config(dev):
    cfg = load()
    if not cfg.get("startup_apply", DEFAULT_CONFIG["startup_apply"]):
        return False
    from .protocol import set_off, set_rgb
    if not is_rgb_enabled(cfg):
        return set_off(dev)
    pct = max(1, effective_brightness_pct(cfg))
    return set_rgb(dev, effective_rgb_hex(cfg), pct)


def enabled_acpi_profiles(discovered: dict, cfg: Optional[dict] = None) -> dict:
    """Filter discovered ACPI profiles by removing user-disabled IDs.

    Always keeps at least one profile active: if all discovered profiles
    would be filtered out, returns a shallow copy of ``discovered``.
    """
    if cfg is None:
        cfg = load()
    disabled = set(_sanitize_disabled_profiles(cfg.get("disabled_profiles", [])))
    filtered = {pid: info for pid, info in discovered.items() if int(pid) not in disabled}
    if not filtered and discovered:
        return dict(discovered)
    return filtered
