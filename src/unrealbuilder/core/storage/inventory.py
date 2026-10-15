"""Inventory of everything UnrealBuilder stores on disk, with btrfs-aware sizes."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core.engines import engine_pairs
from unrealbuilder.core.jobs import BuildPaths, iter_build_dirs
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import Settings


@dataclass
class StorageItem:
    id: str
    label: str
    path: Path
    category: str
    deletable: bool
    reason: str = ""
    effect: str = ""
    needs_idle: bool = False  # cannot be deleted while a build is running
    group: bool = False  # aggregate node: its sizes are the sum of its children
    total: int | None = None
    exclusive: int | None = None
    shared: int | None = None
    mtime: float | None = None
    children: list[StorageItem] = field(default_factory=list)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def _item(id_, label, path, category, deletable, reason="", effect="", needs_idle=False) -> StorageItem | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    return StorageItem(id_, label, path, category, deletable, reason, effect, needs_idle, mtime=mtime)


def _add(parent: list[StorageItem] | StorageItem, item: StorageItem | None) -> None:
    if item is None:
        return
    (parent.children if isinstance(parent, StorageItem) else parent).append(item)


def build_inventory(settings: Settings) -> list[StorageItem]:
    layout = ToolchainLayout.from_settings(settings)
    roots: list[StorageItem] = []

    # Engines (never deletable here; listed for sizes and dedupe)
    engines = StorageItem("engines", "Engines", layout.ue_root, "engines", False, "Managed from the Toolchain page", group=True)
    for version, pair in engine_pairs(settings).items():
        _add(engines, _item(f"engine-linux-{version}", f"UE {version} — Linux (authoring)", pair.linux.root, "engines", False, "The user's main engine"))
        _add(engines, _item(f"engine-win-{version}", f"UE {version} — Windows (packaging)", pair.win.root, "engines", False, "Required for packaging"))
    for z in sorted(layout.ue_root.glob("Linux_Unreal_Engine_*.zip")):
        _add(
            engines,
            _item(f"installer-{z.name}", f"Installer archive {z.name}", z, "junk", True, "Already extracted into the Linux engine", "Must be downloaded again to reinstall"),
        )
    roots.append(engines)

    tool = StorageItem("toolchain", "Toolchain Win64", layout.root, "toolchain", False)
    _add(tool, _item("msvc", "MSVC + Windows SDK (msvc-wine)", layout.msvc, "toolchain", False, "Required to compile"))
    prefix = _item("prefix", "Dedicated Wine prefix", layout.prefix, "toolchain", False, "Required; never to be confused with ~/.wine")
    if prefix:
        common = layout.prefix_local_appdata / "UnrealEngine" / "Common"
        _add(prefix, _item("ddc", "Local DDC cache", common / "DerivedDataCache", "cache", True, "Rebuildable cache", "Next cook takes longer (shaders to recompile)", True))
        _add(prefix, _item("zen", "Zen cache (shaders, DDC)", common / "Zen" / "Data", "cache", True, "Rebuildable cache", "Next cook spends ≈ 15 more min on shaders", True))
        _add(prefix, _item("shaderwork", "Shader working folders", layout.shader_working_dir, "junk", True, "Temporary files", "None", True))
        _add(prefix, _item("uat-logs", "AutomationTool logs (prefix)", layout.prefix_roaming_appdata / "Unreal Engine" / "AutomationTool" / "Logs", "junk", True, "Logs already copied per build", "None", True))
        _add(prefix, _item("ubt-logs", "UnrealBuildTool logs and traces", layout.prefix_local_appdata / "UnrealBuildTool", "junk", True, "UBA logs and traces", "None", True))
        _add(prefix, _item("crash", "Crash reports", layout.prefix_local_appdata / "CrashReportClient", "junk", True, "Local reports", "None"))
        _add(tool, prefix)
    for version, pair in engine_pairs(settings).items():
        _add(tool, _item(f"cooklogs-{version}", f"Cook logs in the Windows engine {version}", pair.win.engine_dir / "Programs" / "AutomationTool" / "Saved", "junk", True, "Commandlet logs", "None", True))
    _add(tool, _item("tmp", "Toolchain temporary files", layout.root / "tmp", "junk", True, "Installation leftovers", "None"))
    roots.append(tool)

    mirrors = StorageItem("mirrors", "Project mirrors", layout.projects, "mirrors", False, group=True)
    if layout.projects.is_dir():
        for proj in sorted(p for p in layout.projects.iterdir() if p.is_dir()):
            m = _item(f"mirror-{proj.name}", proj.name, proj, "mirrors", True, "Recreated on the next sync", "Next build is a full one (compile + cook)", True)
            if m is None:
                continue
            _add(m, _item(f"mirror-{proj.name}-cooked", "Saved/Cooked", proj / "Saved" / "Cooked", "cache", True, "Cooked content", "Next cook is a full one", True))
            _add(m, _item(f"mirror-{proj.name}-staged", "Saved/StagedBuilds", proj / "Saved" / "StagedBuilds", "junk", True, "Copy of the stage, already archived", "None", True))
            _add(m, _item(f"mirror-{proj.name}-intermediate", "Intermediate", proj / "Intermediate", "cache", True, "Compilation objects", "Next compile is a full one", True))
            _add(m, _item(f"mirror-{proj.name}-binaries", "Binaries/Win64", proj / "Binaries" / "Win64", "cache", True, "Compiled binaries", "Next compile has to relink", True))
            _add(mirrors, m)
    roots.append(mirrors)

    builds = StorageItem("builds", "Builds", layout.builds, "builds", False, group=True)
    if layout.builds.is_dir():
        for proj in sorted(p for p in layout.builds.iterdir() if p.is_dir()):
            p_item = StorageItem(f"builds-{proj.name}", proj.name, proj, "builds", False, group=True)
            for b in iter_build_dirs(layout.builds):
                if b.root.parent != proj:
                    continue
                _add(p_item, build_item(b))
            if p_item.children:
                builds.children.append(p_item)
    roots.append(builds)
    return roots


def build_item(b: BuildPaths) -> StorageItem | None:
    result = b.read_result() or {}
    pinned = b.pinned_marker.exists()
    running = not result.get("finalized_at") and not result
    label = f"{b.root.name} — {result.get('state', 'running' if running else '?')}" + (" 📌" if pinned else "")
    item = _item(f"build-{b.root.parent.name}-{b.root.name}", label, b.root, "builds", not pinned and not running,
                 "Pinned" if pinned else ("Build running" if running else "Build finished"), "Deletes the build and its zip")
    if item is None:
        return None
    try:
        spec = b.load_spec()
        _add(item, _item(f"{item.id}-archive", "Archive folder", b.archive(spec), "builds", not pinned, "The zip stays available", "Only the zip remains"))
    except (OSError, TypeError, ValueError):
        pass
    for z in b.zips():
        _add(item, _item(f"{item.id}-zip-{z.name}", z.name, z, "builds", not pinned, "Archive to ship", "The zip is lost"))
    _add(item, _item(f"{item.id}-logs", "Logs", b.logs, "builds", False, "Kept with the build"))
    return item


def btrfs_sizes(paths: list[Path]) -> dict[Path, tuple[int, int, int]]:
    """{path: (total, exclusive, shared)} via `btrfs filesystem du -s --raw` (unprivileged); du fallback."""
    out: dict[Path, tuple[int, int, int]] = {}
    existing = [p for p in paths if p.exists()]
    if not existing:
        return out
    if shutil.which("btrfs"):
        for i in range(0, len(existing), 50):
            chunk = existing[i : i + 50]
            try:
                proc = subprocess.run(["btrfs", "filesystem", "du", "-s", "--raw", *map(str, chunk)], capture_output=True, text=True, timeout=600)
            except (OSError, subprocess.SubprocessError):
                break
            for line in proc.stdout.splitlines()[1:]:
                parts = line.split(None, 3)
                if len(parts) == 4 and parts[0].isdigit():
                    out[Path(parts[3])] = (int(parts[0]), int(parts[1]), int(parts[2]) if parts[2].isdigit() else 0)
    for p in existing:
        if p not in out:
            total = _du(p)
            out[p] = (total, total, 0)
    return out


def _du(path: Path) -> int:
    if path.is_file():
        return path.stat().st_blocks * 512
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_blocks * 512
            except OSError:
                pass
    return total


def fill_sizes(roots: list[StorageItem]) -> None:
    items = [it for r in roots for it in r.walk() if it.path.exists() and not it.group]
    sizes = btrfs_sizes(sorted({it.path for it in items}))
    for it in items:
        total, excl, shared = sizes.get(it.path, (None, None, None))
        it.total, it.exclusive, it.shared = total, excl, shared
    for r in roots:
        _sum_groups(r)


def _sum_groups(item: StorageItem) -> None:
    for c in item.children:
        _sum_groups(c)
    if item.group:
        item.total = sum(c.total or 0 for c in item.children)
        item.exclusive = sum(c.exclusive or 0 for c in item.children)
        item.shared = sum(c.shared or 0 for c in item.children)
