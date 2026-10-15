
import pytest

from unrealbuilder.core.paths import ToolchainLayout, UnsafePathError, safe_rmtree, unit_name
from unrealbuilder.core.settings import EngineSettings
from unrealbuilder.core.wineenv import UnsafePrefixError, merge_dll_overrides, wine_env


@pytest.fixture
def layout(tmp_path):
    ue = tmp_path / "UnrealEngine"
    root = ue / "win64-toolchain"
    (root / "wineprefix").mkdir(parents=True)
    return ToolchainLayout(root=root, prefix=root / "wineprefix", ue_root=ue, drive="u")


def test_to_win_and_back(layout):
    p = layout.ue_root / "win64-toolchain" / "projects" / "TestUnreal" / "TestUnreal.uproject"
    assert layout.to_win(p) == r"U:\win64-toolchain\projects\TestUnreal\TestUnreal.uproject"
    assert layout.from_win(r"U:\win64-toolchain\projects\TestUnreal\TestUnreal.uproject") == p
    assert layout.to_win("/etc/hosts") == r"Z:\etc\hosts"
    assert layout.from_win(r"C:\users\x\AppData") == layout.prefix / "drive_c" / "users" / "x" / "AppData"
    assert layout.from_win("C:/users/x/AppData") == layout.prefix / "drive_c" / "users" / "x" / "AppData"


def test_unit_name_is_sanitized():
    assert unit_name("build", "20261015-120301-TestUnreal-Development") == "ub-build-20261015-120301-testunreal-development.service"
    assert unit_name("maint", "a b/c$d") == "ub-maint-a-b-c-d.service"


def test_wine_env_sets_invariants(layout):
    eng = EngineSettings(autosdk_root=str(layout.root / "sdks"))
    env = wine_env(layout, eng, base={"DISPLAY": ":1", "WAYLAND_DISPLAY": "wayland-1", "WINEDLLOVERRIDES": "foo=b;d3dcompiler_47=b"})
    assert env["WINEPREFIX"] == str(layout.prefix.resolve())
    assert env["WINEDEBUG"] == "-all"
    assert env["WINEDLLOVERRIDES"] == "d3dcompiler_47=n;foo=b"
    assert env["UE_SDKS_ROOT"] == r"U:\win64-toolchain\sdks"
    assert "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env


def test_wine_env_refuses_home_wine(layout, monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    (fake_home / ".wine").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    with pytest.raises(UnsafePrefixError):
        wine_env(layout, prefix=fake_home / ".wine", base={})


def test_wine_env_refuses_prefix_outside_toolchain(layout, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    with pytest.raises(UnsafePrefixError):
        wine_env(layout, prefix=other, base={})


def test_merge_dll_overrides():
    assert merge_dll_overrides(None) == "d3dcompiler_47=n"
    assert merge_dll_overrides("") == "d3dcompiler_47=n"


def test_safe_rmtree(tmp_path):
    root = tmp_path / "root"
    victim = root / "builds" / "x"
    victim.mkdir(parents=True)
    (victim / "f").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")

    with pytest.raises(UnsafePathError):
        safe_rmtree(outside, [root])
    with pytest.raises(UnsafePathError):
        safe_rmtree(root, [root])  # the root itself is never deleted
    link = root / "escape"
    link.symlink_to(outside)
    with pytest.raises(UnsafePathError):
        safe_rmtree(link, [root])
    protected = root / "builds"
    with pytest.raises(UnsafePathError):
        safe_rmtree(protected, [root], protected=[victim])
    safe_rmtree(victim, [root])
    assert not victim.exists()
    assert (outside / "keep").exists()
