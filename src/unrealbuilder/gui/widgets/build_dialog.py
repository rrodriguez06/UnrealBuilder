"""Dialog to configure and queue a Win64 build."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from unrealbuilder.core.launcher import BuildOptions
from unrealbuilder.core.settings import Settings
from unrealbuilder.core.uproject import ProjectAnalysis


class BuildDialog(QDialog):
    def __init__(self, project: str, settings: Settings, analysis: ProjectAnalysis | None, configuration: str = "Development", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Build Win64 — {project}")
        r, o = settings.resources, settings.outputs
        self.config = QComboBox()
        self.config.addItems(["Development", "Shipping", "DebugGame"])
        self.config.setCurrentText(configuration)
        self.clean = QCheckBox("Clean (purge the mirror's Intermediate / Saved / Binaries)")
        self.incremental = QCheckBox("Incremental cook (-cookincremental)")
        self.zip = QCheckBox("Create a zip to ship")
        self.zip.setChecked(o.zip)
        self.smoke = QCheckBox("Startup test under Wine (no rendering)")
        self.smoke.setChecked(o.smoke_test)
        self.workers = QSpinBox()
        self.workers.setRange(1, 24)
        self.workers.setValue(r.shader_workers)
        self.memory = QSpinBox()
        self.memory.setRange(4, 64)
        self.memory.setSuffix(" GiB")
        self.memory.setValue(r.job_memory_max_gib)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Extra UAT arguments (advanced)")

        info = QLabel()
        info.setWordWrap(True)
        if analysis is not None:
            lines = [f"<b>Type:</b> {analysis.kind_label}"]
            if analysis.optional_patches:
                lines.append(f"<b>Mirror:</b> {', '.join(analysis.optional_patches)} made optional")
            for w in analysis.warnings:
                lines.append(f"⚠ {w}")
            for b in analysis.blockers:
                lines.append(f"<span style='color:#e5484d'>✗ {b}</span>")
            info.setText("<br>".join(lines))

        form = QFormLayout()
        form.addRow("Configuration", self.config)
        form.addRow("", self.clean)
        form.addRow("", self.incremental)
        form.addRow("", self.zip)
        form.addRow("", self.smoke)
        form.addRow("Shader workers", self.workers)
        form.addRow("Unit memory limit", self.memory)
        form.addRow("UAT arguments", self.extra)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.queue_btn = buttons.addButton("Queue build", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if analysis is not None and analysis.blockers:
            self.queue_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def options(self) -> BuildOptions:
        return BuildOptions(
            configuration=self.config.currentText(),
            clean=self.clean.isChecked(),
            incremental_cook=self.incremental.isChecked(),
            zip=self.zip.isChecked(),
            smoke_test=self.smoke.isChecked(),
            shader_workers=self.workers.value(),
            memory_max_gib=self.memory.value(),
            extra_uat_args=self.extra.text().split() or None,
        )
