"""Deja-vu detection: past similar task suggestions (#290).

find_similar_tasks() scores past task inputs by Jaccard overlap on a rough
tokenization (no embeddings/search engine).
"""

import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.lifecycle import _tokenize, find_similar_tasks

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = _tokenize("Fix the login bug in the auth module")
    assert "the" not in tokens and "in" not in tokens
    assert "fix" in tokens and "login" in tokens and "auth" in tokens


def test_find_similar_tasks_empty_when_no_runs_dir(tmp_path):
    assert find_similar_tasks(tmp_path, "fix the login bug") == []


# ---- workbench.py new integration -------------------------------------------

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


def test_first_task_has_no_similar_tasks_section(git_repo):
    r = run_cli(["new", "fix the login bug in auth module", "--type", "bugfix", "--no-worktree"], git_repo)
    assert r.returncode == 0
    assert "Similar tasks" not in r.stdout


def test_paraphrased_duplicate_is_caught(git_repo):
    run_cli(["new", "fix the login bug in auth module", "--type", "bugfix", "--no-worktree"], git_repo)
    r = run_cli(["new", "please fix login bug auth module", "--type", "bugfix", "--no-worktree"], git_repo)
    assert r.returncode == 0
    assert "Similar tasks (past runs, deja-vu detection #290):" in r.stdout
    assert "fix the login bug in auth module" in r.stdout


def test_unrelated_task_is_not_flagged(git_repo):
    run_cli(["new", "fix the login bug in auth module", "--type", "bugfix", "--no-worktree"], git_repo)
    r = run_cli(["new", "add dark mode toggle to settings page", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    assert "Similar tasks" not in r.stdout


def test_similar_tasks_excludes_the_task_being_created(git_repo):
    # A second, unrelated task shouldn't accidentally match itself or be self-referential.
    r = run_cli(["new", "one-off task with unique wording xyzzy", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    assert "Similar tasks" not in r.stdout
