"""Group warnings and errors by (severity, category, normalized message)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from unrealbuilder.core.logparse.lines import LogLine, Severity

_NORMALIZERS = [
    (re.compile(r"/(?:Game|Engine|Script)/[\w/.\-]+"), "<asset>"),
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"]+"), "<path>"),
    (re.compile(r"(?<![\w])/[\w.\-]+(?:/[\w.\-]+)+"), "<path>"),
    (re.compile(r"'[^']*'"), "'…'"),
    (re.compile(r'"[^"]*"'), '"…"'),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hash>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
]
# Summary lines that say "it failed" without saying why: listed after the specific errors.
GENERIC_FAILURE = re.compile(
    r"^(?:BUILD FAILED|Result: Failed|AutomationTool exiting|UnrealBuildTool failed|Took .* ExitCode|ERROR: Cook failed\.?$|"
    r"ERROR: BUILD FAILED|ERROR: AutomationTool)"
)
_PREFIX_RE = re.compile(r"^(Log[A-Za-z0-9_]+): (?:(?:Fatal|Error|Warning|Display|Log|Verbose|VeryVerbose): )?")


def normalize(text: str) -> str:
    msg = _PREFIX_RE.sub("", text).strip()
    for regex, repl in _NORMALIZERS:
        msg = regex.sub(repl, msg)
    return msg[:300]


@dataclass
class IssueGroup:
    severity: Severity
    category: str
    key: str
    sample: str
    count: int = 0
    occurrences: list[tuple[str, int]] = field(default_factory=list)  # (source, line number)


class IssueCollector:
    """Accumulates WARNING/ERROR lines from one or more sources."""

    MAX_OCCURRENCES = 200

    def __init__(self) -> None:
        self.groups: dict[tuple[int, str, str], IssueGroup] = {}

    def add(self, source: str, lines: list[LogLine]) -> list[IssueGroup]:
        touched = []
        for line in lines:
            if line.severity < Severity.WARNING:
                continue
            key = normalize(line.text)
            gkey = (int(line.severity), line.category, key)
            group = self.groups.get(gkey)
            if group is None:
                group = IssueGroup(line.severity, line.category, key, line.text.strip())
                self.groups[gkey] = group
            group.count += 1
            if len(group.occurrences) < self.MAX_OCCURRENCES:
                group.occurrences.append((source, line.number))
            touched.append(group)
        return touched

    def sorted_groups(self) -> list[IssueGroup]:
        return sorted(
            self.groups.values(),
            key=lambda g: (-int(g.severity), bool(GENERIC_FAILURE.match(g.sample)), -g.count, g.category, g.key),
        )

    @property
    def error_count(self) -> int:
        return sum(g.count for g in self.groups.values() if g.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(g.count for g in self.groups.values() if g.severity == Severity.WARNING)
