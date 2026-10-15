"""End-to-end runner test with a fake `wine` replaying real POC logs (no Wine, no systemd)."""

import json
import stat
import sys
import textwrap
import zipfile

import pytest

from unrealbuilder.core.jobs import BuildPaths, JobState
from unrealbuilder.core.launcher import BuildOptions, make_spec, prepare_job
from unrealbuilder.core.prepare import find_project
from unrealbuilder.core.settings import EngineSettings, Settings
from unrealbuilder.runner import build as build_mod
from unrealbuilder.runner.finalize import resolve_state

FAKE_WINE = textwrap.dedent(
    """\
    #!{python}
    import os, sys, time
    from pathlib import Path
    args = sys.argv[1:]
    fixtures = Path(os.environ["FAKE_FIXTURES"])
    ue_root = Path(os.environ["FAKE_UE_ROOT"])
    fail = os.environ.get("FAKE_FAIL_STEP", "")
    def to_linux(win):
        win = win.replace("\\\\", "/")
        assert win[:2].lower() == "u:", win
        return ue_root / win[3:]
    if "BuildCookRun" in args:
        if "-build" in args:
            sys.stdout.buffer.write((fixtures / "uat_build.log").read_bytes())
            if fail == "build":
                print("U:\\\\p\\\\A.cpp(1): error C2065: 'x': undeclared identifier")
                print("AutomationTool exiting with ExitCode=6 (Error_Unknown)")
                sys.exit(6)
            sys.exit(0)
        if "-cook" in args:
            archive = to_linux(next(a.split("=", 1)[1] for a in args if a.startswith("-archivedirectory=")))
            project = next(a.split("=", 1)[1] for a in args if a.startswith("-project="))
            name = project.replace("\\\\", "/").rsplit("/", 1)[1].split(".")[0]
            sys.stdout.buffer.write((fixtures / "uat_cook.log").read_bytes())
            if fail == "cook":
                print("LogCook: Error: Cook failed for /Game/Broken")
                print("AutomationTool exiting with ExitCode=25 (Error_UnknownCookFailure)")
                sys.exit(25)
            bindir = archive / name / "Binaries" / "Win64"
            bindir.mkdir(parents=True, exist_ok=True)
            (bindir / f"{{name}}.exe").write_bytes(b"MZ" + b"x" * 1000)
            (bindir / f"{{name}}.pdb").write_bytes(b"pdb" * 1000)
            (archive / f"{{name}}.exe").write_bytes(b"MZ")
            sys.exit(0)
    # smoke test: the game exe
    if os.environ.get("FAKE_SMOKE") == "crash":
        print("Fatal error: boom", flush=True)
        sys.exit(3)
    print("LogInit: Display: Engine is initialized. Leaving FEngineLoop::Init()", flush=True)
    print("LogLoad: LoadMap: /Game/Map", flush=True)
    time.sleep(60)
    """
)


@pytest.fixture
def env(tmp_path, monkeypatch, fixtures_dir):
    ue = tmp_path / "UE"
    t = ue / "win64-toolchain"
    prefix = t / "wineprefix"
    prefix.mkdir(parents=True)
    (prefix / "user.reg").write_text('[Software\\\\Wine\\\\DllOverrides] 1\n"d3dcompiler_47"="native"\n')
    bv = {"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 3, "Changelist": 7, "CompatibleChangelist": 7}
    for name in ("UE_5.8", "UE_5.8_Win64"):
        (ue / name / "Engine" / "Build").mkdir(parents=True)
        (ue / name / "Engine" / "Build" / "Build.version").write_text(json.dumps(bv))
    plug = ue / "UE_5.8_Win64" / "Engine" / "Plugins" / "GameplayStateTree"
    plug.mkdir(parents=True)
    (plug / "GameplayStateTree.uplugin").write_text(json.dumps({"EnabledByDefault": False, "Modules": [{"Name": "G", "Type": "Runtime"}]}))
    projects = tmp_path / "projects"
    proj = projects / "Demo"
    (proj / "Content").mkdir(parents=True)
    (proj / "Config").mkdir()
    (proj / "Content" / "Map.umap").write_bytes(b"map")
    (proj / "Config" / "DefaultEngine.ini").write_text("[A]\nb=c\n")
    (proj / "Demo.uproject").write_text(json.dumps({"EngineAssociation": "5.8", "Plugins": [{"Name": "GameplayStateTree", "Enabled": True}]}))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    wine = bindir / "wine"
    wine.write_text(FAKE_WINE.format(python=sys.executable))
    wine.chmod(wine.stat().st_mode | stat.S_IEXEC)
    wineserver = bindir / "wineserver"
    wineserver.write_text("#!/bin/sh\nexit 0\n")
    wineserver.chmod(0o755)

    s = Settings()
    s.paths.project_roots = [str(projects)]
    s.paths.ue_root = str(ue)
    s.paths.toolchain_root = str(t)
    s.wine.prefix = str(prefix)
    s.wine.wine = str(wine)
    s.wine.wineserver = str(wineserver)
    s.engines = {"5.8": EngineSettings(linux_root=str(ue / "UE_5.8"), win_root=str(ue / "UE_5.8_Win64"), autosdk_root=str(t / "sdks"))}
    s.outputs.smoke_timeout_s = 20

    monkeypatch.setenv("FAKE_FIXTURES", str(fixtures_dir / "logs"))
    monkeypatch.setenv("FAKE_UE_ROOT", str(ue))
    monkeypatch.setattr(build_mod, "MIN_FREE_GIB", 0)
    return s


def run_job(settings, **opts):
    info = find_project(settings, "Demo")
    spec = make_spec(settings, info, BuildOptions(**opts))
    paths, _unit = prepare_job(settings, spec)
    rc = build_mod.BuildRunner(paths.root, settings).run()
    return rc, paths


def test_successful_build(env):
    rc, paths = run_job(env)
    assert rc == 0
    result = paths.read_result()
    assert result["state"] == JobState.SUCCEEDED
    steps = paths.read_status()["steps"]
    assert steps["build"]["state"] == "done"  # code-based through GameplayStateTree
    assert steps["cook"]["state"] == "done"
    assert steps["smoke"]["state"] == "done"
    assert steps["zip"]["state"] == "done"
    zips = paths.zips()
    assert len(zips) == 1
    names = zipfile.ZipFile(zips[0]).namelist()
    assert "Demo-Win64-Development/Demo/Binaries/Win64/Demo.exe" in names
    assert not any(n.endswith(".pdb") for n in names)
    summary = json.loads(paths.summary.read_text())
    assert summary["progress"]["cook_total"] == 581
    assert summary["analysis"]["code_based_reason"] == "GameplayStateTree plugin is enabled"
    assert paths.commands.exists() and "-unrealexe=" in paths.commands.read_text()
    assert (paths.logs / "uat-build.log").exists() and (paths.logs / "uat-cook.log").exists()
    mirror = env.toolchain_root / "projects" / "Demo"
    assert "NumUnusedShaderCompilingThreads" in (mirror / "Config" / "DefaultEngine.ini").read_text()
    assert not (paths.archive(paths.load_spec()) / "Demo" / "Saved").exists()


def test_build_failure_reports_first_error(env, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_STEP", "build")
    rc, paths = run_job(env)
    result = paths.read_result()
    assert rc == 1
    assert result["state"] == JobState.FAILED and result["failed_step"] == "build"
    assert "error C2065" in result["message"]
    assert result["exit_code"] == 6


def test_cook_failure(env, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_STEP", "cook")
    rc, paths = run_job(env, zip=False, smoke_test=False)
    result = paths.read_result()
    assert result["state"] == JobState.FAILED and result["failed_step"] == "cook"
    assert result["exit_code"] == 25


def test_smoke_crash_is_a_warning_not_a_failure(env, monkeypatch):
    monkeypatch.setenv("FAKE_SMOKE", "crash")
    rc, paths = run_job(env, zip=False)
    result = paths.read_result()
    assert result["state"] == JobState.SUCCEEDED
    assert any("Startup test" in w for w in result["warnings"])
    assert paths.read_status()["steps"]["smoke"]["state"] == "warning"


def test_preflight_refuses_prefix_without_native_d3dcompiler(env):
    (env.prefix / "user.reg").write_text("")
    rc, paths = run_job(env)
    result = paths.read_result()
    assert result["state"] == JobState.FAILED and result["failed_step"] == "preflight"
    assert "d3dcompiler_47" in result["message"]


@pytest.mark.parametrize(
    "runner_state,service,cancel,ooms,expected",
    [
        ("succeeded", "success", False, 0, JobState.SUCCEEDED),
        ("failed", "exit-code", False, 0, JobState.FAILED),
        ("cancelled", "exit-code", True, 0, JobState.CANCELLED),
        (None, "oom-kill", False, 0, JobState.OOM_KILLED),
        ("cancelled", "exit-code", False, 1, JobState.OOM_KILLED),
        (None, "timeout", False, 0, JobState.TIMEOUT),
        (None, "signal", False, 0, JobState.FAILED),
    ],
)
def test_resolve_state(runner_state, service, cancel, ooms, expected):
    result = {"state": runner_state} if runner_state else None
    assert resolve_state(result, service, cancel, ooms) == expected


def test_finalize_without_runner_result(env, monkeypatch):
    info = find_project(env, "Demo")
    spec = make_spec(env, info, BuildOptions())
    paths, _ = prepare_job(env, spec)
    paths.write_status({"step": "cook", "state": "running"})
    monkeypatch.setenv("SERVICE_RESULT", "oom-kill")
    monkeypatch.setattr("unrealbuilder.runner.finalize.load_settings", lambda: env)
    monkeypatch.setattr("unrealbuilder.runner.finalize.gui_alive", lambda: True)
    from unrealbuilder.runner.finalize import finalize

    assert finalize(paths.root) == 0
    result = BuildPaths(paths.root).read_result()
    assert result["state"] == JobState.OOM_KILLED
    assert result["failed_step"] == "cook"
    assert "memory" in result["message"]
    assert paths.read_status()["state"] == JobState.OOM_KILLED




def test_find_game_launch_code_and_content_only(tmp_path):
    from unrealbuilder.core.uat import find_game_launch

    code = tmp_path / "code"
    (code / "P" / "Binaries" / "Win64").mkdir(parents=True)
    (code / "P" / "Binaries" / "Win64" / "P.exe").write_bytes(b"MZ")
    (code / "P" / "Binaries" / "Win64" / "P-Win64-Shipping.exe").write_bytes(b"MZ")
    assert find_game_launch(code, "P", "Development") == (code / "P" / "Binaries" / "Win64" / "P.exe", [])
    assert find_game_launch(code, "P", "Shipping")[0].name == "P-Win64-Shipping.exe"

    content = tmp_path / "content"
    (content / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
    (content / "P" / "Content").mkdir(parents=True)
    (content / "Engine" / "Binaries" / "Win64" / "UnrealGame.exe").write_bytes(b"MZ")
    exe, args = find_game_launch(content, "P", "Development")
    assert exe.name == "UnrealGame.exe" and args == ["..\\..\\..\\P\\P.uproject"]
    assert find_game_launch(content, "P", "Shipping") is None


def test_summary_records_its_own_step_as_done(env):
    rc, paths = run_job(env, zip=False, smoke_test=False)
    summary = json.loads(paths.summary.read_text())
    assert summary["steps"]["summary"]["state"] == "done"
