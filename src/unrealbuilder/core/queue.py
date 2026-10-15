"""Persistent build queue (jobs not started yet) and scheduling decisions (pure, no Qt)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core import xdg
from unrealbuilder.core.fsutil import atomic_write_json, read_json
from unrealbuilder.core.jobs import JobSpec


def queue_path() -> Path:
    return xdg.state_dir() / "queue.json"


@dataclass
class BuildQueue:
    items: list[JobSpec] = field(default_factory=list)
    paused: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> BuildQueue:
        data = read_json(path or queue_path(), default=None) or {}
        return cls(items=[JobSpec.from_json(x) for x in data.get("items", [])], paused=bool(data.get("paused", False)))

    def save(self, path: Path | None = None) -> None:
        atomic_write_json(path or queue_path(), {"paused": self.paused, "items": [s.to_json() for s in self.items]})

    def add(self, spec: JobSpec) -> None:
        self.items.append(spec)

    def remove(self, job_id: str) -> JobSpec | None:
        for i, s in enumerate(self.items):
            if s.job_id == job_id:
                return self.items.pop(i)
        return None

    def move(self, job_id: str, delta: int) -> bool:
        for i, s in enumerate(self.items):
            if s.job_id == job_id:
                j = max(0, min(len(self.items) - 1, i + delta))
                if j == i:
                    return False
                self.items.insert(j, self.items.pop(i))
                return True
        return False


def next_to_start(queue: BuildQueue, running_projects: list[str], max_concurrent: int) -> JobSpec | None:
    """First queued job that may start now: respects pause, concurrency and one job per project."""
    if queue.paused or len(running_projects) >= max_concurrent:
        return None
    busy = {p.lower() for p in running_projects}
    for spec in queue.items:
        if spec.project.lower() not in busy:
            return spec
    return None
