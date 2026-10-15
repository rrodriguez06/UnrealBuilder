import json
import os
import time

import pytest

from unrealbuilder.core.jobs import JobSpec, create_build_dir
from unrealbuilder.core.paths import ToolchainLayout, UnsafePathError
from unrealbuilder.core.settings import EngineSettings, Settings
from unrealbuilder.core.storage.cleanup import delete_paths, junk_items, retention_plan
from unrealbuilder.core.storage.dedupe import first_extent, run_dedupe
from unrealbuilder.core.storage.inventory import build_inventory, fill_sizes


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".wine").mkdir(parents=True)
    ue = tmp_path / "UE"
    t = ue / "win64-toolchain"
    prefix = t / "wineprefix"
    local = prefix / "drive_c" / "users" / os.environ.get("USER", "user") / "AppData" / "Local"
    (local / "UnrealEngine" / "Common" / "DerivedDataCache").mkdir(parents=True)
    (local / "UnrealEngine" / "Common" / "DerivedDataCache" / "x.udd").write_bytes(b"x" * 1000)
    (local / "Temp" / "UnrealShaderWorkingDir" / "abc").mkdir(parents=True)
    (t / "msvc").mkdir()
    for name in ("UE_5.8", "UE_5.8_Win64"):
        (ue / name / "Engine" / "Build").mkdir(parents=True)
        (ue / name / "Engine" / "Build" / "Build.version").write_text(json.dumps({"Changelist": 1, "CompatibleChangelist": 1}))
    (ue / "Linux_Unreal_Engine_5.8.3.zip").write_bytes(b"zip")
    s = Settings()
    s.paths.ue_root = str(ue)
    s.paths.toolchain_root = str(t)
    s.paths.project_roots = [str(tmp_path / "projects")]
    s.wine.prefix = str(prefix)
    s.engines = {"5.8": EngineSettings(linux_root=str(ue / "UE_5.8"), win_root=str(ue / "UE_5.8_Win64"))}
    return s


def make_build(settings, project, config, state, age_s=0, zip_=True, n=0):
    layout = ToolchainLayout.from_settings(settings)
    spec = JobSpec(job_id=f"2026101{n}-0000{n}0-{project}-{config}", project=project, project_dir="/p", uproject=f"{project}.uproject", configuration=config)
    paths = create_build_dir(layout.builds, spec)
    archive = paths.archive(spec)
    archive.mkdir()
    (archive / "game.exe").write_bytes(b"MZ")
    if zip_:
        (paths.root / f"{spec.archive_name}.zip").write_bytes(b"PK")
    paths.write_result({"state": state, "finalized_at": time.time() - age_s})
    return paths


def test_delete_paths_guards(env, tmp_path):
    layout = ToolchainLayout.from_settings(env)
    with pytest.raises(UnsafePathError):
        delete_paths(env, [layout.prefix])  # the prefix itself is protected
    with pytest.raises(UnsafePathError):
        delete_paths(env, [tmp_path / "home" / ".wine"])
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(UnsafePathError):
        delete_paths(env, [outside])
    ddc = layout.prefix_local_appdata / "UnrealEngine" / "Common" / "DerivedDataCache"
    assert delete_paths(env, [ddc]) > 0  # caches inside the protected prefix are fine
    assert not ddc.exists()
    zipf = layout.ue_root / "Linux_Unreal_Engine_5.8.3.zip"
    delete_paths(env, [zipf])
    assert not zipf.exists()


def test_inventory_lists_caches_and_builds(env):
    make_build(env, "P", "Development", "succeeded")
    roots = build_inventory(env)
    ids = {it.id for r in roots for it in r.walk()}
    assert {"prefix", "ddc", "shaderwork", "msvc"} <= ids
    assert any(i.startswith("installer-") for i in ids)
    assert any(i.startswith("build-P-") for i in ids)
    fill_sizes(roots)
    ddc = next(it for r in roots for it in r.walk() if it.id == "ddc")
    assert ddc.total and ddc.total >= 1000
    assert {it.id for it in junk_items(env)} >= {"shaderwork"}


def test_retention_plan(env):
    env.retention.keep_successful_per_config = 2
    env.retention.failed_logs_days = 7
    old = make_build(env, "P", "Development", "succeeded", n=1)
    mid = make_build(env, "P", "Development", "succeeded", n=2)
    new = make_build(env, "P", "Development", "succeeded", n=3)
    failed_recent = make_build(env, "P", "Shipping", "failed", n=4)
    failed_old = make_build(env, "P", "Shipping", "failed", age_s=30 * 86400, n=5)
    pinned = make_build(env, "Q", "Development", "failed", n=6)
    pinned.pinned_marker.write_text("x")
    plan = {(a.path, a.kind) for a in retention_plan(env)}
    spec = mid.load_spec()
    assert (mid.archive(spec), "archive") in plan  # kept, compacted to zip only
    assert (old.root, "build") in plan  # beyond keep count
    assert not any(p == new.root or p.is_relative_to(new.root) for p, _k in plan)  # newest untouched
    assert (failed_recent.archive(failed_recent.load_spec()), "archive") in plan
    assert (failed_old.root, "build") in plan
    assert not any(p.is_relative_to(pinned.root) for p, _k in plan)


@pytest.mark.btrfs
def test_real_dedupe(btrfs_dir):
    d = btrfs_dir / "dedupe-test"
    d.mkdir(exist_ok=True)
    data = os.urandom(3 * 1024 * 1024)
    a, b = d / "a.bin", d / "b.bin"
    a.write_bytes(data)
    b.write_bytes(data)
    os.sync()
    try:
        assert first_extent(a) != first_extent(b)
        report = run_dedupe([d], log=lambda _m: None)
        assert report["pairs"] == 1 and report["deduped_bytes"] == len(data) and report["errors"] == 0
        assert first_extent(a) == first_extent(b)
        assert a.read_bytes() == b.read_bytes() == data
        again = run_dedupe([d], log=lambda _m: None, dry_run=True)
        assert again["already_shared"] == 1 and again["pairs"] == 0
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
