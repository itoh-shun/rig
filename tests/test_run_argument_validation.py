"""Run argument errors must stop execution rather than silently drop flags (#599)."""

import json

import pytest


@pytest.fixture
def run_recipe(write_recipe):
    return write_recipe(
        "argument-flow",
        """---
name: argument-flow
steps:
  - id: implement
    instruction: implement
---
""",
    )


@pytest.mark.parametrize("flag", ["--provider-typo", "--unknown=value", "unexpected"])
def test_unknown_run_argument_exits_two_and_lists_valid_flags(
    flag, run_recipe, rig_cli, tmp_path
):
    state = tmp_path / "state.json"
    result = rig_cli("run", run_recipe, "--provider", "mock", "--out", state, flag)

    assert result.returncode == 2
    message = result.stdout + result.stderr
    assert f"unknown run option: {flag}" in message
    assert "Valid flags:" in message
    for valid in ("--provider", "--progress", "--mode", "--generator-executable", "--allow-project-recipes",
                  "--allow-project-manifest", "--allow-project-packs"):
        assert valid in message
    assert not state.exists()


@pytest.mark.parametrize("flag", ["--only", "--from", "--to", "--skip", "--only=implement"])
@pytest.mark.parametrize("strict", [False, True])
def test_slicing_explicitly_names_unsupported_run_and_issue(
    flag, strict, run_recipe, rig_cli, tmp_path
):
    state = tmp_path / "state.json"
    args = ["run", run_recipe, "--provider", "mock", "--out", state]
    if strict:
        args += ["--deterministic", "--isolate"]
    result = rig_cli(*args, flag, *([] if "=" in flag else ["implement"]))

    assert result.returncode == 2
    message = result.stdout + result.stderr
    assert "run does not support slicing yet" in message
    assert "#599" in message
    assert flag in message
    assert not state.exists()


@pytest.mark.parametrize("tail", [["--model"], ["--model", "--isolate"]])
def test_missing_run_option_value_fails_before_execution(
    tail, run_recipe, rig_cli, tmp_path
):
    state = tmp_path / "state.json"
    result = rig_cli("run", run_recipe, "--provider", "mock", "--out", state, *tail)

    assert result.returncode == 2
    assert "--model requires a value" in result.stdout + result.stderr
    assert not state.exists()


def test_real_mock_run_accepts_direct_argv_approval_switches(run_recipe, rig_cli, tmp_path):
    state = tmp_path / "state.json"
    result = rig_cli(
        "run", run_recipe, "--provider", "mock", "--out", state,
        "--allow-project-recipes", "--allow-project-manifest", "--allow-project-packs",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["done"] is True


@pytest.mark.parametrize("goal", ["--progress", "--allow-project-packs", "--only"])
def test_option_looking_goal_remains_data(goal, run_recipe, rig_cli, tmp_path):
    state = tmp_path / "state.json"
    result = rig_cli("run", run_recipe, "--provider", "mock", "--out", state, "--goal", goal)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["goal"] == goal


def test_writing_mode_on_non_japanese_recipe_exits_two(run_recipe, rig_cli, tmp_path):
    state = tmp_path / "state.json"
    result = rig_cli("run", run_recipe, "--provider", "mock", "--out", state, "--mode", "talk,emoji")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "--mode" in result.stdout + result.stderr
    assert not state.exists()
