"""Explicit Microsoft license consent, asked every time (never stored)."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from unrealbuilder.core.install.msvc import LICENSE_URL


class LicenseDialog(QDialog):
    def __init__(self, what: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Microsoft license")
        text = QLabel(
            f"<p>The <b>{what}</b> operation downloads Microsoft Visual Studio 2022 Build Tools packages through msvc-wine.</p>"
            "<p>These tools are covered by the Visual Studio license (the Community license covers individuals "
            "and small organizations). Your acceptance applies to this operation only and is not remembered.</p>"
        )
        text.setWordWrap(True)
        open_btn = QPushButton("Read the license…")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LICENSE_URL)))
        self.accept_box = QCheckBox("I have read and accept the Microsoft Visual Studio 2022 Build Tools license")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.addButton("Install", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setEnabled(False)
        self.accept_box.toggled.connect(self.ok.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(open_btn)
        layout.addWidget(self.accept_box)
        layout.addWidget(buttons)
