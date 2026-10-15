"""Small filesystem helpers: atomic writes, JSON I/O, reflink copies."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

# ioctl(dest_fd, FICLONE, src_fd): whole-file reflink (btrfs, xfs).
FICLONE = 0x40049409


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def reflink_or_copy(src: Path, dst: Path) -> bool:
    """Copy src to dst, sharing extents when the filesystem supports it.

    Returns True when a reflink was made, False when a regular copy was needed.
    The destination is replaced atomically and keeps the source mode and mtime.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".ubtmp", dir=dst.parent)
    cloned = False
    try:
        with open(src, "rb") as fsrc, os.fdopen(fd, "wb") as fdst:
            try:
                fcntl.ioctl(fdst.fileno(), FICLONE, fsrc.fileno())
                cloned = True
            except OSError as exc:
                if exc.errno not in (errno.EOPNOTSUPP, errno.EXDEV, errno.EINVAL, errno.ENOTTY, errno.EBADF):
                    raise
                fsrc.seek(0)
                fdst.seek(0)
                fdst.truncate()
                shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
        st = src.stat()
        os.chmod(tmp, st.st_mode & 0o7777)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return cloned
