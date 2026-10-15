"""btrfs extent deduplication (FIDEDUPERANGE): the kernel compares bytes before sharing, contents never change.

Port of the POC scripts (find_dups.py + dedupe.py) with a FIEMAP pre-check that skips pairs already sharing
their extents (e.g. reflinked at install), so re-runs are cheap.
"""

from __future__ import annotations

import collections
import fcntl
import hashlib
import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FIDEDUPERANGE = 0xC0189436
FS_IOC_FIEMAP = 0xC020660B
CHUNK = 16 * 1024 * 1024
MIN_SIZE = 64 * 1024
HDR, INFO = "=QQHHI", "=qQQiI"
FIEMAP_HDR = "=QQIIII"
FIEMAP_EXT = "=QQQQQIIII"
FIEMAP_FLAG_SYNC = 0x1


def first_extent(path: Path) -> int | None:
    """Physical offset of the first extent (None if unknown)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        buf = bytearray(struct.pack(FIEMAP_HDR, 0, 1 << 62, FIEMAP_FLAG_SYNC, 0, 1, 0) + bytes(struct.calcsize(FIEMAP_EXT)))
        fcntl.ioctl(fd, FS_IOC_FIEMAP, buf, True)
        mapped = struct.unpack_from(FIEMAP_HDR, buf)[3]
        if not mapped:
            return None
        return struct.unpack_from(FIEMAP_EXT, buf, struct.calcsize(FIEMAP_HDR))[1]
    except OSError:
        return None
    finally:
        os.close(fd)


def dedupe_pair(src: Path, dst: Path, size: int) -> tuple[int, bool, bool]:
    """Returns (bytes deduped, differs, error)."""
    deduped = 0
    sfd = os.open(src, os.O_RDONLY)
    dfd = os.open(dst, os.O_RDONLY)
    try:
        off = 0
        while off < size:
            ln = min(CHUNK, size - off)
            buf = bytearray(struct.pack(HDR, off, ln, 1, 0, 0) + struct.pack(INFO, dfd, off, 0, 0, 0))
            fcntl.ioctl(sfd, FIDEDUPERANGE, buf, True)
            _fd, _o, done, status, _r = struct.unpack_from(INFO, buf, struct.calcsize(HDR))
            if status == 1:
                return deduped, True, False
            if status < 0:
                return deduped, False, True
            deduped += done
            off += ln
    except OSError:
        return deduped, False, True
    finally:
        os.close(sfd)
        os.close(dfd)
    return deduped, False, False


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def find_duplicates(roots: list[Path], min_size: int = MIN_SIZE, log=print) -> list[tuple[int, list[str]]]:
    files: list[tuple[str, int, int]] = []
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not os.path.isfile(p) or os.path.islink(p) or st.st_size < min_size:
                    continue
                files.append((p, st.st_size, st.st_ino))
    log(f"{len(files)} files ≥ {min_size // 1024} KiB scanned")
    by_size: dict[int, list[tuple[str, int, int]]] = collections.defaultdict(list)
    for f in files:
        by_size[f[1]].append(f)
    candidates = [f for group in by_size.values() if len(group) > 1 for f in group if len({x[2] for x in group}) > 1]
    log(f"{len(candidates)} same-size candidates to hash")
    with ThreadPoolExecutor(12) as ex:
        hashes = list(ex.map(lambda f: _sha1(f[0]), candidates))
    groups: dict[tuple[int, str], list[str]] = collections.defaultdict(list)
    seen_inodes: dict[tuple[int, str], set[int]] = collections.defaultdict(set)
    for (path, size, ino), h in zip(candidates, hashes, strict=True):
        key = (size, h)
        if ino in seen_inodes[key]:
            continue  # hardlink: already the same file
        seen_inodes[key].add(ino)
        groups[key].append(path)
    return [(size, paths) for (size, _h), paths in groups.items() if len(paths) > 1]


def run_dedupe(roots: list[Path], log=print, state: Path | None = None, dry_run: bool = False) -> dict:
    start = time.monotonic()
    groups = find_duplicates(roots, log=log)
    report = {"groups": len(groups), "pairs": 0, "already_shared": 0, "deduped_bytes": 0, "estimate_bytes": 0, "differs": 0, "errors": 0}
    for size, paths in groups:
        src = Path(paths[0])
        src_ext = first_extent(src)
        for p in paths[1:]:
            dst = Path(p)
            if src_ext is not None and first_extent(dst) == src_ext:
                report["already_shared"] += 1
                continue
            report["pairs"] += 1
            report["estimate_bytes"] += size
            if dry_run:
                continue
            done, differs, error = dedupe_pair(src, dst, size)
            report["deduped_bytes"] += done
            report["differs"] += int(differs)
            report["errors"] += int(error)
    report["seconds"] = round(time.monotonic() - start, 1)
    gib = (report["estimate_bytes"] if dry_run else report["deduped_bytes"]) / 2**30
    log(
        f"{'Estimate' if dry_run else 'Deduplication'}: {report['pairs']} pairs, {gib:.2f} GiB"
        f" ({report['already_shared']} already shared, {report['differs']} different, {report['errors']} errors) in {report['seconds']} s"
    )
    if state is not None:
        (state / "dedupe-report.json").write_text(json.dumps(report, indent=2))
    return report
