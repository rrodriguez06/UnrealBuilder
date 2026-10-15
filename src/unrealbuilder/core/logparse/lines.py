"""Turn raw log bytes into classified lines.

Unreal / UAT / UBT / MSVC logs mix CRLF and LF, may start with a BOM and may contain invalid UTF-8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from unrealbuilder.core.logparse.noise import is_noise


class Severity(IntEnum):
    NOISE = 0
    VERBOSE = 1
    INFO = 2
    WARNING = 3
    ERROR = 4


@dataclass(frozen=True, slots=True)
class LogLine:
    number: int  # 1-based line number in its source
    text: str  # line without the UE timestamp prefix
    severity: Severity
    category: str  # e.g. "LogCook", "MSVC", "UAT"; "" when unknown


class LineSplitter:
    """Incremental splitter: feed() bytes, get complete decoded lines; keeps the partial tail."""

    def __init__(self) -> None:
        self._buf = b""
        self._first = True

    def feed(self, data: bytes) -> list[str]:
        if self._first and data:
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            self._first = False
        self._buf += data
        if b"\n" not in self._buf:
            return []
        head, _, self._buf = self._buf.rpartition(b"\n")
        return [_decode(raw) for raw in head.split(b"\n")]

    def flush(self) -> list[str]:
        if not self._buf:
            return []
        rest, self._buf = self._buf, b""
        return [_decode(rest)]


def _decode(raw: bytes) -> str:
    return raw.rstrip(b"\r").decode("utf-8", errors="replace")


TIMESTAMP_RE = re.compile(r"^\[\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3}\]\[\s*\d+\]")
UE_RE = re.compile(r"^(?P<cat>Log[A-Za-z0-9_]+): (?:(?P<verb>Fatal|Error|Warning|Display|Log|Verbose|VeryVerbose): )?")
MSVC_RE = re.compile(r"\((?:\d+)(?:,\d+)?\)\s*: (?P<kind>fatal error|error|warning) (?P<code>[A-Z]{1,4}\d{3,5})\b")
LINK_RE = re.compile(r"(?:^|\s)(?:fatal )?(?P<kind>error|warning) LNK\d+:")
UAT_RE = re.compile(r"^(?P<kind>ERROR|Error|WARNING|Warning):\s")
EXCEPTION_RE = re.compile(r"^(?:Unhandled exception|[A-Za-z_][\w.]*Exception: )")
UBT_ERROR_RE = re.compile(
    r"^(?:Unable to instantiate module '|Could not find (?:definition for module|NetFxSDK)|UnrealBuildTool failed\b|"
    r"Unable to find plugin '|Missing precompiled manifest|ERROR: Could not find|Expecting to find a type to be declared)"
)
FAIL_RE = re.compile(r"^(?:BUILD FAILED|Result: Failed|Critical error:|Fatal error:)")

_VERB = {
    "Fatal": Severity.ERROR,
    "Error": Severity.ERROR,
    "Warning": Severity.WARNING,
    "Display": Severity.INFO,
    "Log": Severity.INFO,
    "Verbose": Severity.VERBOSE,
    "VeryVerbose": Severity.VERBOSE,
}


def strip_timestamp(text: str) -> str:
    return TIMESTAMP_RE.sub("", text, count=1)


def classify(text: str) -> tuple[Severity, str]:
    """Return (severity, category) for a line already stripped of its timestamp."""
    if is_noise(text):
        return Severity.NOISE, ""
    m = UE_RE.match(text)
    if m:
        verb = m.group("verb")
        return (_VERB.get(verb, Severity.INFO) if verb else Severity.INFO), m.group("cat")
    m = MSVC_RE.search(text)
    if m:
        return (Severity.WARNING if m.group("kind") == "warning" else Severity.ERROR), "MSVC"
    m = LINK_RE.search(text)
    if m:
        return (Severity.WARNING if m.group("kind") == "warning" else Severity.ERROR), "Linker"
    m = UAT_RE.match(text)
    if m:
        return (Severity.ERROR if m.group("kind").lower() == "error" else Severity.WARNING), "UAT"
    if UBT_ERROR_RE.match(text):
        return Severity.ERROR, "UBT"
    if EXCEPTION_RE.match(text) or FAIL_RE.match(text):
        return Severity.ERROR, "UAT"
    return Severity.INFO, ""


def make_line(number: int, raw: str) -> LogLine:
    text = strip_timestamp(raw)
    severity, category = classify(text)
    return LogLine(number, text, severity, category)
