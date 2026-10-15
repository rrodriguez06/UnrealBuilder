"""Toolchain page: doctor checks with fixes, engines and updates, maintenance operations with live logs."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.doctor.checks import CheckResult, run_checks
from unrealbuilder.core.engines import engine_pairs
from unrealbuilder.core.logparse.install import LegendaryProgress, VsDownloadProgress, feed_legendary, feed_vsdownload
from unrealbuilder.core.maint import WINE_ACTIONS, MaintPaths, label, split_action
from unrealbuilder.core.settings import Settings
from unrealbuilder.gui.services.tasks import run_in_pool
from unrealbuilder.gui.widgets.common import mono_font, open_path
from unrealbuilder.gui.widgets.license_dialog import LicenseDialog

STATUS_STYLE = {"ok": ("✓", "#2fb344"), "warn": ("⚠", "#f0b429"), "error": ("✗", "#e5484d"), "info": ("ℹ", "#3d8bfd")}
MAINT_STATE = {"running": "running", "succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled"}


class ToolchainPage(QWidget):
    def __init__(self, settings: Settings, supervisor, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.supervisor = supervisor
        self.checks: list[CheckResult] = []
        self.selected_maint: MaintPaths | None = None
        self._log_offset = 0

        # Doctor
        self.doctor = QTableWidget(0, 4)
        self.doctor.setHorizontalHeaderLabels(["", "Check", "Details", "Action"])
        self.doctor.verticalHeader().setVisible(False)
        self.doctor.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.doctor.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        h = self.doctor.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        rerun = QPushButton("Run checks again")
        rerun.clicked.connect(self.refresh_checks)
        self.doctor_status = QLabel("")
        dbar = QHBoxLayout()
        dbar.addWidget(self.doctor_status, 1)
        dbar.addWidget(rerun)
        doctor_box = QGroupBox("Checks")
        dl = QVBoxLayout(doctor_box)
        dl.addLayout(dbar)
        dl.addWidget(self.doctor)

        # Engines
        self.engines = QTableWidget(0, 5)
        self.engines.setHorizontalHeaderLabels(["Version", "Linux engine", "Windows engine", "Available (Epic)", "Folder"])
        self.engines.verticalHeader().setVisible(False)
        self.engines.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.engines.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.engines.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.engines.horizontalHeader().setStretchLastSection(True)
        check_upd = QPushButton("Check for updates")
        check_upd.clicked.connect(self.check_updates)
        update = QPushButton("Update / repair")
        update.clicked.connect(lambda: self._engine_action("engine-update"))
        install = QPushButton("Install")
        install.clicked.connect(lambda: self._engine_action("engine-install"))
        open_win = QPushButton("Open Windows engine")
        open_win.clicked.connect(self._open_engine)
        ebar = QHBoxLayout()
        for b in (check_upd, update, install, open_win):
            ebar.addWidget(b)
        ebar.addStretch(1)
        engines_box = QGroupBox("Engines")
        el = QVBoxLayout(engines_box)
        el.addWidget(self.engines)
        el.addLayout(ebar)

        # Operations
        self.ops = QTableWidget(0, 4)
        self.ops.setHorizontalHeaderLabels(["Operation", "Status", "Started", "Message"])
        self.ops.verticalHeader().setVisible(False)
        self.ops.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ops.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ops.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.ops.horizontalHeader().setStretchLastSection(True)
        self.ops.itemSelectionChanged.connect(self._select_op)
        self.op_progress = QProgressBar()
        self.op_progress.setVisible(False)
        self.op_detail = QLabel("")
        self.op_log = QPlainTextEdit()
        self.op_log.setReadOnly(True)
        self.op_log.setFont(mono_font())
        self.op_log.setMaximumBlockCount(5000)
        ops_box = QGroupBox("Maintenance operations")
        ol = QVBoxLayout(ops_box)
        ol.addWidget(self.ops, 1)
        ol.addWidget(self.op_progress)
        ol.addWidget(self.op_detail)
        ol.addWidget(self.op_log, 2)

        top = QSplitter(Qt.Orientation.Vertical)
        top.addWidget(doctor_box)
        top.addWidget(engines_box)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(top)
        split.addWidget(ops_box)
        top.setStretchFactor(0, 4)
        top.setStretchFactor(1, 1)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([780, 520])
        layout = QVBoxLayout(self)
        layout.addWidget(split)

        supervisor.maintChanged.connect(self._on_maint)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tail_log)
        self.timer.start()

    # --- doctor -------------------------------------------------------------------
    def refresh_checks(self) -> None:
        self.doctor_status.setText("Running checks…")
        run_in_pool(lambda: run_checks(self.settings), self._show_checks, lambda err: self.doctor_status.setText("Checks failed to run"))
        self._show_engines()

    def _show_checks(self, checks: list[CheckResult]) -> None:
        self.checks = checks
        self.doctor.setRowCount(0)
        group = None
        for c in checks:
            if c.group != group:
                group = c.group
                r = self.doctor.rowCount()
                self.doctor.insertRow(r)
                item = QTableWidgetItem(group)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                self.doctor.setItem(r, 1, item)
            r = self.doctor.rowCount()
            self.doctor.insertRow(r)
            icon, color = STATUS_STYLE.get(c.status, ("?", "#888"))
            status = QTableWidgetItem(icon)
            status.setForeground(QColor(color))
            self.doctor.setItem(r, 0, status)
            self.doctor.setItem(r, 1, QTableWidgetItem(c.title))
            detail = QTableWidgetItem(c.detail)
            detail.setToolTip(c.detail)
            self.doctor.setItem(r, 2, detail)
            if c.fix:
                btn = QPushButton(c.fix_label or "Fix")
                btn.clicked.connect(lambda _=False, check=c: self.fix(check))
                self.doctor.setCellWidget(r, 3, btn)
        errors = sum(1 for c in checks if c.status == "error")
        warns = sum(1 for c in checks if c.status == "warn")
        self.doctor_status.setText(
            "All good." if not errors and not warns else f"{errors} blocking issue(s), {warns} warning(s)"
        )

    def fix(self, check: CheckResult) -> None:
        name, _arg = split_action(check.fix or "")
        if name in WINE_ACTIONS and self.supervisor.running_entries():
            QMessageBox.information(self, "Build running", "This fix uses the Wine prefix: wait for the running builds to finish.")
            return
        if self.supervisor.maint_running():
            QMessageBox.information(self, "Operation running", "A maintenance operation is already running.")
            return
        params = {}
        if check.needs_license:
            dlg = LicenseDialog(label(check.fix or ""), self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            params["accept_license"] = True
        self.selected_maint = self.supervisor.launch_maint(check.fix, params)
        self._log_offset = 0
        self.op_log.clear()

    # --- engines --------------------------------------------------------------------
    def _show_engines(self, latest: dict[str, str] | None = None) -> None:
        pairs = engine_pairs(self.settings)
        self.engines.setRowCount(len(pairs))
        for i, (version, pair) in enumerate(pairs.items()):
            lv, wv = pair.linux.build_version, pair.win.build_version
            values = [
                version,
                lv.label if lv else "missing",
                wv.label if wv else "missing",
                (latest or {}).get(version, "—"),
                str(pair.win.root),
            ]
            for col, v in enumerate(values):
                self.engines.setItem(i, col, QTableWidgetItem(v))

    def check_updates(self) -> None:
        from unrealbuilder.core.install.engine import installed_version, latest_version

        def work() -> dict[str, str]:
            out = {}
            for version, eng in self.settings.engines.items():
                latest = latest_version(eng.legendary_app)
                inst = installed_version(eng.legendary_app)
                out[version] = f"{latest} " + ("(up to date)" if latest == inst else f"(installed: {inst or 'none'})")
            return out

        self.doctor_status.setText("Querying the Epic catalog…")
        run_in_pool(work, lambda res: (self._show_engines(res), self.doctor_status.setText("Epic catalog checked")), lambda err: self.doctor_status.setText("Failed: is legendary logged in?"))

    def _selected_engine(self) -> str | None:
        rows = self.engines.selectionModel().selectedRows()
        if rows:
            return self.engines.item(rows[0].row(), 0).text()
        return next(iter(self.settings.engines), None)

    def _engine_action(self, action: str) -> None:
        version = self._selected_engine()
        if version is None:
            return
        if self.supervisor.running_entries():
            QMessageBox.information(self, "Build running", "Wait for the builds to finish before changing the Windows engine.")
            return
        if QMessageBox.question(self, label(action), f"{label(action)}: UE {version} with legendary (core only, pre-seeded via reflink)?") != QMessageBox.StandardButton.Yes:
            return
        self.selected_maint = self.supervisor.launch_maint(f"{action}:{version}")
        self._log_offset = 0
        self.op_log.clear()

    def _open_engine(self) -> None:
        version = self._selected_engine()
        if version:
            pair = engine_pairs(self.settings).get(version)
            open_path(pair.win.root if pair else None, self)

    # --- operations -------------------------------------------------------------------
    def _on_maint(self, entries: list[tuple[MaintPaths, bool]]) -> None:
        selected = self.selected_maint.job_id if self.selected_maint else None
        self.ops.setRowCount(len(entries))
        for i, (m, active) in enumerate(entries):
            data = m.action()
            result = m.read_result() or {}
            state = "running" if active else result.get("state", "lost" if not result else "?")
            started = time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("created_at", 0)))
            message = result.get("message") or m.read_status().get("message", "")
            for col, v in enumerate([label(data.get("action", "?")), MAINT_STATE.get(state, state), started, message]):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, str(m.root))
                if col == 1:
                    item.setForeground(QColor({"running": "#3d8bfd", "succeeded": "#2fb344", "failed": "#e5484d"}.get(state, "#8a8f98")))
                self.ops.setItem(i, col, item)
            if m.job_id == selected:
                self.ops.selectRow(i)
            if not active and result and result.get("finished_at", 0) > time.time() - 5:
                QTimer.singleShot(500, self.refresh_checks)

    def _select_op(self) -> None:
        items = self.ops.selectedItems()
        if not items:
            return
        from pathlib import Path

        root = Path(items[0].data(Qt.ItemDataRole.UserRole))
        if self.selected_maint is None or self.selected_maint.root != root:
            self.selected_maint = MaintPaths(root)
            self._log_offset = 0
            self.op_log.clear()
            self._tail_log()

    def _tail_log(self) -> None:
        m = self.selected_maint
        if m is None or not m.log.exists():
            return
        try:
            with open(m.log, "rb") as fh:
                fh.seek(self._log_offset)
                data = fh.read(2 * 1024 * 1024)
        except OSError:
            return
        if not data:
            return
        self._log_offset += len(data)
        text = data.decode("utf-8", errors="replace").replace("\r", "")
        self.op_log.appendPlainText(text.rstrip("\n"))
        name, _ = split_action(m.action().get("action", ""))
        if name.startswith("engine-"):
            prog = LegendaryProgress()
            for line in m.log.read_text(errors="replace").splitlines()[-400:]:
                feed_legendary(prog, line)
            self.op_progress.setVisible(True)
            self.op_progress.setValue(int(prog.percent))
            self.op_detail.setText(
                f"{prog.done}/{prog.total} files · downloaded {prog.downloaded_mib:.0f} MiB · {prog.speed_mib_s:.1f} MiB/s · {prog.eta} left"
                if prog.total
                else ""
            )
        elif name in ("msvc-install", "netfx-install"):
            prog = VsDownloadProgress()
            for line in m.log.read_text(errors="replace").splitlines():
                feed_vsdownload(prog, line)
            self.op_progress.setVisible(False)
            self.op_detail.setText(
                f"{prog.selected} packages ({prog.download_size}) · downloaded {prog.downloaded} · extracted {prog.extracted + prog.unpacked}" if prog.selected else ""
            )
        else:
            self.op_progress.setVisible(False)
            self.op_detail.setText("")
