"""A finished workbench task lands in `.rig/runs.jsonl` (T3).

`/rig:go` is the entry point a person types, and until now it appeared in the run log
not at all — the log had one producer, `orchestrate`. SKILL.md §6 said the other
backends append the same format, but said it in prose, to the model. These tests pin
the code that now does it, and pin the two fields that have no source on this side to
`null` rather than to a zero that would read as a measurement.
"""

import json
import pathlib

import pytest

from rig_workbench.workbench.telemetry import _steps_record, record_task_run


@pytest.fixture
def global_mirror(tmp_path, monkeypatch):
    """Redirect the cross-project mirror into this test's tmp dir.

    Patched as an attribute, not as an env var: `config.GLOBAL_RUNS_PATH` is resolved
    once at import time, so `setenv` here would land after the value is already fixed
    and the write would silently go to the suite-wide path from conftest.
    """
    from rig_workbench.orchestrate import config

    path = tmp_path / "global-runs.jsonl"
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", path)
    return path


@pytest.fixture
def task_repo(tmp_path, global_mirror):
    """A repo root holding one task's run directory."""
    d = tmp_path / ".rig" / "runs" / "rig-1"
    d.mkdir(parents=True)
    (d / "steps.json").write_text(json.dumps({"steps": [
        {"name": "reproduce", "status": "passed"},
        {"name": "fix", "status": "passed"},
        {"name": "verify", "status": "running"},
    ]}), encoding="utf-8")
    return tmp_path


def task(**over):
    return {"task_id": "rig-1", "recipe": "bugfix", "task_type": "bugfix", **over}


def read_runs(root: pathlib.Path) -> list[dict]:
    p = root / ".rig" / "runs.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_accepted_task_is_recorded_as_done(task_repo):
    record_task_run(task_repo, task(), "accepted")

    rows = read_runs(task_repo)
    assert len(rows) == 1
    assert rows[0]["final"] == "DONE"
    assert rows[0]["backend"] == "workbench"
    assert rows[0]["recipe"] == "bugfix"
    assert rows[0]["task_id"] == "rig-1"


def test_discard_keeps_its_own_name(task_repo):
    """A person throwing the work away is not a run that failed, and the log's existing
    vocabulary has no word for it — so it gets one instead of the nearest wrong word."""
    record_task_run(task_repo, task(), "discarded")

    assert read_runs(task_repo)[0]["final"] == "DISCARDED"


def test_gate_failure_maps_onto_blocked(task_repo):
    record_task_run(task_repo, task(), "gate_failed")

    assert read_runs(task_repo)[0]["final"] == "BLOCKED"


def test_step_counts_come_from_steps_json(task_repo):
    record_task_run(task_repo, task(), "accepted")

    row = read_runs(task_repo)[0]
    assert row["steps_total"] == 3
    assert row["steps_passed"] == 2  # the third is still running
    assert [s["id"] for s in row["steps"]] == ["reproduce", "fix", "verify"]


def test_unmappable_fields_are_null_not_zero(task_repo):
    """`retries: 0` would claim there were no retries; there is no retry counter on this
    side at all. `null` is the difference between "none happened" and "not measured"."""
    record_task_run(task_repo, task(), "accepted")

    row = read_runs(task_repo)[0]
    assert row["retries"] is None
    assert row["token_usage"] is None


def test_review_verdicts_are_translated_to_the_log_vocabulary(task_repo):
    (task_repo / ".rig" / "runs" / "rig-1" / "review.json").write_text(json.dumps({
        "task_id": "rig-1",
        "verdicts": [{"persona": "security-reviewer", "verdict": "APPROVE"},
                     {"persona": "design-reviewer", "verdict": "APPROVE_WITH_CONDITIONS"},
                     {"persona": "test-reviewer", "verdict": "REJECT"}],
    }), encoding="utf-8")

    record_task_run(task_repo, task(), "accepted")

    verdicts = read_runs(task_repo)[0]["steps"][-1]["verdicts"]
    assert verdicts == [
        {"by": "security-reviewer", "ok": True},
        {"by": "design-reviewer", "ok": True},   # matches reporting.verifier_counters:
        {"by": "test-reviewer", "ok": False},    # REJECT is the only rejection
    ]


def test_a_task_without_a_recipe_still_names_something(task_repo):
    record_task_run(task_repo, task(recipe=None), "accepted")

    assert read_runs(task_repo)[0]["recipe"] == "(no recipe, bugfix)"


def test_the_record_lands_in_the_task_s_repo_not_the_cwd(task_repo, tmp_path):
    """`config.RUNS_PATH` is resolved from the cwd at import time, which is right for
    orchestrate and wrong here — a workbench task carries the root it belongs to."""
    from rig_workbench.orchestrate import config

    record_task_run(task_repo, task(), "accepted")

    assert read_runs(task_repo)
    assert config.RUNS_PATH.resolve() != (task_repo / ".rig" / "runs.jsonl").resolve()


def test_the_global_mirror_carries_the_task_s_project(task_repo, global_mirror):
    record_task_run(task_repo, task(), "accepted")

    mirrored = [json.loads(ln) for ln
                in global_mirror.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert mirrored[0]["project"] == str(task_repo)


def test_steps_record_survives_a_missing_run_directory(tmp_path):
    """Recording must not be the thing that breaks a task that already ended."""
    assert _steps_record(tmp_path / "nonexistent") == []


def test_a_review_with_no_steps_still_records_its_verdicts(tmp_path, global_mirror):
    d = tmp_path / ".rig" / "runs" / "rig-1"
    d.mkdir(parents=True)
    (d / "review.json").write_text(json.dumps({
        "verdicts": [{"persona": "security-reviewer", "verdict": "REJECT"}]}), encoding="utf-8")

    record_task_run(tmp_path, task(), "accepted")

    steps = read_runs(tmp_path)[0]["steps"]
    assert [v["by"] for v in steps[-1]["verdicts"]] == ["security-reviewer"]


def test_invoker_keeps_the_rig_wb_wrapper_s_own_label(task_repo, monkeypatch):
    """`backend` is which engine ran it; `invoker` is what launched the process. Pinning
    the latter to "workbench" would drop every workbench run out of the "via rig-wb"
    share that `rig-wb usage` reports — the number this whole change exists to fix."""
    monkeypatch.setenv("RIG_INVOKER", "rig-wb/2.5.0")

    record_task_run(task_repo, task(), "accepted")

    assert read_runs(task_repo)[0]["invoker"] == "rig-wb/2.5.0"


def test_invoker_falls_back_to_the_backend_name(task_repo, monkeypatch):
    monkeypatch.delenv("RIG_INVOKER", raising=False)

    record_task_run(task_repo, task(), "accepted")

    assert read_runs(task_repo)[0]["invoker"] == "workbench"
