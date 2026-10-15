"""systemd --user transient units for builds and maintenance (invariant 3: never MemoryHigh)."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SLICE = "unrealbuilder.slice"
FORBIDDEN_PROPERTIES = ("MemoryHigh",)


class UnitArgError(ValueError):
    pass


@dataclass
class UnitResources:
    memory_max_gib: int = 20
    swap_max_gib: int = 2
    cpu_weight: int = 50
    io_weight: int = 50
    nice: int = 10
    allowed_cpus: str = ""
    runtime_max_hours: int = 4


@dataclass
class UnitSpec:
    unit: str
    description: str
    command: list[str]
    exec_stop_post: list[str] | None = None
    working_directory: Path | None = None
    resources: UnitResources = field(default_factory=UnitResources)
    environment: dict[str, str] = field(default_factory=dict)


def _check_arg(value: str) -> str:
    if "$" in value or "\n" in value:
        raise UnitArgError(f"Unsupported argument in a systemd property: {value!r}")
    return value


def systemd_run_argv(spec: UnitSpec) -> list[str]:
    r = spec.resources
    props = [
        "Type=exec",
        f"MemoryMax={r.memory_max_gib}G",
        f"MemorySwapMax={r.swap_max_gib}G",
        f"CPUWeight={r.cpu_weight}",
        f"IOWeight={r.io_weight}",
        f"Nice={r.nice}",
        f"RuntimeMaxSec={r.runtime_max_hours * 3600}",
        "TimeoutStopSec=45",
        "OOMPolicy=stop",
        # mixed: SIGTERM to the runner only, so it can `wineserver -k` the prefix while wineserver is
        # still alive; systemd then SIGKILLs whatever remains (Wine system processes ignore SIGTERM).
        "KillMode=mixed",
    ]
    if r.allowed_cpus:
        props.append(f"AllowedCPUs={r.allowed_cpus}")
    if spec.exec_stop_post:
        props.append("ExecStopPost=" + " ".join(shlex.quote(_check_arg(a)) for a in spec.exec_stop_post))
    argv = [
        "systemd-run",
        "--user",
        f"--unit={spec.unit}",
        f"--description={spec.description}",
        f"--slice={SLICE}",
        "--collect",
        "--quiet",
    ]
    for p in props:
        if p.split("=", 1)[0] in FORBIDDEN_PROPERTIES:
            raise UnitArgError(f"Forbidden property: {p}")
        argv += ["-p", p]
    if spec.working_directory:
        argv.append(f"--working-directory={_check_arg(str(spec.working_directory))}")
    for k, v in spec.environment.items():
        argv.append(f"--setenv={k}={_check_arg(v)}")
    argv += [_check_arg(a) for a in spec.command]
    return argv


SHOW_PROPS = [
    "Id",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainStatus",
    "ExecMainCode",
    "ControlGroup",
    "MemoryCurrent",
    "MemoryPeak",
    "MemorySwapCurrent",
    "InvocationID",
    "LoadState",
]


def parse_show(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def show_argv(unit: str, props: list[str] | None = None) -> list[str]:
    return ["systemctl", "--user", "show", unit, "-p", ",".join(props or SHOW_PROPS)]


def show(unit: str, props: list[str] | None = None) -> dict[str, str]:
    proc = subprocess.run(show_argv(unit, props), capture_output=True, text=True, timeout=10)
    return parse_show(proc.stdout)


def is_active(info: dict[str, str]) -> bool:
    return info.get("ActiveState") in ("active", "activating", "deactivating", "reloading")


def stop(unit: str) -> int:
    return subprocess.run(["systemctl", "--user", "stop", "--no-block", unit], capture_output=True, timeout=15).returncode


def set_property_argv(unit: str, **props: str) -> list[str]:
    for k in props:
        if k in FORBIDDEN_PROPERTIES:
            raise UnitArgError(f"Forbidden property: {k}")
    return ["systemctl", "--user", "set-property", "--runtime", unit, *(f"{k}={v}" for k, v in props.items())]


def list_units(pattern: str = "ub-*") -> list[dict[str, str]]:
    proc = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "--no-pager", pattern],
        capture_output=True,
        text=True,
        timeout=10,
    )
    units = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            units.append({"unit": parts[0], "load": parts[1], "active": parts[2], "sub": parts[3]})
    return units


def run_unit(spec: UnitSpec) -> subprocess.CompletedProcess:
    return subprocess.run(systemd_run_argv(spec), capture_output=True, text=True, timeout=30)
