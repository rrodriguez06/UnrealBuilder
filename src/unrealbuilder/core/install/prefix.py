"""Wine prefix provisioning (the dedicated prefix only: wine_env refuses ~/.wine)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import Settings, expand
from unrealbuilder.core.wineenv import wine_env

Run = Callable[[list[str], dict[str, str] | None], int]

REG_OVERRIDES = [
    ["reg", "add", r"HKCU\Software\Wine\DllOverrides", "/v", "d3dcompiler_47", "/t", "REG_SZ", "/d", "native", "/f"],
]
REG_VCTIP = [
    ["reg", "add", r"HKLM\SOFTWARE\Microsoft\VSCommon\17.0\SQM", "/f", "/v", "OptIn", "/t", "REG_DWORD", "/d", "0"],
    ["reg", "add", r"HKLM\SOFTWARE\Wow6432Node\Microsoft\VSCommon\17.0\SQM", "/f", "/v", "OptIn", "/t", "REG_DWORD", "/d", "0"],
]


def ensure_drive_link(layout: ToolchainLayout) -> Path:
    dosdevices = layout.prefix / "dosdevices"
    dosdevices.mkdir(parents=True, exist_ok=True)
    link = dosdevices / f"{layout.drive}:"
    if link.is_symlink() or link.exists():
        if os.path.realpath(link) == os.path.realpath(layout.ue_root):
            return link
        link.unlink()
    link.symlink_to(layout.ue_root)
    return link


def ensure_autosdk_links(layout: ToolchainLayout, autosdk_root: Path) -> list[Path]:
    """sdks/HostWin64/Win64/{VS2022 -> msvc/VC/Tools/MSVC, Windows Kits -> msvc/Windows Kits, DIA SDK/VS2022}."""
    win64 = autosdk_root / "HostWin64" / "Win64"
    (win64 / "DIA SDK").mkdir(parents=True, exist_ok=True)
    targets = {
        win64 / "VS2022": layout.msvc / "VC" / "Tools" / "MSVC",
        win64 / "Windows Kits": layout.msvc / "Windows Kits",
        win64 / "DIA SDK" / "VS2022": layout.msvc / "DIA SDK",
    }
    made = []
    for link, target in targets.items():
        rel = os.path.relpath(target, link.parent)
        if link.is_symlink() and os.readlink(link) == rel:
            continue
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            continue  # a real directory: leave it alone
        link.symlink_to(rel)
        made.append(link)
    return made


def wine_cmd(settings: Settings, layout: ToolchainLayout, args: list[str], run: Run, extra_env: dict[str, str] | None = None) -> int:
    env = wine_env(layout, prefix=layout.prefix, extra=extra_env)
    return run([settings.wine.wine, *args], env)


def create_prefix(settings: Settings, layout: ToolchainLayout, run: Run) -> int:
    layout.prefix.mkdir(parents=True, exist_ok=True)
    # mscoree/mshtml disabled: no Mono/Gecko install prompts (UBT/UAT use the engine's bundled .NET).
    rc = wine_cmd(settings, layout, ["wineboot", "-i"], run, {"WINEARCH": "win64", "WINEDLLOVERRIDES": "mscoree,mshtml=;d3dcompiler_47=n"})
    if rc != 0:
        return rc
    env = wine_env(layout, prefix=layout.prefix)
    subprocess.run([settings.wine.wineserver, "-w"], env=env, timeout=120)
    return 0


def apply_registry(settings: Settings, layout: ToolchainLayout, commands: list[list[str]], run: Run) -> int:
    for args in commands:
        rc = wine_cmd(settings, layout, args, run)
        if rc != 0:
            return rc
    return 0


def install_vcredist(settings: Settings, layout: ToolchainLayout, run: Run) -> int:
    for eng in settings.engines.values():
        exe = expand(eng.win_root) / "Engine" / "Extras" / "Redist" / "en-us" / "vc_redist.x64.exe"
        if exe.is_file():
            return wine_cmd(settings, layout, [str(exe), "/quiet", "/norestart"], run)
    raise FileNotFoundError("vc_redist.x64.exe not found in the configured Windows engines")
