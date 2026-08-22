"""#431 — an autonomous developer may decide how, not whether its result is trustworthy.

The tests are grouped by what they hold: the record is a closed schema, the stop
judgement is computed and not asked for, a developer's PASS cannot be written where a
gate's belongs, and a handoff names a fixed object or is refused.
"""

import dataclasses
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import development_loop
from rig_workbench.workbench.development_loop import (BLOCKED, MAX_CYCLES, NO_PROGRESS,
                                                     TARGET_NOT_IMMUTABLE,
                                                      READY_FOR_ASSURANCE,
                                                      REPEATED_FAILURE, SCHEMA, Cycle,
                                                      Limits, handoff, load, must_stop,
                                                      validate)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

def _oid(seed):
    """A git object id built from a label, so a test can say which object it means."""
    return (str(seed).encode().hex() * 40)[:40]


HEAD = "a" * 40
TASK = "rig-20260822-000000-a-task"
GOAL = "ship the thing"
BASE = "b" * 40


def _receipt(immutable=True, commit=HEAD, task=TASK, head=None, goal=GOAL, base=BASE):
    """A receipt shaped like `assurance.build_receipt`'s, with only what this module reads."""
    return {"task": {"id": task, "input": goal},
            "target": {"immutable": immutable, "base_commit": base,
                       "head": {"observed": True, "commit": commit, "resolvable": immutable}
                       if head is None else head}}


def _history(within=None, advances=None):
    """A history a test can state: everything is inside the range and each commit follows the
    last, unless the test says otherwise."""
    return development_loop.History(within=within or (lambda oid: True),
                                    advances=advances or (lambda a, b: True))


IMMUTABLE = _receipt()
UNRESOLVABLE = _receipt(immutable=False, commit="e" * 40)
NO_COMMIT = _receipt(immutable=False, head={"observed": False,
                                            "reason": "no commit is linked to this task"})


def _cycle(index, state="implement", product=None, failure=None, **extra):
    return {"index": index, "state": state,
            "product": _oid(product) if product else _oid(f"tree-{index}"),
            "failure": failure, "rationale": "", "producer": "", **extra}


def _log(*cycles, goal=GOAL, task=TASK, **extra):
    return {"schema": SCHEMA, "task": task, "goal": goal, "cycles": list(cycles), **extra}


def _done(*cycles, **extra):
    """A log a handoff could accept: the loop says it is finished and its last cycle produced
    the commit the receipt points at."""
    cycles = list(cycles) or [_cycle(0)]
    cycles[-1] = dict(cycles[-1], product=HEAD)
    return _log(*cycles, outcome=READY_FOR_ASSURANCE, **extra)


# ── the record is closed ─────────────────────────────────────────────────────
def test_a_valid_log_has_no_problems():
    assert validate(_log(_cycle(0), _cycle(1, state="test"))) == []


def test_a_loop_that_ran_nothing_is_refused():
    """A stop judgement over an empty record reports no failures, which reads as success."""
    assert any("ran nothing" in p for p in validate(_log()))


def test_a_cycle_may_not_claim_what_the_loop_concluded():
    """`ready-for-assurance` and `blocked` are verdicts about the loop. A cycle carrying one
    would put the loop's own conclusion into the record of its work, where the stop judgement
    reads it back as evidence."""
    for outcome in (READY_FOR_ASSURANCE, BLOCKED):
        problems = validate(_log(_cycle(0, state=outcome)))
        assert any("not work a cycle did" in p for p in problems), outcome


def test_a_cycle_without_a_product_is_refused():
    """Whether the loop is making progress is a question about what changed. A cycle that
    cannot identify what it produced can only be taken at its word — which is the thing a
    stuck loop is wrong about."""
    # `"a" * 41` is the one a prefix match would let through: 40 valid characters with
    # something after them is not an object id, it is an object id and a suffix.
    for bad in ("  ", "not-an-oid", "a" * 39, "a" * 41, "A" * 40, 40, None):
        problems = validate(_log(dict(_cycle(0), product=bad)))
        assert any("not a git object id" in p for p in problems), repr(bad)


def test_a_blank_failure_is_refused_and_an_absent_one_is_not():
    """Absent means the cycle did not fail. Blank means it failed in a way nothing can
    compare to the last one, which is exactly what repeated-failure detection needs."""
    assert validate(_log(_cycle(0, failure=None))) == []
    assert any("blank" in p for p in validate(_log(_cycle(0, failure="   "))))


def test_cycles_must_say_where_they_sit():
    problems = validate(_log(_cycle(0), _cycle(5)))
    assert any("index is 5" in p for p in problems), problems


def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped():
    assert any("'mode'" in p for p in validate(_log(_cycle(0, mode="quick"))))
    assert any("'waivable'" in p for p in validate(dict(_log(_cycle(0)), waivable=True)))


def test_the_accepted_keys_are_the_ones_a_cycle_actually_has():
    assert development_loop.CYCLE_FIELDS == {f.name for f in dataclasses.fields(Cycle)}


# ── a developer's PASS is not a gate's ───────────────────────────────────────
@pytest.mark.parametrize("key", ["gate", "gates", "accepted", "accept", "verdict",
                                 "assurance", "approved", "final_status"])
def test_the_loop_cannot_write_its_verdict_where_a_gates_belongs(key):
    """The loop's `tests passed` is the developer's account of its own work. A schema with a
    field for a verdict is an invitation to write one there, and a later reader cannot tell a
    gate's answer from the account of the thing being judged."""
    problems = validate(_log(_cycle(0), self_reported={key: "passed"}))
    assert any("where a gate's belongs" in p for p in problems), (key, problems)


def test_the_loops_own_account_is_allowed_under_its_own_name():
    assert validate(_log(_cycle(0), self_reported={"tests": "passed", "review": "passed",
                                                   "note": "two flaky reruns"})) == []


def test_the_account_is_carried_through_the_handoff_without_becoming_the_verdict():
    result = handoff(_done(_cycle(0), self_reported={"tests": "passed"}), IMMUTABLE)
    assert result["status"] == development_loop.ADMISSIBLE
    assert result["self_reported"] == {"tests": "passed"}
    assert "tests" not in result["reasons"] and "self_reported" not in result["target"]


def test_a_loop_claiming_success_is_still_refused_when_the_record_says_otherwise():
    """The whole point. `tests: passed` alongside three identical failures is the loop
    reporting on itself, and the record is what decides."""
    stuck = [dict(_cycle(i, failure="test_login"), product=HEAD) for i in range(3)]
    result = handoff(_log(*stuck, outcome=READY_FOR_ASSURANCE,
                          self_reported={"tests": "passed", "review": "passed"}), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert {r["reason"] for r in result["reasons"]} == {REPEATED_FAILURE, NO_PROGRESS}


# ── the stop judgement is computed, never asked for ──────────────────────────
def test_a_loop_within_its_bounds_may_keep_going():
    cycles = load(_log(_cycle(0, failure="a"), _cycle(1, failure="b"), _cycle(2)))
    assert must_stop(cycles) == []


def test_the_cycle_limit_stops_the_loop():
    cycles = load(_log(*[_cycle(i) for i in range(4)]))
    assert [r["reason"] for r in must_stop(cycles, Limits(max_cycles=4))] == [MAX_CYCLES]
    assert must_stop(cycles, Limits(max_cycles=5)) == []


def test_the_same_failure_coming_back_stops_the_loop():
    """A repair that keeps meeting the same failure is not repairing it."""
    cycles = load(_log(_cycle(0, failure="test_login"), _cycle(1, failure="test_logout"),
                       _cycle(2, failure="test_login"), _cycle(3, failure="test_login")))
    assert [r["reason"] for r in must_stop(cycles, Limits(repeated_failure=2))] == [
        REPEATED_FAILURE]


def test_a_failure_seen_again_after_a_different_one_is_not_a_run():
    """Consecutive, not cumulative: a failure seen twice with a different one in between is a
    loop working through several problems, which is what it is for."""
    cycles = load(_log(_cycle(0, failure="a"), _cycle(1, failure="b"), _cycle(2, failure="a")))
    assert must_stop(cycles, Limits(repeated_failure=2)) == []


def test_a_later_cycle_does_not_undo_having_run_past_the_bound():
    """The move the bound exists to prevent. With a limit of 2, `a, a, b` is a loop that was
    required to stop after the second cycle and took a third — and reading only the trailing
    run would let it clear the evidence by continuing."""
    cycles = load(_log(_cycle(0, failure="a"), _cycle(1, failure="a"), _cycle(2, failure="b")))
    reasons = must_stop(cycles, Limits(repeated_failure=2))
    assert [r["reason"] for r in reasons] == [REPEATED_FAILURE]
    # The run's failure, not the last cycle's: naming `b` would report the wrong problem and
    # send a reader looking at the cycle that ended the run rather than the ones that were it.
    assert "'a'" in reasons[0]["detail"], reasons[0]["detail"]

    # The reviewer's example, and the sharper one: a loop that ran past the bound and then
    # succeeded once. Reading the trailing run finds no failure at all.
    recovered = load(_log(_cycle(0, failure="a"), _cycle(1, failure="a"), _cycle(2)))
    assert [r["reason"] for r in must_stop(recovered, Limits(repeated_failure=2))] == [
        REPEATED_FAILURE]

    stalled = load(_log(_cycle(0, product="p"), _cycle(1, product="p"), _cycle(2)))
    assert [r["reason"] for r in must_stop(stalled, Limits(no_progress=2))] == [NO_PROGRESS]


def test_nothing_changing_stops_the_loop_however_busy_it_was():
    """`product` is what makes this a fact rather than an impression. Three cycles that ended
    with the same tree changed nothing, whatever happened in between."""
    cycles = load(_log(_cycle(0, product="tree-a"), _cycle(1, product="same", state="repair"),
                       _cycle(2, product="same", state="test"),
                       _cycle(3, product="same", state="repair")))
    assert [r["reason"] for r in must_stop(cycles, Limits(no_progress=3))] == [NO_PROGRESS]


def test_a_loop_that_never_stalled_for_long_enough_may_keep_going():
    """The bound is the caller's to set: two cycles at the same commit is not a stall when the
    limit is three."""
    cycles = load(_log(_cycle(0, product="same"), _cycle(1, product="same"),
                       _cycle(2, product="moved")))
    assert must_stop(cycles, Limits(no_progress=3)) == []


def test_every_reason_is_reported_not_the_first():
    """A loop told only "max cycles" would raise the limit and hit the repeated failure it was
    always going to hit."""
    cycles = load(_log(*[_cycle(i, product="same", failure="test_login") for i in range(3)]))
    assert {r["reason"] for r in must_stop(cycles, Limits(max_cycles=3, repeated_failure=3,
                                                         no_progress=3))} == {
        MAX_CYCLES, REPEATED_FAILURE, NO_PROGRESS}


def test_a_caller_that_said_nothing_about_bounds_still_gets_bounds():
    """Making the caller opt in to being bounded gets the unbounded-loop failure mode wrong
    by omission."""
    assert must_stop(load(_log(*[_cycle(i) for i in range(12)]))) != []
    assert Limits().max_cycles == 12


@pytest.mark.parametrize("field", ["max_cycles", "repeated_failure", "no_progress"])
@pytest.mark.parametrize("value", [0, -1, True, False, 2.5, "3", None])
def test_a_bound_that_would_not_bound_anything_is_refused(field, value):
    """`True` is an `int` in Python and would set a limit of 1 while reading as "enabled"; a
    limit below 1 disables the bound while looking like a setting."""
    with pytest.raises(ValueError, match="unbounded loop wearing a limit's name"):
        Limits(**{field: value})


# ── a handoff names a fixed object, or it is refused ─────────────────────────
def test_a_converging_loop_pointing_at_a_commit_is_admissible():
    result = handoff(_done(_cycle(0), _cycle(1, state="test"), _cycle(2, state="review")),
                     IMMUTABLE)
    assert result["status"] == development_loop.ADMISSIBLE
    assert result["reasons"] == []
    assert result["target"] == {"commit": "a" * 40, "immutable": True}


@pytest.mark.parametrize("target,fragment", [
    (UNRESOLVABLE, "cannot be resolved"),
    (NO_COMMIT, "no commit is linked"),
])
def test_a_handoff_that_cannot_name_a_fixed_object_is_refused(target, fragment):
    """A verifier's verdict is about whatever it looked at. If that can change afterwards,
    the verdict is about nothing in particular."""
    result = handoff(_done(_cycle(0)), target)
    assert result["status"] == development_loop.REFUSED
    detail = next(r["detail"] for r in result["reasons"] if r["reason"] == TARGET_NOT_IMMUTABLE)
    assert fragment in detail, detail


def test_reaching_the_cycle_limit_does_not_make_the_work_unhandable():
    """Spending the budget is a reason to stop, not a reason to throw the work away — it still
    exists and something else still has to judge it. The other two bounds say the loop was not
    converging, which is a different claim."""
    result = handoff(_done(*[_cycle(i) for i in range(4)]), IMMUTABLE, Limits(max_cycles=4))
    assert result["status"] == development_loop.ADMISSIBLE, result["reasons"]
    assert must_stop(load(_log(*[_cycle(i) for i in range(4)])), Limits(max_cycles=4)) != []


@pytest.mark.parametrize("bound,cycles", [
    (Limits(repeated_failure=2), [_cycle(0, failure="x"), _cycle(1, failure="x")]),
    (Limits(no_progress=2), [dict(_cycle(0), product=HEAD), dict(_cycle(1), product=HEAD)]),
])
def test_a_loop_that_was_not_converging_may_not_hand_over(bound, cycles):
    result = handoff(_done(*cycles), IMMUTABLE, bound)
    assert result["status"] == development_loop.REFUSED, result


@pytest.mark.parametrize("escalation", development_loop.ESCALATIONS)
def test_an_escalated_loop_may_not_route_around_the_human(escalation):
    result = handoff(_done(_cycle(0), escalation=escalation), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.ESCALATION_REQUIRED for r in result["reasons"])


def test_an_escalation_nobody_defined_is_refused():
    """A loop that escalates without saying which case it hit hands the human the same problem
    it had."""
    assert any("is not one of" in p
               for p in validate(_log(_cycle(0), escalation="felt uneasy")))


def test_an_invalid_record_is_refused_without_being_judged():
    """A record that cannot be read cannot be judged converging or not, and answering either
    would be inventing a verdict about a document nobody can parse."""
    result = handoff({"schema": "wrong", "cycles": []}, IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert {r["reason"] for r in result["reasons"]} == {"invalid-record"}
    assert result["target"] is None


# ── it does not run the loop ─────────────────────────────────────────────────
def test_the_judgement_touches_nothing_and_calls_no_model():
    """The loop's decisions — what to research, how to repair — are reading, judging and
    concluding. A module that did them would leave nothing a gate could check and nothing a
    mutation could falsify.

    `git_resolver` is the one exception and the reason this is written as a walk rather than a
    denylist of imports: everything that judges takes what it needs as an argument, so a test
    can state a repository instead of needing one. If a judging function grows a way to reach
    for something, this fails.
    """
    import ast
    tree = ast.parse((REPO_ROOT / "rig_workbench" / "workbench"
                      / "development_loop.py").read_text(encoding="utf-8"))
    reaching = {"subprocess", "socket", "http", "urllib", "requests", "os", "pathlib", "open"}
    judging = {"validate", "load", "must_stop", "handoff", "_trailing_repeats"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in judging):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.Import) for a in n.names}
        names |= {(n.module or "").split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.ImportFrom)}
        assert not names & reaching, (node.name, sorted(names & reaching))

    module_level = {a.name.split(".")[0] for n in tree.body
                    if isinstance(n, ast.Import) for a in n.names}
    module_level |= {(n.module or "").split(".")[0] for n in tree.body
                     if isinstance(n, ast.ImportFrom)}
    assert not module_level & reaching, module_level


# ── the command exits with the answer ────────────────────────────────────────
class _Args:
    def __init__(self, cycles, **flags):
        self.task, self.cycles, self.json = "t", str(cycles), False
        self.max_cycles = self.repeated_failure = self.no_progress = None
        for key, value in flags.items():
            setattr(self, key, value)


def _invoke(tmp_path, payload, target=IMMUTABLE, monkeypatch=None, text=None, **flags):
    """Run the subcommand in-process against a stubbed receipt.

    The receipt is stubbed rather than built from a real task, for the reason
    `test_assurance_target` gives: a test that skips when it cannot find one kills no
    mutation, and what is under test is the exit code, not `build_receipt`.
    """
    import rig_workbench.workbench.assurance as assurance_module
    import rig_workbench.workbench.state as state_module

    monkeypatch.setattr(assurance_module, "build_receipt", lambda root, task_id: target)
    # A repository that holds whatever the record names, so the CLI tests are about the exit
    # code. What the resolver changes is covered directly against `must_stop`.
    monkeypatch.setattr(development_loop, "git_history",
                        lambda root, base, head: _history())
    monkeypatch.setattr(state_module, "repo_root", lambda: REPO_ROOT)
    monkeypatch.setattr(state_module, "resolve_task_id", lambda root, task: "t")

    path = tmp_path / "cycles.json"
    path.write_text(text if text is not None else json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_code:
        development_loop.cmd_dev_loop(_Args(path, **flags))
    return exit_code.value.code


def test_an_admissible_handoff_exits_zero(tmp_path, monkeypatch):
    assert _invoke(tmp_path, _done(_cycle(0)), monkeypatch=monkeypatch) == 0


def test_a_refused_handoff_exits_nonzero(tmp_path, monkeypatch):
    """Refusing and exiting 0 would tell the shell the loop produced something a verifier can
    be pointed at."""
    assert _invoke(tmp_path, _done(_cycle(0)), target=NO_COMMIT, monkeypatch=monkeypatch) == 1


def test_an_invalid_record_exits_nonzero_rather_than_erroring(tmp_path, monkeypatch):
    """Exit 2 says the command could not run. A record that parsed and is wrong is the loop's
    problem, not the caller's."""
    assert _invoke(tmp_path, {"schema": "wrong", "cycles": []}, monkeypatch=monkeypatch) == 1


def test_the_bounds_are_the_callers_to_set(tmp_path, monkeypatch):
    payload = _done(_cycle(0, failure="x"), _cycle(1, failure="x"))
    assert _invoke(tmp_path, payload, monkeypatch=monkeypatch) == 0
    assert _invoke(tmp_path, payload, monkeypatch=monkeypatch, repeated_failure=2) == 1


def test_a_bound_that_would_not_bound_anything_is_an_execution_error(tmp_path, monkeypatch):
    assert _invoke(tmp_path, _done(_cycle(0)), monkeypatch=monkeypatch, max_cycles=0) == 2


def test_a_record_naming_one_key_twice_is_refused(tmp_path, monkeypatch):
    """JSON allows a key twice and `json.loads` keeps the last one silently. A cycle whose
    `product` appears twice reaches the no-progress comparison saying only the last one."""
    text = ('{"schema": "%s", "task": "t", "goal": "g", "cycles": [{"index": 0, "state": "implement", '
            '"product": "%s", "product": "%s"}]}' % (SCHEMA, "c" * 40, "d" * 40))
    assert _invoke(tmp_path, None, monkeypatch=monkeypatch, text=text) == 2


def test_a_receipt_with_no_base_leaves_the_command_with_the_weaker_answer(tmp_path,
                                                                           monkeypatch):
    """Without both ends there is no range, and building a history from one of them would ask
    a question with no lower bound — the defect round 4 found. The answer says which one it
    got rather than pretending."""
    import rig_workbench.workbench.assurance as assurance_module
    import rig_workbench.workbench.state as state_module

    receipt = _receipt(base=None)
    receipt["target"].pop("base_commit")
    monkeypatch.setattr(assurance_module, "build_receipt", lambda root, task_id: receipt)
    monkeypatch.setattr(state_module, "repo_root", lambda: REPO_ROOT)
    monkeypatch.setattr(state_module, "resolve_task_id", lambda root, task: TASK)
    monkeypatch.setattr(development_loop, "git_history",
                        lambda root, base, head: pytest.fail("a history was built with no base"))

    path = tmp_path / "cycles.json"
    path.write_text(json.dumps(_done(_cycle(0))), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_code:
        development_loop.cmd_dev_loop(_Args(path))
    assert exit_code.value.code == 0


def test_the_range_starts_where_the_delivered_work_does_after_a_rebase(tmp_path, monkeypatch):
    """Which base is not a detail. After a rebase the originally registered one is not in the
    delivered history at all, so a range starting there would put every product outside it."""
    import rig_workbench.workbench.assurance as assurance_module
    import rig_workbench.workbench.state as state_module

    receipt = _receipt()
    receipt["target"]["base_commit"] = "9" * 40
    receipt["target"]["base_commit_effective"] = BASE
    asked = []

    monkeypatch.setattr(assurance_module, "build_receipt", lambda root, task_id: receipt)
    monkeypatch.setattr(state_module, "repo_root", lambda: REPO_ROOT)
    monkeypatch.setattr(state_module, "resolve_task_id", lambda root, task: TASK)
    monkeypatch.setattr(development_loop, "git_history",
                        lambda root, base, head: asked.append((base, head)) or _history())

    path = tmp_path / "cycles.json"
    path.write_text(json.dumps(_done(_cycle(0))), encoding="utf-8")
    with pytest.raises(SystemExit):
        development_loop.cmd_dev_loop(_Args(path))
    assert asked == [(BASE, HEAD)]


def test_a_record_that_cannot_be_read_is_its_own_status(tmp_path):
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "dev-loop", "t", str(tmp_path / "absent.json")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_the_report_prints_the_verdict_before_the_loops_own_account(tmp_path, monkeypatch,
                                                                    capsys):
    """A reader who sees the loop's account first reads the verdict as agreeing with it."""
    _invoke(tmp_path, _done(_cycle(0), self_reported={"tests": "passed"}),
            monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert out.index("handoff:") < out.index("the loop's own account")
    assert "not a verdict" in out


# ── gaps the mutation sweep found ────────────────────────────────────────────
@pytest.mark.parametrize("key", ["Gate", "ACCEPTED", "Verdict", "Final_Status"])
def test_a_verdict_key_in_another_case_is_still_a_verdict_key(key):
    """JSON keys are whatever the writer typed. A check that only matched lowercase would let
    `Accepted` through — the same field, spelled the way a different producer spells it."""
    problems = validate(_log(_cycle(0), self_reported={key: "passed"}))
    assert any("where a gate's belongs" in p for p in problems), (key, problems)


def test_an_account_key_this_schema_does_not_define_is_refused():
    """Closed for the reason the cycle's keys are: a field nobody reads would be accepted,
    dropped, and leave the caller believing it said something about the loop."""
    problems = validate(_log(_cycle(0), self_reported={"coverage": "98%"}))
    assert any("'coverage'" in p for p in problems), problems


def test_a_log_that_does_not_say_what_it_is_is_refused():
    """The schema id is what tells a reader which vocabulary the states belong to. Without it,
    `review` could be this module's cycle state or any other document's."""
    assert any("schema" in p for p in validate(dict(_log(_cycle(0)), schema="something/v1")))


def test_a_loop_with_no_goal_is_refused():
    """There is nothing to be finished against, so `ready-for-assurance` would mean only that
    the loop stopped."""
    for goal in ("", "   "):
        assert any("no goal" in p for p in validate(_log(_cycle(0), goal=goal))), repr(goal)


@pytest.mark.parametrize("immutable", [None, 0, "", "no", "false", 1, "yes"])
def test_a_target_that_never_said_it_was_immutable_is_not_treated_as_one(immutable):
    """`immutable` absent means the receipt did not answer, which is not the same as answering
    no — and `"false"`, `"yes"` and `1` are all truthy, so a check written against truthiness
    reads a receipt that said nothing, and one that said no, as one that said yes.

    The commit is valid here on purpose: with a missing commit the shape check would refuse
    this anyway, and the test would pass without the flag being read at all.
    """
    target = _receipt(immutable=immutable, head={"observed": True, "commit": HEAD})
    result = handoff(_done(_cycle(0)), target)
    assert result["status"] == development_loop.REFUSED, (immutable, result)
    assert [r["reason"] for r in result["reasons"]] == [TARGET_NOT_IMMUTABLE], immutable
    assert result["target"]["immutable"] is False


# ── what round 1 found ───────────────────────────────────────────────────────
def test_the_handoff_must_point_at_what_this_loop_produced():
    """The half `assurance.py` cannot answer. It knows the commit is fixed; only the record
    says which fixed thing the loop made. Without the comparison, any immutable commit on the
    task admits any valid-looking log — a receipt describing work nobody in it did."""
    result = handoff(_log(_cycle(0), outcome=READY_FOR_ASSURANCE), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert [r["reason"] for r in result["reasons"]] == [
        development_loop.TARGET_NOT_THE_LOOPS]


def test_a_loop_that_has_not_said_it_is_finished_has_nothing_to_hand_over():
    """Not observably stuck is not the same as done. A single implement cycle with no failure
    passes every bound, and without a declared outcome that would be admissible."""
    result = handoff(_log(dict(_cycle(0), product=HEAD)), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.NOT_DECLARED_DONE for r in result["reasons"])


def test_a_blocked_loop_is_not_a_finished_one():
    result = handoff(_log(dict(_cycle(0), product=HEAD), outcome=BLOCKED), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.NOT_DECLARED_DONE for r in result["reasons"])


def test_an_outcome_nobody_defined_is_refused():
    assert any("is not one of" in p
               for p in validate(_log(_cycle(0), outcome="basically done")))


def test_a_recovery_between_two_identical_failures_is_not_a_repeated_failure():
    """Dropping the successes turns "x, recovered, x" into two consecutive identical failures.
    That is a loop working through a regression, not one stuck on it."""
    cycles = load(_log(_cycle(0, failure="x"), _cycle(1), _cycle(2, failure="x")))
    assert must_stop(cycles, Limits(repeated_failure=2)) == []


def test_a_run_of_successes_is_a_run_but_not_a_repeated_failure():
    """Counting the run of `None` would stop a loop for succeeding three times."""
    cycles = load(_log(_cycle(0, failure="x"), _cycle(1), _cycle(2), _cycle(3)))
    assert must_stop(cycles, Limits(repeated_failure=2)) == []


@pytest.mark.parametrize("index", [False, True])
def test_a_boolean_index_is_not_a_position(index):
    """`False == 0` and `True == 1`, so a bool index passes a comparison against the first two
    positions while being a different kind of thing."""
    log = _log(_cycle(0), _cycle(1))
    log["cycles"][int(index)]["index"] = index
    assert any("index is" in p for p in validate(log)), (index, validate(log))


def test_a_target_that_says_it_is_immutable_but_names_nothing_is_refused():
    """The contract is that a handoff names a commit. A receipt claiming immutability with no
    commit satisfies the flag and not the contract."""
    result = handoff(_log(dict(_cycle(0), product=HEAD), outcome=READY_FOR_ASSURANCE),
                     _receipt(head={}))
    assert result["status"] == development_loop.REFUSED
    # The reason matters: without the shape check this is still refused, but for saying the
    # loop produced something other than `None` — which reads as the loop's mistake rather
    # than the receipt's.
    assert [r["reason"] for r in result["reasons"]] == [TARGET_NOT_IMMUTABLE]
    assert result["target"]["commit"] is None


@pytest.mark.parametrize("commit", ["not-an-oid", "a" * 39, "a" * 41, "A" * 40, 40])
def test_a_target_commit_that_is_not_an_object_id_is_refused(commit):
    result = handoff(_done(_cycle(0)), _receipt(head={"observed": True, "commit": commit}))
    assert result["status"] == development_loop.REFUSED
    assert [r["reason"] for r in result["reasons"]] == [TARGET_NOT_IMMUTABLE], commit


def test_a_sha256_repository_can_answer_honestly():
    """Hard-coding 40 hex would refuse a sha-256 repository's real object ids."""
    oid = "c" * 64
    result = handoff(_log(dict(_cycle(0), product=oid), outcome=READY_FOR_ASSURANCE),
                     _receipt(commit=oid))
    assert result["status"] == development_loop.ADMISSIBLE, result["reasons"]


# ── what round 2 found ───────────────────────────────────────────────────────
def test_a_product_outside_the_delivered_history_is_its_own_stop_reason():
    """40 hex characters is a spelling, not an object — and existence is not enough either: a
    stuck loop can name a different object that was already in the repository every cycle, and
    every one of them resolves. What means "part of this work" is ancestry."""
    cycles = load(_log(_cycle(0), _cycle(1)))
    reached = {cycles[0].product}
    reasons = must_stop(cycles, history=_history(within=lambda oid: oid in reached))
    assert [r["reason"] for r in reasons] == [development_loop.PRODUCT_UNRELATED]
    assert cycles[1].product in reasons[0]["detail"]
    assert must_stop(cycles, history=_history()) == []


def test_without_the_predicate_the_answer_says_so_rather_than_claiming_more():
    """A caller that cannot resolve objects gets the weaker answer and is told which one it
    got, instead of reading "nothing changed" as a fact about git."""
    assert handoff(_done(_cycle(0)), IMMUTABLE)["products_related"] is False
    assert handoff(_done(_cycle(0)), IMMUTABLE,
                   history=_history())["products_related"] is True


def test_a_borrowed_product_refuses_the_handoff():
    result = handoff(_done(_cycle(0), _cycle(1)), IMMUTABLE, history=_history(within=lambda oid: oid == HEAD))
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.PRODUCT_UNRELATED for r in result["reasons"])


def test_a_record_about_another_task_is_not_this_ones_completion():
    """Two loops pursuing different goals that end at the same commit are indistinguishable
    without this. Reading one as the other would credit this task with work done elsewhere."""
    result = handoff(_done(_cycle(0), task="rig-somebody-elses-task"), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert [r["reason"] for r in result["reasons"]] == [development_loop.NOT_THIS_TASK]


def test_a_record_that_does_not_say_which_task_it_is_about_is_refused():
    log = _log(_cycle(0))
    del log["task"]
    assert any("does not say which task" in p for p in validate(log))
    assert any("does not say which task" in p for p in validate(_log(_cycle(0), task="  ")))


def test_a_receipt_that_names_no_task_matches_no_record():
    result = handoff(_done(_cycle(0)), {"task": {}, "target": IMMUTABLE["target"]})
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.NOT_THIS_TASK for r in result["reasons"])


@pytest.mark.parametrize("receipt", [
    {"task": {"id": TASK}, "target": None},
    {"task": {"id": TASK}},
    {"task": {"id": TASK}, "target": {"immutable": True, "head": None}},
    {"task": None, "target": None},
])
def test_a_malformed_receipt_is_refused_rather_than_raising(receipt):
    """`handoff` is called with whatever the caller had. A shape it did not expect should come
    back as a refusal a reader can act on, not a traceback the caller has to interpret."""
    result = handoff(_done(_cycle(0)), receipt)
    assert result["status"] == development_loop.REFUSED, receipt
    assert result["target"]["commit"] is None


def test_a_refusal_also_says_whether_the_products_were_checked():
    """The weaker answer matters most where something was refused: a reader deciding what to
    do next needs to know whether "nothing changed" was checked or taken on trust."""
    for reason_maker in (lambda: handoff(_log(_cycle(0)), IMMUTABLE),
                         lambda: handoff(_done(_cycle(0), task="elsewhere"), IMMUTABLE)):
        assert reason_maker()["products_related"] is False
    # A record that cannot be read still reports which answer the caller was going to get.
    assert handoff({"schema": "wrong"}, IMMUTABLE)["products_related"] is False
    assert handoff({"schema": "wrong"}, IMMUTABLE,
                   history=_history())["products_related"] is True


# ── what round 3 found ───────────────────────────────────────────────────────
def test_existence_is_not_the_question_and_neither_is_bare_ancestry():
    """Two defeated attacks in one place. Every object already in the repository exists, so a
    stuck loop could name a different pre-existing one every cycle. And the delivered commit's
    ancestry has no lower bound, so it could reach back past the task instead."""
    borrowed = load(_log(_cycle(0, product="an-old-commit"), _cycle(1, product="another")))
    assert must_stop(borrowed, history=_history()) == []
    inside = {borrowed[0].product}
    assert [r["reason"] for r in
            must_stop(borrowed, history=_history(within=lambda oid: oid in inside))] == [
        development_loop.PRODUCT_UNRELATED]


def test_real_commits_reported_in_no_particular_order_are_not_a_sequence_of_work():
    """Once borrowing from outside the range is refused, borrowing from inside it is what is
    left — and a set of real in-range commits in any order would otherwise read as progress."""
    cycles = load(_log(_cycle(0), _cycle(1), _cycle(2)))
    chain = {(cycles[0].product, cycles[1].product)}
    reasons = must_stop(cycles, history=_history(advances=lambda a, b: (a, b) in chain))
    assert [r["reason"] for r in reasons] == [development_loop.PRODUCTS_NOT_A_CHAIN]
    assert f"{cycles[1].product} → {cycles[2].product}" in reasons[0]["detail"]


def test_a_cycle_that_ended_where_the_last_one_did_is_no_progress_not_a_broken_chain():
    """Two names for one fact would be two complaints about it. A repeated commit is caught by
    the no-progress bound; asking whether it builds on itself adds nothing."""
    cycles = load(_log(_cycle(0), dict(_cycle(1), product=_oid("tree-0"))))
    reasons = must_stop(cycles, Limits(no_progress=2),
                        history=_history(advances=lambda a, b: False))
    assert [r["reason"] for r in reasons] == [NO_PROGRESS]


def test_a_commit_already_refused_as_outside_is_not_also_a_broken_link():
    """A break reported for a commit refused above would be a second complaint about one
    problem, and a reader fixing the first would find the second still there."""
    cycles = load(_log(_cycle(0), _cycle(1), _cycle(2)))
    inside = {cycles[0].product, cycles[2].product}
    reasons = must_stop(cycles, history=_history(within=lambda oid: oid in inside,
                                                 advances=lambda a, b: False))
    assert [r["reason"] for r in reasons] == [development_loop.PRODUCT_UNRELATED]


def test_a_record_that_restates_the_goal_is_refused():
    """A loop free to say what it was pursuing decides what "done" was measured against, which
    is the decision this boundary exists to reserve."""
    result = handoff(_done(_cycle(0), goal="make a comment change"), IMMUTABLE)
    assert result["status"] == development_loop.REFUSED
    assert [r["reason"] for r in result["reasons"]] == [development_loop.NOT_THIS_GOAL]


def test_a_receipt_that_records_no_goal_matches_no_record():
    result = handoff(_done(_cycle(0)), _receipt(goal=None))
    assert result["status"] == development_loop.REFUSED
    assert any(r["reason"] == development_loop.NOT_THIS_GOAL for r in result["reasons"])


@pytest.mark.parametrize("receipt", [
    {"task": [], "target": None},
    {"task": "a-string", "target": None},
    {"task": {"id": TASK}, "target": []},
    {"task": {"id": TASK}, "target": "a-string"},
    {"task": {"id": TASK}, "target": {"immutable": True, "head": []}},
    {"task": {"id": TASK}, "target": {"immutable": True, "head": "a-string"}},
])
def test_a_truthy_malformed_receipt_container_is_refused_rather_than_raising(receipt):
    """`or {}` passes a truthy non-dict straight through and then raises on `.get`, so the
    contract said "refused" and the code said "traceback" for every shape that is not falsy."""
    result = handoff(_done(_cycle(0)), receipt)
    assert result["status"] == development_loop.REFUSED, receipt
    assert result["target"]["commit"] is None


def test_the_git_history_asks_about_the_range_and_reads_only_zero_as_yes(monkeypatch):
    """The one function that touches the repository, and the one a stubbed `History` cannot
    stand in for. Inverting the comparison would refuse every genuine commit and admit every
    borrowed one; `<= 0` would read a git killed by a signal as a yes."""
    import subprocess as real_subprocess

    calls = []

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode

    answers = {(BASE, "inside"): 0, ("inside", HEAD): 0,
               (BASE, "outside"): 1, ("outside", HEAD): 0,
               # Reachable from the base but not leading to the head: a commit on a branch
               # this task started and abandoned. Dropping the upper bound would admit it.
               (BASE, "sideways"): 0, ("sideways", HEAD): 1,
               (BASE, "killed"): -9, ("killed", HEAD): 0}

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("cwd")))
        return _Result(answers[(argv[3], argv[4])])

    monkeypatch.setattr(real_subprocess, "run", fake_run)
    history = development_loop.git_history("/somewhere", BASE, HEAD)
    assert history.within("inside") is True
    assert history.within("outside") is False, "a commit the task did not start from"
    assert history.within("sideways") is False, "a commit the task started but abandoned"
    assert history.within("killed") is False, "a negative return code is not a yes"
    assert history.advances("inside", HEAD) is True
    assert calls[0][0][:3] == ["git", "merge-base", "--is-ancestor"]
    assert calls[0][1] == "/somewhere"


def test_a_commit_is_its_own_ancestor_without_asking_git(monkeypatch):
    """`git merge-base --is-ancestor X X` is true, and asking is a process per comparison for
    an answer that cannot vary."""
    import subprocess as real_subprocess

    def refuse(*args, **kwargs):
        raise AssertionError("git was called for a commit against itself")

    monkeypatch.setattr(real_subprocess, "run", refuse)
    assert development_loop.git_history("/somewhere", HEAD, HEAD).advances(HEAD, HEAD) is True


def test_two_runs_of_equal_length_report_the_earlier_one():
    """Either is the same stop reason, but a detail that changed with a later tie would make
    two readings of one record look like two different problems."""
    cycles = load(_log(_cycle(0, failure="a"), _cycle(1, failure="a"),
                       _cycle(2, failure="b"), _cycle(3, failure="b")))
    reasons = must_stop(cycles, Limits(repeated_failure=2))
    assert [r["reason"] for r in reasons] == [REPEATED_FAILURE]
    assert "'a'" in reasons[0]["detail"], reasons[0]["detail"]
