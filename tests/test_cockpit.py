"""Read-only Mission Control dashboard (#307): `workbench.py cockpit`.

Subprocess smoke tests against a throwaway git repo, mirroring
tests/test_confidence.py's pattern. Covers the empty state and each panel
(run timeline, gate radar, drill confidence, cost meter, safety strip,
next-action rail) with synthetic .rig/ data, reusing the same aggregation
functions board/stats/audit/confidence already have tests for — this suite
only checks cockpit renders them correctly on one screen, not that the
underlying aggregation is correct (that's covered elsewhere).
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


def test_cockpit_empty_state_shows_unmeasured_not_blank(git_repo):
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert "0 active / 0 total" in r.stdout
    assert "No active tasks." in r.stdout
    assert "No runs yet." in r.stdout
    assert "Unmeasured" in r.stdout  # drill confidence
    assert "Unmeasured (no token usage" in r.stdout  # cost meter
    assert "No force-bypass records." in r.stdout
    assert "No action needed right now." in r.stdout


def test_cockpit_shows_active_task_in_timeline_and_gate_radar(git_repo):
    run_cli(["new", "fix login bug", "--type", "bugfix", "--no-worktree"], git_repo)
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert "1 active / 1 total" in r.stdout
    assert "fix login bug" in r.stdout
    assert "gate=pending" in r.stdout
    assert "pending: 1" in r.stdout


def test_cockpit_surfaces_drill_confidence(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "drill-results.jsonl").write_text(
        json.dumps({"scores": [{"reviewer": "security-reviewer", "detected": 4,
                                "seeded": 5, "false_positives": 1}]}) + "\n",
        encoding="utf-8")
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert "security-reviewer: 80% detection (4/5, 1 false positive(s))" in r.stdout


def test_cockpit_surfaces_cost_meter_from_runs_jsonl(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "runs.jsonl").write_text(
        json.dumps({"token_usage": {"anthropic": {"prompt_tokens": 100, "completion_tokens": 40,
                                                   "cache_read_input_tokens": 10, "calls": 2}}}) + "\n",
        encoding="utf-8")
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert "2 call(s), prompt=100, completion=40, total=140, cache_read=10" in r.stdout


def test_cockpit_surfaces_force_bypass_count(git_repo):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "audit.jsonl").write_text(
        json.dumps({"action": "accept_force", "task_id": "rig-x", "bypassed": ["no_unrelated_diff"],
                    "gate_status": "failed"}) + "\n",
        encoding="utf-8")
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert "force-bypass: 1 (details: `workbench.py audit`)" in r.stdout


def test_cockpit_next_action_rail_for_gate_passed_and_failed(git_repo):
    run_cli(["new", "task a", "--type", "feature", "--no-worktree"], git_repo)
    task_a = next((git_repo / ".rig" / "runs").iterdir()).name

    def _set_status(task_id, status):
        tj = git_repo / ".rig" / "runs" / task_id / "task.json"
        task = json.loads(tj.read_text(encoding="utf-8"))
        task["status"] = status
        tj.write_text(json.dumps(task), encoding="utf-8")

    _set_status(task_a, "gate_passed")
    r = run_cli(["cockpit"], git_repo)
    assert r.returncode == 0
    assert f"1 awaiting diff/accept: {task_a}" in r.stdout
    assert "workbench.py diff <id>" in r.stdout

    _set_status(task_a, "gate_failed")
    r = run_cli(["cockpit"], git_repo)
    assert f"1 gate not met: {task_a}" in r.stdout
    assert "workbench.py discard <id> --yes" in r.stdout
