"""The build pipeline, executed inside a `ub-build-*.service` transient unit.

Steps: preflight → sync (+patch) → clean → build → cook/stage/pak/archive → post → smoke → zip → summary.
The runner owns status.json and writes result.json on every exit path it controls;
`finalize` (ExecStopPost) completes result.json when the runner was killed (OOM, timeout, stop).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import traceback
import zipfile
from pathlib import Path

from unrealbuilder.core.cgroup import read_stats, self_cgroup
from unrealbuilder.core.discovery import ProjectInfo
from unrealbuilder.core.doctor.winereg import d3dcompiler_is_native
from unrealbuilder.core.engines import EnginePair, engine_for_association
from unrealbuilder.core.fsutil import atomic_write_json, atomic_write_text
from unrealbuilder.core.jobs import STEPS, BuildPaths, JobSpec, JobState
from unrealbuilder.core.paths import ToolchainLayout, safe_rmtree
from unrealbuilder.core.prepare import prepare_mirror
from unrealbuilder.core.settings import Settings, load_settings
from unrealbuilder.core.summary import first_error, summarize
from unrealbuilder.core.uat import build_step_argv, cook_step_argv, find_game_launch, smoke_argv
from unrealbuilder.core.uproject import ProjectAnalysis, ProjectDescriptor
from unrealbuilder.core.wineenv import wine_env

EXIT_RE = re.compile(rb"AutomationTool exiting with ExitCode=(-?\d+)")
MIN_FREE_GIB = int(os.environ.get("UB_MIN_FREE_GIB", "20"))
WINESERVER_WAIT_S = 60

log = logging.getLogger("unrealbuilder.runner")


class StepFailed(Exception):
    def __init__(self, step: str, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.exit_code = exit_code


class Cancelled(Exception):
    pass


def setup_logging(paths: BuildPaths) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(paths.log("runner.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger("unrealbuilder")
    root.handlers[:] = [fh, sh]
    root.setLevel(logging.INFO)


class BuildRunner:
    def __init__(self, build_dir: Path, settings: Settings | None = None) -> None:
        self.paths = BuildPaths(build_dir)
        self.spec: JobSpec = self.paths.load_spec()
        self.settings = settings or load_settings()
        self.layout = ToolchainLayout.from_settings(self.settings)
        self.prefix = Path(self.spec.prefix) if self.spec.prefix else self.layout.prefix
        self.engine: EnginePair | None = None
        self.analysis: ProjectAnalysis | None = None
        self.cancelled = False
        self._child: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop_heartbeat = threading.Event()
        self.warnings: list[str] = []
        self.status = {
            "job_id": self.spec.job_id,
            "state": JobState.RUNNING.value,
            "pid": os.getpid(),
            "started_at": time.time(),
            "heartbeat": time.time(),
            "step": None,
            "message": "",
            "steps": {s: {"state": "pending"} for s in STEPS},
        }

    # --- status -------------------------------------------------------------
    def _write_status(self) -> None:
        with self._lock:
            self.status["heartbeat"] = time.time()
            self.paths.write_status(self.status)

    def _heartbeat(self) -> None:
        while not self._stop_heartbeat.wait(5):
            self._write_status()

    def _step(self, name: str, state: str, **extra) -> None:
        entry = self.status["steps"][name]
        entry["state"] = state
        now = time.time()
        if state == "running":
            entry["started"] = now
            self.status["step"] = name
        elif state in ("done", "failed", "skipped", "warning"):
            entry.setdefault("started", now)
            entry["ended"] = now
        entry.update(extra)
        self._write_status()

    def _message(self, text: str) -> None:
        log.info(text)
        self.status["message"] = text
        self._write_status()

    # --- signals ------------------------------------------------------------
    def _on_signal(self, signum, _frame) -> None:
        log.warning("Received signal %s: cancelling", signum)
        self.cancelled = True
        child = self._child
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except OSError:
                pass

    # --- process helpers ------------------------------------------------------
    def wine_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return wine_env(self.layout, self.engine.settings if self.engine else None, prefix=self.prefix, extra=extra)

    def _wineserver(self, *args: str, timeout: float = WINESERVER_WAIT_S) -> None:
        env = self.wine_env()
        try:
            subprocess.run([self.settings.wine.wineserver, *args], env=env, timeout=timeout, capture_output=True)
        except subprocess.TimeoutExpired:
            log.warning("wineserver %s: timed out, force-killing the prefix", " ".join(args))
            subprocess.run([self.settings.wine.wineserver, "-k"], env=env, timeout=30, capture_output=True)

    def run_logged(self, argv: list[str], log_path: Path, env: dict[str, str], timeout: float | None = None) -> int:
        """Run a command with stdout+stderr appended to `log_path`; honour cancellation."""
        log.info("$ %s", shlex.join(argv))
        with open(log_path, "ab") as out:
            self._child = subprocess.Popen(
                argv, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=self.layout.root, start_new_session=True
            )
            start = time.monotonic()
            try:
                while True:
                    try:
                        rc = self._child.wait(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancelled:
                            self._kill_child()
                            raise Cancelled() from None
                        if timeout is not None and time.monotonic() - start > timeout:
                            self._kill_child()
                            return -9
            finally:
                self._child = None
        self._wineserver("-w")
        if self.cancelled:
            raise Cancelled()
        return rc

    def _kill_child(self) -> None:
        child = self._child
        if child is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, sig)
            except OSError:
                pass
            try:
                child.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        self._wineserver("-k", timeout=30)

    @staticmethod
    def uat_exit_code(log_path: Path, rc: int) -> int:
        try:
            matches = EXIT_RE.findall(log_path.read_bytes())
        except OSError:
            matches = []
        return int(matches[-1]) if matches else rc

    # --- pipeline -----------------------------------------------------------
    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        hb = threading.Thread(target=self._heartbeat, daemon=True)
        hb.start()
        result: dict = {"job_id": self.spec.job_id, "written_by": "runner"}
        try:
            for step in STEPS:
                if self.cancelled:
                    raise Cancelled()
                getattr(self, f"step_{step}")()
            result.update(state=JobState.SUCCEEDED.value, message="Build finished")
            rc = 0
        except Cancelled:
            result.update(state=JobState.CANCELLED.value, message="Build cancelled", failed_step=self.status["step"])
            rc = 130
        except StepFailed as exc:
            self._step(exc.step, "failed", error=exc.message)
            result.update(state=JobState.FAILED.value, failed_step=exc.step, message=exc.message, exit_code=exc.exit_code)
            rc = 1
        except Exception as exc:  # noqa: BLE001 - last-resort reporting
            log.error("Internal error:\n%s", traceback.format_exc())
            step = self.status["step"] or "preflight"
            self._step(step, "failed", error=str(exc))
            result.update(state=JobState.FAILED.value, failed_step=step, message=f"Internal error: {exc}")
            rc = 1
        finally:
            self._stop_heartbeat.set()
        result["finished_at"] = time.time()
        result["warnings"] = self.warnings
        result.update(self._outputs())
        stats = self._cgroup_peak()
        if stats:
            result.update(stats)
        self.status["state"] = result["state"]
        self.status["step"] = None
        self._write_status()
        self.paths.write_result(result)
        log.info("Result: %s — %s", result["state"], result.get("message"))
        return rc

    def _outputs(self) -> dict:
        archive = self.paths.archive(self.spec)
        zips = self.paths.zips()
        out: dict = {}
        if archive.is_dir():
            out["archive"] = str(archive)
        if zips:
            out["zip"] = str(zips[-1])
            out["zip_size"] = zips[-1].stat().st_size
        return out

    @staticmethod
    def _cgroup_peak() -> dict:
        cg = self_cgroup()
        if cg is None:
            return {}
        s = read_stats(cg)
        return {"memory_peak": s.memory_peak, "swap_peak_now": s.swap_current, "oom_kills": s.oom_kills}

    # --- steps --------------------------------------------------------------
    def step_preflight(self) -> None:
        self._step("preflight", "running")
        spec = self.spec
        desc = ProjectDescriptor.load(Path(spec.project_dir) / spec.uproject)
        self.engine = engine_for_association(self.settings, desc.engine_association)
        if self.engine is None:
            raise StepFailed("preflight", f"No engine configured for version {desc.engine_association}")
        if not self.engine.win.exists:
            raise StepFailed("preflight", f"Windows engine not found: {self.engine.win.root}")
        if self.engine.changelists_match is False and not spec.allow_cl_mismatch:
            lv, wv = self.engine.linux.build_version, self.engine.win.build_version
            raise StepFailed(
                "preflight",
                f"Engine versions differ: Linux {lv.label if lv else '?'} / Windows {wv.label if wv else '?'}. "
                "Update the Windows engine (Toolchain page).",
            )
        self.wine_env()  # raises UnsafePrefixError for a forbidden prefix
        if not d3dcompiler_is_native(self.prefix):
            raise StepFailed(
                "preflight",
                "The Wine prefix does not force d3dcompiler_47=native: SM5 shaders would be compiled by Wine. "
                "Fix it from the Toolchain page.",
            )
        free_gib = shutil.disk_usage(self.layout.root).free / 2**30
        if free_gib < MIN_FREE_GIB:
            raise StepFailed("preflight", f"Not enough disk space: {free_gib:.1f} GiB free (minimum {MIN_FREE_GIB})")
        self._step("preflight", "done")

    def step_sync(self) -> None:
        self._step("sync", "running")
        spec = self.spec
        info = ProjectInfo(spec.project, Path(spec.project_dir), Path(spec.project_dir) / spec.uproject, None)
        info.descriptor = ProjectDescriptor.load(info.uproject)
        res = prepare_mirror(
            self.settings,
            info,
            spec.configuration,
            apply=True,
            shader_workers=spec.shader_workers,
            memory_max_gib=spec.memory_max_gib,
            zen_port=spec.zen_port,
            progress=lambda done, total: self._message(f"Mirror sync: {done}/{total}"),
        )
        self.analysis = res.analysis
        if res.analysis.blockers:
            raise StepFailed("sync", " ; ".join(res.analysis.blockers))
        self.warnings += res.analysis.warnings
        self.warnings += (res.plan.warnings if res.plan else [])[:50]
        self.mirror = res.mirror
        self.uproject = res.mirror / spec.uproject
        sync = res.sync
        self._write_commands()
        self._step(
            "sync",
            "done",
            copied=sync.copied if sync else 0,
            reflinked=sync.reflinked if sync else 0,
            deleted=sync.deleted if sync else 0,
            patched=res.patched,
            kind=res.analysis.kind_label,
            optional_patches=res.analysis.optional_patches,
            needs_build=res.analysis.needs_build_step,
        )

    def step_clean(self) -> None:
        if not self.spec.clean:
            self._step("clean", "skipped")
            return
        self._step("clean", "running")
        for rel in ("Intermediate", "Saved", "Binaries/Win64", "DerivedDataCache"):
            target = self.mirror / rel
            if target.exists():
                self._message(f"Deleting {rel} in the mirror")
                safe_rmtree(target, [self.mirror])
        self._step("clean", "done")

    def step_build(self) -> None:
        assert self.analysis is not None and self.engine is not None
        if not self.analysis.needs_build_step:
            self._step("build", "skipped", reason="project has no code to compile")
            return
        self._step("build", "running")
        log_path = self.paths.log("uat-build.log")
        env = self.wine_env({"uebp_LogFolder": self.layout.to_win(self.paths.logs / "uat-build")})
        argv = build_step_argv(
            self.settings.wine.wine,
            self.layout,
            self.engine.win.root,
            self.uproject,
            self.spec.configuration,
            self.spec.extra_uat_args,
            compile_editor=self.analysis.has_code,
        )
        rc = self.uat_exit_code(log_path, self.run_logged(argv, log_path, env))
        if rc != 0:
            raise StepFailed("build", first_error(log_path) or f"Compilation failed (code {rc})", rc)
        self._step("build", "done", exit_code=rc)

    def step_cook(self) -> None:
        assert self.engine is not None
        self._step("cook", "running")
        log_path = self.paths.log("uat-cook.log")
        env = self.wine_env({"uebp_LogFolder": self.layout.to_win(self.paths.logs / "uat-cook")})
        argv = cook_step_argv(
            self.settings.wine.wine,
            self.layout,
            self.engine.win.root,
            self.uproject,
            self.spec.configuration,
            self.paths.archive(self.spec),
            incremental=self.spec.incremental_cook,
            nodebuginfo=self.spec.nodebuginfo,
            extra=self.spec.extra_uat_args,
        )
        rc = self.uat_exit_code(log_path, self.run_logged(argv, log_path, env))
        if rc != 0:
            raise StepFailed("cook", first_error(log_path) or f"Cook failed (code {rc})", rc)
        if not self.paths.archive(self.spec).is_dir():
            raise StepFailed("cook", "UAT succeeded but the archive folder is missing")
        self._step("cook", "done", exit_code=rc)

    def step_post(self) -> None:
        self._step("post", "running")
        if self.spec.delete_staged_after_archive:
            staged = self.mirror / "Saved" / "StagedBuilds"
            if staged.exists():
                safe_rmtree(staged, [self.mirror])
        size = sum(f.stat().st_size for f in self.paths.archive(self.spec).rglob("*") if f.is_file())
        self._step("post", "done", archive_size=size)

    def step_smoke(self) -> None:
        if not self.spec.smoke_test:
            self._step("smoke", "skipped")
            return
        self._step("smoke", "running")
        archive = self.paths.archive(self.spec)
        found = find_game_launch(archive, self.spec.project, self.spec.configuration)
        if found is None:
            self.warnings.append("Startup test: game executable not found")
            self._step("smoke", "warning", error="executable not found")
            return
        log_path = self.paths.log("smoke.log")
        shipping = self.spec.configuration == "Shipping"
        exe, exe_args = found
        ok, reason = self._smoke(exe, exe_args, log_path, shipping)
        for saved in (archive / self.spec.project / "Saved", archive / "Engine" / "Saved"):
            if saved.exists():
                safe_rmtree(saved, [archive])
        if ok:
            self._step("smoke", "done", detail=reason)
        else:
            self.warnings.append(f"Startup test inconclusive: {reason}")
            self._step("smoke", "warning", error=reason)

    def _smoke(self, exe: Path, exe_args: list[str], log_path: Path, shipping: bool) -> tuple[bool, str]:
        env = self.wine_env()
        argv = smoke_argv(self.settings.wine.wine, self.layout, exe, exe_args)
        log.info("$ %s", shlex.join(argv))
        deadline = time.monotonic() + self.spec.smoke_timeout_s
        alive_needed = 20.0
        with open(log_path, "ab") as out:
            self._child = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=self.layout.root, start_new_session=True)
            started = time.monotonic()
            verdict: tuple[bool, str] | None = None
            try:
                while verdict is None:
                    if self.cancelled:
                        self._kill_child()
                        raise Cancelled()
                    rc = self._child.poll()
                    text = log_path.read_bytes()
                    if b"Fatal error" in text or b"Critical error" in text:
                        verdict = (False, "fatal error at startup")
                    elif not shipping and b"Engine is initialized" in text:
                        verdict = (True, "engine initialized, map loaded" if b"LoadMap" in text else "engine initialized")
                    elif rc is not None:
                        verdict = (False, f"the game exited (code {rc})")
                    elif shipping and time.monotonic() - started >= alive_needed:
                        verdict = (True, f"still running after {alive_needed:.0f} s (Shipping: no log)")
                    elif time.monotonic() > deadline:
                        verdict = (False, f"no initialization within {self.spec.smoke_timeout_s} s")
                    else:
                        time.sleep(1)
            finally:
                self._kill_child()
                self._child = None
        return verdict

    def step_zip(self) -> None:
        if not self.spec.zip:
            self._step("zip", "skipped")
            return
        self._step("zip", "running")
        name = self.spec.archive_name
        stamp = time.strftime("%Y%m%d-%H%M")
        zip_path = self.paths.root / f"{name}-{stamp}.zip"
        seven = shutil.which("7z")
        if seven:
            argv = [seven, "a", "-tzip", "-mx=5", "-mmt=on", "-bso0", "-bsp0", str(zip_path), name]
            if self.spec.zip_exclude_pdb:
                argv.append("-xr!*.pdb")
            rc = subprocess.run(argv, cwd=self.paths.root, capture_output=True, text=True)
            if rc.returncode != 0:
                raise StepFailed("zip", f"7z failed: {rc.stderr.strip()[:300]}")
        else:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
                for f in sorted(self.paths.archive(self.spec).rglob("*")):
                    if f.is_file() and not (self.spec.zip_exclude_pdb and f.suffix.lower() == ".pdb"):
                        zf.write(f, f.relative_to(self.paths.root))
        self._step("zip", "done", zip=str(zip_path), size=zip_path.stat().st_size)

    def step_summary(self) -> None:
        self._step("summary", "running")
        summary = summarize(self.paths)
        data = summary.to_json()
        data["analysis"] = {
            "kind": self.analysis.kind_label if self.analysis else None,
            "code_based_reason": self.analysis.code_based_reason if self.analysis else None,
            "optional_patches": self.analysis.optional_patches if self.analysis else [],
        }
        steps = {k: dict(v) for k, v in self.status["steps"].items()}
        steps["summary"].update(state="done", ended=time.time())
        data["steps"] = steps
        data["warnings"] = self.warnings
        atomic_write_json(self.paths.summary, data)
        self._step("summary", "done", errors=summary.errors, warnings=summary.warnings)

    # --- reproducibility ------------------------------------------------------
    def _write_commands(self) -> None:
        assert self.engine is not None
        mirror = self.layout.mirror_dir(self.spec.project)
        uproject = mirror / self.spec.uproject
        env = self.wine_env()
        keep = ("WINEPREFIX", "WINEDEBUG", "WINEDLLOVERRIDES", "UE_SDKS_ROOT", "DOTNET_CLI_TELEMETRY_OPTOUT")
        lines = ["#!/usr/bin/env bash", "# Commands equivalent to this build (generated by UnrealBuilder)", "set -e", "unset DISPLAY WAYLAND_DISPLAY"]
        lines += [f"export {k}={shlex.quote(env[k])}" for k in keep if k in env]
        lines.append(f"cd {shlex.quote(str(self.layout.root))}")
        b = build_step_argv(
            self.settings.wine.wine,
            self.layout,
            self.engine.win.root,
            uproject,
            self.spec.configuration,
            self.spec.extra_uat_args,
            compile_editor=bool(self.analysis and self.analysis.has_code),
        )
        c = cook_step_argv(
            self.settings.wine.wine,
            self.layout,
            self.engine.win.root,
            uproject,
            self.spec.configuration,
            self.paths.archive(self.spec),
            incremental=self.spec.incremental_cook,
            nodebuginfo=self.spec.nodebuginfo,
            extra=self.spec.extra_uat_args,
        )
        lines += ["", "# Compile (if the project needs compiling)", "uebp_LogFolder=" + shlex.quote(self.layout.to_win(self.paths.logs / "uat-build")) + " " + shlex.join(b)]
        lines += ["", "# Cook / stage / pak / archive", "uebp_LogFolder=" + shlex.quote(self.layout.to_win(self.paths.logs / "uat-cook")) + " " + shlex.join(c), ""]
        atomic_write_text(self.paths.commands, "\n".join(lines))
        os.chmod(self.paths.commands, 0o755)


def run_build(build_dir: Path) -> int:
    paths = BuildPaths(build_dir)
    setup_logging(paths)
    try:
        runner = BuildRunner(build_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not load the job: %s", exc)
        paths.write_result({"state": JobState.FAILED.value, "failed_step": "preflight", "message": f"Unreadable job: {exc}", "written_by": "runner", "finished_at": time.time()})
        return 1
    return runner.run()
