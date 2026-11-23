"""Responsive grid layout widget for Material Design 3 cards and tiles."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QGridLayout, QSizePolicy, QWidget


class M3ResponsiveGrid(QWidget):
    """Grid container that switches between wide and narrow column counts at a breakpoint."""

    def __init__(
        self,
        wide_cols: int = 4,
        narrow_cols: int = 2,
        breakpoint: int = 660,
        h_spacing: int = 12,
        v_spacing: int = 12,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._wide_cols = max(1, wide_cols)
        self._narrow_cols = max(1, narrow_cols)
        self._breakpoint = breakpoint
        self._widgets: list[QWidget] = []
        self._current_cols: int = 0

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(h_spacing)
        self._grid.setVerticalSpacing(v_spacing)

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

    def _target_columns(self, width: Optional[int] = None) -> int:
        w = self.width() if width is None else width
        if w <= 0:
            return self._wide_cols
        return self._wide_cols if w >= self._breakpoint else self._narrow_cols

    def _relayout(self, cols: Optional[int] = None) -> None:
        target = self._target_columns() if cols is None else max(1, cols)
        self._current_cols = target

        while self._grid.count():
            self._grid.takeAt(0)

        for idx, widget in enumerate(self._widgets):
            self._grid.addWidget(widget, idx // target, idx % target)

        max_cols = max(self._wide_cols, self._narrow_cols)
        for col in range(max_cols):
            self._grid.setColumnStretch(col, 1 if col < target else 0)

    def addWidget(self, widget: QWidget) -> None:
        self._widgets.append(widget)
        self._relayout(self._target_columns())

    def clear(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        for widget in self._widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._widgets.clear()
        self._current_cols = 0

    def count(self) -> int:
        return len(self._widgets)

    def widgetAt(self, index: int) -> Optional[QWidget]:
        if 0 <= index < len(self._widgets):
            return self._widgets[index]
        return None

    def current_columns(self) -> int:
        return self._current_cols or self._target_columns()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        target = self._target_columns()
        if target != self._current_cols:
            self._relayout(target)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        target = self._target_columns(event.size().width())
        if target != self._current_cols:
            self._relayout(target)

    def minimumSizeHint(self) -> QSize:
        visible_children = [w for w in self._widgets if not w.isHidden()]
        min_w = max(
            (min(w.minimumSizeHint().width(), 220) for w in visible_children),
            default=80,
        )
        return QSize(min_w, super().minimumSizeHint().height())
