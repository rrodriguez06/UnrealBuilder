# Contributing to UnrealBuilder

UnrealBuilder is a desktop tool (Python 3.12+ and PySide6) that packages Unreal Engine projects authored on Linux into
Win64 builds, by driving a headless Windows UE install under Wine. See [README.md](README.md) for the user-facing
description.

Issues and pull requests are welcome. The tool has only been developed and tested on Arch Linux, so reports from other
distributions are especially useful: please include your distribution, Wine version and UE version.

## Commands

- Dev environment: `python -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]'`
- Run the GUI: `.venv/bin/unrealbuilder` (or `.venv/bin/python -m unrealbuilder`)
- CLI: `.venv/bin/unrealbuilder discover|analyze|sync|run-job|follow|status|cancel|doctor|install-desktop`
- Tests: `.venv/bin/pytest` (fast, no Wine, no systemd, no Qt display needed; Qt tests use `QT_QPA_PLATFORM=offscreen`)
- Opt-in test markers: `-m systemd` (a reachable `systemd --user`), `-m btrfs` (a btrfs directory in `UB_TEST_BTRFS_DIR`)
- Lint: `.venv/bin/ruff check .`
- Real end-to-end checks (they use the real toolchain and take minutes): `tools/gui_reattach_check.py`,
  `tools/concurrency_check.py`, `tools/launch_project.py <dir>` (a project outside the configured roots)
- Screenshots for the docs (offscreen, real data): `python tools/screenshots.py docs/screenshots`

## Layout

- `src/unrealbuilder/core/`: pure Python logic (settings, paths, Wine environment, engines, project analysis, mirror sync,
  patching, UAT argv, jobs/queue, systemd/cgroup, log parsing, doctor, install, storage). **Never imports Qt.**
- `src/unrealbuilder/runner/`: code executed *inside* the systemd build/maintenance units. **Never imports Qt.**
- `src/unrealbuilder/gui/`: PySide6 UI (services, models, pages, widgets).
- `tests/fixtures/logs/`: real logs from a validated run, used by the parser tests.

## Invariants

Each one is guarded by a test; do not break them.

1. Never touch `~/.wine` (the user's own Wine prefix). `wine_env()` refuses any prefix outside the toolchain root.
2. Always set `WINEDLLOVERRIDES=d3dcompiler_47=n` and keep the matching `HKCU\Software\Wine\DllOverrides` key.
   Otherwise Wine silently swaps Microsoft FXC for its builtin vkd3d HLSL compiler (hangs and wrong SM5 shaders).
3. Builds run in transient `systemd-run --user` units in `unrealbuilder.slice` with `--collect -p Type=exec
   -p MemoryMax -p MemorySwapMax` and **never `MemoryHigh`** (throttling pressure makes systemd-oomd kill the unit).
4. The GUI is never the parent process of a build; it reattaches to running units on startup.
5. The cook step always passes `-unrealexe=...\UnrealEditor-Cmd.exe`; wine/UAT arguments are separate argv items.
6. Every UAT invocation gets its own `uebp_LogFolder` (UAT clears its log folder at startup).
7. `core/` and `runner/` never import Qt.
8. One Wine prefix per concurrently running build (slots): UAT holds a mutex per wineserver, and a prefix's
   wineserver/zenserver die with the first build's cgroup. Units use `KillMode=mixed` so the runner can
   `wineserver -k` the prefix while wineserver is still alive.
9. C++ projects build their Win64 *editor* target too (the cook runs UnrealEditor-Cmd.exe, which loads project
   editor modules); that requires NETFXSDK (doctor check `netfx`).

## Where things live at runtime

- Toolchain root `~/Applications/UnrealEngine/win64-toolchain`: `wineprefix[-slotN]`, `msvc`, `msvc-wine`, `sdks`
  (AutoSDK symlinks), `projects/<P>` (mirrors), `builds/<P>/<stamp>-<Config>` (job.json, status.json, result.json,
  summary.json, commands.sh, logs/, archive, zip).
- State `~/.local/state/unrealbuilder/`: `queue.json`, `maint/<id>/` (maintenance jobs), `plugin-index/`.
- Settings `~/.config/unrealbuilder/settings.toml`.

## Conventions

- Everything in English: code, identifiers, comments, user-facing UI/CLI strings and docs.
  Sizes use binary units (`KiB`, `MiB`, `GiB`), dates use `%Y-%m-%d %H:%M`.
- Atomic JSON writes (`tmp` + `os.replace`) for every state file.
- Deletions only through `core.paths.safe_rmtree(path, allowed_roots)`.
