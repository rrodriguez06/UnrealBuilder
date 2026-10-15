"""Tiny line chart (QtCharts is not installed): values over time with an optional ceiling line."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class Sparkline(QWidget):
    def __init__(self, color: str = "#3d8bfd", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.values: list[float] = []
        self.ceiling: float | None = None
        self.color = QColor(color)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(160, 32)

    def set_values(self, values: list[float], ceiling: float | None = None) -> None:
        self.values = values[-300:]
        self.ceiling = ceiling
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 2
        top = max(max(self.values), self.ceiling or 0) or 1.0
        n = len(self.values)
        path = QPainterPath()
        for i, v in enumerate(self.values):
            pt = QPointF(i * (w - 1) / (n - 1), 1 + h - v / top * h)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(w - 1, h + 1)
        fill.lineTo(0, h + 1)
        c = QColor(self.color)
        c.setAlpha(50)
        p.fillPath(fill, c)
        p.setPen(QPen(self.color, 1.5))
        p.drawPath(path)
        if self.ceiling:
            y = 1 + h - self.ceiling / top * h
            pen = QPen(QColor("#e5484d"), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(0, y), QPointF(w, y))
        p.end()
