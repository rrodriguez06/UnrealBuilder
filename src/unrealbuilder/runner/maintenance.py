"""Maintenance actions executed inside `ub-maint-*` units: python -m unrealbuilder.runner maint <dir>."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
import traceback
from pathlib import Path

from unrealbuilder.core.install import engine as engine_install
from unrealbuilder.core.install import msvc as msvc_install
from unrealbuilder.core.install import prefix as prefix_install
from unrealbuilder.core.maint import MaintPaths, split_action
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import expand, load_settings


class MaintRunner:
    def __init__(self, root: Path) -> None:
        self.paths = MaintPaths(root)
        data = self.paths.action()
        self.action = data.get("action", "")
        self.params = data.get("params", {}) or {}
        self.settings = load_settings()
        self.layout = ToolchainLayout.from_settings(self.settings)
        self.log_fh = open(self.paths.log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        self.child: subprocess.Popen | None = None
        self.cancelled = False
        self.status = {"action": self.action, "state": "running", "started_at": time.time(), "pid": os.getpid(), "message": ""}

    def log(self, text: str) -> None:
        self.log_fh.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.status["message"] = text
        self.paths.write_status(self.status)

    def run_cmd(self, argv: list[str], env: dict[str, str] | None = None) -> int:
        self.log("$ " + shlex.join(argv))
        self.log_fh.flush()
        self.child = subprocess.Popen(argv, stdout=self.log_fh, stderr=subprocess.STDOUT, env=env, cwd=self.layout.root if self.layout.root.exists() else None, start_new_session=True)
        try:
            return self.child.wait()
        finally:
            self.child = None

    def _on_signal(self, _signum, _frame) -> None:
        self.cancelled = True
        if self.child is not None and self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGTERM)
            except OSError:
                pass

    def execute(self) -> int:  # noqa: C901 - flat dispatch
        name, arg = split_action(self.action)
        s, layout = self.settings, self.layout
        accept = bool(self.params.get("accept_license"))
        if name == "prefix-create":
            rc = prefix_install.create_prefix(s, layout, self.run_cmd)
            if rc == 0:
                prefix_install.ensure_drive_link(layout)
                rc = prefix_install.apply_registry(s, layout, prefix_install.REG_OVERRIDES + prefix_install.REG_VCTIP, self.run_cmd)
            if rc == 0:
                try:
                    rc = prefix_install.install_vcredist(s, layout, self.run_cmd)
                except FileNotFoundError as exc:
                    self.log(f"VC++ runtime not installed: {exc}")
            return rc
        if name == "drive-link":
            self.log(f"Link created: {prefix_install.ensure_drive_link(layout)}")
            return 0
        if name == "dll-override":
            return prefix_install.apply_registry(s, layout, prefix_install.REG_OVERRIDES, self.run_cmd)
        if name == "vctip":
            return prefix_install.apply_registry(s, layout, prefix_install.REG_VCTIP, self.run_cmd)
        if name == "vcredist":
            return prefix_install.install_vcredist(s, layout, self.run_cmd)
        if name == "autosdk-links":
            eng = next(iter(s.engines.values()))
            made = prefix_install.ensure_autosdk_links(layout, expand(eng.autosdk_root))
            self.log(f"Links created: {', '.join(map(str, made)) or 'already up to date'}")
            return 0
        if name == "msvc-install":
            rc = msvc_install.install_msvc(layout, self.run_cmd, accept)
            if rc == 0:
                eng = next(iter(s.engines.values()))
                prefix_install.ensure_autosdk_links(layout, expand(eng.autosdk_root))
            return rc
        if name == "netfx-install":
            return msvc_install.install_netfx(layout, self.run_cmd, accept)
        if name in ("engine-install", "engine-update"):
            eng = s.engines[arg or next(iter(s.engines))]
            return engine_install.install_or_update(
                eng.legendary_app, expand(eng.linux_root), expand(eng.win_root), self.run_cmd, update=name == "engine-update", log=self.log
            )
        if name == "slot-create":
            from unrealbuilder.core.slots import slots

            slot = slots(s)[int(arg or 1)]
            if slot.exists:
                self.log(f"Slot {slot.index} already exists: {slot.prefix}")
                return 0
            self.log(f"Reflink-cloning the prefix to {slot.prefix}")
            rc = self.run_cmd(["cp", "-a", "--reflink=auto", str(layout.prefix), str(slot.prefix)])
            return rc
        if name == "dedupe":
            from unrealbuilder.core.storage.dedupe import run_dedupe

            report = run_dedupe([Path(p) for p in self.params.get("roots", [])], log=self.log, state=self.paths.root, dry_run=bool(self.params.get("dry_run")))
            self.status["report"] = report
            return 0
        if name == "cleanup":
            from unrealbuilder.core.storage.cleanup import apply_cleanup

            freed = apply_cleanup(s, self.params.get("items", []), log=self.log)
            self.status["freed"] = freed
            return 0
        raise ValueError(f"Unknown action: {self.action}")

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._on_signal)
        result = {"action": self.action}
        try:
            rc = self.execute()
            state = "cancelled" if self.cancelled else ("succeeded" if rc == 0 else "failed")
            result.update(state=state, exit_code=rc, message="Done" if rc == 0 else f"Failed (code {rc})")
        except Exception as exc:  # noqa: BLE001
            self.log(traceback.format_exc())
            result.update(state="failed", message=str(exc))
            rc = 1
        result["finished_at"] = time.time()
        for k in ("report", "freed"):
            if k in self.status:
                result[k] = self.status[k]
        self.status["state"] = result["state"]
        self.paths.write_status(self.status)
        self.paths.write_result(result)
        self.log(f"Result: {result['state']} — {result.get('message', '')}")
        self.log_fh.close()
        return 0 if result["state"] == "succeeded" else 1


def run_maint(root: Path) -> int:
    return MaintRunner(root).run()
