"""Headless CI usage packaged as a GitHub Action (#265).

action.yml wraps orchestrate.py run --isolate; scripts/rig-action-entrypoint.sh
derives the final status from the run-state JSON and only pushes/opens a PR on
a green gate. Verified here against a throwaway git repo with --provider mock
(the same honest scope the reference implementation documented: the `open-pr`
push + `gh pr create` path needs a live GitHub Actions runner and isn't
exercised here).
"""

import json
import pathlib
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "scripts" / "rig-action-entrypoint.sh"
ACTION_YML = REPO_ROOT / "action.yml"


def test_action_yml_is_valid_and_has_expected_shape():
    spec = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert spec["runs"]["using"] == "composite"
    assert set(spec["inputs"]) >= {"task", "recipe", "provider", "auto_pr", "github_token"}
    assert spec["inputs"]["task"]["required"] is True
    assert spec["inputs"]["recipe"]["required"] is True
    assert spec["inputs"]["provider"]["default"] == "mock"
    assert set(spec["outputs"]) == {"final", "pr_url"}
    step_ids = [s.get("id") for s in spec["runs"]["steps"]]
    assert step_ids == ["run", "pr"]


def test_entrypoint_script_is_executable_and_has_valid_syntax():
    assert ENTRYPOINT.stat().st_mode & 0o111  # at least one execute bit set
    r = subprocess.run(["bash", "-n", str(ENTRYPOINT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _run_entrypoint(subcommand, git_repo, env_overrides, expect_ok=True):
    import os

    env = dict(os.environ, RIG_HOME=str(REPO_ROOT))
    env.pop("GITHUB_OUTPUT", None)
    gh_output = git_repo / "gh_output.txt"
    env["GITHUB_OUTPUT"] = str(gh_output)
    env.update(env_overrides)
    r = subprocess.run(["bash", str(ENTRYPOINT), subcommand],
                       capture_output=True, text=True, cwd=git_repo, env=env, timeout=60)
    return r, gh_output


def test_run_with_mock_provider_reaches_done_and_writes_output(git_repo):
    r, gh_output = _run_entrypoint(
        "run", git_repo,
        {"RIG_TASK": "test task", "RIG_RECIPE": "review-only", "RIG_PROVIDER": "mock"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "final=DONE" in gh_output.read_text(encoding="utf-8")
    state = json.loads((git_repo / "rig-action-state.json").read_text(encoding="utf-8"))
    assert state["done"] is True
    assert "branch" in state["isolation"] and "dir" in state["isolation"]


def test_run_missing_task_env_var_fails_fast(git_repo):
    r, _ = _run_entrypoint("run", git_repo, {"RIG_RECIPE": "review-only"})
    assert r.returncode != 0
    assert "RIG_TASK" in (r.stdout + r.stderr)


def test_run_missing_recipe_env_var_fails_fast(git_repo):
    r, _ = _run_entrypoint("run", git_repo, {"RIG_TASK": "test task"})
    assert r.returncode != 0
    assert "RIG_RECIPE" in (r.stdout + r.stderr)


def test_run_nonexistent_recipe_fails_without_a_state_file(git_repo):
    r, _ = _run_entrypoint(
        "run", git_repo,
        {"RIG_TASK": "test task", "RIG_RECIPE": "no-such-recipe-xyz", "RIG_PROVIDER": "mock"},
    )
    assert r.returncode != 0
    assert not (git_repo / "rig-action-state.json").exists()
    assert "was not created" in (r.stdout + r.stderr)


def test_open_pr_without_a_prior_run_fails_clearly(git_repo):
    r, _ = _run_entrypoint("open-pr", git_repo, {"RIG_TASK": "test task"})
    assert r.returncode != 0
    assert "run the 'run' subcommand first" in (r.stdout + r.stderr)


def test_unknown_subcommand_prints_usage(git_repo):
    r, _ = _run_entrypoint("bogus", git_repo, {})
    assert r.returncode != 0
    assert "usage:" in (r.stdout + r.stderr)
