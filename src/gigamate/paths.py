import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gigamate"


def _this_file() -> Path:
    return Path(__file__).resolve()


def resolve_icon(name: str) -> str:
    """Resolve an icon name to a file path, falling back to theme name.

    Kept gi-free so tests can verify icon assets without Gtk.
    """
    candidates = [
        _this_file().parent.parent.parent / "data" / f"{name}.svg",
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{name}.svg",
        Path(f"/usr/share/icons/hicolor/scalable/apps/{name}.svg"),
    ]
    for _p in candidates:
        if _p.exists():
            return str(_p)
    return name


# Tray icon variants: plain + dGPU-awake dots (green NVIDIA, red AMD).
ICON_NAMES = ("gigamate", "gigamate-nvidia", "gigamate-amd")
ICON_PATHS = {name: resolve_icon(name) for name in ICON_NAMES}

