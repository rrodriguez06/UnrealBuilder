"""Projects page: Linux projects ↔ Win64 mirror and builds."""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.discovery import ProjectInfo, discover_projects
from unrealbuilder.core.engines import EnginePair
from unrealbuilder.core.fsutil import read_json
from unrealbuilder.core.jobstore import JobEntry
from unrealbuilder.core.mirror import manifest_path
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.prepare import analyze
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.uproject import ProjectAnalysis, RefStatus
from unrealbuilder.gui.services.tasks import run_in_pool
from unrealbuilder.gui.widgets.common import human_bytes, open_path

COLUMNS = ["Project", "Engine", "Type", "Plugins", "Mirror", "Last build"]


@dataclass
class ProjectRow:
    info: ProjectInfo
    engine: EnginePair | None
    analysis: ProjectAnalysis | None
    mirror_synced_at: float | None
    error: str | None = None


def scan(settings: Settings) -> list[ProjectRow]:
    layout = ToolchainLayout.from_settings(settings)
    rows = []
    for info in discover_projects(settings.project_roots):
        if info.descriptor is None:
            rows.append(ProjectRow(info, None, None, None, info.error))
            continue
        try:
            engine, analysis = analyze(settings, info)
            error = None
        except Exception as exc:  # noqa: BLE001
            engine, analysis, error = None, None, str(exc)
        manifest = read_json(manifest_path(layout.mirror_dir(info.name)), default={}) or {}
        rows.append(ProjectRow(info, engine, analysis, manifest.get("synced_at"), error))
    return rows


class ProjectsPage(QWidget):
    buildRequested = Signal(object, object, str)  # ProjectInfo, ProjectAnalysis, configuration
    openBuildRequested = Signal(str)  # job_id

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.layout_paths = ToolchainLayout.from_settings(settings)
        self.rows: list[ProjectRow] = []
        self.last_builds: dict[str, JobEntry] = {}

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.build_btn = QToolButton()
        self.build_btn.setText("Build Win64")
        self.build_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(self.build_btn)
        for cfg in ("Development", "Shipping", "DebugGame"):
            menu.addAction(f"{cfg}…", lambda c=cfg: self._request_build(c))
        self.build_btn.setMenu(menu)
        self.build_btn.clicked.connect(lambda: self._request_build("Development"))
        open_project = QPushButton("Open project")
        open_project.clicked.connect(lambda: self._open("project"))
        open_mirror = QPushButton("Open mirror")
        open_mirror.clicked.connect(lambda: self._open("mirror"))
        last = QPushButton("Last build")
        last.clicked.connect(self._open_last)
        self.status = QLabel("")

        bar = QHBoxLayout()
        for w in (self.build_btn, open_project, open_mirror, last):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.status)
        bar.addWidget(refresh)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.doubleClicked.connect(lambda _i: self._request_build("Development"))

        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(split, 1)

    # --- data -------------------------------------------------------------------
    def refresh(self) -> None:
        self.status.setText("Analyzing projects…")
        run_in_pool(lambda: scan(self.settings), self._populate, lambda err: self.status.setText("Analysis failed"))

    def _populate(self, rows: list[ProjectRow]) -> None:
        selected = self.current_row()
        self.rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            a = row.analysis
            engine = row.engine.version if row.engine else (row.info.descriptor.engine_association if row.info.descriptor else "?")
            kind = a.kind_label if a else (row.error or "—")
            if a and a.blockers:
                plugins = "✗ blocking"
            elif a and a.optional_patches:
                plugins = f"{len(a.optional_patches)} made optional"
            elif a:
                plugins = "OK"
            else:
                plugins = "—"
            mirror = time.strftime("%Y-%m-%d %H:%M", time.localtime(row.mirror_synced_at)) if row.mirror_synced_at else "never"
            values = [row.info.name, engine, kind, plugins, mirror, self._last_build_text(row.info.name)]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 3 and a and a.blockers:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(i, col, item)
        self.status.setText(f"{len(rows)} project(s)")
        if rows:
            names = [r.info.name for r in rows]
            self.table.selectRow(names.index(selected.info.name) if selected and selected.info.name in names else 0)

    def _last_build_text(self, project: str) -> str:
        e = self.last_builds.get(project.lower())
        if e is None:
            return "—"
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.spec.created_at))
        size = f" · {human_bytes(e.result.get('zip_size'))}" if e.result and e.result.get("zip_size") else ""
        return f"{e.state.label} · {e.spec.configuration} · {when}{size}"

    def update_builds(self, entries: list[JobEntry]) -> None:
        latest: dict[str, JobEntry] = {}
        for e in entries:
            if e.paths is None:
                continue
            key = e.spec.project.lower()
            if key not in latest or e.spec.created_at > latest[key].spec.created_at:
                latest[key] = e
        self.last_builds = latest
        for i, row in enumerate(self.rows):
            item = self.table.item(i, 5)
            if item is not None:
                item.setText(self._last_build_text(row.info.name))

    # --- selection / actions ----------------------------------------------------
    def current_row(self) -> ProjectRow | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def _show_detail(self) -> None:
        row = self.current_row()
        if row is None:
            self.detail.clear()
            return
        a = row.analysis
        html = [f"<h3>{row.info.name}</h3><p><code>{row.info.directory}</code></p>"]
        if row.engine:
            lv, wv = row.engine.linux.build_version, row.engine.win.build_version
            html.append(f"<p><b>Linux engine:</b> {lv.label if lv else '?'} &nbsp; <b>Windows engine:</b> {wv.label if wv else 'missing'}</p>")
            if row.engine.changelists_match is False:
                html.append("<p style='color:#e5484d'>⚠ The two engines are not on the same changelist.</p>")
        if a:
            html.append(f"<p><b>Type:</b> {a.kind_label} — compile step: {'yes' if a.needs_build_step else 'no'}</p>")
            html.append("<table cellpadding='3'><tr><th align='left'>Plugin</th><th align='left'>Status</th><th align='left'>Target</th><th align='left'>Details</th></tr>")
            colors = {RefStatus.BLOCKER: "#e5484d", RefStatus.PATCH_OPTIONAL: "#f0b429", RefStatus.MISSING_UNKNOWN: "#f0b429"}
            for r in a.references:
                color = colors.get(r.status, "#2fb344" if r.status == RefStatus.OK else None)
                style = f" style='color:{color}'" if color else ""
                html.append(
                    f"<tr><td>{r.name}</td><td{style}>{r.status.value}</td>"
                    f"<td>{'game' if r.for_game else 'editor'}</td><td>{r.message}</td></tr>"
                )
            html.append("</table>")
            for w in a.warnings:
                html.append(f"<p style='color:#f0b429'>⚠ {w}</p>")
            for b in a.blockers:
                html.append(f"<p style='color:#e5484d'>✗ {b}</p>")
        elif row.error:
            html.append(f"<p style='color:#e5484d'>{row.error}</p>")
        self.detail.setHtml("".join(html))

    def _request_build(self, configuration: str) -> None:
        row = self.current_row()
        if row is not None and row.info.descriptor is not None:
            self.buildRequested.emit(row.info, row.analysis, configuration)

    def _open(self, what: str) -> None:
        row = self.current_row()
        if row is None:
            return
        target = row.info.directory if what == "project" else self.layout_paths.mirror_dir(row.info.name)
        open_path(target, self)

    def _open_last(self) -> None:
        row = self.current_row()
        if row is None:
            return
        e = self.last_builds.get(row.info.name.lower())
        if e is not None:
            self.openBuildRequested.emit(e.job_id)

