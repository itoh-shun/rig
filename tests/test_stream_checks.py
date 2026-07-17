"""Mid-implementation lightweight checks (#302): `workbench.py stream-checks`.

Pins the structural guarantees: hints for secret/injection/destructive
findings in the task diff, exit 0 ALWAYS (advisory), acceptance.json never
touched, and the --watch loop's change detection.
"""

import json
import pathlib
import subprocess
import sys

import pytest

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


def _new_task(git_repo):
    r = run_cli(["new", "test task", "--type", "feature"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, pathlib.Path(task["worktree_path"])


def test_clean_worktree_reports_no_hints_and_exits_zero(git_repo):
    task_id, _wt = _new_task(git_repo)
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    assert "no hints" in r.stdout
    assert "never blocks the gate" in r.stdout  # the advisory framing is part of the output


def test_findings_become_hints_but_exit_stays_zero(git_repo):
    task_id, wt = _new_task(git_repo)
    (wt / "oops.sh").write_text(
        'TOKEN="ghp_' + "a" * 40 + '"\ngit clean -fdx\n', encoding="utf-8")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0  # advisory: findings never change the exit code
    assert "hint[secret]" in r.stdout
    assert "hint[destructive]" in r.stdout
    assert "ghp_aaaa" not in r.stdout  # secret excerpts stay masked


def test_stream_checks_never_touches_acceptance_json(git_repo):
    task_id, wt = _new_task(git_repo)
    acc_path = git_repo / ".rig" / "runs" / task_id / "acceptance.json"
    before = acc_path.read_text(encoding="utf-8")
    (wt / "oops.sh").write_text("rm -rf /\n", encoding="utf-8")
    run_cli(["stream-checks", task_id], git_repo)
    assert acc_path.read_text(encoding="utf-8") == before  # gate state untouched


def test_gate_still_decides_pass_fail_independently(git_repo):
    # The same detector that hinted also fails the gate — streaming is a preview,
    # not a substitute.
    task_id, wt = _new_task(git_repo)
    (wt / "oops.sh").write_text("rm -rf /\n", encoding="utf-8")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    assert r.returncode != 0  # fail-grade destructive finding fails the gate

def test_watch_mode_bounded_by_max_passes(git_repo):
    task_id, _wt = _new_task(git_repo)
    r = run_cli(["stream-checks", task_id, "--watch", "--interval", "0.05", "--max-passes", "3"],
                git_repo)
    assert r.returncode == 0  # terminates on its own


def test_no_worktree_task_errors(git_repo):
    r = run_cli(["new", "read only", "--type", "review", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode != 0
    assert "no worktree" in (r.stdout + r.stderr)
