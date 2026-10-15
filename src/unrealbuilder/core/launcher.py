"""Create build jobs and launch them as systemd --user transient units."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.discovery import ProjectInfo
from unrealbuilder.core.jobs import BuildPaths, JobSpec, create_build_dir
from unrealbuilder.core.paths import ToolchainLayout, unit_name
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.systemd import UnitResources, UnitSpec, run_unit, systemd_run_argv


@dataclass
class BuildOptions:
    configuration: str = "Development"
    clean: bool = False
    incremental_cook: bool = False
    zip: bool | None = None
    smoke_test: bool | None = None
    shader_workers: int | None = None
    memory_max_gib: int | None = None
    swap_max_gib: int | None = None
    extra_uat_args: list[str] | None = None
    allow_cl_mismatch: bool = False


def make_spec(settings: Settings, info: ProjectInfo, options: BuildOptions, prefix: Path | None = None, zen_port: int | None = None) -> JobSpec:
    r, o = settings.resources, settings.outputs
    override = settings.project_override(info.name)
    shipping = options.configuration == "Shipping"
    return JobSpec(
        job_id=JobSpec.make_id(info.name, options.configuration),
        project=info.name,
        project_dir=str(info.directory),
        uproject=info.uproject.name,
        configuration=options.configuration,
        clean=options.clean,
        incremental_cook=options.incremental_cook,
        zip=o.zip if options.zip is None else options.zip,
        zip_exclude_pdb=o.zip_exclude_pdb,
        smoke_test=o.smoke_test if options.smoke_test is None else options.smoke_test,
        smoke_timeout_s=o.smoke_timeout_s,
        nodebuginfo=o.nodebuginfo_shipping and shipping,
        shader_workers=options.shader_workers or r.shader_workers,
        memory_max_gib=options.memory_max_gib or r.job_memory_max_gib,
        swap_max_gib=r.job_swap_max_gib if options.swap_max_gib is None else options.swap_max_gib,
        zen_port=zen_port or settings.cook.zen_port,
        extra_uat_args=list(override.extra_uat_args) + list(options.extra_uat_args or []),
        prefix=str(prefix or settings.prefix),
        allow_cl_mismatch=options.allow_cl_mismatch,
        delete_staged_after_archive=o.delete_staged_after_archive,
    )


def runner_command(action: str, build_dir: Path, python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", "unrealbuilder.runner", action, str(build_dir)]


def unit_spec_for(settings: Settings, spec: JobSpec, paths: BuildPaths, python: str | None = None) -> UnitSpec:
    r = settings.resources
    return UnitSpec(
        unit=unit_name("build", spec.job_id),
        description=f"UnrealBuilder: {spec.project} {spec.configuration}",
        command=runner_command("run", paths.root, python),
        exec_stop_post=runner_command("finalize", paths.root, python),
        working_directory=ToolchainLayout.from_settings(settings).root,
        resources=UnitResources(
            memory_max_gib=spec.memory_max_gib,
            swap_max_gib=spec.swap_max_gib,
            cpu_weight=r.cpu_weight,
            io_weight=r.io_weight,
            nice=r.nice,
            allowed_cpus=r.allowed_cpus,
            runtime_max_hours=r.max_runtime_hours,
        ),
    )


def prepare_job(settings: Settings, spec: JobSpec) -> tuple[BuildPaths, UnitSpec]:
    layout = ToolchainLayout.from_settings(settings)
    paths = create_build_dir(layout.builds, spec)
    return paths, unit_spec_for(settings, spec, paths)


def launch_argv(settings: Settings, spec: JobSpec) -> tuple[BuildPaths, UnitSpec, list[str]]:
    paths, unit = prepare_job(settings, spec)
    return paths, unit, systemd_run_argv(unit)


def launch(settings: Settings, spec: JobSpec) -> tuple[BuildPaths, UnitSpec, subprocess.CompletedProcess]:
    paths, unit = prepare_job(settings, spec)
    return paths, unit, run_unit(unit)


def maint_unit_spec(settings: Settings, maint_root: Path, python: str | None = None) -> UnitSpec:
    from unrealbuilder.core.maint import MaintPaths, label

    paths = MaintPaths(maint_root)
    return UnitSpec(
        unit=unit_name("maint", paths.job_id),
        description=f"UnrealBuilder: {label(paths.action().get('action', ''))}",
        command=runner_command("maint", maint_root, python),
        working_directory=ToolchainLayout.from_settings(settings).root if ToolchainLayout.from_settings(settings).root.exists() else None,
        resources=UnitResources(memory_max_gib=8, swap_max_gib=2, cpu_weight=50, io_weight=20, nice=15, runtime_max_hours=6),
    )
