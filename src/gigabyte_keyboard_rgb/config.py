import json
import os
from pathlib import Path


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gigabyte-keyboard-rgb"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "colour": "purple",
    "brightness": 100,
    "startup_apply": True,
    "vid": 0x0414,
    "pid": 0x8105,
    "interface": 3,
}


def load():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            config.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def apply_from_config(dev):
    cfg = load()
    if cfg.get("startup_apply", False):
        from .protocol import set_static, set_off
        brightness = cfg.get("brightness", 100)
        if brightness == 0:
            return set_off(dev)
        colour = cfg.get("colour", "purple")
        return set_static(dev, colour, brightness)
    return False
