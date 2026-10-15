"""Caches & storage page: inventory with btrfs sizes, open/delete, cleanup presets, retention and dedupe."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.engines import engine_pairs
from unrealbuilder.core.fsutil import read_json
from unrealbuilder.core.maint import list_maint, split_action
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.storage.cleanup import junk_items, retention_plan
from unrealbuilder.core.storage.inventory import StorageItem, build_inventory, fill_sizes
from unrealbuilder.gui.services.tasks import run_in_pool
from unrealbuilder.gui.widgets.common import human_bytes, open_path

COLUMNS = ["Item", "Total", "Exclusive", "Shared", "Modified", "Deletable", "Effect"]


class PreviewDialog(QDialog):
    """Checkbox list of paths to delete, with sizes."""

    def __init__(self, title: str, entries: list[tuple[Path, str, int | None]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 480)
        self.list = QListWidget()
        for path, reason, size in entries:
            item = QListWidgetItem(f"{path}  —  {reason}" + (f"  ({human_bytes(size)})" if size else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Delete selected", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setEnabled(bool(entries))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Checked items will be deleted (the operation runs in a systemd unit)."))
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class StoragePage(QWidget):
    def __init__(self, settings: Settings, supervisor, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.supervisor = supervisor
        self.layout_paths = ToolchainLayout.from_settings(settings)
        self.roots: list[StorageItem] = []

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self._open(item))

        refresh = QPushButton("Compute sizes")
        refresh.clicked.connect(self.refresh)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(lambda: self._open(self.tree.currentItem()))
        delete = QPushButton("Delete…")
        delete.clicked.connect(self._delete_selected)
        junk = QPushButton("Clean up junk…")
        junk.clicked.connect(self._clean_junk)
        retention = QPushButton("Apply retention…")
        retention.clicked.connect(self._apply_retention)
        pin = QPushButton("Pin / unpin")
        pin.setToolTip("A pinned build is never deleted by the retention policy")
        pin.clicked.connect(self._toggle_pin)
        self.status = QLabel("")
        bar = QHBoxLayout()
        for w in (refresh, open_btn, delete, junk, retention, pin):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.status)

        # Dedupe panel
        self.scope_engines = QCheckBox("Linux ↔ Windows engines")
        self.scope_engines.setChecked(True)
        self.scope_toolchain = QCheckBox("MSVC / SDK")
        self.scope_toolchain.setChecked(True)
        self.scope_builds = QCheckBox("Mirrors and builds")
        estimate = QPushButton("Estimate")
        estimate.clicked.connect(lambda: self._dedupe(dry_run=True))
        run = QPushButton("Deduplicate")
        run.clicked.connect(lambda: self._dedupe(dry_run=False))
        self.dedupe_report = QLabel("")
        self.dedupe_report.setWordWrap(True)
        dl = QHBoxLayout()
        for w in (self.scope_engines, self.scope_toolchain, self.scope_builds, estimate, run):
            dl.addWidget(w)
        dl.addStretch(1)
        dedupe_box = QGroupBox("btrfs deduplication (FIDEDUPERANGE: the kernel compares the bytes, no content is modified)")
        dbl = QVBoxLayout(dedupe_box)
        dbl.addLayout(dl)
        dbl.addWidget(self.dedupe_report)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.tree, 1)
        layout.addWidget(dedupe_box)
        supervisor.maintChanged.connect(self._on_maint)

    # --- data --------------------------------------------------------------------
    def refresh(self) -> None:
        self.status.setText("Measuring sizes (btrfs filesystem du)…")

        def work():
            roots = build_inventory(self.settings)
            fill_sizes(roots)
            return roots

        run_in_pool(work, self._populate, lambda err: self.status.setText("Measurement failed"))

    def _populate(self, roots: list[StorageItem]) -> None:
        self.roots = roots
        self.tree.clear()
        for r in roots:
            self.tree.addTopLevelItem(self._make(r))
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)
        self.status.setText(f"Measured at {time.strftime('%H:%M:%S')}")
        self._show_last_report()

    def _make(self, it: StorageItem) -> QTreeWidgetItem:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.mtime)) if it.mtime else ""
        node = QTreeWidgetItem(
            [it.label, human_bytes(it.total), human_bytes(it.exclusive), human_bytes(it.shared), when, "yes" if it.deletable else "", it.effect or it.reason]
        )
        node.setData(0, Qt.ItemDataRole.UserRole, it)
        node.setToolTip(0, str(it.path))
        node.setToolTip(6, it.reason)
        if it.deletable:
            node.setForeground(5, QColor("#2fb344"))
        for c in it.children:
            node.addChild(self._make(c))
        return node

    def _items(self) -> list[StorageItem]:
        return [i.data(0, Qt.ItemDataRole.UserRole) for i in self.tree.selectedItems() if i.data(0, Qt.ItemDataRole.UserRole)]

    # --- actions ------------------------------------------------------------------
    def _open(self, node) -> None:
        if node is None:
            return
        it: StorageItem = node.data(0, Qt.ItemDataRole.UserRole)
        if it:
            open_path(it.path if it.path.is_dir() else it.path.parent, self)

    def _busy_check(self, items: list[StorageItem]) -> bool:
        if any(it.needs_idle for it in items) and self.supervisor.running_entries():
            QMessageBox.information(self, "Build running", "Some items are in use by running builds: wait for them to finish.")
            return False
        if self.supervisor.maint_running():
            QMessageBox.information(self, "Operation running", "A maintenance operation is already running.")
            return False
        return True

    def _launch_cleanup(self, paths: list[str]) -> None:
        if paths:
            self.supervisor.launch_maint("cleanup", {"items": paths})
            self.status.setText(f"Deleting {len(paths)} item(s)…")

    def _delete_selected(self) -> None:
        items = [it for it in self._items() if it.deletable]
        if not items:
            QMessageBox.information(self, "Nothing to delete", "Select items marked as deletable.")
            return
        if not self._busy_check(items):
            return
        dlg = PreviewDialog("Delete", [(it.path, it.effect or it.reason, it.exclusive) for it in items], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._launch_cleanup(dlg.selected())

    def _clean_junk(self) -> None:
        items = junk_items(self.settings)
        sizes = {it.path: it.exclusive for r in self.roots for it in r.walk()}
        if not self._busy_check(items):
            return
        dlg = PreviewDialog("Clean up junk", [(it.path, it.reason, sizes.get(it.path)) for it in items], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._launch_cleanup(dlg.selected())

    def _apply_retention(self) -> None:
        plan = retention_plan(self.settings)
        if not plan:
            QMessageBox.information(self, "Retention", "Nothing to do: the retention policy is already met.")
            return
        if not self._busy_check([]):
            return
        sizes = {it.path: it.exclusive for r in self.roots for it in r.walk()}
        dlg = PreviewDialog("Retention policy", [(a.path, a.reason, sizes.get(a.path)) for a in plan], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._launch_cleanup(dlg.selected())

    def _toggle_pin(self) -> None:
        for it in self._items():
            if it.id.startswith("build-") and (it.path / "job.json").exists():
                marker = it.path / "pinned"
                if marker.exists():
                    marker.unlink()
                else:
                    marker.write_text("pinned\n")
        self.refresh()

    def _dedupe(self, dry_run: bool) -> None:
        roots: list[str] = []
        if self.scope_engines.isChecked():
            for pair in engine_pairs(self.settings).values():
                roots += [str(pair.linux.root), str(pair.win.root)]
        if self.scope_toolchain.isChecked():
            roots.append(str(self.layout_paths.msvc))
        if self.scope_builds.isChecked():
            roots += [str(self.layout_paths.projects), str(self.layout_paths.builds)]
        roots = [r for r in roots if Path(r).exists()]
        if not roots or not self._busy_check([]):
            return
        self.supervisor.launch_maint("dedupe", {"roots": roots, "dry_run": dry_run})
        self.dedupe_report.setText("Estimating…" if dry_run else "Deduplicating…")

    def _show_last_report(self) -> None:
        for m in list_maint(30):
            name, _ = split_action(m.action().get("action", ""))
            if name != "dedupe":
                continue
            report = read_json(m.root / "dedupe-report.json", default=None)
            if not report:
                continue
            dry = m.action().get("params", {}).get("dry_run")
            gib = (report["estimate_bytes"] if dry else report["deduped_bytes"]) / 2**30
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.action().get("created_at", 0)))
            self.dedupe_report.setText(
                f"{'Estimate' if dry else 'Last deduplication'} ({when}): {gib:.2f} GiB over {report['pairs']} pairs · "
                f"{report['already_shared']} pairs already shared · {report['errors']} error(s) · {report['seconds']} s"
            )
            return

    def _on_maint(self, entries) -> None:
        for m, active in entries[:3]:
            name, _ = split_action(m.action().get("action", ""))
            if not active and name in ("dedupe", "cleanup") and (m.read_result() or {}).get("finished_at", 0) > time.time() - 4:
                self.refresh()
                return
