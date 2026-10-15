"""Segmented bar of runner steps, proportional to their durations."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from unrealbuilder.core.jobs import STEP_LABELS, STEPS

COLORS = {
    "done": "#2fb344",
    "running": "#3d8bfd",
    "failed": "#e5484d",
    "warning": "#f0b429",
}


class PhaseTimeline(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.steps: dict = {}
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(600, 30)

    def set_steps(self, steps: dict) -> None:
        self.steps = steps or {}
        segs = self._segments()
        self.setToolTip("\n".join(f"{STEP_LABELS[n]}: {d:.0f} s" for n, d, _s in segs))
        self.update()

    def _segments(self) -> list[tuple[str, float, str]]:
        now = time.time()
        segs = []
        for name in STEPS:
            s = self.steps.get(name, {})
            state = s.get("state", "pending")
            if state in ("pending", "skipped") or s.get("started") is None:
                continue
            end = s.get("ended") or now
            segs.append((name, max(0.5, end - s["started"]), state))
        return segs

    def paintEvent(self, _event) -> None:  # noqa: N802
        segs = self._segments()
        total = sum(d for _n, d, _s in segs)
        if not total:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = 0.0
        w = self.width()
        h = self.height() - 4
        for name, dur, state in segs:
            width = max(2.0, dur / total * w)
            rect = QRectF(x, 2, width - 2, h)
            p.setBrush(QColor(COLORS.get(state, "#8a8f98")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, 4, 4)
            label = STEP_LABELS[name]
            if p.fontMetrics().horizontalAdvance(label) < width - 8:
                p.setPen(QColor("white"))
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            x += width
        p.end()
