"""Base drift: the task diff range must survive a rebase (#312).

`task.json` records `base_commit` once, at registration, and never updates it.
Rebasing a task branch onto a newer base is routine — and it used to make every
range silently wrong: `git diff <recorded>...HEAD` resolves three-dot against the
*recorded* commit, which after a rebase onto a descendant is still the merge base,
so everything that landed on the base in between was counted as the task's own
work. `accept` would then re-apply that on top of itself.

These tests build the real situation (base branch moves, task branch rebases onto
it) and pin three things: the computed range is the narrow correct one, the drift
is reported where a human sees it, and none of that fires when there is no drift.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.state import _diff_lines, effective_base

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(tmp_path):
    sh(["git", "init", "-q", "-b", "master"], tmp_path)
    sh(["git", "config", "user.email", "t@t.com"], tmp_path)
    sh(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    sh(["git", "add", "f.txt"], tmp_path)
    sh(["git", "commit", "-q", "-m", "init"], tmp_path)
    return tmp_path


def _new_task(git_repo):
    """Register a task, commit the .gitignore `new` appends (accept needs a clean root)."""
    r = run_cli(["new", "drift task", "--type", "feature"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    sh(["git", "add", "-A", "--", ".gitignore"], git_repo)
    sh(["git", "commit", "-q", "-m", "gitignore .rig/"], git_repo)
    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, task


def _commit_in_worktree(task, name="task_file.txt"):
    wt = pathlib.Path(task["worktree_path"])
    (wt / name).write_text("the task's own work\n", encoding="utf-8")
    sh(["git", "add", name], wt)
    sh(["git", "commit", "-q", "-m", "task work"], wt)
    return wt


def _land_on_base(git_repo, n=5):
    """Someone else's work lands on the base branch after the task was registered."""
    for i in range(n):
        (git_repo / f"other{i}.txt").write_text(f"not the task's work {i}\n", encoding="utf-8")
    sh(["git", "add", "-A"], git_repo)
    sh(["git", "commit", "-q", "-m", "other task landed"], git_repo)
    return sh(["git", "rev-parse", "HEAD"], git_repo).strip()


def _make_acceptable(git_repo, task_id):
    d = git_repo / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        c["status"] = "passed" if c["name"] == "no_unrelated_diff" else "skipped"
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")


def _names(lines):
    return sorted(line.split("\t")[-1] for line in lines)


# ── the drift case ────────────────────────────────────────────────────────────

@pytest.fixture
def rebased(git_repo):
    """base@A → task branch commits → base moves to B → task branch rebases onto B."""
    task_id, task = _new_task(git_repo)
    wt = _commit_in_worktree(task)
    new_base = _land_on_base(git_repo)
    sh(["git", "rebase", "master"], wt)
    return git_repo, task_id, task, wt, new_base


def test_range_is_narrow_after_rebase(rebased):
    git_repo, _task_id, task, wt, new_base = rebased

    # The stale record still resolves, and the old three-dot range against it is
    # exactly the widened one — without this the narrow assertion below could pass
    # for the wrong reason (a setup that never produced drift at all).
    stale = task["base_commit"]
    wide = sh(["git", "diff", "--name-status", f"{stale}...HEAD"], wt).splitlines()
    assert _names(wide) == [".gitignore", "other0.txt", "other1.txt", "other2.txt",
                            "other3.txt", "other4.txt", "task_file.txt"]

    names, stat, dirty = _diff_lines(git_repo, task)
    assert _names(names) == ["task_file.txt"]
    assert "1 file changed" in stat
    assert dirty == []

    eff, drifted_from = effective_base(git_repo, task)
    assert eff == new_base
    assert drifted_from == stale


def test_status_reports_the_drift(rebased):
    git_repo, task_id, task, _wt, new_base = rebased
    r = run_cli(["status", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "base drift" in r.stdout
    assert "rebased since it was registered" in r.stdout
    assert task["base_commit"][:12] in r.stdout      # the recorded value, named as recorded
    assert new_base[:12] in r.stdout                 # the live merge base actually used
    assert "Pending diff: 1 file(s) changed" in r.stdout


def test_diff_reports_the_drift_and_the_narrow_range(rebased):
    git_repo, task_id, _task, _wt, new_base = rebased
    r = run_cli(["diff", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "base drift" in r.stdout
    assert new_base[:12] in r.stdout
    assert "task_file.txt" in r.stdout
    assert "other0.txt" not in r.stdout


def test_accept_reports_drift_proceeds_and_records_it(rebased):
    git_repo, task_id, task, _wt, new_base = rebased
    _make_acceptable(git_repo, task_id)

    r = run_cli(["accept", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr      # drift is reported, never blocking
    assert "base drift" in r.stdout
    # Applied range = the task's own commit only, not the base commits it was rebased onto.
    assert "(1 commits)" in r.stdout

    staged = _names(sh(["git", "diff", "--staged", "--name-status"], git_repo).splitlines())
    assert staged == ["task_file.txt"]

    prov = json.loads((git_repo / ".rig" / "runs" / task_id / "provenance.json")
                      .read_text(encoding="utf-8"))["record"]
    assert prov["base_commit"] == task["base_commit"]   # the record stays the historical fact
    assert prov["base_commit_effective"] == new_base
    assert prov["base_rebased"] is True

    assert run_cli(["verify-provenance", task_id], git_repo).returncode == 0


def test_secret_sensor_scans_only_the_rebased_range(rebased):
    """A stale base makes the diff-scoped sensors scan other people's changes."""
    from rig_workbench.workbench.secrets import scan_worktree_diff

    # Borrow the sample instead of restating a token literal. The scanner's own
    # test module is the one place in the repo that holds these; a second copy
    # would surface as a fresh finding in every diff scan that touches this file.
    from test_secret_scan import SAMPLES

    git_repo, _task_id, task, wt, _new_base = rebased
    # Plant a secret in a file that landed on the *base*, not in the task's diff.
    (git_repo / "leaked.txt").write_text(
        f'aws_access_key_id = "{SAMPLES[0][1]}"\n', encoding="utf-8")
    sh(["git", "add", "-A"], git_repo)
    sh(["git", "commit", "-q", "-m", "base-side commit"], git_repo)
    sh(["git", "rebase", "master"], wt)

    eff, drifted_from = effective_base(git_repo, task)
    assert drifted_from is not None
    assert scan_worktree_diff(wt, eff) == []
    assert scan_worktree_diff(wt, task["base_commit"]) != []   # what the stale base would have seen


# ── the no-drift case: nothing must fire ─────────────────────────────────────

def test_no_drift_is_silent_and_range_unchanged(git_repo):
    task_id, task = _new_task(git_repo)
    _commit_in_worktree(task)

    eff, drifted_from = effective_base(git_repo, task)
    assert eff == task["base_commit"]
    assert drifted_from is None

    names, _stat, _dirty = _diff_lines(git_repo, task)
    assert _names(names) == ["task_file.txt"]

    r = run_cli(["status", task_id], git_repo)
    assert "base drift" not in r.stdout
    assert "rebased" not in r.stdout
    assert run_cli(["diff", task_id], git_repo).stdout.count("base drift") == 0


# ── `--base <branch>` from another branch ────────────────────────────────────
# The drift machinery above assumes the recorded `base_commit` was the real fork
# point. `new --base master` while `feature` is checked out used to record HEAD
# (= feature's tip) instead, so every commit unique to `feature` was counted as
# the task's own work — no rebase required, and the drift warning never fires
# because nothing drifted. Fixed where it originates: at registration.


@pytest.fixture
def diverged(git_repo):
    """master@A, then `feature` branches off and moves to F, checked out."""
    base_sha = sh(["git", "rev-parse", "HEAD"], git_repo).strip()
    sh(["git", "checkout", "-q", "-b", "feature"], git_repo)
    (git_repo / "feature_only.txt").write_text("someone else's branch work\n", encoding="utf-8")
    sh(["git", "add", "-A"], git_repo)
    sh(["git", "commit", "-q", "-m", "feature work"], git_repo)
    feature_sha = sh(["git", "rev-parse", "HEAD"], git_repo).strip()
    return git_repo, base_sha, feature_sha


def test_explicit_base_is_recorded_and_forked_from(diverged):
    git_repo, base_sha, feature_sha = diverged
    r = run_cli(["new", "based task", "--type", "feature", "--base", "master"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr

    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json")
                      .read_text(encoding="utf-8"))
    assert task["base_branch"] == "master"
    assert task["base_commit"] == base_sha != feature_sha
    wt = pathlib.Path(task["worktree_path"])
    assert sh(["git", "rev-parse", "HEAD"], wt).strip() == base_sha


def test_explicit_base_keeps_the_other_branch_out_of_the_task_diff(diverged):
    git_repo, base_sha, _feature_sha = diverged
    r = run_cli(["new", "based task", "--type", "feature", "--base", "master"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = sorted(p.name for p in (git_repo / ".rig" / "runs").iterdir())[-1]
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json")
                      .read_text(encoding="utf-8"))
    _commit_in_worktree(task)

    names, _stat, _dirty = _diff_lines(git_repo, task)
    assert _names(names) == ["task_file.txt"]        # not feature_only.txt

    # And the drift machinery still agrees with the record: nothing drifted.
    eff, drifted_from = effective_base(git_repo, task)
    assert eff == base_sha
    assert drifted_from is None


def test_unresolvable_base_fails_fast(git_repo):
    r = run_cli(["new", "based task", "--type", "feature", "--base", "no-such-branch"], git_repo)
    assert r.returncode != 0
    assert "does not resolve to a commit" in (r.stdout + r.stderr)
    # No partial state: it aborts before the run dir and before the .gitignore edit.
    assert not (git_repo / ".rig" / "runs").exists()
    assert not (git_repo / ".gitignore").exists()


def test_base_branch_moving_ahead_alone_is_not_drift(git_repo):
    """Only a rebase changes the merge base. A base branch that merely moved ahead
    while the task branch stayed put must not be reported."""
    task_id, task = _new_task(git_repo)
    _commit_in_worktree(task)
    _land_on_base(git_repo)

    eff, drifted_from = effective_base(git_repo, task)
    assert eff == task["base_commit"]
    assert drifted_from is None
    assert "base drift" not in run_cli(["status", task_id], git_repo).stdout


def test_no_drift_accept_records_the_recorded_base(git_repo):
    task_id, task = _new_task(git_repo)
    _commit_in_worktree(task)
    _make_acceptable(git_repo, task_id)

    r = run_cli(["accept", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "base drift" not in r.stdout
    # The companion pre-flight warning (preview range vs the range the squash merge
    # applies) must stay quiet too, or it becomes noise on every accept.
    assert "is not an ancestor" not in r.stdout
    prov = json.loads((git_repo / ".rig" / "runs" / task_id / "provenance.json")
                      .read_text(encoding="utf-8"))["record"]
    assert prov["base_commit_effective"] == task["base_commit"]
    assert prov["base_rebased"] is False


# ── fallbacks: never worse than the record ───────────────────────────────────

def test_falls_back_to_the_record_when_the_live_value_is_unavailable(git_repo, tmp_path):
    task_id, task = _new_task(git_repo)
    _commit_in_worktree(task)

    # No base_branch recorded (older run state / hand-built task dicts).
    no_branch = dict(task, base_branch="")
    assert effective_base(git_repo, no_branch) == (task["base_commit"], None)

    # base_branch no longer resolves.
    gone = dict(task, base_branch="deleted-branch")
    assert effective_base(git_repo, gone) == (task["base_commit"], None)

    # Worktree-less run: there is no branch to rebase, so there is nothing to recompute.
    assert effective_base(git_repo, dict(task, worktree_path=None)) == (task["base_commit"], None)


def test_detached_base_branch_record_does_not_collapse_the_range(git_repo):
    """`current_branch` returns the literal "HEAD" on a detached root, and "HEAD"
    resolves per worktree — resolving it inside the worktree would return the task
    branch tip and silently empty the range. It must fall back instead."""
    task_id, task = _new_task(git_repo)
    _commit_in_worktree(task)

    detached = dict(task, base_branch="HEAD")
    assert effective_base(git_repo, detached) == (task["base_commit"], None)
    names, _stat, _dirty = _diff_lines(git_repo, detached)
    assert _names(names) == ["task_file.txt"]
