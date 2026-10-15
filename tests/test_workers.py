from unrealbuilder.core.workers import WorkerInfo, classify


def w(pid, cpu, rate, age):
    return WorkerInfo(pid, str(pid), 0, cpu, rate, age, "?", None)


def test_stall_detection_matches_poc_diagnosis():
    workers = [
        w(1, 99.5, 0.0, 3300),  # same input for 55 min, no faults: the hung worker from the POC
        w(2, 99.4, 4000.0, 40),
        w(3, 99.5, 2500.0, 30),
        w(4, 1.0, 0.0, 5),
    ]
    classify(workers)
    assert [x.state for x in workers] == ["stalled", "active", "active", "idle"]


def test_long_running_but_faulting_is_not_stalled():
    workers = [w(1, 99.0, 900.0, 700), w(2, 99.0, 800.0, 10)]
    classify(workers)
    assert workers[0].state == "long"


def test_no_stall_when_nobody_faults():
    # A single busy worker with no fault activity is not flagged: nothing to compare with.
    workers = [w(1, 99.0, 0.0, 400)]
    classify(workers)
    assert workers[0].state == "active"
