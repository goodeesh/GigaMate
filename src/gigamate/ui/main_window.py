import fcntl
import logging
import os
import socket as py_socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .pages.automation_page import AutomationPage
from .pages.battery_page import BatteryPage
from .pages.dashboard_page import DashboardPage
from .pages.gpu_page import GpuPage
from .pages.rgb_page import RgbPage
from .pages.settings_page import SettingsPage
from .styles import apply_theme
from .theme import (
    PROFILE_ACCENTS,
    THEME,
    detect_system_color_scheme,
    get_active_scheme,
    resolve_color_scheme,
)
from .widgets import highlight_widget
from ..capabilities import detect_system_capabilities, invalidate_capabilities
from ..config import CONFIG_FILE, load as load_config
from ..paths import ICON_PATHS, get_runtime_ipc_paths

logger = logging.getLogger(__name__)


IPC_SOCKET_PATH, IPC_LOCK_PATH = get_runtime_ipc_paths()
IPC_SOCKET_NAME = IPC_SOCKET_PATH  # Backwards compatibility


class SingleInstanceServer(QObject):
    """Listens for activation requests from secondary instances."""

    def __init__(self, window: "MainWindow", sock_path: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.window = window
        self.sock_path = sock_path or IPC_SOCKET_PATH
        self.server = QLocalServer(self)
        try:
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        self.server.newConnection.connect(self._handle_connection)
        self.server.listen(self.sock_path)
        # Restrict the socket to this user (private runtime dir + 0600).
        try:
            os.chmod(self.sock_path, 0o600)
        except OSError:
            pass

    def _peer_uid(self, client: QLocalSocket) -> Optional[int]:
        """Return the peer's uid via SO_PEERCRED, or None if unavailable."""
        try:
            fd = int(client.socketDescriptor())
            if fd < 0:
                return None
            dupfd = os.dup(fd)
        except (OSError, TypeError, ValueError):
            return None
        try:
            sock = py_socket.socket(fileno=dupfd)
        except OSError:
            try:
                os.close(dupfd)
            except OSError:
                pass
            return None
        try:
            creds = sock.getsockopt(
                py_socket.SOL_SOCKET, py_socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            _pid, uid, _gid = struct.unpack("3i", creds)
            return uid
        except OSError:
            return None
        finally:
            sock.close()

    def _handle_connection(self) -> None:
        client = self.server.nextPendingConnection()
        if client is None:
            return
        uid = self._peer_uid(client)
        if uid is not None and uid != os.getuid():
            logger.warning("Rejecting IPC connection from uid %s", uid)
            client.disconnectFromServer()
            client.deleteLater()
            return
        client.disconnected.connect(client.deleteLater)
        client.readyRead.connect(lambda: self._on_ready_read(client))
        if client.bytesAvailable() > 0:
            self._on_ready_read(client)

    def _on_ready_read(self, client: QLocalSocket) -> None:
        try:
            msg = bytes(client.readAll()).decode("utf-8", errors="ignore").strip()
            if "QUIT" in msg:
                QApplication.quit()
            elif "SETUP" in msg:
                self.activate_window()
                self.window.show_onboarding()
            elif "ACTIVATE" in msg:
                self.activate_window()
        finally:
            client.disconnectFromServer()

    def activate_window(self) -> None:
        """Unminimize, raise, and bring the existing window to the front."""
        self.window.sync_all_from_config()
        win = self.window
        if win.isMinimized():
            win.showNormal()
        win.setWindowState((win.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()


class MainWindow(QMainWindow):
    """Primary control center window for GigaMate."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GigaMate Center")
        self.setMinimumSize(920, 640)

        # Set Window Icon if available
        icon_path = ICON_PATHS.get("gigamate")
        if icon_path and Path(icon_path).exists():
            self.setWindowIcon(QIcon(icon_path))

        self._init_ui()

        # Enforce saved user settings to hardware on app open
        try:
            from ..hardware import apply_hardware_settings
            apply_hardware_settings()
        except Exception as exc:
            logger.debug(f"Could not apply saved hardware settings on launch: {exc}")

        self._ui_theme_pref = str(load_config().get("ui_theme", "system")).lower()
        self._rebuilding_theme = False
        self._last_config_mtime = self._get_config_mtime()
        self.config_timer = QTimer(self)
        self.config_timer.timeout.connect(self._check_config_mtime)
        self.config_timer.start(1000)

        app = QApplication.instance()
        if app is not None:
            hints = app.styleHints()
            sig = getattr(hints, "colorSchemeChanged", None)
            if sig is not None:
                try:
                    sig.connect(self._on_system_color_scheme_changed)
                except Exception:
                    pass

        # First-run setup, deferred until the window/event loop is up.
        QTimer.singleShot(0, self._maybe_show_onboarding)

    def _maybe_show_onboarding(self) -> None:
        """Show the first-run wizard once (new installs and 2.0.x upgrades)."""
        force = bool(os.environ.get("GIGAMATE_FORCE_SETUP"))
        if not force:
            # Never block headless/test runs.
            if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
                return
            if os.environ.get("GIGAMATE_SKIP_ONBOARDING"):
                return
            try:
                if load_config().get("onboarding_complete"):
                    return
            except Exception:
                return
        self.show_onboarding()

    def show_onboarding(self) -> None:
        """Open the setup wizard (first run, or invoked from tray/Settings)."""
        if getattr(self, "_setup_open", False):
            return  # never stack two wizards
        self._setup_open = True
        try:
            from .onboarding import OnboardingWizard
            OnboardingWizard(self).exec()
            self.sync_all_from_config(invalidate=True)
        except Exception as exc:
            logger.warning(f"Onboarding failed to run: {exc}")
        finally:
            self._setup_open = False

    def _init_ui(self) -> None:
        central_widget = QWidget()
        central_widget.setObjectName("CentralWidget")
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar Navigation (Aero Instrument 100px Vertical Icon Rail) ──
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(100)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(8, 12, 8, 12)
        sb_layout.setSpacing(5)

        # ── Brand Icon (clickable -> Overview) ──
        brand_widget = QWidget()
        brand_widget.setObjectName("BrandHeader")
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(0, 2, 0, 10)
        brand_layout.setSpacing(0)
        brand_layout.addStretch()

        brand_btn = QPushButton()
        brand_btn.setFixedSize(42, 42)
        brand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        brand_btn.setToolTip("GigaMate Center — Overview")
        brand_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 10px; padding: 0px; }"
            f"QPushButton:hover {{ background-color: {THEME.surface_container_high}; }}"
        )
        icon_path = ICON_PATHS.get("gigamate")
        if icon_path and Path(icon_path).exists():
            brand_btn.setIcon(QIcon(icon_path))
            brand_btn.setIconSize(QSize(34, 34))
        brand_btn.clicked.connect(lambda: self.navigate_to("overview"))
        brand_layout.addWidget(brand_btn)
        brand_layout.addStretch()

        sb_layout.addWidget(brand_widget)

        # Nav Buttons (visual order: Overview, GPU, Battery, Keyboard, Automation, Settings)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_items: list[tuple[QPushButton, QLabel, QLabel, str]] = []
        self._nav_by_idx: dict[int, QPushButton] = {}
        self._current_accent = THEME.primary

        self.btn_dashboard = self._add_nav_btn("Overview", "dashboard", 0, sb_layout)
        self.btn_gpu = self._add_nav_btn("GPU", "tune", 3, sb_layout)
        self.btn_battery = self._add_nav_btn("Battery", "battery", 1, sb_layout)
        self.btn_rgb = self._add_nav_btn("Keyboard", "keyboard", 2, sb_layout)
        self.btn_automation = self._add_nav_btn("Automation", "bolt", 4, sb_layout)
        self.btn_settings = self._add_nav_btn("Settings", "settings", 5, sb_layout)

        sb_layout.addStretch()

        # ── Compact Rail Footer (Daemon LED + ON/OFF) ──
        try:
            sys_caps = detect_system_capabilities()
            if sys_caps.is_gigabyte_laptop:
                sys_name = sys_caps.product_name
            else:
                sys_name = f"{sys_caps.product_name} · Generic Device"
        except Exception as exc:
            logger.debug(f"Capability probe for sidebar footer failed: {exc}")
            sys_name = "Standard PC"
        self._sys_name = sys_name

        footer_widget = QWidget()
        footer_widget.setObjectName("SidebarFooter")
        footer_outer = QVBoxLayout(footer_widget)
        footer_outer.setContentsMargins(0, 6, 0, 0)
        footer_outer.setSpacing(0)

        footer_pill = QFrame()
        footer_pill.setStyleSheet(
            f"background-color: {THEME.surface_container}; "
            f"border-radius: 10px; border: 1px solid {THEME.outline_variant};"
        )
        footer_pill.setToolTip(f"gigamate.service\n{sys_name}")
        footer_layout = QHBoxLayout(footer_pill)
        footer_layout.setContentsMargins(8, 6, 8, 6)
        footer_layout.setSpacing(6)
        footer_layout.addStretch()

        self._daemon_dot = QLabel()
        self._daemon_dot.setFixedSize(8, 8)
        self._daemon_dot.setStyleSheet("background: transparent;")
        self._daemon_dot.setPixmap(
            icons.render_icon_pixmap("dot", color=THEME.on_surface_muted, size=8)
        )
        self._daemon_lbl = QLabel("…")
        self._daemon_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; "
            f"font-weight: 700; font-family: {THEME.mono_family}; background: transparent;"
        )
        footer_layout.addWidget(self._daemon_dot)
        footer_layout.addWidget(self._daemon_lbl)
        footer_layout.addStretch()
        self._footer_pill = footer_pill
        # Probe the service after the window is up (avoids blocking startup).
        QTimer.singleShot(0, self._refresh_daemon_status)

        footer_outer.addWidget(footer_pill)
        sb_layout.addWidget(footer_widget)

        root_layout.addWidget(sidebar)

        # ── Right Column: TopInstrumentBar + Main Content Stack ──
        content_col = QWidget()
        content_col_layout = QVBoxLayout(content_col)
        content_col_layout.setContentsMargins(0, 0, 0, 0)
        content_col_layout.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("TopInstrumentBar")
        top_bar.setFixedHeight(38)
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(20, 0, 20, 0)
        tb_layout.setSpacing(10)

        tb_title = QLabel("GigaMate")
        tb_title.setObjectName("AppTitle")
        tb_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        tb_layout.addWidget(tb_title)

        hw_pill = QLabel(sys_name)
        hw_pill.setStyleSheet(
            f"color: {THEME.on_surface_variant}; background-color: {THEME.surface_container_high}; "
            f"border: 1px solid {THEME.outline_variant}; border-radius: 6px; "
            f"padding: 2px 8px; font-size: 11px; font-family: {THEME.mono_family};"
        )
        tb_layout.addWidget(hw_pill)
        tb_layout.addStretch()

        from .. import __version__
        ver_pill = QLabel(f"Center v{__version__}")
        ver_pill.setObjectName("AppSubtitle")
        ver_pill.setStyleSheet(
            f"color: {THEME.on_surface_muted}; background-color: {THEME.surface_container_high}; "
            f"border: 1px solid {THEME.outline_variant}; border-radius: 6px; "
            f"padding: 2px 8px; font-size: 11px; font-family: {THEME.mono_family};"
        )
        tb_layout.addWidget(ver_pill)

        content_col_layout.addWidget(top_bar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")

        self.page_dashboard = DashboardPage()
        self.page_battery = BatteryPage()
        self.page_rgb = RgbPage()
        self.page_gpu = GpuPage()
        self.page_automation = AutomationPage()
        self.page_settings = SettingsPage()

        # Each page lives in its own scroll area. Without this, shrinking the
        # window (or moving it to a smaller/scaled monitor) forces Qt to squeeze
        # a page's layout below its minimum, which collapses grids and overlaps
        # labels — the GPU page alone needs ~913 px. Scrollable pages keep their
        # natural size and simply scroll instead.
        self._pages_by_idx: dict[int, QWidget] = {}
        self._scroll_by_idx: dict[int, QScrollArea] = {}
        self._scroll_anim: Optional[QPropertyAnimation] = None
        self._last_highlighted_widget: Optional[QWidget] = None

        for idx, page in enumerate((
            self.page_dashboard,
            self.page_battery,
            self.page_rgb,
            self.page_gpu,
            self.page_automation,
            self.page_settings,
        )):
            area = self._wrap_scrollable(page)
            self.stack.addWidget(area)
            self._pages_by_idx[idx] = page
            self._scroll_by_idx[idx] = area
            if hasattr(page, "navigate_requested"):
                page.navigate_requested.connect(self.navigate_to)

        if hasattr(self.page_dashboard, "profile_accent_changed"):
            self.page_dashboard.profile_accent_changed.connect(self._on_profile_accent_changed)
        if hasattr(self.page_settings, "theme_changed"):
            self.page_settings.theme_changed.connect(self._on_user_theme_changed)

        content_col_layout.addWidget(self.stack, stretch=1)
        root_layout.addWidget(content_col, stretch=1)

        # Default to dashboard
        self.btn_dashboard.setChecked(True)
        self._update_nav_icons(self._current_accent)

    @staticmethod
    def _wrap_scrollable(page: QWidget, max_width: int = 1140) -> QScrollArea:
        """Put ``page`` in a centered, max-width bounded scrollable viewport."""
        page.setMaximumWidth(max_width)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        container = QWidget()
        container.setObjectName("PageScrollContainer")
        container.setStyleSheet("QWidget#PageScrollContainer { background: transparent; }")
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)
        h_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        h_layout.addWidget(page, stretch=1)
        h_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )

        area = QScrollArea()
        area.setObjectName("PageScrollArea")
        area.setWidget(container)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return area

    def _add_nav_btn(
        self, text: str, icon_name: str, page_idx: int, layout: QVBoxLayout
    ) -> QPushButton:
        btn = QPushButton("")
        btn.setProperty("class", "NavButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(58)

        inner = QVBoxLayout(btn)
        inner.setContentsMargins(4, 7, 4, 7)
        inner.setSpacing(5)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        icon_lbl.setStyleSheet("background: transparent;")
        icon_lbl.setPixmap(
            icons.render_icon_pixmap(icon_name, color=THEME.on_surface_variant, size=19)
        )

        text_lbl = QLabel(text)
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; font-weight: 600; background: transparent;"
        )

        inner.addWidget(icon_lbl)
        inner.addWidget(text_lbl)

        btn.clicked.connect(lambda: self._on_nav_clicked(page_idx))
        self.nav_group.addButton(btn)
        self._nav_items.append((btn, icon_lbl, text_lbl, icon_name))
        self._nav_by_idx[page_idx] = btn
        layout.addWidget(btn)
        return btn

    def _on_nav_clicked(self, page_idx: int) -> None:
        self.stack.setCurrentIndex(page_idx)
        self._update_nav_icons(getattr(self, "_current_accent", THEME.primary))

    def _update_nav_icons(self, accent_hex: str) -> None:
        self._current_accent = accent_hex
        for btn, icon_lbl, text_lbl, icon_name in getattr(self, "_nav_items", []):
            checked = btn.isChecked()
            color = accent_hex if checked else THEME.on_surface_variant
            icon_lbl.setPixmap(icons.render_icon_pixmap(icon_name, color=color, size=19))
            text_lbl.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: {'700' if checked else '600'}; background: transparent;"
            )

    def navigate_to(self, page_name: str) -> None:
        """Switch to a named page (optionally ``page:section``) and sync the rail."""
        spec = page_name.strip().lower()
        if ":" in spec:
            page_key, section_key = spec.split(":", 1)
            page_key = page_key.strip()
            section_key = section_key.strip() or None
        else:
            page_key, section_key = spec, None

        mapping = {
            "overview": 0,
            "dashboard": 0,
            "battery": 1,
            "keyboard": 2,
            "rgb": 2,
            "gpu": 3,
            "automation": 4,
            "settings": 5,
        }
        idx = mapping.get(page_key)
        if idx is None:
            return
        self.stack.setCurrentIndex(idx)
        btn = self._nav_by_idx.get(idx)
        if btn is not None:
            btn.setChecked(True)
        self._update_nav_icons(getattr(self, "_current_accent", THEME.primary))
        if section_key:
            QTimer.singleShot(0, lambda i=idx, s=section_key: self._focus_section(i, s))

    def _focus_section(self, page_idx: int, section_key: str) -> None:
        """Scroll smoothly to a deep-linked section and emit a brief accent glow."""
        page = self._pages_by_idx.get(page_idx)
        area = self._scroll_by_idx.get(page_idx)
        if page is None or area is None or not hasattr(page, "resolve_section"):
            return
        target = page.resolve_section(section_key)
        if target is None or not target.isVisible():
            return

        content = area.widget()
        sb = area.verticalScrollBar()
        if content is not None and sb is not None:
            target_y = target.mapTo(content, QPoint(0, 0)).y()
            vp_h = area.viewport().height()
            desired = max(0, min(sb.maximum(), target_y - max(0, (vp_h - target.height()) // 2)))
            if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
                sb.setValue(desired)
            else:
                if self._scroll_anim is not None:
                    self._scroll_anim.stop()
                anim = QPropertyAnimation(sb, b"value", self)
                anim.setDuration(260)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.setStartValue(sb.value())
                anim.setEndValue(desired)
                anim.start()
                self._scroll_anim = anim

        prev_target = self._last_highlighted_widget
        if prev_target is not None and prev_target is not target:
            prev_overlay = getattr(prev_target, "_glow_overlay", None)
            if prev_overlay is not None:
                prev_overlay.stop_and_remove()

        self._last_highlighted_widget = target
        highlight_widget(target, getattr(self, "_current_accent", THEME.primary))

    def _on_user_theme_changed(self, pref: str) -> None:
        """Apply a user-selected theme preference ('system' | 'dark' | 'light')."""
        pref = pref if pref in ("system", "dark", "light") else "system"
        self._ui_theme_pref = pref
        app = QApplication.instance()
        if pref == "system" and app is not None:
            # Clear any Qt override before reading the live desktop color scheme.
            unset_cs = getattr(app.styleHints(), "unsetColorScheme", None)
            if unset_cs is not None:
                self._rebuilding_theme = True
                try:
                    unset_cs()
                finally:
                    self._rebuilding_theme = False
        target = resolve_color_scheme(pref, app)
        self._apply_color_scheme(target, follow_system=(pref == "system"))

    def _on_system_color_scheme_changed(self, _qt_scheme=None) -> None:
        """Live-switch when the desktop OS toggles Dark / Light mode."""
        if getattr(self, "_rebuilding_theme", False):
            return
        if getattr(self, "_ui_theme_pref", "system") != "system":
            return
        target = detect_system_color_scheme(QApplication.instance())
        self._apply_color_scheme(target, follow_system=True)

    def _apply_color_scheme(self, scheme: str, *, follow_system: bool = False) -> None:
        """Switch active Dark/Light tokens, re-apply QSS/palette, and re-mount pages."""
        app = QApplication.instance()
        accent = getattr(self, "_current_accent", THEME.primary)
        if scheme == get_active_scheme():
            if app is not None:
                self._rebuilding_theme = True
                try:
                    apply_theme(app, accent, scheme=scheme, follow_system=follow_system)
                finally:
                    self._rebuilding_theme = False
            return

        curr_idx = self.stack.currentIndex() if hasattr(self, "stack") else 0
        self._rebuilding_theme = True
        try:
            for page in getattr(self, "_pages_by_idx", {}).values():
                for timer_attr in ("timer", "poll_timer", "_poll_timer"):
                    t = getattr(page, timer_attr, None)
                    if t is not None and hasattr(t, "stop"):
                        try:
                            t.stop()
                        except Exception:
                            pass
            if app is not None:
                apply_theme(app, accent, scheme=scheme, follow_system=follow_system)
            self._init_ui()
            if curr_idx in self._pages_by_idx:
                self.stack.setCurrentIndex(curr_idx)
                btn = self._nav_by_idx.get(curr_idx)
                if btn is not None:
                    btn.setChecked(True)
            self._update_nav_icons(getattr(self, "_current_accent", THEME.primary))
        finally:
            self._rebuilding_theme = False

    def _on_profile_accent_changed(self, profile_id: int) -> None:
        """Apply dynamic accent color for the active power profile."""
        accent_hex = PROFILE_ACCENTS.get(profile_id, THEME.primary)
        self._current_accent = accent_hex
        app = QApplication.instance()
        if app is not None:
            follow = getattr(self, "_ui_theme_pref", "system") == "system"
            apply_theme(app, accent_hex, follow_system=follow)
        self._update_nav_icons(accent_hex)
        for page in (
            self.page_dashboard,
            self.page_battery,
            self.page_rgb,
            self.page_gpu,
            self.page_automation,
            self.page_settings,
        ):
            if hasattr(page, "set_accent"):
                page.set_accent(accent_hex)

    def _get_config_mtime(self) -> float:
        try:
            return CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0.0

    def _check_config_mtime(self) -> None:
        mtime = self._get_config_mtime()
        if mtime > getattr(self, "_last_config_mtime", 0.0):
            self.sync_all_from_config(invalidate=True)

    def _service_is_active(self) -> bool:
        """Best-effort check of the tray daemon's systemd user service."""
        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "gigamate.service"],
                capture_output=True,
                text=True,
                check=False,
                timeout=1,
            )
            return res.stdout.strip() == "active"
        except Exception:
            return False

    def _refresh_daemon_status(self) -> None:
        """Update the sidebar daemon indicator to match reality (30 s cache)."""
        if not hasattr(self, "_daemon_lbl"):
            return
        now = time.monotonic()
        cache = getattr(self, "_daemon_status_cache", None)
        if cache is not None and (now - cache[0]) < 30.0:
            active = cache[1]
        else:
            active = self._service_is_active()
            self._daemon_status_cache = (now, active)
        status_color = THEME.success if active else THEME.error
        self._daemon_dot.setPixmap(
            icons.render_icon_pixmap("dot", color=status_color, size=8)
        )
        self._daemon_lbl.setStyleSheet(
            f"color: {status_color}; font-size: 10px; "
            f"font-weight: 700; font-family: {THEME.mono_family}; background: transparent;"
        )
        self._daemon_lbl.setText("ON" if active else "OFF")
        if hasattr(self, "_footer_pill"):
            state_str = "active" if active else "inactive"
            sys_name = getattr(self, "_sys_name", "GigaMate")
            self._footer_pill.setToolTip(f"gigamate.service ({state_str})\n{sys_name}")

    def sync_all_from_config(self, invalidate: bool = False) -> None:
        """Reload and update all pages from on-disk configuration.

        Hardware capability re-probing is only forced when the config actually
        changed (``invalidate=True``); window activation must not trigger a
        USB/ACPI scan on every focus.
        """
        self._last_config_mtime = self._get_config_mtime()
        if invalidate:
            invalidate_capabilities()
        if hasattr(self, "_daemon_lbl"):
            QTimer.singleShot(0, self._refresh_daemon_status)
        if hasattr(self, "page_rgb"):
            self.page_rgb.reload_from_config()
        if hasattr(self, "page_gpu"):
            self.page_gpu.reload_from_config()
        if hasattr(self, "page_dashboard"):
            self.page_dashboard.reload_from_config()
        if hasattr(self, "page_battery"):
            self.page_battery.reload_from_config()
        if hasattr(self, "page_automation"):
            self.page_automation.reload_from_config()
        if hasattr(self, "page_settings"):
            self.page_settings.reload_from_config()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.sync_all_from_config()
        super().changeEvent(event)


def ensure_tray_running() -> None:
    """Ensure that the GigaMate system tray daemon is running.

    Checks user systemd service first, starts it if inactive.
    Falls back to spawning the tray daemon process if systemd is not active or unavailable.
    """
    # 1. Check user systemd service
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "gigamate.service"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if res.stdout.strip() == "active":
            return
        subprocess.run(
            ["systemctl", "--user", "start", "gigamate.service"],
            capture_output=True,
            timeout=2.0,
        )
        res_after = subprocess.run(
            ["systemctl", "--user", "is-active", "gigamate.service"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if res_after.stdout.strip() == "active":
            return
    except Exception as exc:
        logger.debug(f"systemd gigamate.service status check failed: {exc}")

    # 2. Fallback: check if tray process is running via pgrep
    try:
        check = subprocess.run(
            ["pgrep", "-f", "gigamate[. -]tray"],
            capture_output=True,
            timeout=2,
        )
        if check.returncode == 0:
            return

        import shutil
        tray_bin = shutil.which("gigamate-tray")
        cmd = [tray_bin] if tray_bin else [sys.executable, "-m", "gigamate.tray"]
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.debug(f"Could not spawn tray daemon: {exc}")


def run_gui() -> None:
    """Entry point for GigaMate Center GUI."""
    if os.geteuid() == 0:
        print(
            "GigaMate Center must be run as your desktop user, not root.\n"
            "Running it with sudo would create root-owned settings and cannot "
            "reach your user session. Re-run as your normal user.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check exclusive instance lock
    try:
        lock_file = open(IPC_LOCK_PATH, "a+")
    except OSError as exc:
        print(f"Could not open runtime lock {IPC_LOCK_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        is_primary = True
    except (BlockingIOError, OSError):
        is_primary = False

    if not is_primary:
        # Another instance is already running! Connect via socket and tell it to activate.
        cmd_msg = b"SETUP\n" if os.environ.get("GIGAMATE_FORCE_SETUP") else b"ACTIVATE\n"
        start_time = time.monotonic()
        while (time.monotonic() - start_time) < 1.5:
            try:
                with py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(IPC_SOCKET_PATH)
                    s.sendall(cmd_msg)
                    break
            except (FileNotFoundError, ConnectionRefusedError, BlockingIOError):
                time.sleep(0.1)
            except Exception as exc:
                logger.debug(f"Could not signal existing instance: {exc}")
                break
        sys.exit(0)

    # Ensure system tray background daemon runs together with Center
    ensure_tray_running()

    app = QApplication.instance()
    is_external_app = app is not None
    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName("gigamate-center")
    app.setApplicationDisplayName("GigaMate Center")
    app.setDesktopFileName("gigamate-center.desktop")

    icon_path = ICON_PATHS.get("gigamate")
    if icon_path and Path(icon_path).exists():
        app.setWindowIcon(QIcon(icon_path))

    theme_pref = str(load_config().get("ui_theme", "system")).lower()
    resolved_scheme = resolve_color_scheme(theme_pref, app)
    apply_theme(app, scheme=resolved_scheme, follow_system=(theme_pref == "system"))
    window = MainWindow()
    server = SingleInstanceServer(window, IPC_SOCKET_PATH, app)
    window._ipc_server = server  # Prevent GC
    window._instance_lock_file = lock_file  # Keep flock open for process lifetime
    window.show()

    if not is_external_app:
        sys.exit(app.exec())
