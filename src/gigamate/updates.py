"""GigaMate — Battery-efficient update checking.

Only trusts tags/releases created by the maintainer (manual batched
releases like ``v2.0.1``). Contributors via forks/PRs cannot push tags
to the upstream repo, so any ``vX.Y.Z`` tag on upstream is authoritative.

Design for battery efficiency:
- At most one tiny HTTPS request per 24h (cached timestamp in
  ``update_state.json``).
- Network I/O runs in a background thread from the tray; never on the
  GTK main loop.
- Failures are silent and cached — no retry storms.

Stdlib only (urllib + json), gi-free so tests run without PyGObject.
"""

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .paths import CONFIG_DIR

REPO = "goodeesh/GigaMate"
RELEASE_LATEST_URL = f"https://api.github.com/{REPO}/releases/latest"
# Fallback when no GitHub Release exists yet (only tags pushed).
TAGS_URL = f"https://api.github.com/{REPO}/tags?per_page=1"
RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{REPO}/main/pyproject.toml"
)
INSTALL_URL = f"https://raw.githubusercontent.com/{REPO}/main/install.sh"

CHECK_INTERVAL_SEC = 24 * 3600  # daily + startup
CHECK_JITTER_SEC = 30 * 60  # tray adds 0-30m jitter to the 24h timer
FETCH_TIMEOUT_SEC = 5

STATE_FILE = CONFIG_DIR / "update_state.json"

_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_VALID_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def parse_version(text: str) -> Tuple[int, int, int]:
    """Parse '2.0.1' or 'v2.0.1' into a comparable tuple. Raises ValueError."""
    m = _TAG_RE.match(text.strip())
    if not m:
        raise ValueError(f"Invalid version: {text!r}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer(latest: str, current: str) -> bool:
    """True if latest > current (both 'vX.Y.Z' or 'X.Y.Z')."""
    try:
        return parse_version(latest) > parse_version(current)
    except ValueError:
        return False


def is_valid_tag(tag: str) -> bool:
    """Only maintainer-pushed release tags count (vX.Y.Z exactly)."""
    return bool(_VALID_TAG_RE.match((tag or "").strip()))


def get_installed_version() -> str:
    from . import __version__
    return __version__


def should_check(now_ts: float, last_check_ts: Optional[float],
                 interval_sec: int = CHECK_INTERVAL_SEC) -> bool:
    if last_check_ts is None:
        return True
    try:
        return (float(now_ts) - float(last_check_ts)) >= interval_sec
    except (TypeError, ValueError):
        return True


def load_state(state_file: Path = STATE_FILE) -> Dict:
    try:
        if state_file.exists():
            data = json.loads(state_file.read_text())
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_state(state: Dict, state_file: Path = STATE_FILE) -> None:
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2) + "\n")
    except OSError:
        pass


def _http_get_json(url: str, timeout: int,
                   urlopen: Callable = urllib.request.urlopen
                   ) -> Tuple[Optional[Dict], bool]:
    """GET a JSON object. Returns (data|None, reachable).

    ``reachable`` is False only on connection-level failure. An HTTP
    error (e.g. 404 no releases yet) still means GitHub is reachable.
    """
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "GigaMate-update-check"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), True
    except urllib.error.HTTPError:
        return None, True
    except Exception:
        return None, False


def _http_get_json_list(url: str, timeout: int,
                        urlopen: Callable = urllib.request.urlopen
                        ) -> Tuple[Optional[list], bool]:
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "GigaMate-update-check"})
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return (data if isinstance(data, list) else None), True
    except urllib.error.HTTPError:
        return None, True
    except Exception:
        return None, False


def _http_reachable(urlopen: Callable = urllib.request.urlopen) -> bool:
    """True if api.github.com answers at all (even 404 = reachable)."""
    _, reachable = _http_get_json(RELEASE_LATEST_URL, FETCH_TIMEOUT_SEC,
                                  urlopen)
    return reachable


def fetch_latest_version(timeout: int = FETCH_TIMEOUT_SEC,
                         urlopen: Callable = urllib.request.urlopen
                         ) -> Optional[str]:
    """Fetch latest release tag from GitHub. Returns 'vX.Y.Z' or None."""
    data, _ = _http_get_json(RELEASE_LATEST_URL, timeout, urlopen)
    if isinstance(data, dict):
        tag = (data.get("tag_name") or "").strip()
        if is_valid_tag(tag):
            return tag
    # Fallback: newest tag (covers repos with tags but no Release yet).
    tags, _ = _http_get_json_list(TAGS_URL, timeout, urlopen)
    if tags:
        tag = ((tags[0] or {}).get("name") or "").strip()
        if is_valid_tag(tag):
            return tag
    return None


def check_for_updates(force: bool = False,
                      timeout: int = FETCH_TIMEOUT_SEC,
                      now_ts: Optional[float] = None,
                      state_file: Path = STATE_FILE,
                      urlopen: Callable = urllib.request.urlopen) -> Dict:
    """Battery-efficient check.

    Returns {"current":, "latest":|None, "update_available": bool,
             "checked_now": bool, "reachable": bool}. Uses cached state
    unless forced or the 24h interval elapsed. Never raises on failure.
    ``reachable`` tells whether github.com answered (False = offline;
    True + latest None = no releases/tags published yet).
    """
    now = now_ts if now_ts is not None else time.time()
    current = get_installed_version()
    state = load_state(state_file)
    cached_latest = state.get("latest_known")

    if not force and not should_check(now, state.get("last_check_ts")):
        latest = cached_latest if isinstance(cached_latest, str) else None
        dismissed = state.get("dismissed_version")
        avail = bool(latest and is_newer(latest.lstrip("v"), current)
                     and dismissed != latest)
        return {"current": current, "latest": latest,
                "update_available": avail, "checked_now": False,
                "reachable": True}

    latest = fetch_latest_version(timeout=timeout, urlopen=urlopen)
    reachable = latest is not None or _http_reachable(urlopen)
    state["last_check_ts"] = now
    if latest:
        state["latest_known"] = latest
    save_state(state, state_file)

    known = latest or (cached_latest if isinstance(cached_latest, str) else None)
    dismissed = state.get("dismissed_version")
    avail = bool(known and is_newer(known.lstrip("v"), current)
                 and dismissed != known)
    return {"current": current, "latest": known,
            "update_available": avail, "checked_now": latest is not None,
            "reachable": reachable}


def dismiss_version(tag: str, state_file: Path = STATE_FILE) -> None:
    state = load_state(state_file)
    state["dismissed_version"] = tag
    save_state(state, state_file)


def build_update_command() -> list:
    """Argv the tray spawns in background to self-update non-interactively."""
    # Piped through bash -s -- so it works without a local checkout;
    # install.sh bootstraps the latest tag itself (see --update).
    return ["bash", "-c",
            f"curl -sSL {INSTALL_URL} | bash -s -- --update --yes"]
