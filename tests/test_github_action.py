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
import shutil
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
    assert step_ids == ["gh", "run", "pr"]
    # gh-stack only advises rig, it never gates it, so hosted runners get the
    # extension installed for stacked-PR publishing and everyone else carries on.
    # Authentication is not part of the requirement: the token is only for
    # fetching the extension release and for the PR step.
    gh_step, run_step = spec["runs"]["steps"][0], spec["runs"]["steps"][1]
    assert "gh extension install github/gh-stack" in gh_step["run"]
    assert "GH_TOKEN" in gh_step["env"]
    assert "GH_TOKEN" not in run_step["env"]


# ── the optional gh-stack step must never fail the action ───────────────────
# Composite steps have no continue-on-error and run under `bash -eo pipefail`,
# so the tolerance has to be in the shell. Running the step body for real (with
# a stub `gh`) is what proves it — grepping the YAML for `||` would not.


def _gh_step_script() -> str:
    spec = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    return spec["runs"]["steps"][0]["run"]


def _run_gh_step(tmp_path, path_value):
    script = tmp_path / "step.sh"
    script.write_text(_gh_step_script(), encoding="utf-8")
    # bash by absolute path: the no-gh case deliberately hands the step an empty
    # PATH, which would otherwise make the interpreter itself unfindable.
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-eo", "pipefail", str(script)],
        cwd=tmp_path, capture_output=True, text=True,
        env={"PATH": path_value, "GH_TOKEN": "x"},
    )


def _stub_gh(tmp_path, exit_code):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "$(dirname "$0")/calls.log"\nexit {exit_code}\n',
                  encoding="utf-8")
    gh.chmod(0o755)
    return bin_dir


def test_gh_step_survives_a_failing_extension_install(tmp_path):
    """Auth failure, rate limiting, a flaky network — the exit code Codex
    reproduced (23) must not stop the action before the rig task runs."""
    bin_dir = _stub_gh(tmp_path, 23)
    result = _run_gh_step(tmp_path, f"{bin_dir}:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    assert "continuing without it" in result.stdout
    # …and it did genuinely attempt the install rather than skipping it.
    assert "extension install github/gh-stack" in (bin_dir / "calls.log").read_text(
        encoding="utf-8")


def test_gh_step_survives_a_runner_without_gh(tmp_path):
    """Self-hosted runners do not ship `gh` at all."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run_gh_step(tmp_path, str(empty))
    assert result.returncode == 0, result.stderr
    assert "gh is not installed" in result.stdout


def test_gh_step_still_installs_when_gh_works(tmp_path):
    bin_dir = _stub_gh(tmp_path, 0)
    result = _run_gh_step(tmp_path, f"{bin_dir}:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
    assert "continuing without it" not in result.stdout
    assert "extension install github/gh-stack --force" in (bin_dir / "calls.log").read_text(
        encoding="utf-8")


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
