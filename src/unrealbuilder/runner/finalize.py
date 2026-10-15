"""ExecStopPost of build units: always leaves a correct result.json behind.

systemd passes $SERVICE_RESULT (success, exit-code, signal, timeout, oom-kill, core-dump…),
$EXIT_CODE and $EXIT_STATUS. When the runner was killed it could not write its own result.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from unrealbuilder.core.cgroup import read_stats, self_cgroup
from unrealbuilder.core.jobs import BuildPaths, JobState
from unrealbuilder.core.notify import gui_alive, notify
from unrealbuilder.core.paths import ToolchainLayout, safe_rmtree
from unrealbuilder.core.settings import load_settings
from unrealbuilder.core.wineenv import wine_env


def resolve_state(runner_result: dict | None, service_result: str, cancel_requested: bool, oom_kills: int) -> JobState:
    if cancel_requested:
        return JobState.CANCELLED
    if service_result == "oom-kill" or oom_kills > 0:
        return JobState.OOM_KILLED
    if service_result == "timeout":
        return JobState.TIMEOUT
    if runner_result and runner_result.get("state"):
        return JobState(runner_result["state"])
    return JobState.FAILED


MESSAGES = {
    JobState.OOM_KILLED: "Build killed for lack of memory (lower the shader workers or raise MemoryMax)",
    JobState.TIMEOUT: "Maximum build duration exceeded",
    JobState.CANCELLED: "Build cancelled",
}


def finalize(build_dir: Path) -> int:
    paths = BuildPaths(build_dir)
    service_result = os.environ.get("SERVICE_RESULT", "")
    exit_code = os.environ.get("EXIT_CODE", "")
    exit_status = os.environ.get("EXIT_STATUS", "")
    runner_result = paths.read_result()
    cg = self_cgroup()
    stats = read_stats(cg) if cg else None
    oom_kills = stats.oom_kills if stats else 0

    state = resolve_state(runner_result, service_result, paths.cancel_marker.exists(), oom_kills)
    result = dict(runner_result or {})
    result.setdefault("job_id", paths.root.name)
    if runner_result is None:
        result["message"] = MESSAGES.get(state, f"The runner stopped without a result ({service_result or 'unknown'})")
        result["failed_step"] = (paths.read_status() or {}).get("step")
        result["finished_at"] = time.time()
    elif state != JobState(runner_result.get("state", JobState.FAILED.value)):
        result["message"] = MESSAGES.get(state, result.get("message", ""))
    result["state"] = state.value
    result["service_result"] = service_result
    result["exit_code_service"] = exit_code
    result["exit_status_service"] = exit_status
    if stats and stats.memory_peak:
        result["memory_peak"] = max(stats.memory_peak, result.get("memory_peak") or 0)
    result["finalized_at"] = time.time()

    status = paths.read_status()
    if status:
        status["state"] = state.value
        status["step"] = None
        paths.write_status(status)

    _cleanup_prefix(paths)

    if not result.get("notified_by") and not gui_alive():
        _notify(paths, state, result)
        result["notified_by"] = "runner"
    paths.write_result(result)
    return 0


def _cleanup_prefix(paths: BuildPaths) -> None:
    try:
        settings = load_settings()
        layout = ToolchainLayout.from_settings(settings)
        spec = paths.load_spec()
        prefix = Path(spec.prefix) if spec.prefix else layout.prefix
        env = wine_env(layout, prefix=prefix)
        subprocess.run([settings.wine.wineserver, "-k"], env=env, timeout=30, capture_output=True)
        work = ToolchainLayout(layout.root, prefix, layout.ue_root, layout.drive).shader_working_dir
        if work.is_dir():
            for child in work.iterdir():
                try:
                    safe_rmtree(child, [work])
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 - cleanup must never fail the finalize step
        pass


def _notify(paths: BuildPaths, state: JobState, result: dict) -> None:
    try:
        settings = load_settings()
        n = settings.notifications
        if state == JobState.SUCCEEDED and not n.on_success:
            return
        if state != JobState.SUCCEEDED and not n.on_failure:
            return
    except Exception:  # noqa: BLE001
        pass
    title = f"UnrealBuilder — {state.label}"
    body = f"{paths.root.parent.name} · {paths.root.name}\n{result.get('message', '')}"
    if result.get("zip"):
        body += f"\n{Path(result['zip']).name}"
    notify(title, body, "normal" if state == JobState.SUCCEEDED else "critical")

