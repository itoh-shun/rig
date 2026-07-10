"""Unit tests for rig_workbench.orchestrate.runstate (state machine, gate evaluation)."""

import pytest

from rig_workbench.orchestrate.runstate import (compute_next, gate_outcome,
                                                load_state, new_state, save_state)


def test_new_state_shape(step_factory):
    steps = [step_factory(id="a"), step_factory(id="b", gate="review-gate")]
    state = new_state("demo", steps, "goal text")
    assert state["recipe"] == "demo"
    assert state["goal"] == "goal text"
    assert state["cursor"] == 0
    assert state["done"] is False
    assert state["stopped"] is None
    assert set(state["step_state"]) == {"a", "b"}
    assert state["step_state"]["a"] == {"status": "pending", "retries": 0,
                                        "checks": [], "verdicts": []}


def test_save_load_roundtrip(tmp_path, step_factory):
    state = new_state("demo", [step_factory(id="a")], None)
    path = tmp_path / "run-state.json"
    save_state(state, path)
    assert load_state(path) == state


def test_gate_outcome_checks(step_factory):
    step = step_factory(id="v", gate="acceptance-gate", checks=["true", "true"])
    st = {"status": "running", "retries": 0, "checks": [], "verdicts": []}
    assert gate_outcome(step, st) == "incomplete"                 # nothing ran yet
    st["checks"] = [{"cmd": "true", "ok": True}]
    assert gate_outcome(step, st) == "incomplete"                 # 1 of 2 ran
    st["checks"] = [{"cmd": "true", "ok": True}, {"cmd": "true", "ok": False}]
    assert gate_outcome(step, st) == "fail"
    st["checks"] = [{"cmd": "true", "ok": True}, {"cmd": "true", "ok": True}]
    assert gate_outcome(step, st) == "pass"


def test_gate_outcome_verdicts(step_factory):
    step = step_factory(id="r", gate="review-gate")
    st = {"status": "running", "retries": 0, "checks": [], "verdicts": []}
    assert gate_outcome(step, st) == "incomplete"                 # awaiting verdict
    st["verdicts"] = [{"by": "reviewer", "ok": True, "note": ""}]
    assert gate_outcome(step, st) == "pass"
    st["verdicts"] = [{"by": "reviewer", "ok": False, "note": ""}]
    assert gate_outcome(step, st) == "fail"


@pytest.mark.parametrize("by", ["self", "generator", "producer", ""])
def test_gate_outcome_self_grading_blocked(step_factory, by):
    step = step_factory(id="r", gate="review-gate")
    st = {"status": "running", "retries": 0, "checks": [],
          "verdicts": [{"by": by, "ok": True, "note": ""}]}
    assert gate_outcome(step, st) == "self-graded"


def test_gate_outcome_no_gate_passes(step_factory):
    step = step_factory(id="free")
    st = {"status": "running", "retries": 0, "checks": [], "verdicts": []}
    assert gate_outcome(step, st) == "pass"


def _drive(state, script):
    """Advance the state machine; return the sequence of action codes."""
    trace = []
    for kind, payload in script:
        if kind == "next":
            action, _msg = compute_next(state)
            trace.append(action)
        elif kind == "check":
            step = state["steps"][state["cursor"]]
            st = state["step_state"][step["id"]]
            st["checks"] = [{"cmd": c, "ok": payload} for c in step["checks"]]
        elif kind == "verdict":
            step = state["steps"][state["cursor"]]
            state["step_state"][step["id"]]["verdicts"].append(
                {"by": payload[0], "ok": payload[1], "note": ""})
    return trace


def test_compute_next_happy_path(step_factory):
    steps = [step_factory(id="design"),
             step_factory(id="verify", gate="acceptance-gate", checks=["true"]),
             step_factory(id="review", gate="review-gate")]
    state = new_state("t", steps, None)
    trace = _drive(state, [("next", None), ("next", None), ("next", None),
                           ("check", True), ("next", None), ("next", None),
                           ("verdict", ("reviewer", True)), ("next", None)])
    assert trace == ["START", "ADVANCE", "START", "ADVANCE", "START", "DONE"]
    assert state["done"] is True
    assert all(st["status"] == "passed" for st in state["step_state"].values())
    assert [h["action"] for h in state["history"]].count("PASS") == 3


def test_compute_next_await_before_gate(step_factory):
    state = new_state("t", [step_factory(id="v", gate="acceptance-gate", checks=["true"])], None)
    trace = _drive(state, [("next", None), ("next", None)])  # next without running checks
    assert trace == ["START", "AWAIT"]
    assert state["step_state"]["v"]["status"] == "running"


def test_compute_next_retry_then_escalate(step_factory):
    state = new_state("t", [step_factory(id="v", gate="acceptance-gate",
                                         checks=["false"], max_retries=2)], None)
    trace = _drive(state, [("next", None), ("check", False), ("next", None),
                           ("next", None), ("check", False), ("next", None)])
    assert trace == ["START", "RETRY", "START", "ESCALATE"]
    assert state["stopped"] is not None and state["stopped"]["at"] == "v"
    assert state["done"] is False
    # a retry resets the step's evidence
    assert state["step_state"]["v"]["retries"] == 2
    # once stopped, further next calls are inert
    action, _ = compute_next(state)
    assert action == "STOPPED"


def test_compute_next_blocks_self_grading(step_factory):
    state = new_state("t", [step_factory(id="r", gate="review-gate")], None)
    trace = _drive(state, [("next", None), ("verdict", ("self", True)), ("next", None)])
    assert trace == ["START", "BLOCKED"]
    assert state["step_state"]["r"]["status"] == "running"  # not passed


def test_compute_next_deterministic(step_factory):
    def run():
        steps = [step_factory(id="a"), step_factory(id="b", gate="review-gate")]
        state = new_state("t", steps, None)
        trace = _drive(state, [("next", None), ("next", None), ("next", None),
                               ("verdict", ("ver", True)), ("next", None)])
        return trace, state

    t1, s1 = run()
    t2, s2 = run()
    assert t1 == t2 == ["START", "ADVANCE", "START", "DONE"]
    assert s1 == s2
