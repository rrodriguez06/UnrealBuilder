"""Card showing a running job: phase, progress, resources, shader workers and actions."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from unrealbuilder.core.jobs import JobState
from unrealbuilder.core.jobstore import JobEntry
from unrealbuilder.gui.widgets.common import StateBadge, human_bytes, human_duration
from unrealbuilder.gui.widgets.sparkline import Sparkline


class JobCard(QFrame):
    cancelRequested = Signal(str)
    detailsRequested = Signal(str)
    folderRequested = Signal(str)

    def __init__(self, entry: JobEntry, parent=None) -> None:
        super().__init__(parent)
        self.job_id = entry.job_id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("JobCard")
        self.setStyleSheet("#JobCard { border-radius: 10px; }")

        self.title = QLabel(f"<b>{entry.spec.project}</b> · {entry.spec.configuration}" + (" · clean" if entry.spec.clean else ""))
        self.badge = StateBadge()
        self.badge.set_state(entry.state)
        self.step = QLabel("—")
        self.detail = QLabel("")
        self.detail.setStyleSheet("color: palette(placeholder-text);")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.elapsed = QLabel("")
        self.eta = QLabel("")
        self.eta.setToolTip("Estimate based on the last comparable successful build")
        self.issues = QLabel("")
        self.memory = QLabel("RAM —")
        self.cpu = QLabel("CPU —")
        self.workers = QLabel("")
        self.alert = QLabel("")
        self.alert.setStyleSheet("color: #e5484d; font-weight: 600;")
        self.alert.setWordWrap(True)
        self.spark = Sparkline()
        self.spark.setToolTip("Unit memory (GiB); dotted line: MemoryMax")

        cancel = QPushButton("Cancel")
        details = QPushButton("Details…")
        folder = QPushButton("Folder")
        cancel.clicked.connect(lambda: self.cancelRequested.emit(self.job_id))
        details.clicked.connect(lambda: self.detailsRequested.emit(self.job_id))
        folder.clicked.connect(lambda: self.folderRequested.emit(self.job_id))
        self.cancel_btn = cancel

        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addWidget(self.badge)
        top.addStretch(1)
        top.addWidget(self.elapsed)
        top.addWidget(self.eta)
        top.addWidget(details)
        top.addWidget(folder)
        top.addWidget(cancel)

        grid = QGridLayout()
        grid.addWidget(self.step, 0, 0)
        grid.addWidget(self.detail, 0, 1)
        grid.addWidget(self.issues, 0, 2)
        grid.addWidget(self.memory, 1, 0)
        grid.addWidget(self.cpu, 1, 1)
        grid.addWidget(self.workers, 1, 2)
        grid.setColumnStretch(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        layout.addLayout(grid)
        layout.addWidget(self.spark)
        layout.addWidget(self.alert)

    def set_eta(self, seconds: float | None) -> None:
        self.eta.setText(f"· ≈ {human_duration(seconds)} left" if seconds is not None else "")

    def update_entry(self, entry: JobEntry) -> None:
        self.badge.set_state(entry.state)
        self.cancel_btn.setEnabled(entry.state in (JobState.RUNNING, JobState.STARTING))

    def update_snapshot(self, snap) -> None:
        self.bar.setValue(int(snap.fraction * 1000))
        self.bar.setFormat(f"{snap.fraction * 100:.0f}%")
        self.step.setText(f"<b>{snap.step_label}</b>")
        self.detail.setText(snap.detail or snap.message or "")
        self.elapsed.setText(human_duration(snap.elapsed))
        self.issues.setText(f"⛔ {snap.errors}   ⚠ {snap.warnings}")
        if snap.mem_current is not None:
            limit = f" / {human_bytes(snap.mem_max)}" if snap.mem_max else ""
            swap = f" · swap {human_bytes(snap.swap)}" if snap.swap else ""
            pressure = f" · pressure {snap.pressure_full:.0f}%" if snap.pressure_full >= 1 else ""
            self.memory.setText(f"RAM {human_bytes(snap.mem_current)}{limit} (peak {human_bytes(snap.mem_peak)}){swap}{pressure}")
        if snap.cpu_percent is not None:
            self.cpu.setText(f"CPU {snap.cpu_percent:.0f}% · {snap.nprocs} processes")
        ceiling = snap.mem_max / 2**30 if snap.mem_max else None
        self.spark.set_values(snap.mem_history, ceiling)
        alerts = []
        if snap.workers:
            stalled = [w for w in snap.workers if w.state == "stalled"]
            busy = sum(1 for w in snap.workers if w.state in ("active", "long"))
            text = f"Shader workers: {busy}/{len(snap.workers)} active"
            if stalled:
                text += f" · {len(stalled)} stalled"
                alerts.append(f"{len(stalled)} shader worker(s) look stalled — see the Workers tab")
            self.workers.setText(text)
            if any(w.d3dcompiler == "builtin" for w in snap.workers):
                alerts.append("Wine's builtin d3dcompiler is loaded: SM5 shaders will be wrong. Cancel the build and fix the prefix.")
        else:
            self.workers.setText("")
        if snap.pressure_full >= 20:
            alerts.append("High memory pressure: systemd-oomd may kill the build")
        self.alert.setText("\n".join(alerts))
        self.alert.setVisible(bool(alerts))
