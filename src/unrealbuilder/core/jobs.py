"""Job model and on-disk layout of a build directory.

$T/builds/<Project>/<YYYYMMDD-HHMMSS>-<Config>/
  job.json      frozen JobSpec (+ settings snapshot)
  status.json   live status written by the runner
  result.json   final result (runner on normal exit, finalize in every case)
  summary.json  parsed summary (issues, durations, memory peak…)
  commands.sh   reproducible commands
  cancel.requested   marker written before stopping the unit
  logs/         runner.log, uat-build.log, uat-cook.log, uat-build/, uat-cook/, Cook-*.txt, smoke.log
  <P>-Win64-<Config>/   archive
  <P>-Win64-<Config>-<stamp>.zip
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from unrealbuilder.core.fsutil import atomic_write_json, read_json


class JobState(StrEnum):
    QUEUED = "queued"
    BLOCKED = "blocked"
    STARTING = "starting"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel-requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OOM_KILLED = "oom-killed"
    TIMEOUT = "timeout"
    LOST = "lost"

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATES

    @property
    def label(self) -> str:
        return STATE_LABELS[self]


FINAL_STATES = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.OOM_KILLED, JobState.TIMEOUT, JobState.LOST}

STATE_LABELS = {
    JobState.QUEUED: "Queued",
    JobState.BLOCKED: "Blocked",
    JobState.STARTING: "Starting",
    JobState.RUNNING: "Running",
    JobState.CANCEL_REQUESTED: "Cancelling…",
    JobState.SUCCEEDED: "Succeeded",
    JobState.FAILED: "Failed",
    JobState.CANCELLED: "Cancelled",
    JobState.OOM_KILLED: "Killed (memory)",
    JobState.TIMEOUT: "Timed out",
    JobState.LOST: "Lost",
}

STEPS = ["preflight", "sync", "clean", "build", "cook", "post", "smoke", "zip", "summary"]
STEP_LABELS = {
    "preflight": "Preflight checks",
    "sync": "Mirror sync",
    "clean": "Clean",
    "build": "Compile",
    "cook": "Cook / stage / pak",
    "post": "Finalize",
    "smoke": "Startup test",
    "zip": "Zip archive",
    "summary": "Summary",
}


@dataclass
class JobSpec:
    job_id: str
    project: str
    project_dir: str
    uproject: str
    configuration: str = "Development"
    clean: bool = False
    incremental_cook: bool = False
    zip: bool = True
    zip_exclude_pdb: bool = True
    smoke_test: bool = True
    smoke_timeout_s: int = 120
    nodebuginfo: bool = False
    shader_workers: int = 8
    memory_max_gib: int = 20
    swap_max_gib: int = 2
    zen_port: int = 8559
    extra_uat_args: list[str] = field(default_factory=list)
    prefix: str = ""
    allow_cl_mismatch: bool = False
    delete_staged_after_archive: bool = True
    created_at: float = field(default_factory=time.time)
    build_dir: str = ""

    @staticmethod
    def make_id(project: str, configuration: str, when: float | None = None) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
        return f"{stamp}-{project}-{configuration}"

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> JobSpec:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    @property
    def archive_name(self) -> str:
        return f"{self.project}-Win64-{self.configuration}"


@dataclass
class BuildPaths:
    root: Path

    @property
    def job(self) -> Path:
        return self.root / "job.json"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def result(self) -> Path:
        return self.root / "result.json"

    @property
    def summary(self) -> Path:
        return self.root / "summary.json"

    @property
    def commands(self) -> Path:
        return self.root / "commands.sh"

    @property
    def cancel_marker(self) -> Path:
        return self.root / "cancel.requested"

    @property
    def pinned_marker(self) -> Path:
        return self.root / "pinned"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def log(self, name: str) -> Path:
        return self.logs / name

    def archive(self, spec: JobSpec) -> Path:
        return self.root / spec.archive_name

    def zips(self) -> list[Path]:
        return sorted(self.root.glob("*.zip"))

    def load_spec(self) -> JobSpec:
        return JobSpec.from_json(read_json(self.job))

    def read_status(self) -> dict[str, Any]:
        return read_json(self.status, default={}) or {}

    def read_result(self) -> dict[str, Any] | None:
        return read_json(self.result, default=None)

    def write_status(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.status, data)

    def write_result(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.result, data)


def create_build_dir(builds_root: Path, spec: JobSpec) -> BuildPaths:
    stamp = spec.job_id.split(f"-{spec.project}-")[0]
    root = builds_root / spec.project / f"{stamp}-{spec.configuration}"
    n = 1
    while root.exists():
        n += 1
        root = builds_root / spec.project / f"{stamp}-{spec.configuration}-{n}"
    paths = BuildPaths(root)
    paths.logs.mkdir(parents=True)
    spec.build_dir = str(root)
    atomic_write_json(paths.job, spec.to_json())
    return paths


def iter_build_dirs(builds_root: Path) -> list[BuildPaths]:
    if not builds_root.is_dir():
        return []
    found = [BuildPaths(p.parent) for p in builds_root.glob("*/*/job.json")]
    return sorted(found, key=lambda b: b.root.name, reverse=True)
