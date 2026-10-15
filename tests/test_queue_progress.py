from unrealbuilder.core.jobs import JobSpec
from unrealbuilder.core.logparse.uat import UatProgress
from unrealbuilder.core.progress import cook_fraction, overall
from unrealbuilder.core.queue import BuildQueue, next_to_start


def spec(project, cfg="Development", n=0):
    return JobSpec(job_id=f"2026101{n}-{project}-{cfg}", project=project, project_dir="/p", uproject=f"{project}.uproject", configuration=cfg)


def test_queue_persistence_and_moves():
    q = BuildQueue()
    q.add(spec("A", n=1))
    q.add(spec("B", n=2))
    q.add(spec("C", n=3))
    assert q.move("20261013-C-Development", -2)
    assert [s.project for s in q.items] == ["C", "A", "B"]
    assert not q.move("20261013-C-Development", -1)
    q.paused = True
    q.save()
    loaded = BuildQueue.load()
    assert loaded.paused and [s.project for s in loaded.items] == ["C", "A", "B"]
    assert loaded.remove("20261011-A-Development").project == "A"
    assert loaded.remove("missing") is None


def test_next_to_start_rules():
    q = BuildQueue(items=[spec("A", n=1), spec("A", "Shipping", n=2), spec("B", n=3)])
    assert next_to_start(q, [], 1).job_id == "20261011-A-Development"
    assert next_to_start(q, ["X"], 1) is None  # concurrency reached
    assert next_to_start(q, ["A"], 2).project == "B"  # one job per project
    q.paused = True
    assert next_to_start(q, [], 2) is None


def test_cook_fraction_is_monotonic_over_phases():
    p = UatProgress()
    values = []
    p.current_phase = "COOK"
    values.append(cook_fraction(p))
    p.global_shaders, p.shaders_left, p.shaders_left_max = True, 50, 100
    values.append(cook_fraction(p))
    p.global_shaders, p.cook_done, p.cook_total = False, 290, 580
    values.append(cook_fraction(p))
    p.current_phase, p.phases_completed = "STAGE", ["COOK"]
    values.append(cook_fraction(p))
    p.current_phase = "ARCHIVE"
    values.append(cook_fraction(p))
    p.phases_completed.append("ARCHIVE")
    values.append(cook_fraction(p))
    assert values == sorted(values) and values[-1] == 1.0


def test_overall_skips_skipped_steps():
    steps = {s: {"state": "done"} for s in ("preflight", "sync")}
    steps.update({"clean": {"state": "skipped"}, "build": {"state": "skipped"}, "cook": {"state": "running"}})
    p = UatProgress(current_phase="COOK", cook_done=580, cook_total=580)
    o = overall(steps, "cook", p)
    assert 0.6 < o.fraction < 0.9
    assert o.step_label == "Cook / stage / pak"
    assert "Packages 580/580" in o.detail


def test_estimate_remaining_with_reference():
    from unrealbuilder.core.progress import estimate_remaining, step_durations

    ref = step_durations(
        {
            "sync": {"started": 0, "ended": 1},
            "build": {"started": 1, "ended": 41},
            "cook": {"started": 41, "ended": 141},
            "zip": {"started": 141, "ended": 151},
        }
    )
    assert ref == {"sync": 1, "build": 40, "cook": 100, "zip": 10}
    steps = {"sync": {"state": "done"}, "build": {"state": "done"}, "cook": {"state": "running", "started": 1000}, "zip": {"state": "pending"}}
    p = UatProgress(current_phase="COOK", cook_done=290, cook_total=580)
    left = estimate_remaining(steps, "cook", p, elapsed=80, fraction=0.5, reference=ref, now=1040)
    # cook: 100 s reference, 40 s spent, ~57 % done → ~60 s left (max of both estimates) + zip 10 s
    assert 60 <= left <= 75


def test_estimate_remaining_without_reference():
    from unrealbuilder.core.progress import estimate_remaining

    p = UatProgress()
    assert estimate_remaining({}, "cook", p, elapsed=100, fraction=0.25, reference=None, now=0) == 300
    assert estimate_remaining({}, "cook", p, elapsed=10, fraction=0.05, reference=None, now=0) is None
