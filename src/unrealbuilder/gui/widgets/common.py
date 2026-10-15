"""Small shared helpers and widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from unrealbuilder.core.jobs import JobState
from unrealbuilder.gui.theme import STATE_COLORS


def open_path(path: Path | str | None, parent: QWidget | None = None) -> None:
    if not path:
        return
    p = Path(path)
    if not p.exists():
        QMessageBox.warning(parent, "Not found", f"This path does not exist:\n{p}")
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


def human_bytes(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit in ("B", "KiB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {sec:02d} s"
    return f"{sec} s"


def mono_font() -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(max(8.5, f.pointSizeF() * 0.95))
    return f


class StateBadge(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_state(JobState.QUEUED)

    def set_state(self, state: JobState) -> None:
        color = STATE_COLORS.get(state, "#8a8f98")
        self.setText(state.label)
        self.setStyleSheet(
            f"QLabel {{ background: {color}; color: white; border-radius: 8px; padding: 2px 8px; font-weight: 600; }}"
        )
