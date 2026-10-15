"""Parse a finished (or running) build directory's logs into a summary."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core.jobs import BuildPaths
from unrealbuilder.core.logparse.issues import GENERIC_FAILURE, IssueCollector
from unrealbuilder.core.logparse.lines import LineSplitter, LogLine, make_line
from unrealbuilder.core.logparse.uat import UatParser, UatProgress

LOG_SOURCES = [
    ("uat-build", "uat-build.log"),
    ("uat-cook", "uat-cook.log"),
    ("smoke", "smoke.log"),
]


def read_lines(path: Path) -> list[LogLine]:
    splitter = LineSplitter()
    try:
        data = path.read_bytes()
    except OSError:
        return []
    raws = splitter.feed(data) + splitter.flush()
    return [make_line(i, raw) for i, raw in enumerate(raws, 1)]


def find_cook_logs(paths: BuildPaths) -> list[Path]:
    """Cook commandlet logs end up in the UAT log folder of the cook step (uebp_LogFolder)."""
    return sorted((paths.logs / "uat-cook").glob("Cook-*.txt"))


@dataclass
class BuildSummary:
    progress: UatProgress
    errors: int
    warnings: int
    issues: list[dict] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        p = dataclasses.asdict(self.progress)
        p["slow_shaders"] = p["slow_shaders"][:50]
        return {"progress": p, "errors": self.errors, "warnings": self.warnings, "issues": self.issues, "sources": self.sources}


def summarize(paths: BuildPaths, max_issues: int = 200) -> BuildSummary:
    parser = UatParser()
    issues = IssueCollector()
    sources: dict[str, str] = {}
    files = [(name, paths.log(fname)) for name, fname in LOG_SOURCES]
    files += [("cook", p) for p in find_cook_logs(paths)]
    for name, path in files:
        if not path.exists():
            continue
        lines = read_lines(path)
        sources[name] = str(path)
        if name != "smoke":
            parser.feed(lines)
        issues.add(name, lines)
    groups = [
        {
            "severity": int(g.severity),
            "category": g.category,
            "key": g.key,
            "sample": g.sample,
            "count": g.count,
            "occurrences": g.occurrences[:20],
        }
        for g in issues.sorted_groups()[:max_issues]
    ]
    return BuildSummary(parser.progress, issues.error_count, issues.warning_count, groups, sources)


def first_error(path: Path) -> str | None:
    """Most explanatory error line of a log: the first specific error, else the first generic one."""
    from unrealbuilder.core.logparse.lines import Severity

    generic = None
    for line in read_lines(path):
        if line.severity != Severity.ERROR:
            continue
        text = line.text.strip()
        if GENERIC_FAILURE.match(text):
            generic = generic or text
            continue
        return text[:500]
    return generic[:500] if generic else None
