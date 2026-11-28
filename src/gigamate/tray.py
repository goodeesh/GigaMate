"""GigaMate — PyQt6 System Tray Application.

Thin, stateless Qt6 ``QSystemTrayIcon`` shell over :class:`gigamate.daemon.GigaMateDaemon`.
Rebuilds its compact menu fresh from live config and hardware state on every
``QMenu.aboutToShow`` (and whenever polled state changes), eliminating stale
labels, stuck ``(dGPU off)`` sensitivity, and GTK/AppIndicator3 dependencies.
"""

from __future__ import annotations

import fcntl
import logging
import os
import random
import signal
import subprocess
import sys
import threading
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .daemon import (
    DaemonSnapshot,
    GigaMateDaemon,
    IDLE_FALLBACK_POLL_MS,
    STATUS_POLL_INTERVAL_MS,
    UPDATE_CHECK_INTERVAL_MS,
)
from .paths import ICON_PATHS, get_runtime_dir
from . import updates as update_checker

logger = logging.getLogger(__name__)

APP_ID = "gigamate"
APP_ICON_PATH = ICON_PATHS["gigamate"]
APP_ICON_PATHS = ICON_PATHS

BACKLIGHT_STEPS: Tuple[Tuple[str, int], ...] = (
    ("Off", 0),
    ("15%", 15),
    ("50%", 50),
    ("100%", 100),
)


class GigaMateTrayApp(QObject):
    """Thin PyQt6 QSystemTrayIcon shell wrapping :class:`GigaMateDaemon`."""

    _refresh_requested = pyqtSignal()

    def __init__(
        self,
        daemon: Optional[GigaMateDaemon] = None,
        *,
        start_daemon: bool = True,
    ) -> None:
        super().__init__()
        self.daemon = daemon if daemon is not None else GigaMateDaemon(
            on_state_changed=self._schedule_refresh,
        )
        self._icon_key: Optional[str] = None
        self._last_tooltip_text: Optional[str] = None
        self._last_menu_sig: Optional[tuple] = None
        self._quitting: bool = False

        # Strong references to prevent GC of submenus / action groups while open
        self._submenus: List[QMenu] = []
        self._action_groups: List[QActionGroup] = []
        self._profile_actions: Dict[int, QAction] = {}
        self._backlight_actions: Dict[int, QAction] = {}

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._populate_menu)

        self.tray_icon = QSystemTrayIcon(QIcon(APP_ICON_PATH), self)
        self.tray_icon.setToolTip("GigaMate")
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

        self._refresh_requested.connect(self._on_refresh_requested)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_POLL_INTERVAL_MS)
        self._status_timer.timeout.connect(self._on_poll_tick)

        self._fallback_idle_timer = QTimer(self)
        self._fallback_idle_timer.setInterval(IDLE_FALLBACK_POLL_MS)
        self._fallback_idle_timer.timeout.connect(self._on_fallback_idle_tick)

        self._update_timer = QTimer(self)
        jitter_ms = random.randint(0, 30 * 60 * 1000)
        self._update_timer.setInterval(UPDATE_CHECK_INTERVAL_MS + jitter_ms)
        self._update_timer.timeout.connect(
            lambda: self.daemon.check_updates_async(force=False)
        )

        if start_daemon:
            self.daemon.start()
            self._apply_snapshot(self.daemon.poll_tick(run_automation=False))
            self._populate_menu()
            self.tray_icon.show()
            self._status_timer.start()
            self._sync_fallback_idle_timer()
            self._update_timer.start()
        else:
            self._populate_menu()

    # ────────────────────────────────────────────
    # Thread-safe UI refresh
    # ────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        if not self._quitting:
            self._refresh_requested.emit()

    def _on_refresh_requested(self) -> None:
        if self._quitting:
            return
        self._apply_snapshot(self.daemon.poll_tick(run_automation=False))

    # ────────────────────────────────────────────
    # Menu construction (stateless, rebuilt fresh)
    # ────────────────────────────────────────────

    @staticmethod
    def _nearest_backlight_step(active_pct: int) -> int:
        if active_pct <= 0:
            return 0
        return min(
            (val for _lbl, val in BACKLIGHT_STEPS if val > 0),
            key=lambda v: abs(v - active_pct),
        )

    def _populate_menu(self) -> None:
        """Rebuild the compact v5.0 menu fresh from live daemon state."""
        state = self.daemon.get_menu_state()

        self.menu.clear()
        self._submenus.clear()
        self._action_groups.clear()
        self._profile_actions.clear()
        self._backlight_actions.clear()

        # 1. Open GigaMate Center
        open_act = QAction("GigaMate Center...", self.menu)
        open_act.triggered.connect(lambda _checked=False: self.daemon.open_center())
        self.menu.addAction(open_act)
        self.menu.addSeparator()

        # 2. Live status summary
        status_act = QAction(state["status_text"], self.menu)
        status_act.setEnabled(False)
        self.menu.addAction(status_act)

        # 3. Power Profile radio group
        if state["acpi_available"]:
            self.menu.addSeparator()
            hdr = QAction("Power Profile", self.menu)
            hdr.setEnabled(False)
            self.menu.addAction(hdr)

            enabled_profiles = state["enabled_profiles"]
            if not enabled_profiles:
                empty_act = QAction("No power profiles exposed by BIOS", self.menu)
                empty_act.setEnabled(False)
                self.menu.addAction(empty_act)
            else:
                high_ok = bool(state["high_power_profiles_allowed"])
                active_prof = state["active_profile"]
                group = QActionGroup(self.menu)
                group.setExclusive(True)
                self._action_groups.append(group)

                for pid_int in sorted(int(k) for k in enabled_profiles.keys()):
                    entry = (
                        enabled_profiles.get(pid_int)
                        or enabled_profiles.get(str(pid_int), {})
                    )
                    label = entry.get("name", f"Profile {pid_int}")
                    allowed = high_ok or pid_int not in (2, 3)
                    disp = (
                        f"{label} (dGPU off)"
                        if (not high_ok and pid_int in (2, 3))
                        else label
                    )
                    act = QAction(disp, self.menu)
                    act.setCheckable(True)
                    act.setChecked(active_prof == pid_int)
                    act.setEnabled(allowed)
                    act.triggered.connect(
                        lambda _checked=False, p=pid_int: self._on_profile_selected(p)
                    )
                    group.addAction(act)
                    self.menu.addAction(act)
                    self._profile_actions[pid_int] = act

        # 4. Backlight submenu (LampArray full RGB)
        if state["has_lamparray"]:
            self.menu.addSeparator()
            sub = QMenu("Backlight", self.menu)
            self._submenus.append(sub)

            b_group = QActionGroup(sub)
            b_group.setExclusive(True)
            self._action_groups.append(b_group)

            nearest = self._nearest_backlight_step(int(state["backlight_pct"]))
            for label, val in BACKLIGHT_STEPS:
                act = QAction(label, sub)
                act.setCheckable(True)
                act.setChecked(val == nearest)
                # Qt fires triggered() even if the action is already checked,
                # so re-clicking 100% always re-applies hardware brightness.
                act.triggered.connect(
                    lambda _checked=False, v=val: self._on_backlight_selected(v)
                )
                b_group.addAction(act)
                sub.addAction(act)
                self._backlight_actions[val] = act
            self.menu.addMenu(sub)

        # 5. Optional Update Available action
        if state["update_available"] and state["latest_version"]:
            self.menu.addSeparator()
            upd_act = QAction(update_checker.menu_item_label(True), self.menu)
            upd_act.triggered.connect(lambda _checked=False: self._on_update_clicked())
            self.menu.addAction(upd_act)

        # 6. Quit
        self.menu.addSeparator()
        quit_act = QAction("Quit", self.menu)
        quit_act.triggered.connect(lambda _checked=False: self.quit())
        self.menu.addAction(quit_act)

    # ────────────────────────────────────────────
    # Polling & icon/tooltip updates
    # ────────────────────────────────────────────

    def _on_poll_tick(self) -> None:
        if self._quitting:
            return
        snap = self.daemon.poll_tick(run_automation=True)
        self._apply_snapshot(snap)
        self._sync_fallback_idle_timer()

    def _apply_snapshot(self, snap: DaemonSnapshot) -> None:
        if snap.icon_key != self._icon_key:
            self._icon_key = snap.icon_key
            icon_path = APP_ICON_PATHS.get(snap.icon_key, APP_ICON_PATH)
            self.tray_icon.setIcon(QIcon(icon_path))

        if snap.status_text != self._last_tooltip_text:
            self._last_tooltip_text = snap.status_text
            self.tray_icon.setToolTip(f"GigaMate\n{snap.status_text}")

        if self._status_timer.interval() != snap.poll_interval_ms:
            self._status_timer.setInterval(snap.poll_interval_ms)

        menu_sig = (
            snap.status_text,
            snap.acpi_available,
            snap.has_lamparray,
            snap.active_profile,
            tuple(sorted(int(k) for k in snap.enabled_profiles.keys())),
            snap.high_power_profiles_allowed,
            self._nearest_backlight_step(snap.backlight_pct),
            snap.update_available,
            snap.latest_version,
        )
        if menu_sig != self._last_menu_sig:
            self._last_menu_sig = menu_sig
            if not self.menu.isVisible():
                self._populate_menu()

    def _sync_fallback_idle_timer(self) -> None:
        if self.daemon.needs_fallback_idle_poll:
            if not self._fallback_idle_timer.isActive():
                self._fallback_idle_timer.start()
        elif self._fallback_idle_timer.isActive():
            self._fallback_idle_timer.stop()

    def _on_fallback_idle_tick(self) -> None:
        if not self.daemon.poll_fallback_idle():
            self._fallback_idle_timer.stop()

    # ────────────────────────────────────────────
    # User actions
    # ────────────────────────────────────────────

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.daemon.open_center()

    def _on_profile_selected(self, profile_id: int) -> None:
        if self.daemon.set_profile(profile_id):
            self._apply_snapshot(self.daemon.poll_tick(run_automation=False))
        else:
            self._populate_menu()

    def _on_backlight_selected(self, pct: int) -> None:
        self.daemon.set_backlight_pct(pct)
        self._populate_menu()

    def _on_update_clicked(self) -> None:
        try:
            admin = update_checker.admin_status()
        except Exception:
            admin = "password"
        if admin in ("denied", "no-sudo"):
            QMessageBox.warning(
                None,
                "Administrator rights required",
                "Updating drivers needs sudo, which is not available "
                f"for your user (status: {admin}).\n\n"
                f"{update_checker.manual_update_instructions()}",
            )
            return

        use_terminal = admin == "password"
        current = update_checker.get_installed_version()
        latest = self.daemon.latest_version or "latest"
        if use_terminal:
            body = (
                "A terminal window will open and run the update there "
                "(re-runs install.sh for the latest tagged release, "
                "including drivers and tray). Enter your password in "
                "the terminal when asked.\n\n"
                "Update now?"
            )
        else:
            body = (
                "GigaMate will update in the background (re-runs install.sh "
                "for the latest tagged release, including drivers and tray).\n"
                "No password prompt is expected (passwordless sudo).\n\n"
                "Update now?"
            )
        resp = QMessageBox.question(
            None,
            f"Update available ({current} → {latest})",
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        if use_terminal:
            argv = update_checker.build_terminal_update_command(
                desktop=os.environ.get("XDG_CURRENT_DESKTOP", ""),
                tag=self.daemon.latest_version,
            )
            if argv is None:
                QMessageBox.warning(
                    None,
                    "Administrator rights required",
                    "Could not locate a terminal emulator.\n\n"
                    f"{update_checker.manual_update_instructions()}",
                )
                return
            try:
                subprocess.Popen(argv, start_new_session=True)
            except Exception:
                QMessageBox.critical(
                    None,
                    "Update failed",
                    f"Could not open a terminal.\n\nLog: {update_checker.UPDATE_LOG_FILE}\n\n"
                    f"{update_checker.manual_update_instructions()}",
                )
                return
            QMessageBox.information(
                None,
                "Updating in terminal",
                "The update is running in the terminal window.\n"
                "Enter your password there when asked.",
            )
        else:
            if self.daemon.latest_version:
                update_checker.dismiss_version(self.daemon.latest_version)
            try:
                proc = subprocess.Popen(
                    update_checker.build_update_command(tag=self.daemon.latest_version),
                    start_new_session=True,
                )
            except Exception:
                update_checker.undismiss()
                QMessageBox.critical(
                    None,
                    "Update failed",
                    f"Could not start the updater.\n\nLog: {update_checker.UPDATE_LOG_FILE}\n\n"
                    f"{update_checker.manual_update_instructions()}",
                )
                return

            def _watch() -> None:
                rc = proc.wait()
                if rc != 0:
                    update_checker.undismiss()
                    self._schedule_refresh()

            threading.Thread(target=_watch, name="gigamate-update-watch", daemon=True).start()
            QMessageBox.information(
                None,
                "Updating in background",
                "The update is running. The tray will restart automatically "
                "when install.sh finishes (systemd service restart).\n"
                f"Progress log: {update_checker.UPDATE_LOG_FILE}",
            )

    def quit(self) -> None:
        """Stop timers, shut down daemon cleanly, and exit Qt event loop."""
        if self._quitting:
            return
        self._quitting = True
        self._status_timer.stop()
        self._fallback_idle_timer.stop()
        self._update_timer.stop()
        try:
            self.tray_icon.hide()
        except Exception:
            pass
        self.daemon.stop()
        app = QApplication.instance()
        if app is not None:
            app.quit()


def main() -> None:
    """Entry point for the GigaMate tray application."""
    if os.geteuid() == 0:
        print(
            "GigaMate Tray must be run as your desktop user, not root.\n"
            "Running it with sudo would create root-owned settings and cannot "
            "reach your user session. Re-run as your normal user.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Single-instance guard to prevent duplicate tray icons
    runtime_dir = get_runtime_dir()
    lock_file_path = runtime_dir / f"gigamate-tray-{os.getuid()}.lock"
    try:
        lock_fd = open(lock_file_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Tray already running: focus/raise GigaMate Center instead
        try:
            subprocess.Popen(["gigamate", "center"], start_new_session=True)
        except Exception:
            pass
        return

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("GigaMate")
    qt_app.setDesktopFileName("gigamate")
    qt_app.setQuitOnLastWindowClosed(False)

    tray_app = GigaMateTrayApp(start_daemon=True)

    # Periodic no-op wakeup so Python signal handlers run promptly inside Qt loop
    sig_timer = QTimer()
    sig_timer.start(500)
    sig_timer.timeout.connect(lambda: None)

    def _handle_signal(_signum, _frame) -> None:
        tray_app.quit()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        qt_app.exec()
    finally:
        tray_app.quit()
        try:
            lock_fd.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
