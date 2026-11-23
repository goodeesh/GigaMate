"""Material Design 3 (Warm Obsidian) connected segmented button bar."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from gigamate.ui import icons
from gigamate.ui.theme import THEME


class M3SegmentButton(QPushButton):
    """Single segment inside an M3SegmentedBar."""

    def __init__(
        self,
        text: str = "",
        checkable: bool = True,
        min_height: int = 40,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._locked: bool = False
        self.setProperty("class", "M3SegmentButton")
        self.setProperty("segPos", "only")
        self.setProperty("locked", False)
        self.setCheckable(checkable)
        self.setMinimumHeight(min_height)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(15, 15))
        self.toggled.connect(self._on_toggled)
        self._sync_icon()

    def _repolish(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def _on_toggled(self, _checked: bool) -> None:
        self._sync_icon()

    def setChecked(self, checked: bool) -> None:  # type: ignore[override]
        super().setChecked(checked)
        self._sync_icon()

    def _sync_icon(self) -> None:
        if self._locked:
            self.setIcon(
                icons.make_icon(
                    "lock",
                    color=THEME.on_surface_disabled,
                    disabled_color=THEME.on_surface_disabled,
                    size=15,
                )
            )
        elif self.isCheckable() and self.isChecked():
            self.setIcon(
                icons.make_icon(
                    "check",
                    color=THEME.on_primary_container,
                    disabled_color=THEME.on_surface_disabled,
                    size=15,
                )
            )
        else:
            self.setIcon(QIcon())

    def setSegmentPosition(self, pos: str) -> None:
        """Set segment position: 'only', 'first', 'middle', or 'last'."""
        self.setProperty("segPos", pos)
        self._repolish()

    def setLocked(self, locked: bool, tooltip: str = "") -> None:
        """Lock/unlock this segment with a disabled lock icon and optional tooltip."""
        self._locked = bool(locked)
        self.setEnabled(not self._locked)
        self.setProperty("locked", self._locked)
        self.setCursor(
            Qt.CursorShape.ForbiddenCursor
            if self._locked
            else Qt.CursorShape.PointingHandCursor
        )
        if tooltip:
            self.setToolTip(tooltip)
        self._sync_icon()
        self._repolish()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            if not self._locked:
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor
                    if self.isEnabled()
                    else Qt.CursorShape.ArrowCursor
                )
            self._sync_icon()

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 120), max(self.minimumHeight(), hint.height()))


class M3SegmentedBar(QFrame):
    """Connected Material Design 3 pill segmented button container."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "M3SegmentedBar")
        self._segments: list[M3SegmentButton] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    def _update_positions(self) -> None:
        count = len(self._segments)
        for idx, btn in enumerate(self._segments):
            if count == 1:
                pos = "only"
            elif idx == 0:
                pos = "first"
            elif idx == count - 1:
                pos = "last"
            else:
                pos = "middle"
            btn.setSegmentPosition(pos)

    def add_segment(
        self,
        label: str,
        tooltip: str = "",
        checkable: bool = True,
        min_height: int = 40,
    ) -> M3SegmentButton:
        """Create, append, and return a new M3SegmentButton."""
        btn = M3SegmentButton(
            label, checkable=checkable, min_height=min_height, parent=self
        )
        if tooltip:
            btn.setToolTip(tooltip)
        self.add_button(btn)
        return btn

    def add_button(self, btn: M3SegmentButton) -> None:
        """Append an existing M3SegmentButton to the bar."""
        btn.setParent(self)
        self._segments.append(btn)
        self._layout.addWidget(btn)
        self._update_positions()

    def clear_segments(self) -> None:
        """Remove and schedule deletion of all segment buttons."""
        for btn in self._segments:
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._segments.clear()

    def minimumSizeHint(self) -> QSize:
        if not self._segments:
            return QSize(80, 40)
        total_w = sum(btn.minimumSizeHint().width() for btn in self._segments)
        max_h = max(btn.minimumSizeHint().height() for btn in self._segments)
        return QSize(min(total_w, 480), max_h)
