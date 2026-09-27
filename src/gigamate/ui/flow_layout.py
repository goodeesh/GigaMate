"""A layout that wraps its children, so rows reflow instead of overflowing.

Qt has no flow layout in the box, and a plain ``QHBoxLayout`` cannot reflow: its
minimum width is the *sum* of its children, so a row of chips or buttons either
overflows a narrow window or gets squeezed. That is not a cosmetic problem in
GigaMate Center — the page's minimum width then exceeds the viewport and the
content is clipped, because the pages sit in scroll areas with the horizontal
scrollbar switched off.

``FlowLayout`` lays children out left to right and wraps onto a new line when the
next one would not fit, and ``FlowContainer`` wraps it in a widget that reports
``heightForWidth`` so it behaves correctly inside a card's vertical layout.
"""

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Lay widgets out left-to-right, wrapping at the available width."""

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 12,
                 v_spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):  # pragma: no cover - Qt ownership dance
        self._items = []

    def addItem(self, item) -> None:  # noqa: N802 (Qt naming)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 (Qt naming)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 (Qt naming)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 (Qt naming)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        return self._arrange(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt naming)
        super().setGeometry(rect)
        self._arrange(rect, dry_run=False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt naming)
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, dry_run: bool) -> int:
        """Place items in rows that fit ``rect``; return the height used."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x = area.x()
        y = area.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            if next_x - self._h_spacing > area.right() and line_height > 0:
                x = area.x()
                y = y + line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class FlowContainer(QWidget):
    """A widget hosting a :class:`FlowLayout` that grows downwards as it wraps.

    ``heightForWidth`` is what makes the reflow visible to the enclosing layout:
    the row reports a height based on the width it is given, so cards stack
    correctly however narrow the window becomes.
    """

    def __init__(self, parent=None, spacing: int = 12,
                 margins: tuple = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, margin=0, h_spacing=spacing, v_spacing=spacing)
        m = QMargins(*margins)
        self._flow.setContentsMargins(m)
        # Minimum/Maximum: let the height follow heightForWidth instead of the
        # widget's own geometry, otherwise Qt would fight the wrapping.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802 (Qt naming)
        self._flow.addWidget(widget)

    def count(self) -> int:
        return self._flow.count()

    def widgetAt(self, index: int):  # noqa: N802 (Qt naming)
        item = self._flow.itemAt(index)
        return item.widget() if item is not None else None

    def clear(self) -> None:
        """Remove and destroy every child widget."""
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        return self._flow.heightForWidth(width)

    def hasHeightForWidthChanged(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt naming)
        # Width is the widest single child (rows wrap), height follows the width.
        return QSize(self._flow.minimumSize().width(), 0)
