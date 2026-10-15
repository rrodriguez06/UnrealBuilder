"""Incremental reflink mirror of a Linux project into the Wine toolchain tree.

Only authored inputs are mirrored (`.uproject`, Config, Content, Source, Plugins, Build, Platforms).
Build state in the mirror (Intermediate, Saved, Binaries, DerivedDataCache) is never touched by the sync:
files are only deleted from the mirror when the sync itself had copied them before (manifest).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core.fsutil import atomic_write_json, read_json, reflink_or_copy

MANIFEST_DIR = ".unrealbuilder"
MANIFEST_FILE = "mirror.json"
MANIFEST_VERSION = 1

INCLUDE_DIRS = ("Config", "Content", "Source", "Plugins", "Build", "Platforms")
EXCLUDE_DIR_NAMES = {
    "Binaries",
    "Intermediate",
    "Saved",
    "DerivedDataCache",
    ".git",
    ".vs",
    ".idea",
    ".vscode",
    "__pycache__",
    MANIFEST_DIR,
}
EXCLUDE_SUFFIXES = (".sln", ".code-workspace", ".tmp", ".ubtmp")
WIN_PATH_WARN = 220  # cooked paths add ~40 chars (Saved\Cooked\Windows\<P>\...) under MAX_PATH=260


@dataclass
class SyncPlan:
    source: Path
    mirror: Path
    uproject: str
    copy: list[str] = field(default_factory=list)
    delete: list[str] = field(default_factory=list)
    unchanged: int = 0
    copy_bytes: int = 0
    source_files: dict[str, list[int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.copy and not self.delete


@dataclass
class SyncResult:
    copied: int = 0
    reflinked: int = 0
    deleted: int = 0
    bytes: int = 0
    seconds: float = 0.0


def _iter_source_files(source: Path, uproject: str) -> dict[str, list[int]]:
    files: dict[str, list[int]] = {}
    up = source / uproject
    if up.is_file():
        st = up.stat()
        files[uproject] = [st.st_size, st.st_mtime_ns]
    for top in INCLUDE_DIRS:
        base = source / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIR_NAMES)
            for name in filenames:
                if name.endswith(EXCLUDE_SUFFIXES):
                    continue
                full = Path(dirpath) / name
                try:
                    st = full.stat()
                except OSError:
                    continue
                rel = full.relative_to(source).as_posix()
                files[rel] = [st.st_size, st.st_mtime_ns]
    return files


def manifest_path(mirror: Path) -> Path:
    return mirror / MANIFEST_DIR / MANIFEST_FILE


def load_manifest(mirror: Path) -> dict:
    data = read_json(manifest_path(mirror), default=None)
    if not data or data.get("version") != MANIFEST_VERSION:
        return {"version": MANIFEST_VERSION, "files": {}}
    return data


def plan_sync(source: Path, mirror: Path, uproject: str, win_path=None) -> SyncPlan:
    """Compare the source project with the mirror manifest. `win_path` maps a Linux path to Windows."""
    plan = SyncPlan(source=source, mirror=mirror, uproject=uproject)
    manifest = load_manifest(mirror)["files"]
    plan.source_files = _iter_source_files(source, uproject)
    lower_seen: dict[str, str] = {}
    for rel, (size, mtime) in plan.source_files.items():
        prev = manifest.get(rel)
        dst = mirror / rel
        if prev != [size, mtime] or not dst.exists():
            plan.copy.append(rel)
            plan.copy_bytes += size
        else:
            plan.unchanged += 1
        low = rel.lower()
        if low in lower_seen and lower_seen[low] != rel:
            plan.warnings.append(f"Case collision (Windows): {lower_seen[low]} / {rel}")
        lower_seen[low] = rel
        if win_path is not None:
            wp = win_path(mirror / rel)
            if len(wp) > WIN_PATH_WARN:
                plan.warnings.append(f"Long Windows path ({len(wp)} chars): {rel}")
    for rel in manifest:
        if rel not in plan.source_files:
            plan.delete.append(rel)
    return plan


def apply_sync(plan: SyncPlan, progress=None) -> SyncResult:
    """Execute a sync plan. `progress(done, total)` is called periodically."""
    start = time.monotonic()
    result = SyncResult()
    plan.mirror.mkdir(parents=True, exist_ok=True)
    total = len(plan.copy) + len(plan.delete)
    done = 0
    for rel in plan.copy:
        src = plan.source / rel
        if not src.exists():
            continue
        if reflink_or_copy(src, plan.mirror / rel):
            result.reflinked += 1
        result.copied += 1
        result.bytes += plan.source_files.get(rel, [0])[0]
        done += 1
        if progress and done % 200 == 0:
            progress(done, total)
    for rel in plan.delete:
        target = plan.mirror / rel
        if target.is_file() or target.is_symlink():
            target.unlink()
            result.deleted += 1
            _prune_empty_parents(target.parent, plan.mirror)
        done += 1
    if progress:
        progress(total, total)
    atomic_write_json(
        manifest_path(plan.mirror),
        {
            "version": MANIFEST_VERSION,
            "source": str(plan.source),
            "uproject": plan.uproject,
            "synced_at": time.time(),
            "files": plan.source_files,
        },
    )
    result.seconds = time.monotonic() - start
    return result


def _prune_empty_parents(directory: Path, stop: Path) -> None:
    while directory != stop and stop in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
