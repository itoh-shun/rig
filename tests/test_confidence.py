"""Confidence-weighted gate via drill detection rate (#301).

Subprocess smoke tests against a throwaway git repo, mirroring
tests/test_production_feedback.py's pattern.
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


@pytest.fixture
def task_id(git_repo):
    r = run_cli(["new", "test task", "--type", "review", "--no-worktree"], git_repo)
    assert r.returncode == 0
    return next((git_repo / ".rig" / "runs").iterdir()).name


def _write_drill(git_repo, rows):
    (git_repo / ".rig").mkdir(exist_ok=True)
    (git_repo / ".rig" / "drill-results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_confidence_with_no_drill_data(git_repo):
    r = run_cli(["confidence"], git_repo)
    assert r.returncode == 0
    assert "No drill measurements" in r.stdout


def test_whole_repo_confidence_flags_low_and_high(git_repo):
    _write_drill(git_repo, [
        {"scores": [{"reviewer": "security-reviewer", "detected": 9, "seeded": 10, "false_positives": 0},
                    {"reviewer": "perf-reviewer", "detected": 3, "seeded": 10, "false_positives": 1}]},
    ])
    r = run_cli(["confidence"], git_repo)
    assert r.returncode == 0
    assert "security-reviewer: 90%" in r.stdout
    assert "perf-reviewer: 30%" in r.stdout and "low confidence" in r.stdout
    # security-reviewer's own line should not be flagged
    sec_line = next(line for line in r.stdout.splitlines() if "security-reviewer:" in line)
    assert "low confidence" not in sec_line


def test_unmeasured_persona_is_not_fabricated(git_repo):
    _write_drill(git_repo, [{"scores": [{"reviewer": "security-reviewer", "detected": 0, "seeded": 0}]}])
    r = run_cli(["confidence"], git_repo)
    assert r.returncode == 0
    assert "security-reviewer: unmeasured" in r.stdout


def test_task_scoped_without_review_json_reports_no_verdicts(git_repo, task_id):
    r = run_cli(["confidence", task_id], git_repo)
    assert r.returncode == 0
    assert "no review.json record" in r.stdout


def test_task_scoped_records_reviewer_confidence_into_acceptance_json(git_repo, task_id):
    _write_drill(git_repo, [
        {"scores": [{"reviewer": "security-reviewer", "detected": 9, "seeded": 10}]},
    ])
    run_cli(["review", task_id, "--set", "security-reviewer=APPROVE"], git_repo)
    r = run_cli(["confidence", task_id], git_repo)
    assert r.returncode == 0
    assert "security-reviewer: 90%" in r.stdout

    acc = json.loads((git_repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    assert acc["reviewer_confidence"] == {"security-reviewer": 0.9}


def test_task_scoped_low_confidence_suggests_extra_reviewer(git_repo, task_id):
    _write_drill(git_repo, [{"scores": [{"reviewer": "security-reviewer", "detected": 2, "seeded": 10}]}])
    run_cli(["review", task_id, "--set", "security-reviewer=APPROVE"], git_repo)
    r = run_cli(["confidence", task_id], git_repo)
    assert r.returncode == 0
    assert "low confidence" in r.stdout
    assert "Consider bringing in an additional reviewer" in r.stdout


def test_task_scoped_unmeasured_reviewer_stays_unmeasured(git_repo, task_id):
    run_cli(["review", task_id, "--set", "never-drilled-reviewer=APPROVE"], git_repo)
    r = run_cli(["confidence", task_id], git_repo)
    assert r.returncode == 0
    assert "never-drilled-reviewer: unmeasured" in r.stdout
