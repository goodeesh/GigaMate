"""Material Design 3 (Warm Obsidian) toggle switch widgets."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gigamate.ui.theme import THEME


class M3Switch(QCheckBox):
    """Material Design 3 pill toggle switch (50x28 track) painted via QPainter."""

    TRACK_W = 50
    TRACK_H = 28
    _SPACING = 10

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._anim_pos: float = 1.0 if self.isChecked() else 0.0
        self._hovered: bool = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)

        self.toggled.connect(self._sync_animation)

    def _on_anim_value(self, value: object) -> None:
        self._anim_pos = float(value)
        self.update()

    def _sync_animation(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            self._anim.stop()
            self._anim_pos = target
            self.update()
            return
        if abs(self._anim_pos - target) < 1e-3:
            self._anim_pos = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._anim_pos)
        self._anim.setEndValue(target)
        self._anim.start()

    def checkStateSet(self) -> None:
        super().checkStateSet()
        self._sync_animation(self.isChecked())

    def nextCheckState(self) -> None:
        super().nextCheckState()
        self._sync_animation(self.isChecked())

    def sizeHint(self) -> QSize:
        if not self.text():
            return QSize(self.TRACK_W, self.TRACK_H)
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(self.text())
        h = max(self.TRACK_H, fm.height() + 4)
        return QSize(self.TRACK_W + self._SPACING + text_w + 4, h)

    def minimumSizeHint(self) -> QSize:
        if not self.text():
            return QSize(self.TRACK_W, self.TRACK_H)
        return QSize(self.TRACK_W + self._SPACING + 20, self.TRACK_H)

    def hitButton(self, pos) -> bool:  # type: ignore[override]
        return self.isEnabled() and self.rect().contains(pos)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self.isEnabled()
                else Qt.CursorShape.ForbiddenCursor
            )
            self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        t = max(0.0, min(1.0, self._anim_pos))
        checked = self.isChecked()

        track_y = (self.height() - self.TRACK_H) / 2.0
        track_rect = QRectF(0.0, track_y, float(self.TRACK_W), float(self.TRACK_H))
        radius = self.TRACK_H / 2.0

        # Track fill & border
        if not enabled:
            track_fill = QColor(THEME.surface_disabled)
            border_color: Optional[QColor] = QColor(THEME.outline_variant)
        elif checked or t > 0.5:
            track_fill = QColor(
                THEME.accent_cta_hover if self._hovered else THEME.accent_cta
            )
            border_color = None
        else:
            track_fill = QColor(THEME.surface_container_highest)
            border_color = QColor(THEME.outline)

        if border_color is not None:
            pen = QPen(border_color, 1.5)
            painter.setPen(pen)
            painter.setBrush(QBrush(track_fill))
            painter.drawRoundedRect(
                track_rect.adjusted(0.75, 0.75, -0.75, -0.75),
                radius - 0.75,
                radius - 0.75,
            )
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(track_fill))
            painter.drawRoundedRect(track_rect, radius, radius)

        # Thumb geometry interpolated smoothly between off (14px) and on (20px)
        thumb_d = 14.0 + (20.0 - 14.0) * t
        off_cx = 14.0
        on_cx = float(self.TRACK_W) - 14.0
        cx = off_cx + (on_cx - off_cx) * t
        cy = track_y + self.TRACK_H / 2.0

        # Hover halo
        if self._hovered and enabled:
            halo_color = QColor(THEME.primary if checked else THEME.on_surface)
            halo_color.setAlpha(36)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(halo_color))
            painter.drawEllipse(QPointF(cx, cy), thumb_d / 2.0 + 4.0, thumb_d / 2.0 + 4.0)

        # Thumb fill
        if not enabled:
            thumb_color = QColor(THEME.on_surface_disabled)
        elif checked or t > 0.5:
            thumb_color = QColor("#FFFFFF")
        else:
            thumb_color = QColor(THEME.on_surface_variant)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(thumb_color))
        painter.drawEllipse(QPointF(cx, cy), thumb_d / 2.0, thumb_d / 2.0)

        # Crisp checkmark inside thumb when checked
        if enabled and t > 0.65:
            check_alpha = int(255 * min(1.0, (t - 0.65) / 0.35))
            check_color = QColor(THEME.accent_cta)
            check_color.setAlpha(check_alpha)
            check_pen = QPen(
                check_color,
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.setPen(check_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 4.0, cy + 0.3)
            path.lineTo(cx - 1.2, cy + 3.2)
            path.lineTo(cx + 4.2, cy - 2.6)
            painter.drawPath(path)

        # Optional label text
        if self.text():
            text_color = QColor(
                THEME.on_surface if enabled else THEME.on_surface_disabled
            )
            painter.setPen(text_color)
            text_rect = QRectF(
                float(self.TRACK_W + self._SPACING),
                0.0,
                float(max(0, self.width() - self.TRACK_W - self._SPACING)),
                float(self.height()),
            )
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )

        painter.end()


class M3SwitchRow(QFrame):
    """Horizontal settings row with title, optional wrapped subtitle, and trailing M3Switch."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        checked: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "M3SwitchRow")
        self.setProperty("clickable", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_lbl = QLabel(title)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        text_col.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel(subtitle)
        self.subtitle_lbl.setWordWrap(True)
        self.subtitle_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: 12px; background: transparent;"
        )
        self.subtitle_lbl.setVisible(bool(subtitle))
        text_col.addWidget(self.subtitle_lbl)

        layout.addLayout(text_col, stretch=1)

        self.switch = M3Switch()
        self.switch.setChecked(checked)
        self.switch.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.switch.installEventFilter(self)
        layout.addWidget(self.switch, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._sync_enabled_visuals()

    def _sync_enabled_visuals(self) -> None:
        enabled = self.isEnabled() and self.switch.isEnabled()
        title_color = THEME.on_surface if enabled else THEME.on_surface_disabled
        subtitle_color = (
            THEME.on_surface_variant if enabled else THEME.on_surface_disabled
        )
        self.title_lbl.setStyleSheet(
            f"color: {title_color}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        self.subtitle_lbl.setStyleSheet(
            f"color: {subtitle_color}; font-size: 12px; background: transparent;"
        )
        self.setProperty("clickable", "true" if enabled else "false")
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if enabled
            else Qt.CursorShape.ArrowCursor
        )
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.switch and event.type() == QEvent.Type.EnabledChange:
            self._sync_enabled_visuals()
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_enabled_visuals()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.switch.isEnabled()
            and not self.switch.geometry().contains(event.pos())
        ):
            self.switch.click()
            event.accept()
            return
        super().mouseReleaseEvent(event)
