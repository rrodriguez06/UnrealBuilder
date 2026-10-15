"""Real systemd --user integration (opt-in: pytest -m systemd): unit launch, runner, finalize, cancel."""

import os
import shutil
import subprocess
import time

import pytest

from unrealbuilder.core.jobs import JobState
from unrealbuilder.core.launcher import BuildOptions, make_spec, prepare_job
from unrealbuilder.core.prepare import find_project
from unrealbuilder.core.settings import save_settings
from unrealbuilder.core.systemd import run_unit, show

from .test_runner_fake_wine import env  # noqa: F401 - fixture reuse

pytestmark = pytest.mark.systemd


def launch(settings, monkeypatch, **opts):
    settings.notifications.on_success = False
    settings.notifications.on_failure = False
    save_settings(settings)
    info = find_project(settings, "Demo")
    spec = make_spec(settings, info, BuildOptions(**opts))
    paths, unit = prepare_job(settings, spec)
    unit.environment = {
        k: os.environ[k]
        for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "FAKE_FIXTURES", "FAKE_UE_ROOT", "FAKE_SMOKE")
        if k in os.environ
    }
    unit.environment["UB_MIN_FREE_GIB"] = "0"
    proc = run_unit(unit)
    assert proc.returncode == 0, proc.stderr
    return paths, unit.unit


def wait_final(paths, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = paths.read_result()
        if r and r.get("finalized_at"):
            return r
        time.sleep(0.5)
    raise AssertionError("no finalized result")


@pytest.fixture(autouse=True)
def real_runtime_dir(monkeypatch):
    # systemd-run --user talks to $XDG_RUNTIME_DIR/bus: the isolated tmp runtime dir would break it.
    monkeypatch.setenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


@pytest.fixture(autouse=True)
def need_systemd(real_runtime_dir):
    if not shutil.which("systemd-run") or subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode not in (0, 1):
        pytest.skip("systemd --user unavailable")


def test_unit_build_and_finalize(env, monkeypatch):  # noqa: F811
    paths, unit = launch(env, monkeypatch, zip=True, smoke_test=False)
    result = wait_final(paths)
    assert result["state"] == JobState.SUCCEEDED
    assert result["service_result"] == "success"
    assert paths.zips()
    assert show(unit, ["ActiveState"]).get("ActiveState") in ("inactive", "")


def test_unit_cancel(env, monkeypatch):  # noqa: F811
    paths, unit = launch(env, monkeypatch, zip=False, smoke_test=True)
    deadline = time.monotonic() + 60
    while (paths.read_status() or {}).get("step") != "smoke" and time.monotonic() < deadline:
        time.sleep(0.3)
    paths.cancel_marker.write_text("x")
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=60)
    result = wait_final(paths)
    assert result["state"] == JobState.CANCELLED
