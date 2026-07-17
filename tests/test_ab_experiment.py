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


# ---- manifest A/B (#317) -----------------------------------------------------

def run_cli_trust(args, cwd, trust_store):
    import os

    env = dict(os.environ, RIG_HOME=str(REPO_ROOT), RIG_TRUST_STORE=str(trust_store))
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, cwd=cwd, env=env, timeout=60)


def _write_manifests(git_repo):
    (git_repo / "mA.md").write_text("---\nname: rules-a\n---\nrule set A\n", encoding="utf-8")
    (git_repo / "mB.md").write_text("---\nname: rules-b\n---\nrule set B\n", encoding="utf-8")


def test_manifest_ab_runs_one_recipe_under_two_manifests(git_repo, tmp_path):
    _write_manifests(git_repo)
    trust = tmp_path / "trust.json"
    r = run_cli_trust(["ab", "review-only", "--manifest-a", "mA.md", "--manifest-b", "mB.md",
                       "--provider", "mock", "--goal", "rule ab"], git_repo, trust)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "A(mA)" in r.stdout and "B(mB)" in r.stdout  # which manifest per row is explicit
    assert "DONE" in r.stdout
    assert (git_repo / "ab-manifest-a-state.json").exists()
    assert (git_repo / "ab-manifest-b-state.json").exists()
    # Explicit CLI provision = consent: both variant manifests are trust-recorded.
    import json as _json
    store = _json.loads(trust.read_text(encoding="utf-8"))
    assert sum(1 for k in store if k.endswith(".claude/rig.md")) == 2
    # The main repo's own manifest is untouched.
    assert not (git_repo / ".claude" / "rig.md").exists()


def test_manifest_ab_requires_both_manifests(git_repo):
    _write_manifests(git_repo)
    r = run_cli(["ab", "review-only", "--manifest-a", "mA.md",
                 "--provider", "mock", "--goal", "x"], git_repo)
    assert r.returncode != 0
    assert "BOTH" in (r.stdout + r.stderr)


def test_manifest_ab_requires_exactly_one_recipe(git_repo):
    _write_manifests(git_repo)
    r = run_cli(["ab", "review-only", "bugfix", "--manifest-a", "mA.md", "--manifest-b", "mB.md",
                 "--provider", "mock", "--goal", "x"], git_repo)
    assert r.returncode != 0
    assert "exactly 1 recipe" in (r.stdout + r.stderr)


def test_manifest_ab_missing_file_errors(git_repo):
    (git_repo / "mA.md").write_text("x\n", encoding="utf-8")
    r = run_cli(["ab", "review-only", "--manifest-a", "mA.md", "--manifest-b", "nope.md",
                 "--provider", "mock", "--goal", "x"], git_repo)
    assert r.returncode != 0
    assert "does not exist" in (r.stdout + r.stderr)
