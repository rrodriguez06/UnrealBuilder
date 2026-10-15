"""Manual end-to-end check of the GUI services (offscreen), on the real toolchain.

    phase A:  python tools/gui_reattach_check.py start <Project>   → queues a build, waits until it cooks, exits
    phase B:  python tools/gui_reattach_check.py reattach <job_id>  → new GUI reattaches, sees progress, cancels it
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
from unrealbuilder.gui.main_window import MainWindow  # noqa: E402

mode, arg = sys.argv[1], sys.argv[2]
app = QApplication([])
win = MainWindow()
deadline = time.monotonic() + 600
seen = {"snap": None, "job": None, "cancel_sent": False}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def on_snapshot(snap) -> None:
    if seen["job"] and snap.job_id == seen["job"]:
        seen["snap"] = snap


win.monitor.snapshot.connect(on_snapshot)

if mode == "start":
    info = find_project(win.settings, arg)
    spec = make_spec(win.settings, info, BuildOptions(zip=False, smoke_test=False))
    seen["job"] = spec.job_id
    win.supervisor.enqueue(spec)
    log(f"queued {spec.job_id}")
else:
    seen["job"] = arg


def poll() -> None:
    job = seen["job"]
    entry = win.supervisor.entry(job)
    snap = seen["snap"]
    if time.monotonic() > deadline:
        log("TIMEOUT")
        app.exit(2)
        return
    if entry is None:
        return
    if mode == "start":
        if snap and snap.step == "cook" and snap.fraction > 0.05:
            log(f"running: {entry.state.value} step={snap.step} {snap.fraction:.0%} {snap.detail} RAM={snap.mem_current}")
            log(f"JOB_ID={job}")
            app.exit(0)
        return
    # reattach mode
    if snap and not seen["cancel_sent"] and entry.state == JobState.RUNNING:
        log(f"reattached: {entry.state.value} step={snap.step} {snap.fraction:.0%} {snap.detail} errors={snap.errors}")
        win.supervisor.cancel(entry)
        seen["cancel_sent"] = True
        log("cancel sent")
        return
    if seen["cancel_sent"] and entry.state.is_final:
        log(f"final state: {entry.state.value} — {(entry.result or {}).get('message')}")
        app.exit(0 if entry.state == JobState.CANCELLED else 1)


timer = QTimer()
timer.timeout.connect(poll)
timer.start(500)
rc = app.exec()
win.monitor.stop()
if mode == "reattach":
    prefix = str(win.settings.prefix)
    time.sleep(3)
    left = []
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                env = (proc / "environ").read_bytes()
            except OSError:
                continue
            if f"WINEPREFIX={prefix}".encode() in env:
                left.append(proc.name)
    log(f"wine processes left in prefix: {len(left)} {left[:5]}")
    rc = rc or (1 if left else 0)
sys.exit(rc)
