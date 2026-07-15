"""RBAC for accept (#282) and time/cost budget warnings (#281).

Both are additive and opt-in: absent .rig/access.json means unrestricted accept
(today's behavior); absent --budget-minutes means no warning.
"""

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.state import budget_status

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd, env=None):
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=full_env)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---- budget_status (pure function) ------------------------------------------

def test_budget_status_no_budget_is_never_over():
    task = {"created_at": "2020-01-01T00:00:00+00:00"}  # ancient, but no budget set
    elapsed, budget, over = budget_status(task)
    assert budget is None
    assert over is False


def test_budget_status_under_budget_is_not_over():
    import datetime

    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    task = {"created_at": now, "budget_minutes": 60}
    _, budget, over = budget_status(task)
    assert budget == 60
    assert over is False


def test_budget_status_over_budget_is_flagged():
    task = {"created_at": "2020-01-01T00:00:00+00:00", "budget_minutes": 5}
    elapsed, budget, over = budget_status(task)
    assert elapsed > 5
    assert over is True


# ---- workbench.py new --budget-minutes + status/board -----------------------

def test_no_budget_flag_means_no_warning(git_repo):
    r = run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    r = run_cli(["status", task_id], git_repo)
    assert "budget:" not in r.stdout
    r = run_cli(["board", "--all"], git_repo)
    assert "over budget" not in r.stdout


def test_budget_over_shows_warning_in_status_and_board(git_repo):
    run_cli(["new", "test task", "--type", "feature", "--no-worktree", "--budget-minutes", "0.001"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    # Backdate created_at so the tiny budget is already exceeded without sleeping.
    tj = git_repo / ".rig" / "runs" / task_id / "task.json"
    task = json.loads(tj.read_text(encoding="utf-8"))
    task["created_at"] = "2020-01-01T00:00:00+00:00"
    tj.write_text(json.dumps(task), encoding="utf-8")

    r = run_cli(["status", task_id], git_repo)
    assert "over budget" in r.stdout
    r = run_cli(["board", "--all"], git_repo)
    assert "over budget" in r.stdout


# ---- RBAC (.rig/access.json) -------------------------------------------------

def _make_acceptable_task(git_repo, task_id):
    """Set every criterion to skipped/passed and write diff.md so only RBAC can block accept."""
    d = git_repo / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        c["status"] = "passed" if c["name"] in ("no_unrelated_diff",) else "skipped"
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")
    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    task["worktree_path"] = str(git_repo)  # satisfy worktree_exists without a real worktree
    (d / "task.json").write_text(json.dumps(task), encoding="utf-8")


def test_no_access_json_is_unrestricted(git_repo):
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)
    r = run_cli(["accept", task_id], git_repo, env={"RIG_USER": "anyone"})
    # RBAC must not be what blocks this — whatever happens past accept_requirements
    # (squash-merge mechanics) is out of scope for this test.
    assert "is not permitted to accept" not in (r.stdout + r.stderr)
    assert "✓ acceptance_gate_not_failed" in r.stdout


def test_access_json_blocks_unlisted_identity(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "access.json").write_text(
        json.dumps({"feature": ["alice", "bob"]}), encoding="utf-8")
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)
    r = run_cli(["accept", task_id], git_repo, env={"RIG_USER": "eve"})
    assert r.returncode != 0
    assert "'eve' is not permitted to accept task_type 'feature'" in (r.stdout + r.stderr)


def test_access_json_allows_listed_identity(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "access.json").write_text(
        json.dumps({"feature": ["alice", "bob"]}), encoding="utf-8")
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)
    r = run_cli(["accept", task_id], git_repo, env={"RIG_USER": "alice"})
    assert "is not permitted to accept" not in (r.stdout + r.stderr)
    assert "✓ acceptance_gate_not_failed" in r.stdout


def test_access_json_default_key_is_fallback(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "access.json").write_text(
        json.dumps({"default": ["alice"]}), encoding="utf-8")
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)
    r = run_cli(["accept", task_id], git_repo, env={"RIG_USER": "eve"})
    assert r.returncode != 0
    assert "not permitted" in (r.stdout + r.stderr)


def test_malformed_access_json_falls_back_to_unrestricted(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "access.json").write_text("not valid json{{{", encoding="utf-8")
    run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    _make_acceptable_task(git_repo, task_id)
    r = run_cli(["accept", task_id], git_repo, env={"RIG_USER": "anyone"})
    assert "is not permitted to accept" not in (r.stdout + r.stderr)
    assert "✓ acceptance_gate_not_failed" in r.stdout
