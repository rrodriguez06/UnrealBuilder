"""Entry point: `unrealbuilder` opens the GUI, `unrealbuilder <command>` runs the CLI.

The CLI path must stay importable without Qt, so GUI modules are imported lazily.
"""

from __future__ import annotations

import sys

from unrealbuilder import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"unrealbuilder {__version__}")
        return 0
    if args and args[0] == "gui":
        args = args[1:]
    elif args and not args[0].startswith("-"):
        from unrealbuilder.cli import cli_commands, run_cli

        if args[0] in cli_commands():
            return run_cli(args)
        print(f"Unknown command: {args[0]} (commands: {', '.join(sorted(cli_commands()))})", file=sys.stderr)
        return 2
    from unrealbuilder.gui.app import run_gui

    return run_gui(args)
