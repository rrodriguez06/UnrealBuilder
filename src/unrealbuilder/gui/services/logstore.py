"""Per-job log lines and issue groups, fed live by the monitor or loaded from disk for finished jobs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from unrealbuilder.core.jobs import BuildPaths
from unrealbuilder.core.logparse.issues import IssueCollector
from unrealbuilder.core.logparse.lines import LogLine
from unrealbuilder.core.summary import find_cook_logs, read_lines
from unrealbuilder.gui.services.tasks import run_in_pool

FILES = {"runner": "runner.log", "uat-build": "uat-build.log", "uat-cook": "uat-cook.log", "smoke": "smoke.log"}


class JobLogs:
    def __init__(self) -> None:
        self.sources: dict[str, list[LogLine]] = {}
        self.issues = IssueCollector()


def load_job_logs(paths: BuildPaths) -> JobLogs:
    logs = JobLogs()
    for source, name in FILES.items():
        lines = read_lines(paths.log(name))
        logs.sources[source] = lines
        logs.issues.add(source, lines)
    cook = find_cook_logs(paths)
    if cook:
        lines = read_lines(cook[-1])
        logs.sources["cook"] = lines
        logs.issues.add("cook", lines)
    return logs


class LogStore(QObject):
    appended = Signal(str, str, list)  # job_id, source, lines
    replaced = Signal(str)  # job_id: whole content (re)loaded

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.jobs: dict[str, JobLogs] = {}
        self._loading: set[str] = set()

    def get(self, job_id: str) -> JobLogs:
        return self.jobs.setdefault(job_id, JobLogs())

    def append(self, job_id: str, source: str, lines: list[LogLine]) -> None:
        logs = self.get(job_id)
        logs.sources.setdefault(source, []).extend(lines)
        logs.issues.add(source, lines)
        self.appended.emit(job_id, source, lines)

    def reset_source(self, job_id: str, source: str) -> None:
        self.get(job_id).sources[source] = []
        self.replaced.emit(job_id)

    def ensure_loaded(self, job_id: str, build_dir: Path) -> None:
        """For finished jobs (not watched by the monitor): load logs from disk in the thread pool."""
        if job_id in self.jobs or job_id in self._loading:
            return
        self._loading.add(job_id)

        def done(logs: JobLogs) -> None:
            self._loading.discard(job_id)
            self.jobs[job_id] = logs
            self.replaced.emit(job_id)

        run_in_pool(lambda: load_job_logs(BuildPaths(build_dir)), done, lambda _err: self._loading.discard(job_id))

    def forget(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
