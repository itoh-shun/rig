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
    # `approvals` (v2.1) sits beside `checks` (a machine's judgment) and `verdicts`
    # (a model's) as the third kind of evidence a step can carry — a person's.
    assert state["step_state"]["a"] == {"status": "pending", "retries": 0,
                                        "checks": [], "verdicts": [], "approvals": []}


def test_save_load_roundtrip(tmp_path, step_factory):
    state = new_state("demo", [step_factory(id="a")], None)
    path = tmp_path / "run-state.json"
    save_state(state, path)
    assert load_state(path) == state


def test_gate_outcome_checks(step_factory):
    """checks[] are a PRECONDITION for a runtime gate's verdict, never a substitute (#496).

    The last assertion used to read `== "pass"` on all-checks-ok with `verdicts: []`. That
    was the deterministic runner asserting that a step named `acceptance-gate` could pass
    with nobody having judged it — the rubber stamp #496 reported, in golden form. Failing
    checks still short-circuit to "fail" before any verdict is consulted, so a step whose
    machine evidence is bad never reaches (or spends a call on) the verifier.
    """
    step = step_factory(id="v", gate="acceptance-gate", checks=["true", "true"])
    st = {"status": "running", "retries": 0, "checks": [], "verdicts": []}
    assert gate_outcome(step, st) == "incomplete"                 # nothing ran yet
    st["checks"] = [{"cmd": "true", "ok": True}]
    assert gate_outcome(step, st) == "incomplete"                 # 1 of 2 ran
    st["checks"] = [{"cmd": "true", "ok": True}, {"cmd": "true", "ok": False}]
    assert gate_outcome(step, st) == "fail"
    st["checks"] = [{"cmd": "true", "ok": True}, {"cmd": "true", "ok": True}]
    assert gate_outcome(step, st) == "incomplete"                 # checks ok, verdict owed
    st["verdicts"] = [{"by": "reviewer", "ok": True, "note": ""}]
    assert gate_outcome(step, st) == "pass"


def test_gate_outcome_requires_a_verdict_to_answer_every_declared_criterion(step_factory):
    """Positive control for the arity rule: a passing verdict has to answer every criterion
    the step declared, not one of them.

    `_judge_output`'s all-UNKNOWN guard cannot hold this line — it reads
    `if ok and criteria and all(UNKNOWN)`, so an empty criteria list skips it entirely, and
    answering one criterion UNKNOWN used to be strictly stricter than answering nothing.
    """
    step = step_factory(id="v", gate="acceptance-gate", checks=["true"],
                        acceptance=["task_intent_satisfied — x", "no_unrelated_diff — y"])
    st = {"status": "running", "retries": 0,
          "checks": [{"cmd": "true", "ok": True}],
          "verdicts": [{"by": "reviewer", "ok": True, "note": ""}]}
    assert gate_outcome(step, st) == "unanswered"
    # Answering one of two is the case the old floor-of-one guard let through.
    st["verdicts"][0]["criteria"] = [{"n": 1, "verdict": "PASS", "anchor": "f.py:1"}]
    assert gate_outcome(step, st) == "unanswered"
    # Answering both is the pass.
    st["verdicts"][0]["criteria"].append({"n": 2, "verdict": "PASS", "anchor": "f.py:2"})
    assert gate_outcome(step, st) == "pass"
    # A step that declares nothing is untouched by the rule (no criteria = nothing owed).
    bare = step_factory(id="v", gate="acceptance-gate", checks=["true"])
    assert gate_outcome(bare, {"status": "running", "retries": 0,
                               "checks": [{"cmd": "true", "ok": True}],
                               "verdicts": [{"by": "reviewer", "ok": True}]}) == "pass"


def test_arity_counts_declared_criteria_answered_not_lines_parsed(step_factory):
    """Thirteen CRITERION lines that index nothing the step declared answer nothing, and a
    criterion answered twice is answered once. Counting parsed lines would pass both."""
    step = step_factory(id="v", gate="acceptance-gate", checks=["true"],
                        acceptance=["a — x", "b — y", "c — z"])
    st = {"status": "running", "retries": 0, "checks": [{"cmd": "true", "ok": True}],
          "verdicts": [{"by": "reviewer", "ok": True, "criteria": [
              {"n": 20, "verdict": "PASS"}, {"n": 21, "verdict": "PASS"},
              {"n": 22, "verdict": "PASS"}]}]}
    assert gate_outcome(step, st) == "unanswered"          # out of range answers nothing
    st["verdicts"][0]["criteria"] = [{"n": 1, "verdict": "PASS"}, {"n": 1, "verdict": "PASS"},
                                     {"n": 2, "verdict": "PASS"}]
    assert gate_outcome(step, st) == "unanswered"          # 3 lines, 2 criteria answered
    st["verdicts"][0]["criteria"].append({"n": 3, "verdict": "PASS"})
    assert gate_outcome(step, st) == "pass"


def test_arity_is_judged_apart_from_the_answers_themselves(step_factory):
    """A verdict that answers all of them and marks some UNKNOWN satisfies arity. Whether
    that is a pass is `_judge_output`'s question — the gate must not conflate the two, or a
    judge could buy its way past arity with UNKNOWN, or be failed for the exact shape the
    contract asked for."""
    step = step_factory(id="v", gate="acceptance-gate", checks=["true"],
                        acceptance=["a — x", "b — y"])
    st = {"status": "running", "retries": 0, "checks": [{"cmd": "true", "ok": True}],
          "verdicts": [{"by": "reviewer", "ok": True, "criteria": [
              {"n": 1, "verdict": "PASS"}, {"n": 2, "verdict": "UNKNOWN"}]}]}
    assert gate_outcome(step, st) == "pass"
    # A verifier whose own judgment was FAIL fails as a judgment, not as arity.
    st["verdicts"][0]["ok"] = False
    assert gate_outcome(step, st) == "fail"


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
    """The `verify` step now needs BOTH its checks and a verdict (#496): the script gained
    a ("verdict", …) between the check and the `next` that advances. The old script — check
    then next — is kept as its own positive control in
    `test_compute_next_awaits_a_verdict_when_only_the_checks_passed`."""
    steps = [step_factory(id="design"),
             step_factory(id="verify", gate="acceptance-gate", checks=["true"]),
             step_factory(id="review", gate="review-gate")]
    state = new_state("t", steps, None)
    trace = _drive(state, [("next", None), ("next", None), ("next", None),
                           ("check", True), ("verdict", ("reviewer", True)),
                           ("next", None), ("next", None),
                           ("verdict", ("reviewer", True)), ("next", None)])
    assert trace == ["START", "ADVANCE", "START", "ADVANCE", "START", "DONE"]
    assert state["done"] is True
    assert all(st["status"] == "passed" for st in state["step_state"].values())
    assert [h["action"] for h in state["history"]].count("PASS") == 3


def test_compute_next_awaits_a_verdict_when_only_the_checks_passed(step_factory):
    """Positive control for the happy path above: the exact old script — run the checks,
    then `next` — must NOT advance an acceptance-gate step any more."""
    steps = [step_factory(id="verify", gate="acceptance-gate", checks=["true"]),
             step_factory(id="review", gate="review-gate")]
    state = new_state("t", steps, None)
    trace = _drive(state, [("next", None), ("check", True), ("next", None)])
    assert trace == ["START", "AWAIT"]
    assert state["step_state"]["verify"]["status"] == "running"
    assert state["cursor"] == 0


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
    # Identity is excluded, and its absence is asserted separately below rather than dropped:
    # `run_id` is deliberately *not* deterministic — two runs of one recipe in the same second
    # must be told apart, which is what its random suffix is for. What this test is about is
    # that the state machine's own output is reproducible, so it compares everything the
    # machine decides and nothing about which run happened to make the decisions.
    assert {k: v for k, v in s1.items() if k != "run_id"} == \
           {k: v for k, v in s2.items() if k != "run_id"}
    assert s1["run_id"] != s2["run_id"]
