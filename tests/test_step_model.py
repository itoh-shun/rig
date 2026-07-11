"""Tests for runtime per-step model assignment on `run` (--step-model; issue #293)."""

import json

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.commands import cmd_run
from rig_workbench.orchestrate.providers import run_loop, unknown_step_model_ids
from rig_workbench.orchestrate.runstate import new_state


def _steps(step_factory):
    plan = step_factory(id="plan")
    plan["model"] = "recipe-m"  # recipe frontmatter `model:` equivalent
    return [plan, step_factory(id="implement")]


@pytest.fixture
def tmp_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl")
    return tmp_path / "runs.jsonl"


def test_step_model_precedence(step_factory, tmp_telemetry):
    """runtime --step-model > recipe `model:` > global --model, recorded per step in run-state."""
    state = new_state("t", _steps(step_factory), None)
    final = run_loop(state, None, "mock", "mock",
                     {"model": "global-m", "step_models": {"plan": "runtime-m"}},
                     20, quiet=True)
    assert final == "DONE"
    assert state["step_state"]["plan"]["model"] == "runtime-m"       # runtime beats recipe
    assert state["step_state"]["implement"]["model"] == "global-m"   # global default fallback

    state2 = new_state("t", _steps(step_factory), None)
    run_loop(state2, None, "mock", "mock", {"model": "global-m"}, 20, quiet=True)
    assert state2["step_state"]["plan"]["model"] == "recipe-m"       # recipe beats global


def test_step_model_recorded_in_telemetry(step_factory, tmp_telemetry):
    state = new_state("t", _steps(step_factory), None)
    run_loop(state, None, "mock", "mock",
             {"step_models": {"implement": "runtime-m"}}, 20, quiet=True)
    row = json.loads(tmp_telemetry.read_text(encoding="utf-8").splitlines()[0])
    by_id = {s["id"]: s for s in row["steps"]}
    assert by_id["implement"]["model"] == "runtime-m"
    assert by_id["plan"]["model"] == "recipe-m"


def test_unknown_step_id_aborts_before_execution(step_factory, write_recipe, capsys):
    recipe = write_recipe("flow", "---\nname: flow\nsteps:\n"
                                  "  - id: plan\n    instruction: x\n---\n")
    with pytest.raises(SystemExit) as e:
        cmd_run([str(recipe), "--provider", "mock", "--step-model", "nope=m"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "[ERROR]" in out and "nope" in out
    assert unknown_step_model_ids({"nope": "m"}, [step_factory(id="plan")]) == ["nope"]


def test_malformed_step_model_spec_exits_1(write_recipe, capsys):
    recipe = write_recipe("flow", "---\nname: flow\nsteps:\n"
                                  "  - id: plan\n    instruction: x\n---\n")
    with pytest.raises(SystemExit) as e:
        cmd_run([str(recipe), "--provider", "mock", "--step-model", "plan"])
    assert e.value.code == 1
    assert "[ERROR]" in capsys.readouterr().out
