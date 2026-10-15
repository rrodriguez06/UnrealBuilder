"""Find Unreal projects under the configured project roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.uproject import ProjectDescriptor

SKIP_DIRS = {"Binaries", "Intermediate", "Saved", "DerivedDataCache", "Content", "Source", "Plugins", "Config"}


@dataclass
class ProjectInfo:
    name: str
    directory: Path
    uproject: Path
    descriptor: ProjectDescriptor | None
    error: str | None = None


def _candidate_files(root: Path, max_depth: int) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found

    def walk(directory: Path, depth: int) -> None:
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        ups = [e for e in entries if e.is_file() and e.suffix == ".uproject"]
        if ups:
            found.extend(ups)
            return
        if depth >= max_depth:
            return
        for e in sorted(entries):
            if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS:
                walk(e, depth + 1)

    walk(root, 0)
    return found


def discover_projects(roots: list[Path], max_depth: int = 2) -> list[ProjectInfo]:
    projects: list[ProjectInfo] = []
    seen: set[Path] = set()
    for root in roots:
        for uproject in _candidate_files(root, max_depth):
            real = uproject.resolve()
            if real in seen:
                continue
            seen.add(real)
            try:
                desc = ProjectDescriptor.load(uproject)
                projects.append(ProjectInfo(uproject.stem, uproject.parent, uproject, desc))
            except (OSError, ValueError) as exc:
                projects.append(ProjectInfo(uproject.stem, uproject.parent, uproject, None, str(exc)))
    return sorted(projects, key=lambda p: p.name.lower())
