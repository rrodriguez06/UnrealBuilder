"""MonitorWorker: lives in its own QThread, tails logs and samples cgroup stats of running jobs."""

from __future__ import annotations

import copy
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from unrealbuilder.core.cgroup import CgroupStats, cgroup_dir, pids, read_stats
from unrealbuilder.core.jobs import BuildPaths, JobState
from unrealbuilder.core.logparse.issues import IssueCollector
from unrealbuilder.core.logparse.uat import UatParser, UatProgress
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.progress import overall
from unrealbuilder.core.tail import FileTailer
from unrealbuilder.core.workers import ShaderWorkerSampler, WorkerInfo

SOURCES = ("runner", "uat-build", "uat-cook", "cook", "smoke")
SOURCE_LABELS = {
    "runner": "Runner",
    "uat-build": "UAT — compile",
    "uat-cook": "UAT — cook / stage",
    "cook": "Cook commandlet",
    "smoke": "Startup test",
}


@dataclass
class JobSnapshot:
    job_id: str
    state: str
    step: str | None
    step_label: str
    fraction: float
    detail: str
    message: str
    errors: int
    warnings: int
    elapsed: float
    mem_current: int | None = None
    mem_peak: int | None = None
    mem_max: int | None = None
    swap: int | None = None
    pressure_full: float = 0.0
    cpu_percent: float | None = None
    nprocs: int = 0
    workers: list[WorkerInfo] = field(default_factory=list)
    mem_history: list[float] = field(default_factory=list)
    progress: UatProgress | None = None
    steps: dict = field(default_factory=dict)


def unit_cgroup(unit: str) -> Path | None:
    uid = os.getuid()
    return cgroup_dir(f"/user.slice/user-{uid}.slice/user@{uid}.service/unrealbuilder.slice/{unit}")


class JobWatch:
    def __init__(self, job_id: str, paths: BuildPaths, unit: str, layout: ToolchainLayout) -> None:
        self.job_id = job_id
        self.paths = paths
        self.unit = unit
        self.layout = layout
        self.tailers = {
            "runner": FileTailer(paths.log("runner.log")),
            "uat-build": FileTailer(paths.log("uat-build.log")),
            "uat-cook": FileTailer(paths.log("uat-cook.log")),
            "smoke": FileTailer(paths.log("smoke.log")),
        }
        self.parser = UatParser()
        self.issues = IssueCollector()
        self.cook_tailer: FileTailer | None = None
        self.cgroup: Path | None = None
        self.last_cpu: tuple[int, float] | None = None
        self.mem_history: deque[float] = deque(maxlen=300)
        self.workers = ShaderWorkerSampler(layout)
        self.final = False


class MonitorWorker(QObject):
    snapshot = Signal(object)  # JobSnapshot
    lines = Signal(str, str, list)  # job_id, source, list[LogLine]
    reset = Signal(str, str)  # job_id, source (file was replaced/truncated)

    def __init__(self, layout: ToolchainLayout, interval_ms: int = 1000) -> None:
        super().__init__()
        self.layout = layout
        self.interval_ms = interval_ms
        self.watches: dict[str, JobWatch] = {}
        self.timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(self.interval_ms)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    @Slot(str, str, str)
    def watch(self, job_id: str, build_dir: str, unit: str) -> None:
        if job_id not in self.watches:
            self.watches[job_id] = JobWatch(job_id, BuildPaths(Path(build_dir)), unit, self.layout)
            self._poll(self.watches[job_id])

    @Slot(str)
    def unwatch(self, job_id: str) -> None:
        self.watches.pop(job_id, None)

    @Slot()
    def tick(self) -> None:
        for w in list(self.watches.values()):
            try:
                self._poll(w)
            except Exception:  # noqa: BLE001 - a broken job must not stop monitoring of others
                pass
            if w.final:
                self.watches.pop(w.job_id, None)

    def _read(self, w: JobWatch, source: str, tailer: FileTailer) -> None:
        # Several reads per tick allow a fast catch-up after a reattach.
        for _ in range(4):
            new, was_reset = tailer.read_new()
            if was_reset:
                self.reset.emit(w.job_id, source)
            if not new:
                return
            if source not in ("runner", "smoke"):
                w.parser.feed(new)
            w.issues.add(source, new)
            self.lines.emit(w.job_id, source, new)
            if tailer.caught_up:
                return

    def _poll(self, w: JobWatch) -> None:
        for source, tailer in w.tailers.items():
            self._read(w, source, tailer)
        p = w.parser.progress
        if w.cook_tailer is None and p.commandlet_log:
            w.cook_tailer = FileTailer(self.layout.from_win(p.commandlet_log))
        if w.cook_tailer is not None:
            if not w.cook_tailer.path.exists():
                moved = sorted((w.paths.logs / "uat-cook").glob(w.cook_tailer.path.name))
                if moved:
                    offset = w.cook_tailer.offset
                    w.cook_tailer = FileTailer(moved[0])
                    w.cook_tailer.offset = offset
                    w.cook_tailer.inode = os.stat(moved[0]).st_ino
            self._read(w, "cook", w.cook_tailer)

        status = w.paths.read_status()
        result = w.paths.read_result()
        state = (result or {}).get("state") if result and result.get("finalized_at") else status.get("state", JobState.RUNNING.value)
        if result and result.get("finalized_at"):
            w.final = True

        stats: CgroupStats | None = None
        if not w.final:
            if w.cgroup is None or not w.cgroup.exists():
                w.cgroup = unit_cgroup(w.unit)
            if w.cgroup is not None:
                stats = read_stats(w.cgroup)
        cpu_pct = None
        if stats and stats.cpu_usage_usec is not None:
            now = time.monotonic()
            if w.last_cpu:
                du = stats.cpu_usage_usec - w.last_cpu[0]
                dt = now - w.last_cpu[1]
                if dt > 0:
                    cpu_pct = du / 1e6 / dt * 100
            w.last_cpu = (stats.cpu_usage_usec, now)
        if stats and stats.memory_current is not None:
            w.mem_history.append(stats.memory_current / 2**30)
        workers = w.workers.sample(pids(w.cgroup)) if (w.cgroup is not None and not w.final) else []

        steps = status.get("steps", {})
        o = overall(steps, status.get("step"), p)
        started = status.get("started_at") or time.time()
        end = (result or {}).get("finished_at") if w.final else None
        snap = JobSnapshot(
            job_id=w.job_id,
            state=state or JobState.RUNNING.value,
            step=status.get("step"),
            step_label=o.step_label,
            fraction=1.0 if (w.final and state == JobState.SUCCEEDED.value) else o.fraction,
            detail=o.detail,
            message=(result or {}).get("message") if w.final else status.get("message", ""),
            errors=w.issues.error_count,
            warnings=w.issues.warning_count,
            elapsed=(end or time.time()) - started,
            mem_current=stats.memory_current if stats else None,
            mem_peak=stats.memory_peak if stats else (result or {}).get("memory_peak"),
            mem_max=stats.memory_max if stats else None,
            swap=stats.swap_current if stats else None,
            pressure_full=stats.pressure_full_avg10 if stats else 0.0,
            cpu_percent=cpu_pct,
            nprocs=stats.nprocs if stats else 0,
            workers=workers,
            mem_history=list(w.mem_history),
            progress=copy.deepcopy(p),
            steps=steps,
        )
        self.snapshot.emit(snap)


class Monitor(QObject):
    """Owns the worker thread; GUI-side facade."""

    snapshot = Signal(object)
    lines = Signal(str, str, list)
    reset = Signal(str, str)
    _watch = Signal(str, str, str)
    _unwatch = Signal(str)

    def __init__(self, layout: ToolchainLayout, interval_ms: int = 1000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.thread = QThread(self)
        self.worker = MonitorWorker(layout, interval_ms)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.worker.snapshot.connect(self.snapshot)
        self.worker.lines.connect(self.lines)
        self.worker.reset.connect(self.reset)
        self._watch.connect(self.worker.watch)
        self._unwatch.connect(self.worker.unwatch)
        self.watched: set[str] = set()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.thread.quit()
        self.thread.wait(3000)

    def watch(self, job_id: str, build_dir: Path, unit: str) -> None:
        if job_id in self.watched:
            return
        self.watched.add(job_id)
        self._watch.emit(job_id, str(build_dir), unit)

    def unwatch(self, job_id: str) -> None:
        self.watched.discard(job_id)
        self._unwatch.emit(job_id)
