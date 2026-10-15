"""Application style (Fusion, dark palette when the system prefers dark) and shared colors."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from unrealbuilder.core.jobs import JobState
from unrealbuilder.core.logparse.lines import Severity

STATE_COLORS = {
    JobState.QUEUED: "#8a8f98",
    JobState.BLOCKED: "#d4a017",
    JobState.STARTING: "#3d8bfd",
    JobState.RUNNING: "#3d8bfd",
    JobState.CANCEL_REQUESTED: "#d4a017",
    JobState.SUCCEEDED: "#2fb344",
    JobState.FAILED: "#e5484d",
    JobState.CANCELLED: "#8a8f98",
    JobState.OOM_KILLED: "#e5484d",
    JobState.TIMEOUT: "#e5484d",
    JobState.LOST: "#d4a017",
}

SEVERITY_COLORS = {
    Severity.ERROR: QColor("#ff6b6b"),
    Severity.WARNING: QColor("#f0b429"),
    Severity.NOISE: QColor("#6b7280"),
    Severity.VERBOSE: QColor("#8a8f98"),
}


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    if not is_dark():
        return
    pal = QPalette()
    base, alt, text = QColor("#1e1f24"), QColor("#26272d"), QColor("#e6e6e6")
    pal.setColor(QPalette.ColorRole.Window, QColor("#232429"))
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, QColor("#2c2d33"))
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#3d8bfd"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a8f98"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6b7280"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6b7280"))
    app.setPalette(pal)
