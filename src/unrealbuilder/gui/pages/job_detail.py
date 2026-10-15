"""Detail window of a job: summary, grouped issues, logs, resources and shader workers."""

from __future__ import annotations

import os
import signal
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.fsutil import read_json
from unrealbuilder.core.jobs import STEP_LABELS, STEPS, JobState
from unrealbuilder.core.jobstore import JobEntry
from unrealbuilder.core.logparse.lines import Severity
from unrealbuilder.gui.services.logstore import LogStore
from unrealbuilder.gui.services.monitor import SOURCE_LABELS
from unrealbuilder.gui.theme import SEVERITY_COLORS
from unrealbuilder.gui.widgets.common import StateBadge, human_bytes, human_duration, open_path
from unrealbuilder.gui.widgets.log_view import LogView
from unrealbuilder.gui.widgets.phase_timeline import PhaseTimeline
from unrealbuilder.gui.widgets.sparkline import Sparkline

STEP_STATE_LABELS = {
    "pending": "pending",
    "running": "running",
    "done": "done",
    "skipped": "skipped",
    "failed": "failed",
    "warning": "warning",
}
WORKER_COLUMNS = ["PID", "Batch", "Status", "CPU", "RAM", "Faults/s", "Batch age", "d3dcompiler"]
WORKER_STATE_LABELS = {"active": "active", "idle": "idle", "long": "long", "stalled": "STALLED"}


class JobDetailWindow(QWidget):
    def __init__(self, entry: JobEntry, store: LogStore, on_memory_max=None, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.entry = entry
        self.store = store
        self.on_memory_max = on_memory_max
        self.setWindowTitle(f"{entry.spec.project} · {entry.spec.configuration} — {entry.job_id}")
        self.resize(1200, 800)

        self.badge = StateBadge()
        self.badge.set_state(entry.state)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        open_dir = QPushButton("Build folder")
        open_dir.clicked.connect(lambda: open_path(entry.paths.root if entry.paths else None, self))
        self.open_zip = QPushButton("Archive folder")
        self.open_zip.clicked.connect(self._open_archive)
        commands = QPushButton("Commands")
        commands.setToolTip("Open commands.sh (equivalent commands, to replay the build by hand)")
        commands.clicked.connect(lambda: open_path(entry.paths.commands if entry.paths else None, self))
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<h3>{entry.spec.project} · {entry.spec.configuration}</h3>"))
        header.addWidget(self.badge)
        header.addStretch(1)
        for b in (open_dir, self.open_zip, commands):
            header.addWidget(b)

        self.tabs = QTabWidget()
        self.summary = QTextBrowser()
        self.issues = QTreeWidget()
        self.issues.setHeaderLabels(["Issue", "Count", "Category"])
        self.issues.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.issues.itemDoubleClicked.connect(self._goto_issue)
        order = ["uat-cook", "cook", "uat-build", "runner", "smoke"]
        self.logs = LogView({k: SOURCE_LABELS[k] for k in order})
        self.resources = self._build_resources_tab()
        self.workers = QTableWidget(0, len(WORKER_COLUMNS))
        self.workers.setHorizontalHeaderLabels(WORKER_COLUMNS)
        self.workers.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.workers.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.workers.verticalHeader().setVisible(False)
        kill = QPushButton("Kill selected worker…")
        kill.clicked.connect(self._kill_worker)
        workers_tab = QWidget()
        wl = QVBoxLayout(workers_tab)
        wl.addWidget(
            QLabel(
                "A STALLED worker has kept the same batch for more than 2 min with no memory activity while the others "
                "make progress. UE normally restarts the jobs of a killed worker."
            )
        )
        wl.addWidget(self.workers)
        wl.addWidget(kill, 0, Qt.AlignmentFlag.AlignLeft)

        self.tabs.addTab(self.summary, "Summary")
        self.tabs.addTab(self.issues, "Issues")
        self.tabs.addTab(self.logs, "Logs")
        self.tabs.addTab(self.resources, "Resources")
        self.tabs.addTab(workers_tab, "Workers")

        self.timeline = PhaseTimeline()
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.message)
        layout.addWidget(self.timeline)
        layout.addWidget(self.tabs, 1)

        store.appended.connect(self._on_lines)
        store.replaced.connect(self._on_replaced)
        self._issues_timer = QTimer(self)
        self._issues_timer.setInterval(2000)
        self._issues_timer.timeout.connect(self._refresh_issues)
        self._issues_timer.start()
        self._load_from_store()
        self.update_entry(entry)

    def _build_resources_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.res_text = QLabel("Resources are shown while the build runs.")
        self.res_text.setWordWrap(True)
        self.res_spark = Sparkline()
        self.res_spark.setMinimumHeight(160)
        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(4, 128)
        self.mem_spin.setSuffix(" GiB")
        self.mem_spin.setValue(self.entry.spec.memory_max_gib)
        apply = QPushButton("Apply MemoryMax live")
        apply.setToolTip("systemctl --user set-property --runtime <unit> MemoryMax=… (never MemoryHigh)")
        apply.clicked.connect(self._apply_memory_max)
        row = QHBoxLayout()
        row.addWidget(QLabel("Unit MemoryMax:"))
        row.addWidget(self.mem_spin)
        row.addWidget(apply)
        row.addStretch(1)
        lay.addWidget(self.res_text)
        lay.addLayout(row)
        lay.addWidget(QLabel("Unit memory (GiB) — dotted line: MemoryMax"))
        lay.addWidget(self.res_spark, 1)
        return w

    def _apply_memory_max(self) -> None:
        if self.on_memory_max and self.entry.is_running:
            self.on_memory_max(self.entry, self.mem_spin.value())

    # --- data ---------------------------------------------------------------------
    def _load_from_store(self) -> None:
        logs = self.store.get(self.entry.job_id)
        for source, lines in logs.sources.items():
            self.logs.set_lines(source, lines)
        self._refresh_issues()

    def _on_lines(self, job_id: str, source: str, lines: list) -> None:
        if job_id == self.entry.job_id:
            self.logs.append(source, lines)

    def _on_replaced(self, job_id: str) -> None:
        if job_id == self.entry.job_id:
            self._load_from_store()

    def _refresh_issues(self) -> None:
        coll = self.store.get(self.entry.job_id).issues
        groups = coll.sorted_groups()
        if self.issues.topLevelItemCount() and self.issues.property("count") == sum(g.count for g in groups):
            return
        self.issues.setProperty("count", sum(g.count for g in groups))
        self.issues.clear()
        roots: dict[int, QTreeWidgetItem] = {}
        for g in groups:
            sev = int(g.severity)
            if sev not in roots:
                label = "Errors" if g.severity == Severity.ERROR else "Warnings"
                roots[sev] = QTreeWidgetItem([label, "", ""])
                self.issues.addTopLevelItem(roots[sev])
            item = QTreeWidgetItem([g.sample[:300], str(g.count), g.category])
            item.setForeground(0, SEVERITY_COLORS.get(g.severity))
            item.setToolTip(0, g.sample)
            for source, line in g.occurrences[:50]:
                child = QTreeWidgetItem([f"{SOURCE_LABELS.get(source, source)} — line {line}", "", ""])
                child.setData(0, Qt.ItemDataRole.UserRole, (source, line))
                item.addChild(child)
            roots[sev].addChild(item)
        for sev, root in roots.items():
            n = sum(int(root.child(i).text(1)) for i in range(root.childCount()))
            root.setText(1, str(n))
            root.setExpanded(sev == int(Severity.ERROR))
        if not groups:
            self.issues.addTopLevelItem(QTreeWidgetItem(["No errors or warnings", "", ""]))
        self.tabs.setTabText(1, f"Issues ({coll.error_count} ⛔ / {coll.warning_count} ⚠)")

    def _goto_issue(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data and item.childCount():
            data = item.child(0).data(0, Qt.ItemDataRole.UserRole)
        if data:
            source, line = data
            self.tabs.setCurrentWidget(self.logs)
            self.logs.show_line(source, line)

    def update_entry(self, entry: JobEntry) -> None:
        self.entry = entry
        self.badge.set_state(entry.state)
        r = entry.result or {}
        msg = r.get("message") or entry.status.get("message", "")
        if r.get("failed_step"):
            msg = f"Step \"{STEP_LABELS.get(r['failed_step'], r['failed_step'])}\": {msg}"
        self.message.setText(msg)
        self._render_summary()

    def _render_summary(self) -> None:
        e = self.entry
        steps = (e.status or {}).get("steps", {})
        self.timeline.set_steps(steps)
        summary = read_json(e.paths.summary, default={}) if e.paths else {}
        r = e.result or {}
        html = ["<table cellpadding='4'>"]
        html.append(f"<tr><td><b>Job</b></td><td>{e.job_id}</td></tr>")
        html.append(f"<tr><td><b>Duration</b></td><td>{human_duration(e.duration)}</td></tr>")
        if summary.get("analysis", {}).get("kind"):
            html.append(f"<tr><td><b>Type</b></td><td>{summary['analysis']['kind']}</td></tr>")
        prog = summary.get("progress", {})
        if prog.get("toolchain"):
            html.append(f"<tr><td><b>Toolchain</b></td><td>MSVC {prog['toolchain']} · SDK {prog.get('windows_sdk')}</td></tr>")
        if prog.get("shaders_compiled"):
            html.append(f"<tr><td><b>Shaders compiled</b></td><td>{prog['shaders_compiled']:,}</td></tr>")
        if r.get("memory_peak"):
            html.append(f"<tr><td><b>Peak memory</b></td><td>{human_bytes(r['memory_peak'])}</td></tr>")
        if r.get("zip"):
            html.append(f"<tr><td><b>Zip</b></td><td>{r['zip']} ({human_bytes(r.get('zip_size'))})</td></tr>")
        for w in r.get("warnings", []):
            html.append(f"<tr><td><b>⚠</b></td><td>{w}</td></tr>")
        html.append("</table><h4>Steps</h4><table cellpadding='4'><tr><th align='left'>Step</th><th align='left'>Status</th><th align='left'>Duration</th><th align='left'>Details</th></tr>")
        for name in STEPS:
            s = steps.get(name, {})
            dur = s["ended"] - s["started"] if s.get("ended") and s.get("started") else (time.time() - s["started"] if s.get("state") == "running" and s.get("started") else None)
            detail = s.get("error") or s.get("detail") or s.get("reason") or s.get("kind") or ""
            color = {"failed": "#e5484d", "warning": "#f0b429", "running": "#3d8bfd", "done": "#2fb344"}.get(s.get("state", ""))
            style = f" style='color:{color}'" if color else ""
            html.append(
                f"<tr><td>{STEP_LABELS[name]}</td><td{style}>{STEP_STATE_LABELS.get(s.get('state', 'pending'), s.get('state'))}</td>"
                f"<td>{human_duration(dur) if dur is not None else ''}</td><td>{detail}</td></tr>"
            )
        html.append("</table>")
        self.summary.setHtml("".join(html))

    def update_snapshot(self, snap) -> None:
        if snap.job_id != self.entry.job_id:
            return
        lines = [f"<b>Step:</b> {snap.step_label} — {snap.detail}"]
        if snap.mem_current is not None:
            lines.append(f"<b>RAM:</b> {human_bytes(snap.mem_current)} / {human_bytes(snap.mem_max)} (peak {human_bytes(snap.mem_peak)})")
        if snap.swap:
            lines.append(f"<b>Swap:</b> {human_bytes(snap.swap)}")
        lines.append(f"<b>Memory pressure (full, 10 s):</b> {snap.pressure_full:.1f}%")
        if snap.cpu_percent is not None:
            lines.append(f"<b>CPU:</b> {snap.cpu_percent:.0f}% ({snap.nprocs} processes)")
        self.res_text.setText("<br>".join(lines))
        self.res_spark.set_values(snap.mem_history, snap.mem_max / 2**30 if snap.mem_max else None)
        self.workers.setRowCount(len(snap.workers))
        for i, w in enumerate(snap.workers):
            values = [
                str(w.pid),
                w.index,
                WORKER_STATE_LABELS.get(w.state, w.state),
                f"{w.cpu_percent:.0f}%",
                human_bytes(w.rss),
                f"{w.minflt_rate:.0f}",
                human_duration(w.input_age_s),
                {"native": "Microsoft ✓", "builtin": "Wine ✗"}.get(w.d3dcompiler or "", "—"),
            ]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                if w.state == "stalled" or w.d3dcompiler == "builtin":
                    item.setForeground(SEVERITY_COLORS[Severity.ERROR])
                self.workers.setItem(i, col, item)
        self._render_summary()

    def _open_archive(self) -> None:
        r = self.entry.result or {}
        open_path(r.get("archive") or (self.entry.paths.archive(self.entry.spec) if self.entry.paths else None), self)

    def _kill_worker(self) -> None:
        items = self.workers.selectedItems()
        if not items or self.entry.state != JobState.RUNNING:
            return
        pid = int(self.workers.item(items[0].row(), 0).text())
        if QMessageBox.question(self, "Kill worker", f"Kill ShaderCompileWorker {pid}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            QMessageBox.warning(self, "Failed", str(exc))
