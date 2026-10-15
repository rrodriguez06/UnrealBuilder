"""Invariant 3: golden systemd-run argv, never MemoryHigh."""

from pathlib import Path

import pytest

from unrealbuilder.core.systemd import UnitArgError, UnitResources, UnitSpec, parse_show, set_property_argv, systemd_run_argv


def spec(**kw):
    return UnitSpec(
        unit="ub-build-x.service",
        description="UnrealBuilder: P Development",
        command=["/venv/bin/python", "-m", "unrealbuilder.runner", "run", "/t/builds/P/x"],
        exec_stop_post=["/venv/bin/python", "-m", "unrealbuilder.runner", "finalize", "/t/builds/P/x"],
        working_directory=Path("/t"),
        resources=UnitResources(**kw),
    )


def test_golden_argv():
    argv = systemd_run_argv(spec())
    assert argv[:7] == [
        "systemd-run",
        "--user",
        "--unit=ub-build-x.service",
        "--description=UnrealBuilder: P Development",
        "--slice=unrealbuilder.slice",
        "--collect",
        "--quiet",
    ]
    props = [argv[i + 1] for i, a in enumerate(argv) if a == "-p"]
    assert "Type=exec" in props
    assert "MemoryMax=20G" in props and "MemorySwapMax=2G" in props
    assert "OOMPolicy=stop" in props and "KillMode=mixed" in props
    assert "RuntimeMaxSec=14400" in props
    assert any(p.startswith("ExecStopPost=/venv/bin/python -m unrealbuilder.runner finalize") for p in props)
    assert not any("MemoryHigh" in a for a in argv)
    assert argv[-5:] == ["/venv/bin/python", "-m", "unrealbuilder.runner", "run", "/t/builds/P/x"]
    assert "--working-directory=/t" in argv


def test_allowed_cpus():
    argv = systemd_run_argv(spec(allowed_cpus="6-11,18-23"))
    assert "AllowedCPUs=6-11,18-23" in argv


def test_dollar_is_rejected():
    s = spec()
    s.command = ["/bin/echo", "$HOME"]
    with pytest.raises(UnitArgError):
        systemd_run_argv(s)


def test_memory_high_cannot_be_set_live():
    with pytest.raises(UnitArgError):
        set_property_argv("ub-build-x.service", MemoryHigh="10G")
    assert set_property_argv("ub-build-x.service", MemoryMax="24G")[-1] == "MemoryMax=24G"


def test_parse_show():
    info = parse_show("ActiveState=active\nSubState=running\nControlGroup=/user.slice/x.service\n")
    assert info == {"ActiveState": "active", "SubState": "running", "ControlGroup": "/user.slice/x.service"}


def test_build_step_compiles_editor_only_for_cpp_projects():
    from pathlib import Path

    from unrealbuilder.core.paths import ToolchainLayout
    from unrealbuilder.core.uat import build_step_argv, cook_step_argv

    layout = ToolchainLayout(root=Path("/ue/t"), prefix=Path("/ue/t/p"), ue_root=Path("/ue"), drive="u")
    plugin_based = build_step_argv("wine", layout, Path("/ue/UE_Win"), Path("/ue/t/projects/P/P.uproject"), "Development")
    cpp = build_step_argv("wine", layout, Path("/ue/UE_Win"), Path("/ue/t/projects/P/P.uproject"), "Development", compile_editor=True)
    assert "-nocompileeditor" in plugin_based and "-nocompileeditor" not in cpp
    cook = cook_step_argv("wine", layout, Path("/ue/UE_Win"), Path("/ue/t/projects/P/P.uproject"), "Shipping", Path("/ue/t/builds/P/x/A"))
    assert "-nocompileeditor" in cook and "-skipbuild" in cook
    assert r"-unrealexe=U:\UE_Win\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" in cook
    assert r"-archivedirectory=U:\t\builds\P\x\A" in cook
