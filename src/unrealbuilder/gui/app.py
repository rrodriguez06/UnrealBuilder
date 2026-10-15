"""QApplication bootstrap: theme, single instance, GUI lock."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from unrealbuilder import APP_ID, APP_NAME, __version__
from unrealbuilder.core.notify import GuiLock
from unrealbuilder.gui.theme import apply_theme

ICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "unrealbuilder.svg"
SERVER_NAME = f"unrealbuilder-{os.getuid()}"


def _activate_existing() -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"activate\n")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def run_gui(argv: list[str]) -> int:
    app = QApplication([sys.argv[0], *argv])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    # Wayland app_id: must match the .desktop file name for Hyprland rules and launchers.
    app.setDesktopFileName(APP_ID)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    apply_theme(app)

    if _activate_existing():
        print("UnrealBuilder is already running: activated the existing window.")
        return 0
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    lock = GuiLock()
    lock.acquire()  # tells the runners' finalize step that the GUI owns notifications

    from unrealbuilder.gui.main_window import MainWindow

    window = MainWindow()

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(on_connection)
    window.show()
    try:
        return app.exec()
    finally:
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().waitForDone(5000)
        lock.release()
        server.close()
