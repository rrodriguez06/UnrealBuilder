"""Build slots for concurrency: one Wine prefix per concurrently running build.

Two builds cannot share a prefix: UAT holds a mutex per wineserver, and the prefix's wineserver/zenserver
live in the cgroup of the first build (they die with it). Slot N>0 uses `wineprefix-slotN`, a reflink clone
of the main prefix (free on btrfs, and it inherits the warm DDC/Zen shader cache), with its own Zen port.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import Settings

MAX_SLOTS = 2


@dataclass(frozen=True)
class Slot:
    index: int
    prefix: Path
    zen_port: int

    @property
    def exists(self) -> bool:
        return (self.prefix / "system.reg").is_file()


def slots(settings: Settings) -> list[Slot]:
    layout = ToolchainLayout.from_settings(settings)
    out = [Slot(0, layout.prefix, settings.cook.zen_port)]
    for i in range(1, MAX_SLOTS):
        out.append(Slot(i, layout.prefix.with_name(f"{layout.prefix.name}-slot{i}"), settings.cook.zen_port + i))
    return out


def free_slot(settings: Settings, busy_prefixes: set[str]) -> Slot | None:
    """First slot (within the configured concurrency) whose prefix is not used by a running build."""
    for slot in slots(settings)[: max(1, settings.resources.max_concurrent_builds)]:
        if str(slot.prefix) not in busy_prefixes and (slot.index == 0 or slot.exists):
            return slot
    return None


def workers_for(settings: Settings, requested: int) -> int:
    """With N concurrent builds, each one gets its share of the shader workers."""
    n = max(1, settings.resources.max_concurrent_builds)
    return max(1, requested // n) if n > 1 else requested
