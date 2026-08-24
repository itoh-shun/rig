"""`rig-wb usage` states what it is not counting (T6).

The command answers "how much has rig been used", and a reader takes a missing entry
as absence rather than as a blind spot. This session made exactly that inference: the
global count said 2%, and the conclusion drawn was that rig was not used on real work
— in a repository that held eight workbench tasks and forty-five learned instincts.

Two gaps are real. One is countable: a workbench task is recorded when it reaches
accept or discard, so tasks still in flight have no record and cannot have one yet.
The other cannot be counted from here: SKILL.md §6 assigns the manual/workflow append
to the model in prose, so a flow that never went through `/rig:go` may be missing with
no trace to count. The first gets a number; the second gets named.
"""

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_usage(cwd, *args):
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", "usage", *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60,
                          env={**_env(), "PYTHONPATH": str(REPO_ROOT)})


def _env():
    import os
    return dict(os.environ)


def write_task(root: pathlib.Path, task_id: str, status: str) -> None:
    """A run directory holding a record every reader can use.

    The full record, not `{task_id, status}`: the counter reads runs through
    `read_all_tasks` (#493), and `REQUIRED_FIELDS` is the one rule for what a usable record
    is. A fixture writing less than a shipped run does would have made this file measure the
    unreadable path everywhere it means to measure the readable one.
    """
    d = root / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps(
        {"task_id": task_id, "status": status, "task_type": "bugfix",
         "created_at": "2026-08-24T10:00:00+09:00", "input": f"do {task_id}"}),
        encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".rig").mkdir()
    return tmp_path


def test_unfinished_tasks_are_counted_and_named(repo):
    write_task(repo, "t1", "running")
    write_task(repo, "t2", "gate_passed")
    write_task(repo, "t3", "gate_failed")
    write_task(repo, "t4", "accepted")
    write_task(repo, "t5", "discarded")

    out = run_usage(repo).stdout

    assert "Not in this count:" in out
    assert "3 workbench task(s)" in out  # accepted and discarded are recorded, the rest are not


def test_the_prose_assigned_append_is_named_even_with_nothing_to_count(repo):
    """No unfinished tasks does not mean the count is complete — the gap that cannot be
    measured is still there, so the line stays."""
    write_task(repo, "t1", "accepted")

    out = run_usage(repo).stdout

    assert "3 workbench task(s)" not in out
    assert "SKILL.md §6" in out


def test_an_empty_log_still_says_what_is_missing(repo):
    """'No records found' is the reading most likely to be mistaken for 'rig was not
    used' — which is the inference this note exists to block."""
    write_task(repo, "t1", "running")

    out = run_usage(repo).stdout

    assert "No records found" in out
    assert "1 workbench task(s)" in out


def test_global_scope_does_not_claim_a_count_it_cannot_take(repo):
    """`--global` spans repositories whose `.rig/runs/` this process cannot read, so it
    reports the gap it can name and stays silent about the one it cannot measure."""
    write_task(repo, "t1", "running")

    out = run_usage(repo, "--global").stdout

    assert "workbench task(s)" not in out
    assert "SKILL.md §6" in out


def test_json_output_carries_the_same_coverage_facts(repo):
    write_task(repo, "t1", "running")
    write_task(repo, "t2", "accepted")
    (repo / ".rig" / "runs.jsonl").write_text(
        json.dumps({"ts": "2026-08-14T00:00:00+09:00", "invoker": "workbench"}) + "\n",
        encoding="utf-8")

    payload = json.loads(run_usage(repo, "--json").stdout)

    assert payload["unfinished_workbench_tasks"] == 1
    assert any("SKILL.md §6" in line for line in payload["not_counted"])


def test_the_json_names_the_records_the_count_could_not_read(repo):
    """The machine consumer's half of the same fact (#493): it cannot see the printed note,
    and `unfinished_workbench_tasks` alone would read as a complete count."""
    write_task(repo, "t1", "running")
    (repo / ".rig" / "runs" / "broken").mkdir(parents=True)
    (repo / ".rig" / "runs" / "broken" / "task.json").write_text("{not json", encoding="utf-8")

    payload = json.loads(run_usage(repo, "--json").stdout)

    assert payload["unfinished_workbench_tasks"] == 1
    assert payload["unreadable_workbench_task_records"] == ["broken"]
    assert payload["workbench_task_collection_error"] is None


def test_the_json_of_a_fully_readable_repository_claims_nothing_unread(repo):
    write_task(repo, "t1", "running")

    payload = json.loads(run_usage(repo, "--json").stdout)

    assert payload["unreadable_workbench_task_records"] == []
    assert not any("could not be read" in line for line in payload["not_counted"])


def test_an_unreadable_task_file_does_not_break_the_count(repo):
    """It does not break the count, and it is not silently absent from it either (#493).

    The record that could not be read may have been an unfinished task, so the count above
    it is a count of what could be read — and this section exists to say what the number
    does not contain.
    """
    write_task(repo, "t1", "running")
    (repo / ".rig" / "runs" / "broken").mkdir(parents=True)
    (repo / ".rig" / "runs" / "broken" / "task.json").write_text("{not json", encoding="utf-8")

    r = run_usage(repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 workbench task(s)" in r.stdout
    assert "1 of 2 records could not be read: broken" in r.stdout
