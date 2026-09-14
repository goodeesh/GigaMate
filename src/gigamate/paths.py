import os
from pathlib import Path
from typing import Tuple

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gigamate"


def get_runtime_dir() -> Path:
    """Return a private per-user runtime directory.

    Prefers ``$XDG_RUNTIME_DIR`` / ``/run/user/<uid>``. When neither exists
    (unusual sessions), create a 0700 directory under ``/tmp`` so the socket
    and lock are not exposed in a world-writable location.
    """
    candidates = []
    env = os.environ.get("XDG_RUNTIME_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(f"/run/user/{os.getuid()}"))
    for candidate in candidates:
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate

    fallback = Path(f"/tmp/gigamate-{os.getuid()}")
    try:
        fallback.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(fallback, 0o700)
    except OSError:
        return Path("/tmp")
    return fallback


def get_runtime_ipc_paths() -> Tuple[str, str]:
    """Return the fixed (socket, lock) paths used by the single Center instance."""
    runtime_dir = get_runtime_dir()
    sock_path = str(runtime_dir / f"gigamate-center-{os.getuid()}.sock")
    lock_path = str(runtime_dir / f"gigamate-center-{os.getuid()}.lock")
    return sock_path, lock_path


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


# Tray icon variants: plain + dGPU-awake dots (green NVIDIA, red AMD),
# each with and without an orange update badge (bottom-right).
ICON_NAMES = ("gigamate", "gigamate-nvidia", "gigamate-amd",
              "gigamate-update", "gigamate-nvidia-update",
              "gigamate-amd-update")
ICON_PATHS = {name: resolve_icon(name) for name in ICON_NAMES}

