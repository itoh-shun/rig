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
    d = root / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps({"task_id": task_id, "status": status}),
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


def test_an_unreadable_task_file_does_not_break_the_count(repo):
    write_task(repo, "t1", "running")
    (repo / ".rig" / "runs" / "broken").mkdir(parents=True)
    (repo / ".rig" / "runs" / "broken" / "task.json").write_text("{not json", encoding="utf-8")

    r = run_usage(repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 workbench task(s)" in r.stdout
