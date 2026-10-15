"""Toolchain layout, Linux→Windows path mapping and guarded deletion."""

from __future__ import annotations

import getpass
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from unrealbuilder.core.settings import Settings, expand


class UnsafePathError(RuntimeError):
    """Raised when an operation would touch a path outside the allowed roots."""


@dataclass(frozen=True)
class ToolchainLayout:
    root: Path
    prefix: Path
    ue_root: Path
    drive: str

    @classmethod
    def from_settings(cls, settings: Settings) -> ToolchainLayout:
        return cls(
            root=settings.toolchain_root,
            prefix=settings.prefix,
            ue_root=settings.ue_root,
            drive=settings.wine.drive.lower().rstrip(":"),
        )

    @property
    def msvc(self) -> Path:
        return self.root / "msvc"

    @property
    def msvc_wine(self) -> Path:
        return self.root / "msvc-wine"

    @property
    def sdks(self) -> Path:
        return self.root / "sdks"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def builds(self) -> Path:
        return self.root / "builds"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    def mirror_dir(self, project: str) -> Path:
        return self.projects / project

    def project_builds_dir(self, project: str) -> Path:
        return self.builds / project

    @property
    def prefix_user_dir(self) -> Path:
        return self.prefix / "drive_c" / "users" / getpass.getuser()

    @property
    def prefix_local_appdata(self) -> Path:
        return self.prefix_user_dir / "AppData" / "Local"

    @property
    def prefix_roaming_appdata(self) -> Path:
        return self.prefix_user_dir / "AppData" / "Roaming"

    @property
    def shader_working_dir(self) -> Path:
        return self.prefix_local_appdata / "Temp" / "UnrealShaderWorkingDir"

    def to_win(self, path: Path | str) -> str:
        """Map a Linux path to the Windows path Wine sees (U:\\... under ue_root, else Z:\\...)."""
        p = Path(os.path.abspath(expand(path)))
        try:
            rel = p.relative_to(self.ue_root)
            parts = rel.parts
            return f"{self.drive.upper()}:\\" + "\\".join(parts)
        except ValueError:
            return "Z:" + str(PureWindowsPath(p.as_posix()))

    def from_win(self, win_path: str) -> Path:
        """Map a Windows path printed by Wine/UE back to a Linux path (best effort)."""
        s = win_path.strip().strip('"').replace("/", "\\")
        drive = s[:2].lower()
        rest = s[3:] if len(s) > 2 and s[2] == "\\" else s[2:]
        parts = [x for x in rest.split("\\") if x]
        if drive == f"{self.drive}:":
            return self.ue_root.joinpath(*parts)
        if drive == "z:":
            return Path("/").joinpath(*parts)
        if drive == "c:":
            return self.prefix.joinpath("drive_c", *parts)
        return Path(s)


_UNIT_SAFE = re.compile(r"[^a-z0-9-]+")


def unit_name(kind: str, job_id: str) -> str:
    """systemd unit name for a job: ub-<kind>-<sanitized id>.service."""
    safe = _UNIT_SAFE.sub("-", job_id.lower()).strip("-")
    return f"ub-{kind}-{safe}.service"


def wine_home_prefix() -> Path:
    return Path.home() / ".wine"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def protected_paths(settings: Settings) -> list[Path]:
    """Paths that must never be deleted, even when under an allowed root."""
    protected = [wine_home_prefix().resolve()]
    protected += [p.resolve() for p in settings.project_roots]
    for eng in settings.engines.values():
        for root in (eng.linux_root, eng.win_root):
            if root:
                protected.append(expand(root).resolve())
    return protected


def safe_rmtree(path: Path, allowed_roots: list[Path], protected: list[Path] | None = None) -> None:
    """Delete a file or directory tree, refusing anything outside `allowed_roots`.

    Symlinks are resolved first, so a link cannot redirect the deletion elsewhere.
    A path equal to (or containing) a protected path is refused.
    """
    real = Path(os.path.realpath(path))
    roots = [Path(os.path.realpath(r)) for r in allowed_roots]
    # A directory root is never deleted itself; a *file* listed as a root (e.g. an installer zip) may be.
    if not any((is_within(real, r) and real != r) or (real == r and r.is_file()) for r in roots):
        raise UnsafePathError(f"Refusing to delete {real}: outside the allowed roots")
    for prot in protected or []:
        prot = Path(os.path.realpath(prot))
        if real == prot or is_within(prot, real):
            raise UnsafePathError(f"Refusing to delete {real}: protected path ({prot})")
    if wine_home_prefix().exists() and is_within(real, Path(os.path.realpath(wine_home_prefix()))):
        raise UnsafePathError("Refusing to touch ~/.wine")
    if path.is_symlink() or real.is_file():
        Path(path).unlink(missing_ok=True)
    elif real.is_dir():
        shutil.rmtree(real)
