"""An autonomous developer may decide how to pursue a goal. Not whether its own result
is trustworthy (#431).

A loop that researches, plans, implements, tests, reviews and repairs is a good way to
reach a result and a terrible way to judge one: every signal it would judge itself by is
a signal it produced. So the loop's decisions stay with the agent running it, and what
lives here is the part a gate can check — when the loop must stop, and whether what it
hands over can be verified by something other than itself.

**It does not run the loop.** Choosing what to research, how to repair, what to plan
next: reading, judging and concluding, which is an agent's work. A module that called a
model to do it would leave nothing a gate could check and nothing a mutation could
falsify. Cycles arrive as a record of what happened, and what lives here is the schema,
the stop judgement, and the refusals.

**It stops the loop on evidence, not on the loop's own account of itself.** A repair
loop with no bound is a way to spend a budget without reaching a result, and "I am making
progress" is exactly the judgement a stuck loop gets wrong. So the three stop conditions
are computed from the record: how many cycles have run, whether the same failure keeps
coming back, and whether the work product has changed at all.

**A bound that has been passed stays passed.** The runs are counted anywhere in the record and
not at the end of it: a loop that ran past a bound and then produced one different cycle did
not un-run past it, and reading only the trailing run would let it clear the evidence by
continuing — the one move the bound exists to prevent.

**Two of those three are only as good as what the loop can author, and the record says which.**
`product` must be spelled like a git object id. That stops a counter standing in for work and
proves nothing on its own — 40 hex characters is a spelling — and existence proves little more,
because a stuck loop can name a different object that was already in the repository every
cycle. Ancestry of the delivered commit is not enough either — that history has no lower
bound, so a loop can name commits that predate the task. So what `must_stop` asks of a
`History` is two questions: is this commit **inside this task's range** (a descendant of the
base the receipt records, an ancestor of the head it points at), and does each cycle's commit
**build on the one before it**. A borrowed object fails the first; a shuffled or repeated set
of real commits fails the second.

That is a chain inside a range, and it is worth being exact about what a chain is evidence of:
the record describes commits that lead from where this task started to what it delivered, in
order. It does not establish that the loop occupied those states — a loop could build the chain
after the fact — and nothing here says it did.

Without a `History` the bound falls back to the loop's own account, and the result carries
`products_related` so a reader is told which answer they got rather than left to assume the
stronger one. The command supplies one; a caller that cannot is told it did not.

`failure` cannot be constrained at all: canonicalising a test failure is reading structured
output this module never sees, so a loop that appends a nonce to the same failure every cycle
defeats that bound. It is a backstop against a loop that is honestly stuck, not a control
against one that is not, and nothing downstream should read it as the latter.

**Nothing here says the result is good, or even finished.** What an admissible handoff says
is narrower and worth stating exactly: the loop declared itself done, it is not observably
stuck, and what it points at is a fixed object that matches what its last cycle produced.
Whether the change is any good is the acceptance gate's answer, and a "converged" this module
cannot check is a word it should not use.

**A developer's PASS is not an assurance PASS.** The loop's own `tests passed` and
`review passed` are recorded as what they are — the developer's account — under a key
that cannot be mistaken for a gate verdict, because the schema has no field for one.
What accepts a change is `build_acceptance` and the receipt, and neither of them reads
this document.

**A handoff and its record name the same commit, or it is refused.**
`assurance.py` already draws half the line: a receipt about a commit git can resolve
describes a fixed object, and one about a branch name describes whatever that name points at
today. That is read from the receipt rather than re-derived, so there is one answer to "what
was verified" and not two. The other half is that the record and the receipt have to be about the
same object: a record paired with some other immutable commit on the same task would describe
work nobody in it did. That the loop *made* the commit is not something either half
establishes, and this module does not say it did.
"""

from __future__ import annotations

import dataclasses
import re

SCHEMA = "rig.development-cycles/v1"

#: A git object id: 40 hex for sha-1, 64 for sha-256. Both, because a repository can be
#: either and hard-coding 40 would refuse a sha-256 repository's honest answer.
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

#: What a cycle was doing. Closed, because a state nobody defined would be accepted,
#: dropped, and leave a reader believing the loop reported something it did not.
RESEARCH = "research"
PLAN = "plan"
IMPLEMENT = "implement"
TEST = "test"
REVIEW = "review"
REPAIR = "repair"
REPLAN = "re-plan"
STATES = (RESEARCH, PLAN, IMPLEMENT, TEST, REVIEW, REPAIR, REPLAN)

#: Where a loop ends up. Not states a cycle can be *in* — a cycle is work, and these are
#: what the work concluded — so they are a separate vocabulary and a cycle claiming one
#: is refused.
READY_FOR_ASSURANCE = "ready-for-assurance"
BLOCKED = "blocked"
OUTCOMES = (READY_FOR_ASSURANCE, BLOCKED)

#: Why the loop must stop. Each is computed from the record; none is the loop's opinion.
MAX_CYCLES = "max-cycles-reached"
PRODUCT_UNRELATED = "product-is-outside-this-task"
PRODUCTS_NOT_A_CHAIN = "products-do-not-build-on-each-other"
REPEATED_FAILURE = "repeated-failure"
NO_PROGRESS = "no-progress"
ESCALATION_REQUIRED = "escalation-required"
STOP_REASONS = (MAX_CYCLES, PRODUCT_UNRELATED, PRODUCTS_NOT_A_CHAIN, REPEATED_FAILURE,
                NO_PROGRESS, ESCALATION_REQUIRED)

#: What a human is being asked to decide. A loop that escalates without saying which of
#: these it hit gives the human the same problem the loop had.
DESTRUCTIVE = "destructive-operation"
AMBIGUOUS = "ambiguous-requirement"
POLICY_APPROVAL = "policy-requires-approval"
BUDGET = "budget-exhausted"
CAPABILITY = "capability-missing"
ESCALATIONS = (DESTRUCTIVE, AMBIGUOUS, POLICY_APPROVAL, BUDGET, CAPABILITY)


@dataclasses.dataclass(frozen=True)
class Cycle:
    """One turn of the loop, and the commit it ended at.

    `product` is that commit. Commits rather than trees because the relationships that make
    the record checkable — inside this task's range, building on the previous cycle — are
    relationships between commits. It is what makes no-progress a fact rather than an
    impression: two cycles ending at the same commit changed nothing, however much happened in
    between, and the loop saying otherwise is exactly the judgement it gets wrong when stuck.
    Requiring the *shape* of an object id stops a counter standing in for work; whether the
    commit belongs to this task is a question this module cannot answer, which is why
    `must_stop` takes a `History` and says whether it had one.

    `failure` is a signature, not prose — the same failing test, the same error class —
    because "did this fail the same way again" has to be answerable by comparison. Prose
    belongs in `rationale`, which nothing compares and a human reads.
    """

    index: int
    state: str
    product: str
    failure: str | None = None
    rationale: str = ""
    producer: str = ""

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


#: The keys a cycle may carry, derived so that the set `validate` accepts, the one `load`
#: reads and the ones compared cannot drift apart.
CYCLE_FIELDS = frozenset(f.name for f in dataclasses.fields(Cycle))

#: The keys the document itself may carry.
DOCUMENT_KEYS = frozenset({"schema", "task", "goal", "cycles", "outcome", "self_reported",
                           "escalation"})

#: What the loop says about its own work. Kept under one key, and *not* merged into
#: anything a gate reads: `build_acceptance` and the receipt decide acceptance, and
#: neither of them reads this document. A schema with a `gate` or an `accepted` field
#: would be an invitation to write the developer's verdict where a gate's belongs.
SELF_REPORTED_KEYS = frozenset({"tests", "review", "note"})

_FORBIDDEN_SELF_REPORTED = ("gate", "gates", "accepted", "accept", "verdict", "assurance",
                            "approved", "final_status")


def validate(payload: dict) -> list[str]:
    """Every way this is not a cycle log, not the first one."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"cycles: expected an object, got {type(payload).__name__}"]

    unknown_root = sorted(set(payload) - DOCUMENT_KEYS)
    if unknown_root:
        problems.append(
            f"cycles: {', '.join(repr(k) for k in unknown_root)} is not part of {SCHEMA}. A "
            f"key this schema does not define would be dropped rather than honoured")

    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")

    if not (isinstance(payload.get("task"), str) and payload["task"].strip()):
        problems.append(
            "task: the record does not say which task it is about. Two loops pursuing "
            "different goals that end at the same commit are indistinguishable without it")

    if not (isinstance(payload.get("goal"), str) and payload["goal"].strip()):
        problems.append("goal: a loop with no goal has nothing to be finished against")

    cycles = payload.get("cycles")
    if not isinstance(cycles, list):
        problems.append("cycles: expected a list")
        cycles = []
    elif not cycles:
        # A loop that ran nothing produced nothing, and a stop judgement over an empty
        # record would report "no failures" — which reads as success.
        problems.append("cycles: a loop with no cycles ran nothing; there is no work to "
                        "hand over and nothing to judge it by")

    for position, item in enumerate(cycles):
        where = f"cycles[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        unknown = sorted(set(item) - CYCLE_FIELDS)
        if unknown:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in unknown)} is not part of a cycle")
        # `type(...) is int`, because `False == 0` and `True == 1`: a bool index would pass
        # a comparison against the first two positions while being a different kind of thing.
        if type(item.get("index")) is not int or item["index"] != position:
            problems.append(
                f"{where}: index is {item.get('index')!r}. Cycles are ordered and the order "
                f"is what 'the same failure again' and 'nothing changed' are computed over, "
                f"so a record that does not say where it sits cannot be compared")
        state = item.get("state")
        if state in OUTCOMES:
            problems.append(
                f"{where}: {state!r} is what a loop concluded, not work a cycle did. A cycle "
                f"claiming it would put the loop's own verdict in the record of its work")
        elif state not in STATES:
            problems.append(f"{where}: state {state!r} is not one of {', '.join(STATES)}")
        product = item.get("product")
        if not isinstance(product, str) or not OBJECT_ID.fullmatch(product):
            problems.append(
                f"{where}: product {product!r} is not a git object id. Whether the loop is "
                f"making progress is a question about what changed, and any string it liked "
                f"would let a counter stand in for work")
        failure = item.get("failure")
        if failure is not None and not (isinstance(failure, str) and failure.strip()):
            problems.append(
                f"{where}: failure is {failure!r}. Absent means the cycle did not fail; a "
                f"blank means it failed in a way nothing can compare to the last one")

    reported = payload.get("self_reported")
    if reported is not None:
        if not isinstance(reported, dict):
            problems.append(f"self_reported: expected an object, got {type(reported).__name__}")
        else:
            smuggled = sorted(k for k in reported if k.lower() in _FORBIDDEN_SELF_REPORTED)
            if smuggled:
                problems.append(
                    f"self_reported: {', '.join(repr(k) for k in smuggled)} would record the "
                    f"loop's own verdict where a gate's belongs. What accepts a change is the "
                    f"acceptance gate and the receipt, and neither of them reads this document")
            unknown = sorted(set(reported) - SELF_REPORTED_KEYS - set(smuggled))
            if unknown:
                problems.append(
                    f"self_reported: {', '.join(repr(k) for k in unknown)} is not part of a "
                    f"developer's own account")

    outcome = payload.get("outcome")
    if outcome is not None and outcome not in OUTCOMES:
        problems.append(
            f"outcome: {outcome!r} is not one of {', '.join(OUTCOMES)}. Absent means the loop "
            f"is still running, which is a third thing and not a way to be finished")

    escalation = payload.get("escalation")
    if escalation is not None and escalation not in ESCALATIONS:
        problems.append(
            f"escalation: {escalation!r} is not one of {', '.join(ESCALATIONS)}. A loop that "
            f"escalates without saying which of these it hit hands the human the same problem "
            f"it had")
    return problems


def load(payload: dict) -> tuple[Cycle, ...]:
    """Build the cycles from a validated log. Raises `ValueError` if it is not one."""
    problems = validate(payload)
    if problems:
        raise ValueError("not a cycle log:\n  " + "\n  ".join(problems))
    return tuple(Cycle(**{name: item[name] for name in CYCLE_FIELDS if name in item})
                 for item in payload["cycles"])


@dataclasses.dataclass(frozen=True)
class Limits:
    """What the loop is allowed to spend before someone else decides.

    Defaults rather than a required argument, because a caller that has not thought about
    bounds still gets bounds — an unbounded repair loop is the failure mode this exists to
    prevent, and making the caller opt in to being bounded gets it wrong by omission.
    """

    max_cycles: int = 12
    repeated_failure: int = 3
    no_progress: int = 3

    def __post_init__(self) -> None:
        for name in ("max_cycles", "repeated_failure", "no_progress"):
            value = getattr(self, name)
            # `True` is an `int` in Python and would set a limit of 1 while reading as
            # "enabled"; a limit below 1 disables the bound while looking like a setting.
            if value is True or value is False or not isinstance(value, int) or value < 1:
                raise ValueError(
                    f"{name}={value!r}: a bound has to be a whole number of cycles, at least "
                    f"one. Anything else is an unbounded loop wearing a limit's name")


def _longest_run(values: list) -> tuple:
    """The longest consecutive run of one value, as `(length, value)`.

    Anywhere in the record, not at the end of it. A bound says the loop must stop; a loop that
    ran past one and then produced a different cycle did not un-run past it, and looking only
    at the trailing run would let it clear the evidence by continuing. `x, x, x, None` is a
    loop that was required to stop after the third cycle and took a fourth.
    """
    best = (0, None)
    run = 0
    for position, value in enumerate(values):
        run = run + 1 if position and value == values[position - 1] else 1
        # Strictly greater, so two runs of equal length report the earlier one. Either would
        # be the same stop reason, but a message that changed with a later tie would make two
        # runs of the same record read as two different problems.
        if run > best[0]:
            best = (run, value)
    return best


@dataclasses.dataclass(frozen=True)
class History:
    """The two questions about commits this module cannot answer for itself.

    `within(commit)` — is it inside this task's range, between the base the receipt records
    and the head it points at. `advances(earlier, later)` — does `later` build on `earlier`.
    Two callables rather than a repository handle, so a test states a history instead of
    needing one, and so nothing here acquires an opinion about where a repository lives.
    """

    within: object
    advances: object


def must_stop(cycles: tuple[Cycle, ...], limits: Limits | None = None,
              history: History | None = None) -> list[dict]:
    """Every reason this loop may not run another cycle, not the first one.

    All three are computed from the record. None of them asks the loop how it thinks it is
    doing, because "I am making progress" is the judgement a stuck loop gets wrong, and a
    bound the loop can talk its way past is not a bound.

    Returning every reason rather than the first is the same rule `validate` follows: a
    loop told only "max cycles" would raise the limit and hit the repeated failure it was
    always going to hit. And a reason found in the middle of the record is as much a reason as
    one found at the end — the question is whether the loop was ever required to stop, not
    whether it happens to be stuck right now.

    `history` answers the two questions about commits this module cannot: whether one is
    inside this task's range, and whether one builds on another. Passed in rather than reached
    for, so this module keeps no opinion about where the repository is and a test can state a
    history rather than need one.

    Neither question is optional in the way it might look. Existence is not enough — a stuck
    loop can name a different object that was already there every cycle. Ancestry of the
    delivered commit is not enough either — that history has no lower bound, so the loop can
    reach back past the task. A chain inside a range is what is left, and it is what a loop
    cannot produce by borrowing.
    """
    limits = limits or Limits()
    reasons: list[dict] = []

    if history is not None:
        outside = sorted({c.product for c in cycles if not history.within(c.product)})
        if outside:
            reasons.append({
                "reason": PRODUCT_UNRELATED,
                "detail": f"{', '.join(outside)} is not between where this task started and "
                          f"what it delivered. Progress computed over commits from outside it "
                          f"is about someone else's work",
            })
        # Only over the cycles that are inside the range: a break reported for a commit
        # already refused above would be a second complaint about one problem.
        broken = [f"{a.product} → {b.product}"
                  for a, b in zip(cycles, cycles[1:])
                  if a.product not in outside and b.product not in outside
                  and a.product != b.product and not history.advances(a.product, b.product)]
        if broken:
            reasons.append({
                "reason": PRODUCTS_NOT_A_CHAIN,
                "detail": f"{'; '.join(broken)} — the later commit does not build on the "
                          f"earlier one. A set of real commits in no particular order is what "
                          f"borrowing looks like once borrowing from outside is refused",
            })

    if len(cycles) >= limits.max_cycles:
        reasons.append({
            "reason": MAX_CYCLES,
            "detail": f"{len(cycles)} cycle(s) have run and the limit is {limits.max_cycles}",
        })

    # All the cycles, not the failures filtered out of them: dropping the successes turns
    # "x, recovered, x" into two consecutive identical failures, which is a loop working
    # through a regression rather than one stuck on it.
    repeats, failure = _longest_run([c.failure for c in cycles])
    if failure is not None and repeats >= limits.repeated_failure:
        reasons.append({
            "reason": REPEATED_FAILURE,
            "detail": f"{failure!r} came back {repeats} time(s) in a row; the limit is "
                      f"{limits.repeated_failure}. A repair that keeps meeting the same "
                      f"failure is not repairing it, and a later cycle does not undo having "
                      f"run past the bound",
        })

    stalled, _ = _longest_run([c.product for c in cycles])
    if stalled >= limits.no_progress:
        reasons.append({
            "reason": NO_PROGRESS,
            "detail": f"{stalled} cycle(s) in a row ended at the same commit; the limit is "
                      f"{limits.no_progress}. Whatever happened in between, nothing changed, "
                      f"and a later cycle does not undo having run past the bound",
        })
    return reasons


#: What `handoff` answers. `refused` is not a failure of the loop — a loop that stopped
#: because a human has to decide something did the right thing — it is a statement that
#: nothing may be accepted on the strength of this document.
ADMISSIBLE, REFUSED = "admissible", "refused"

#: Why a handoff was refused, beyond the stop reasons. Named rather than described so a
#: caller can branch on them: "still running" and "points at the wrong thing" call for
#: different next moves.
NOT_DECLARED_DONE = "not-declared-done"
NOT_THIS_TASK = "record-is-about-another-task"
NOT_THIS_GOAL = "record-restates-the-goal"
TARGET_NOT_IMMUTABLE = "target-not-immutable"
TARGET_NOT_THE_LOOPS = "target-is-not-what-the-loop-produced"


def handoff(payload: dict, receipt: dict, limits: Limits | None = None,
            history: History | None = None) -> dict:
    """Whether what the record points at can be handed to something that verifies it.

    `receipt` is `assurance.build_receipt`'s result (#428) — the whole thing, because the
    target answers what was verified and `task.id` answers what it was verified for.

    Five things, and only five. The record is **about this task, and about the goal the task
    was given** — a loop free to restate the goal decides what "done" was measured against,
    which is the decision this boundary exists to reserve. The
    loop **declared itself done**: absent is still running, which is not a way to be finished.
    It is **not observably stuck** by the bounds that say so. And what the receipt points at is
    a **fixed object that matches the loop's last product**, because a record paired with some
    other immutable commit on the same task would describe work nobody in it did.

    None of that says the result is good. An admissible handoff means a verifier can be
    pointed at a specific thing and its verdict will be about that thing; whether the change
    is acceptable is the acceptance gate's answer, and this document is not an input to it.
    """
    problems = validate(payload)
    if problems:
        return {
            "schema": SCHEMA,
            "status": REFUSED,
            "reasons": [{"reason": "invalid-record", "detail": p} for p in problems],
            "target": None,
            "products_related": history is not None,
            "self_reported": None,
        }

    cycles = load(payload)
    reasons = [r for r in must_stop(cycles, limits, history) if r["reason"] != MAX_CYCLES]
    # Reaching the cycle limit is a reason to stop, not a reason to throw the work away:
    # the work still exists and something else still has to judge it. The other two say
    # the loop was not converging, and handing over a non-converging result as though it
    # were finished is the claim this module refuses to let anyone make.

    task_block = receipt.get("task")
    task_id = task_block.get("id") if isinstance(task_block, dict) else None
    if payload["task"] != task_id:
        reasons.append({
            "reason": NOT_THIS_TASK,
            "detail": f"the record is about {payload['task']!r} and the receipt is for "
                      f"{task_id!r}. Reading one as the other's completion would credit this "
                      f"task with work done somewhere else",
        })

    goal = task_block.get("input") if isinstance(task_block, dict) else None
    if payload["goal"] != goal:
        reasons.append({
            "reason": NOT_THIS_GOAL,
            "detail": f"the record was pursuing {payload['goal']!r} and the task was given "
                      f"{goal!r}. A loop that may restate the goal decides what finished means",
        })

    outcome = payload.get("outcome")
    if outcome != READY_FOR_ASSURANCE:
        reasons.append({
            "reason": NOT_DECLARED_DONE,
            "detail": (f"the loop concluded {outcome!r}; a handoff is for a loop that says it "
                       f"is finished" if outcome else
                       "the loop has not said it is finished — no outcome is recorded, and a "
                       "loop still running has nothing to hand over"),
        })

    escalation = payload.get("escalation")
    if escalation is not None:
        reasons.append({
            "reason": ESCALATION_REQUIRED,
            "detail": f"the loop escalated: {escalation}. A human decides this one, and a "
                      f"handoff would route around them",
        })

    # `isinstance`, not `or {}`: a truthy non-dict — a list, a string — passes `or` and then
    # raises on `.get`, so a malformed receipt would come back as a traceback where the
    # contract says it comes back as a refusal.
    target = receipt.get("target")
    target = target if isinstance(target, dict) else {}
    head = target.get("head")
    head = head if isinstance(head, dict) else {}
    commit = head.get("commit")
    # Identity, not truthiness. `assurance.py` writes a real bool, so anything else means
    # the receipt did not answer this question — and the string `"false"` is truthy, which
    # would read a target that says it is not immutable as one that is.
    if target.get("immutable") is not True or not (isinstance(commit, str)
                                                   and OBJECT_ID.fullmatch(commit)):
        reasons.append({
            "reason": TARGET_NOT_IMMUTABLE,
            "detail": head.get("reason") or (
                f"the head commit {commit!r} cannot be resolved" if commit
                else "no commit is linked; a branch name points at whatever it points at today"),
        })
    elif cycles[-1].product != commit:
        # The half `assurance.py` cannot answer. It knows the commit is fixed; only the loop's
        # record says which fixed thing the loop made, and if the two differ the receipt
        # describes work this record is not about.
        reasons.append({
            "reason": TARGET_NOT_THE_LOOPS,
            "detail": f"the receipt points at {commit}, and the loop's last cycle produced "
                      f"{cycles[-1].product}. One of the two is about someone else's work",
        })

    return {
        "schema": SCHEMA,
        "status": REFUSED if reasons else ADMISSIBLE,
        "reasons": reasons,
        "target": {"commit": commit if isinstance(commit, str) else None,
                   "immutable": target.get("immutable") is True},
        # Said rather than assumed: without a history, "nothing changed" is the loop's own
        # account and not a fact about the commits this task reached.
        "products_related": history is not None,
        # Carried through, never merged into the verdict: a reader learns what the loop
        # said about itself and learns it separately from anything that decided.
        "self_reported": payload.get("self_reported"),
    }


def git_history(root, base: str, head: str) -> History:
    """A `History` backed by this repository, between `base` and `head`.

    Ancestry of the head alone was the earlier and wrong answer: that history has no lower
    bound, so a loop could reach back past the task and name commits that predate it. The
    range is what gives it one, and `advances` is what stops a set of real in-range commits
    reported in no particular order from reading as a sequence of work.

    The one function here that touches anything. Everything else takes what it needs as an
    argument, which is what lets a test state a history rather than need one, and what keeps
    the judgement free of anything a mutation could not reach.
    """
    import subprocess

    def ancestor(earlier: str, later: str) -> bool:
        if earlier == later:
            return True
        # `returncode == 0` and not `<= 0`: a git killed by a signal reports a negative code,
        # and reading that as "yes" would admit whatever the question was about.
        return subprocess.run(["git", "merge-base", "--is-ancestor", earlier, later],
                              cwd=str(root), capture_output=True).returncode == 0

    return History(within=lambda oid: ancestor(base, oid) and ancestor(oid, head),
                   advances=ancestor)


#: What `dev-loop` returns. `1` covers an invalid record and a refused handoff: both mean
#: nothing may be accepted on the strength of this document.
ADMITTED, REJECTED, EXECUTION_ERROR = 0, 1, 2


def cmd_dev_loop(args) -> "NoReturn":  # noqa: F821
    """Judge a loop's record against its bounds, and its handoff against the receipt.

    Exits rather than returns, for the reason `cmd_synthesis` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that
    returned `1` would print its reasons and leave the shell believing the handoff was
    admissible.
    """
    import json
    import pathlib
    import sys

    from . import assurance
    from . import state as state_module
    from .synthesis import _no_duplicate_keys

    try:
        payload = json.loads(pathlib.Path(args.cycles).read_text(encoding="utf-8"),
                             object_pairs_hook=_no_duplicate_keys("cycles"))
        limits = Limits(**{k: v for k, v in (("max_cycles", args.max_cycles),
                                             ("repeated_failure", args.repeated_failure),
                                             ("no_progress", args.no_progress))
                           if v is not None})
        root = state_module.repo_root()
        receipt = assurance.build_receipt(root, state_module.resolve_task_id(root, args.task))
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    # Built from the range the receipt records, so the questions are about this task's work
    # rather than about the repository in general. Without both ends there is no range, and
    # the answer says it was the loop's own account rather than pretending otherwise.
    #
    # `base_commit_effective` first, and it matters which: after a rebase the originally
    # registered base is not in the delivered history at all, so a range starting there would
    # put every product outside it. The effective base is where the delivered work actually
    # begins, which is the question `within` is asking.
    target = receipt.get("target") or {}
    base = target.get("base_commit_effective") or target.get("base_commit")
    commit = (target.get("head") or {}).get("commit")
    history = (git_history(root, base, commit)
               if isinstance(base, str) and isinstance(commit, str) else None)
    result = handoff(payload, receipt, limits, history)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"handoff: {result['status']}"
              + (f" — commit {result['target']['commit']}" if result["target"] else ""))
        for item in result["reasons"]:
            print(f"    {item['reason']}: {item['detail']}")
        if result["self_reported"]:
            # Printed apart from the verdict and after it, because a reader who sees the
            # loop's own account first reads the verdict as agreeing with it.
            print(f"  the loop's own account (not a verdict): {result['self_reported']}")
    sys.exit(ADMITTED if result["status"] == ADMISSIBLE else REJECTED)
