"""Production outcome feedback loop: record-commit / record-outcome / trace-commit (#289, #300).

Subprocess smoke tests against a throwaway git repo (workbench.py needs `repo_root()`,
i.e. an actual git repository) — mirrors tests/test_cli_smoke.py's pattern for orchestrate.py.
"""

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
    r = run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    return next((git_repo / ".rig" / "runs").iterdir()).name


@pytest.fixture
def head_sha(git_repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=git_repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def test_record_commit_defaults_to_head(git_repo, task_id, head_sha):
    r = run_cli(["record-commit", task_id], git_repo)
    assert r.returncode == 0
    assert head_sha[:12] in r.stdout


def test_record_commit_explicit_sha(git_repo, task_id):
    r = run_cli(["record-commit", task_id, "deadbeef"], git_repo)
    assert r.returncode == 0
    assert "deadbeef" in r.stdout


def test_trace_commit_before_any_link_fails(git_repo, task_id, head_sha):
    # task_id exists (so .rig/runs/ is populated) but was never linked via record-commit.
    r = run_cli(["trace-commit", head_sha], git_repo)
    assert r.returncode != 0
    assert "No task is linked" in (r.stdout + r.stderr)


def test_trace_commit_with_no_run_history_at_all_fails(git_repo, head_sha):
    r = run_cli(["trace-commit", head_sha], git_repo)
    assert r.returncode != 0
    assert "no run history" in (r.stdout + r.stderr)


def test_trace_commit_finds_linked_task_with_no_outcome_yet(git_repo, task_id, head_sha):
    run_cli(["record-commit", task_id], git_repo)
    r = run_cli(["trace-commit", head_sha], git_repo)
    assert r.returncode == 0
    assert task_id in r.stdout
    assert "Recorded outcome: none" in r.stdout


def test_record_outcome_ok_status(git_repo, task_id):
    r = run_cli(["record-outcome", task_id, "--status", "ok"], git_repo)
    assert r.returncode == 0
    assert "ok" in r.stdout
    assert "Next step" not in r.stdout  # only incidents get the trace-commit nudge


def test_record_outcome_incident_status_suggests_next_step(git_repo, task_id):
    r = run_cli(["record-outcome", task_id, "--status", "incident", "--note", "broke prod"], git_repo)
    assert r.returncode == 0
    assert "incident" in r.stdout and "broke prod" in r.stdout
    assert "trace-commit" in r.stdout


def test_record_outcome_rejects_invalid_status(git_repo, task_id):
    r = run_cli(["record-outcome", task_id, "--status", "bogus"], git_repo)
    assert r.returncode != 0


def test_trace_commit_shows_incident_outcome_and_revert_draft(git_repo, task_id, head_sha):
    run_cli(["record-commit", task_id], git_repo)
    run_cli(["record-outcome", task_id, "--status", "incident", "--note", "broke prod"], git_repo)
    r = run_cli(["trace-commit", head_sha], git_repo)
    assert r.returncode == 0
    assert "incident" in r.stdout
    assert "broke prod" in r.stdout
    assert f"git revert {head_sha}" in r.stdout
    assert "Revert" in r.stdout and task_id in r.stdout


def test_trace_commit_accepts_short_sha_prefix(git_repo, task_id, head_sha):
    run_cli(["record-commit", task_id], git_repo)
    r = run_cli(["trace-commit", head_sha[:10]], git_repo)
    assert r.returncode == 0
    assert task_id in r.stdout
