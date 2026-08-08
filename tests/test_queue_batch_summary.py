"""What a finished `queue go` batch leaves behind, and how deep the backlog is.

`queue go` reported `3/4 done` and a `[DONE]`/`[FAIL]` line per item. Both describe the
*queue's* bookkeeping: `DONE` means the gate settled and the verifier passed, which is
neither "merged" nor "nothing left to do" — every one of those tasks is sitting in its
own isolated worktree waiting for a person to accept or discard it. A batch that
reports `4/4 done` while leaving four undisclosed decisions on the desk is a green
build that never ran the tests.

Two properties carry the redesign:

- **the regrouping speaks `board`'s vocabulary** — the same `next_action` strings, so
  "whose turn is it" has one wording in this repository and not two that drift; and
- **linking is evidence-based and its absence is named** — a queue item is tied to a
  workbench task only by the id the provider printed, so an item with no recoverable id
  is listed as unlinked rather than folded into a bucket on a guess. In a screen whose
  entire job is "which of these needs me", a wrong attribution is worse than an
  admitted gap.

Backlog depth lives in `cockpit` rather than in a per-turn line: it is something you go
and look at, and the parent session's context is the budget `context-minimal` protects.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.batch import (find_task_id, group_batch, render_batch,
                                           task_action)
from rig_workbench.workbench.cockpit import queue_depth_lines

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _write_task(root, task_id, *, status, gate=None, steps=None, seeded=True):
    directory = root / ".rig" / "runs" / task_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "task.json").write_text(json.dumps({
        "task_id": task_id, "input": "do the thing", "task_type": "feature",
        "status": status, "created_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    checks = [{"name": "c", "status": gate}] if gate else []
    (directory / "acceptance.json").write_text(
        json.dumps({"checks": checks, "presets": []}), encoding="utf-8")
    (directory / "steps.json").write_text(
        json.dumps({"steps": steps or [], "seeded": seeded}), encoding="utf-8")
    return directory


# ── recovering the link ──────────────────────────────────────────────────────
def test_find_task_id_recognises_only_a_real_task_id():
    text = "registered rig-20260101-101010-fix-login in an isolated worktree"
    assert find_task_id(text) == "rig-20260101-101010-fix-login"


def test_queue_labels_are_not_mistaken_for_task_ids():
    """`rig-queue`, `rig-running`, `rig-done` and `rig-failed` are all over the queue's
    own output. The pattern is anchored to the timestamped shape for that reason."""
    assert find_task_id("moved rig-queue -> rig-running, then rig-done") == ""


def test_the_last_mention_wins():
    """The transcript quotes the id at register, at each step, at the gate and in the
    board hint, and a retry inside the same session registers a second task. The last
    one named is the run the operator is being pointed at."""
    text = ("started rig-20260101-101010-first\n"
            "abandoned; restarted as rig-20260101-111111-second\n")
    assert find_task_id(text) == "rig-20260101-111111-second"


@pytest.mark.parametrize("text", ["", None, "no ids here at all"])
def test_find_task_id_returns_empty_rather_than_raising(text):
    assert find_task_id(text) == ""


def test_task_action_on_an_id_that_was_printed_but_never_registered(tmp_path):
    """A provider that died before `new`, or one that invented an id. Not an error —
    the honest answer is "cannot link", and the caller says so."""
    assert task_action(tmp_path, "rig-20260101-101010-ghost") is None


def test_task_action_survives_a_half_written_run_directory(tmp_path):
    directory = tmp_path / ".rig" / "runs" / "rig-20260101-101010-partial"
    directory.mkdir(parents=True)
    (directory / "task.json").write_text("{ not json", encoding="utf-8")
    assert task_action(tmp_path, "rig-20260101-101010-partial") is None


# ── the regrouping ───────────────────────────────────────────────────────────
def test_a_settled_gate_is_reported_as_a_decision_you_still_owe(tmp_path):
    """The point of the whole change: `DONE` in the queue means the gate settled, and
    the task is still sitting in a worktree waiting for a person."""
    _write_task(tmp_path, "rig-20260101-101010-a", status="gate_passed", gate="passed")
    grouped = group_batch(tmp_path, [
        {"id": 1, "task": "add the endpoint", "ok": True, "task_id": "rig-20260101-101010-a"}])
    assert list(grouped["groups"]) == ["→ あなた: diff を見て accept"]
    assert grouped["unlinked"] == [] and grouped["failed"] == []


def test_items_are_bucketed_by_the_move_each_one_waits_on(tmp_path):
    _write_task(tmp_path, "rig-20260101-101010-a", status="gate_passed", gate="passed")
    _write_task(tmp_path, "rig-20260101-101011-b", status="gate_failed", gate="failed")
    _write_task(tmp_path, "rig-20260101-101012-c", status="running",
                steps=[{"name": "sign", "status": "pending", "human_gate": True,
                        "actor": "architect"}])
    grouped = group_batch(tmp_path, [
        {"id": 1, "task": "a", "ok": True, "task_id": "rig-20260101-101010-a"},
        {"id": 2, "task": "b", "ok": True, "task_id": "rig-20260101-101011-b"},
        {"id": 3, "task": "c", "ok": True, "task_id": "rig-20260101-101012-c"},
    ])
    markers = [action[:1] for action in grouped["groups"]]
    # Your own decisions first, then what is parked on somebody else. A batch summary
    # that leads with other people's signatures buries the part you can act on.
    assert markers == ["→", "→", "⏸"]


def test_a_queue_level_failure_is_separated_from_a_diff_to_review(tmp_path):
    """`retry` and `accept` are different moves. Merging them would put an item that
    never produced a diff into the list of diffs to look at."""
    grouped = group_batch(tmp_path, [{"id": 7, "task": "broken", "ok": False, "task_id": ""}])
    assert grouped["failed"] and not grouped["groups"]
    assert "queue retry" in "\n".join(render_batch(grouped))


def test_an_item_with_no_recoverable_id_is_named_not_hidden(tmp_path):
    """Silently omitting it would make the batch look smaller than it was, and the
    summary genuinely does not know what that run left behind."""
    grouped = group_batch(tmp_path, [{"id": 4, "task": "mystery", "ok": True, "task_id": ""}])
    assert [row["id"] for row in grouped["unlinked"]] == [4]
    out = "\n".join(render_batch(grouped))
    assert "確認できませんでした" in out and "board" in out


def test_an_id_that_does_not_resolve_is_unlinked_rather_than_assumed_done(tmp_path):
    grouped = group_batch(tmp_path, [
        {"id": 5, "task": "vanished", "ok": True, "task_id": "rig-20260101-101010-ghost"}])
    assert [row["id"] for row in grouped["unlinked"]] == [5]
    assert grouped["groups"] == {}


def test_render_is_empty_for_an_empty_batch(tmp_path):
    assert render_batch(group_batch(tmp_path, [])) == []


def test_the_rendered_block_names_both_the_queue_item_and_the_task(tmp_path):
    """The operator has the queue id in front of them and needs the task id to act."""
    _write_task(tmp_path, "rig-20260101-101010-a", status="gate_passed", gate="passed")
    out = "\n".join(render_batch(group_batch(tmp_path, [
        {"id": 9, "task": "add the endpoint", "ok": True,
         "task_id": "rig-20260101-101010-a"}])))
    assert "#9" in out
    assert "rig-20260101-101010-a" in out
    assert "add the endpoint" in out


# ── backlog depth on the dashboard ───────────────────────────────────────────
def test_queue_depth_without_a_local_queue(tmp_path):
    assert "No local queue" in queue_depth_lines(tmp_path)[0]


def test_queue_depth_counts_by_status_and_hides_done(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "status": "queued", "task": "a"},
        {"id": 2, "status": "queued", "task": "b"},
        {"id": 3, "status": "running", "task": "c"},
        {"id": 4, "status": "done", "task": "d"},
    ]}), encoding="utf-8")
    line = queue_depth_lines(tmp_path)[0]
    assert "queued=2" in line and "running=1" in line and "done" not in line


def test_queue_depth_points_at_the_retry_for_failed_items(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "queue.json").write_text(json.dumps({"items": [
        {"id": 8, "status": "failed", "task": "flaky one"}]}), encoding="utf-8")
    out = "\n".join(queue_depth_lines(tmp_path))
    assert "failed #8" in out and "retry 8" in out


def test_an_unreadable_queue_is_never_displayed_as_an_empty_one(tmp_path):
    """"0 queued" and "the backlog file is broken" must not look identical on a
    dashboard — that is the reading that let a queue store get silently rewritten
    empty (#360)."""
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "queue.json").write_text("{ truncated", encoding="utf-8")
    assert "Unreadable" in queue_depth_lines(tmp_path)[0]


def test_a_drained_queue_says_so(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "status": "done", "task": "a"}]}), encoding="utf-8")
    assert "Nothing pending (1 done)." in queue_depth_lines(tmp_path)


def test_cockpit_renders_the_queue_panel(git_repo):
    result = run_cli(["cockpit"], git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Queue depth" in result.stdout
    assert "No local queue" in result.stdout
