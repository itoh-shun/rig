"""Learned auto-router from historical run data (issue #305).

learned_auto_route() aggregates runs.jsonl's track record (model actually
used + step pass/fail) and picks the cheapest static auto_route candidate
meeting a pass-rate/sample-size bar. Wired into run_loop as shadow-mode-by-
default: predictions are always recorded, but only override the applied
model under --auto-route-mode active.
"""

import json

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.providers import run_loop
from rig_workbench.orchestrate.recipes import learned_auto_route
from rig_workbench.orchestrate.runstate import new_state

CANDIDATES = [
    {"model": "haiku", "cost_tier": "low", "max_size": "S"},
    {"model": "sonnet", "cost_tier": "medium", "max_size": "L"},
]


def _run(model, step_id, ok, recipe="lr-recipe"):
    return {"recipe": recipe, "steps": [{"id": step_id, "status": "passed" if ok else "failed",
                                          "model": model}]}


@pytest.fixture
def tmp_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global-runs.jsonl")
    # Pin the measured diff size to 0 (-> size class "S") regardless of the ambient repo's
    # actual git status, so these tests don't depend on whether the working tree is dirty.
    from rig_workbench.orchestrate import providers
    monkeypatch.setattr(providers, "git_diff_lines", lambda: 0)
    return tmp_path / "runs.jsonl"


# ---- pure-function coverage -------------------------------------------------

def test_insufficient_sample_is_not_confident():
    rows = [_run("haiku", "implement", True)] * 2  # < 3 samples
    r = learned_auto_route("lr-recipe", "implement", CANDIDATES, rows)
    assert r["sufficient"] is False
    assert any("insufficient sample" in c["rejected_reason"] for c in r["counterfactuals"])


def test_sufficient_and_high_pass_rate_picks_cheapest():
    rows = [_run("haiku", "implement", True)] * 4 + [_run("haiku", "implement", False)]  # 4/5
    r = learned_auto_route("lr-recipe", "implement", CANDIDATES, rows)
    assert r["model"] == "haiku"


def test_same_input_is_deterministic():
    rows = [_run("haiku", "implement", True)] * 4 + [_run("haiku", "implement", False)]
    assert (learned_auto_route("lr-recipe", "implement", CANDIDATES, rows)
            == learned_auto_route("lr-recipe", "implement", CANDIDATES, rows))


def test_low_pass_rate_falls_back_to_next_candidate():
    rows = ([_run("haiku", "implement", False)] * 4 + [_run("haiku", "implement", True)]  # 1/5
            + [_run("sonnet", "implement", True)] * 3)  # 3/3
    r = learned_auto_route("lr-recipe", "implement", CANDIDATES, rows)
    assert r["model"] == "sonnet"
    assert any(c["model"] == "haiku" and "insufficient pass rate" in c["rejected_reason"]
               for c in r["counterfactuals"])


def test_exploration_pct_100_always_explores_deterministically():
    rows = [_run("haiku", "implement", True)] * 4 + [_run("haiku", "implement", False)]
    r = learned_auto_route("lr-recipe", "implement", CANDIDATES, rows,
                           exploration_key="fixed-key", exploration_pct=100)
    assert r["model"] == "sonnet"
    assert r["explored_from"] == "haiku"


def test_exploration_pct_0_never_explores():
    rows = [_run("haiku", "implement", True)] * 4 + [_run("haiku", "implement", False)]
    r = learned_auto_route("lr-recipe", "implement", CANDIDATES, rows,
                           exploration_key="fixed-key", exploration_pct=0)
    assert r["explored_from"] is None


# ---- wiring into run_loop ----------------------------------------------------

def _routed_step(step_factory, **overrides):
    step = step_factory(id="implement")
    step["auto_route"] = {"candidates": CANDIDATES}
    step.update(overrides)
    return step


def test_shadow_mode_records_but_does_not_apply(step_factory, tmp_telemetry):
    # Seed history with 4/4 sonnet passes for a step whose static auto-route (size S, no
    # git diff in a test env) would pick haiku.
    tmp_telemetry.write_text(
        "\n".join(json.dumps(_run("sonnet", "implement", True, recipe="t")) for _ in range(4)) + "\n",
        encoding="utf-8",
    )
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"auto_route": True, "auto_route_learn": True}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "haiku"  # shadow: static choice still applied
    prediction = next(h for h in state["history"] if h["action"] == "LEARNED_ROUTE_PREDICTION")
    assert prediction["predicted_model"] == "sonnet"
    assert prediction["applied"] is False


def test_active_mode_applies_the_learned_prediction(step_factory, tmp_telemetry):
    tmp_telemetry.write_text(
        "\n".join(json.dumps(_run("sonnet", "implement", True, recipe="t")) for _ in range(4)) + "\n",
        encoding="utf-8",
    )
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock",
             {"auto_route": True, "auto_route_learn": True, "auto_route_mode": "active"}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "sonnet"
    prediction = next(h for h in state["history"] if h["action"] == "LEARNED_ROUTE_PREDICTION")
    assert prediction["applied"] is True


def test_insufficient_data_falls_back_to_static_even_in_active_mode(step_factory, tmp_telemetry):
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock",
             {"auto_route": True, "auto_route_learn": True, "auto_route_mode": "active"}, 20, quiet=True)
    assert state["step_state"]["implement"]["model"] == "haiku"  # no data yet -> static #264 fallback


def test_learned_route_recorded_in_telemetry(step_factory, tmp_telemetry):
    tmp_telemetry.write_text(
        "\n".join(json.dumps(_run("sonnet", "implement", True, recipe="t")) for _ in range(4)) + "\n",
        encoding="utf-8",
    )
    step = _routed_step(step_factory)
    state = new_state("t", [step], None)
    run_loop(state, None, "mock", "mock", {"auto_route": True, "auto_route_learn": True}, 20, quiet=True)
    rows = [json.loads(line) for line in tmp_telemetry.read_text(encoding="utf-8").splitlines()]
    row = rows[-1]  # the run just executed (appended after the 4 seeded rows)
    by_id = {s["id"]: s for s in row["steps"]}
    assert by_id["implement"]["learned_route"]["predicted_model"] == "sonnet"
