"""Material Design 3 (Warm Obsidian) slider, progress bar, and help button controls."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QComboBox, QProgressBar, QPushButton, QSlider, QToolTip, QWidget

from gigamate.ui import icons
from gigamate.ui.theme import THEME


class M3Slider(QSlider):
    """Material Design 3 slider preserving QSlider API and optional wheel-ignore."""

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        ignore_wheel: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(orientation, parent)
        self._ignore_wheel = bool(ignore_wheel)
        self.setMinimumHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self._ignore_wheel:
            event.ignore()
            return
        super().wheelEvent(event)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self.isEnabled()
                else Qt.CursorShape.ForbiddenCursor
            )


class M3ProgressBar(QProgressBar):
    """Material Design 3 pill progress bar with dynamic fill color support."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "M3ProgressBar")
        self.setFixedHeight(22)
        self.setTextVisible(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_fill_color(THEME.accent_cta)

    def set_fill_color(self, hex_color: str) -> None:
        self.setStyleSheet(
            f"QProgressBar.M3ProgressBar {{"
            f"  background-color: {THEME.surface_container_highest};"
            f"  border: none;"
            f"  border-radius: 11px;"
            f"  color: #FFFFFF;"
            f"  font-size: 11px;"
            f"  font-weight: 700;"
            f"  text-align: center;"
            f"}}"
            f"QProgressBar.M3ProgressBar::chunk {{"
            f"  background-color: {hex_color};"
            f"  border-radius: 11px;"
            f"}}"
        )


class M3IconHelpButton(QPushButton):
    """24x24 circular tonal help button showing tooltip on hover and click."""

    def __init__(
        self,
        tooltip: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("", parent)
        self.setProperty("class", "IconHelpBtn")
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(
            icons.make_icon(
                "help",
                color=THEME.on_surface_variant,
                disabled_color=THEME.on_surface_disabled,
                size=14,
            )
        )
        self.setIconSize(QSize(14, 14))
        if tooltip:
            self.setToolTip(tooltip)
        self.clicked.connect(self._show_help_tooltip)

    def _show_help_tooltip(self) -> None:
        tip = self.toolTip()
        if tip:
            QToolTip.showText(self.mapToGlobal(QPoint(12, 24)), tip, self)


class M3ComboBox(QComboBox):
    """Material Design 3 combo box with crisp painted chevron_down icon."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self.isEnabled()
                else Qt.CursorShape.ForbiddenCursor
            )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        painter = QPainter(self)
        color = (
            THEME.on_surface_variant
            if self.isEnabled()
            else THEME.on_surface_disabled
        )
        pm = icons.render_icon_pixmap("chevron_down", color=color, size=14)
        x = self.width() - 22
        y = (self.height() - 14) // 2
        painter.drawPixmap(x, y, pm)
        painter.end()
