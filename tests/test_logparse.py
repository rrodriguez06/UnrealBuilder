import random

import pytest

from unrealbuilder.core.logparse.issues import IssueCollector, normalize
from unrealbuilder.core.logparse.lines import LineSplitter, Severity, classify, make_line
from unrealbuilder.core.logparse.uat import UatParser


def parse_bytes(data: bytes, chunks: list[int] | None = None):
    splitter = LineSplitter()
    parser = UatParser()
    issues = IssueCollector()
    lines = []
    if chunks is None:
        raws = splitter.feed(data)
    else:
        raws = []
        pos = 0
        for size in chunks:
            raws += splitter.feed(data[pos : pos + size])
            pos += size
        raws += splitter.feed(data[pos:])
    raws += splitter.flush()
    for i, raw in enumerate(raws, 1):
        lines.append(make_line(i, raw))
    parser.feed(lines)
    issues.add("test", lines)
    return parser.progress, issues, lines


def test_uat_build_log(log_fixture):
    p, issues, _ = parse_bytes(log_fixture("uat_build.log"))
    assert p.phases_started == ["BUILD"]
    assert p.phases_completed == ["BUILD"]
    assert (p.ubt_done, p.ubt_total) == (11, 11)
    assert p.toolchain == "14.44.35229"
    assert p.windows_sdk == "10.0.22621.0"
    assert p.code_based_reason == "GameplayStateTree plugin is enabled"
    assert p.ubt_result == "Succeeded"
    assert p.result == "SUCCESSFUL"
    assert p.exit_code == 0
    assert issues.error_count == 0


def test_uat_cook_log(log_fixture):
    p, issues, _ = parse_bytes(log_fixture("uat_cook.log"))
    assert p.phases_started == ["COOK", "STAGE", "ARCHIVE"]
    assert p.phases_completed == ["COOK", "STAGE", "ARCHIVE"]
    assert p.cook_total == 581
    assert p.cook_done >= 565
    assert p.shader_workers == 8
    assert p.global_shaders is False
    assert p.commandlet_log and p.commandlet_log.endswith(".txt")
    assert any(tool == "UnrealPak.exe" and rc == 0 for tool, _s, rc in p.tool_runs)
    assert any(tool == "UnrealEditor-Cmd.exe" for tool, _s, _rc in p.tool_runs)
    assert p.exit_code == 0
    assert p.result == "SUCCESSFUL"
    assert p.buildcookrun_seconds and p.buildcookrun_seconds > 100
    assert p.crash is None
    assert issues.error_count == 0


def test_cook_commandlet_log_has_bom_and_no_errors(log_fixture):
    data = log_fixture("cook_commandlet.log")
    assert data.startswith(b"\xef\xbb\xbf")
    p, issues, lines = parse_bytes(data)
    assert lines[0].text.startswith("Log file open")
    assert p.shader_workers == 8
    assert p.shaders_compiled > 10000
    assert issues.error_count == 0


def test_smoke_log_only_has_benign_noise(log_fixture):
    _p, issues, lines = parse_bytes(log_fixture("smoke.log"))
    assert issues.error_count == 0
    noisy = [ln for ln in lines if "aqProf.dll" in ln.text]
    assert noisy and all(ln.severity == Severity.NOISE for ln in noisy)
    assert any("LoadMap: /Game/FirstPerson/Lvl_FirstPerson" in ln.text for ln in lines)


@pytest.mark.parametrize("seed", range(5))
def test_random_chunking_is_equivalent(log_fixture, seed):
    data = log_fixture("uat_cook.log")
    whole, whole_issues, whole_lines = parse_bytes(data)
    rng = random.Random(seed)
    chunks = []
    total = 0
    while total < len(data):
        size = rng.randint(1, 4096)
        chunks.append(size)
        total += size
    split, split_issues, split_lines = parse_bytes(data, chunks)
    assert [ln.text for ln in split_lines] == [ln.text for ln in whole_lines]
    assert split == whole
    assert split_issues.error_count == whole_issues.error_count


def test_splitter_keeps_partial_lines_and_strips_cr():
    s = LineSplitter()
    assert s.feed(b"abc\r\nde") == ["abc"]
    assert s.feed(b"f\r") == []
    assert s.feed(b"\nlast") == ["def"]
    assert s.flush() == ["last"]


@pytest.mark.parametrize(
    "text,severity,category",
    [
        ("LogCook: Error: Failed to cook /Game/Foo", Severity.ERROR, "LogCook"),
        ("LogShaderCompilers: Warning: something odd", Severity.WARNING, "LogShaderCompilers"),
        ("LogInit: Display: Game Engine Initialized.", Severity.INFO, "LogInit"),
        ("LogShaderCompilers: Verbose: Worker (1/8): shaders left to compile 12", Severity.VERBOSE, "LogShaderCompilers"),
        (r"U:\p\Source\A.cpp(12): error C2065: 'x': undeclared identifier", Severity.ERROR, "MSVC"),
        (r"U:\p\Source\A.cpp(3,9): warning C4996: deprecated", Severity.WARNING, "MSVC"),
        ("A.obj : error LNK2019: unresolved external symbol", Severity.ERROR, "Linker"),
        ("ERROR: Cook failed.", Severity.ERROR, "UAT"),
        ("BUILD FAILED", Severity.ERROR, "UAT"),
        ("System.IO.FileNotFoundException: Could not find file 'x'", Severity.ERROR, "UAT"),
        ("MESA-EGL: warning: egl: failed to create dri2 screen", Severity.NOISE, ""),
        ("LogWindows: Failed to load 'VtuneApi.dll' (GetLastError=126)", Severity.NOISE, ""),
    ],
)
def test_classify(text, severity, category):
    assert classify(text) == (severity, category)


def test_timestamp_is_stripped():
    line = make_line(1, "[2026.10.15-10.09.02:885][  0]LogWorld: Bringing World up")
    assert line.text == "LogWorld: Bringing World up"
    assert line.category == "LogWorld"


def test_shader_progress_and_slow_jobs():
    parser = UatParser()
    lines = [
        make_line(1, "LogCook: Display: Compiling global changed shaders for platform 'Windows'"),
        make_line(2, "LogShaderCompilers: Verbose: Worker (1/2): shaders left to compile 100"),
        make_line(3, "LogShaderCompilers: Verbose: Worker (2/2): shaders left to compile 60"),
        make_line(4, "LogShaderCompilers: Verbose: Worker (1/2): shaders left to compile 40"),
        make_line(5, "LogShaderCompilers: Display: Job FDeferredLightPS(permutation 12, format PCD3D_SM5) compile time exceeded threshold (126.5s)"),
    ]
    parser.feed(lines)
    p = parser.progress
    assert p.global_shaders
    assert p.shaders_left == 60
    assert p.shaders_left_max == 100
    assert p.shader_fraction == pytest.approx(0.4)
    assert p.slow_shaders[0].job == "FDeferredLightPS" and p.slow_shaders[0].seconds == 126.5


def test_issue_grouping_normalizes_assets_and_numbers():
    assert normalize("LogCook: Warning: Missing /Game/A/B.B in 12 packages") == normalize(
        "LogCook: Warning: Missing /Game/C/D.D in 3 packages"
    )
    coll = IssueCollector()
    coll.add(
        "cook",
        [
            make_line(1, "LogCook: Warning: Missing /Game/A/B.B in 12 packages"),
            make_line(2, "LogCook: Warning: Missing /Game/C/D.D in 3 packages"),
            make_line(3, "LogCook: Error: Boom"),
            make_line(4, "LogCook: Display: fine"),
        ],
    )
    groups = coll.sorted_groups()
    assert groups[0].severity == Severity.ERROR
    assert groups[1].count == 2
    assert groups[1].occurrences == [("cook", 1), ("cook", 2)]
    assert coll.warning_count == 2 and coll.error_count == 1


def test_ubt_rules_errors_are_errors_and_preferred_as_failure_message(tmp_path):
    from unrealbuilder.core.summary import first_error

    log = tmp_path / "uat-build.log"
    log.write_text(
        "Unable to instantiate module 'SwarmInterface': Could not find NetFxSDK install dir; this will prevent SwarmInterface from installing.\n"
        "Result: Failed (RulesError)\n"
        "AutomationTool exiting with ExitCode=8 (8)\n"
        "BUILD FAILED\n"
    )
    assert classify("Unable to instantiate module 'SwarmInterface': x")[0] == Severity.ERROR
    assert first_error(log).startswith("Unable to instantiate module 'SwarmInterface'")
    log.write_text("Result: Failed (RulesError)\nBUILD FAILED\n")
    assert first_error(log) == "Result: Failed (RulesError)"


def test_specific_errors_sort_before_generic_failures():
    coll = IssueCollector()
    coll.add("b", [make_line(1, "BUILD FAILED"), make_line(2, "BUILD FAILED"), make_line(3, "Unable to instantiate module 'X': y")])
    assert coll.sorted_groups()[0].sample.startswith("Unable to instantiate module")
