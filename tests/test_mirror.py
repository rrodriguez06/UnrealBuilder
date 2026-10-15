import os

import pytest

from unrealbuilder.core.fsutil import reflink_or_copy
from unrealbuilder.core.mirror import apply_sync, manifest_path, plan_sync


def make_project(root):
    (root / "Content" / "Maps").mkdir(parents=True)
    (root / "Config").mkdir()
    (root / "Saved" / "Logs").mkdir(parents=True)
    (root / "Intermediate").mkdir()
    (root / "Plugins" / "Mine" / "Binaries").mkdir(parents=True)
    (root / "P.uproject").write_text("{}")
    (root / "Content" / "Maps" / "Map.umap").write_bytes(b"umap" * 100)
    (root / "Config" / "DefaultEngine.ini").write_text("[A]\n")
    (root / "Saved" / "Logs" / "P.log").write_text("log")
    (root / "Intermediate" / "x").write_text("x")
    (root / "Plugins" / "Mine" / "Mine.uplugin").write_text("{}")
    (root / "Plugins" / "Mine" / "Binaries" / "lib.so").write_text("bin")
    (root / "P.sln").write_text("sln")


def test_full_cycle(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "mirror"
    src.mkdir()
    make_project(src)
    plan = plan_sync(src, dst, "P.uproject")
    assert sorted(plan.copy) == [
        "Config/DefaultEngine.ini",
        "Content/Maps/Map.umap",
        "P.uproject",
        "Plugins/Mine/Mine.uplugin",
    ]
    res = apply_sync(plan)
    assert res.copied == 4
    assert manifest_path(dst).exists()
    assert not (dst / "Saved").exists() and not (dst / "Intermediate").exists()
    assert (dst / "Content" / "Maps" / "Map.umap").read_bytes() == b"umap" * 100
    assert os.stat(dst / "P.uproject").st_mtime_ns == os.stat(src / "P.uproject").st_mtime_ns

    # Mirror build state must survive syncs.
    (dst / "Saved" / "Cooked").mkdir(parents=True)
    (dst / "Saved" / "Cooked" / "keep").write_text("cooked")
    (dst / "Config" / "DefaultEditor.ini").write_text("generated")

    assert plan_sync(src, dst, "P.uproject").is_noop

    (src / "Content" / "Maps" / "Map.umap").write_bytes(b"changed")
    os.utime(src / "Content" / "Maps" / "Map.umap", ns=(1, 2_000_000_000))
    (src / "Config" / "DefaultEngine.ini").unlink()
    (src / "Content" / "New.uasset").write_bytes(b"new")
    plan = plan_sync(src, dst, "P.uproject")
    assert sorted(plan.copy) == ["Content/Maps/Map.umap", "Content/New.uasset"]
    assert plan.delete == ["Config/DefaultEngine.ini"]
    res = apply_sync(plan)
    assert res.deleted == 1
    assert not (dst / "Config" / "DefaultEngine.ini").exists()
    assert (dst / "Config" / "DefaultEditor.ini").read_text() == "generated"
    assert (dst / "Saved" / "Cooked" / "keep").exists()
    assert (dst / "Content" / "Maps" / "Map.umap").read_bytes() == b"changed"


def test_missing_destination_file_is_recopied(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "mirror"
    src.mkdir()
    make_project(src)
    apply_sync(plan_sync(src, dst, "P.uproject"))
    (dst / "P.uproject").unlink()
    assert plan_sync(src, dst, "P.uproject").copy == ["P.uproject"]


def test_case_collision_and_long_path_warnings(tmp_path):
    src = tmp_path / "src"
    (src / "Content").mkdir(parents=True)
    (src / "P.uproject").write_text("{}")
    (src / "Content" / "Foo.uasset").write_text("a")
    (src / "Content" / "foo.uasset").write_text("b")
    plan = plan_sync(src, tmp_path / "m", "P.uproject", win_path=lambda p: "U:\\" + "x" * 300)
    assert any("Case collision" in w for w in plan.warnings)
    assert any("Long Windows path" in w for w in plan.warnings)


@pytest.mark.btrfs
def test_real_reflink(btrfs_dir):
    a = btrfs_dir / "reflink-src.bin"
    b = btrfs_dir / "reflink-dst.bin"
    a.write_bytes(os.urandom(1 << 20))
    try:
        assert reflink_or_copy(a, b) is True
        assert a.read_bytes() == b.read_bytes()
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)
