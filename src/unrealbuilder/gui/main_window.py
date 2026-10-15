"""Main window: sidebar of pages, wiring of supervisor / monitor / log store."""

from __future__ import annotations

import shutil
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from unrealbuilder import APP_NAME, __version__
from unrealbuilder.core.jobs import JobState
from unrealbuilder.core.jobstore import JobEntry
from unrealbuilder.core.launcher import make_spec
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.progress import estimate_remaining, step_durations
from unrealbuilder.core.settings import Settings, load_settings
from unrealbuilder.gui.pages.builds_page import BuildsPage
from unrealbuilder.gui.pages.job_detail import JobDetailWindow
from unrealbuilder.gui.pages.projects_page import ProjectsPage
from unrealbuilder.gui.pages.settings_page import SettingsPage
from unrealbuilder.gui.pages.storage_page import StoragePage
from unrealbuilder.gui.pages.toolchain_page import ToolchainPage
from unrealbuilder.gui.services.logstore import LogStore
from unrealbuilder.gui.services.monitor import Monitor
from unrealbuilder.gui.services.notifications import notify
from unrealbuilder.gui.services.supervisor import Supervisor
from unrealbuilder.gui.widgets.build_dialog import BuildDialog
from unrealbuilder.gui.widgets.common import human_bytes, open_path

PAGES = [
    ("projects", "Projects"),
    ("builds", "Builds"),
    ("storage", "Caches & storage"),
    ("toolchain", "Toolchain"),
    ("settings", "Settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None, start_services: bool = True) -> None:
        super().__init__()
        self.settings = settings or load_settings()
        self.layout_paths = ToolchainLayout.from_settings(self.settings)
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 880)

        self.supervisor = Supervisor(self.settings, self)
        self.monitor = Monitor(self.layout_paths, self.settings.ui.poll_interval_ms, self)
        self.store = LogStore(self)
        self.details: dict[str, JobDetailWindow] = {}
        self._stall_notified: set[tuple[str, int]] = set()
        self._reference_cache: dict[str, dict | None] = {}

        self.projects = ProjectsPage(self.settings)
        self.builds = BuildsPage()
        self.settings_page = SettingsPage(self.settings)
        self.toolchain = ToolchainPage(self.settings, self.supervisor)
        self.storage = StoragePage(self.settings, self.supervisor)
        self.pages: dict[str, QWidget] = {
            "projects": self.projects,
            "builds": self.builds,
            "storage": self.storage,
            "toolchain": self.toolchain,
            "settings": self.settings_page,
        }

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(200)
        self.sidebar.setStyleSheet("QListWidget { border: none; font-size: 11pt; } QListWidget::item { padding: 10px 14px; }")
        self.stack = QStackedWidget()
        for key, label in PAGES:
            self.stack.addWidget(self.pages[key])
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self._on_page)
        self.sidebar.setCurrentRow(0)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.sidebar)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.status_jobs = QLabel()
        self.status_disk = QLabel()
        self.statusBar().addWidget(self.status_jobs, 1)
        self.statusBar().addPermanentWidget(self.status_disk)

        # wiring
        self.supervisor.entriesChanged.connect(self._on_entries)
        self.supervisor.error.connect(lambda msg: self.statusBar().showMessage(msg, 15000))
        self.monitor.snapshot.connect(self._on_snapshot)
        self.monitor.lines.connect(self.store.append)
        self.monitor.reset.connect(self.store.reset_source)
        self.projects.buildRequested.connect(self._build_dialog)
        self.projects.openBuildRequested.connect(self.open_details)
        self.builds.detailsRequested.connect(self.open_details)
        self.builds.folderRequested.connect(self._open_job_folder)
        self.builds.cancelRequested.connect(self._cancel)
        self.builds.queueMove.connect(self.supervisor.move_queued)
        self.builds.queueRemove.connect(self.supervisor.remove_queued)
        self.builds.pauseToggled.connect(self.supervisor.set_paused)
        self.settings_page.saved.connect(lambda: self.statusBar().showMessage("Settings saved", 5000))

        self.disk_timer = QTimer(self)
        self.disk_timer.setInterval(30000)
        self.disk_timer.timeout.connect(self._update_disk)
        self._update_disk()
        if start_services:
            self.start_services()

    def start_services(self) -> None:
        self.monitor.start()
        self.supervisor.start()
        self.disk_timer.start()
        self.projects.refresh()
        self.toolchain.refresh_checks()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.monitor.stop()
        for w in list(self.details.values()):
            w.close()
        super().closeEvent(event)

    def _on_page(self, row: int) -> None:
        if PAGES[row][0] == "storage" and not self.storage.roots:
            self.storage.refresh()

    @staticmethod
    def _placeholder(title: str) -> QWidget:
        label = QLabel(f"<h2>{title}</h2><p>Coming soon.</p>")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    # --- supervisor / monitor ------------------------------------------------------
    def _on_entries(self, entries: list[JobEntry]) -> None:
        for e in entries:
            if e.is_running and e.paths is not None:
                self.monitor.watch(e.job_id, e.paths.root, e.unit)
            elif e.job_id in self.monitor.watched and e.state.is_final:
                # One last poll happens in the worker when it sees the finalized result.
                self.monitor.watched.discard(e.job_id)
            if e.job_id in self.details:
                self.details[e.job_id].update_entry(e)
        self.builds.update_entries(entries, self.supervisor.queue.paused)
        self.projects.update_builds(entries)
        running = sum(1 for e in entries if e.is_running)
        queued = sum(1 for e in entries if e.state == JobState.QUEUED)
        self.status_jobs.setText(f"{running} build(s) running · {queued} queued" + (" (paused)" if self.supervisor.queue.paused else ""))

    def _reference_for(self, job_id: str) -> dict | None:
        if job_id in self._reference_cache:
            return self._reference_cache[job_id]
        entry = self.supervisor.entry(job_id)
        ref = None
        if entry is not None:
            for e in self.supervisor.entries:
                if (
                    e.job_id != job_id
                    and e.state == JobState.SUCCEEDED
                    and e.spec.project == entry.spec.project
                    and e.spec.configuration == entry.spec.configuration
                    and e.spec.clean == entry.spec.clean
                ):
                    ref = step_durations(e.status.get("steps", {})) or None
                    break
        self._reference_cache[job_id] = ref
        return ref

    def _on_snapshot(self, snap) -> None:
        self.builds.update_snapshot(snap)
        if snap.state in (JobState.RUNNING.value, JobState.STARTING.value):
            eta = estimate_remaining(snap.steps, snap.step, snap.progress, snap.elapsed, snap.fraction, self._reference_for(snap.job_id), time.time())
            self.builds.set_eta(snap.job_id, eta)
        win = self.details.get(snap.job_id)
        if win is not None:
            win.update_snapshot(snap)
        if self.settings.notifications.on_stall:
            for w in snap.workers:
                key = (snap.job_id, w.pid)
                if w.state == "stalled" and key not in self._stall_notified:
                    self._stall_notified.add(key)
                    notify("UnrealBuilder — shader worker stalled", f"PID {w.pid} idle for {int(w.input_age_s or 0)} s", critical=True)

    def _update_disk(self) -> None:
        try:
            usage = shutil.disk_usage(self.layout_paths.root)
            self.status_disk.setText(f"Free on toolchain disk: {human_bytes(usage.free)}")
        except OSError:
            self.status_disk.setText("")

    # --- actions ---------------------------------------------------------------------
    def _build_dialog(self, info, analysis, configuration: str) -> None:
        dlg = BuildDialog(info.name, self.settings, analysis, configuration, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        spec = make_spec(self.settings, info, dlg.options())
        self.supervisor.enqueue(spec)
        self.sidebar.setCurrentRow(1)

    def open_details(self, job_id: str) -> None:
        entry = self.supervisor.entry(job_id)
        if entry is None or entry.paths is None:
            return
        win = self.details.get(job_id)
        if win is None:
            if not entry.is_running:
                self.store.ensure_loaded(job_id, entry.paths.root)
            win = JobDetailWindow(entry, self.store, self.supervisor.set_memory_max)
            win.destroyed.connect(lambda *_: self.details.pop(job_id, None))
            win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.details[job_id] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def _open_job_folder(self, job_id: str) -> None:
        entry = self.supervisor.entry(job_id)
        if entry and entry.paths:
            open_path(entry.paths.root, self)

    def _cancel(self, job_id: str) -> None:
        entry = self.supervisor.entry(job_id)
        if entry is None:
            return
        if QMessageBox.question(self, "Cancel build", f"Cancel {entry.spec.project} · {entry.spec.configuration}?") == QMessageBox.StandardButton.Yes:
            self.supervisor.cancel(entry)
