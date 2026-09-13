import fcntl
import os
import socket as py_socket
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple
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

from .pages.dashboard_page import DashboardPage
from .pages.battery_page import BatteryPage
from .pages.rgb_page import RgbPage
from .styles import DARK_THEME
from ..config import CONFIG_FILE
from ..paths import ICON_PATHS


def get_runtime_ipc_paths() -> Tuple[str, str]:
    """Return persistent, fixed (sock_path, lock_path) in user runtime directory."""
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if not runtime_dir.exists():
        runtime_dir = Path("/tmp")
    sock_path = str(runtime_dir / f"gigamate-center-{os.getuid()}.sock")
    lock_path = str(runtime_dir / f"gigamate-center-{os.getuid()}.lock")
    return sock_path, lock_path


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

    def _handle_connection(self) -> None:
        client = self.server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda: self._on_ready_read(client))

    def _on_ready_read(self, client: QLocalSocket) -> None:
        try:
            msg = bytes(client.readAll()).decode("utf-8", errors="ignore").strip()
            if "QUIT" in msg:
                QApplication.quit()
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
        self._last_config_mtime = self._get_config_mtime()
        self.config_timer = QTimer(self)
        self.config_timer.timeout.connect(self._check_config_mtime)
        self.config_timer.start(1000)

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

        sb_layout.addStretch()

        # ── Sidebar Footer (Hardware Info & Daemon Status) ──
        footer_widget = QWidget()
        footer_widget.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(footer_widget)
        footer_layout.setContentsMargins(14, 10, 14, 12)
        footer_layout.setSpacing(2)

        daemon_row = QHBoxLayout()
        daemon_row.setSpacing(6)
        daemon_dot = QLabel("●")
        daemon_dot.setStyleSheet("color: #48bb78; font-size: 11px;")
        daemon_lbl = QLabel("Daemon Active")
        daemon_lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 600;")
        daemon_row.addWidget(daemon_dot)
        daemon_row.addWidget(daemon_lbl)
        daemon_row.addStretch()
        footer_layout.addLayout(daemon_row)

        dmi_name = "Gigabyte Laptop"
        dmi_path = Path("/sys/class/dmi/id/product_name")
        if dmi_path.exists():
            try:
                raw_name = dmi_path.read_text().strip()
                if raw_name:
                    dmi_name = raw_name
            except Exception:
                pass

        sys_lbl = QLabel(dmi_name)
        sys_lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 500;")
        footer_layout.addWidget(sys_lbl)

        sb_layout.addWidget(footer_widget)

        root_layout.addWidget(sidebar)

        # ── Main Content Stack ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")

        self.page_dashboard = DashboardPage()
        self.page_battery = BatteryPage()
        self.page_rgb = RgbPage()

        self.stack.addWidget(self.page_dashboard)
        self.stack.addWidget(self.page_battery)
        self.stack.addWidget(self.page_rgb)

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
            self.sync_all_from_config()

    def sync_all_from_config(self) -> None:
        """Reload and update all pages from on-disk configuration."""
        self._last_config_mtime = self._get_config_mtime()
        if hasattr(self, "page_rgb"):
            self.page_rgb.reload_from_config()
        if hasattr(self, "page_dashboard"):
            self.page_dashboard._refresh_telemetry()
        if hasattr(self, "page_battery"):
            self.page_battery._refresh_battery_data()

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
    except Exception:
        pass

    # 2. Fallback: check if tray process is running via pgrep
    try:
        check = subprocess.run(["pgrep", "-f", "gigamate[- ]tray"], capture_output=True)
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
    except Exception:
        pass


def run_gui() -> None:
    """Entry point for GigaMate Center GUI."""
    # Ensure system tray background daemon runs together with Center
    ensure_tray_running()

    # Check exclusive instance lock
    lock_file = open(IPC_LOCK_PATH, "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        is_primary = True
    except (BlockingIOError, OSError):
        is_primary = False

    if not is_primary:
        # Another instance is already running! Connect via socket and tell it to activate.
        try:
            with py_socket.socket(py_socket.AF_UNIX, py_socket.SOCK_STREAM) as s:
                s.settimeout(1.5)
                s.connect(IPC_SOCKET_PATH)
                s.sendall(b"ACTIVATE\n")
        except Exception:
            pass
        sys.exit(0)

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
