"""Aero Instrument custom vector/canvas widgets for GigaMate."""

from __future__ import annotations

import math
import os
from collections import deque
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

from gigamate.ui.theme import THEME


def _is_offscreen() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"


class ArcGaugeWidget(QWidget):
    """240-degree precision radial arc gauge with smooth animated needle/arc."""

    def __init__(
        self,
        title: str,
        unit: str = "",
        min_val: float = 0.0,
        max_val: float = 100.0,
        size: int = 96,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._unit = unit
        self._min_val = float(min_val)
        self._max_val = float(max_val)
        self._gauge_size = max(64, int(size))
        self._value: Optional[float] = None
        self._display_value: float = self._min_val
        self._color = QColor(THEME.primary)

        self._anim = QPropertyAnimation(self, b"display_value", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(self._gauge_size, self._gauge_size)

    def sizeHint(self) -> QSize:
        return QSize(self._gauge_size, self._gauge_size)

    def minimumSizeHint(self) -> QSize:
        return QSize(min(self._gauge_size, 96), min(self._gauge_size, 96))

    @pyqtProperty(float)
    def display_value(self) -> float:
        return self._display_value

    @display_value.setter
    def display_value(self, val: float) -> None:
        self._display_value = float(val)
        self.update()

    def set_value(
        self,
        value: Optional[float],
        color: Optional[str] = None,
        animated: bool = True,
    ) -> None:
        if color is not None:
            self._color = QColor(color)
        self._value = None if value is None else float(value)
        target = (
            self._min_val
            if self._value is None
            else max(self._min_val, min(self._max_val, self._value))
        )
        if not animated or _is_offscreen():
            self._anim.stop()
            self._display_value = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(self._display_value)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        margin = max(8.0, side * 0.11)
        rect = QRectF(
            (self.width() - side) / 2.0 + margin,
            (self.height() - side) / 2.0 + margin,
            side - 2 * margin,
            side - 2 * margin,
        )
        stroke = max(5.0, side * 0.085)

        # Background track: 240 deg starting at 210 deg (bottom-left) clockwise
        track_pen = QPen(QColor(THEME.surface_container_highest), stroke)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track_pen)
        p.drawArc(rect, 210 * 16, -240 * 16)

        span = max(1e-6, self._max_val - self._min_val)
        frac = max(0.0, min(1.0, (self._display_value - self._min_val) / span))
        if self._value is not None and frac > 0.0:
            arc_pen = QPen(self._color, stroke)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(arc_pen)
            p.drawArc(rect, 210 * 16, int(-240 * 16 * frac))

        # Center readout
        val_text = "--" if self._value is None else f"{self._value:.0f}{self._unit}"
        p.setPen(QColor(THEME.on_surface))
        val_font = QFont()
        val_font.setPointSize(max(10, int(side * 0.16)))
        val_font.setBold(True)
        p.setFont(val_font)
        p.drawText(
            QRectF(0, side * 0.28, self.width(), side * 0.34),
            Qt.AlignmentFlag.AlignCenter,
            val_text,
        )

        # Title
        p.setPen(QColor(THEME.on_surface_muted))
        title_font = QFont()
        title_font.setPointSize(max(8, int(side * 0.095)))
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(
            QRectF(0, side * 0.68, self.width(), side * 0.24),
            Qt.AlignmentFlag.AlignCenter,
            self._title,
        )


class SparklineWidget(QWidget):
    """Compact live telemetry sparkline with gradient fill and terminal dot."""

    def __init__(
        self,
        min_val: float = 30.0,
        max_val: float = 95.0,
        max_points: int = 30,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._min_val = float(min_val)
        self._max_val = float(max_val)
        self._points: deque[float] = deque(maxlen=max(2, int(max_points)))
        self._color = QColor(THEME.primary)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(36)

    def sizeHint(self) -> QSize:
        return QSize(138, 42)

    def minimumSizeHint(self) -> QSize:
        return QSize(80, 36)

    def push(self, val: float) -> None:
        self._points.append(float(val))
        self.update()

    def set_data(self, values: list[float]) -> None:
        self._points.clear()
        for v in values:
            self._points.append(float(v))
        self.update()

    def set_color(self, hex_color: str) -> None:
        self._color = QColor(hex_color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        mid_pen = QPen(QColor(THEME.outline_variant), 1.0, Qt.PenStyle.DashLine)
        p.setPen(mid_pen)
        p.drawLine(QPointF(0.0, h * 0.5), QPointF(w, h * 0.5))

        if len(self._points) < 2:
            return

        pad_x, pad_y = 3.0, 4.0
        uw = max(1.0, w - 2 * pad_x)
        uh = max(1.0, h - 2 * pad_y)
        span = max(1e-6, self._max_val - self._min_val)

        pts: list[QPointF] = []
        n = len(self._points)
        for i, v in enumerate(self._points):
            clamped = max(self._min_val, min(self._max_val, v))
            x = pad_x + (i / max(1, n - 1)) * uw
            y = pad_y + uh - ((clamped - self._min_val) / span) * uh
            pts.append(QPointF(x, y))

        line_path = QPainterPath(pts[0])
        for pt in pts[1:]:
            line_path.lineTo(pt)

        area_path = QPainterPath(line_path)
        area_path.lineTo(pts[-1].x(), h)
        area_path.lineTo(pts[0].x(), h)
        area_path.closeSubpath()

        grad = QLinearGradient(0.0, 0.0, 0.0, h)
        c_top = QColor(self._color)
        c_top.setAlphaF(0.30)
        c_bot = QColor(self._color)
        c_bot.setAlphaF(0.0)
        grad.setColorAt(0.0, c_top)
        grad.setColorAt(1.0, c_bot)
        p.fillPath(area_path, QBrush(grad))

        line_pen = QPen(self._color, 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(line_pen)
        p.drawPath(line_path)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(pts[-1], 2.8, 2.8)


class PowerEnvelopeBarWidget(QWidget):
    """Horizontal stacked power budget bar for CPU / GPU / Combined watts."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cpu_w: Optional[int] = None
        self._gpu_w: Optional[int] = None
        self._combined_w: Optional[int] = None
        self._max_w = 160
        self._accent = QColor(THEME.primary)
        self._gated = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(44)

    def sizeHint(self) -> QSize:
        return QSize(220, 44)

    def minimumSizeHint(self) -> QSize:
        return QSize(120, 44)

    def set_budget(
        self,
        cpu_w: Optional[int],
        gpu_w: Optional[int],
        combined_w: Optional[int],
        max_w: int = 160,
        accent_hex: str = THEME.primary,
        gated: bool = False,
    ) -> None:
        self._cpu_w = cpu_w
        self._gpu_w = gpu_w
        self._combined_w = combined_w
        self._max_w = max(1, int(max_w))
        self._accent = QColor(accent_hex)
        self._gated = gated
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        bar_y = 6.0
        bar_h = 12.0
        track_rect = QRectF(0.0, bar_y, w, bar_h)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(THEME.surface_container_highest))
        p.drawRoundedRect(track_rect, 6.0, 6.0)

        cpu = max(0, self._cpu_w or 0)
        gpu = max(0, self._gpu_w or 0)
        total = self._combined_w if self._combined_w is not None else (cpu + gpu)
        scale = w / float(self._max_w)

        if self._gated:
            bar_color = QColor(THEME.gated)
            bar_color.setAlphaF(0.45)
        else:
            bar_color = self._accent

        cpu_w_px = min(w, cpu * scale)
        gpu_w_px = min(max(0.0, w - cpu_w_px), gpu * scale)

        if cpu_w_px > 0:
            p.setBrush(QColor(THEME.info))
            p.drawRoundedRect(QRectF(0.0, bar_y, cpu_w_px, bar_h), 4.0, 4.0)
        if gpu_w_px > 0:
            p.setBrush(bar_color)
            p.drawRoundedRect(QRectF(cpu_w_px, bar_y, gpu_w_px, bar_h), 4.0, 4.0)

        # Combined cap tick
        if total > 0:
            cap_x = min(w - 2.0, total * scale)
            p.setPen(QPen(QColor(THEME.on_surface), 2.0))
            p.drawLine(QPointF(cap_x, bar_y - 2.0), QPointF(cap_x, bar_y + bar_h + 2.0))

        # Legend
        p.setPen(QColor(THEME.on_surface_variant))
        font = QFont()
        font.setPointSize(9)
        p.setFont(font)
        cpu_s = "--" if self._cpu_w is None else f"{self._cpu_w}W"
        gpu_s = "--" if self._gpu_w is None else f"{self._gpu_w}W"
        tot_s = "--" if self._combined_w is None else f"{self._combined_w}W"
        p.drawText(
            QRectF(0.0, bar_y + bar_h + 4.0, w, 18.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"CPU {cpu_s}  •  GPU {gpu_s}  •  Total {tot_s}",
        )


class UsbcBudgetBarWidget(QWidget):
    """Three-segment allocation bar for GPU / CPU / Safety Buffer on USB-C PD."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gpu_w = 55
        self._cpu_w = 20
        self._buffer_w = 25
        self._total_w = 100
        self._accent = QColor(THEME.primary)
        self._dimmed = True
        self._trimmed = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(46)

    def sizeHint(self) -> QSize:
        return QSize(240, 46)

    def minimumSizeHint(self) -> QSize:
        return QSize(140, 46)

    def set_allocation(
        self,
        gpu_w: int,
        cpu_w: int,
        buffer_w: int,
        total_w: int,
        *,
        accent_hex: str = THEME.primary,
        dimmed: bool = False,
        trimmed: bool = False,
    ) -> None:
        self._gpu_w = max(0, int(gpu_w))
        self._cpu_w = max(0, int(cpu_w))
        self._buffer_w = max(0, int(buffer_w))
        self._total_w = max(0, int(total_w))
        self._accent = QColor(accent_hex)
        self._dimmed = bool(dimmed)
        self._trimmed = bool(trimmed)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        bar_y = 6.0
        bar_h = 12.0
        track_rect = QRectF(0.0, bar_y, w, bar_h)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(THEME.surface_container_highest))
        p.drawRoundedRect(track_rect, 6.0, 6.0)

        denom = max(1, self._total_w, self._gpu_w + self._cpu_w + self._buffer_w)
        alpha = 0.38 if self._dimmed else 1.0
        gpu_px = min(w, w * (self._gpu_w / float(denom)))
        cpu_px = min(max(0.0, w - gpu_px), w * (self._cpu_w / float(denom)))
        buf_px = max(0.0, w - gpu_px - cpu_px)

        if gpu_px > 0:
            gpu_col = QColor(THEME.warning if self._trimmed else self._accent)
            gpu_col.setAlphaF(alpha)
            p.setBrush(gpu_col)
            p.drawRoundedRect(QRectF(0.0, bar_y, gpu_px, bar_h), 4.0, 4.0)

        if cpu_px > 0:
            cpu_col = QColor(THEME.info)
            cpu_col.setAlphaF(alpha)
            p.setBrush(cpu_col)
            p.drawRoundedRect(QRectF(gpu_px, bar_y, cpu_px, bar_h), 4.0, 4.0)

        if buf_px > 0:
            hatch = QColor(THEME.on_surface_muted)
            p.setBrush(QBrush(hatch, Qt.BrushStyle.BDiagPattern))
            p.setOpacity(0.18 if self._dimmed else 0.28)
            p.drawRoundedRect(QRectF(gpu_px + cpu_px, bar_y, buf_px, bar_h), 4.0, 4.0)
            p.setOpacity(1.0)

        p.setPen(QColor(THEME.on_surface_variant))
        font = QFont()
        font.setPointSize(9)
        p.setFont(font)
        if self._total_w > 0:
            legend = f"GPU {self._gpu_w} W  •  CPU {self._cpu_w} W  •  Buffer {self._buffer_w} W  ({self._total_w} W)"
        else:
            legend = "Waiting for USB-C PD contract / GPU caps"
        p.drawText(
            QRectF(0.0, bar_y + bar_h + 4.0, w, 18.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            legend,
        )


class VfCurveWidget(QWidget):
    """Voltage/Frequency curve plot with Stock vs Tuned and Boost Cap clamp."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._uv_offset = 0
        self._max_clock: Optional[int] = None
        self._ceiling = 2600
        self._accent = QColor(THEME.primary)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(132)

    def sizeHint(self) -> QSize:
        return QSize(360, 148)

    def minimumSizeHint(self) -> QSize:
        return QSize(160, 132)

    def set_curve(
        self,
        uv_offset_mhz: int,
        max_clock_mhz: Optional[int],
        ceiling_mhz: int = 2600,
        accent_hex: str = THEME.primary,
    ) -> None:
        self._uv_offset = int(uv_offset_mhz)
        self._max_clock = max_clock_mhz
        self._ceiling = max(1200, int(ceiling_mhz))
        self._accent = QColor(accent_hex)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        x0, x1 = 40.0, max(60.0, w - 12.0)
        y0, y1 = max(40.0, h - 22.0), 18.0

        y_min = 900.0
        raw_top = max(2800.0, float(self._ceiling + max(0, self._uv_offset) + 180))
        y_max = float(int(math.ceil(raw_top / 400.0)) * 400)

        def map_x(mv: float) -> float:
            return x0 + ((mv - 650.0) / 400.0) * (x1 - x0)

        def map_y(mhz: float) -> float:
            clamped = max(y_min, min(y_max, mhz))
            return y0 - ((clamped - y_min) / (y_max - y_min)) * (y0 - y1)

        def stock_mhz(mv: float) -> float:
            return 1020.0 + max(600.0, float(self._ceiling) - 1020.0) * math.pow((mv - 650.0) / 400.0, 0.72)

        # Grid lines
        grid_font = QFont()
        grid_font.setPointSize(8)
        p.setFont(grid_font)
        step = 500 if y_max >= 3400 else 400
        for freq in range(1200, int(y_max) - 199, step):
            gy = map_y(freq)
            p.setPen(QPen(QColor(THEME.outline_variant), 1.0))
            p.drawLine(QPointF(x0, gy), QPointF(x1, gy))
            p.setPen(QColor(THEME.on_surface_muted))
            p.drawText(QRectF(0.0, gy - 7.0, x0 - 6.0, 14.0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(freq))

        for mv in (700, 800, 900, 1000):
            gx = map_x(mv)
            p.setPen(QPen(QColor(255, 255, 255, 10), 1.0))
            p.drawLine(QPointF(gx, y1), QPointF(gx, y0))
            p.setPen(QColor(THEME.on_surface_muted))
            p.drawText(QRectF(gx - 22.0, y0 + 2.0, 44.0, 16.0), Qt.AlignmentFlag.AlignCenter, f"{mv}mV")

        mvs = [float(mv) for mv in range(650, 1051, 20)]
        cap_eff = float(self._max_clock) if (self._max_clock is not None and self._max_clock < self._ceiling) else 9999.0

        stock_pts = [QPointF(map_x(mv), map_y(stock_mhz(mv))) for mv in mvs]
        tuned_vals = [min(cap_eff, stock_mhz(mv) + self._uv_offset) for mv in mvs]
        tuned_pts = [QPointF(map_x(mv), map_y(val)) for mv, val in zip(mvs, tuned_vals)]

        # Clamp zone
        if cap_eff < self._ceiling:
            cap_y = map_y(cap_eff)
            clamp_idx = next((i for i, mv in enumerate(mvs) if stock_mhz(mv) + self._uv_offset >= cap_eff), -1)
            if clamp_idx >= 0:
                xs = map_x(mvs[clamp_idx])
                p.fillRect(QRectF(xs, y1, x1 - xs, y0 - y1), QColor(16, 185, 129, 20))
                p.setPen(QPen(QColor(THEME.success), 1.3, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x0, cap_y), QPointF(x1, cap_y))
                if cap_y - 14.0 >= y1:
                    p.setPen(QColor(THEME.success))
                    p.drawText(
                        QRectF(x1 - 92.0, cap_y - 14.0, 90.0, 13.0),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"Cap {int(cap_eff)} MHz",
                    )

        # Stock dashed curve
        stock_path = QPainterPath(stock_pts[0])
        for pt in stock_pts[1:]:
            stock_path.lineTo(pt)
        p.setPen(QPen(QColor(THEME.on_surface_disabled), 1.8, Qt.PenStyle.DashLine))
        p.drawPath(stock_path)

        # Tuned glow + curve
        tuned_path = QPainterPath(tuned_pts[0])
        for pt in tuned_pts[1:]:
            tuned_path.lineTo(pt)
        glow_color = QColor(self._accent)
        glow_color.setAlphaF(0.28)
        p.setPen(QPen(glow_color, 5.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(tuned_path)
        p.setPen(QPen(self._accent, 2.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(tuned_path)

        # Top legend
        p.setPen(QColor(THEME.on_surface_variant))
        p.drawText(QPointF(x0, 12.0), "MHz / mV")
        p.setPen(self._accent)
        p.drawText(QPointF(x0 + 72.0, 12.0), f"Tuned ({self._uv_offset:+d} MHz)")


class BatteryTankWidget(QWidget):
    """Horizontal battery charge tank with movable cap line and longevity zone."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pct = 80
        self._cap_pct = 80
        self._topup = False
        self._accent = QColor(THEME.primary)
        self._present = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(48)

    def sizeHint(self) -> QSize:
        return QSize(240, 52)

    def minimumSizeHint(self) -> QSize:
        return QSize(140, 48)

    def set_state(
        self,
        pct: int,
        cap_pct: int,
        topup_active: bool = False,
        accent_hex: str = THEME.primary,
        present: bool = True,
    ) -> None:
        self._pct = max(0, min(100, int(pct)))
        self._cap_pct = max(40, min(100, int(cap_pct)))
        self._topup = topup_active
        self._accent = QColor(accent_hex)
        self._present = present
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        tank_rect = QRectF(2.0, 14.0, max(40.0, w - 10.0), max(22.0, h - 18.0))

        p.setPen(QPen(QColor(THEME.outline_variant), 1.4))
        p.setBrush(QColor(THEME.surface_container_low))
        p.drawRoundedRect(tank_rect, 8.0, 8.0)

        # Battery terminal nub
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(THEME.outline))
        p.drawRoundedRect(QRectF(tank_rect.right() + 1.0, tank_rect.center().y() - 6.0, 4.0, 12.0), 2.0, 2.0)

        if not self._present:
            p.setPen(QColor(THEME.on_surface_muted))
            p.drawText(tank_rect, Qt.AlignmentFlag.AlignCenter, "No Battery")
            return

        inner = tank_rect.adjusted(3.0, 3.0, -3.0, -3.0)
        fill_w = inner.width() * (self._pct / 100.0)
        if fill_w > 0:
            fill_color = QColor(THEME.success if not self._topup else THEME.warning)
            p.setBrush(fill_color)
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), fill_w, inner.height()), 5.0, 5.0)

        # Longevity zone beyond cap
        if self._cap_pct < 100:
            cap_x = inner.left() + inner.width() * (self._cap_pct / 100.0)
            zone = QRectF(cap_x, inner.top(), inner.right() - cap_x, inner.height())
            p.setBrush(QBrush(QColor(THEME.on_surface_muted), Qt.BrushStyle.BDiagPattern))
            p.setOpacity(0.22)
            p.drawRoundedRect(zone, 4.0, 4.0)
            p.setOpacity(1.0)

            p.setPen(QPen(self._accent, 2.0))
            p.drawLine(QPointF(cap_x, tank_rect.top() - 2.0), QPointF(cap_x, tank_rect.bottom() + 2.0))

            badge_font = QFont()
            badge_font.setPointSize(8)
            badge_font.setBold(True)
            p.setFont(badge_font)
            p.setPen(self._accent)
            p.drawText(
                QRectF(max(0.0, cap_x - 24.0), 0.0, 48.0, 13.0),
                Qt.AlignmentFlag.AlignCenter,
                f"Cap {self._cap_pct}%",
            )

        pct_font = QFont()
        pct_font.setPointSizeF(9.5)
        pct_font.setBold(True)
        p.setFont(pct_font)
        if fill_w >= 44.0:
            p.setPen(QColor("#09090B"))
            p.drawText(
                QRectF(inner.left() + 4.0, inner.top(), fill_w - 10.0, inner.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{self._pct}%",
            )
        else:
            p.setPen(QColor("#FAFAFA"))
            p.drawText(
                QRectF(inner.left() + fill_w + 6.0, inner.top(), max(32.0, inner.right() - (inner.left() + fill_w + 6.0)), inner.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{self._pct}%",
            )


class KeyboardDeckWidget(QWidget):
    """Stylized 6-row laptop keyboard deck with WASD highlight and RGB halo."""

    _ROWS = [
        ["ESC", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "DEL"],
        ["~", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=", "BKSP"],
        ["TAB", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "[", "]", "\\"],
        ["CAPS", "A", "S", "D", "F", "G", "H", "J", "K", "L", ";", "'", "ENTER"],
        ["SHIFT", "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/", "SHIFT"],
        ["CTRL", "FN", "WIN", "ALT", "SPACE", "ALT", "CTRL", "<", "^v", ">"],
    ]
    _WIDE = {
        "BKSP": 1.9,
        "TAB": 1.5,
        "\\": 1.4,
        "CAPS": 1.75,
        "ENTER": 2.15,
        "SHIFT": 2.35,
        "SPACE": 5.4,
        "CTRL": 1.35,
    }

    _ASPECT = 3.10

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(THEME.primary)
        self._brightness = 80
        self._enabled = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(176)

    def sizeHint(self) -> QSize:
        return QSize(620, 204)

    def minimumSizeHint(self) -> QSize:
        return QSize(280, 140)

    @staticmethod
    def _display_color(col: QColor) -> QColor:
        """Lift dark saturated hues so they remain vivid on dark #101014 keycaps."""
        if col.lightnessF() < 0.54:
            hue = col.hslHueF()
            sat = max(0.75, col.hslSaturationF()) if hue >= 0 else col.hslSaturationF()
            return QColor.fromHslF(max(0.0, hue) if hue >= 0 else 0.0, sat, 0.56)
        return QColor(col)

    def set_lighting(self, hex_color: str, brightness_pct: int, enabled: bool = True) -> None:
        self._color = QColor(hex_color)
        self._brightness = max(0, min(100, int(brightness_pct)))
        self._enabled = bool(enabled)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = float(self.width()), float(self.height())
        max_w = max(140.0, (h - 4.0) * self._ASPECT)
        deck_w = min(w - 4.0, max_w)
        deck_x = (w - deck_w) * 0.5
        deck_rect = QRectF(deck_x, 2.0, deck_w, h - 4.0)
        disp_col = self._display_color(self._color)

        # Under-key halo
        if self._enabled:
            halo = QRadialGradient(deck_rect.center(), max(deck_w, h) * 0.55)
            c_in = QColor(disp_col)
            c_in.setAlphaF(0.08 + (self._brightness / 100.0) * 0.22)
            halo.setColorAt(0.0, c_in)
            halo.setColorAt(1.0, QColor("#09090C"))
            p.setBrush(QBrush(halo))
        else:
            p.setBrush(QColor("#09090C"))

        border_col = QColor(disp_col) if self._enabled else QColor(THEME.outline_variant)
        if self._enabled:
            border_col.setAlphaF(0.35 + (self._brightness / 100.0) * 0.45)
        p.setPen(QPen(border_col, 1.2))
        p.drawRoundedRect(deck_rect, 9.0, 9.0)

        pad_x, pad_y = 8.0, 8.0
        gap = 3.0
        row_h = max(8.0, (deck_rect.height() - 2 * pad_y - 5 * gap) / 6.0)
        avail_w = max(40.0, deck_rect.width() - 2 * pad_x)

        key_stroke = QColor(disp_col) if self._enabled else QColor(THEME.outline_variant)
        if self._enabled:
            key_stroke.setAlphaF(0.30 + (self._brightness / 100.0) * 0.65)

        legend_font = QFont()
        legend_font.setPointSizeF(7.0)
        legend_font.setBold(True)
        p.setFont(legend_font)
        fm = p.fontMetrics()

        y = deck_rect.top() + pad_y
        for row in self._ROWS:
            total_units = sum(self._WIDE.get(k, 1.0) for k in row)
            unit_w = (avail_w - (len(row) - 1) * gap) / max(1.0, total_units)
            x = deck_rect.left() + pad_x
            for key in row:
                kw = self._WIDE.get(key, 1.0) * unit_w
                wasd = key in ("W", "A", "S", "D")
                key_rect = QRectF(x, y, kw, row_h)
                p.setBrush(QColor("#101014"))
                p.setPen(QPen(disp_col if (wasd and self._enabled) else key_stroke, 1.4 if (wasd and self._enabled) else 0.9))
                p.drawRoundedRect(key_rect, 3.0, 3.0)
                if kw >= 14.0 and row_h >= 12.0:
                    label = {"ENTER": "ENT", "SHIFT": "SHF", "SPACE": "SPC"}.get(key, key)
                    if fm.horizontalAdvance(label) + 4 <= kw:
                        if self._enabled:
                            leg_col = QColor(disp_col) if wasd else QColor(THEME.on_surface_variant)
                            if not wasd:
                                leg_col.setAlphaF(0.75)
                        else:
                            leg_col = QColor("#52525B")
                        p.setPen(leg_col)
                        p.drawText(key_rect, Qt.AlignmentFlag.AlignCenter, label)
                if self._enabled and key == "SPACE":
                    bar_c = QColor(disp_col)
                    bar_c.setAlphaF(max(0.2, self._brightness / 100.0))
                    p.setPen(QPen(bar_c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawLine(QPointF(x + kw * 0.25, y + row_h - 3.5), QPointF(x + kw * 0.75, y + row_h - 3.5))
                x += kw + gap
            y += row_h + gap


class SpinningFanIcon(QWidget):
    """Vector fan icon that rotates smoothly when RPM > 0."""

    def __init__(self, size: int = 16, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = max(12, int(size))
        self._angle = 0.0
        self._rpm: Optional[int] = None
        self._color = QColor(THEME.on_surface_variant)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(self._size, self._size)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def set_rpm(self, rpm: Optional[int], color: Optional[str] = None) -> None:
        self._rpm = rpm
        if color is not None:
            self._color = QColor(color)
        if rpm is not None and rpm > 0 and not _is_offscreen():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        step = 6.0 if not self._rpm else min(24.0, max(4.0, self._rpm / 300.0))
        self._angle = (self._angle + step) % 360.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = min(cx, cy) - 1.5
        p.translate(cx, cy)
        p.rotate(self._angle)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        for i in range(3):
            p.save()
            p.rotate(i * 120.0)
            blade = QPainterPath()
            blade.moveTo(0.0, 0.0)
            blade.quadTo(r * 0.65, -r * 0.55, r * 0.92, 0.0)
            blade.quadTo(r * 0.45, r * 0.35, 0.0, 0.0)
            p.drawPath(blade)
            p.restore()

        p.setBrush(QColor(THEME.surface_container))
        p.drawEllipse(QPointF(0.0, 0.0), max(1.8, r * 0.24), max(1.8, r * 0.24))


class LinkPillButton(QPushButton):
    """Subtle monospace-friendly pill button that emits target page navigation."""

    navigate_requested = pyqtSignal(str)

    def __init__(
        self,
        text: str,
        target_page: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._target_page = target_page
        self.setProperty("class", "LinkPillButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: self.navigate_requested.emit(self._target_page))

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 160), hint.height())


class _GlowHighlightOverlay(QWidget):
    """Non-intrusive translucent glow pulse rendered over a target widget."""

    def __init__(
        self,
        target: QWidget,
        accent_hex: str = THEME.primary,
        duration_ms: int = 1400,
    ) -> None:
        parent = target.parentWidget()
        super().__init__(parent)
        self._target = target
        self._accent = QColor(accent_hex)
        self._intensity = 1.0 if _is_offscreen() else 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._sync_geometry()
        self.raise_()
        self.show()

        self._target.installEventFilter(self)
        self._target.destroyed.connect(self._cleanup)

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(max(200, int(duration_ms)))
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self._cleanup)

        if not _is_offscreen():
            self._anim.start()
        else:
            self.update()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None or self._target is None:
            return
        rect = self._target.geometry().adjusted(-4, -4, 4, 4)
        self.setGeometry(rect.intersected(parent.rect()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._target:
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
                self._sync_geometry()
            elif event.type() == QEvent.Type.Hide:
                self._cleanup()
        return super().eventFilter(watched, event)

    def _on_progress(self, value: object) -> None:
        t = float(value)
        if t < 0.14:
            self._intensity = t / 0.14
        elif t < 0.30:
            self._intensity = 1.0
        else:
            p = (t - 0.30) / 0.70
            self._intensity = max(0.0, (1.0 - p) ** 2.2)
        self.update()

    def stop_and_remove(self) -> None:
        self._anim.stop()
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            self._target.removeEventFilter(self)
        except Exception:
            pass
        if getattr(self._target, "_glow_overlay", None) is self:
            setattr(self._target, "_glow_overlay", None)
        self.hide()
        self.deleteLater()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._intensity <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(3.0, 3.0, max(2.0, self.width() - 6.0), max(2.0, self.height() - 6.0))

        fill = QColor(self._accent)
        fill.setAlphaF(0.16 * self._intensity)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 12.0, 12.0)

        halo = QColor(self._accent)
        halo.setAlphaF(0.32 * self._intensity)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(halo, 5.0))
        p.drawRoundedRect(rect, 12.0, 12.0)

        border = QColor(self._accent)
        border.setAlphaF(0.95 * self._intensity)
        p.setPen(QPen(border, 2.0))
        p.drawRoundedRect(rect, 12.0, 12.0)


def highlight_widget(
    target: QWidget,
    accent_hex: str = THEME.primary,
    duration_ms: int = 1400,
) -> Optional[QWidget]:
    """Briefly pulse an accent glow around ``target`` to guide user focus."""
    if target is None or target.parentWidget() is None or not target.isVisible():
        return None
    prev = getattr(target, "_glow_overlay", None)
    if prev is not None:
        try:
            prev.stop_and_remove()
        except Exception:
            pass
    overlay = _GlowHighlightOverlay(target, accent_hex=accent_hex, duration_ms=duration_ms)
    setattr(target, "_glow_overlay", overlay)
    return overlay
