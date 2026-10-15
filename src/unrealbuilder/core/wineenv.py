"""The single place that builds the environment for every Wine invocation.

Invariants (see CONTRIBUTING.md):
- the prefix must live under the toolchain root and can never be ~/.wine;
- d3dcompiler_47 is always forced to the native (Microsoft) DLL;
- builds run headless: DISPLAY / WAYLAND_DISPLAY are removed.
"""

from __future__ import annotations

import os
from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout, is_within, wine_home_prefix
from unrealbuilder.core.settings import EngineSettings, expand

DLL_OVERRIDES = "d3dcompiler_47=n"
HEADLESS_DROP = ("DISPLAY", "WAYLAND_DISPLAY")


class UnsafePrefixError(RuntimeError):
    pass


def check_prefix(prefix: Path, layout: ToolchainLayout) -> Path:
    real = Path(os.path.realpath(expand(prefix)))
    home_wine = Path(os.path.realpath(wine_home_prefix()))
    if real == home_wine or is_within(real, home_wine):
        raise UnsafePrefixError("The ~/.wine prefix is off-limits (it is the user's own Wine prefix)")
    root = Path(os.path.realpath(layout.root))
    if not is_within(real, root) or real == root:
        raise UnsafePrefixError(f"Prefix {real} must be inside {root}")
    return real


def merge_dll_overrides(existing: str | None) -> str:
    entries = [e for e in (existing or "").split(";") if e and not e.lower().startswith("d3dcompiler_47=")]
    return ";".join([DLL_OVERRIDES, *entries])


def wine_env(
    layout: ToolchainLayout,
    engine: EngineSettings | None = None,
    prefix: Path | None = None,
    headless: bool = True,
    base: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    real_prefix = check_prefix(prefix or layout.prefix, layout)
    env["WINEPREFIX"] = str(real_prefix)
    env["WINEDEBUG"] = "-all"
    env["WINEDLLOVERRIDES"] = merge_dll_overrides(env.get("WINEDLLOVERRIDES"))
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    if engine is not None and engine.autosdk_root:
        env["UE_SDKS_ROOT"] = layout.to_win(engine.autosdk_root)
    if headless:
        for key in HEADLESS_DROP:
            env.pop(key, None)
    if extra:
        env.update(extra)
    return env
