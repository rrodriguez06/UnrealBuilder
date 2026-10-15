"""Windows engine core install/update through legendary, pre-seeded with reflinks from the Linux engine.

Proven trick: files byte-identical to the Linux install (same CL) are reflinked into the Windows dir and listed
in legendary's resume file (`sha1:path` lines), so legendary skips downloading and writing them.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.fsutil import reflink_or_copy

Run = Callable[[list[str], dict[str, str] | None], int]


@dataclass
class ManifestFile:
    path: str
    size: int
    sha1: str
    tags: list[str]


def fetch_manifest(app: str, platform: str = "Windows") -> tuple[str, list[ManifestFile]]:
    """Use legendary's Python API (system package) to read the CDN manifest: (version, files)."""
    from legendary.core import LegendaryCore  # noqa: PLC0415 - system package, optional

    core = LegendaryCore()
    if not core.login():
        raise RuntimeError("legendary is not logged in: run `legendary auth`")
    game = core.get_game(app, update_meta=True, platform=platform)
    data = core.get_cdn_manifest(game, platform)
    manifest = core.load_manifest(data[0])
    files = [
        ManifestFile(fm.filename, fm.file_size, fm.hash.hex(), list(fm.install_tags))
        for fm in manifest.file_manifest_list.elements
    ]
    return manifest.meta.build_version, files


def installed_version(app: str) -> str | None:
    from unrealbuilder.core.fsutil import read_json

    data = read_json(Path.home() / ".config" / "legendary" / "installed.json", default={}) or {}
    entry = data.get(app)
    return entry.get("version") if entry else None


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def seed_from_linux(files: list[ManifestFile], linux_root: Path, win_root: Path, resume_file: Path, progress=None) -> tuple[int, int]:
    """Reflink core files identical to the Linux install; write the legendary resume file. Returns (files, bytes)."""
    core = [f for f in files if not f.tags]
    candidates = []
    for f in core:
        src = linux_root / f.path
        try:
            if src.is_file() and not src.is_symlink() and src.stat().st_size == f.size:
                candidates.append(f)
        except OSError:
            continue
    with ThreadPoolExecutor(12) as ex:
        hashes = list(ex.map(lambda f: _sha1(linux_root / f.path), candidates))
    seeded = []
    total = 0
    for i, (f, h) in enumerate(zip(candidates, hashes, strict=True)):
        if h != f.sha1:
            continue
        dst = win_root / f.path
        if not dst.exists():
            reflink_or_copy(linux_root / f.path, dst)
        seeded.append(f)
        total += f.size
        if progress and i % 2000 == 0:
            progress(i, len(candidates))
    resume_file.parent.mkdir(parents=True, exist_ok=True)
    existing = resume_file.read_text().splitlines() if resume_file.exists() else []
    lines = set(existing) | {f"{f.sha1}:{f.path}" for f in seeded}
    resume_file.write_text("\n".join(sorted(lines)) + "\n")
    return len(seeded), total


def resume_path(app: str) -> Path:
    return Path.home() / ".config" / "legendary" / "tmp" / f"{app}.resume"


def legendary_install_argv(app: str, win_root: Path, update: bool = False) -> list[str]:
    argv = ["legendary", "install", app, "--platform", "Windows", "--install-tag", "", "--game-folder", str(win_root), "-y"]
    if update:
        argv.append("--update-only")
    return argv


def install_or_update(app: str, linux_root: Path, win_root: Path, run: Run, update: bool, log=print) -> int:
    version, files = fetch_manifest(app)
    log(f"Manifest {app}: {version}, {len(files)} files ({sum(f.size for f in files if not f.tags) / 2**30:.1f} GiB for the core)")
    win_root.mkdir(parents=True, exist_ok=True)
    if linux_root.is_dir():
        n, size = seed_from_linux(files, linux_root, win_root, resume_path(app), lambda i, t: log(f"Pre-seeding: {i}/{t}"))
        log(f"Pre-seeded via reflink: {n} identical files, {size / 2**30:.1f} GiB")
    os.environ.pop("DISPLAY", None)
    return run(legendary_install_argv(app, win_root, update), None)


def latest_version(app: str, platform: str = "Windows") -> str | None:
    """Latest available build version from Epic's catalog metadata (no manifest download)."""
    from legendary.core import LegendaryCore  # noqa: PLC0415

    core = LegendaryCore()
    if not core.login():
        raise RuntimeError("legendary is not logged in: run `legendary auth`")
    game = core.get_game(app, update_meta=True, platform=platform)
    return game.app_version(platform) if game else None
