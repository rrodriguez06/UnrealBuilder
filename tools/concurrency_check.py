"""Manual check: two different projects build in parallel (one Wine prefix per slot), offscreen GUI services.

    python tools/concurrency_check.py ProjectA ProjectB
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unrealbuilder.core.jobs import JobState  # noqa: E402
from unrealbuilder.core.launcher import BuildOptions, make_spec  # noqa: E402
from unrealbuilder.core.prepare import find_project  # noqa: E402
from unrealbuilder.core.settings import load_settings  # noqa: E402
from unrealbuilder.gui.main_window import MainWindow  # noqa: E402

settings = load_settings()
settings.resources.max_concurrent_builds = 2
app = QApplication([])
win = MainWindow(settings)
jobs = []
for name in sys.argv[1:3]:
    spec = make_spec(settings, find_project(settings, name), BuildOptions(zip=False, smoke_test=False))
    jobs.append(spec.job_id)
    win.supervisor.enqueue(spec)
    time.sleep(1.1)  # distinct job ids
max_parallel = 0
start = time.monotonic()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def poll():
    global max_parallel
    entries = [win.supervisor.entry(j) for j in jobs]
    if any(e is None for e in entries):
        return
    running = [e for e in entries if e.is_running]
    if len(running) > max_parallel:
        max_parallel = len(running)
        log(f"{len(running)} build(s) in parallel: " + ", ".join(f"{e.spec.project} → {Path(e.spec.prefix).name}, Zen {e.spec.zen_port}, {e.spec.shader_workers} workers" for e in running))
    if all(e.state.is_final for e in entries):
        for e in entries:
            log(f"{e.spec.project}: {e.state.value} — {(e.result or {}).get('message')} ({(e.result or {}).get('memory_peak', 0) / 2**30:.1f} GiB peak)")
        app.exit(0 if all(e.state == JobState.SUCCEEDED for e in entries) and max_parallel == 2 else 1)
    elif time.monotonic() - start > 1800:
        log("TIMEOUT")
        app.exit(2)


timer = QTimer()
timer.timeout.connect(poll)
timer.start(1000)
rc = app.exec()
win.monitor.stop()
log(f"max parallelism observed: {max_parallel}")
sys.exit(rc)
