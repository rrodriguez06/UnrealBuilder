"""Command-line interface (no Qt)."""

from __future__ import annotations

import argparse
import sys

from unrealbuilder.core.settings import Settings, load_settings

CONFIGS = ("Development", "Shipping", "DebugGame")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unrealbuilder")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="List the Unreal projects found")

    p = sub.add_parser("analyze", help="Analyze one or more projects for a Win64 build")
    p.add_argument("projects", nargs="*", help="Project names (all by default)")
    p.add_argument("-c", "--config", default="Development", choices=CONFIGS)

    p = sub.add_parser("sync", help="Sync and patch a project's mirror")
    p.add_argument("project")
    p.add_argument("-c", "--config", default="Development", choices=CONFIGS)
    p.add_argument("--apply", action="store_true", help="Apply (otherwise: dry run)")

    p = sub.add_parser("run-job", help="Run a Win64 build in an isolated systemd unit")
    p.add_argument("project")
    p.add_argument("-c", "--config", default="Development", choices=CONFIGS)
    p.add_argument("--clean", action="store_true", help="Purge the mirror's Intermediate/Saved/Binaries")
    p.add_argument("--incremental", action="store_true", help="Incremental cook (-cookincremental)")
    p.add_argument("--no-zip", action="store_true")
    p.add_argument("--no-smoke", action="store_true")
    p.add_argument("--workers", type=int, help="Number of shader workers")
    p.add_argument("--memory-max", type=int, help="Unit MemoryMax, in GiB")
    p.add_argument("--uat-arg", action="append", default=[], help="Extra UAT argument (repeatable)")
    p.add_argument("--follow", "-f", action="store_true", help="Follow progress until the end")

    p = sub.add_parser("follow", help="Follow a running or finished build")
    p.add_argument("build_dir", nargs="?", help="Build folder (most recent by default)")

    sub.add_parser("status", help="List builds and active units")

    p = sub.add_parser("cancel", help="Cancel a running build")
    p.add_argument("build_dir", nargs="?")

    p = sub.add_parser("doctor", help="Check the prefix, MSVC and engines; --fix runs a fix")
    p.add_argument("--fix", help="Maintenance action to run (e.g. dll-override, autosdk-links)")
    p.add_argument("--accept-license", action="store_true", help="I accept the Microsoft license (msvc-install, netfx-install)")

    sub.add_parser("install-desktop", help="Install the .desktop launcher and the icon")
    return parser


def cli_commands() -> set[str]:
    parser = build_parser()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse has no public accessor
        return set(action.choices)
    return set()


def run_cli(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    handler = {
        "discover": cmd_discover,
        "analyze": cmd_analyze,
        "sync": cmd_sync,
        "run-job": cmd_run_job,
        "follow": cmd_follow,
        "status": cmd_status,
        "cancel": cmd_cancel,
        "doctor": cmd_doctor,
        "install-desktop": cmd_install_desktop,
    }.get(args.command)
    if handler is None:
        print(f"Command '{args.command}': not implemented yet.")
        return 2
    return handler(settings, args)


def cmd_discover(settings: Settings, args) -> int:
    from unrealbuilder.core.discovery import discover_projects

    projects = discover_projects(settings.project_roots)
    if not projects:
        print("No project found in:", ", ".join(map(str, settings.project_roots)))
        return 1
    for info in projects:
        assoc = info.descriptor.engine_association if info.descriptor else "?"
        print(f"{info.name:<24} UE {assoc:<6} {info.directory}" + (f"  [error: {info.error}]" if info.error else ""))
    return 0


def cmd_analyze(settings: Settings, args) -> int:
    from unrealbuilder.core.discovery import discover_projects
    from unrealbuilder.core.prepare import analyze

    projects = discover_projects(settings.project_roots)
    if args.projects:
        wanted = {n.lower() for n in args.projects}
        projects = [p for p in projects if p.name.lower() in wanted]
    rc = 0
    for info in projects:
        if info.descriptor is None:
            print(f"{info.name}: unreadable .uproject ({info.error})")
            rc = 1
            continue
        engine, a = analyze(settings, info, args.config)
        eng = f"{engine.version} ({engine.win.build_version.label if engine and engine.win.build_version else '?'})" if engine else "none"
        print(f"{info.name} [{args.config}] — engine {eng}")
        print(f"  Type: {a.kind_label}")
        print(f"  Compile step: {'yes' if a.needs_build_step else 'no'}")
        for ref in a.references:
            game = "game" if ref.for_game else "editor"
            print(f"  - {ref.name:<28} {ref.status.value:<17} ({game}) {ref.message}")
        if a.optional_patches:
            print(f"  Mirror patch: Optional → {', '.join(a.optional_patches)}")
        for w in a.warnings:
            print(f"  ! {w}")
        for b in a.blockers:
            print(f"  ✗ {b}")
            rc = 1
    return rc


def cmd_sync(settings: Settings, args) -> int:
    from unrealbuilder.core.prepare import ProjectNotFound, find_project, prepare_mirror

    try:
        info = find_project(settings, args.project)
    except ProjectNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    res = prepare_mirror(settings, info, args.config, apply=args.apply)
    plan = res.plan
    assert plan is not None
    print(f"Mirror: {res.mirror}")
    print(f"  To copy: {len(plan.copy)} files ({plan.copy_bytes / 2**20:.1f} MiB), to delete: {len(plan.delete)}, unchanged: {plan.unchanged}")
    for w in plan.warnings[:20]:
        print(f"  ! {w}")
    if res.analysis.blockers:
        for b in res.analysis.blockers:
            print(f"  ✗ {b}")
        return 1
    if not args.apply:
        print("  (dry run: run again with --apply)")
        return 0
    s = res.sync
    assert s is not None
    print(f"  Copied: {s.copied} ({s.reflinked} via reflink), deleted: {s.deleted}, in {s.seconds:.2f} s")
    print(f"  Patched: {', '.join(res.patched) or 'nothing (already up to date)'}")
    return 0


def _latest_build(settings: Settings, build_dir: str | None):
    from pathlib import Path

    from unrealbuilder.core.jobs import BuildPaths, iter_build_dirs
    from unrealbuilder.core.paths import ToolchainLayout

    if build_dir:
        return BuildPaths(Path(build_dir).expanduser().resolve())
    builds = iter_build_dirs(ToolchainLayout.from_settings(settings).builds)
    if not builds:
        return None
    return max(builds, key=lambda b: b.job.stat().st_mtime)


def cmd_run_job(settings: Settings, args) -> int:
    from unrealbuilder.core.launcher import BuildOptions, launch, make_spec
    from unrealbuilder.core.prepare import ProjectNotFound, analyze, find_project

    try:
        info = find_project(settings, args.project)
    except ProjectNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    _engine, analysis = analyze(settings, info, args.config)
    if analysis.blockers:
        for b in analysis.blockers:
            print(f"✗ {b}", file=sys.stderr)
        return 1
    options = BuildOptions(
        configuration=args.config,
        clean=args.clean,
        incremental_cook=args.incremental,
        zip=False if args.no_zip else None,
        smoke_test=False if args.no_smoke else None,
        shader_workers=args.workers,
        memory_max_gib=args.memory_max,
        extra_uat_args=args.uat_arg or None,
    )
    spec = make_spec(settings, info, options)
    paths, unit, proc = launch(settings, spec)
    if proc.returncode != 0:
        print(f"systemd-run failed: {proc.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Build started: {unit.unit}")
    print(f"  Folder: {paths.root}")
    print(f"  Type: {analysis.kind_label}")
    if args.follow:
        return follow_build(paths, unit.unit)
    return 0


def follow_build(paths, unit: str | None = None) -> int:
    import time

    from unrealbuilder.core import systemd
    from unrealbuilder.core.jobs import STEP_LABELS
    from unrealbuilder.core.logparse.uat import UatParser
    from unrealbuilder.core.paths import unit_name
    from unrealbuilder.core.tail import FileTailer

    spec = paths.load_spec()
    unit = unit or unit_name("build", spec.job_id)
    parser = UatParser()
    tailers = [FileTailer(paths.log("uat-build.log")), FileTailer(paths.log("uat-cook.log"))]
    cook_tailer: FileTailer | None = None
    last_line = ""
    last_print = 0.0
    try:
        while True:
            for t in tailers + ([cook_tailer] if cook_tailer else []):
                lines, _ = t.read_new()
                parser.feed(lines)
            p = parser.progress
            if cook_tailer is None and p.commandlet_log:
                from unrealbuilder.core.paths import ToolchainLayout

                cook_tailer = FileTailer(ToolchainLayout.from_settings(load_settings()).from_win(p.commandlet_log))
            status = paths.read_status()
            step = status.get("step")
            parts = [STEP_LABELS.get(step, step or "—")]
            if step == "build" and p.ubt_total:
                parts.append(f"UBT {p.ubt_done}/{p.ubt_total}")
            if step == "cook":
                if p.cook_total:
                    parts.append(f"packages {p.cook_done}/{p.cook_total}")
                elif p.global_shaders:
                    parts.append("global shaders")
                if p.shaders_left is not None:
                    parts.append(f"{p.shaders_left} shaders left")
                if p.current_phase:
                    parts.append(p.current_phase.lower())
            parts.append(f"{p.errors} error(s), {p.warnings} warning(s)")
            line = " · ".join(parts)
            now = time.monotonic()
            if line != last_line or now - last_print > 30:
                print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
                last_line, last_print = line, now
            result = paths.read_result()
            info = systemd.show(unit, ["ActiveState"])
            if result and (result.get("finalized_at") or not systemd.is_active(info)):
                print(f"Result: {result.get('state')} — {result.get('message', '')}")
                if result.get("zip"):
                    print(f"  Zip: {result['zip']}")
                for w in result.get("warnings", []):
                    print(f"  ! {w}")
                return 0 if result.get("state") == "succeeded" else 1
            if not result and not systemd.is_active(info) and info.get("ActiveState") not in (None, ""):
                time.sleep(2)
                if not paths.read_result() and not systemd.is_active(systemd.show(unit, ["ActiveState"])):
                    print("The unit stopped without a result (see journalctl --user -u " + unit + ")")
                    return 1
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped following (the build keeps running in the background).")
        return 0


def cmd_follow(settings: Settings, args) -> int:
    paths = _latest_build(settings, args.build_dir)
    if paths is None:
        print("No build found.")
        return 1
    print(f"Build: {paths.root}")
    return follow_build(paths)


def cmd_status(settings: Settings, args) -> int:
    from unrealbuilder.core import systemd
    from unrealbuilder.core.jobs import iter_build_dirs
    from unrealbuilder.core.paths import ToolchainLayout, unit_name

    layout = ToolchainLayout.from_settings(settings)
    builds = iter_build_dirs(layout.builds)
    active = {u["unit"] for u in systemd.list_units() if u["active"] in ("active", "activating", "deactivating")}
    if not builds:
        print("No builds.")
    for b in builds[:30]:
        spec = b.load_spec()
        result = b.read_result() or {}
        unit = unit_name("build", spec.job_id)
        state = "running" if unit in active else result.get("state", "?")
        print(f"{spec.project:<16} {spec.configuration:<12} {b.root.name:<36} {state:<12} {result.get('message', '')[:60]}")
    return 0


def cmd_cancel(settings: Settings, args) -> int:
    from unrealbuilder.core import systemd
    from unrealbuilder.core.paths import unit_name

    paths = _latest_build(settings, args.build_dir)
    if paths is None:
        print("No build found.")
        return 1
    spec = paths.load_spec()
    paths.cancel_marker.write_text("cancel\n")
    unit = unit_name("build", spec.job_id)
    rc = systemd.stop(unit)
    print(f"Cancellation requested for {unit} (code {rc})")
    return 0


def cmd_doctor(settings: Settings, args) -> int:
    import time

    from unrealbuilder.core import systemd
    from unrealbuilder.core.doctor.checks import run_checks
    from unrealbuilder.core.launcher import maint_unit_spec
    from unrealbuilder.core.maint import create_maint
    from unrealbuilder.core.paths import unit_name

    if args.fix:
        params = {"accept_license": True} if args.accept_license else {}
        paths = create_maint(args.fix, params)
        proc = systemd.run_unit(maint_unit_spec(settings, paths.root))
        if proc.returncode != 0:
            print(f"systemd-run failed: {proc.stderr.strip()}", file=sys.stderr)
            return 1
        unit = unit_name("maint", paths.job_id)
        print(f"Operation started: {unit}")
        offset = 0
        while True:
            if paths.log.exists():
                with open(paths.log, encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
                if chunk:
                    print(chunk, end="", flush=True)
            result = paths.read_result()
            if result or not systemd.is_active(systemd.show(unit, ["ActiveState"])):
                time.sleep(0.5)
                result = paths.read_result() or {"state": "lost", "message": "the unit stopped without a result"}
                print(f"\n=> {result.get('state')} — {result.get('message', '')}")
                return 0 if result.get("state") == "succeeded" else 1
            time.sleep(1)
    rc = 0
    group = None
    for c in run_checks(settings):
        if c.group != group:
            group = c.group
            print(f"\n{group}")
        mark = {"ok": "✓", "warn": "⚠", "error": "✗"}.get(c.status, "·")
        fix = f"   → unrealbuilder doctor --fix {c.fix}" + (" --accept-license" if c.needs_license else "") if c.fix else ""
        print(f"  {mark} {c.title:<34} {c.detail}{fix}")
        if c.status == "error":
            rc = 1
    return rc


def cmd_install_desktop(settings: Settings, args) -> int:
    """Install ~/.local/share/applications/unrealbuilder.desktop and the icon (app_id = unrealbuilder)."""
    import shutil
    import subprocess
    from pathlib import Path

    from unrealbuilder.core.fsutil import atomic_write_text

    exe = Path(sys.executable).with_name("unrealbuilder")
    if not exe.exists():
        exe = Path(shutil.which("unrealbuilder") or exe)
    data = Path.home() / ".local" / "share"
    icon_src = Path(__file__).resolve().parent / "resources" / "unrealbuilder.svg"
    icon_dst = data / "icons" / "hicolor" / "scalable" / "apps" / "unrealbuilder.svg"
    icon_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_src, icon_dst)
    template = (Path(__file__).resolve().parents[2] / "packaging" / "unrealbuilder.desktop.in")
    text = template.read_text() if template.exists() else DESKTOP_FALLBACK
    desktop = data / "applications" / "unrealbuilder.desktop"
    atomic_write_text(desktop, text.replace("@EXEC@", str(exe)))
    for cmd in (["update-desktop-database", str(desktop.parent)], ["gtk-update-icon-cache", "-q", str(data / "icons" / "hicolor")]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True)
    print(f"Launcher installed: {desktop}")
    print(f"Icon: {icon_dst}")
    print(f"Command: {exe}")
    return 0


DESKTOP_FALLBACK = """[Desktop Entry]
Type=Application
Name=UnrealBuilder
Exec=@EXEC@
Icon=unrealbuilder
Categories=Development;
StartupWMClass=unrealbuilder
"""
