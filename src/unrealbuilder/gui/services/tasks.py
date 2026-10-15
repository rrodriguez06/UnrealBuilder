"""Run blocking functions in the global QThreadPool and deliver results on the GUI thread."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: TaskSignals) -> None:
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception:  # noqa: BLE001 - surfaced to the UI
            self._emit(self.signals.failed, traceback.format_exc())
            return
        self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value) -> None:
        # The receiver may be gone if the application quit while the task was running.
        try:
            signal.emit(value)
        except RuntimeError:
            pass


_alive: set[TaskSignals] = set()


def run_in_pool(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None, on_error: Callable[[str], None] | None = None) -> TaskSignals:
    signals = TaskSignals()
    _alive.add(signals)

    def _finish(*_args) -> None:
        _alive.discard(signals)

    if on_done:
        signals.done.connect(on_done)
    if on_error:
        signals.failed.connect(on_error)
    signals.done.connect(_finish)
    signals.failed.connect(_finish)
    QThreadPool.globalInstance().start(_Task(fn, signals))
    return signals
