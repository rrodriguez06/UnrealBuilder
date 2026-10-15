import time

from unrealbuilder.core.jobs import JobSpec, JobState, create_build_dir
from unrealbuilder.core.jobstore import build_unit, entry_for_build, scan_jobs
from unrealbuilder.core.queue import BuildQueue


def make(tmp_path, project="P", created_at=None):
    spec = JobSpec(job_id=JobSpec.make_id(project, "Development"), project=project, project_dir="/p", uproject="P.uproject")
    if created_at:
        spec.created_at = created_at
    return create_build_dir(tmp_path / "builds", spec), spec


def test_reconcile_matrix(tmp_path):
    paths, spec = make(tmp_path)
    unit = build_unit(spec)
    # just created, no status, unit not yet listed → starting
    assert entry_for_build(paths, set()).state == JobState.STARTING
    # unit active → running / cancel requested
    paths.write_status({"state": "running", "step": "cook"})
    assert entry_for_build(paths, {unit}).state == JobState.RUNNING
    paths.cancel_marker.write_text("x")
    assert entry_for_build(paths, {unit}).state == JobState.CANCEL_REQUESTED
    paths.cancel_marker.unlink()
    # runner wrote a result but finalize has not run yet while the unit is still active → still running
    paths.write_result({"state": "succeeded"})
    assert entry_for_build(paths, {unit}).state == JobState.RUNNING
    # unit gone → final state from result
    assert entry_for_build(paths, set()).state == JobState.SUCCEEDED
    # finalized result wins even if the unit is (briefly) still listed
    paths.write_result({"state": "oom-killed", "finalized_at": time.time()})
    assert entry_for_build(paths, {unit}).state == JobState.OOM_KILLED


def test_lost_when_unit_vanished_without_result(tmp_path):
    paths, spec = make(tmp_path, created_at=time.time() - 3600)
    paths.write_status({"state": "running", "step": "cook"})
    assert entry_for_build(paths, set()).state == JobState.LOST


def test_scan_includes_queue_first(tmp_path):
    make(tmp_path, "A")
    q = BuildQueue()
    q.add(JobSpec(job_id="20261015-000000-B-Development", project="B", project_dir="/b", uproject="B.uproject"))
    entries = scan_jobs(tmp_path / "builds", q, set())
    assert entries[0].state == JobState.QUEUED and entries[0].spec.project == "B"
    assert entries[1].spec.project == "A"
