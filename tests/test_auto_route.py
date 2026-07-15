"""Cost-tier auto-routing based on diff size (issue #264).

resolve_auto_route is a pure function (deterministic candidate selection);
run_loop wires it into --auto-route as a fallback below runtime --step-model
and the recipe's own model:, above the global --model default.
"""

import json

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.providers import run_loop
from rig_workbench.orchestrate.recipes import resolve_auto_route
from rig_workbench.orchestrate.runstate import new_state

CANDIDATES = {"auto_route": {"candidates": [
    {"model": "haiku", "cost_tier": "low", "max_size": "S"},
    {"model": "sonnet", "cost_tier": "medium", "max_size": "L"},
    {"model": "opus", "cost_tier": "high", "max_size": "XL"},
]}}


@pytest.fixture
def tmp_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl")
    # Pin the measured diff size to 0 (-> size class "S") regardless of the ambient repo's
    # actual git status, so these tests don't depend on whether the working tree is dirty.
    from rig_workbench.orchestrate import providers
    monkeypatch.setattr(providers, "git_diff_lines", lambda: 0)
    return tmp_path / "runs.jsonl"


def test_resolve_auto_route_picks_cheapest_covering_candidate():
    assert resolve_auto_route(CANDIDATES, "S")[0] == "haiku"
    assert resolve_auto_route(CANDIDATES, "M")[0] == "sonnet"  # S is too small, next tier up
    assert resolve_auto_route(CANDIDATES, "L")[0] == "sonnet"


def test_resolve_auto_route_falls_back_to_last_candidate_when_none_cover():
    assert resolve_auto_route(CANDIDATES, "XL")[0] == "opus"


def test_resolve_auto_route_is_deterministic():
    assert resolve_auto_route(CANDIDATES, "M") == resolve_auto_route(CANDIDATES, "M")


def test_resolve_auto_route_none_without_declaration():
    model, reason = resolve_auto_route({}, "S")
    assert model is None and "not declared" in reason


def _routed_step(step_factory, **overrides):
    step = step_factory(id="implement")
    step["auto_route"] = CANDIDATES["auto_route"]
    step.update(overrides)
    return step


def test_auto_route_only_applies_when_flag_set(step_factory, tmp_telemetry):
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"model": "global-m"}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "global-m"  # --auto-route not requested


def test_auto_route_is_a_fallback_below_recipe_model(step_factory, tmp_telemetry):
    step = _routed_step(step_factory, model="recipe-m")
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"auto_route": True}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "recipe-m"  # explicit model: wins


def test_auto_route_is_a_fallback_below_runtime_step_model(step_factory, tmp_telemetry):
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock",
             {"auto_route": True, "step_models": {"implement": "runtime-m"}}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "runtime-m"  # --step-model wins


def test_auto_route_applies_when_nothing_else_set(step_factory, tmp_telemetry):
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"auto_route": True}, 20, quiet=True)
    # git_diff_lines() is pinned to 0 by tmp_telemetry -> size class "S"
    assert state["step_state"]["implement"]["model"] == "haiku"


def test_auto_route_decision_recorded_in_telemetry(step_factory, tmp_telemetry):
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"auto_route": True}, 20, quiet=True)
    row = json.loads(tmp_telemetry.read_text(encoding="utf-8").splitlines()[0])
    by_id = {s["id"]: s for s in row["steps"]}
    assert by_id["implement"]["auto_route"]["model"] == "haiku"
    assert "reason" in by_id["implement"]["auto_route"]
