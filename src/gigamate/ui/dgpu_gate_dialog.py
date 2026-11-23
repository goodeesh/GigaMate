"""Standalone active confirmation window for cutting dGPU power (AI Power Gear III).

Launched both from the system tray / GigaMate Center when switching to ECO or
battery Auto mode, and proactively by the background watcher when AC power is
unplugged in Auto mode. Lists any apps or user services currently holding
``/dev/nvidia*`` so the user can choose either to close them and power off the
dGPU immediately, or revert to Optimus to keep them running.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import queue
import sys
import threading
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import dgpu_gate
from ..battery import get_battery_manager
from . import icons
from .styles import apply_theme, dialog_stylesheet
from .theme import THEME
from .widgets import M3Card


class DgpuGateConfirmDialog(QDialog):
    """Modal confirmation window enumerating dGPU holders before gating off."""

    def __init__(
        self,
        trigger: str = "eco",
        target_mode: str = dgpu_gate.MODE_ECO,
        prev_mode: Optional[str] = None,
        parent: Optional[QWidget] = None,
        start_timers: bool = True,
    ) -> None:
        super().__init__(parent)
        self.trigger = trigger
        self.target_mode = (
            target_mode if target_mode in dgpu_gate.VALID_MODES else dgpu_gate.MODE_ECO
        )
        self.prev_mode = prev_mode
        self._busy = False
        self._user_acted = False
        self._auto_dismissed = False
        self._results: queue.Queue = queue.Queue()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._collect_result)

        self._ac_timer = QTimer(self)
        self._ac_timer.setInterval(1000)
        self._ac_timer.timeout.connect(self._check_ac_returned)

        self.setWindowTitle("GigaMate — dGPU Power Gate")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumWidth(580)
        self.setMaximumWidth(660)
        self.setStyleSheet(dialog_stylesheet())

        self._scan_state()
        self._build_ui()

        if start_timers and self.trigger == "auto_ac_unplug":
            self._ac_timer.start()

    def _scan_state(self) -> None:
        self.topo = dgpu_gate.find_dgpu_topology()
        self.displays: List[str] = (
            dgpu_gate.connected_dgpu_displays(self.topo.bdf) if self.topo.bdf else []
        )
        self.closable: List[dgpu_gate.HolderProcess] = dgpu_gate.closable_gpu_holders()
        self.audio_holders: List[str] = (
            dgpu_gate.audio_card_holders(self.topo.audio_bdf)
            if self.topo.audio_bdf
            else []
        )

    def _header_copy(self) -> tuple[str, str]:
        if self.trigger == "auto_ac_unplug":
            return (
                "Battery Power Detected — Power Off dGPU?",
                "AC power was disconnected while GigaMate is in Auto mode. "
                "Power off the discrete GPU rail now to maximize battery life, or "
                "revert to Optimus to keep running GPU apps untouched.",
            )
        if self.trigger == "auto":
            return (
                "Switch to Auto Mode — Power Off dGPU?",
                "Laptop is currently on battery. Confirm powering off the "
                "discrete GPU rail now, or revert to Optimus to keep apps open.",
            )
        return (
            "Switch to ECO Mode — Power Off dGPU?",
            "Cutting the discrete GPU power rail drops idle dGPU draw to ~0 W "
            "until you switch back to Optimus (or plug in AC in Auto mode).",
        )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        title_txt, sub_txt = self._header_copy()
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(14)

        if self.displays:
            badge_bg, badge_fg, badge_icon = THEME.error_container, THEME.error, "monitor"
        elif self.closable:
            badge_bg, badge_fg, badge_icon = THEME.warning_container, THEME.warning, "warning"
        else:
            badge_bg, badge_fg, badge_icon = THEME.gated_container, THEME.gated, "power_off"

        icon_badge = QLabel()
        icon_badge.setFixedSize(40, 40)
        icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_badge.setPixmap(icons.render_icon_pixmap(badge_icon, color=badge_fg, size=20))
        icon_badge.setStyleSheet(
            f"background-color: {badge_bg}; border-radius: 20px;"
        )
        hdr_row.addWidget(icon_badge, alignment=Qt.AlignmentFlag.AlignTop)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(4)

        self.title_lbl = QLabel(title_txt)
        self.title_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 18px; font-weight: 700; background: transparent;"
        )
        self.title_lbl.setWordWrap(True)
        title_col.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel(sub_txt)
        self.subtitle_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_body}px; background: transparent;"
        )
        self.subtitle_lbl.setWordWrap(True)
        title_col.addWidget(self.subtitle_lbl)
        hdr_row.addLayout(title_col, 1)
        root.addLayout(hdr_row)

        # Summary of hardware effects card
        info_card = M3Card()
        info_lay = QVBoxLayout(info_card)
        info_lay.setContentsMargins(16, 14, 16, 14)
        info_lay.setSpacing(6)
        info_hdr = QLabel("What this action will do")
        info_hdr.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: {THEME.text_section}px; font-weight: 700; background: transparent;"
        )
        info_lay.addWidget(info_hdr)
        info_body = QLabel(
            "• Cut the dGPU power rail via ACPI and remove its PCI device (~0 W draw)\n"
            "• Preserve your saved undervolt / overclock profile (re-applies automatically on wake)\n"
            "• Disable HDMI/DisplayPort outputs wired directly to the dGPU while off"
        )
        info_body.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_caption}px; background: transparent;"
        )
        info_body.setWordWrap(True)
        info_lay.addWidget(info_body)
        root.addWidget(info_card)

        # Holders / blocker section
        self.holders_card = QFrame()
        holders_lay = QVBoxLayout(self.holders_card)
        holders_lay.setContentsMargins(16, 14, 16, 14)
        holders_lay.setSpacing(8)

        if self.displays:
            self.holders_card.setStyleSheet(
                f"QFrame {{ background-color: {THEME.error_container}; "
                f"border: 1px solid {THEME.error_border}; border-radius: {THEME.radius_tile}px; }}"
            )
            h_row = QHBoxLayout()
            h_row.setSpacing(8)
            h_ico = QLabel()
            h_ico.setFixedSize(18, 18)
            h_ico.setPixmap(icons.render_icon_pixmap("monitor", color=THEME.error, size=18))
            h_ico.setStyleSheet("background: transparent; border: none;")
            h_row.addWidget(h_ico)
            hdr = QLabel("External display connected to dGPU")
            hdr.setStyleSheet(
                f"color: {THEME.error}; font-size: {THEME.text_section}px; font-weight: 700; "
                f"background: transparent; border: none;"
            )
            h_row.addWidget(hdr, 1)
            holders_lay.addLayout(h_row)
            msg = QLabel(
                f"Connected display output(s): {', '.join(self.displays)}\n\n"
                "Disconnect displays wired to the discrete GPU before powering it off."
            )
            msg.setStyleSheet(
                f"color: {THEME.on_surface}; font-size: {THEME.text_caption}px; "
                f"background: transparent; border: none;"
            )
            msg.setWordWrap(True)
            holders_lay.addWidget(msg)
        elif self.closable:
            self.holders_card.setStyleSheet(
                f"QFrame {{ background-color: {THEME.warning_container}; "
                f"border: 1px solid {THEME.warning_border}; border-radius: {THEME.radius_tile}px; }}"
            )
            h_row = QHBoxLayout()
            h_row.setSpacing(8)
            h_ico = QLabel()
            h_ico.setFixedSize(18, 18)
            h_ico.setPixmap(icons.render_icon_pixmap("warning", color=THEME.warning, size=18))
            h_ico.setStyleSheet("background: transparent; border: none;")
            h_row.addWidget(h_ico)
            hdr = QLabel(f"Apps/services to be closed ({len(self.closable)})")
            hdr.setStyleSheet(
                f"color: {THEME.warning}; font-size: {THEME.text_section}px; font-weight: 700; "
                f"background: transparent; border: none;"
            )
            h_row.addWidget(hdr, 1)
            holders_lay.addLayout(h_row)
            warn = QLabel(
                "These apps/services currently hold the discrete GPU open and will be "
                "closed immediately before cutting power (unsaved work may be lost):"
            )
            warn.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: {THEME.text_caption}px; "
                f"background: transparent; border: none;"
            )
            warn.setWordWrap(True)
            holders_lay.addWidget(warn)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            row_count = min(4, len(self.closable))
            scroll.setMinimumHeight(min(140, row_count * 38 + 6))
            scroll.setMaximumHeight(180)
            scroll.setStyleSheet("background: transparent; border: none;")
            list_container = QWidget()
            list_container.setStyleSheet("background: transparent;")
            list_lay = QVBoxLayout(list_container)
            list_lay.setContentsMargins(0, 2, 0, 2)
            list_lay.setSpacing(6)
            for h in self.closable:
                row_pill = QFrame()
                row_pill.setStyleSheet(
                    f"QFrame {{ background-color: {THEME.surface_container}; "
                    f"border-radius: 10px; border: none; }}"
                )
                rp_lay = QHBoxLayout(row_pill)
                rp_lay.setContentsMargins(10, 6, 10, 6)
                row_lbl = QLabel(h.label)
                row_lbl.setStyleSheet(
                    f"color: {THEME.on_surface}; font-size: {THEME.text_caption}px; "
                    f"font-weight: 600; background: transparent;"
                )
                if h.cmdline:
                    row_pill.setToolTip(h.cmdline)
                rp_lay.addWidget(row_lbl)
                list_lay.addWidget(row_pill)
            list_lay.addStretch()
            scroll.setWidget(list_container)
            holders_lay.addWidget(scroll)
        else:
            self.holders_card.setStyleSheet(
                f"QFrame {{ background-color: {THEME.gated_container}; "
                f"border: 1px solid {THEME.gated_border}; border-radius: {THEME.radius_tile}px; }}"
            )
            h_row = QHBoxLayout()
            h_row.setSpacing(8)
            h_ico = QLabel()
            h_ico.setFixedSize(18, 18)
            h_ico.setPixmap(icons.render_icon_pixmap("check", color=THEME.gated, size=18))
            h_ico.setStyleSheet("background: transparent; border: none;")
            h_row.addWidget(h_ico)
            hdr = QLabel("No apps holding the discrete GPU")
            hdr.setStyleSheet(
                f"color: {THEME.gated}; font-size: {THEME.text_section}px; font-weight: 700; "
                f"background: transparent; border: none;"
            )
            h_row.addWidget(hdr, 1)
            holders_lay.addLayout(h_row)
            msg = QLabel(
                "No user applications are currently using the discrete GPU — it can "
                "power off cleanly without closing anything."
            )
            msg.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: {THEME.text_caption}px; "
                f"background: transparent; border: none;"
            )
            msg.setWordWrap(True)
            holders_lay.addWidget(msg)

        root.addWidget(self.holders_card)

        if self.audio_holders or (self.topo.audio_bdf and not self.displays):
            audio_row = QHBoxLayout()
            audio_row.setSpacing(6)
            audio_ico = QLabel()
            audio_ico.setFixedSize(14, 14)
            audio_ico.setPixmap(icons.render_icon_pixmap("volume", color=THEME.on_surface_muted, size=14))
            audio_ico.setStyleSheet("background: transparent;")
            audio_row.addWidget(audio_ico, alignment=Qt.AlignmentFlag.AlignTop)
            audio_note = QLabel(
                "Audio note: Sound may pause briefly (~0.5 s) while detaching the GPU's HDMI audio device."
            )
            audio_note.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: {THEME.text_overline}px; background: transparent;"
            )
            audio_note.setWordWrap(True)
            audio_row.addWidget(audio_note, 1)
            root.addLayout(audio_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            f"color: {THEME.warning}; font-size: {THEME.text_caption}px; font-weight: 600; background: transparent;"
        )
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setVisible(False)
        root.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.revert_btn = QPushButton("Revert to Optimus (Keep Open)")
        self.revert_btn.setProperty("class", "OutlinedButton")
        self.revert_btn.setIcon(
            icons.make_icon("undo", color=THEME.on_surface, size=15)
        )
        self.revert_btn.setIconSize(QSize(15, 15))
        self.revert_btn.setMinimumHeight(40)
        self.revert_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.revert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.revert_btn.clicked.connect(self._on_revert_clicked)
        btn_row.addWidget(self.revert_btn)

        btn_row.addStretch()

        if self.closable:
            self.confirm_btn = QPushButton("Close Apps && Power Off dGPU")
            self.confirm_btn.setProperty("class", "DangerButton")
            self.confirm_btn.setIcon(
                icons.make_icon("power_off", color=THEME.on_accent_cta, size=15)
            )
            self.confirm_btn.setIconSize(QSize(15, 15))
        else:
            self.confirm_btn = QPushButton("Power Off dGPU")
            self.confirm_btn.setProperty("class", "SuccessButton")
            self.confirm_btn.setIcon(
                icons.make_icon("power_off", color=THEME.on_accent_cta, size=15)
            )
            self.confirm_btn.setIconSize(QSize(15, 15))
        self.confirm_btn.setMinimumHeight(40)
        self.confirm_btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if self.displays:
            self.confirm_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            self.confirm_btn.setToolTip(
                "Disconnect external displays wired to the discrete GPU first"
            )
            self.confirm_btn.setEnabled(False)
        else:
            self.confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.confirm_btn.setEnabled(True)
        self.confirm_btn.clicked.connect(self._on_confirm_clicked)
        btn_row.addWidget(self.confirm_btn)

        root.addLayout(btn_row)

    def _on_revert_clicked(self) -> None:
        if self._busy:
            return
        self._user_acted = True
        self._ac_timer.stop()
        try:
            dgpu_gate.set_mode(dgpu_gate.MODE_OPTIMUS, apply_now=not dgpu_gate.is_gated_off())
        except Exception:  # noqa: BLE001
            pass
        self.reject()

    def _on_confirm_clicked(self) -> None:
        if self._busy or self.displays:
            return
        self._busy = True
        self._ac_timer.stop()
        self.revert_btn.setEnabled(False)
        self.confirm_btn.setEnabled(False)
        self.status_lbl.setStyleSheet(
            f"color: {THEME.warning}; font-size: {THEME.text_caption}px; font-weight: 600; background: transparent;"
        )
        self.status_lbl.setText(
            "Closing apps holding dGPU and cutting power rail…"
            if self.closable
            else "Cutting discrete GPU power rail…"
        )
        self.status_lbl.setVisible(True)

        target_mode = self.target_mode

        def _worker() -> None:
            try:
                res = dgpu_gate.force_close_and_gate_off(mode=target_mode)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            self._results.put(res)

        threading.Thread(target=_worker, name="gigamate-gate-confirm", daemon=True).start()
        self._poll_timer.start()

    def _collect_result(self) -> None:
        try:
            res = self._results.get_nowait()
        except queue.Empty:
            return
        self._poll_timer.stop()
        self._busy = False
        if res.get("ok"):
            self._user_acted = True
            self.accept()
            return

        err = str(res.get("error") or "Failed to power off discrete GPU.")
        self.status_lbl.setStyleSheet(
            f"color: {THEME.error}; font-size: {THEME.text_caption}px; font-weight: 600; background: transparent;"
        )
        self.status_lbl.setText(f"Could not power off dGPU: {err}")
        self.status_lbl.setVisible(True)
        self.revert_btn.setEnabled(True)
        self.confirm_btn.setEnabled(not bool(self.displays))
        self.confirm_btn.setCursor(
            Qt.CursorShape.ForbiddenCursor
            if self.displays
            else Qt.CursorShape.PointingHandCursor
        )

    def _check_ac_returned(self) -> None:
        if self._busy or self._user_acted:
            return
        try:
            if bool(get_battery_manager().is_ac_online()):
                self._auto_dismissed = True
                self._ac_timer.stop()
                dgpu_gate.sync_policy(reason="confirm_ac_returned")
                self.reject()
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy:
            event.ignore()
            return
        self._ac_timer.stop()
        if not self._user_acted and not self._auto_dismissed:
            self._user_acted = True
            try:
                dgpu_gate.set_mode(
                    dgpu_gate.MODE_OPTIMUS,
                    apply_now=not dgpu_gate.is_gated_off(),
                )
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)

    def reject(self) -> None:
        if self._busy:
            return
        self._ac_timer.stop()
        if not self._user_acted and not self._auto_dismissed:
            self._user_acted = True
            try:
                dgpu_gate.set_mode(
                    dgpu_gate.MODE_OPTIMUS,
                    apply_now=not dgpu_gate.is_gated_off(),
                )
            except Exception:  # noqa: BLE001
                pass
        super().reject()


def _lock_path() -> Path:
    rdir = os.environ.get("XDG_RUNTIME_DIR")
    if rdir and os.path.isdir(rdir):
        return Path(rdir) / "gigamate-dgpu-confirm.lock"
    return Path(f"/tmp/gigamate-dgpu-confirm-{os.getuid()}.lock")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="gigamate-dgpu-confirm")
    parser.add_argument(
        "--trigger",
        default="eco",
        choices=("eco", "auto", "auto_ac_unplug"),
    )
    parser.add_argument(
        "--target-mode",
        default=dgpu_gate.MODE_ECO,
        choices=dgpu_gate.VALID_MODES,
    )
    parser.add_argument("--prev-mode", default=None)
    args = parser.parse_args(argv)

    lock_fh = None
    try:
        lp = _lock_path()
        lp.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lp, "a+", encoding="utf-8")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if lock_fh is not None:
            lock_fh.close()
        return 0
    except OSError:
        lock_fh = None

    try:
        supported, reason = dgpu_gate.get_availability()
        if not supported:
            print(dgpu_gate.availability_message(reason), file=sys.stderr)
            return 1

        app = QApplication.instance() or QApplication(sys.argv[:1])
        apply_theme(app)
        dlg = DgpuGateConfirmDialog(
            trigger=args.trigger,
            target_mode=args.target_mode,
            prev_mode=args.prev_mode,
        )
        screen = app.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            dlg.adjustSize()
            dlg.move(
                geom.center().x() - dlg.width() // 2,
                geom.center().y() - dlg.height() // 2,
            )
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()
        return 0
    finally:
        if lock_fh is not None:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
                lock_fh.close()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
