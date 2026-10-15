"""Structured progress extracted from UAT / UBT / cook commandlet output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from unrealbuilder.core.logparse.lines import LogLine, Severity

PHASE_RE = re.compile(r"\*{10} (BUILD|COOK|STAGE|PACKAGE|ARCHIVE|DEPLOY|RUN) COMMAND (STARTED|COMPLETED) \*{10}")
CODE_BASED_RE = re.compile(r"is being treated as a code-based project for platforms .* because: (?P<reason>.*?)\.(?: Creating|$)")
TOOLCHAIN_RE = re.compile(r"^Using Visual Studio (?P<ver>[\d.]+) toolchain .* and Windows (?P<sdk>[\d.]+) SDK")
UBT_ACTION_RE = re.compile(r"^\[(?P<n>\d+)/(?P<m>\d+)\] (?P<what>.+)$")
COMMANDLET_LOG_RE = re.compile(r"^Commandlet log file is (?P<path>.+)$")
UBT_LOG_RE = re.compile(r"^Log file: (?P<path>.+)$")
WORKERS_RE = re.compile(r"Using (?P<n>\d+) local workers for shader compilation")
GLOBAL_SHADERS_RE = re.compile(r"Compiling global changed shaders for platform")
SHADERS_LEFT_RE = re.compile(r"Worker \((?P<i>\d+)/(?P<n>\d+)\): shaders left to compile (?P<left>\d+)")
COOKED_RE = re.compile(r"Cooked packages (?P<done>\d+) Packages Remain (?P<remain>\d+) Total (?P<total>\d+)")
COOK_DIAG_RE = re.compile(r"Cook Diagnostics: .*VirtualMemory=(?P<vm>\d+)MiB")
SLOW_SHADER_RE = re.compile(r"Job (?P<job>\S+)\(permutation (?P<perm>\d+), format (?P<fmt>\w+)\) compile time exceeded threshold \((?P<s>[\d.]+)s\)")
TOOL_TIME_RE = re.compile(r"^Took (?P<s>[\d.]+)s to run (?P<tool>\S+), ExitCode=(?P<rc>-?\d+)")
SHADERS_COMPILED_RE = re.compile(r"Shaders Compiled: (?P<n>[\d,]+)")
RESULT_RE = re.compile(r"^BUILD (?P<r>SUCCESSFUL|FAILED)")
EXIT_RE = re.compile(r"AutomationTool exiting with ExitCode=(?P<rc>-?\d+)")
BCR_TIME_RE = re.compile(r"^BuildCookRun time: (?P<s>[\d.]+) s")
UBT_RESULT_RE = re.compile(r"^Result: (?P<r>Succeeded|Failed)")
RUNNING_RE = re.compile(r"^Running: (?P<cmd>.+)$")
CRASH_RE = re.compile(r"(?:Critical error:|Fatal error:|Unhandled Exception|A conflicting instance of AutomationTool)")


@dataclass
class SlowShader:
    job: str
    permutation: int
    format: str
    seconds: float


@dataclass
class UatProgress:
    phases_started: list[str] = field(default_factory=list)
    phases_completed: list[str] = field(default_factory=list)
    current_phase: str | None = None
    code_based_reason: str | None = None
    toolchain: str | None = None
    windows_sdk: str | None = None
    ubt_done: int = 0
    ubt_total: int = 0
    ubt_last_action: str | None = None
    ubt_result: str | None = None
    commandlet_log: str | None = None
    ubt_log: str | None = None
    shader_workers: int | None = None
    global_shaders: bool = False
    shaders_left: int | None = None
    shaders_left_max: int = 0
    shaders_compiled: int = 0
    slow_shaders: list[SlowShader] = field(default_factory=list)
    cook_done: int = 0
    cook_remain: int = 0
    cook_total: int = 0
    cook_virtual_mib: int | None = None
    tool_runs: list[tuple[str, float, int]] = field(default_factory=list)
    running: str | None = None
    result: str | None = None  # SUCCESSFUL / FAILED
    exit_code: int | None = None
    buildcookrun_seconds: float | None = None
    crash: str | None = None
    errors: int = 0
    warnings: int = 0

    @property
    def cook_fraction(self) -> float | None:
        if self.cook_total <= 0:
            return None
        return min(1.0, self.cook_done / self.cook_total)

    @property
    def shader_fraction(self) -> float | None:
        if self.shaders_left is None or self.shaders_left_max <= 0:
            return None
        return max(0.0, min(1.0, 1 - self.shaders_left / self.shaders_left_max))

    @property
    def ubt_fraction(self) -> float | None:
        if self.ubt_total <= 0:
            return None
        return self.ubt_done / self.ubt_total


class UatParser:
    """Feed classified lines (any source: UAT stdout, cook commandlet log, UBT log) to update progress."""

    def __init__(self) -> None:
        self.progress = UatProgress()
        self._shader_left_by_worker: dict[int, int] = {}

    def feed(self, lines: list[LogLine]) -> None:
        for line in lines:
            self._feed_one(line)

    def _feed_one(self, line: LogLine) -> None:  # noqa: C901 - flat dispatch table
        p = self.progress
        t = line.text
        if line.severity == Severity.ERROR:
            p.errors += 1
        elif line.severity == Severity.WARNING:
            p.warnings += 1
        if "COMMAND" in t and (m := PHASE_RE.search(t)):
            phase, state = m.group(1), m.group(2)
            if state == "STARTED":
                p.phases_started.append(phase)
                p.current_phase = phase
            else:
                p.phases_completed.append(phase)
                if p.current_phase == phase:
                    p.current_phase = None
            return
        if t.startswith("[") and (m := UBT_ACTION_RE.match(t)):
            p.ubt_done = int(m.group("n"))
            p.ubt_total = int(m.group("m"))
            p.ubt_last_action = m.group("what")
            return
        if "Cooked packages" in t and (m := COOKED_RE.search(t)):
            done, total = int(m.group("done")), int(m.group("total"))
            p.cook_done = max(p.cook_done, done)
            p.cook_remain = int(m.group("remain"))
            p.cook_total = max(p.cook_total, total)
            p.global_shaders = False
            return
        if "shaders left to compile" in t and (m := SHADERS_LEFT_RE.search(t)):
            self._shader_left_by_worker[int(m.group("i"))] = int(m.group("left"))
            left = max(self._shader_left_by_worker.values())
            p.shaders_left = left
            p.shaders_left_max = max(p.shaders_left_max, left)
            return
        if "compile time exceeded" in t and (m := SLOW_SHADER_RE.search(t)):
            p.slow_shaders.append(SlowShader(m.group("job"), int(m.group("perm")), m.group("fmt"), float(m.group("s"))))
            return
        if t.startswith("Took ") and (m := TOOL_TIME_RE.match(t)):
            p.tool_runs.append((m.group("tool"), float(m.group("s")), int(m.group("rc"))))
            return
        if t.startswith("Running: ") and (m := RUNNING_RE.match(t)):
            p.running = m.group("cmd")
            return
        if "code-based project" in t and (m := CODE_BASED_RE.search(t)):
            p.code_based_reason = m.group("reason")
            return
        if t.startswith("Using Visual Studio") and (m := TOOLCHAIN_RE.match(t)):
            p.toolchain, p.windows_sdk = m.group("ver"), m.group("sdk")
            return
        if t.startswith("Commandlet log file is") and (m := COMMANDLET_LOG_RE.match(t)):
            p.commandlet_log = m.group("path").strip()
            return
        if t.startswith("Log file: ") and (m := UBT_LOG_RE.match(t)):
            p.ubt_log = m.group("path").strip()
            return
        if "local workers" in t and (m := WORKERS_RE.search(t)):
            p.shader_workers = int(m.group("n"))
            return
        if GLOBAL_SHADERS_RE.search(t):
            p.global_shaders = True
            return
        if "Shaders Compiled:" in t and (m := SHADERS_COMPILED_RE.search(t)):
            p.shaders_compiled = max(p.shaders_compiled, int(m.group("n").replace(",", "")))
            return
        if "Cook Diagnostics" in t and (m := COOK_DIAG_RE.search(t)):
            p.cook_virtual_mib = int(m.group("vm"))
            return
        if t.startswith("BUILD ") and (m := RESULT_RE.match(t)):
            p.result = m.group("r")
            return
        if "AutomationTool exiting" in t and (m := EXIT_RE.search(t)):
            p.exit_code = int(m.group("rc"))
            return
        if t.startswith("BuildCookRun time") and (m := BCR_TIME_RE.match(t)):
            p.buildcookrun_seconds = float(m.group("s"))
            return
        if t.startswith("Result: ") and (m := UBT_RESULT_RE.match(t)):
            p.ubt_result = m.group("r")
            return
        if p.crash is None and CRASH_RE.search(t):
            p.crash = t.strip()
