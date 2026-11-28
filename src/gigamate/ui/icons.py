"""Zero-dependency monochrome Material Symbols Rounded vector icon engine."""

from __future__ import annotations

import math
from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from gigamate.ui.theme import THEME

_ICON_CACHE: dict[tuple[str, str, int, int], QPixmap] = {}


def _make_pen(color: QColor, width: float = 1.9) -> QPen:
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_dashboard(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for x, y in ((3.5, 3.5), (13.5, 3.5), (3.5, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(QRectF(x, y, 7.0, 7.0), 1.8, 1.8)


def _draw_battery(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2.5, 6.5, 16.5, 11.0), 2.2, 2.2)
    # Terminal nub
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawRoundedRect(QRectF(19.5, 9.5, 2.2, 5.0), 1.0, 1.0)
    # Inner charge fill bar
    p.drawRoundedRect(QRectF(4.8, 8.8, 9.5, 6.4), 1.0, 1.0)


def _draw_keyboard(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(2.5, 5.5, 19.0, 13.0), 2.2, 2.2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    for row_y in (8.5, 11.5):
        for col_x in (5.2, 8.2, 11.2, 14.2, 17.2):
            p.drawEllipse(QPointF(col_x, row_y), 0.9, 0.9)
    p.drawRoundedRect(QRectF(7.0, 14.5, 10.0, 1.6), 0.8, 0.8)


def _draw_gpu(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5.5, 5.5, 13.0, 13.0), 2.0, 2.0)
    p.drawRoundedRect(QRectF(9.0, 9.0, 6.0, 6.0), 1.0, 1.0)
    for pos in (8.5, 12.0, 15.5):
        p.drawLine(QPointF(pos, 2.8), QPointF(pos, 5.5))
        p.drawLine(QPointF(pos, 18.5), QPointF(pos, 21.2))
        p.drawLine(QPointF(2.8, pos), QPointF(5.5, pos))
        p.drawLine(QPointF(18.5, pos), QPointF(21.2, pos))


def _draw_tune(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    tracks = ((6.0, 15.5), (12.0, 8.5), (18.0, 14.5))
    for y, thumb_x in tracks:
        p.drawLine(QPointF(3.5, y), QPointF(thumb_x - 2.3, y))
        p.drawLine(QPointF(thumb_x + 2.3, y), QPointF(20.5, y))
        p.drawEllipse(QPointF(thumb_x, y), 2.2, 2.2)


def _draw_settings(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 3.0, 3.0)
    path = QPainterPath()
    teeth = 8
    r_outer = 9.2
    r_inner = 7.0
    for i in range(teeth * 2):
        angle = (i * math.pi) / teeth - math.pi / 2.0
        r = r_outer if i % 2 == 0 else r_inner
        pt = QPointF(12.0 + r * math.cos(angle), 12.0 + r * math.sin(angle))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    p.drawPath(path)


def _draw_thermometer(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(9.8, 14.2)
    path.lineTo(9.8, 5.2)
    path.arcTo(QRectF(9.8, 3.0, 4.4, 4.4), 180.0, -180.0)
    path.lineTo(14.2, 14.2)
    path.arcTo(QRectF(8.0, 13.5, 8.0, 8.0), 42.0, -264.0)
    path.closeSubpath()
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 17.5), 2.1, 2.1)
    p.setPen(_make_pen(c, 1.5))
    p.drawLine(QPointF(12.0, 9.0), QPointF(12.0, 16.0))


def _draw_fan(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 2.1, 2.1)
    for deg in (0, 120, 240):
        p.save()
        p.translate(12.0, 12.0)
        p.rotate(deg)
        blade = QPainterPath()
        blade.moveTo(0.0, -2.1)
        blade.cubicTo(4.2, -3.5, 7.2, -8.5, 1.8, -9.5)
        blade.cubicTo(-2.5, -10.2, -3.2, -5.2, -1.2, -2.0)
        p.drawPath(blade)
        p.restore()


def _draw_gauge(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(3.2, 3.8, 17.6, 17.6), 210 * 16, -240 * 16)
    p.drawLine(QPointF(12.0, 13.0), QPointF(16.4, 8.2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 13.0), 1.6, 1.6)


def _draw_bolt(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    bolt = QPainterPath()
    bolt.moveTo(13.2, 2.8)
    bolt.lineTo(5.8, 13.2)
    bolt.lineTo(11.4, 13.2)
    bolt.lineTo(10.6, 21.2)
    bolt.lineTo(18.2, 10.6)
    bolt.lineTo(12.6, 10.6)
    bolt.closeSubpath()
    p.drawPath(bolt)


def _draw_power(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(4.2, 4.5, 15.6, 15.6), 130 * 16, 280 * 16)
    p.drawLine(QPointF(12.0, 3.0), QPointF(12.0, 11.2))


def _draw_power_off(p: QPainter, c: QColor) -> None:
    _draw_power(p, c)
    p.setPen(_make_pen(c))
    p.drawLine(QPointF(4.0, 4.0), QPointF(20.0, 20.0))


def _draw_heart(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    heart = QPainterPath()
    heart.moveTo(12.0, 20.2)
    heart.cubicTo(4.0, 14.8, 2.8, 8.2, 6.6, 5.2)
    heart.cubicTo(9.0, 3.4, 11.2, 4.6, 12.0, 6.4)
    heart.cubicTo(12.8, 4.6, 15.0, 3.4, 17.4, 5.2)
    heart.cubicTo(21.2, 8.2, 20.0, 14.8, 12.0, 20.2)
    p.drawPath(heart)


def _draw_plug(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(9.0, 3.2), QPointF(9.0, 7.5))
    p.drawLine(QPointF(15.0, 3.2), QPointF(15.0, 7.5))
    p.drawRoundedRect(QRectF(6.5, 7.5, 11.0, 7.2), 2.4, 2.4)
    p.drawLine(QPointF(12.0, 14.7), QPointF(12.0, 20.8))


def _draw_laptop(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(4.2, 5.0, 15.6, 10.5), 1.8, 1.8)
    p.drawLine(QPointF(2.2, 18.2), QPointF(21.8, 18.2))


def _draw_monitor(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(3.0, 4.2, 18.0, 12.0), 2.0, 2.0)
    p.drawLine(QPointF(12.0, 16.2), QPointF(12.0, 19.6))
    p.drawLine(QPointF(8.0, 19.6), QPointF(16.0, 19.6))


def _draw_volume(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    speaker = QPainterPath()
    speaker.moveTo(4.0, 9.5)
    speaker.lineTo(7.8, 9.5)
    speaker.lineTo(12.5, 5.5)
    speaker.lineTo(12.5, 18.5)
    speaker.lineTo(7.8, 14.5)
    speaker.lineTo(4.0, 14.5)
    speaker.closeSubpath()
    p.drawPath(speaker)
    p.drawArc(QRectF(13.5, 8.2, 4.8, 7.6), 55 * 16, -110 * 16)
    p.drawArc(QRectF(15.2, 6.0, 6.2, 12.0), 55 * 16, -110 * 16)


def _draw_check(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 2.1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(4.8, 12.5)
    path.lineTo(9.6, 17.3)
    path.lineTo(19.2, 7.0)
    p.drawPath(path)


def _draw_lock(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(5.0, 10.2, 14.0, 10.2), 2.2, 2.2)
    p.drawArc(QRectF(8.0, 3.8, 8.0, 10.0), 0, 180 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 15.2), 1.4, 1.4)


def _draw_warning(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    tri = QPainterPath()
    tri.moveTo(12.0, 3.5)
    tri.lineTo(20.8, 19.2)
    tri.lineTo(3.2, 19.2)
    tri.closeSubpath()
    p.drawPath(tri)
    p.drawLine(QPointF(12.0, 9.5), QPointF(12.0, 13.8))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 16.5), 1.1, 1.1)


def _draw_info(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 9.0, 9.0)
    p.drawLine(QPointF(12.0, 11.2), QPointF(12.0, 16.4))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 8.0), 1.1, 1.1)


def _draw_error(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 9.0, 9.0)
    p.drawLine(QPointF(8.8, 8.8), QPointF(15.2, 15.2))
    p.drawLine(QPointF(15.2, 8.8), QPointF(8.8, 15.2))


def _draw_refresh(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(4.2, 4.2, 15.6, 15.6), 42 * 16, 280 * 16)
    head = QPainterPath()
    head.moveTo(14.5, 6.8)
    head.lineTo(17.8, 6.8)
    head.lineTo(17.8, 3.5)
    p.drawPath(head)


def _draw_wrench(p: QPainter, c: QColor) -> None:
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_make_pen(c, 2.0))
    p.drawArc(QRectF(11.2, 4.0, 8.8, 8.8), 82 * 16, 276 * 16)
    notch = QPainterPath()
    notch.moveTo(16.2, 4.0)
    notch.lineTo(14.4, 7.6)
    notch.lineTo(16.8, 9.6)
    notch.lineTo(20.0, 8.0)
    p.drawPath(notch)
    p.setPen(_make_pen(c, 2.4))
    p.drawLine(QPointF(12.2, 11.8), QPointF(5.0, 19.0))


def _draw_undo(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    head = QPainterPath()
    head.moveTo(8.2, 5.8)
    head.lineTo(4.8, 9.2)
    head.lineTo(8.2, 12.6)
    p.drawPath(head)
    p.drawLine(QPointF(5.2, 9.2), QPointF(14.2, 9.2))
    p.drawArc(QRectF(10.0, 9.2, 9.0, 8.6), 90 * 16, -180 * 16)


def _draw_help(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 9.0, 9.0)
    q = QPainterPath()
    q.moveTo(9.6, 9.4)
    q.cubicTo(9.8, 7.2, 14.4, 7.2, 14.4, 9.6)
    q.cubicTo(14.4, 11.4, 12.0, 11.6, 12.0, 13.6)
    p.drawPath(q)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 16.6), 1.1, 1.1)


def _draw_close(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(6.0, 6.0), QPointF(18.0, 18.0))
    p.drawLine(QPointF(18.0, 6.0), QPointF(6.0, 18.0))


def _draw_chevron_down(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 2.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(6.0, 9.0)
    path.lineTo(12.0, 15.0)
    path.lineTo(18.0, 9.0)
    p.drawPath(path)


def _draw_sparkles(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c, 1.7))
    p.setBrush(Qt.BrushStyle.NoBrush)

    def _star(cx: float, cy: float, r_out: float, r_in: float) -> QPainterPath:
        path = QPainterPath()
        for i in range(8):
            angle = i * math.pi / 4.0 - math.pi / 2.0
            r = r_out if i % 2 == 0 else r_in
            pt = QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        return path

    p.drawPath(_star(10.5, 13.0, 7.2, 2.2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(_star(18.2, 6.2, 3.2, 1.0))


def _draw_dot(p: QPainter, c: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(12.0, 12.0), 4.5, 4.5)


def _draw_fallback(p: QPainter, c: QColor) -> None:
    p.setPen(_make_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12.0, 12.0), 6.5, 6.5)


_ICON_DRAWERS: dict[str, Callable[[QPainter, QColor], None]] = {
    "dashboard": _draw_dashboard,
    "battery": _draw_battery,
    "keyboard": _draw_keyboard,
    "gpu": _draw_gpu,
    "tune": _draw_tune,
    "settings": _draw_settings,
    "thermometer": _draw_thermometer,
    "fan": _draw_fan,
    "gauge": _draw_gauge,
    "bolt": _draw_bolt,
    "power": _draw_power,
    "power_off": _draw_power_off,
    "heart": _draw_heart,
    "plug": _draw_plug,
    "laptop": _draw_laptop,
    "monitor": _draw_monitor,
    "volume": _draw_volume,
    "check": _draw_check,
    "lock": _draw_lock,
    "warning": _draw_warning,
    "info": _draw_info,
    "error": _draw_error,
    "refresh": _draw_refresh,
    "wrench": _draw_wrench,
    "undo": _draw_undo,
    "help": _draw_help,
    "close": _draw_close,
    "chevron_down": _draw_chevron_down,
    "sparkles": _draw_sparkles,
    "dot": _draw_dot,
}


def render_icon_pixmap(
    name: str,
    color: str = THEME.on_surface,
    size: int = 18,
    dpr: float = 2.0,
) -> QPixmap:
    """Render a named monochrome vector icon into a cached HiDPI QPixmap."""
    key = (name, color, int(size), int(round(dpr * 100)))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    px_size = max(1, int(round(size * dpr)))
    pixmap = QPixmap(px_size, px_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = size / 24.0
    painter.scale(scale, scale)

    drawer = _ICON_DRAWERS.get(name, _draw_fallback)
    drawer(painter, QColor(color))
    painter.end()

    _ICON_CACHE[key] = pixmap
    return pixmap


def make_icon(
    name: str,
    color: str = THEME.on_surface,
    size: int = 18,
    active_color: Optional[str] = None,
    disabled_color: Optional[str] = THEME.on_surface_disabled,
    dpr: float = 2.0,
) -> QIcon:
    """Build a QIcon with Normal/Off and optional Active/Selected and Disabled states."""
    icon = QIcon()
    icon.addPixmap(
        render_icon_pixmap(name, color=color, size=size, dpr=dpr),
        QIcon.Mode.Normal,
        QIcon.State.Off,
    )
    if active_color is not None:
        active_pm = render_icon_pixmap(name, color=active_color, size=size, dpr=dpr)
        icon.addPixmap(active_pm, QIcon.Mode.Normal, QIcon.State.On)
        icon.addPixmap(active_pm, QIcon.Mode.Selected, QIcon.State.On)
    if disabled_color is not None:
        disabled_pm = render_icon_pixmap(name, color=disabled_color, size=size, dpr=dpr)
        icon.addPixmap(disabled_pm, QIcon.Mode.Disabled, QIcon.State.Off)
        icon.addPixmap(disabled_pm, QIcon.Mode.Disabled, QIcon.State.On)
    return icon
