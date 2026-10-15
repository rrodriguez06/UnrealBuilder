"""Supervisor: queue, launching, cancelling and reconciling jobs with systemd (GUI thread, async QProcess)."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from unrealbuilder.core import systemd
from unrealbuilder.core.cgroup import cgroup_dir, read_stats
from unrealbuilder.core.jobs import FINAL_STATES, BuildPaths, JobSpec, JobState
from unrealbuilder.core.jobstore import JobEntry, scan_jobs
from unrealbuilder.core.launcher import maint_unit_spec, prepare_job
from unrealbuilder.core.maint import MaintPaths, create_maint, list_maint
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.queue import BuildQueue, next_to_start
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.slots import free_slot, workers_for
from unrealbuilder.core.systemd import systemd_run_argv
from unrealbuilder.gui.services.notifications import notify_job_finished


class Supervisor(QObject):
    entriesChanged = Signal(list)  # list[JobEntry]
    maintChanged = Signal(list)  # list[(MaintPaths, active: bool)]
    jobStarted = Signal(object)  # JobEntry
    jobFinished = Signal(object)  # JobEntry
    error = Signal(str)

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.layout = ToolchainLayout.from_settings(settings)
        self.queue = BuildQueue.load()
        self.entries: list[JobEntry] = []
        self.active_units: set[str] = set()
        self.active_maint: set[str] = set()
        self._states: dict[str, JobState] = {}
        self._first_scan = True
        self._launching: set[str] = set()
        self._launching_prefixes: set[str] = set()
        self._list_proc: QProcess | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.apply_slice_budget()
        self.refresh()
        self.timer.start()

    def apply_slice_budget(self) -> None:
        """Runtime-only RAM budget shared by all builds (never MemoryHigh)."""
        r = self.settings.resources
        argv = systemd.set_property_argv("unrealbuilder.slice", MemoryMax=f"{r.ram_budget_gib}G", MemorySwapMax=f"{r.job_swap_max_gib * 2}G")
        QProcess.startDetached(argv[0], argv[1:])

    # --- reconciliation -------------------------------------------------------
    def refresh(self) -> None:
        if self._list_proc is not None:
            return
        proc = QProcess(self)
        self._list_proc = proc
        proc.finished.connect(lambda *_: self._on_units(proc))
        proc.start("systemctl", ["--user", "list-units", "--all", "--plain", "--no-legend", "--no-pager", "ub-*"])

    def _on_units(self, proc: QProcess) -> None:
        text = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        proc.deleteLater()
        self._list_proc = None
        active = set()
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] in ("active", "activating", "deactivating", "reloading"):
                active.add(parts[0])
        self.active_units = {u for u in active if u.startswith("ub-build-")}
        self.active_maint = {u for u in active if u.startswith("ub-maint-")}
        self._rescan()
        self.maintChanged.emit(self.maint_entries())

    def _rescan(self) -> None:
        entries = scan_jobs(self.layout.builds, self.queue, self.active_units)
        for e in entries:
            prev = self._states.get(e.job_id)
            if e.state in FINAL_STATES and prev is not None and prev not in FINAL_STATES and not self._first_scan:
                self._finished(e)
            elif e.state in FINAL_STATES and not self._first_scan and prev is None and e.result and not e.result.get("notified_by"):
                self._finished(e)
            self._states[e.job_id] = e.state
        self._first_scan = False
        self.entries = entries
        self.entriesChanged.emit(entries)
        self._schedule()

    def _finished(self, entry: JobEntry) -> None:
        if entry.paths and entry.result is not None and not entry.result.get("notified_by"):
            notify_job_finished(self.settings, entry)
            entry.result["notified_by"] = "gui"
            try:
                entry.paths.write_result(entry.result)
            except OSError:
                pass
        self.jobFinished.emit(entry)

    # --- queue ----------------------------------------------------------------
    def running_entries(self) -> list[JobEntry]:
        return [e for e in self.entries if e.is_running]

    def enqueue(self, spec: JobSpec) -> None:
        self.queue.add(spec)
        self.queue.save()
        self._rescan()

    def remove_queued(self, job_id: str) -> None:
        if self.queue.remove(job_id):
            self.queue.save()
            self._rescan()

    def move_queued(self, job_id: str, delta: int) -> None:
        if self.queue.move(job_id, delta):
            self.queue.save()
            self._rescan()

    def set_paused(self, paused: bool) -> None:
        self.queue.paused = paused
        self.queue.save()
        self._rescan()

    def _schedule(self) -> None:
        running_entries = [e for e in self.entries if e.is_running]
        running = [e.spec.project for e in running_entries] + list(self._launching)
        spec = next_to_start(self.queue, running, self.settings.resources.max_concurrent_builds)
        if spec is None:
            return
        busy = {e.spec.prefix for e in running_entries} | set(self._launching_prefixes)
        slot = free_slot(self.settings, busy)
        if slot is None:
            return
        if running and not self._admit():
            return
        self.queue.remove(spec.job_id)
        self.queue.save()
        spec.prefix = str(slot.prefix)
        spec.zen_port = slot.zen_port
        spec.shader_workers = workers_for(self.settings, spec.shader_workers)
        self._launch(spec)

    def _admit(self) -> bool:
        """A second concurrent build starts only if the RAM budget and free memory allow it."""
        try:
            import psutil

            available = psutil.virtual_memory().available / 2**30
        except Exception:  # noqa: BLE001
            return False
        uid = os.getuid()
        slice_dir = cgroup_dir(f"/user.slice/user-{uid}.slice/user@{uid}.service/unrealbuilder.slice")
        used = (read_stats(slice_dir).memory_current or 0) / 2**30 if slice_dir else 0.0
        expected = 10.0
        return available >= 8 and used + expected <= self.settings.resources.ram_budget_gib

    def _launch(self, spec: JobSpec) -> None:
        try:
            paths, unit = prepare_job(self.settings, spec)
            argv = systemd_run_argv(unit)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"Could not prepare build {spec.job_id}: {exc}")
            return
        self._launching.add(spec.project)
        self._launching_prefixes.add(spec.prefix)
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def done(code: int, _status) -> None:
            self._launching.discard(spec.project)
            self._launching_prefixes.discard(spec.prefix)
            out = bytes(proc.readAll()).decode(errors="replace").strip()
            proc.deleteLater()
            if code != 0:
                paths.write_result(
                    {
                        "job_id": spec.job_id,
                        "state": JobState.FAILED.value,
                        "failed_step": "preflight",
                        "message": f"systemd-run failed: {out[:400]}",
                        "finished_at": time.time(),
                        "finalized_at": time.time(),
                        "written_by": "gui",
                    }
                )
                self.error.emit(f"systemd-run failed for {spec.job_id}: {out}")
            self.refresh()

        proc.finished.connect(done)
        proc.start(argv[0], argv[1:])
        QTimer.singleShot(1500, self.apply_slice_budget)

    # --- actions --------------------------------------------------------------
    def cancel(self, entry: JobEntry) -> None:
        if entry.state == JobState.QUEUED:
            self.remove_queued(entry.job_id)
            return
        if entry.paths is None:
            return
        entry.paths.cancel_marker.write_text("cancel\n")
        QProcess.startDetached("systemctl", ["--user", "stop", "--no-block", entry.unit])
        self.refresh()

    def set_memory_max(self, entry: JobEntry, gib: int) -> None:
        argv = systemd.set_property_argv(entry.unit, MemoryMax=f"{gib}G")
        QProcess.startDetached(argv[0], argv[1:])

    def entry(self, job_id: str) -> JobEntry | None:
        for e in self.entries:
            if e.job_id == job_id:
                return e
        return None

    @staticmethod
    def paths_for(entry: JobEntry) -> BuildPaths | None:
        return entry.paths

    # --- maintenance -----------------------------------------------------------
    def maint_entries(self) -> list[tuple[MaintPaths, bool]]:
        from unrealbuilder.core.paths import unit_name

        return [(m, unit_name("maint", m.job_id) in self.active_maint) for m in list_maint(30)]

    def maint_running(self) -> bool:
        return bool(self.active_maint)

    def launch_maint(self, action: str, params: dict | None = None) -> MaintPaths:
        paths = create_maint(action, params)
        argv = systemd_run_argv(maint_unit_spec(self.settings, paths.root))
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def done(code: int, _status) -> None:
            out = bytes(proc.readAll()).decode(errors="replace").strip()
            proc.deleteLater()
            if code != 0:
                paths.write_result({"action": action, "state": "failed", "message": f"systemd-run failed: {out[:300]}", "finished_at": time.time()})
                self.error.emit(f"systemd-run failed: {out}")
            self.refresh()

        proc.finished.connect(done)
        proc.start(argv[0], argv[1:])
        return paths
