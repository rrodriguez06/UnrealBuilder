"""Prepare a project's mirror for a Win64 build: analyze → sync → patch."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core.discovery import ProjectInfo, discover_projects
from unrealbuilder.core.engines import EnginePair, engine_for_association
from unrealbuilder.core.mirror import SyncPlan, SyncResult, apply_sync, plan_sync
from unrealbuilder.core.patching import apply_ini_block, editor_ini_sections, engine_ini_sections, patch_uproject
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.uproject import ProjectAnalysis, analyze_project


class ProjectNotFound(LookupError):
    pass


def find_project(settings: Settings, name: str) -> ProjectInfo:
    for info in discover_projects(settings.project_roots):
        if info.name.lower() == name.lower():
            return info
    raise ProjectNotFound(f"Project not found: {name}")


def available_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


@dataclass
class PrepareResult:
    project: ProjectInfo
    engine: EnginePair | None
    analysis: ProjectAnalysis
    mirror: Path
    plan: SyncPlan | None = None
    sync: SyncResult | None = None
    patched: list[str] = field(default_factory=list)


def analyze(settings: Settings, info: ProjectInfo, configuration: str = "Development") -> tuple[EnginePair | None, ProjectAnalysis]:
    assert info.descriptor is not None
    engine = engine_for_association(settings, info.descriptor.engine_association)
    override = settings.project_override(info.name)
    return engine, analyze_project(info.descriptor, engine, configuration, override.force_optional_plugins)


def prepare_mirror(
    settings: Settings,
    info: ProjectInfo,
    configuration: str = "Development",
    apply: bool = True,
    shader_workers: int | None = None,
    memory_max_gib: int | None = None,
    zen_port: int | None = None,
    progress=None,
) -> PrepareResult:
    layout = ToolchainLayout.from_settings(settings)
    engine, analysis = analyze(settings, info, configuration)
    mirror = layout.mirror_dir(info.name)
    result = PrepareResult(info, engine, analysis, mirror)
    result.plan = plan_sync(info.directory, mirror, info.uproject.name, win_path=layout.to_win)
    if not apply or analysis.blockers:
        return result
    result.sync = apply_sync(result.plan, progress)

    workers = shader_workers or settings.resources.shader_workers
    if patch_uproject(mirror / info.uproject.name, analysis.optional_patches):
        result.patched.append(info.uproject.name)
    engine_sections = engine_ini_sections(
        nproc=available_cpus(),
        shader_workers=workers,
        zen_port=zen_port if zen_port is not None else settings.cook.zen_port,
        verbose_shader_progress=settings.cook.verbose_shader_progress,
    )
    if apply_ini_block(mirror / "Config" / "DefaultEngine.ini", engine_sections):
        result.patched.append("Config/DefaultEngine.ini")
    editor_sections = (
        editor_ini_sections(memory_max_gib or settings.resources.job_memory_max_gib, workers)
        if settings.cook.cooker_memory_guard
        else {}
    )
    if apply_ini_block(mirror / "Config" / "DefaultEditor.ini", editor_sections):
        result.patched.append("Config/DefaultEditor.ini")
    return result
