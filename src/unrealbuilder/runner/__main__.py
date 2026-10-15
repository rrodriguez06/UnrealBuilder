"""Entry point executed inside systemd units.

    python -m unrealbuilder.runner run <build_dir>        (ExecStart of ub-build-*.service)
    python -m unrealbuilder.runner finalize <build_dir>   (ExecStopPost; runs in every outcome)
    python -m unrealbuilder.runner maint <maint_dir>      (ExecStart of ub-maint-*.service)
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("run", "finalize", "maint"):
        print("usage: python -m unrealbuilder.runner run|finalize|maint <dir>", file=sys.stderr)
        return 2
    build_dir = Path(argv[1])
    if argv[0] == "maint":
        from unrealbuilder.runner.maintenance import run_maint

        return run_maint(build_dir)
    if argv[0] == "run":
        from unrealbuilder.runner.build import run_build

        return run_build(build_dir)
    from unrealbuilder.runner.finalize import finalize

    return finalize(build_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
