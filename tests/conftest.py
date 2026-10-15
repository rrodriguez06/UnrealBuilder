from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    """Never read or write the user's real ~/.config/unrealbuilder or ~/.local/state/unrealbuilder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def log_fixture(fixtures_dir):
    def _read(name: str) -> bytes:
        return (fixtures_dir / "logs" / name).read_bytes()

    return _read


@pytest.fixture
def btrfs_dir() -> Path:
    """A scratch directory on btrfs for real reflink tests (opt-in with -m btrfs)."""
    base = Path(os.environ.get("UB_TEST_BTRFS_DIR", Path.home() / ".cache" / "unrealbuilder-tests"))
    base.mkdir(parents=True, exist_ok=True)
    return base
