"""#439 — minimise the cost of producing the required assurance, never the requirement.

Grouped by what they hold: the record is a closed schema, the floor is not the plan's to state,
an unknown cost is not a low one, and running out of budget is an answer rather than a discount.
"""

import dataclasses
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import assurance_budget as budget_mod
from rig_workbench.workbench.assurance_budget import (BALANCED, CHEAPEST, ESTIMATED, FASTEST,
                                                      MEASURED, SCHEMA, UNKNOWN, Budget, Plan,
                                                      excluded, load, select, validate)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"
FLOOR = frozenset({"independent-review", "signed-provenance"})


def _plan(plan_id="thorough", guarantees=("independent-review", "signed-provenance"),
          cost=4.0, cost_basis=MEASURED, latency_seconds=600.0,
          reasons=("measured on this task class",)):
    # Not `list(...)`: a test saying `guarantees="a-name"` means the document holds that
    # string, and coercing it here would test the helper rather than the schema.
    return {"id": plan_id,
            "guarantees": list(guarantees) if isinstance(guarantees, tuple) else guarantees,
            "cost": cost,
            "cost_basis": cost_basis, "latency_seconds": latency_seconds,
            "reasons": list(reasons) if isinstance(reasons, tuple) else reasons}


_DEFAULT = object()


def _record(*plans, task="a-task", **extra):
    given = extra.pop("plans", _DEFAULT)
    listed = (list(plans) or [_plan()]) if given is _DEFAULT else list(given)
    return {"schema": SCHEMA, "task": task, "plans": listed, **extra}


# ── the record is closed ─────────────────────────────────────────────────────
def test_a_valid_record_has_no_problems():
    assert validate(_record(_plan(), _plan("quick", cost=1.0))) == []


def test_a_record_with_no_plans_is_refused():
    """An empty list is not "the budget was met by everything"; it is nobody proposing
    anything, and the answer to that is exhaustion rather than success."""
    assert any("nothing to choose between" in p for p in validate(_record(plans=[])))


def test_two_plans_under_one_name_are_two_answers():
    problems = validate(_record(_plan("a"), _plan("a", cost=1.0)))
    assert any("more than once" in p for p in problems), problems


def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped():
    assert any("'quality'" in p for p in validate(_record(_plan() | {"quality": 9})))
    assert any("'waivable'" in p for p in validate(_record(waivable=True)))


def test_the_accepted_keys_are_the_ones_a_plan_actually_has():
    assert budget_mod.PLAN_FIELDS == {f.name for f in dataclasses.fields(Plan)}


def test_a_record_that_does_not_say_which_change_it_plans_is_refused():
    for task in (None, "", "  ", 42):
        assert any("which change" in p for p in validate(_record(task=task))), repr(task)


# ── an unknown cost is not a low one ─────────────────────────────────────────
def test_an_unknown_price_is_not_a_price_with_a_number_next_to_it():
    problems = validate(_record(_plan(cost=0.0, cost_basis=UNKNOWN)))
    assert any("not a price with a number" in p for p in problems), problems
    assert validate(_record(_plan(cost=None, cost_basis=UNKNOWN))) == []


@pytest.mark.parametrize("cost", [None, -1, True, False, "4.0", []])
def test_a_priced_plan_needs_a_number(cost):
    """`True` is an `int` in Python and would price a plan at one unit while reading as a flag
    somebody set."""
    assert any("cost is" in p
               for p in validate(_record(_plan(cost=cost, cost_basis=ESTIMATED)))), cost


def test_a_cost_basis_nobody_defined_is_refused():
    """Leaving it out would let a plan that knows nothing about its price read as free."""
    for basis in (None, "cheap", ""):
        assert any("cost_basis" in p for p in validate(_record(_plan(cost_basis=basis)))), basis


def test_a_plan_that_cannot_say_what_it_costs_is_not_a_candidate():
    """Comparing it as though the answer were zero makes the plan that knows least about
    itself the cheapest one on the list."""
    reasons = excluded(load(_record(_plan(cost=None, cost_basis=UNKNOWN)))[0],
                       Budget(required=FLOOR, task="a-task"))
    assert [r["reason"] for r in reasons] == [budget_mod.PRICE_UNKNOWN]


def test_an_unpriced_plan_never_wins_by_being_the_cheapest():
    result = select(_record(_plan("known", cost=9.0),
                            _plan("unpriced", cost=None, cost_basis=UNKNOWN)),
                    Budget(required=FLOOR, task="a-task", optimisation=CHEAPEST))
    assert result["status"] == budget_mod.SELECTED
    assert result["selected"]["id"] == "known"


def test_a_plan_that_cannot_say_how_long_it_takes_is_not_the_quickest():
    """The same rule as the price, one field over."""
    result = select(_record(_plan("timed", latency_seconds=900.0),
                            _plan("untimed", latency_seconds=None)),
                    Budget(required=FLOOR, task="a-task", optimisation=FASTEST))
    assert result["selected"]["id"] == "timed"


# ── the floor is not the plan's to state ─────────────────────────────────────
def test_a_plan_that_does_not_produce_what_is_required_is_not_a_cheaper_way():
    """It is a different, smaller thing. Ranking it against the others by price would be
    comparing a discount against a purchase."""
    reasons = excluded(load(_record(_plan("cheap", guarantees=("independent-review",),
                                          cost=0.5)))[0],
                       Budget(required=FLOOR, task="a-task"))
    assert [r["reason"] for r in reasons] == [budget_mod.BELOW_FLOOR]
    assert reasons[0]["missing"] == ["signed-provenance"]


def test_the_cheapest_plan_that_clears_the_floor_wins_and_the_cheaper_one_does_not():
    result = select(_record(_plan("thorough", cost=4.0),
                            _plan("skimpy", guarantees=("independent-review",), cost=0.5)),
                    Budget(required=FLOOR, task="a-task", optimisation=CHEAPEST))
    assert result["selected"]["id"] == "thorough"
    assert [e["reason"] for e in result["excluded"]] == [budget_mod.BELOW_FLOOR]


def test_a_floor_the_plans_wrote_themselves_is_not_a_floor():
    """`Budget.required` comes from the caller. Nothing reads a plan's `guarantees` to decide
    what is required — that is the plan grading its own homework."""
    assert "required" in {f.name for f in dataclasses.fields(Budget)}
    everything = load(_record(_plan(guarantees=("whatever-i-do",))))
    assert [r["reason"] for r in excluded(everything[0], Budget(required=FLOOR, task="a-task"))] == [
        budget_mod.BELOW_FLOOR]


def test_no_floor_stated_requires_nothing_rather_than_everything():
    assert excluded(load(_record(_plan(guarantees=())))[0], Budget(required=frozenset(), task="a-task")) == []


@pytest.mark.parametrize("required", ["a-name", ["a-name"], {"a-name": True}, 42])
def test_a_floor_that_is_not_a_set_of_names_is_refused(required):
    """A bare string iterates as characters and a dict as its keys, so either would become a
    floor nobody wrote."""
    with pytest.raises(ValueError, match="not a set of guarantee names"):
        Budget(required=required, task="a-task")


@pytest.mark.parametrize("name", ["", "   ", " padded ", 42, None])
def test_a_floor_naming_nothing_is_refused(name):
    with pytest.raises(ValueError, match="does not name a guarantee"):
        Budget(required=frozenset({name}), task="a-task")


# ── money and time are limits, and absent is not zero ────────────────────────
def test_a_plan_over_the_budget_is_excluded_and_says_so_separately():
    """Costing too much and producing too little mean different things to a reader: one might
    be affordable tomorrow and the other is not this task at all."""
    reasons = excluded(load(_record(_plan(cost=9.0)))[0], Budget(required=FLOOR, task="a-task", max_cost=5.0))
    assert [r["reason"] for r in reasons] == [budget_mod.OVER_BUDGET]


def test_no_cost_limit_means_no_limit_and_a_limit_of_zero_means_zero():
    """Absent and zero used to be the same answer in the module before this one."""
    plan = load(_record(_plan(cost=0.5)))[0]
    assert excluded(plan, Budget(required=FLOOR, task="a-task")) == []
    assert [r["reason"] for r in excluded(plan, Budget(required=FLOOR, task="a-task", max_cost=0))] == [
        budget_mod.OVER_BUDGET]
    assert excluded(load(_record(_plan(cost=0.0)))[0],
                    Budget(required=FLOOR, task="a-task", max_cost=0)) == []


def test_a_plan_that_cannot_say_how_long_it_takes_fails_a_latency_budget():
    """Not "no answer, so no problem": the caller stated a limit, and nothing can be compared
    against it."""
    reasons = excluded(load(_record(_plan(latency_seconds=None)))[0],
                       Budget(required=FLOOR, task="a-task", max_latency_seconds=900.0))
    assert [r["reason"] for r in reasons] == [budget_mod.TOO_SLOW]
    assert excluded(load(_record(_plan(latency_seconds=None)))[0], Budget(required=FLOOR, task="a-task")) == []


@pytest.mark.parametrize("field", ["max_cost", "max_latency_seconds"])
@pytest.mark.parametrize("value", [-1, True, False, "5", []])
def test_a_limit_that_is_not_a_limit_is_refused(field, value):
    with pytest.raises(ValueError, match="a limit or nothing"):
        Budget(required=frozenset(), task="a-task", **{field: value})


def test_every_reason_a_plan_was_ruled_out_is_reported():
    """A caller told only that it costs too much would fund it and meet the floor it was always
    going to miss."""
    reasons = excluded(load(_record(_plan(guarantees=(), cost=9.0, latency_seconds=99999.0)))[0],
                       Budget(required=FLOOR, task="a-task", max_cost=5.0, max_latency_seconds=900.0))
    assert {r["reason"] for r in reasons} == {budget_mod.BELOW_FLOOR, budget_mod.OVER_BUDGET,
                                              budget_mod.TOO_SLOW}


# ── running out of budget is an answer, not a discount ───────────────────────
def test_nothing_affordable_that_clears_the_floor_is_exhaustion_not_a_selection():
    result = select(_record(_plan("thorough", cost=9.0),
                            _plan("skimpy", guarantees=("independent-review",), cost=0.5)),
                    Budget(required=FLOOR, task="a-task", max_cost=5.0))
    assert result["status"] == budget_mod.EXHAUSTED
    assert result["selected"] is None
    assert {e["reason"] for e in result["excluded"]} == {budget_mod.OVER_BUDGET,
                                                         budget_mod.BELOW_FLOOR}


def test_exhaustion_names_what_someone_can_do_about_it():
    """"We lowered the target a bit", written as prose in a field nobody parses, is exactly the
    silent downgrade the design principle rules out. The ways out are a closed vocabulary."""
    result = select(_record(_plan(cost=9.0)), Budget(required=FLOOR, task="a-task", max_cost=1.0))
    assert result["answers"] == list(budget_mod.EXHAUSTION_ANSWERS)
    assert budget_mod.RELAXED in result["answers"]
    assert set(budget_mod.EXHAUSTION_ANSWERS) == {budget_mod.BLOCKED, budget_mod.MORE_BUDGET,
                                                  budget_mod.ALTERNATE_RUNTIME,
                                                  budget_mod.RELAXED}


def test_a_selection_offers_no_ways_out_because_there_is_nothing_to_get_out_of():
    assert select(_record(_plan()), Budget(required=FLOOR, task="a-task"))["answers"] == []


def test_exhaustion_never_selects_the_closest_thing():
    """The one behaviour this module exists to prevent: picking the plan that nearly clears the
    floor because it was the only affordable one."""
    result = select(_record(_plan("nearly", guarantees=("independent-review",), cost=0.5)),
                    Budget(required=FLOOR, task="a-task", max_cost=5.0))
    assert result["status"] == budget_mod.EXHAUSTED
    assert result["selected"] is None


# ── the dial ranks the survivors, and only them ──────────────────────────────
@pytest.mark.parametrize("optimisation,expected", [
    (CHEAPEST, "cheap-slow"), (FASTEST, "dear-quick"), (BALANCED, "middle")])
def test_the_dial_orders_plans_that_already_clear_the_floor(optimisation, expected):
    result = select(_record(_plan("cheap-slow", cost=1.0, latency_seconds=1800.0),
                            _plan("dear-quick", cost=9.0, latency_seconds=60.0),
                            _plan("middle", cost=4.0, latency_seconds=300.0)),
                    Budget(required=FLOOR, task="a-task", optimisation=optimisation,
                           **({"seconds_per_unit_cost": 100.0}
                              if optimisation == BALANCED else {})))
    assert result["selected"]["id"] == expected, optimisation


def test_balancing_money_against_time_needs_the_callers_exchange_rate():
    """Adding dollars to seconds is a category error, and what an hour is worth is a judgement
    about the caller's situation rather than something this module can supply."""
    with pytest.raises(ValueError, match="adding dollars to seconds"):
        Budget(required=FLOOR, task="a-task", optimisation=BALANCED)


@pytest.mark.parametrize("rate", [0, -1, True, "100", []])
def test_a_rate_that_is_not_a_rate_is_refused(rate):
    with pytest.raises(ValueError, match="a positive rate or nothing"):
        Budget(required=frozenset(), task="a-task", seconds_per_unit_cost=rate)


def test_the_rate_changes_which_plan_wins():
    """It is the caller's judgement doing the work, which is the point of asking for it."""
    plans = _record(_plan("cheap-slow", cost=1.0, latency_seconds=1800.0),
                    _plan("dear-quick", cost=9.0, latency_seconds=60.0))
    patient = select(plans, Budget(required=FLOOR, task="a-task", optimisation=BALANCED,
                                   seconds_per_unit_cost=10000.0))
    impatient = select(plans, Budget(required=FLOOR, task="a-task", optimisation=BALANCED,
                                     seconds_per_unit_cost=10.0))
    assert patient["selected"]["id"] == "cheap-slow"
    assert impatient["selected"]["id"] == "dear-quick"


def test_an_optimisation_nobody_defined_is_refused():
    with pytest.raises(ValueError, match="optimisation"):
        Budget(required=frozenset(), task="a-task", optimisation="quality")


def test_two_plans_that_tie_are_ordered_by_name():
    """Either would be the same answer, but a choice that changed between two readings of one
    record would make the record look like two different decisions."""
    result = select(_record(_plan("bbb", cost=1.0, latency_seconds=10.0),
                            _plan("aaa", cost=1.0, latency_seconds=10.0)),
                    Budget(required=FLOOR, task="a-task", optimisation=CHEAPEST))
    assert result["selected"]["id"] == "aaa"


# ── the command exits with the answer ────────────────────────────────────────
def _run(tmp_path, record, budget=None, json_out=False, record_text=None, budget_text=None,
         no_budget=False):
    plans = tmp_path / "plans.json"
    plans.write_text(record_text if record_text is not None else json.dumps(record),
                     encoding="utf-8")
    argv = ["budget-plan", str(plans)]
    if not no_budget:
        path = tmp_path / "budget.json"
        path.write_text(budget_text if budget_text is not None
                        else json.dumps({"required": sorted(FLOOR), "task": "a-task"} | (budget or {})),
                        encoding="utf-8")
        argv += ["--budget", str(path)]
    if json_out:
        argv.append("--json")
    return subprocess.run([sys.executable, str(WORKBENCH), *argv],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


def test_a_chosen_plan_exits_zero(tmp_path):
    result = _run(tmp_path, _record(_plan()))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "selected" in result.stdout and "thorough" in result.stdout


def test_exhaustion_exits_nonzero(tmp_path):
    """Exiting 0 would tell the shell a plan had been chosen, which is the moment a silent
    downgrade becomes possible downstream."""
    result = _run(tmp_path, _record(_plan(cost=9.0)), budget={"max_cost": 1.0})
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert budget_mod.RELAXED in result.stdout


def test_the_command_will_not_run_without_a_budget(tmp_path):
    result = _run(tmp_path, _record(), no_budget=True)
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "--budget" in result.stderr


def test_the_json_output_carries_what_a_caller_would_act_on(tmp_path):
    result = _run(tmp_path,
                  _record(_plan("thorough", cost=4.0),
                          _plan("skimpy", guarantees=("independent-review",), cost=0.5)),
                  json_out=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema"] == SCHEMA
    assert payload["status"] == budget_mod.SELECTED
    assert payload["selected"]["id"] == "thorough"
    assert payload["task"] == "a-task"
    assert [e["reason"] for e in payload["excluded"]] == [budget_mod.BELOW_FLOOR]
    assert payload["answers"] == []


def test_a_budget_key_this_schema_does_not_define_is_refused(tmp_path):
    result = _run(tmp_path, _record(), budget={"quality_floor": 9})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "'quality_floor'" in json.loads(result.stdout)["error"]


def test_a_budget_floor_that_is_an_object_registers_its_keys_unless_it_is_checked(tmp_path):
    """`frozenset(...)` takes whatever iterates, and this is the document that says what the
    assurance must include."""
    result = _run(tmp_path, _record(), budget_text='{"required": {"independent-review": false}, "task": "a-task"}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "array of guarantee names" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("which", ["record", "budget"])
def test_a_document_naming_one_key_twice_is_refused(tmp_path, which):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so a plan whose
    `cost` appears twice reaches the comparison naming only the last one."""
    if which == "record":
        text = ('{"schema": "%s", "task": "t", "plans": [{"id": "a", "guarantees": [], '
                '"cost": 9.0, "cost": 0.5, "cost_basis": "measured", "reasons": ["r"]}]}'
                % SCHEMA)
        result = _run(tmp_path, None, record_text=text)
    else:
        result = _run(tmp_path, _record(),
                      budget_text='{"required": [], "task": "t", "max_cost": 9, "max_cost": 1}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "twice" in json.loads(result.stdout)["error"]


def test_a_record_that_cannot_be_read_is_its_own_status(tmp_path):
    budget = tmp_path / "budget.json"
    budget.write_text('{"required": [], "task": "t"}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "budget-plan", str(tmp_path / "absent.json"),
         "--budget", str(budget)],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_a_budget_chosen_for_another_change_says_nothing_about_this_one(tmp_path):
    result = _run(tmp_path, _record(task="wording-change"),
                  budget={"task": "auth-boundary-change"}, json_out=True)
    assert result.returncode == 1, (result.returncode, result.stdout)
    payload = json.loads(result.stdout)
    assert payload["status"] == budget_mod.REFUSED
    assert payload["excluded"][0]["reason"] == "budget-was-chosen-for-another-task"


def test_a_budget_that_names_no_change_is_refused(tmp_path):
    """Absent used to disable the binding, which is "this budget applies to any change" written
    as silence — the shape every fail-open in the module before this one had."""
    result = _run(tmp_path, _record(), budget_text='{"required": []}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "'task' is not stated" in json.loads(result.stdout)["error"]


def test_a_budget_that_states_no_floor_is_refused(tmp_path):
    """"The caller did not supply the policy floor" and "the policy requires nothing" are the
    same value with a default and opposite answers without one."""
    result = _run(tmp_path, _record(), budget_text='{"task": "a-task"}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "'required' is not stated" in json.loads(result.stdout)["error"]


def test_a_floor_written_as_an_empty_array_is_a_floor_of_nothing(tmp_path):
    """Stated, and therefore allowed: a caller who wrote `[]` said what they meant."""
    result = _run(tmp_path, _record(_plan(guarantees=("whatever",))),
                  budget_text='{"required": [], "task": "a-task"}')
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_a_matching_task_selects(tmp_path):
    result = _run(tmp_path, _record(task="the-change"), budget={"task": "the-change"})
    assert result.returncode == 0, (result.stdout, result.stderr)


# ── it does not estimate, and it does not plan ───────────────────────────────
def test_the_judgement_touches_nothing_and_calls_no_model():
    """What a verifier will cost and which plans are worth considering are reading, judging and
    concluding. A module that did them would leave nothing a gate could check."""
    import ast
    tree = ast.parse((REPO_ROOT / "rig_workbench" / "workbench"
                      / "assurance_budget.py").read_text(encoding="utf-8"))
    reaching = {"subprocess", "socket", "http", "urllib", "requests", "os", "open"}
    judging = {"validate", "load", "excluded", "select", "_rank", "plan_problems",
               "load_budget"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in judging):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.Import) for a in n.names}
        assert not names & reaching, (node.name, sorted(names & reaching))
    module_level = {a.name.split(".")[0] for n in tree.body
                    if isinstance(n, ast.Import) for a in n.names}
    module_level |= {(n.module or "").split(".")[0] for n in tree.body
                     if isinstance(n, ast.ImportFrom)}
    assert not module_level & reaching, module_level


# ── gaps the mutation sweep found ────────────────────────────────────────────
def test_an_unpriced_plan_is_not_compared_against_the_budget_as_though_it_were_free():
    """`plan.cost or 0` would put it under every limit while `PRICE_UNKNOWN` was the only thing
    reported — and a reader seeing one reason might raise the budget rather than the question."""
    reasons = excluded(load(_record(_plan(cost=None, cost_basis=UNKNOWN)))[0],
                       Budget(required=FLOOR, task="a-task", max_cost=0.0))
    assert [r["reason"] for r in reasons] == [budget_mod.PRICE_UNKNOWN]


def test_a_record_that_does_not_say_what_it_is_is_refused():
    """The schema id is what tells a reader which vocabulary `measured` belongs to."""
    for schema in ("rig.something-else/v1", None, ""):
        assert any("schema" in p for p in validate(_record(schema=schema))), repr(schema)


@pytest.mark.parametrize("plan_id", ["", "   ", " padded ", 42, None])
def test_a_plan_with_no_name_is_refused(plan_id):
    """The selected plan is named back to the caller, and two plans that are both `""` are one
    plan to the duplicate check and two to everything else."""
    problems = validate(_record(_plan(plan_id=plan_id)))
    assert any("has to name something" in p for p in problems), (plan_id, problems)


@pytest.mark.parametrize("guarantees", ["a-name", [""], [42], None, {"a": True}])
def test_a_plan_that_cannot_say_what_it_produces_is_refused(guarantees):
    """A bare string iterates as characters, so `"independent-review"` would be eighteen
    guarantees — and the floor comparison would find none of them."""
    problems = validate(_record(_plan(guarantees=guarantees)))
    assert any("guarantees must be names" in p for p in problems), (guarantees, problems)


@pytest.mark.parametrize("reasons", [(), "a string", [42], ["", "  "], None])
def test_a_plan_that_justifies_nothing_is_refused(reasons):
    problems = validate(_record(_plan(reasons=reasons)))
    assert any("reason" in p for p in problems), (reasons, problems)


@pytest.mark.parametrize("kwargs,fragment", [
    ({"id": " padded "}, "has to name something"),
    ({"guarantees": "a-name"}, "guarantees must be names"),
    ({"cost": None, "cost_basis": MEASURED}, "cost is"),
    ({"cost": 1.0, "cost_basis": UNKNOWN}, "not a price with a number"),
    ({"cost_basis": "cheap"}, "cost_basis"),
    ({"latency_seconds": -1}, "latency_seconds"),
    ({"reasons": ()}, "gives no reason"),
])
def test_a_plan_cannot_be_built_in_a_state_the_document_would_be_refused_in(kwargs, fragment):
    """One rule both paths reach. The module before this one took four review rounds to learn
    that a check on one ingestion path is a check on one ingestion path."""
    fields = {"id": "p", "guarantees": ("independent-review",), "cost": 1.0,
              "cost_basis": MEASURED, "latency_seconds": 10.0, "reasons": ("r",)} | kwargs
    with pytest.raises(ValueError, match=fragment):
        Plan(**fields)


def test_both_paths_answer_with_the_same_rule():
    bad = {"id": " x ", "guarantees": "a-name", "cost": None, "cost_basis": MEASURED,
           "latency_seconds": -1, "reasons": ()}
    from_document = [p for p in validate(_record(bad)) if p.startswith("plans[0]")]
    direct = budget_mod.plan_problems(bad["id"], bad["guarantees"], bad["cost"],
                                      bad["cost_basis"], bad["latency_seconds"],
                                      bad["reasons"], "plans[0]")
    assert from_document == direct != []


@pytest.mark.parametrize("task", ["", "   ", " padded ", 42, [], None])
def test_a_budget_task_that_names_nothing_is_refused(task):
    """A budget carries the floor, so one prepared for a wording change applied to an
    authentication change is a weaker floor arriving by mispairing."""
    with pytest.raises(ValueError, match="arriving by mispairing"):
        Budget(required=frozenset(), task=task)


@pytest.mark.parametrize("payload", [[], "a string", 42, None])
def test_a_budget_that_is_not_an_object_is_refused(payload):
    with pytest.raises(ValueError, match="expected an object"):
        budget_mod.load_budget(payload)


# ── what round 1 found: a quantity nobody can hold is not a quantity ─────────
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_a_plan_priced_at_infinity_is_not_priced(value):
    """As a price it clears every limit; `NaN` loses every comparison it is in, including
    against itself, so it would be neither over nor under a budget."""
    assert any("cost is" in p
               for p in validate(_record(_plan(cost=value, cost_basis=MEASURED)))), value


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
@pytest.mark.parametrize("field", ["max_cost", "max_latency_seconds",
                                   "seconds_per_unit_cost"])
def test_a_limit_of_infinity_is_not_a_limit(field, value):
    """As a limit it disables the constraint, and as an exchange rate it makes every latency
    worth nothing — silently collapsing `balanced` into `cheapest`."""
    with pytest.raises(ValueError):
        Budget(required=frozenset(), task="t", **{field: value})


def test_an_infinite_latency_is_not_a_duration():
    assert any("latency_seconds" in p
               for p in validate(_record(_plan(latency_seconds=float("inf")))))


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"])
def test_the_command_refuses_json_that_is_not_json(tmp_path, token):
    """Python's decoder accepts these tokens by default. Refused at the door as well as by the
    field rule, so a reader learns the file is not JSON rather than that a field was out of
    range."""
    result = _run(tmp_path, None,
                  record_text='{"schema": "%s", "task": "t", "plans": [{"id": "a", '
                              '"guarantees": [], "cost": %s, "cost_basis": "measured", '
                              '"reasons": ["r"]}]}' % (SCHEMA, token))
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "not a number JSON defines" in json.loads(result.stdout)["error"]


def test_the_budget_document_refuses_them_too(tmp_path):
    result = _run(tmp_path, _record(),
                  budget_text='{"required": [], "task": "t", "max_cost": Infinity}')
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "not a number JSON defines" in json.loads(result.stdout)["error"]


def test_the_module_names_the_moves_that_may_follow_not_a_record_of_anyone_making_one():
    """Recording who relaxed a target is somebody else's job, and saying otherwise here would
    claim an accountability this module does not hold."""
    source = (REPO_ROOT / "rig_workbench" / "workbench"
              / "assurance_budget.py").read_text(encoding="utf-8")
    assert "target-relaxed-by-a-decision" in source
    assert "relaxed-by-record" not in source


# ── what round 2 found: refusing is not running out ──────────────────────────
@pytest.mark.parametrize("record,budget", [
    ({"schema": "wrong"}, None),
    (None, "mismatch"),
])
def test_a_refusal_does_not_offer_to_relax_the_target(record, budget):
    """Refusing says the record could not be read, or that the budget belongs to another
    change. It does not say no affordable plan clears the floor — and offering "relax the
    target" to someone whose file was malformed is a fail-open with a helpful tone."""
    if budget == "mismatch":
        result = select(_record(task="one-change"),
                        Budget(required=FLOOR, task="another-change"))
    else:
        result = select(record, Budget(required=FLOOR, task="a-task"))
    assert result["status"] == budget_mod.REFUSED
    assert result["answers"] == []


def test_only_exhaustion_says_nothing_affordable_clears_the_floor(tmp_path):
    refused = _run(tmp_path, _record(task="one-change"), budget={"task": "another-change"})
    assert refused.returncode == 1
    assert "nothing affordable" not in refused.stdout, refused.stdout

    exhausted = _run(tmp_path, _record(_plan(cost=9.0)), budget={"max_cost": 1.0})
    assert exhausted.returncode == 1
    assert "nothing affordable" in exhausted.stdout


def test_an_exchange_rate_no_dial_reads_is_a_caller_asking_for_something_else():
    """Accepting and ignoring it would answer a caller who misspelled `balanced` with a
    selection instead of the question they meant to ask."""
    for optimisation in (CHEAPEST, FASTEST):
        with pytest.raises(ValueError, match="a caller asking for something other"):
            Budget(required=FLOOR, task="t", optimisation=optimisation,
                   seconds_per_unit_cost=100.0)


def test_the_command_refuses_a_rate_the_chosen_dial_does_not_read(tmp_path):
    result = _run(tmp_path, _record(),
                  budget={"optimisation": CHEAPEST, "seconds_per_unit_cost": 100.0})
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "asking for something other" in json.loads(result.stdout)["error"]


def test_a_plan_exactly_on_the_budget_is_within_it():
    """`>` and not `>=`: a limit of 900 seconds admits a plan that takes 900. Refusing it would
    make every stated limit one unit tighter than what the caller wrote."""
    plan = load(_record(_plan(cost=5.0, latency_seconds=900.0)))[0]
    assert excluded(plan, Budget(required=FLOOR, task="t", max_cost=5.0,
                                 max_latency_seconds=900.0)) == []
    assert [r["reason"] for r in excluded(plan, Budget(required=FLOOR, task="t",
                                                       max_latency_seconds=899.0))] == [
        budget_mod.TOO_SLOW]
    assert [r["reason"] for r in excluded(plan, Budget(required=FLOOR, task="t",
                                                       max_cost=4.99))] == [
        budget_mod.OVER_BUDGET]
