"""Read Wine's text registry hives (user.reg / system.reg) without starting Wine.

Format:
    [Software\\\\Wine\\\\DllOverrides] 1792057827
    #time=1dd5c8a9b842784
    "d3dcompiler_47"="native"
    "OptIn"=dword:00000000
"""

from __future__ import annotations

import re
from pathlib import Path

SECTION_RE = re.compile(r"^\[(?P<key>.+?)\](?:\s+\d+)?\s*$")
STRING_RE = re.compile(r'^"(?P<name>(?:[^"\\]|\\.)*)"="(?P<value>(?:[^"\\]|\\.)*)"\s*$')
DWORD_RE = re.compile(r'^"(?P<name>(?:[^"\\]|\\.)*)"=dword:(?P<value>[0-9a-fA-F]{8})\s*$')
DEFAULT_RE = re.compile(r'^@="(?P<value>(?:[^"\\]|\\.)*)"\s*$')


def _unescape(s: str) -> str:
    return s.replace("\\\\", "\\").replace('\\"', '"')


def normalize_key(key: str) -> str:
    return key.replace("\\\\", "\\").strip("\\").lower()


class WineRegistry:
    def __init__(self, values: dict[str, dict[str, str | int]]) -> None:
        self.values = values

    @classmethod
    def load(cls, path: Path, wanted_prefixes: tuple[str, ...] | None = None) -> WineRegistry:
        """Parse a hive. `wanted_prefixes` (normalized key prefixes) limits memory use on huge hives."""
        values: dict[str, dict[str, str | int]] = {}
        current: dict[str, str | int] | None = None
        try:
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            return cls(values)
        with fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if line.startswith("["):
                    m = SECTION_RE.match(line)
                    if m:
                        key = normalize_key(m.group("key"))
                        if wanted_prefixes is None or key.startswith(wanted_prefixes):
                            current = values.setdefault(key, {})
                        else:
                            current = None
                    continue
                if current is None or not line or line.startswith("#"):
                    continue
                if m := STRING_RE.match(line):
                    current[_unescape(m.group("name")).lower()] = _unescape(m.group("value"))
                elif m := DWORD_RE.match(line):
                    current[_unescape(m.group("name")).lower()] = int(m.group("value"), 16)
                elif m := DEFAULT_RE.match(line):
                    current["@"] = _unescape(m.group("value"))
        return cls(values)

    def get(self, key: str, name: str, default=None):
        return self.values.get(normalize_key(key), {}).get(name.lower(), default)


def dll_override(prefix: Path, dll: str) -> str | None:
    reg = WineRegistry.load(prefix / "user.reg", ("software\\wine\\dlloverrides",))
    return reg.get(r"Software\Wine\DllOverrides", dll)


def d3dcompiler_is_native(prefix: Path) -> bool:
    value = dll_override(prefix, "d3dcompiler_47")
    return bool(value) and value.split(",")[0].strip().lower() in ("native", "n")
