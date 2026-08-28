"""Recorded workflow effectiveness, without turning missing telemetry into zero (#433).

The first tests are negative controls: the detector has no authority to invent what
"recurring", "late", or "excessive" means.  The positive controls then damage a real
signal in the fixture and require the public report to change.
"""

import json

import pytest

from rig_workbench.workbench.workflow_effectiveness import (
    SCHEMA,
    cmd_workflow_effectiveness,
    analyse,
    validate_query,
)


def query(**changes):
    payload = {
        "schema": "rig.workflow-effectiveness-query/v1",
        "patterns": [
            {"kind": "late-stage-failure", "minimum_occurrences": 2,
             "late_steps": ["review"]},
            {"kind": "excessive-repair-loops", "minimum_occurrences": 2,
             "repair_cycles_above": 1},
            {"kind": "task-gate-failure", "minimum_occurrences": 2,
             "gate_statuses": ["failed"]},
        ],
    }
    payload.update(changes)
    return payload


@pytest.mark.parametrize("payload,says", [
    ({}, "schema"),
    ({"schema": "rig.workflow-effectiveness-query/v1"}, "patterns"),
    (query(patterns=[]), "at least one"),
    (query(patterns="late"), "expected a list"),
    (query(extra=True), "unknown key"),
    (query(patterns=[{"kind": "late-stage-failure", "minimum_occurrences": 2}]),
     "late_steps"),
    (query(patterns=[{"kind": "late-stage-failure", "minimum_occurrences": 2,
                      "late_steps": []}]), "late_steps"),
    (query(patterns=[{"kind": "late-stage-failure", "minimum_occurrences": 2,
                      "late_steps": "review"}]), "late_steps"),
    (query(patterns=[{"kind": "excessive-repair-loops", "minimum_occurrences": 2}]),
     "repair_cycles_above"),
    (query(patterns=[{"kind": "unknown", "minimum_occurrences": 2}]), "not supported"),
    (query(patterns=[{"kind": "task-gate-failure", "minimum_occurrences": 2}]),
     "gate_statuses"),
])
def test_missing_empty_wrong_typed_and_unresolvable_constraints_are_refused(payload, says):
    assert any(says in problem for problem in validate_query(payload))


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


@pytest.fixture
def recorded_repo(tmp_path):
    rig = tmp_path / ".rig"
    rows = [
        {"ts": "2026-01-01T00:00:10+00:00", "recipe": "bugfix", "backend": "orchestrate",
         "final": "ESCALATE", "steps_total": 2, "steps_passed": 1, "retries": 2,
         "escalated_at": "review", "token_usage": {}, "steps": []},
        {"ts": "2026-01-02T00:00:10+00:00", "recipe": "bugfix", "backend": "orchestrate",
         "final": "ESCALATE", "steps_total": 2, "steps_passed": 1, "retries": 3,
         "escalated_at": "review", "token_usage": {}, "steps": []},
        {"ts": "2026-01-03T00:00:10+00:00", "recipe": "feature", "backend": "orchestrate",
         "final": "DONE", "steps_total": 2, "steps_passed": 2, "retries": 0,
         "escalated_at": None, "token_usage": {"mock": {"prompt_tokens": 7,
                                                           "completion_tokens": 3,
                                                           "calls": 1}}, "steps": []},
    ]
    rig.mkdir()
    (rig / "runs.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows),
                                    encoding="utf-8")
    for task_id, task_type, gate_status, checked_at in [
        ("one", "bugfix", "failed", "2026-01-01T00:10:00+00:00"),
        ("two", "bugfix", "failed", "2026-01-02T00:20:00+00:00"),
        ("three", "feature", "passed", "2026-01-03T00:30:00+00:00"),
    ]:
        run = rig / "runs" / task_id
        _write_json(run / "task.json", {"task_id": task_id, "task_type": task_type,
                    "created_at": checked_at.replace("00:10", "00:00").replace("00:20", "00:00")
                                            .replace("00:30", "00:00")})
        _write_json(run / "acceptance.json", {"task_id": task_id, "status": gate_status,
                    "checked_at": checked_at,
                    "checks": [{"name": "tests", "status": gate_status}]})
    return tmp_path


def test_report_separates_measured_zero_from_unobservable(recorded_repo):
    report = analyse(recorded_repo, query())
    assert report["schema"] == SCHEMA
    assert report["records"] == {"orchestrate_runs": 3, "workbench_tasks": 3,
                                  "unreadable": []}
    assert report["metrics"]["repair_cycle_count"]["status"] == "observed"
    assert report["metrics"]["repair_cycle_count"]["total"] == 5
    assert report["metrics"]["time_to_assurance_seconds"]["by_task_type"]["feature"]["total"] == 1800
    assert report["metrics"]["token_usage"]["measured_runs"] == 1
    assert report["metrics"]["token_usage"]["unmeasured_runs"] == 2
    assert report["metrics"]["cost"]["status"] == "unobservable"
    assert report["metrics"]["reviewer_finding_yield"]["status"] == "unobservable"
    assert report["metrics"]["production_rework"]["status"] == "unobservable"


def test_patterns_only_state_what_the_supplied_thresholds_support(recorded_repo):
    report = analyse(recorded_repo, query())
    assert report["patterns"] == [
        {"kind": "late-stage-failure", "group": {"recipe": "bugfix"},
         "occurrences": 2, "sample_size": 2, "late_steps": ["review"]},
        {"kind": "excessive-repair-loops", "group": {"recipe": "bugfix"},
         "occurrences": 2, "sample_size": 2, "repair_cycles_above": 1},
        {"kind": "task-gate-failure", "group": {"task_type": "bugfix"},
         "check": "tests", "occurrences": 2, "sample_size": 2,
         "gate_statuses": ["failed"]},
    ]
    assert report["unobservable_patterns"] == [
        "redundant-repeated-steps", "low-yield-verifier", "high-yield-verifier",
        "missing-early-analysis-step", "risk-class-patterns"]


def test_positive_control_removing_a_late_failure_removes_the_pattern(recorded_repo):
    path = recorded_repo / ".rig" / "runs.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["escalated_at"] = "implement"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    kinds = [item["kind"] for item in analyse(recorded_repo, query())["patterns"]]
    assert "late-stage-failure" not in kinds
    assert "excessive-repair-loops" in kinds


def test_unreadable_records_are_named_not_dropped(recorded_repo):
    with (recorded_repo / ".rig" / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    report = analyse(recorded_repo, query())
    assert report["records"]["orchestrate_runs"] == 3
    assert report["records"]["unreadable"] == [".rig/runs.jsonl:4"]


def test_no_records_is_unobservable_not_zero(tmp_path):
    report = analyse(tmp_path, query())
    assert report["metrics"]["repair_cycle_count"]["status"] == "unobservable"
    assert report["metrics"]["repair_cycle_count"]["value"] is None
    assert report["patterns"] == []


def test_human_output_renders_task_type_patterns(recorded_repo, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from rig_workbench.workbench import state

    query_path = tmp_path / "query.json"
    _write_json(query_path, query())
    monkeypatch.setattr(state, "repo_root", lambda: recorded_repo)
    with pytest.raises(SystemExit) as stopped:
        cmd_workflow_effectiveness(SimpleNamespace(query=str(query_path), json=False))
    assert stopped.value.code == 0
    assert "pattern task-gate-failure [task_type=bugfix]: 2 of 2" in capsys.readouterr().out


def test_the_command_itself_refuses_what_validate_query_rejects(recorded_repo, tmp_path,
                                                                monkeypatch, capsys):
    """A validator nothing calls is not a constraint.

    Every refusal above is measured against `validate_query`. That says the rules
    exist, not that the entry point applies them — a command that built its report
    without consulting the validator would pass all of them. This drives the
    command, and requires both that it stops and that it names every problem it
    found rather than only the first.
    """
    from types import SimpleNamespace

    from rig_workbench.workbench import state

    query_path = tmp_path / "query.json"
    _write_json(query_path, {**query(patterns=[{"kind": "unknown",
                                                "minimum_occurrences": 2}]),
                             "bogus": 1})
    monkeypatch.setattr(state, "repo_root", lambda: recorded_repo)
    with pytest.raises(SystemExit) as stopped:
        cmd_workflow_effectiveness(SimpleNamespace(query=str(query_path), json=True))
    assert stopped.value.code != 0
    captured = capsys.readouterr()
    assert "bogus" in captured.err and "unknown" in captured.err
    # And nothing that looks like a report: refusing must not also answer.
    assert "unobservable_patterns" not in captured.out


def test_a_query_the_validator_accepts_still_reaches_a_report(recorded_repo, tmp_path,
                                                              monkeypatch, capsys):
    """The control for the test above: refusing everything would pass it too."""
    from types import SimpleNamespace

    from rig_workbench.workbench import state

    query_path = tmp_path / "query.json"
    _write_json(query_path, query())
    monkeypatch.setattr(state, "repo_root", lambda: recorded_repo)
    with pytest.raises(SystemExit) as stopped:
        cmd_workflow_effectiveness(SimpleNamespace(query=str(query_path), json=True))
    assert stopped.value.code == 0
    assert "unobservable_patterns" in capsys.readouterr().out


# ── runtime, from the perf block #502 records (#433 §1) ──────────────────────
def _with_perf(*perfs):
    """Run records carrying the perf blocks given, plus one that carries none."""
    runs = [{"recipe": "bugfix", "final": "DONE", "retries": 0, **({"perf": p} if p else {})}
            for p in perfs]
    return runs


def test_runtime_is_measured_from_the_perf_block(tmp_path):
    """It used to say 'runs.jsonl records a finish timestamp but no start timestamp'. #502 put
    a measured total in the same records this module reads, and a metric that keeps claiming it
    cannot be measured while the measurement sits in the file it is reading is worse than one
    that was never offered."""
    from rig_workbench.workbench import workflow_effectiveness as we

    report = we._runtime(_with_perf({"total_ms": 100.0, "rig_overhead_ms": 40.0},
                                    {"total_ms": 200.0, "rig_overhead_ms": 60.0}))
    assert report["status"] == "observed"
    assert report["total_ms"] == 300.0
    assert report["rig_overhead_ms"] == 100.0
    assert report["measured_runs"] == 2 and report["unmeasured_runs"] == 0


def test_a_run_without_a_perf_block_is_unmeasured_not_zero(tmp_path):
    from rig_workbench.workbench import workflow_effectiveness as we

    report = we._runtime(_with_perf({"total_ms": 100.0, "rig_overhead_ms": 40.0}, None))
    assert report["measured_runs"] == 1 and report["unmeasured_runs"] == 1
    assert report["total_ms"] == 100.0


def test_overhead_that_perf_withheld_is_not_counted_as_measured():
    """perf withholds `rig_overhead_ms` whenever a provider call went untimed, because overhead
    is a subtraction and one missed call would silently become rig's time. Reading the absence
    as zero here would reintroduce downstream exactly the fabrication perf refuses."""
    from rig_workbench.workbench import workflow_effectiveness as we

    report = we._runtime(_with_perf(
        {"total_ms": 100.0, "rig_overhead_ms": 40.0},
        {"total_ms": 200.0, "rig_overhead_unmeasured": "1 provider call(s) were not timed"}))
    assert report["rig_overhead_ms"] == 40.0
    assert report["measured_overhead_runs"] == 1
    assert report["unmeasured_overhead_runs"] == 1
    assert report["total_ms"] == 300.0, "elapsed is still known for both runs"


def test_elapsed_known_but_no_split_says_so_rather_than_omitting_it():
    """A reader who saw only `total_ms` would reasonably assume the split was available and
    simply left out."""
    from rig_workbench.workbench import workflow_effectiveness as we

    report = we._runtime(_with_perf({"total_ms": 100.0}))
    assert report["status"] == "observed"
    assert "rig_overhead_ms" not in report
    assert report["rig_overhead"]["status"] == "unobservable"
    assert "untimed" in report["rig_overhead"]["reason"]


def test_no_perf_anywhere_is_unobservable_with_the_counts():
    from rig_workbench.workbench import workflow_effectiveness as we

    report = we._runtime(_with_perf(None, None))
    assert report["status"] == "unobservable" and report["value"] is None
    assert report["measured_runs"] == 0 and report["unmeasured_runs"] == 2


def test_no_runs_at_all_says_that_instead():
    from rig_workbench.workbench import workflow_effectiveness as we

    assert we._runtime([])["reason"] == "no orchestrate run records were found"


@pytest.mark.parametrize("bad", [
    {"total_ms": "100"}, {"total_ms": None}, {"total_ms": -5.0}, {"total_ms": True}, {},
])
def test_a_perf_block_with_an_unusable_total_is_not_read(bad):
    """A boolean is an int in Python, and `True` would otherwise contribute 1ms of pure
    fiction to the total."""
    from rig_workbench.workbench import workflow_effectiveness as we

    assert we._runtime(_with_perf(bad))["measured_runs"] == 0


def test_the_metric_reaches_the_report_a_caller_actually_reads(tmp_path):
    """Through `analyse`, not just the helper: a metric wired to nothing would pass every test
    above."""
    import json

    from rig_workbench.workbench import workflow_effectiveness as we

    rig = tmp_path / ".rig"
    rig.mkdir()
    (rig / "runs.jsonl").write_text(json.dumps(
        {"recipe": "bugfix", "final": "DONE", "retries": 0,
         "perf": {"total_ms": 150.0, "rig_overhead_ms": 50.0}}) + "\n", encoding="utf-8")
    # A real constraint, because the module refuses an empty pattern list on purpose: an empty
    # query gives the detector no caller-supplied boundary. The metric under test is not the
    # pattern, but the query still has to be one this module would accept.
    report = we.analyse(tmp_path, {"schema": we.QUERY_SCHEMA, "patterns": [
        {"kind": "excessive-repair-loops", "minimum_occurrences": 1, "repair_cycles_above": 2}]})
    runtime_metric = report["metrics"]["runtime"]
    assert runtime_metric["status"] == "observed"
    assert runtime_metric["total_ms"] == 150.0 and runtime_metric["rig_overhead_ms"] == 50.0
