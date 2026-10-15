"""Engine installs (Linux authoring engine + Windows packaging engine) and their plugin indexes."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from unrealbuilder.core import xdg
from unrealbuilder.core.fsutil import atomic_write_json, read_json
from unrealbuilder.core.plugins import LoadedFrom, PluginDescriptor, scan_plugins
from unrealbuilder.core.settings import EngineSettings, Settings, expand

PLUGIN_INDEX_VERSION = 1


@dataclass(frozen=True)
class BuildVersion:
    major: int
    minor: int
    patch: int
    changelist: int
    compatible_changelist: int
    branch: str

    @property
    def label(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch} (CL {self.changelist})"


class EngineInstall:
    def __init__(self, root: Path, label: str) -> None:
        self.root = root
        self.label = label

    @property
    def engine_dir(self) -> Path:
        return self.root / "Engine"

    @property
    def exists(self) -> bool:
        return (self.engine_dir / "Build" / "Build.version").is_file()

    @property
    def is_installed_build(self) -> bool:
        return (self.engine_dir / "Build" / "InstalledBuild.txt").is_file()

    @cached_property
    def build_version(self) -> BuildVersion | None:
        data = read_json(self.engine_dir / "Build" / "Build.version")
        if not data:
            return None
        return BuildVersion(
            major=int(data.get("MajorVersion", 0)),
            minor=int(data.get("MinorVersion", 0)),
            patch=int(data.get("PatchVersion", 0)),
            changelist=int(data.get("Changelist", 0)),
            compatible_changelist=int(data.get("CompatibleChangelist", 0)),
            branch=str(data.get("BranchName", "")),
        )

    @property
    def binary_platforms(self) -> list[str]:
        binaries = self.engine_dir / "Binaries"
        if not binaries.is_dir():
            return []
        skip = {"DotNET", "ThirdParty"}
        return sorted(p.name for p in binaries.iterdir() if p.is_dir() and p.name not in skip)

    def plugin_index(self, use_cache: bool = True) -> dict[str, PluginDescriptor]:
        """Engine plugins keyed by lower-case name, cached per engine changelist."""
        bv = self.build_version
        cache = None
        if bv is not None:
            cache = xdg.state_dir() / "plugin-index" / f"{self.label}-{bv.changelist}.json"
            if use_cache:
                data = read_json(cache)
                if data and data.get("version") == PLUGIN_INDEX_VERSION and data.get("root") == str(self.root):
                    return {k: PluginDescriptor.from_cache(v) for k, v in data["plugins"].items()}
        index: dict[str, PluginDescriptor] = {}
        for sub in (self.engine_dir / "Plugins", self.engine_dir / "Platforms"):
            for key, desc in scan_plugins(sub, LoadedFrom.ENGINE).items():
                index.setdefault(key, desc)
        if cache is not None:
            atomic_write_json(
                cache,
                {
                    "version": PLUGIN_INDEX_VERSION,
                    "root": str(self.root),
                    "plugins": {k: v.to_json() for k, v in index.items()},
                },
            )
        return index


@dataclass
class EnginePair:
    """The Linux engine a project is authored with, and the Windows engine used to package it."""

    version: str
    settings: EngineSettings
    linux: EngineInstall
    win: EngineInstall

    @property
    def changelists_match(self) -> bool | None:
        a, b = self.linux.build_version, self.win.build_version
        if a is None or b is None:
            return None
        return a.changelist == b.changelist

    @property
    def compatible(self) -> bool | None:
        a, b = self.linux.build_version, self.win.build_version
        if a is None or b is None:
            return None
        return a.compatible_changelist == b.compatible_changelist


def engine_pairs(settings: Settings) -> dict[str, EnginePair]:
    pairs = {}
    for version, eng in settings.engines.items():
        pairs[version] = EnginePair(
            version=version,
            settings=eng,
            linux=EngineInstall(expand(eng.linux_root), f"linux-{version}"),
            win=EngineInstall(expand(eng.win_root), f"win64-{version}"),
        )
    return pairs


def engine_for_association(settings: Settings, association: str) -> EnginePair | None:
    pairs = engine_pairs(settings)
    if association in pairs:
        return pairs[association]
    # "5.8.3" → "5.8"
    short = ".".join(association.split(".")[:2])
    return pairs.get(short)
