"""Inventory of jobs: queued specs + build directories, reconciled with active systemd units.

This is how the GUI reattaches after a restart: the truth lives in build dirs and systemd, not in memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.jobs import BuildPaths, JobSpec, JobState, iter_build_dirs
from unrealbuilder.core.paths import unit_name
from unrealbuilder.core.queue import BuildQueue

STARTING_GRACE_S = 20


@dataclass
class JobEntry:
    job_id: str
    spec: JobSpec
    paths: BuildPaths | None
    state: JobState
    unit: str
    result: dict | None
    status: dict

    @property
    def is_running(self) -> bool:
        return self.state in (JobState.STARTING, JobState.RUNNING, JobState.CANCEL_REQUESTED)

    @property
    def started_at(self) -> float | None:
        return self.status.get("started_at") or (self.spec.created_at if self.paths else None)

    @property
    def finished_at(self) -> float | None:
        if self.result:
            return self.result.get("finalized_at") or self.result.get("finished_at")
        return None

    @property
    def duration(self) -> float | None:
        start = self.started_at
        if start is None:
            return None
        end = self.finished_at or (time.time() if self.is_running else None)
        return None if end is None else max(0.0, end - start)


def build_unit(spec: JobSpec) -> str:
    return unit_name("build", spec.job_id)


def entry_for_build(paths: BuildPaths, active_units: set[str], now: float | None = None) -> JobEntry | None:
    try:
        spec = paths.load_spec()
    except (OSError, ValueError, TypeError):
        return None
    now = now or time.time()
    unit = build_unit(spec)
    result = paths.read_result()
    status = paths.read_status()
    active = unit in active_units
    if result and (result.get("finalized_at") or not active):
        try:
            state = JobState(result.get("state", "failed"))
        except ValueError:
            state = JobState.FAILED
    elif active:
        state = JobState.CANCEL_REQUESTED if paths.cancel_marker.exists() else JobState.RUNNING
    elif not status and now - spec.created_at < STARTING_GRACE_S:
        state = JobState.STARTING
    else:
        state = JobState.LOST
    return JobEntry(spec.job_id, spec, paths, state, unit, result, status)


def scan_jobs(builds_root: Path, queue: BuildQueue, active_units: set[str], limit: int = 300) -> list[JobEntry]:
    entries = [
        JobEntry(s.job_id, s, None, JobState.QUEUED, build_unit(s), None, {}) for s in queue.items
    ]
    now = time.time()
    for paths in iter_build_dirs(builds_root)[:limit]:
        entry = entry_for_build(paths, active_units, now)
        if entry is not None:
            entries.append(entry)
    return entries
