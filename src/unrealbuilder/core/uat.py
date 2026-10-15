"""argv builders for UAT BuildCookRun steps and the smoke test (invariant 5: separate argv items)."""

from __future__ import annotations

from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout

COMMON_FLAGS = ["-nocompileeditor", "-unattended", "-utf8output", "-nop4"]


def runuat_bat(layout: ToolchainLayout, win_engine_root: Path) -> str:
    return layout.to_win(win_engine_root / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat")


def editor_cmd_exe(layout: ToolchainLayout, win_engine_root: Path) -> str:
    return layout.to_win(win_engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe")


def build_step_argv(
    wine: str,
    layout: ToolchainLayout,
    win_engine_root: Path,
    uproject: Path,
    configuration: str,
    extra: list[str] | None = None,
    compile_editor: bool = False,
) -> list[str]:
    """Compile step. C++ projects also need their Win64 *editor* target: the cook runs the engine's
    UnrealEditor-Cmd.exe, which loads the project's editor module DLLs."""
    flags = [f for f in COMMON_FLAGS if not (compile_editor and f == "-nocompileeditor")]
    return [
        wine,
        "cmd",
        "/c",
        runuat_bat(layout, win_engine_root),
        "BuildCookRun",
        f"-project={layout.to_win(uproject)}",
        "-platform=Win64",
        f"-clientconfig={configuration}",
        "-build",
        "-skipcook",
        "-skipstage",
        *flags,
        *(extra or []),
    ]


def cook_step_argv(
    wine: str,
    layout: ToolchainLayout,
    win_engine_root: Path,
    uproject: Path,
    configuration: str,
    archive_dir: Path,
    incremental: bool = False,
    nodebuginfo: bool = False,
    prereqs: bool = True,
    extra: list[str] | None = None,
) -> list[str]:
    argv = [
        wine,
        "cmd",
        "/c",
        runuat_bat(layout, win_engine_root),
        "BuildCookRun",
        f"-project={layout.to_win(uproject)}",
        # Always: needed when UAT treats a content-only project as code-based (temp targets).
        f"-unrealexe={editor_cmd_exe(layout, win_engine_root)}",
        "-platform=Win64",
        f"-clientconfig={configuration}",
        "-skipbuild",
        "-cook",
        "-stage",
        "-pak",
        "-archive",
        f"-archivedirectory={layout.to_win(archive_dir)}",
    ]
    if prereqs:
        argv.append("-prereqs")
    if incremental:
        argv.append("-cookincremental")
    if nodebuginfo:
        argv.append("-nodebuginfo")
    return argv + COMMON_FLAGS + list(extra or [])


def _exe_names(base: str, configuration: str) -> list[str]:
    if configuration == "Development":
        return [f"{base}.exe"]
    return [f"{base}-Win64-{configuration}.exe"]


def find_game_launch(archive_dir: Path, project: str, configuration: str) -> tuple[Path, list[str]] | None:
    """The real game executable inside an archive (not the root bootstrap) and its arguments.

    Code-based projects ship `<P>/Binaries/Win64/<P>[-Win64-<Config>].exe`.
    Content-only projects ship the engine's `Engine/Binaries/Win64/UnrealGame[...].exe`, which the bootstrap
    launches with the relative project path (`..\\..\\..\\<P>\\<P>.uproject`).
    """
    bindir = archive_dir / project / "Binaries" / "Win64"
    for name in _exe_names(project, configuration):
        if (bindir / name).is_file():
            return bindir / name, []
    engine_bin = archive_dir / "Engine" / "Binaries" / "Win64"
    for name in _exe_names("UnrealGame", configuration):
        if (engine_bin / name).is_file():
            return engine_bin / name, [f"..\\..\\..\\{project}\\{project}.uproject"]
    if bindir.is_dir():
        for exe in sorted(bindir.glob(f"{project}*.exe")):
            return exe, []
    return None


def find_game_exe(archive_dir: Path, project: str, configuration: str) -> Path | None:
    found = find_game_launch(archive_dir, project, configuration)
    return found[0] if found else None


def smoke_argv(wine: str, layout: ToolchainLayout, game_exe: Path, args: list[str] | None = None) -> list[str]:
    return [wine, layout.to_win(game_exe), *(args or []), "-nullrhi", "-nosound", "-unattended", "-stdout", "-FullStdOutLogOutput"]
