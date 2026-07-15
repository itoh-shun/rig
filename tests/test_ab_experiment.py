"""Multi-recipe A/B experiment mode (#291).

Subprocess smoke tests running `orchestrate.py ab` against real (shipped)
recipes with --provider mock, in a throwaway git repo (isolation needs a real
git worktree).
"""

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"


def run_cli(args, cwd):
    import os

    env = dict(os.environ, RIG_HOME=str(REPO_ROOT))
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, cwd=cwd, env=env, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_ab_requires_at_least_two_recipes(git_repo):
    r = run_cli(["ab", "review-only", "--provider", "mock", "--goal", "x"], git_repo)
    assert r.returncode != 0
    assert "2 or more recipes" in (r.stdout + r.stderr)


def test_ab_requires_provider(git_repo):
    r = run_cli(["ab", "review-only", "review-only", "--goal", "x"], git_repo)
    assert r.returncode != 0
    assert "--provider" in (r.stdout + r.stderr)


def test_ab_runs_two_variants_concurrently_and_reports_comparison(git_repo):
    r = run_cli(["ab", "review-only", "bugfix", "--provider", "mock", "--goal", "test ab"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "review-only" in r.stdout and "bugfix" in r.stdout
    assert "DONE" in r.stdout
    assert "## rig ab" in r.stdout


def test_ab_variants_use_separate_isolated_worktrees(git_repo):
    r = run_cli(["ab", "review-only", "bugfix", "--provider", "mock", "--goal", "test ab"], git_repo)
    assert r.returncode == 0
    # Both variants finish DONE with no changes -> worktrees are cleaned up, not "kept".
    assert "worktree(s) were preserved" not in r.stdout
    assert not (git_repo / ".rig" / "worktrees").exists() or not list(
        (git_repo / ".rig" / "worktrees").iterdir())


def test_ab_writes_a_state_file_per_variant(git_repo):
    r = run_cli(["ab", "review-only", "bugfix", "--provider", "mock", "--goal", "test ab"], git_repo)
    assert r.returncode == 0
    assert (git_repo / "ab-review-only-state.json").exists()
    assert (git_repo / "ab-bugfix-state.json").exists()
