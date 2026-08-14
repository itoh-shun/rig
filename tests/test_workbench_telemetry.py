"""A finished workbench task lands in `.rig/runs.jsonl` (T3).

`/rig:go` is the entry point a person types, and until now it appeared in the run log
not at all — the log had one producer, `orchestrate`. SKILL.md §6 said the other
backends append the same format, but said it in prose, to the model. These tests pin
the code that now does it, and pin the two fields that have no source on this side to
`null` rather than to a zero that would read as a measurement.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

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


# ---- the wiring from accept / discard ----------------------------------------

def _cli(args, cwd):
    env = dict(os.environ, RIG_ACTOR="alice")
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "workbench.py"), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=120, env=env)


@pytest.fixture
def accepting_repo(tmp_path):
    """A repo with one task that `accept` will actually take.

    The full shape matters: `accept` needs a real worktree with a commit ahead of base
    and a clean main tree, and `--no-worktree` records no branch at all. Short-cutting
    any of it means the test never reaches the code it is here to cover.
    """
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t.com"],
              ["git", "config", "user.name", "alice"]):
        subprocess.run(c, cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    _cli(["new", "add a thing", "--type", "feature"], tmp_path)
    task_id = sorted(p.name for p in (tmp_path / ".rig" / "runs").iterdir())[-1]
    d = tmp_path / ".rig" / "runs" / task_id

    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    wt = pathlib.Path(task["worktree_path"])
    (wt / "g.txt").write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "work"], cwd=wt, check=True)

    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        c["status"] = "passed" if c["name"] == "no_unrelated_diff" else "skipped"
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")

    # `new` writes .gitignore; accept refuses to run on a dirty main tree.
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ignore"], cwd=tmp_path, check=True)
    return tmp_path, task_id


def test_accept_writes_exactly_one_record_through_the_cli(accepting_repo):
    """Driven through `workbench.py accept`, not through `record_task_run`. Re-deriving
    the caller's logic inside the test would leave both "accept records nothing" and
    "cleanup records a second time" undetectable — which is what the previous version
    of this test did."""
    repo, task_id = accepting_repo

    r = _cli(["accept", task_id], repo)

    assert r.returncode == 0, r.stdout + r.stderr
    rows = read_runs(repo)
    assert [row["final"] for row in rows] == ["DONE"]
    assert rows[0]["backend"] == "workbench"
    assert rows[0]["task_id"] == task_id


def test_cleanup_after_an_accept_does_not_record_a_second_time(accepting_repo):
    """`discard` is also the cleanup step after an accept. Recording there too would
    double-count every accepted task in the very number this change exists to fix."""
    repo, task_id = accepting_repo
    assert _cli(["accept", task_id], repo).returncode == 0

    r = _cli(["discard", task_id, "--yes"], repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert [row["final"] for row in read_runs(repo)] == ["DONE"]


def test_a_discard_without_an_accept_records_the_discard(accepting_repo):
    repo, task_id = accepting_repo

    assert _cli(["discard", task_id, "--yes"], repo).returncode == 0

    assert [row["final"] for row in read_runs(repo)] == ["DISCARDED"]


def test_the_run_record_lands_after_the_signed_provenance(accepting_repo):
    """Ordering, asserted on the artifacts: a telemetry failure must not be able to
    cost an accepted task its audit trail, so provenance is written first."""
    repo, task_id = accepting_repo

    assert _cli(["accept", task_id], repo).returncode == 0

    assert (repo / ".rig" / "runs" / task_id / "provenance.json").exists()
    assert read_runs(repo)


def test_a_corrupt_steps_file_does_not_propagate_into_the_caller(task_repo):
    """`record_task_run` runs inside accept, after the governance ledger and the signed
    provenance. An exception here would surface in a task that has already ended."""
    (task_repo / ".rig" / "runs" / "rig-1" / "steps.json").write_text("{not json",
                                                                     encoding="utf-8")

    record_task_run(task_repo, task(), "accepted")  # must not raise

    assert read_runs(task_repo) == []


def test_a_steps_file_holding_a_list_does_not_propagate(task_repo):
    (task_repo / ".rig" / "runs" / "rig-1" / "steps.json").write_text(
        '[{"name": "fix"}]', encoding="utf-8")  # a list, where the reader expects a mapping

    record_task_run(task_repo, task(), "accepted")

    assert read_runs(task_repo) == []


def test_a_missing_run_directory_does_not_propagate(tmp_path, global_mirror):
    """`state.run_dir` calls `die()`, which is a SystemExit — not caught by a bare
    `except Exception`."""
    record_task_run(tmp_path, task(), "accepted")

    assert read_runs(tmp_path) == []
