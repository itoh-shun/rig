"""Stacked tasks: `new --parent`, the parent/child model, and `wb cascade`.

Covers the thing `gh stack` could not do in rig's layout — rebase a child while
every branch stays checked out in its own worktree — plus the refusals that
make it safe to run unattended: a dirty child is blocked, a conflicted rebase
is aborted and its subtree skipped, and nothing is moved silently.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.cascade import ancestry, children_of, roots

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=120)


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    git(["config", "user.email", "t@t.com"], tmp_path)
    git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("base\n", encoding="utf-8")
    git(["add", "-A"], tmp_path)
    git(["commit", "-qm", "init"], tmp_path)
    return tmp_path


def _new(repo, name, parent=None):
    args = ["new", name, "--type", "feature", "--slug", name]
    if parent:
        args += ["--parent", parent]
    r = run_cli(args, repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next(ln.split(": ", 1)[1] for ln in r.stdout.splitlines()
                   if ln.startswith("task_id: "))
    return task_id, _wt(repo, task_id)


def _task(repo, task_id):
    return json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))


def _wt(repo, task_id):
    return pathlib.Path(_task(repo, task_id)["worktree_path"])


def _commit(wt, filename, content, message):
    (wt / filename).write_text(content, encoding="utf-8")
    git(["add", "-A"], wt)
    git(["commit", "-qm", message], wt)


def _subjects(wt):
    return git(["log", "--format=%s"], wt).stdout.split()


# ---- the model ---------------------------------------------------------------

def test_parent_records_the_stack_fields(repo):
    parent, _ = _new(repo, "parent")
    child, _ = _new(repo, "child", parent=parent)

    t = _task(repo, child)
    assert t["parent_task"] == parent
    assert t["base_branch"] == _task(repo, parent)["branch"]
    # stack_base is the --onto upstream a later cascade needs; it cannot be
    # recomputed once the parent's history is rewritten.
    assert t["stack_base"] == t["base_commit"]


def test_parent_and_base_are_mutually_exclusive(repo):
    parent, _ = _new(repo, "parent")
    r = run_cli(["new", "x", "--type", "feature", "--parent", parent, "--base", "master"], repo)
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr


def test_unknown_parent_is_refused(repo):
    r = run_cli(["new", "x", "--type", "feature", "--parent", "rig-nope"], repo)
    assert r.returncode != 0
    assert "not found" in r.stderr


def test_ancestry_and_roots_are_derived_from_the_records(repo):
    a, _ = _new(repo, "a")
    b, _ = _new(repo, "b", parent=a)
    c, _ = _new(repo, "c", parent=b)
    from rig_workbench.workbench.cascade import read_all
    tasks = read_all(repo)

    assert ancestry(tasks, c) == [b, a]
    assert roots(tasks) == [a]
    assert [t["task_id"] for t in children_of(tasks, a)] == [b]


def test_ancestry_survives_a_cycle_in_hand_edited_records():
    tasks = {"x": {"task_id": "x", "parent_task": "y"},
             "y": {"task_id": "y", "parent_task": "x"}}
    assert ancestry(tasks, "x") == ["y"]  # stops instead of looping forever


# ---- cascade -----------------------------------------------------------------

def test_nothing_to_cascade_is_stated_not_silent(repo):
    _new(repo, "solo")
    r = run_cli(["cascade"], repo)
    assert r.returncode == 0
    assert "no stacked tasks" in r.stdout


def test_up_to_date_stack_is_left_alone(repo):
    a, _ = _new(repo, "a")
    _new(repo, "b", parent=a)
    r = run_cli(["cascade", "--dry-run"], repo)
    assert "up to date" in r.stdout
    assert "would rebase: 0" in r.stdout


def test_child_is_replayed_onto_the_parents_new_tip(repo):
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    _commit(wa, "a.txt", "a\n", "parent-work")
    _commit(wb, "b.txt", "b\n", "child-work")
    _commit(wa, "a.txt", "a2\n", "parent-fixup")

    assert "parent-work" not in _subjects(wb)
    r = run_cli(["cascade"], repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rebased: 1" in r.stdout

    subjects = _subjects(wb)
    assert subjects[:3] == ["child-work", "parent-fixup", "parent-work"]
    assert _task(repo, b)["stack_base"] == git(["rev-parse", "HEAD"], wa).stdout.strip()


def test_three_layers_cascade_top_down_in_one_pass(repo):
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    c, wc = _new(repo, "c", parent=b)
    _commit(wa, "a.txt", "a\n", "layer-a")
    _commit(wb, "b.txt", "b\n", "layer-b")
    _commit(wc, "c.txt", "c\n", "layer-c")

    r = run_cli(["cascade"], repo)
    assert "rebased: 2" in r.stdout, r.stdout
    # The deepest layer now carries every ancestor's work, in stack order.
    assert _subjects(wc)[:3] == ["layer-c", "layer-b", "layer-a"]


def test_dirty_child_is_blocked_and_its_subtree_skipped(repo):
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    c, _ = _new(repo, "c", parent=b)
    _commit(wa, "a.txt", "a\n", "layer-a")
    (wb / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")

    r = run_cli(["cascade"], repo)
    assert "BLOCKED" in r.stdout
    assert "uncommitted" in r.stdout
    assert "SKIPPED (its parent did not move)" in r.stdout
    assert "blocked: 1 / skipped: 1" in r.stdout
    # Refused, never stashed: the uncommitted file is still there.
    assert (wb / "scratch.txt").exists()
    assert "layer-a" not in _subjects(wb)


def test_conflict_aborts_the_rebase_and_leaves_the_child_untouched(repo):
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    _commit(wb, "f.txt", "child-side\n", "child-edit")
    _commit(wa, "f.txt", "parent-side\n", "parent-edit")

    r = run_cli(["cascade"], repo)
    assert "CONFLICT" in r.stdout
    assert "rebase aborted" in r.stdout
    assert git(["status", "--porcelain"], wb).stdout.strip() == ""
    assert _subjects(wb)[0] == "child-edit"
    assert (wb / "f.txt").read_text(encoding="utf-8") == "child-side\n"


def test_dry_run_changes_nothing(repo):
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    _commit(wa, "a.txt", "a\n", "layer-a")
    before = git(["rev-parse", "HEAD"], wb).stdout.strip()

    r = run_cli(["cascade", "--dry-run"], repo)
    assert "would rebase --onto" in r.stdout
    assert git(["rev-parse", "HEAD"], wb).stdout.strip() == before


def test_gate_diff_stays_scoped_to_the_child_after_a_cascade(repo):
    """The whole point of stacking: each layer keeps its own gate.

    After the cascade the child's diff must still be the child's own work — if
    the base went stale the gate would grade it on the parent's commits too."""
    a, wa = _new(repo, "a")
    b, wb = _new(repo, "b", parent=a)
    _commit(wa, "parent_only.py", "X = 1\n", "layer-a")
    _commit(wb, "child_only.py", "Y = 2\n", "layer-b")
    _commit(wa, "parent_only.py", "X = 2\n", "layer-a-fixup")
    run_cli(["cascade"], repo)

    r = run_cli(["diff", b], repo)
    assert "child_only.py" in r.stdout
    assert "parent_only.py" not in r.stdout


def test_discarding_a_parent_warns_about_the_orphans(repo):
    a, _ = _new(repo, "a")
    b, _ = _new(repo, "b", parent=a)
    r = run_cli(["discard", a], repo)
    assert "will be orphaned" in r.stdout
    assert b in r.stdout


def test_status_shows_the_stack_position(repo):
    a, _ = _new(repo, "a")
    b, _ = _new(repo, "b", parent=a)
    assert b in run_cli(["status", a], repo).stdout
    assert a in run_cli(["status", b], repo).stdout
