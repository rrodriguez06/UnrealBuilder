"""Render every page of the GUI offscreen with the real settings/data (for visual checks and docs/screenshots).

python tools/screenshots.py [out_dir]   (UB_JOB=<job_id> picks the build shown in the detail window)
"""

import os
import sys
import time
from pathlib import Path

# Always offscreen: a live Wayland window would be resized by the compositor (tiling) and the shots would vary.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unrealbuilder.gui.main_window import PAGES, MainWindow  # noqa: E402
from unrealbuilder.gui.theme import apply_theme  # noqa: E402

WINDOW_SIZE = (1257, 1362)  # size of the images in docs/screenshots
DETAIL_SIZE = (1257, 676)
DATA_TIMEOUT_S = 600  # btrfs sizes of the engines can take a while

out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
apply_theme(app)
win = MainWindow()
win.resize(*WINDOW_SIZE)
win.statusBar().setSizeGripEnabled(False)
win.show()
win.storage.refresh()
started = time.monotonic()


def wait_for_data() -> None:
    """Shoot once projects, doctor checks and storage sizes are loaded (all computed in the thread pool)."""
    ready = bool(win.projects.rows) and bool(win.toolchain.checks) and bool(win.storage.roots)
    if ready or time.monotonic() - started > DATA_TIMEOUT_S:
        QTimer.singleShot(1000, shoot)
    else:
        QTimer.singleShot(500, wait_for_data)


def shoot() -> None:
    for i, (key, _label) in enumerate(PAGES):
        win.sidebar.setCurrentRow(i)
        app.processEvents()
        win.grab().save(str(out / f"page_{i}_{key}.png"))
    wanted = os.environ.get("UB_JOB")
    entries = [e for e in win.supervisor.entries if e.paths is not None and (not wanted or e.job_id == wanted)]
    if entries:
        win.open_details(entries[0].job_id)
        win.details[entries[0].job_id].resize(*DETAIL_SIZE)
        QTimer.singleShot(2500, shoot_detail)
    else:
        app.quit()


def shoot_detail() -> None:
    for w in win.details.values():
        for t in range(w.tabs.count()):
            w.tabs.setCurrentIndex(t)
            app.processEvents()
            w.grab().save(str(out / f"detail_{t}.png"))
        break
    app.quit()


QTimer.singleShot(4000, wait_for_data)
app.exec()
from PySide6.QtCore import QThreadPool  # noqa: E402

QThreadPool.globalInstance().waitForDone(10000)
win.monitor.stop()
print("saved to", out)
