"""Minimise the cost of producing the required assurance, never the requirement (#439).

Applying the heaviest verifier, drill and approval to every change buys quality with money and
waiting; dropping a mandatory gate to save either buys the money back with something that was
not ours to spend. What is left is a constrained problem rather than a trade-off: hold the
assurance floor fixed, and choose among the plans that clear it.

**It does not estimate, and it does not plan.** What a verifier will cost, how long a runtime
will take, which plans are worth considering: reading, judging and concluding, which is an
agent's work. A module that called a model to do it would leave nothing a gate could check and
nothing a mutation could falsify. Plans arrive as records of what was considered, and what
lives here is the schema, the floor, and the refusals.

**The floor is not the plan's to state.** Which gates are mandatory, which verifier
independence is required, which approvals must be taken: built by the caller from the policy
and the assurance target (#434), for the reason `synthesise` builds its floor that way and
`route-team` builds its constraints that way. A requirement the thing being checked gets to
state is not a requirement, and a cheap plan that says so about itself is the failure this
module exists to prevent.

**An unknown cost is not a low one.** A runtime that cannot report what it charges reports
`unknown`, and `unknown` does not compare: it is not zero, it is not cheap, and it does not win
a comparison against a plan that measured itself honestly. Treating it as zero is how the plan
that knows least about itself becomes the cheapest one on the list.

**Running out of budget is an answer, not a discount.** When nothing within the budget clears
the floor, this says so — and the ways of saying so are a closed vocabulary, because "we
lowered the target a bit" written as prose in a field nobody parses is exactly the silent
downgrade the design principle rules out. Someone decides: block the change, raise the budget,
use a different runtime, or relax the target. What this names is the set of moves that are
allowed to follow, not a record of anyone making one — recording who relaxed a target is
somebody else's job, and saying otherwise here would claim an accountability this module does
not hold.
"""

from __future__ import annotations

import dataclasses
import math

SCHEMA = "rig.assurance-budget/v1"

#: What a plan costs, when anyone can say. `UNKNOWN` is a value and not a gap: a runtime that
#: cannot report its price says so, and nothing downstream reads the silence as free.
MEASURED, ESTIMATED, UNKNOWN = "measured", "estimated", "unknown"
COST_BASIS = (MEASURED, ESTIMATED, UNKNOWN)

#: How the caller wants the survivors ranked, once the floor has already excluded everyone who
#: does not clear it. Not "quality versus cost" — quality is the floor and is not on the dial.
CHEAPEST, FASTEST, BALANCED = "cheapest", "fastest", "balanced"
OPTIMISATIONS = (CHEAPEST, FASTEST, BALANCED)

#: What happens when nothing affordable clears the floor. A closed vocabulary because the
#: alternative — prose in a field nobody parses — is the silent downgrade this exists to stop.
BLOCKED = "blocked"
MORE_BUDGET = "more-budget-requested"
ALTERNATE_RUNTIME = "alternate-runtime-suggested"
RELAXED = "target-relaxed-by-a-decision"
EXHAUSTION_ANSWERS = (BLOCKED, MORE_BUDGET, ALTERNATE_RUNTIME, RELAXED)


def _is_name(value: object) -> bool:
    """A name: a non-blank string that is exactly itself once trimmed.

    The trimming rule is not cosmetic here either — plans and guarantees are compared by
    string, so two spellings of one name are two things to a comparison and one to everything
    downstream that trims.
    """
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _is_amount(value: object) -> bool:
    """A finite, non-negative number that is not a bool.

    `True` is an `int` in Python and would price a plan at one unit while reading as a flag
    somebody set. And `Infinity` is what Python's JSON decoder makes of the token by default:
    as a limit it disables the constraint, as a price it clears every limit, and as an exchange
    rate it makes every latency worth nothing — three ways for a quantity nobody can hold to
    pass as a quantity. `NaN` loses every comparison it is in, including against itself.
    """
    return (value is not True and value is not False and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def plan_problems(plan_id, guarantees, cost, cost_basis, latency_seconds, reasons,
                  where: str = "plan") -> list[str]:
    """Everything wrong with one plan's fields, wherever it came from.

    One function rather than a rule in `validate` and a hope on the programmatic path. The
    previous module in this series took four review rounds to learn that a check on one
    ingestion path is a check on one ingestion path.
    """
    problems: list[str] = []
    if not _is_name(plan_id):
        problems.append(f"{where}: id {plan_id!r} has to name something, exactly")
    if not isinstance(guarantees, (list, tuple, frozenset, set)) or not all(
            _is_name(g) for g in guarantees):
        problems.append(
            f"{where}: guarantees must be names of what this plan achieves. A plan that cannot "
            f"say what it produces cannot be compared against what is required")
    if cost_basis not in COST_BASIS:
        problems.append(
            f"{where}: cost_basis {cost_basis!r} is not one of {', '.join(COST_BASIS)}. "
            f"Leaving it out would let a plan that knows nothing about its price read as free")
    elif cost_basis == UNKNOWN:
        if cost is not None:
            problems.append(
                f"{where}: cost is {cost!r} and cost_basis is {UNKNOWN!r}. A price nobody knows "
                f"is not a price with a number next to it")
    elif not _is_amount(cost):
        problems.append(
            f"{where}: cost is {cost!r}. A plan claiming {cost_basis!r} has a number, or it is "
            f"{UNKNOWN!r}")
    if latency_seconds is not None and not _is_amount(latency_seconds):
        problems.append(
            f"{where}: latency_seconds is {latency_seconds!r}; a duration or nothing")
    if not isinstance(reasons, (list, tuple)) or not all(
            isinstance(r, str) and r.strip() for r in reasons):
        problems.append(f"{where}: reasons must be a list of non-empty strings")
    elif not reasons:
        problems.append(
            f"{where}: gives no reason. A plan nobody has to justify is a default wearing the "
            f"shape of a candidate")
    return problems


@dataclasses.dataclass(frozen=True)
class Plan:
    """One way of producing the assurance, and what it would cost to run.

    `guarantees` is what this plan *achieves*, named in the same vocabulary the floor uses, so
    "does it clear the floor" is a comparison rather than an interpretation. `cost` is `None`
    exactly when `cost_basis` is `unknown`: a price nobody knows is not a price with a number
    next to it, and the pair is checked together so neither can be read without the other.
    """

    id: str
    guarantees: tuple
    cost: float | None
    cost_basis: str
    latency_seconds: float | None = None
    reasons: tuple = ()

    def __post_init__(self) -> None:
        problems = plan_problems(self.id, self.guarantees, self.cost, self.cost_basis,
                                 self.latency_seconds, self.reasons)
        if problems:
            raise ValueError("\n  ".join(problems))
        object.__setattr__(self, "guarantees", tuple(self.guarantees))
        object.__setattr__(self, "reasons", tuple(self.reasons))

    def as_dict(self) -> dict:
        return {"id": self.id, "guarantees": list(self.guarantees), "cost": self.cost,
                "cost_basis": self.cost_basis, "latency_seconds": self.latency_seconds,
                "reasons": list(self.reasons)}


#: The keys a plan may carry, derived so the set `validate` accepts, the one `load` reads and
#: the ones compared cannot drift apart.
PLAN_FIELDS = frozenset(f.name for f in dataclasses.fields(Plan))

#: The keys the document itself may carry.
DOCUMENT_KEYS = frozenset({"schema", "task", "plans"})


@dataclasses.dataclass(frozen=True)
class Budget:
    """What the caller will spend, and what the assurance must include whatever it costs.

    `required` is the floor: names a plan's `guarantees` must contain, built from the policy
    and the assurance target rather than read out of the plans. It has no default, because
    "the caller did not supply the floor" and "the policy requires nothing" are the same value
    with a default and opposite answers without one — and the first of them, read as the
    second, selects a plan that produces no assurance at all.

    `task` likewise: a budget carries the floor, so a budget prepared for a wording change
    applied to an authentication change is a weaker floor arriving by mispairing.

    `max_cost` and `max_latency_seconds` are `None` when the caller states no limit — absent is
    "no limit stated", and zero is a limit of zero.
    """

    required: frozenset
    #: The change these were chosen for, compared against what the record says it planned.
    task: str
    max_cost: float | None = None
    max_latency_seconds: float | None = None
    optimisation: str = CHEAPEST
    #: How many seconds of waiting one unit of cost is worth. Required by `balanced` and
    #: meaningless without it: adding money to seconds is a category error, and the exchange
    #: rate between them is the caller's judgement about their own situation rather than
    #: something this module can supply.
    seconds_per_unit_cost: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "required", frozenset(self.required)
                           if isinstance(self.required, (frozenset, set)) else self.required)
        if not isinstance(self.required, frozenset):
            raise ValueError(
                f"budget: required is {self.required!r}, not a set of guarantee names. "
                f"Anything that iterates would otherwise become a floor nobody wrote")
        bad = sorted(repr(item) for item in self.required if not _is_name(item))
        if bad:
            raise ValueError(
                f"budget: required contains {', '.join(bad)}, which does not name a guarantee")
        for name in ("max_cost", "max_latency_seconds"):
            value = getattr(self, name)
            if value is not None and not _is_amount(value):
                raise ValueError(
                    f"budget: {name} is {value!r}; a limit or nothing. Absent is 'no limit "
                    f"stated' and zero is a limit of zero")
        if self.optimisation not in OPTIMISATIONS:
            raise ValueError(
                f"budget: optimisation {self.optimisation!r} is not one of "
                f"{', '.join(OPTIMISATIONS)}")
        if self.seconds_per_unit_cost is not None and not (
                _is_amount(self.seconds_per_unit_cost) and self.seconds_per_unit_cost > 0):
            raise ValueError(
                f"budget: seconds_per_unit_cost is {self.seconds_per_unit_cost!r}; a positive "
                f"rate or nothing")
        if self.optimisation == BALANCED and self.seconds_per_unit_cost is None:
            raise ValueError(
                f"budget: {BALANCED!r} needs seconds_per_unit_cost — how many seconds of "
                f"waiting one unit of cost is worth. Balancing money against time without "
                f"saying what an hour is worth is adding dollars to seconds")
        if self.optimisation != BALANCED and self.seconds_per_unit_cost is not None:
            # Accepting and ignoring it would answer a caller who misspelled `balanced` with a
            # selection instead of the question they meant to ask.
            raise ValueError(
                f"budget: seconds_per_unit_cost is set and optimisation is "
                f"{self.optimisation!r}, which does not use it. A rate nothing reads is a "
                f"caller asking for something other than what they got")
        if not _is_name(self.task):
            raise ValueError(
                f"budget: task is {self.task!r}. A budget carries the floor, so one prepared "
                f"for a wording change applied to an authentication change is a weaker floor "
                f"arriving by mispairing")


#: Why a plan is not a candidate. Named rather than described so a caller can branch: "it does
#: not do enough" and "we cannot tell what it costs" call for different next moves.
BELOW_FLOOR = "does-not-produce-the-required-assurance"
OVER_BUDGET = "costs-more-than-the-budget"
TOO_SLOW = "takes-longer-than-the-budget-allows"
PRICE_UNKNOWN = "cost-is-unknown-so-it-cannot-be-compared"


def excluded(plan: Plan, budget: Budget) -> list[dict]:
    """Every reason this plan is not a candidate, not the first one.

    The floor is checked first and separately from the money, because the two mean different
    things to whoever reads the answer: a plan that costs too much might be affordable
    tomorrow, and a plan that does not produce the required assurance is not a cheaper way of
    doing this at all.
    """
    reasons: list[dict] = []
    missing = sorted(budget.required - set(plan.guarantees))
    if missing:
        reasons.append({
            "reason": BELOW_FLOOR, "plan": plan.id, "missing": missing,
            "detail": f"{plan.id!r} does not produce {', '.join(missing)}. That is not a "
                      f"cheaper way of doing this; it is a different, smaller thing",
        })
    if plan.cost_basis == UNKNOWN:
        reasons.append({
            "reason": PRICE_UNKNOWN, "plan": plan.id,
            "detail": f"{plan.id!r} cannot say what it costs. Comparing it as though the "
                      f"answer were zero makes the plan that knows least about itself the "
                      f"cheapest one on the list",
        })
    elif budget.max_cost is not None and plan.cost > budget.max_cost:
        reasons.append({
            "reason": OVER_BUDGET, "plan": plan.id,
            "detail": f"{plan.id!r} costs {plan.cost} and the budget is {budget.max_cost}",
        })
    if budget.max_latency_seconds is not None and (
            plan.latency_seconds is None
            or plan.latency_seconds > budget.max_latency_seconds):
        reasons.append({
            "reason": TOO_SLOW, "plan": plan.id,
            "detail": f"{plan.id!r} takes {plan.latency_seconds} and the budget allows "
                      f"{budget.max_latency_seconds}"
                      if plan.latency_seconds is not None else
                      f"{plan.id!r} cannot say how long it takes, and this budget has a limit "
                      f"to compare it against",
        })
    return reasons


def validate(payload: dict) -> list[str]:
    """Every way this is not a set of candidate plans, not the first one."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"plans: expected an object, got {type(payload).__name__}"]

    unknown_root = sorted(set(payload) - DOCUMENT_KEYS)
    if unknown_root:
        problems.append(
            f"plans: {', '.join(repr(k) for k in unknown_root)} is not part of {SCHEMA}. A key "
            f"this schema does not define would be dropped rather than honoured")
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")
    if not _is_name(payload.get("task")):
        problems.append(
            "task: the record does not say which change these plans are for. Two sets of plans "
            "for different changes are indistinguishable without it")

    plans = payload.get("plans")
    if not isinstance(plans, list):
        problems.append("plans: expected a list")
        plans = []
    elif not plans:
        # An empty list is not "the budget was met by everything"; it is nobody proposing
        # anything, and the answer to it is exhaustion rather than success.
        problems.append("plans: no plan was proposed; there is nothing to choose between")

    seen: set = set()
    for position, item in enumerate(plans):
        where = f"plans[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        unknown = sorted(set(item) - PLAN_FIELDS)
        if unknown:
            problems.append(f"{where}: {', '.join(repr(k) for k in unknown)} is not part of "
                            f"a plan")
        plan_id = item.get("id")
        if _is_name(plan_id):
            if plan_id in seen:
                problems.append(
                    f"{where}: {plan_id!r} is proposed more than once. Two plans under one name "
                    f"is two answers to what was chosen")
            else:
                seen.add(plan_id)
        problems.extend(plan_problems(
            plan_id, item.get("guarantees"), item.get("cost"), item.get("cost_basis"),
            item.get("latency_seconds"), item.get("reasons"), where))
    return problems


def load(payload: dict) -> tuple[Plan, ...]:
    """Build the plans from a validated record. Raises `ValueError` if it is not one."""
    problems = validate(payload)
    if problems:
        raise ValueError("not a set of candidate plans:\n  " + "\n  ".join(problems))
    return tuple(Plan(**{name: item[name] for name in PLAN_FIELDS if name in item})
                 for item in payload["plans"])


def _rank(plan: Plan, budget: Budget) -> tuple:
    """The order key. Only reached by plans that already clear the floor.

    Latency sorts `None` last rather than first: a plan that cannot say how long it takes is
    not the quickest one, for the same reason an unknown price is not the cheapest. The plan id
    breaks ties, so two readings of one record cannot look like two different decisions.
    """
    slowest = float("inf") if plan.latency_seconds is None else plan.latency_seconds
    if budget.optimisation == CHEAPEST:
        return (plan.cost, slowest, plan.id)
    if budget.optimisation == FASTEST:
        return (slowest, plan.cost, plan.id)
    # Seconds converted into cost at the rate the caller stated, so the two are added in one
    # unit rather than in none.
    combined = (float("inf") if slowest == float("inf")
                else plan.cost + slowest / budget.seconds_per_unit_cost)
    return (combined, plan.cost, plan.id)


#: What `select` answers.
SELECTED, EXHAUSTED, REFUSED = "selected", "exhausted", "refused"


def select(payload: dict, budget: Budget) -> dict:
    """The best-ranked plan that produces the required assurance, or why there is not one.

    Nothing here decides that a plan is *good*. It says which of the candidates the caller was
    willing to pay for still produces what the policy requires, and picks among those by the
    dial the caller set. When none does, it says that too — `exhausted` is an answer somebody
    has to act on, and the ways of acting on it are named so that "we lowered the target" cannot
    be one of them by accident.
    """
    problems = validate(payload)
    if problems:
        # `answers` is empty on a refusal, and that is the point. Refusing says the record
        # could not be read; it does not say no affordable plan clears the floor. Offering
        # "relax the target" to someone whose file was malformed is a fail-open with a helpful
        # tone.
        return {"schema": SCHEMA, "status": REFUSED, "selected": None, "task": None,
                "excluded": [{"reason": "invalid-record", "detail": p} for p in problems],
                "answers": []}

    if payload["task"] != budget.task:
        return {"schema": SCHEMA, "status": REFUSED, "selected": None, "task": payload["task"],
                "excluded": [{"reason": "budget-was-chosen-for-another-task",
                              "detail": f"the record plans {payload['task']!r} and this budget "
                                        f"was chosen for {budget.task!r}. A budget selected for "
                                        f"a different change says nothing about this one"}],
                "answers": []}

    plans = load(payload)
    ruled_out: list[dict] = []
    candidates: list[Plan] = []
    for plan in plans:
        reasons = excluded(plan, budget)
        ruled_out.extend(reasons)
        if not reasons:
            candidates.append(plan)

    if not candidates:
        # Not a downgrade and not a silent one: the floor stands, and what is left is a
        # decision somebody makes and records.
        return {"schema": SCHEMA, "status": EXHAUSTED, "selected": None,
                "task": payload["task"], "excluded": ruled_out,
                "answers": list(EXHAUSTION_ANSWERS)}

    chosen = min(candidates, key=lambda plan: _rank(plan, budget))
    return {"schema": SCHEMA, "status": SELECTED, "selected": chosen.as_dict(),
            "task": payload["task"], "excluded": ruled_out,
            "answers": []}


#: The keys a budget document may carry. Closed for the reason a plan's are.
BUDGET_KEYS = frozenset({"required", "max_cost", "max_latency_seconds", "optimisation",
                         "seconds_per_unit_cost", "task"})


def load_budget(payload: object) -> Budget:
    """A `Budget`, or a refusal.

    Shape here and contents in `Budget`: a JSON object becomes its keys and a string its
    characters, and only this layer can still see that. What is *in* the floor is the
    dataclass's to judge, so a caller building one directly gets the same answer.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"budget: expected an object, got {type(payload).__name__}")
    unknown = sorted(set(payload) - BUDGET_KEYS)
    if unknown:
        raise ValueError(
            f"budget: {', '.join(repr(k) for k in unknown)} is not part of a budget. A key this "
            f"schema does not define would be dropped rather than honoured")
    for name in ("required", "task"):
        if name not in payload:
            raise ValueError(
                f"budget: {name!r} is not stated. Leaving it out would mean "
                + ("the policy requires nothing" if name == "required"
                   else "this budget applies to any change")
                + ", which is not what a caller who forgot it meant")
    required = payload["required"]
    if not isinstance(required, list):
        raise ValueError(
            f"budget: 'required' must be an array of guarantee names, got "
            f"{type(required).__name__}. Anything that iterates would otherwise become a floor "
            f"nobody wrote")
    return Budget(required=frozenset(required), task=payload["task"],
                  max_cost=payload.get("max_cost"),
                  max_latency_seconds=payload.get("max_latency_seconds"),
                  optimisation=payload.get("optimisation", CHEAPEST),
                  seconds_per_unit_cost=payload.get("seconds_per_unit_cost"))


#: What `budget-plan` returns. `1` covers an invalid record, a mismatched budget and an
#: exhausted one: none of them is a plan the caller may run.
CHOSEN, NO_PLAN, EXECUTION_ERROR = 0, 1, 2


def cmd_budget_plan(args) -> "NoReturn":  # noqa: F821
    """Choose among candidate plans that clear the assurance floor.

    Exits rather than returns, for the reason `cmd_synthesis` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that returned
    `1` would print its reasons and leave the shell believing a plan had been chosen.
    """
    import json
    import pathlib
    import sys

    from .synthesis import _no_duplicate_keys

    def _no_constants(token: str):
        """`Infinity` and `NaN` are not JSON; Python's decoder accepts them anyway.

        Refused here as well as in `_is_amount`, so a caller reading the error learns the file
        is not JSON rather than that some field was out of range.
        """
        raise ValueError(f"{token} is not a number JSON defines; write a value or leave the "
                         f"field out")

    try:
        payload = json.loads(pathlib.Path(args.plans).read_text(encoding="utf-8"),
                             object_pairs_hook=_no_duplicate_keys("plans"),
                             parse_constant=_no_constants)
        budget = load_budget(json.loads(pathlib.Path(args.budget).read_text(encoding="utf-8"),
                                        object_pairs_hook=_no_duplicate_keys("budget"),
                                        parse_constant=_no_constants))
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    result = select(payload, budget)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"budget: {result['status']}"
              + (f" — {result['selected']['id']}" if result["selected"] else ""))
        for item in result["excluded"]:
            print(f"    {item['reason']}: {item['detail']}")
        if result["status"] == EXHAUSTED:
            print(f"  nothing affordable clears the floor. Someone decides: "
                  f"{', '.join(result['answers'])}")
    sys.exit(CHOSEN if result["status"] == SELECTED else NO_PLAN)
