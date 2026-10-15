"""XDG base directories for UnrealBuilder (config, state, runtime)."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = "unrealbuilder"


def _base(env: str, default: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else Path.home() / default


def config_dir() -> Path:
    return _base("XDG_CONFIG_HOME", ".config") / APP_DIR


def state_dir() -> Path:
    return _base("XDG_STATE_HOME", ".local/state") / APP_DIR


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(value) if value else Path("/tmp") / f"unrealbuilder-{os.getuid()}"
    return base / APP_DIR
