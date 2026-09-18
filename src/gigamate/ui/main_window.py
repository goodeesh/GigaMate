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
from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .pages.battery_page import BatteryPage
from .pages.dashboard_page import DashboardPage
from .pages.gpu_page import GpuPage
from .pages.rgb_page import RgbPage
from .pages.settings_page import SettingsPage
from .styles import DARK_THEME
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

        self._last_config_mtime = self._get_config_mtime()
        self.config_timer = QTimer(self)
        self.config_timer.timeout.connect(self._check_config_mtime)
        self.config_timer.start(1000)

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

        # ── Sidebar Navigation ──
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 16)
        sb_layout.setSpacing(4)

        # ── Brand Header with App Icon ──
        brand_widget = QWidget()
        brand_widget.setObjectName("BrandHeader")
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(16, 18, 16, 12)
        brand_layout.setSpacing(12)

        icon_path = ICON_PATHS.get("gigamate")
        if icon_path and Path(icon_path).exists():
            icon_lbl = QLabel()
            pixmap = QIcon(icon_path).pixmap(QSize(32, 32))
            icon_lbl.setPixmap(pixmap)
            icon_lbl.setFixedSize(32, 32)
            brand_layout.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(1)

        title_lbl = QLabel("GigaMate")
        title_lbl.setObjectName("AppTitle")
        sub_lbl = QLabel("Command Center 3.0")
        sub_lbl.setObjectName("AppSubtitle")

        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        brand_layout.addLayout(title_box)
        brand_layout.addStretch()

        sb_layout.addWidget(brand_widget)

        # Nav Buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_dashboard = self._add_nav_btn("⚡  Dashboard", 0, sb_layout)
        self.btn_battery = self._add_nav_btn("🔋  Battery Care", 1, sb_layout)
        self.btn_rgb = self._add_nav_btn("🎨  RGB Lighting", 2, sb_layout)
        self.btn_gpu = self._add_nav_btn("⚡  GPU", 3, sb_layout)
        self.btn_settings = self._add_nav_btn("⚙️  Settings", 4, sb_layout)

        sb_layout.addStretch()

        # ── Sidebar Footer (Hardware Info & Daemon Status) ──
        footer_widget = QWidget()
        footer_widget.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(footer_widget)
        footer_layout.setContentsMargins(14, 10, 14, 12)
        footer_layout.setSpacing(2)

        daemon_row = QHBoxLayout()
        daemon_row.setSpacing(6)
        self._daemon_dot = QLabel("●")
        self._daemon_dot.setStyleSheet("color: #8896ab; font-size: 11px;")
        self._daemon_lbl = QLabel("Daemon: checking…")
        self._daemon_lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
        daemon_row.addWidget(self._daemon_dot)
        daemon_row.addWidget(self._daemon_lbl)
        daemon_row.addStretch()
        footer_layout.addLayout(daemon_row)
        # Probe the service after the window is up (avoids blocking startup).
        QTimer.singleShot(0, self._refresh_daemon_status)

        try:
            sys_caps = detect_system_capabilities()
            if sys_caps.is_gigabyte_laptop:
                sys_name = sys_caps.product_name
            else:
                sys_name = f"{sys_caps.product_name} · Generic Device"
        except Exception as exc:
            logger.debug(f"Capability probe for sidebar footer failed: {exc}")
            sys_name = "Standard PC"

        sys_lbl = QLabel(sys_name)
        color = "#64748b" if "Generic Device" in sys_name else "#8896ab"
        sys_lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 500;")
        sys_lbl.setWordWrap(True)
        footer_layout.addWidget(sys_lbl)

        sb_layout.addWidget(footer_widget)

        root_layout.addWidget(sidebar)

        # ── Main Content Stack ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")

        self.page_dashboard = DashboardPage()
        self.page_battery = BatteryPage()
        self.page_rgb = RgbPage()
        self.page_gpu = GpuPage()
        self.page_settings = SettingsPage()

        self.stack.addWidget(self.page_dashboard)
        self.stack.addWidget(self.page_battery)
        self.stack.addWidget(self.page_rgb)
        self.stack.addWidget(self.page_gpu)
        self.stack.addWidget(self.page_settings)

        root_layout.addWidget(self.stack)

        # Default to dashboard
        self.btn_dashboard.setChecked(True)

    def _add_nav_btn(self, text: str, page_idx: int, layout: QVBoxLayout) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("class", "NavButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.stack.setCurrentIndex(page_idx))
        self.nav_group.addButton(btn)
        layout.addWidget(btn)
        return btn

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
        self._daemon_dot.setStyleSheet(
            f"color: {'#48bb78' if active else '#e53e3e'}; font-size: 11px;")
        self._daemon_lbl.setText("Daemon Active" if active else "Daemon Inactive")

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

    app.setStyleSheet(DARK_THEME)
    window = MainWindow()
    server = SingleInstanceServer(window, IPC_SOCKET_PATH, app)
    window._ipc_server = server  # Prevent GC
    window._instance_lock_file = lock_file  # Keep flock open for process lifetime
    window.show()

    if not is_external_app:
        sys.exit(app.exec())
