"""Incremental file tailing that survives truncation and file replacement."""

from __future__ import annotations

import os
from pathlib import Path

from unrealbuilder.core.logparse.lines import LineSplitter, LogLine, make_line


class FileTailer:
    def __init__(self, path: Path, max_bytes_per_read: int = 8 * 1024 * 1024) -> None:
        self.path = path
        self.max_bytes = max_bytes_per_read
        self.offset = 0
        self.inode: int | None = None
        self.splitter = LineSplitter()
        self.line_count = 0

    def reset(self) -> None:
        self.offset = 0
        self.splitter = LineSplitter()
        self.line_count = 0

    def read_new(self) -> tuple[list[LogLine], bool]:
        """Return (new complete lines, reset_happened)."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return [], False
        reset = False
        if self.inode is not None and (st.st_ino != self.inode or st.st_size < self.offset):
            self.reset()
            reset = True
        self.inode = st.st_ino
        if st.st_size <= self.offset:
            return [], reset
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read(self.max_bytes)
        self.offset += len(data)
        lines = []
        for raw in self.splitter.feed(data):
            self.line_count += 1
            lines.append(make_line(self.line_count, raw))
        return lines, reset

    @property
    def caught_up(self) -> bool:
        try:
            return os.stat(self.path).st_size <= self.offset
        except FileNotFoundError:
            return True
