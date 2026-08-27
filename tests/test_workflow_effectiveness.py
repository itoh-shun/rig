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
