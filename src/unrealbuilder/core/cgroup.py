"""Read cgroup v2 statistics for a unit (memory, swap, pressure, OOM events, CPU)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


def cgroup_dir(control_group: str) -> Path | None:
    if not control_group:
        return None
    path = CGROUP_ROOT / control_group.lstrip("/")
    return path if path.is_dir() else None


def self_cgroup() -> Path | None:
    try:
        text = Path("/proc/self/cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return cgroup_dir(line[3:])
    return None


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    if value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _read_kv(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition(" ")
            if v.strip().lstrip("-").isdigit():
                out[k] = int(v)
    except OSError:
        pass
    return out


def _read_pressure(path: Path) -> dict[str, float]:
    """memory.pressure → {'some_avg10': x, 'full_avg10': y, 'some_avg60': …}"""
    out: dict[str, float] = {}
    try:
        for line in path.read_text().splitlines():
            kind, *fields = line.split()
            for f in fields:
                k, _, v = f.partition("=")
                if k.startswith("avg"):
                    out[f"{kind}_{k}"] = float(v)
    except OSError:
        pass
    return out


@dataclass
class CgroupStats:
    memory_current: int | None = None
    memory_peak: int | None = None
    memory_max: int | None = None
    swap_current: int | None = None
    anon: int | None = None
    file: int | None = None
    oom_kills: int = 0
    pressure_some_avg10: float = 0.0
    pressure_full_avg10: float = 0.0
    cpu_usage_usec: int | None = None
    nprocs: int = 0


def read_stats(path: Path) -> CgroupStats:
    stat = _read_kv(path / "memory.stat")
    events = _read_kv(path / "memory.events")
    pressure = _read_pressure(path / "memory.pressure")
    cpu = _read_kv(path / "cpu.stat")
    try:
        nprocs = len([x for x in (path / "cgroup.procs").read_text().split() if x])
    except OSError:
        nprocs = 0
    return CgroupStats(
        memory_current=_read_int(path / "memory.current"),
        memory_peak=_read_int(path / "memory.peak"),
        memory_max=_read_int(path / "memory.max"),
        swap_current=_read_int(path / "memory.swap.current"),
        anon=stat.get("anon"),
        file=stat.get("file"),
        oom_kills=events.get("oom_kill", 0),
        pressure_some_avg10=pressure.get("some_avg10", 0.0),
        pressure_full_avg10=pressure.get("full_avg10", 0.0),
        cpu_usage_usec=cpu.get("usage_usec"),
        nprocs=nprocs,
    )


def pids(path: Path) -> list[int]:
    try:
        return [int(x) for x in (path / "cgroup.procs").read_text().split()]
    except (OSError, ValueError):
        return []
