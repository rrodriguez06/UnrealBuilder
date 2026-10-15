"""Builds page: running job cards, queue and history."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.jobs import FINAL_STATES, JobState
from unrealbuilder.core.jobstore import JobEntry
from unrealbuilder.gui.theme import STATE_COLORS
from unrealbuilder.gui.widgets.common import human_bytes, human_duration
from unrealbuilder.gui.widgets.job_card import JobCard

HISTORY_COLUMNS = ["Project", "Config", "Started", "Status", "Duration", "Zip", "Peak RAM", "Message"]


class BuildsPage(QWidget):
    detailsRequested = Signal(str)
    folderRequested = Signal(str)
    cancelRequested = Signal(str)
    queueMove = Signal(str, int)
    queueRemove = Signal(str)
    pauseToggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cards: dict[str, JobCard] = {}
        self.entries: list[JobEntry] = []

        # Running
        self.cards_box = QVBoxLayout()
        self.cards_box.addStretch(1)
        cards_host = QWidget()
        cards_host.setLayout(self.cards_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(cards_host)
        self.empty = QLabel("No build running.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        running = QGroupBox("Running")
        rl = QVBoxLayout(running)
        rl.addWidget(self.empty)
        rl.addWidget(scroll, 1)

        # Queue
        self.queue = QListWidget()
        up, down, remove = QPushButton("↑"), QPushButton("↓"), QPushButton("Remove")
        self.pause = QCheckBox("Pause queue")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(+1))
        remove.clicked.connect(self._remove)
        self.pause.toggled.connect(self.pauseToggled)
        qbar = QHBoxLayout()
        for w in (up, down, remove):
            qbar.addWidget(w)
        qbar.addStretch(1)
        qbar.addWidget(self.pause)
        queue_box = QGroupBox("Queue")
        ql = QVBoxLayout(queue_box)
        ql.addWidget(self.queue)
        ql.addLayout(qbar)

        # History
        self.history = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.verticalHeader().setVisible(False)
        header = self.history.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(HISTORY_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self.history.doubleClicked.connect(self._history_details)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter (project, config, status, message)…")
        self.filter.textChanged.connect(self._apply_history_filter)
        hbar = QHBoxLayout()
        details = QPushButton("Details…")
        folder = QPushButton("Open folder")
        details.clicked.connect(self._history_details)
        folder.clicked.connect(self._history_folder)
        hbar.addWidget(details)
        hbar.addWidget(folder)
        hbar.addWidget(self.filter, 1)
        history_box = QGroupBox("History")
        hl = QVBoxLayout(history_box)
        hl.addLayout(hbar)
        hl.addWidget(self.history)

        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(queue_box)
        bottom.addWidget(history_box)
        bottom.setStretchFactor(0, 1)
        bottom.setStretchFactor(1, 3)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(running)
        split.addWidget(bottom)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(split)

    # --- updates --------------------------------------------------------------
    def update_entries(self, entries: list[JobEntry], paused: bool) -> None:
        self.entries = entries
        running = [e for e in entries if e.is_running or e.state == JobState.LOST and e.paths and not e.result]
        running_ids = {e.job_id for e in running}
        for job_id in list(self.cards):
            if job_id not in running_ids:
                card = self.cards.pop(job_id)
                card.setParent(None)
                card.deleteLater()
        for e in running:
            card = self.cards.get(e.job_id)
            if card is None:
                card = JobCard(e)
                card.cancelRequested.connect(self.cancelRequested)
                card.detailsRequested.connect(self.detailsRequested)
                card.folderRequested.connect(self.folderRequested)
                self.cards[e.job_id] = card
                self.cards_box.insertWidget(self.cards_box.count() - 1, card)
            card.update_entry(e)
        self.empty.setVisible(not self.cards)

        self.pause.blockSignals(True)
        self.pause.setChecked(paused)
        self.pause.blockSignals(False)
        selected = self._selected_queue_id()
        self.queue.clear()
        for e in entries:
            if e.state == JobState.QUEUED:
                item = QListWidgetItem(f"{e.spec.project} · {e.spec.configuration}" + (" · clean" if e.spec.clean else ""))
                item.setData(Qt.ItemDataRole.UserRole, e.job_id)
                self.queue.addItem(item)
                if e.job_id == selected:
                    self.queue.setCurrentItem(item)

        history = [e for e in entries if e.paths is not None and e.state in FINAL_STATES][:300]
        self.history.setRowCount(len(history))
        for i, e in enumerate(history):
            r = e.result or {}
            values = [
                e.spec.project,
                e.spec.configuration,
                time.strftime("%Y-%m-%d %H:%M", time.localtime(e.spec.created_at)),
                e.state.label,
                human_duration(e.duration),
                human_bytes(r.get("zip_size")) if r.get("zip_size") else "—",
                human_bytes(r.get("memory_peak")) if r.get("memory_peak") else "—",
                r.get("message", ""),
            ]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, e.job_id)
                if col == 3:
                    item.setForeground(QColor(STATE_COLORS.get(e.state, "#888")))
                self.history.setItem(i, col, item)
        self._apply_history_filter()

    def _apply_history_filter(self, *_args) -> None:
        needle = self.filter.text().strip().lower()
        for row in range(self.history.rowCount()):
            text = " ".join(self.history.item(row, c).text().lower() for c in range(self.history.columnCount()) if self.history.item(row, c))
            self.history.setRowHidden(row, bool(needle) and needle not in text)

    def set_eta(self, job_id: str, seconds) -> None:
        card = self.cards.get(job_id)
        if card is not None:
            card.set_eta(seconds)

    def update_snapshot(self, snap) -> None:
        card = self.cards.get(snap.job_id)
        if card is not None:
            card.update_snapshot(snap)

    # --- helpers --------------------------------------------------------------
    def _selected_queue_id(self) -> str | None:
        item = self.queue.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _move(self, delta: int) -> None:
        job_id = self._selected_queue_id()
        if job_id:
            self.queueMove.emit(job_id, delta)

    def _remove(self) -> None:
        job_id = self._selected_queue_id()
        if job_id:
            self.queueRemove.emit(job_id)

    def _selected_history_id(self) -> str | None:
        items = self.history.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _history_details(self, *_args) -> None:
        job_id = self._selected_history_id()
        if job_id:
            self.detailsRequested.emit(job_id)

    def _history_folder(self) -> None:
        job_id = self._selected_history_id()
        if job_id:
            self.folderRequested.emit(job_id)
