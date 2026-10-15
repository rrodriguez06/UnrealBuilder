"""User settings, stored as TOML in ~/.config/unrealbuilder/settings.toml.

Paths may use `~`; they are expanded by the accessors, never rewritten on disk.
Unknown keys are ignored so older/newer files stay loadable.
"""

from __future__ import annotations

import dataclasses
import tomllib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from unrealbuilder.core import xdg
from unrealbuilder.core.fsutil import atomic_write_text

SCHEMA_VERSION = 1
SETTINGS_FILE = "settings.toml"


def expand(path: str | Path) -> Path:
    return Path(str(path)).expanduser()


@dataclass
class PathsSettings:
    project_roots: list[str] = field(default_factory=lambda: ["~/Documents/Unreal Projects"])
    ue_root: str = "~/Applications/UnrealEngine"
    toolchain_root: str = "~/Applications/UnrealEngine/win64-toolchain"


@dataclass
class WineSettings:
    wine: str = "/usr/bin/wine"
    wineserver: str = "/usr/bin/wineserver"
    prefix: str = "~/Applications/UnrealEngine/win64-toolchain/wineprefix"
    # Wine drive letter mapped to paths.ue_root (keeps Windows paths short).
    drive: str = "u"


@dataclass
class EngineSettings:
    linux_root: str = ""
    win_root: str = ""
    legendary_app: str = ""
    autosdk_root: str = "~/Applications/UnrealEngine/win64-toolchain/sdks"


def _default_engines() -> dict[str, EngineSettings]:
    return {
        "5.8": EngineSettings(
            linux_root="~/Applications/UnrealEngine/UE_5.8",
            win_root="~/Applications/UnrealEngine/UE_5.8_Win64",
            legendary_app="UE_5.8",
        )
    }


@dataclass
class ResourceSettings:
    max_concurrent_builds: int = 1
    ram_budget_gib: int = 24
    job_memory_max_gib: int = 20
    job_swap_max_gib: int = 2
    shader_workers: int = 8
    cpu_weight: int = 50
    io_weight: int = 50
    nice: int = 10
    allowed_cpus: str = ""
    max_runtime_hours: int = 4


@dataclass
class CookSettings:
    zen_port: int = 8559
    verbose_shader_progress: bool = True
    # MemoryMaxUsedPhysical is deprecated by Epic (5.8) and Wine reports host RAM: off by default.
    cooker_memory_guard: bool = False


@dataclass
class OutputSettings:
    zip: bool = True
    zip_exclude_pdb: bool = True
    smoke_test: bool = True
    smoke_timeout_s: int = 120
    delete_staged_after_archive: bool = True
    nodebuginfo_shipping: bool = True


@dataclass
class RetentionSettings:
    keep_successful_per_config: int = 3
    compact_older_to_zip_only: bool = True
    failed_logs_days: int = 14


@dataclass
class NotificationSettings:
    on_success: bool = True
    on_failure: bool = True
    on_stall: bool = True


@dataclass
class ProjectOverride:
    force_optional_plugins: list[str] = field(default_factory=list)
    extra_uat_args: list[str] = field(default_factory=list)


@dataclass
class UiSettings:
    poll_interval_ms: int = 1000


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    paths: PathsSettings = field(default_factory=PathsSettings)
    wine: WineSettings = field(default_factory=WineSettings)
    engines: dict[str, EngineSettings] = field(default_factory=_default_engines)
    resources: ResourceSettings = field(default_factory=ResourceSettings)
    cook: CookSettings = field(default_factory=CookSettings)
    outputs: OutputSettings = field(default_factory=OutputSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    ui: UiSettings = field(default_factory=UiSettings)
    projects: dict[str, ProjectOverride] = field(default_factory=dict)

    # --- resolved accessors -------------------------------------------------
    @property
    def ue_root(self) -> Path:
        return expand(self.paths.ue_root)

    @property
    def toolchain_root(self) -> Path:
        return expand(self.paths.toolchain_root)

    @property
    def project_roots(self) -> list[Path]:
        return [expand(p) for p in self.paths.project_roots]

    @property
    def prefix(self) -> Path:
        return expand(self.wine.prefix)

    def project_override(self, name: str) -> ProjectOverride:
        return self.projects.get(name, ProjectOverride())


# --- (de)serialization ----------------------------------------------------------


def _convert(tp: Any, value: Any) -> Any:
    origin = typing.get_origin(tp)
    if dataclasses.is_dataclass(tp):
        return _from_dict(tp, value if isinstance(value, dict) else {})
    if origin is dict:
        _key_t, val_t = typing.get_args(tp)
        return {str(k): _convert(val_t, v) for k, v in (value or {}).items()}
    if origin is list:
        (item_t,) = typing.get_args(tp)
        return [_convert(item_t, v) for v in (value or [])]
    if origin in (typing.Union, types.UnionType):
        return value
    if tp is bool:
        return bool(value)
    if tp is int:
        return int(value)
    if tp is float:
        return float(value)
    if tp is str:
        return str(value)
    return value


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(hints[f.name], data[f.name])
    return cls(**kwargs)


def _to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    return obj


def settings_path() -> Path:
    return xdg.config_dir() / SETTINGS_FILE


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    if not path.exists():
        return Settings()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return _from_dict(Settings, data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or settings_path()
    atomic_write_text(path, tomli_w.dumps(_to_dict(settings)))
    return path
