"""GigaMate Center — Main Application Window."""

import sys
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon
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
from .pages.gpu_page import GpuPage
from .pages.rgb_page import RgbPage
from .styles import DARK_THEME
from ..paths import ICON_PATHS


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
        sidebar.setFixedWidth(220)
        sb_layout = QVBoxLayout(sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 16)
        sb_layout.setSpacing(4)

        # Title
        title_lbl = QLabel("GigaMate")
        title_lbl.setObjectName("AppTitle")
        sub_lbl = QLabel("Command Center 3.0")
        sub_lbl.setObjectName("AppSubtitle")
        sb_layout.addWidget(title_lbl)
        sb_layout.addWidget(sub_lbl)

        # Nav Buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_dashboard = self._add_nav_btn("⚡  Dashboard", 0, sb_layout)
        self.btn_battery = self._add_nav_btn("🔋  Battery Care", 1, sb_layout)
        self.btn_gpu = self._add_nav_btn("🛡️  dGPU Sleep Guard", 2, sb_layout)
        self.btn_rgb = self._add_nav_btn("🎨  RGB Lighting", 3, sb_layout)

        sb_layout.addStretch()

        # Version tag
        ver_lbl = QLabel("v3.0.0-beta")
        ver_lbl.setStyleSheet("color: #4a5568; font-size: 11px; padding: 0 16px;")
        sb_layout.addWidget(ver_lbl)

        root_layout.addWidget(sidebar)

        # ── Stacked Pages ──
        self.stack = QStackedWidget()
        self.page_dashboard = DashboardPage()
        self.page_battery = BatteryPage()
        self.page_gpu = GpuPage()
        self.page_rgb = RgbPage()

        self.stack.addWidget(self.page_dashboard)
        self.stack.addWidget(self.page_battery)
        self.stack.addWidget(self.page_gpu)
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


def run_gui() -> None:
    """Entry point for GigaMate Center GUI."""
    app = QApplication.instance()
    is_external_app = app is not None
    if app is None:
        app = QApplication(sys.argv)

    app.setStyleSheet(DARK_THEME)
    window = MainWindow()
    window.show()

    if not is_external_app:
        sys.exit(app.exec())
