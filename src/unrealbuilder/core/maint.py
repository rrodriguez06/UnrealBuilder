"""Maintenance jobs (installs, fixes, dedupe, cleanup) run as `ub-maint-*` systemd units.

state_dir/maint/<id>/  action.json  status.json  result.json  log.txt
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core import xdg
from unrealbuilder.core.fsutil import atomic_write_json, read_json

ACTION_LABELS = {
    "prefix-create": "Create Wine prefix",
    "drive-link": "Link U: drive",
    "dll-override": "d3dcompiler_47 override",
    "vctip": "Disable MSVC telemetry",
    "vcredist": "Visual C++ runtime",
    "autosdk-links": "AutoSDK links",
    "msvc-install": "Install MSVC + Windows SDK",
    "netfx-install": "Install .NET Framework SDK",
    "engine-install": "Install Windows engine",
    "engine-update": "Update Windows engine",
    "dedupe": "btrfs deduplication",
    "cleanup": "Cleanup",
    "slot-create": "Create parallel build slot",
}
WINE_ACTIONS = {"prefix-create", "dll-override", "vctip", "vcredist", "engine-install", "engine-update", "slot-create"}


def maint_root() -> Path:
    return xdg.state_dir() / "maint"


@dataclass
class MaintPaths:
    root: Path

    @property
    def action_file(self) -> Path:
        return self.root / "action.json"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def result(self) -> Path:
        return self.root / "result.json"

    @property
    def log(self) -> Path:
        return self.root / "log.txt"

    @property
    def job_id(self) -> str:
        return self.root.name

    def action(self) -> dict:
        return read_json(self.action_file, default={}) or {}

    def read_status(self) -> dict:
        return read_json(self.status, default={}) or {}

    def read_result(self) -> dict | None:
        return read_json(self.result, default=None)

    def write_status(self, data: dict) -> None:
        atomic_write_json(self.status, data)

    def write_result(self, data: dict) -> None:
        atomic_write_json(self.result, data)


def split_action(action: str) -> tuple[str, str | None]:
    name, _, arg = action.partition(":")
    return name, (arg or None)


def create_maint(action: str, params: dict | None = None) -> MaintPaths:
    name, _arg = split_action(action)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = maint_root() / f"{stamp}-{name}"
    n = 1
    while root.exists():
        n += 1
        root = maint_root() / f"{stamp}-{name}-{n}"
    root.mkdir(parents=True)
    paths = MaintPaths(root)
    atomic_write_json(paths.action_file, {"action": action, "params": params or {}, "created_at": time.time()})
    return paths


def list_maint(limit: int = 50) -> list[MaintPaths]:
    root = maint_root()
    if not root.is_dir():
        return []
    dirs = sorted((p for p in root.iterdir() if (p / "action.json").exists()), key=lambda p: p.name, reverse=True)
    return [MaintPaths(p) for p in dirs[:limit]]


def label(action: str) -> str:
    name, arg = split_action(action)
    base = ACTION_LABELS.get(name, name)
    return f"{base} ({arg})" if arg else base
