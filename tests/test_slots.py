from unrealbuilder.core.settings import Settings
from unrealbuilder.core.slots import free_slot, slots, workers_for


def settings(tmp_path, concurrency):
    s = Settings()
    s.paths.toolchain_root = str(tmp_path / "t")
    s.wine.prefix = str(tmp_path / "t" / "wineprefix")
    s.resources.max_concurrent_builds = concurrency
    return s


def test_slots_layout(tmp_path):
    s = settings(tmp_path, 2)
    a, b = slots(s)
    assert a.prefix.name == "wineprefix" and a.zen_port == 8559
    assert b.prefix.name == "wineprefix-slot1" and b.zen_port == 8560


def test_free_slot_respects_concurrency_and_existence(tmp_path):
    s = settings(tmp_path, 1)
    main = slots(s)[0]
    assert free_slot(s, set()) == main
    assert free_slot(s, {str(main.prefix)}) is None
    s.resources.max_concurrent_builds = 2
    assert free_slot(s, {str(main.prefix)}) is None  # slot 1 not created yet
    second = slots(s)[1]
    second.prefix.mkdir(parents=True)
    (second.prefix / "system.reg").write_text("")
    assert free_slot(s, {str(main.prefix)}) == second


def test_workers_are_shared(tmp_path):
    s = settings(tmp_path, 1)
    assert workers_for(s, 8) == 8
    s.resources.max_concurrent_builds = 2
    assert workers_for(s, 8) == 4
    assert workers_for(s, 1) == 1
