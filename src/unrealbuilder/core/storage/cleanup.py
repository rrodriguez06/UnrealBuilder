"""Guarded deletion of inventory items, "useless files" preset and build retention policy."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.engines import engine_pairs
from unrealbuilder.core.jobs import BuildPaths, JobState, iter_build_dirs
from unrealbuilder.core.paths import ToolchainLayout, protected_paths, safe_rmtree
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.storage.inventory import StorageItem, _du, build_inventory

JUNK_IDS = ("shaderwork", "uat-logs", "ubt-logs", "crash", "tmp")


def allowed_roots(settings: Settings) -> list[Path]:
    """Everything UnrealBuilder may delete lives under these roots."""
    layout = ToolchainLayout.from_settings(settings)
    roots = [layout.root]
    for pair in engine_pairs(settings).values():
        roots.append(pair.win.engine_dir / "Programs" / "AutomationTool" / "Saved")
    roots += list(layout.ue_root.glob("Linux_Unreal_Engine_*.zip"))
    return roots


def protected(settings: Settings) -> list[Path]:
    layout = ToolchainLayout.from_settings(settings)
    prot = protected_paths(settings)
    prot += [layout.prefix, layout.msvc, layout.msvc_wine, layout.sdks]
    return prot


def delete_paths(settings: Settings, paths: list[Path], log=print) -> int:
    """Delete paths with safe_rmtree. A protected root itself is refused, its *content* is not
    (e.g. the prefix is protected but its DDC folder can be cleared). Returns bytes freed (approx.)."""
    roots = allowed_roots(settings)
    prot = protected(settings)
    freed = 0
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        size = _du(path) if path.exists() else 0
        # Only protect exact roots and their ancestors: children of the prefix are fine.
        exact = [p for p in prot if p.resolve() == path.resolve() or p.resolve().is_relative_to(path.resolve())]
        safe_rmtree(path, roots, protected=exact)
        freed += size
        log(f"Deleted: {path} ({size / 2**30:.2f} GiB)")
    return freed


def apply_cleanup(settings: Settings, items: list[str], log=print) -> int:
    return delete_paths(settings, [Path(p) for p in items], log)


def junk_items(settings: Settings) -> list[StorageItem]:
    out = []
    for root in build_inventory(settings):
        for it in root.walk():
            if it.deletable and (it.id in JUNK_IDS or it.id.startswith("cooklogs-") or it.id.endswith("-staged")):
                out.append(it)
    return out


@dataclass
class RetentionAction:
    path: Path
    kind: str  # "archive" (delete archive folder, keep zip) | "build" (delete the whole build dir)
    reason: str


def retention_plan(settings: Settings, now: float | None = None) -> list[RetentionAction]:
    """Builds to compact/delete according to settings.retention (pinned builds are never touched)."""
    now = now or time.time()
    ret = settings.retention
    layout = ToolchainLayout.from_settings(settings)
    groups: dict[tuple[str, str], list[BuildPaths]] = {}
    actions: list[RetentionAction] = []
    for b in iter_build_dirs(layout.builds):
        if b.pinned_marker.exists():
            continue
        result = b.read_result()
        if not result or not result.get("finalized_at"):
            continue  # running or never finalized: leave it alone
        try:
            spec = b.load_spec()
        except (OSError, TypeError, ValueError):
            continue
        state = result.get("state")
        if state == JobState.SUCCEEDED.value:
            groups.setdefault((spec.project, spec.configuration), []).append(b)
            continue
        archive = b.archive(spec)
        if archive.exists():
            actions.append(RetentionAction(archive, "archive", f"Build {state}: output not needed"))
        finished = result.get("finalized_at") or 0
        if now - finished > ret.failed_logs_days * 86400:
            actions.append(RetentionAction(b.root, "build", f"Build {state}, older than {ret.failed_logs_days} days"))
    for (project, config), builds in groups.items():
        builds.sort(key=lambda b: b.root.name, reverse=True)
        for i, b in enumerate(builds):
            spec = b.load_spec()
            if i == 0:
                continue
            if i < ret.keep_successful_per_config:
                archive = b.archive(spec)
                if ret.compact_older_to_zip_only and archive.exists() and b.zips():
                    actions.append(RetentionAction(archive, "archive", f"{project} {config}: older build, the zip is enough"))
            else:
                actions.append(RetentionAction(b.root, "build", f"{project} {config}: beyond the {ret.keep_successful_per_config} builds kept"))
    return actions
