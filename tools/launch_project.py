"""Launch a build for a project outside the configured roots: python tools/launch_project.py <dir> [Config]."""

import sys
from pathlib import Path

from unrealbuilder.core.discovery import ProjectInfo
from unrealbuilder.core.launcher import BuildOptions, launch, make_spec
from unrealbuilder.core.settings import load_settings
from unrealbuilder.core.uproject import ProjectDescriptor

project_dir = Path(sys.argv[1]).expanduser().resolve()
config = sys.argv[2] if len(sys.argv) > 2 else "Development"
uproject = next(project_dir.glob("*.uproject"))
info = ProjectInfo(uproject.stem, project_dir, uproject, ProjectDescriptor.load(uproject))
settings = load_settings()
spec = make_spec(settings, info, BuildOptions(configuration=config))
paths, unit, proc = launch(settings, spec)
print(paths.root if proc.returncode == 0 else f"systemd-run failed: {proc.stderr}")
