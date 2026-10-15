"""Settings page (resources, cook, outputs, retention, notifications)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from unrealbuilder.core.prepare import available_cpus
from unrealbuilder.core.settings import Settings, save_settings, settings_path


def spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        r, c, o, ret, n = settings.resources, settings.cook, settings.outputs, settings.retention, settings.notifications
        nproc = available_cpus()

        self.workers = spin(1, nproc, r.shader_workers)
        self.workers_hint = QLabel()
        self.workers.valueChanged.connect(lambda v: self.workers_hint.setText(f"→ NumUnusedShaderCompilingThreads = {nproc - v}"))
        self.workers.valueChanged.emit(self.workers.value())
        self.memory = spin(4, 128, r.job_memory_max_gib, " GiB")
        self.swap = spin(0, 64, r.job_swap_max_gib, " GiB")
        self.budget = spin(4, 128, r.ram_budget_gib, " GiB")
        self.concurrency = QComboBox()
        self.concurrency.addItems(["1 build at a time", "2 builds in parallel (experimental)"])
        self.concurrency.setCurrentIndex(max(0, min(1, r.max_concurrent_builds - 1)))
        self.cpu_weight = spin(1, 10000, r.cpu_weight)
        self.io_weight = spin(1, 10000, r.io_weight)
        self.nice = spin(-20, 19, r.nice)
        self.cpus = QComboBox()
        self.cpus.setEditable(True)
        self.cpus.addItem("")
        self.cpus.setCurrentText(r.allowed_cpus)
        self.cpus.setToolTip("AllowedCPUs of the build unit, e.g. 0-5,12-17 (empty = all cores)")
        self.runtime = spin(1, 48, r.max_runtime_hours, " h")

        self.zen_port = spin(1024, 65535, c.zen_port)
        self.verbose_shaders = QCheckBox("Detailed shader progress (LogShaderCompilers=Verbose)")
        self.verbose_shaders.setChecked(c.verbose_shader_progress)
        self.memory_guard = QCheckBox("Cooker memory guard (MemoryMaxUsedPhysical, deprecated by Epic)")
        self.memory_guard.setChecked(c.cooker_memory_guard)

        self.zip = QCheckBox("Create a zip")
        self.zip.setChecked(o.zip)
        self.no_pdb = QCheckBox("Leave .pdb files out of the zip")
        self.no_pdb.setChecked(o.zip_exclude_pdb)
        self.smoke = QCheckBox("Startup test under Wine")
        self.smoke.setChecked(o.smoke_test)
        self.smoke_timeout = spin(20, 600, o.smoke_timeout_s, " s")
        self.staged = QCheckBox("Delete the mirror's Saved/StagedBuilds after archiving")
        self.staged.setChecked(o.delete_staged_after_archive)
        self.nodebug = QCheckBox("-nodebuginfo for Shipping")
        self.nodebug.setChecked(o.nodebuginfo_shipping)

        self.keep = spin(1, 50, ret.keep_successful_per_config)
        self.compact = QCheckBox("Older builds keep only their zip")
        self.compact.setChecked(ret.compact_older_to_zip_only)
        self.failed_days = spin(1, 365, ret.failed_logs_days, " days")

        self.n_success = QCheckBox("Build succeeded")
        self.n_success.setChecked(n.on_success)
        self.n_failure = QCheckBox("Build failed")
        self.n_failure.setChecked(n.on_failure)
        self.n_stall = QCheckBox("Shader worker stalled")
        self.n_stall.setChecked(n.on_stall)

        self.roots = QLineEdit(";".join(settings.paths.project_roots))
        self.ue_root = QLineEdit(settings.paths.ue_root)
        self.toolchain = QLineEdit(settings.paths.toolchain_root)

        res = QGroupBox("Wine build resources")
        f = QFormLayout(res)
        f.addRow("Shader workers", self._row(self.workers, self.workers_hint))
        f.addRow("Max memory per build (MemoryMax)", self.memory)
        f.addRow("Max swap per build (MemorySwapMax)", self.swap)
        f.addRow("Concurrency", self.concurrency)
        f.addRow("Total RAM budget (slice)", self.budget)
        f.addRow("CPU / IO weight", self._row(self.cpu_weight, self.io_weight))
        f.addRow("Nice", self.nice)
        f.addRow("Allowed cores", self.cpus)
        f.addRow("Max build duration", self.runtime)
        f.addRow(QLabel("<i>Never MemoryHigh: throttling gets the build killed by systemd-oomd.</i>"))

        cook = QGroupBox("Cook")
        f = QFormLayout(cook)
        f.addRow("Zen port (≠ 8558 used by the Linux editor)", self.zen_port)
        f.addRow(self.verbose_shaders)
        f.addRow(self.memory_guard)

        outputs = QGroupBox("Outputs")
        f = QFormLayout(outputs)
        for w in (self.zip, self.no_pdb, self.smoke):
            f.addRow(w)
        f.addRow("Startup test timeout", self.smoke_timeout)
        f.addRow(self.staged)
        f.addRow(self.nodebug)

        retention = QGroupBox("Build retention")
        f = QFormLayout(retention)
        f.addRow("Successful builds kept per configuration", self.keep)
        f.addRow(self.compact)
        f.addRow("Keep logs of failed builds for", self.failed_days)

        notif = QGroupBox("Notifications")
        f = QFormLayout(notif)
        for w in (self.n_success, self.n_failure, self.n_stall):
            f.addRow(w)

        paths = QGroupBox("Paths")
        f = QFormLayout(paths)
        f.addRow("Project roots (separated by ;)", self.roots)
        f.addRow("Unreal root (U: drive)", self.ue_root)
        f.addRow("Toolchain Win64", self.toolchain)

        save = QPushButton("Save")
        save.clicked.connect(self.save)
        self.saved_label = QLabel(f"<small>{settings_path()}</small>")

        body = QWidget()
        bl = QVBoxLayout(body)
        for g in (res, cook, outputs, retention, notif, paths):
            bl.addWidget(g)
        bottom = QHBoxLayout()
        bottom.addWidget(save)
        bottom.addWidget(self.saved_label)
        bottom.addStretch(1)
        bl.addLayout(bottom)
        bl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

    @staticmethod
    def _row(*widgets) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            lay.addWidget(x)
        lay.addStretch(1)
        return w

    def save(self) -> None:
        s = self.settings
        r, c, o, ret, n = s.resources, s.cook, s.outputs, s.retention, s.notifications
        r.shader_workers = self.workers.value()
        r.job_memory_max_gib = self.memory.value()
        r.job_swap_max_gib = self.swap.value()
        r.ram_budget_gib = self.budget.value()
        r.max_concurrent_builds = self.concurrency.currentIndex() + 1
        r.cpu_weight = self.cpu_weight.value()
        r.io_weight = self.io_weight.value()
        r.nice = self.nice.value()
        r.allowed_cpus = self.cpus.currentText().strip()
        r.max_runtime_hours = self.runtime.value()
        c.zen_port = self.zen_port.value()
        c.verbose_shader_progress = self.verbose_shaders.isChecked()
        c.cooker_memory_guard = self.memory_guard.isChecked()
        o.zip = self.zip.isChecked()
        o.zip_exclude_pdb = self.no_pdb.isChecked()
        o.smoke_test = self.smoke.isChecked()
        o.smoke_timeout_s = self.smoke_timeout.value()
        o.delete_staged_after_archive = self.staged.isChecked()
        o.nodebuginfo_shipping = self.nodebug.isChecked()
        ret.keep_successful_per_config = self.keep.value()
        ret.compact_older_to_zip_only = self.compact.isChecked()
        ret.failed_logs_days = self.failed_days.value()
        n.on_success = self.n_success.isChecked()
        n.on_failure = self.n_failure.isChecked()
        n.on_stall = self.n_stall.isChecked()
        s.paths.project_roots = [x.strip() for x in self.roots.text().split(";") if x.strip()]
        s.paths.ue_root = self.ue_root.text().strip()
        s.paths.toolchain_root = self.toolchain.text().strip()
        path = save_settings(s)
        self.saved_label.setText(f"<small>Saved to {path}</small>")
        self.saved.emit()
