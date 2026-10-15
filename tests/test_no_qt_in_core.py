"""Invariant 7: core/ and runner/ never import Qt."""

import subprocess
import sys

SCRIPT = r"""
import importlib, pkgutil, sys
import unrealbuilder.core, unrealbuilder.runner
for pkg in (unrealbuilder.core, unrealbuilder.runner):
    for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        importlib.import_module(mod.name)
import unrealbuilder.cli
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "shiboken6", "PyQt6"))
print(",".join(bad))
"""


def test_core_and_runner_do_not_import_qt():
    out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"Qt imported by core/runner: {out.stdout}"
