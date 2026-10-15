"""ShaderCompileWorker sampling: activity, stall detection and d3dcompiler sanity check.

Diagnosis used during the POC: a hung worker keeps the same WorkerInputOnly.in for a long time while its
minor page faults stop growing, even though it burns 100 % CPU; healthy workers keep faulting.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout

CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
STALL_AFTER_S = 120
LONG_AFTER_S = 600


@dataclass
class WorkerInfo:
    pid: int
    index: str  # working dir index
    rss: int
    cpu_percent: float
    minflt_rate: float  # faults per second
    input_age_s: float | None
    state: str  # active | idle | long | stalled
    d3dcompiler: str | None  # native | builtin | None (not loaded yet)


def _read_stat(pid: int) -> tuple[int, int] | None:
    """Return (cpu_ticks, minflt) from /proc/<pid>/stat."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = text[text.rfind(")") + 2 :].split()
    # rest[0] is field 3 (state); minflt is field 10, utime 14, stime 15.
    try:
        return int(rest[11]) + int(rest[12]), int(rest[7])
    except (IndexError, ValueError):
        return None


def _rss(pid: int) -> int:
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * PAGE
    except (OSError, IndexError, ValueError):
        return 0


def _cmdline(pid: int) -> list[str]:
    try:
        return [a for a in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        return []


def d3dcompiler_kind(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "d3dcompiler_47" in line.lower():
                    if "ThirdParty/Windows/DirectX" in line:
                        return "native"
                    return "builtin"
    except OSError:
        return None
    return None


class ShaderWorkerSampler:
    def __init__(self, layout: ToolchainLayout) -> None:
        self.layout = layout
        self._prev: dict[int, tuple[float, int, int]] = {}
        self._d3d: dict[int, str] = {}

    def sample(self, pids: list[int], now: float | None = None) -> list[WorkerInfo]:
        now = now or time.monotonic()
        wall = time.time()
        infos: list[WorkerInfo] = []
        alive: set[int] = set()
        for pid in pids:
            argv = _cmdline(pid)
            if not argv or "ShaderCompileWorker" not in argv[0]:
                continue
            alive.add(pid)
            st = _read_stat(pid)
            if st is None:
                continue
            ticks, minflt = st
            prev = self._prev.get(pid)
            self._prev[pid] = (now, ticks, minflt)
            cpu = rate = 0.0
            if prev and now > prev[0]:
                dt = now - prev[0]
                cpu = (ticks - prev[1]) / CLK_TCK / dt * 100
                rate = (minflt - prev[2]) / dt
            workdir = argv[1] if len(argv) > 1 else ""
            index = workdir.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] if workdir else "?"
            age = None
            if workdir:
                inp = self.layout.from_win(workdir) / "WorkerInputOnly.in"
                try:
                    age = wall - inp.stat().st_mtime
                except OSError:
                    age = None
            if pid not in self._d3d:
                kind = d3dcompiler_kind(pid)
                if kind:
                    self._d3d[pid] = kind
            infos.append(WorkerInfo(pid, index, _rss(pid), cpu, rate, age, "active", self._d3d.get(pid)))
        for pid in list(self._prev):
            if pid not in alive:
                self._prev.pop(pid, None)
                self._d3d.pop(pid, None)
        classify(infos)
        return infos


def classify(infos: list[WorkerInfo]) -> None:
    others_fault = any(w.minflt_rate > 50 for w in infos)
    for w in infos:
        if w.cpu_percent < 5:
            w.state = "idle"
        elif w.input_age_s is not None and w.input_age_s > STALL_AFTER_S and w.minflt_rate < 1 and others_fault:
            w.state = "stalled"
        elif w.input_age_s is not None and w.input_age_s > LONG_AFTER_S:
            w.state = "long"
        else:
            w.state = "active"
