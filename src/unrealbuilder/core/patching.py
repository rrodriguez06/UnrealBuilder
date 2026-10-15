"""Patches applied to the mirror only (never to the user's Linux project).

- `.uproject`: plugin references made `"Optional": true` (editor-only plugins missing from the Win64 engine).
- `Config/DefaultEngine.ini` / `Config/DefaultEditor.ini`: an UnrealBuilder managed block, regenerated each build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from unrealbuilder.core.fsutil import atomic_write_bytes

BLOCK_BEGIN = "; >>> UnrealBuilder managed block - do not edit >>>"
BLOCK_END = "; <<< UnrealBuilder managed block <<<"
_BLOCK_RE = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"[ \t]*(\r?\n)?", re.S)
BOM = "﻿"


def _read_text(path: Path) -> tuple[str, bool, str]:
    """Return (text without BOM, had_bom, newline)."""
    if not path.exists():
        return "", False, "\r\n"
    raw = path.read_bytes().decode("utf-8", errors="replace")
    had_bom = raw.startswith(BOM)
    text = raw[1:] if had_bom else raw
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, had_bom, newline


def _write_text(path: Path, text: str, had_bom: bool) -> None:
    atomic_write_bytes(path, ((BOM if had_bom else "") + text).encode("utf-8"))


def patch_uproject(path: Path, optional_plugins: list[str]) -> bool:
    """Set `"Optional": true` on the given plugin references. Returns True if the file changed."""
    text, had_bom, newline = _read_text(path)
    data = json.loads(text)
    wanted = {n.lower() for n in optional_plugins}
    changed = False
    for ref in data.get("Plugins", []) or []:
        if str(ref.get("Name", "")).lower() in wanted and ref.get("Optional") is not True:
            ref["Optional"] = True
            changed = True
    if not changed:
        return False
    out = json.dumps(data, indent="\t", ensure_ascii=False)
    out = out.replace("\n", newline) + newline
    _write_text(path, out, had_bom)
    return True


def render_block(sections: dict[str, dict[str, str]], newline: str) -> str:
    lines = [BLOCK_BEGIN]
    for section, values in sections.items():
        lines.append(f"[{section}]")
        lines.extend(f"{k}={v}" for k, v in values.items())
    lines.append(BLOCK_END)
    return newline.join(lines) + newline


def apply_ini_block(path: Path, sections: dict[str, dict[str, str]]) -> bool:
    """Insert or replace the managed block at the end of an ini file. Returns True if the file changed.

    Placing it last makes its keys win over earlier duplicates (UE merges repeated sections).
    An empty `sections` removes the block.
    """
    text, had_bom, newline = _read_text(path)
    stripped = _BLOCK_RE.sub("", text)
    if sections:
        if stripped and not stripped.endswith(("\n", "\r")):
            stripped += newline
        if stripped and not stripped.endswith(newline * 2):
            stripped += newline
        new_text = stripped + render_block(sections, newline)
    else:
        new_text = stripped
    if new_text == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, new_text, had_bom)
    return True


def engine_ini_sections(nproc: int, shader_workers: int, zen_port: int | None, verbose_shader_progress: bool) -> dict:
    sections: dict[str, dict[str, str]] = {
        "DevOptions.Shaders": {"NumUnusedShaderCompilingThreads": str(max(0, nproc - max(1, shader_workers)))},
    }
    if verbose_shader_progress:
        sections["Core.Log"] = {"LogShaderCompilers": "Verbose"}
    if zen_port:
        sections["Zen.AutoLaunch"] = {"DesiredPort": str(zen_port)}
    return sections


def editor_ini_sections(memory_max_gib: int, shader_workers: int) -> dict:
    """Cooker GC guard: Wine reports host RAM, so cap 'used' memory below the cgroup MemoryMax.

    MemoryMinFreePhysical is zeroed because when both are set, both conditions must trigger.
    """
    budget_mib = int((memory_max_gib - shader_workers * 0.75 - 1.5) * 1024)
    budget_mib = max(budget_mib, 4096)
    return {"CookSettings": {"MemoryMaxUsedPhysical": str(budget_mib), "MemoryMinFreePhysical": "0"}}
