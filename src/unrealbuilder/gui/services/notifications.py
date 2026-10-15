"""Desktop notifications sent by the GUI (the runner's finalize sends them when the GUI is closed)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess

from unrealbuilder.core.jobs import JobState
from unrealbuilder.core.notify import notify_argv
from unrealbuilder.core.settings import Settings


def notify(summary: str, body: str = "", critical: bool = False) -> None:
    argv = notify_argv(summary, body, "critical" if critical else "normal")
    QProcess.startDetached(argv[0], argv[1:])


def notify_job_finished(settings: Settings, entry) -> None:
    n = settings.notifications
    ok = entry.state == JobState.SUCCEEDED
    if (ok and not n.on_success) or (not ok and not n.on_failure):
        return
    result = entry.result or {}
    body = f"{entry.spec.project} · {entry.spec.configuration}\n{result.get('message', '')}"
    if result.get("zip"):
        body += f"\n{Path(result['zip']).name}"
    notify(f"UnrealBuilder — {entry.state.label}", body, critical=not ok)
