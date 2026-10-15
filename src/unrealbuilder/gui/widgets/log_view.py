"""Virtualized, filterable log viewer (one model per source)."""

from __future__ import annotations

import re

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.logparse.lines import LogLine, Severity
from unrealbuilder.gui.theme import SEVERITY_COLORS
from unrealbuilder.gui.widgets.common import mono_font


class LogModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lines: list[LogLine] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.lines)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        line = self.lines[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{line.number:>6}  {line.text}"
        if role == Qt.ItemDataRole.ForegroundRole:
            return SEVERITY_COLORS.get(line.severity)
        if role == Qt.ItemDataRole.UserRole:
            return line
        return None

    def append(self, lines: list[LogLine]) -> None:
        if not lines:
            return
        start = len(self.lines)
        self.beginInsertRows(QModelIndex(), start, start + len(lines) - 1)
        self.lines.extend(lines)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.lines = []
        self.endResetModel()

    def set_lines(self, lines: list[LogLine]) -> None:
        self.beginResetModel()
        self.lines = list(lines)
        self.endResetModel()


class LogFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.min_severity = Severity.INFO
        self.only_issues = False
        self.pattern: re.Pattern | None = None

    def set_filters(self, show_noise: bool, show_verbose: bool, only_issues: bool, text: str, regex: bool) -> None:
        self.beginFilterChange()
        self.min_severity = Severity.NOISE if show_noise else (Severity.VERBOSE if show_verbose else Severity.INFO)
        self.only_issues = only_issues
        self.pattern = None
        if text:
            try:
                self.pattern = re.compile(text if regex else re.escape(text), re.IGNORECASE)
            except re.error:
                self.pattern = re.compile(re.escape(text), re.IGNORECASE)
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        line: LogLine = self.sourceModel().lines[row]
        if self.only_issues:
            if line.severity < Severity.WARNING:
                return False
        elif line.severity < self.min_severity:
            return False
        return not self.pattern or bool(self.pattern.search(line.text))


class LogView(QWidget):
    """Source selector + filters + virtualized list. Call `append(source, lines)` as data arrives."""

    def __init__(self, sources: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.models = {key: LogModel(self) for key in sources}
        self.proxy = LogFilter(self)
        self.source = QComboBox()
        for key, label in sources.items():
            self.source.addItem(label, key)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search… (Enter: next)")
        self.regex = QCheckBox("Regex")
        self.issues_only = QCheckBox("Errors/warnings")
        self.verbose = QCheckBox("Verbose")
        self.noise = QCheckBox("Noise")
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        self.count = QLabel()
        prev_btn, next_btn = QPushButton("↑"), QPushButton("↓")
        prev_btn.setFixedWidth(28)
        next_btn.setFixedWidth(28)

        self.view = QListView()
        self.view.setFont(mono_font())
        self.view.setUniformItemSizes(True)
        self.view.setModel(self.proxy)
        self.view.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(False)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Source"))
        bar.addWidget(self.source)
        bar.addWidget(self.search, 1)
        bar.addWidget(prev_btn)
        bar.addWidget(next_btn)
        for w in (self.regex, self.issues_only, self.verbose, self.noise, self.follow, self.count):
            bar.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._apply_filters)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        for cb in (self.regex, self.issues_only, self.verbose, self.noise):
            cb.toggled.connect(lambda _c: self._apply_filters())
        self.source.currentIndexChanged.connect(self._on_source)
        self.search.returnPressed.connect(lambda: self._jump(+1))
        next_btn.clicked.connect(lambda: self._jump(+1))
        prev_btn.clicked.connect(lambda: self._jump(-1))
        self._on_source()

    def current_source(self) -> str:
        return self.source.currentData()

    def _on_source(self) -> None:
        self.proxy.setSourceModel(self.models[self.current_source()])
        self._apply_filters()

    def _apply_filters(self) -> None:
        self.proxy.set_filters(
            self.noise.isChecked(), self.verbose.isChecked(), self.issues_only.isChecked(), self.search.text(), self.regex.isChecked()
        )
        self._update_count()
        if self.follow.isChecked():
            self.view.scrollToBottom()

    def _update_count(self) -> None:
        model = self.models[self.current_source()]
        self.count.setText(f"{self.proxy.rowCount()} / {len(model.lines)}")

    def _jump(self, step: int) -> None:
        n = self.proxy.rowCount()
        if not n:
            return
        cur = self.view.currentIndex().row()
        row = (cur + step) % n if cur >= 0 else (0 if step > 0 else n - 1)
        idx = self.proxy.index(row, 0)
        self.follow.setChecked(False)
        self.view.setCurrentIndex(idx)
        self.view.scrollTo(idx, QListView.ScrollHint.PositionAtCenter)

    def append(self, source: str, lines: list[LogLine]) -> None:
        model = self.models.get(source)
        if model is None:
            return
        model.append(lines)
        if source == self.current_source():
            self._update_count()
            if self.follow.isChecked():
                self.view.scrollToBottom()

    def set_lines(self, source: str, lines: list[LogLine]) -> None:
        model = self.models.get(source)
        if model is not None:
            model.set_lines(lines)
            if source == self.current_source():
                self._apply_filters()

    def clear(self, source: str) -> None:
        if source in self.models:
            self.models[source].clear()

    def show_line(self, source: str, number: int) -> None:
        """Select the given source and scroll to a line number (from the issues tree)."""
        i = self.source.findData(source)
        if i >= 0:
            self.source.setCurrentIndex(i)
        self.follow.setChecked(False)
        self.issues_only.setChecked(False)
        self.search.clear()
        self._apply_filters()
        model = self.models[source]
        for row, line in enumerate(model.lines):
            if line.number == number:
                idx = self.proxy.mapFromSource(model.index(row, 0))
                if idx.isValid():
                    self.view.setCurrentIndex(idx)
                    self.view.scrollTo(idx, QListView.ScrollHint.PositionAtCenter)
                return
