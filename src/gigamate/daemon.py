"""GigaMate — Pure-Python background daemon engine (zero GUI dependencies).

Owns all non-UI background responsibilities previously embedded inside the
GTK tray monolith:
- Keyboard & ACPI hardware discovery + self-healing retries
- Hardware hotkey listener (Mode switch + Open Center)
- Keyboard idle auto-off monitor (evdev + fallback poll) & sleep/resume hooks
- External ``config.json`` reload on mtime change
- Live sensor / GPU / battery polling and BMS calibration evaluation
- AC / Battery & Follow-System power-profile automation (with dGPU wake
  deferral, battery high-power clamp, tlp-pd settle window, and one-shot
  external power-manager conflict notification)
- Daily background update check

Reads fresh configuration from ``config.json`` via ``load_config()`` /
``update_config()`` rather than maintaining duplicated shadow state.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from .acpi import AcpiCapabilities, AcpiController, FanProfile, FanState
from .battery import evaluate_calibration_state, get_battery_manager
from .config import (
    CONFIG_FILE,
    effective_brightness_pct,
    effective_rgb_hex,
    enabled_acpi_profiles,
    is_rgb_enabled,
    load as load_config,
    pct_to_legacy_brightness,
    update_config,
)
from . import dgpu_gate
from .gpu import GpuState, get_gpu_state, gpu_icon_key, gpu_short_status_text
from . import hardware
from .hardware import apply_hardware_settings
from .hotkeys import HotkeyListener, resolve_hotkey_specs
from .idle import (
    DEFAULT_TIMEOUT_SEC,
    IdleMonitor,
    clamp_timeout,
    fallback_idle_ms,
)
from .osd import show_profile_osd
from .paths import get_runtime_ipc_paths
from .power_limits import discover_acpi_profiles
from .protocol import (
    detect_keyboards,
    get_keyboard,
    hardware_supports_lamparray,
    set_off,
    set_rgb,
)
from .sleep_handler import get_sleep_handler
from .system_power import (
    DEFAULT_PROFILE_MAP,
    SYSTEM_TO_GIGAMATE_PROFILE,
    SystemPowerManager,
    detect_power_managers,
    power_config_mtime_token,
)
from . import updates as update_checker

logger = logging.getLogger(__name__)

STATUS_POLL_INTERVAL_MS = 5000  # 5 seconds on AC
STATUS_POLL_BATTERY_MS = 15000  # 15 seconds on battery
IDLE_FALLBACK_POLL_MS = 5000  # 5 seconds (only when evdev unavailable)
UPDATE_CHECK_INTERVAL_MS = 24 * 3600 * 1000  # daily; one tiny HTTPS req/day
_ACPI_RETRY_SEC = 5.0
_KEYBOARD_RETRY_SEC = 10.0


@dataclass(frozen=True)
class DaemonSnapshot:
    """Immutable snapshot of live daemon state for the tray UI."""

    status_text: str
    icon_key: str
    ac_online: Optional[bool]
    poll_interval_ms: int
    acpi_available: bool
    has_lamparray: bool
    active_profile: Optional[int]
    enabled_profiles: Dict[int, Dict[str, str]]
    high_power_profiles_allowed: bool
    backlight_pct: int
    update_available: bool
    latest_version: Optional[str]


class GigaMateDaemon:
    """Headless background controller for GigaMate hardware & automation."""

    def __init__(
        self,
        on_state_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_state_changed = on_state_changed
        self._config: dict = load_config()
        self._last_config_mtime: float = self._get_config_mtime()

        # Keyboard hardware state
        self._has_lamparray: bool = False
        self._no_keyboard: bool = False
        self._unsupported: bool = False
        self._detected_vid: Optional[int] = None
        self._detected_pid: Optional[int] = None
        self._keyboard_retry_at: float = 0.0

        # ACPI state
        self._acpi_controller: Optional[AcpiController] = None
        self._acpi_caps: Optional[AcpiCapabilities] = None
        self._acpi_retry_at: float = 0.0
        self._current_acpi_profile: Optional[int] = self._config.get("acpi_profile")

        # Hotkey listener
        self._hotkey_listener: Optional[HotkeyListener] = None

        # Keyboard idle auto-off
        self._idle_enabled: bool = bool(self._config.get("idle_off_enabled", False))
        self._idle_timeout: int = clamp_timeout(
            self._config.get("idle_timeout_sec", DEFAULT_TIMEOUT_SEC)
        )
        self._idle_dimmed: bool = False
        self._idle_monitor: Optional[IdleMonitor] = None
        self._use_fallback_idle: bool = False
        self._idle_lock = threading.RLock()

        # AC / Battery & Follow-System automation runtime state
        self._last_ac_online: Optional[bool] = None
        self._last_seen_system_profile: Optional[str] = None
        self._profile_sync_until: float = 0.0
        self._pending_ac_profile_target: Optional[Tuple[int, bool, float]] = None
        self._last_power_cfg_token = None
        self._cached_power_info: Optional[dict] = None
        self._notified_ac_conflicts: set[str] = set()

        # Update checker state
        self._update_available: bool = False
        self._latest_version: Optional[str] = None

        # Cached status text for instant menu builds
        self._last_status_text: str = "Status: reading..."
        self._stopped: bool = False

    # ────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────

    def start(self) -> None:
        """Initialize hardware, hotkeys, startup apply, idle, sleep, and updates."""
        self._stopped = False
        self._detect_keyboard()
        self._init_acpi()
        self._init_hotkeys()
        self._apply_on_startup()
        self._init_idle()
        self._init_sleep_handler()
        self._last_config_mtime = self._get_config_mtime()
        self.check_updates_async(force=False)

    def stop(self) -> None:
        """Cleanly stop all background listeners and restore dimmed backlight."""
        if self._stopped:
            return
        self._stopped = True

        # Ask GigaMate Center to close if running
        try:
            sock_path = get_runtime_ipc_paths()[0]
            if os.path.exists(sock_path):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    s.connect(sock_path)
                    s.sendall(b"QUIT\n")
        except Exception:
            pass

        if self._idle_dimmed:
            self._idle_dimmed = False
            try:
                cfg = load_config()
                dev = self._get_keyboard()
                if dev is not None and is_rgb_enabled(cfg):
                    pct = effective_brightness_pct(cfg)
                    if pct > 0:
                        set_rgb(dev, effective_rgb_hex(cfg), pct)
            except Exception:
                pass

        self._stop_idle()
        try:
            get_sleep_handler().stop_listening()
        except Exception:
            pass
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

    @property
    def needs_fallback_idle_poll(self) -> bool:
        return bool(self._use_fallback_idle and self._idle_enabled and self._idle_monitor is None)

    @property
    def update_available(self) -> bool:
        return self._update_available

    @property
    def latest_version(self) -> Optional[str]:
        return self._latest_version

    def _notify_ui(self) -> None:
        cb = self._on_state_changed
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    # ────────────────────────────────────────────
    # Hardware detection & self-healing
    # ────────────────────────────────────────────

    def _detect_keyboard(self) -> None:
        detected = detect_keyboards()
        if not detected:
            self._no_keyboard = True
            self._has_lamparray = False
            self._unsupported = False
            return
        self._no_keyboard = False
        self._detected_vid, self._detected_pid = detected[0]
        self._has_lamparray = hardware_supports_lamparray(
            self._detected_vid, self._detected_pid
        )
        self._unsupported = not self._has_lamparray

    def _maybe_retry_keyboard(self) -> None:
        if not self._no_keyboard:
            return
        now = time.monotonic()
        if now - self._keyboard_retry_at < _KEYBOARD_RETRY_SEC:
            return
        self._keyboard_retry_at = now
        detected = detect_keyboards()
        if not detected:
            return
        self._no_keyboard = False
        self._detected_vid, self._detected_pid = detected[0]
        self._has_lamparray = hardware_supports_lamparray(
            self._detected_vid, self._detected_pid
        )
        self._unsupported = not self._has_lamparray
        self._init_hotkeys()
        self._apply_on_startup()
        self._init_idle()

    def _get_keyboard(self):
        if self._detected_vid and self._detected_pid:
            dev = get_keyboard(vid=self._detected_vid, pid=self._detected_pid)
        else:
            dev = get_keyboard()
        if dev is None:
            self._no_keyboard = True
            return None
        self._no_keyboard = False
        return dev

    def _init_acpi(self) -> None:
        ctrl = AcpiController()
        if ctrl.available:
            self._acpi_controller = ctrl
            self._acpi_caps = ctrl.capabilities
        else:
            self._acpi_controller = None
            self._acpi_caps = None

    def _ensure_acpi_controller(self) -> bool:
        """Self-heal: re-probe ACPI backend if it became available after startup."""
        if self._acpi_controller is not None and self._acpi_controller.available:
            return False
        now = time.monotonic()
        if now - self._acpi_retry_at < _ACPI_RETRY_SEC:
            return False
        self._acpi_retry_at = now
        try:
            ctrl = AcpiController()
            available = bool(ctrl.available)
        except Exception:
            ctrl = None
            available = False
        if ctrl is None or not available:
            return False
        self._acpi_controller = ctrl
        self._acpi_caps = ctrl.capabilities
        self._last_status_text = ""
        return True

    def get_discovered_profiles(self) -> Dict[int, Dict[str, str]]:
        """Return discovered ACPI power profiles, falling back to cached caps."""
        profiles = discover_acpi_profiles()
        if not profiles and self._acpi_caps is not None:
            profiles = getattr(self._acpi_caps, "discovered_profiles", {}) or {}
        return profiles

    def _apply_on_startup(self) -> None:
        cfg = dict(self._config)
        if self._current_acpi_profile is not None:
            cfg["acpi_profile"] = self._current_acpi_profile
        apply_hardware_settings(cfg)

    # ────────────────────────────────────────────
    # Config sync (zero shadow-state drift)
    # ────────────────────────────────────────────

    def _get_config_mtime(self) -> float:
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def _effective_sync_system_power(self, cfg: dict) -> bool:
        return bool(cfg.get("sync_system_power", False)) or (
            str(cfg.get("ac_profile_mode", "off")) == "switch"
        )

    def sync_from_external_config(self) -> bool:
        """Reload configuration when changed externally by GigaMate Center or CLI."""
        mtime = self._get_config_mtime()
        if mtime <= self._last_config_mtime:
            return False
        self._last_config_mtime = mtime

        old_cfg = self._config or {}
        old_custom_hotkeys = old_cfg.get("custom_hotkeys")
        old_hotkey_overrides = old_cfg.get("hotkey_overrides")

        new_cfg = load_config()
        self._config = new_cfg

        new_profile = new_cfg.get("acpi_profile")
        if new_profile is not None and new_profile != self._current_acpi_profile:
            self._current_acpi_profile = new_profile
            if self._effective_sync_system_power(new_cfg):
                self._mark_profile_synced(new_profile)

        new_idle_off = bool(new_cfg.get("idle_off_enabled", False))
        new_idle_sec = clamp_timeout(
            new_cfg.get("idle_timeout_sec", DEFAULT_TIMEOUT_SEC)
        )
        if (new_idle_off != self._idle_enabled) or (new_idle_sec != self._idle_timeout):
            self._idle_enabled = new_idle_off
            self._idle_timeout = new_idle_sec
            self._init_idle()

        if (
            new_cfg.get("custom_hotkeys") != old_custom_hotkeys
            or new_cfg.get("hotkey_overrides") != old_hotkey_overrides
        ):
            self._init_hotkeys()

        return True

    # ────────────────────────────────────────────
    # Hotkeys & Center IPC
    # ────────────────────────────────────────────

    def _resolve_hotkey_specs(self):
        try:
            return resolve_hotkey_specs(cfg=self._config)
        except Exception:
            return {}

    def _init_hotkeys(self) -> None:
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None

        vid = self._detected_vid or 0x0414
        pid = self._detected_pid or 0x8105

        specs = self._resolve_hotkey_specs()
        if not specs:
            return
        self._hotkey_listener = HotkeyListener(
            on_action=self._on_hotkey_action,
            vid=vid,
            pid=pid,
            specs=specs,
        )
        self._hotkey_listener.start()

    def _on_hotkey_action(self, action: str) -> None:
        if action == "mode_switch":
            self.cycle_profile_hotkey()
        elif action == "open_center":
            self.open_center()
        else:
            logger.warning("Unknown hotkey action: %s", action)

    def open_center(self) -> None:
        """Raise an existing Center instance via IPC, else launch one detached."""
        sock_path = get_runtime_ipc_paths()[0]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.8)
                s.connect(sock_path)
                s.sendall(b"ACTIVATE\n")
                return
        except Exception:
            pass

        try:
            subprocess.Popen(["gigamate", "center"], start_new_session=True)
        except Exception:
            pass

    def cycle_profile_hotkey(self) -> Optional[int]:
        """Cycle through enabled, dGPU-allowed profiles and show OSD."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return None

        cfg = load_config()
        self._config = cfg
        p_data = enabled_acpi_profiles(self.get_discovered_profiles(), cfg)
        if not p_data:
            return None
        pids = sorted(int(k) for k in p_data.keys())
        if not dgpu_gate.high_power_profiles_allowed():
            filtered = [p for p in pids if p not in (2, 3)]
            if filtered:
                pids = filtered
        if not pids:
            return None

        current_val = self._current_acpi_profile
        if current_val is None:
            current_fp = self._acpi_controller.get_profile()
            current_val = current_fp.value if current_fp is not None else pids[0]

        try:
            next_idx = (pids.index(current_val) + 1) % len(pids)
        except ValueError:
            next_idx = 0

        next_profile_id = pids[next_idx]
        FanProfile(next_profile_id)  # validate enum value
        sync_sys = self._effective_sync_system_power(cfg)

        try:
            if hardware.apply_profile(next_profile_id, cfg=None, sync_system=sync_sys):
                self._current_acpi_profile = next_profile_id
                self._last_config_mtime = self._get_config_mtime()
                if sync_sys:
                    self._mark_profile_synced(next_profile_id)

                entry = p_data.get(next_profile_id) or p_data.get(str(next_profile_id)) or {}
                name = entry.get("name", f"Profile {next_profile_id}")
                desc = entry.get("description") or entry.get("desc", "")
                show_profile_osd(name, desc)
                self._notify_ui()
                return next_profile_id
        except Exception:
            pass
        return None

    # ────────────────────────────────────────────
    # Direct user actions (Tray UI -> Daemon)
    # ────────────────────────────────────────────

    def set_profile(self, profile_id: int) -> bool:
        """Apply a user-selected power profile if allowed by dGPU gate."""
        if self._acpi_controller is None or not self._acpi_controller.available:
            return False
        if profile_id in (2, 3) and not dgpu_gate.high_power_profiles_allowed():
            return False
        cfg = load_config()
        self._config = cfg
        sync_sys = self._effective_sync_system_power(cfg)
        try:
            ok = bool(hardware.apply_profile(profile_id, cfg=None, sync_system=sync_sys))
        except Exception:
            return False
        if ok:
            self._current_acpi_profile = profile_id
            self._last_config_mtime = self._get_config_mtime()
            if sync_sys:
                self._mark_profile_synced(profile_id)
        return ok

    def set_backlight_pct(self, pct: int) -> bool:
        """Persist and apply keyboard backlight brightness (0 = off, 1-100%)."""
        pct = max(0, min(100, int(pct)))
        self._idle_dimmed = False
        self._config = update_config(
            lambda c: {
                **c,
                "rgb_enabled": pct > 0,
                **(
                    {
                        "brightness_pct": pct,
                        "brightness": pct_to_legacy_brightness(pct),
                    }
                    if pct > 0
                    else {"brightness": 0}
                ),
            }
        )
        self._last_config_mtime = self._get_config_mtime()

        dev = self._get_keyboard()
        if dev is None:
            return False
        try:
            if pct == 0:
                set_off(dev)
            else:
                set_rgb(dev, effective_rgb_hex(self._config), pct)
            return True
        except Exception:
            return False

    # ────────────────────────────────────────────
    # Keyboard idle auto-off & sleep/resume hooks
    # ────────────────────────────────────────────

    def _init_idle(self) -> None:
        with self._idle_lock:
            self._stop_idle_locked()
            self._idle_dimmed = False
            self._use_fallback_idle = False
            if not self._idle_enabled:
                return
            if self._no_keyboard and not self._has_lamparray:
                return
            monitor = IdleMonitor(
                on_idle=self._on_idle_fired,
                on_active=self._on_idle_active,
                timeout_sec=self._idle_timeout,
                enabled=True,
            )
            if monitor.start():
                self._idle_monitor = monitor
                return
            ms, _name = fallback_idle_ms()
            if ms is not None:
                self._use_fallback_idle = True

    def _stop_idle(self) -> None:
        with self._idle_lock:
            self._stop_idle_locked()

    def _stop_idle_locked(self) -> None:
        if self._idle_monitor is not None:
            try:
                self._idle_monitor.stop()
            except Exception:
                pass
            self._idle_monitor = None
        self._use_fallback_idle = False

    def _on_idle_fired(self) -> None:
        """Temporarily turn off backlight after idle timeout (no config write)."""
        if not self._idle_enabled or self._idle_dimmed:
            return
        cfg = load_config()
        self._config = cfg
        if not is_rgb_enabled(cfg) or effective_brightness_pct(cfg) <= 0:
            return
        if cfg.get("idle_keep_on_ac", False) and get_battery_manager().is_ac_online():
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        try:
            set_off(dev)
            self._idle_dimmed = True
        except Exception:
            pass

    def _on_idle_active(self) -> None:
        """Restore saved backlight on first input after idle."""
        if not self._idle_dimmed:
            return
        self._idle_dimmed = False
        cfg = load_config()
        self._config = cfg
        if not is_rgb_enabled(cfg):
            return
        pct = effective_brightness_pct(cfg)
        if pct <= 0:
            return
        dev = self._get_keyboard()
        if dev is None:
            return
        try:
            set_rgb(dev, effective_rgb_hex(cfg), pct)
        except Exception:
            pass

    def poll_fallback_idle(self) -> bool:
        """Poll platform idle backend when evdev is unavailable."""
        if not self._idle_enabled or self._idle_monitor is not None:
            return False
        try:
            ms, _name = fallback_idle_ms()
        except Exception:
            return True
        if ms is None:
            return True
        if ms >= self._idle_timeout * 1000:
            self._on_idle_fired()
        else:
            self._on_idle_active()
        return True

    def _init_sleep_handler(self) -> None:
        try:
            handler = get_sleep_handler()
            handler.set_hooks(self._on_suspend_hook, self._on_resume_hook)
            handler.start_listening()
        except Exception:
            pass

    def _on_suspend_hook(self) -> None:
        self._stop_idle()

    def _on_resume_hook(self) -> None:
        if self._idle_dimmed:
            self._on_idle_active()
        self._init_idle()

    # ────────────────────────────────────────────
    # Updates
    # ────────────────────────────────────────────

    def check_updates_async(self, force: bool = False) -> None:
        t = threading.Thread(
            target=self._check_updates_bg,
            args=(force,),
            name="gigamate-update-check",
            daemon=True,
        )
        t.start()

    def _check_updates_bg(self, force: bool) -> None:
        try:
            result = update_checker.check_for_updates(force=force)
        except Exception:
            return
        self._apply_update_result(result)

    def _apply_update_result(self, result: dict) -> None:
        changed = (
            bool(result.get("update_available")) != self._update_available
            or result.get("latest") != self._latest_version
        )
        self._update_available = bool(result.get("update_available"))
        self._latest_version = result.get("latest")
        if changed:
            self._notify_ui()

    def tray_icon_key(self, gpu: Optional[GpuState] = None) -> str:
        """Combine dGPU state with update badge into one of 6 icon keys."""
        base = gpu_icon_key(gpu if gpu is not None else get_gpu_state())
        if self._update_available:
            return f"{base}-update" if base != "gigamate" else "gigamate-update"
        return base

    # ────────────────────────────────────────────
    # Polling, menu state & profile automation
    # ────────────────────────────────────────────

    @staticmethod
    def desired_poll_interval_ms(ac_online: Optional[bool]) -> int:
        if ac_online is False:
            return STATUS_POLL_BATTERY_MS
        return STATUS_POLL_INTERVAL_MS

    def get_menu_state(self) -> dict:
        """Build fresh, live menu state directly from config + hardware gate.

        Called right before showing the tray menu so profile sensitivity,
        enabled profiles, active profile, and backlight step can never drift.
        """
        self.sync_from_external_config()
        cfg = load_config()
        self._config = cfg
        acpi_ok = self._acpi_controller is not None and bool(self._acpi_controller.available)
        enabled = (
            enabled_acpi_profiles(self.get_discovered_profiles(), cfg)
            if acpi_ok
            else {}
        )
        active_prof = cfg.get("acpi_profile", self._current_acpi_profile)
        if active_prof is not None:
            self._current_acpi_profile = int(active_prof)
        rgb_on = is_rgb_enabled(cfg)
        backlight_pct = effective_brightness_pct(cfg) if rgb_on else 0
        return {
            "status_text": self._last_status_text or "Status: N/A",
            "acpi_available": acpi_ok,
            "has_lamparray": bool(self._has_lamparray),
            "enabled_profiles": enabled,
            "active_profile": self._current_acpi_profile,
            "high_power_profiles_allowed": bool(dgpu_gate.high_power_profiles_allowed()),
            "backlight_pct": backlight_pct,
            "update_available": self._update_available,
            "latest_version": self._latest_version,
        }

    def poll_tick(self, *, run_automation: bool = True) -> DaemonSnapshot:
        """Run one periodic status + automation tick and return a fresh snapshot."""
        self.sync_from_external_config()
        self._ensure_acpi_controller()
        self._maybe_retry_keyboard()

        gpu = get_gpu_state()
        icon_key = self.tray_icon_key(gpu)
        parts = []

        acpi_ok = self._acpi_controller is not None and bool(self._acpi_controller.available)
        if acpi_ok:
            state: Optional[FanState] = None
            try:
                state = self._acpi_controller.read_state()
            except Exception:
                pass
            if state is not None:
                if state.temp_cpu is not None:
                    parts.append(f"CPU: {state.temp_cpu} C")
                elif state.temp_socket is not None:
                    parts.append(f"Socket: {state.temp_socket} C")
                if state.fan1_rpm is not None:
                    parts.append(f"Fan: {state.fan1_rpm} RPM")
                if state.duty_cpu is not None:
                    parts.append(f"{state.duty_cpu}%")
                if state.profile is not None:
                    profiles = self.get_discovered_profiles()
                    entry = profiles.get(state.profile.value) or profiles.get(
                        str(state.profile.value), {}
                    )
                    pname = entry.get("name", str(state.profile.value))
                    parts.append(f"Profile: {pname}")

        if gpu.present:
            parts.append(f"dGPU: {gpu_short_status_text(gpu)}")

        ac_online: Optional[bool] = None
        try:
            bat_mgr = get_battery_manager()
            bat_info = bat_mgr.get_battery_info()
            if bat_info.present:
                ac_online = bool(bat_info.ac_online)
                cal = evaluate_calibration_state(bat_mgr, self._config)
                if cal.get("event") in ("completed", "timeout", "auto_started"):
                    self.sync_from_external_config()
                ac_str = "AC" if bat_info.ac_online else "Bat"
                if self._config.get("charge_topup_active"):
                    parts.append(f"Batt: {bat_info.capacity}% ({ac_str} - 100% Top-Up)")
                else:
                    parts.append(f"Batt: {bat_info.capacity}% ({ac_str})")
        except Exception:
            pass

        # Restore idle-dimmed backlight immediately when AC comes online
        if (
            self._idle_dimmed
            and ac_online is True
            and self._last_ac_online is False
            and self._config.get("idle_keep_on_ac", False)
        ):
            self._on_idle_active()

        if run_automation:
            self._run_profile_automation(ac_online)

        if ac_online is not None:
            self._last_ac_online = ac_online

        status_text = "  |  ".join(parts) if parts else "Status: N/A"
        self._last_status_text = status_text

        cfg = self._config
        enabled = (
            enabled_acpi_profiles(self.get_discovered_profiles(), cfg)
            if acpi_ok
            else {}
        )
        rgb_on = is_rgb_enabled(cfg)
        backlight_pct = effective_brightness_pct(cfg) if rgb_on else 0

        return DaemonSnapshot(
            status_text=status_text,
            icon_key=icon_key,
            ac_online=ac_online,
            poll_interval_ms=self.desired_poll_interval_ms(ac_online),
            acpi_available=acpi_ok,
            has_lamparray=bool(self._has_lamparray),
            active_profile=self._current_acpi_profile,
            enabled_profiles=enabled,
            high_power_profiles_allowed=bool(dgpu_gate.high_power_profiles_allowed()),
            backlight_pct=backlight_pct,
            update_available=self._update_available,
            latest_version=self._latest_version,
        )

    def _mark_profile_synced(self, profile_id: int, grace_sec: float = 4.0) -> None:
        """Record a GigaMate-initiated switch and hold follow_system briefly.

        ``tlp-pd`` spawns detached ``tlp`` asynchronously on ``Set(ActiveProfile)``
        so ``Get(ActiveProfile)`` can return the previous profile for ~0.5-2 s.
        """
        mapped = DEFAULT_PROFILE_MAP.get(int(profile_id))
        if mapped is not None:
            self._last_seen_system_profile = mapped
        self._profile_sync_until = time.monotonic() + grace_sec

    def _run_profile_automation(self, ac_online: Optional[bool]) -> None:
        """Apply AC/Battery or Follow-System power profile automation on transitions."""
        cfg = load_config()
        self._config = cfg
        ac_profile_mode = cfg.get("ac_profile_mode", "off")
        target: Optional[int] = None
        sync_sys = True

        if ac_profile_mode == "switch":
            token = power_config_mtime_token()
            if token != self._last_power_cfg_token or self._cached_power_info is None:
                self._last_power_cfg_token = token
                self._cached_power_info = detect_power_managers()

            power_info = self._cached_power_info
            conflicts = power_info.get("conflicts", [])
            if conflicts:
                conflict_ids = {c["id"] for c in conflicts if "id" in c}
                new_ids = conflict_ids - self._notified_ac_conflicts
                if new_ids:
                    self._notified_ac_conflicts.update(new_ids)
                    try:
                        if shutil.which("notify-send"):
                            subprocess.Popen(
                                [
                                    "notify-send",
                                    "GigaMate — External Power Manager Detected",
                                    "An external power manager re-enabled AC auto-switching. "
                                    "OS profile pushes are paused until resolved in Automation.",
                                ],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                    except Exception:
                        pass
            else:
                self._notified_ac_conflicts.clear()

            prev = self._last_ac_online
            if prev is not None and ac_online is not None and ac_online != prev:
                target = int(
                    cfg.get("ac_profile_id", 1)
                    if ac_online
                    else cfg.get("battery_profile_id", 0)
                )
                has_ext = power_info.get("has_external_ac_switching", False)
                sync_sys = not has_ext
        elif ac_profile_mode == "follow_system":
            try:
                sys_prof = SystemPowerManager().get_active_profile()
            except Exception:
                sys_prof = None
            if not sys_prof:
                return
            now = time.monotonic()
            if now < self._profile_sync_until:
                if sys_prof == self._last_seen_system_profile:
                    self._profile_sync_until = 0.0
                return
            if self._last_seen_system_profile is None:
                self._last_seen_system_profile = sys_prof
                return
            if sys_prof != self._last_seen_system_profile:
                self._last_seen_system_profile = sys_prof
                mapped = SYSTEM_TO_GIGAMATE_PROFILE.get(sys_prof)
                already_matches = (
                    mapped == self._current_acpi_profile
                    or (sys_prof == "performance" and self._current_acpi_profile in (2, 3))
                )
                if mapped is not None and not already_matches:
                    target = int(mapped)
                    sync_sys = False

        if target is not None:
            disabled = set(cfg.get("disabled_profiles", []))
            if target in disabled:
                enabled = enabled_acpi_profiles(self.get_discovered_profiles(), cfg)
                if 1 in enabled:
                    target = 1
                elif enabled:
                    target = sorted(int(k) for k in enabled.keys())[0]
                else:
                    target = None

        if ac_online is False:
            self._pending_ac_profile_target = None
            if target in (2, 3) and dgpu_gate.configured_mode() in (
                dgpu_gate.MODE_AUTO,
                dgpu_gate.MODE_ECO,
            ):
                enabled = enabled_acpi_profiles(self.get_discovered_profiles(), cfg)
                if 0 in enabled:
                    target = 0
                elif 1 in enabled:
                    target = 1
                else:
                    safe = [int(k) for k in sorted(enabled.keys()) if int(k) not in (2, 3)]
                    target = safe[0] if safe else None
        elif ac_online is True:
            pending = self._pending_ac_profile_target
            if (
                ac_profile_mode == "switch"
                and target in (2, 3)
                and not dgpu_gate.high_power_profiles_allowed()
            ):
                self._pending_ac_profile_target = (target, sync_sys, time.monotonic() + 20.0)
                target = None
            elif target is None and pending is not None:
                p_target, p_sync, p_deadline = pending
                if time.monotonic() > p_deadline:
                    self._pending_ac_profile_target = None
                elif dgpu_gate.high_power_profiles_allowed():
                    self._pending_ac_profile_target = None
                    target, sync_sys = p_target, p_sync

        if target in (2, 3) and not dgpu_gate.high_power_profiles_allowed():
            target = None

        if target is not None:
            try:
                hardware.apply_profile(target, cfg=cfg, sync_system=sync_sys)
                self._current_acpi_profile = target
                self._last_config_mtime = self._get_config_mtime()
            except Exception:
                pass
