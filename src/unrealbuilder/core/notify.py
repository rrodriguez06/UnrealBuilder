"""Desktop notifications (notify-send) and the GUI liveness lock.

When the GUI is running it owns the notifications; otherwise the runner's finalize step sends them.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

from unrealbuilder.core import xdg

APP_NAME = "UnrealBuilder"


def gui_lock_path() -> Path:
    return xdg.runtime_dir() / "gui.lock"


class GuiLock:
    """Held by the GUI for its whole lifetime (flock is released automatically on exit/crash)."""

    def __init__(self) -> None:
        self._fd: int | None = None

    def acquire(self) -> bool:
        path = gui_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def gui_alive() -> bool:
    path = gui_lock_path()
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def notify_argv(summary: str, body: str = "", urgency: str = "normal", icon: str = "unrealbuilder") -> list[str]:
    return ["notify-send", "-a", APP_NAME, "-u", urgency, "-i", icon, summary, body]


def notify(summary: str, body: str = "", urgency: str = "normal") -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.run(notify_argv(summary, body, urgency), timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
