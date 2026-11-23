"""Material Design 3 (Warm Obsidian) surface, header, banner, and metric tile widgets."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gigamate.ui import icons
from gigamate.ui.theme import THEME, tone_colors


class M3Card(QFrame):
    """Borderless Material Design 3 tonal card container."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "M3Card")

    def set_padding(self, h: int = 20, v: int = 20) -> None:
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(h, v, h, v)


class M3PageHeader(QWidget):
    """Top-of-page header with title, optional wrapped subtitle, and trailing slot."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)

        self.title_lbl = QLabel(title)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: {THEME.text_page_title}px; "
            f"font-weight: 700; background: transparent;"
        )
        text_col.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel(subtitle)
        self.subtitle_lbl.setWordWrap(True)
        self.subtitle_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_body}px; "
            f"background: transparent;"
        )
        self.subtitle_lbl.setVisible(bool(subtitle))
        text_col.addWidget(self.subtitle_lbl)

        root.addLayout(text_col, stretch=1)

        self._trailing_slot = QHBoxLayout()
        self._trailing_slot.setContentsMargins(0, 0, 0, 0)
        self._trailing_slot.setSpacing(8)
        root.addLayout(self._trailing_slot)

    def set_trailing_widget(self, widget: QWidget) -> None:
        """Place a trailing widget (e.g., status pill or action button) on the right."""
        while self._trailing_slot.count():
            item = self._trailing_slot.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        self._trailing_slot.addWidget(widget, alignment=Qt.AlignmentFlag.AlignVCenter)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 240), hint.height())


class M3StatusPill(QLabel):
    """Compact semantic pill badge."""

    def __init__(
        self,
        text: str = "",
        tone: str = "neutral",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        fg, bg, _border = tone_colors(tone)
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg}; "
            f"border-radius: 11px; padding: 4px 11px; "
            f"font-size: 11px; font-weight: 600;"
        )


class M3Banner(QFrame):
    """Tonal semantic banner with icon, title, optional body, and optional actions."""

    def __init__(
        self,
        title: str = "",
        body: str = "",
        tone: str = "warning",
        icon: str = "warning",
        compact: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self._tone = tone
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        root = QHBoxLayout(self)
        pad_v = 10 if compact else 12
        pad_h = 14 if compact else 16
        root.setContentsMargins(pad_h, pad_v, pad_h, pad_v)
        root.setSpacing(12)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(18, 18)
        self.icon_lbl.setStyleSheet("background: transparent;")
        root.addWidget(self.icon_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        content_col = QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        content_col.addWidget(self.title_lbl)

        self.body_lbl = QLabel(body)
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.body_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.body_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 12px; background: transparent;"
        )
        self.body_lbl.setVisible(bool(body))
        content_col.addWidget(self.body_lbl)

        self._action_container = QWidget(self)
        self.action_row = QHBoxLayout(self._action_container)
        self.action_row.setContentsMargins(0, 4, 0, 0)
        self.action_row.setSpacing(8)
        self._action_container.setVisible(False)
        content_col.addWidget(self._action_container)

        root.addLayout(content_col, stretch=1)

        self.set_tone(tone, icon=icon)

    def set_tone(self, tone: str, icon: Optional[str] = None) -> None:
        self._tone = tone
        if icon is not None:
            self._icon_name = icon
        fg, bg, border = tone_colors(tone)
        self.setStyleSheet(
            f"M3Banner {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 14px; }}"
        )
        self.title_lbl.setStyleSheet(
            f"color: {fg}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        self.icon_lbl.setPixmap(
            icons.render_icon_pixmap(self._icon_name, color=fg, size=18)
        )

    def set_content(self, title: str, body: str = "") -> None:
        self.title_lbl.setText(title)
        self.body_lbl.setText(body)
        self.body_lbl.setVisible(bool(body))

    def add_action(self, widget: QWidget) -> None:
        self.action_row.addWidget(widget)
        self._action_container.setVisible(True)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 240), hint.height())


class M3MiniBar(QWidget):
    """Compact 5px-tall rounded horizontal progress indicator."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ratio: float = 0.0
        self._color: str = THEME.accent_cta
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_progress(self, ratio: float, color: str = THEME.accent_cta) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self._color = color
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        h = 5.0
        y = (self.height() - h) / 2.0
        w = float(self.width())
        r = h / 2.0

        painter.setBrush(QBrush(QColor(THEME.surface_container_highest)))
        painter.drawRoundedRect(QRectF(0.0, y, w, h), r, r)

        if self._ratio > 0.0 and w > 0.0:
            fill_w = max(h, w * self._ratio)
            painter.setBrush(QBrush(QColor(self._color)))
            painter.drawRoundedRect(QRectF(0.0, y, min(w, fill_w), h), r, r)

        painter.end()


class M3MetricTile(QFrame):
    """Borderless tonal telemetry tile with icon, overline title, value, and optional bar/subtext."""

    def __init__(
        self,
        title: str,
        object_name: str,
        icon_name: str,
        subtext_obj: str = "",
        show_bar: bool = False,
        icon_color: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "MetricTile")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(16, 16)
        self.icon_lbl.setStyleSheet("background: transparent;")
        self.set_icon(icon_name, icon_color or THEME.on_surface_variant)
        header.addWidget(self.icon_lbl)

        self.title_lbl = QLabel(title)
        if object_name:
            self.title_lbl.setObjectName(f"{object_name}_title")
        self.title_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 11px; "
            f"font-weight: 700; letter-spacing: 0.4px; text-transform: uppercase; "
            f"background: transparent;"
        )
        header.addWidget(self.title_lbl, stretch=1)
        layout.addLayout(header)

        self.value_lbl = QLabel("--")
        if object_name:
            self.value_lbl.setObjectName(object_name)
        self.value_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-family: {THEME.mono_family}; "
            f"font-size: 22px; font-weight: 700; background: transparent;"
        )
        layout.addWidget(self.value_lbl)

        self.mini_bar: Optional[M3MiniBar] = None
        if show_bar:
            self.mini_bar = M3MiniBar(self)
            layout.addWidget(self.mini_bar)

        self.sub_lbl: Optional[QLabel] = None
        if subtext_obj:
            self.sub_lbl = QLabel("")
            self.sub_lbl.setObjectName(subtext_obj)
            self.sub_lbl.setWordWrap(True)
            self.sub_lbl.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: 11px; background: transparent;"
            )
            layout.addWidget(self.sub_lbl)

    def set_icon(self, icon_name: str, color: str = THEME.on_surface_variant) -> None:
        self.icon_lbl.setPixmap(
            icons.render_icon_pixmap(icon_name, color=color, size=16)
        )

    def set_value_color(self, color: str) -> None:
        self.value_lbl.setStyleSheet(
            f"color: {color}; font-family: {THEME.mono_family}; "
            f"font-size: 22px; font-weight: 700; background: transparent;"
        )

    def set_bar(self, ratio: float, color: str = THEME.accent_cta) -> None:
        if self.mini_bar is not None:
            self.mini_bar.set_progress(ratio, color)
