"""A workflow may adapt to the risk. What it is trusted for may not (#432).

A fixed recipe is either too heavy for a wording change or too light for a change to an
authentication boundary, and picking one per task is what a planner is for. But a planner
that can also decide which gates apply has been handed the question it was supposed to be
constrained by — so this module holds the floor, and nothing else.

**It does not classify.** Deciding that a diff touches an authentication boundary is reading,
judging and concluding: an agent's work. A module that called a model to do it would leave
nothing a gate could check and nothing a mutation could falsify. Classification arrives as a
payload, with its reasons, and what lives here is the schema, the validation, and the
refusals.

**It does not let the planner shrink the floor.** `build_acceptance` already states the rule
for gate criteria — a project file or an org policy may *add* to a gate and never take
built-ins away. This is the same rule applied one level up, to which steps a workflow
contains: the mandatory ones are computed from the policy and the operator's instructions,
not read out of the proposal, and a proposal that leaves one out or authors it differently is
corrected and reported rather than quietly accepted. A planner that could drop a step by
omitting it would be deciding what the change is trusted for.

**The floor here is about steps, not about what a step then checks.** A resolved step is an
id, a source and a reason; it carries no criteria, no configuration, no arguments. What a
gate criterion must hold is `build_acceptance`'s to protect and it protects it there, so a
`floor_held` from this module means the workflow contains the mandatory components, attributed
to whoever requires them — not that any of them was configured to check anything in
particular. Saying more than that would be this module vouching for a guarantee it has no way
to inspect.

**It does not accept a component nobody registered.** Synthesis chooses from the catalog that
exists; a step naming something outside it is refused, because "the planner invented a step"
and "the planner selected a step" look identical in a resolved workflow and only one of them
is what this is for.

**It records why, or it refuses.** A selected step carries the reason it was selected and
where that reason came from. A workflow that cannot say why it contains what it contains is
not evidence of anything, and the resolved workflow is meant to be evidence.
"""

from __future__ import annotations

import dataclasses

SCHEMA = "rig.resolved-workflow/v1"

#: What `resolve` returns: the workflow, plus what had to be corrected to get there. A
#: separate schema because it is a report *about* a workflow and not one — labelling it
#: `SCHEMA` would make its own `corrections` a key that schema does not define, and feeding it
#: back in would be refused. `report["workflow"]` is the proposal-shaped half.
REPORT_SCHEMA = "rig.workflow-resolution/v1"

#: The keys a resolved workflow may carry. Closed for the reason a step's keys are.
WORKFLOW_KEYS = frozenset({"schema", "steps"})

#: Where a step's selection came from. The same distinction `intent.py` draws for
#: requirements and `caller.Caller` draws for callers: what someone or something *stated*,
#: versus what the planner concluded on its own.
POLICY_REQUIRED = "policy-required"
RISK_DERIVED = "risk-derived"
TASK_TYPE_DEFAULT = "task-type-default"
OPERATOR_REQUESTED = "operator-requested"
PLANNER_PROPOSED = "planner-proposed"

SOURCES = (POLICY_REQUIRED, RISK_DERIVED, TASK_TYPE_DEFAULT, OPERATOR_REQUESTED,
           PLANNER_PROPOSED)

#: The only sources a floor entry may carry, and therefore the only ones that survive a
#: planner leaving the step out — `policy-required` because a policy requires it,
#: `operator-requested` because a planner deciding a human asked for too much is the same
#: overreach wearing a friendlier name. The other three are conclusions the planner reached
#: on its own, and a floor built from those would be the planner writing its own floor.
MANDATORY_SOURCES = frozenset({POLICY_REQUIRED, OPERATOR_REQUESTED})


@dataclasses.dataclass(frozen=True)
class Step:
    """One step of a resolved workflow, and why it is there.

    `reason` is prose for a human; `source` is the vocabulary a machine compares. Both are
    required, because a step that cannot say why it was selected turns the resolved workflow
    from evidence into a list.
    """

    id: str
    source: str
    reason: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


#: The keys a resolved step may carry, and therefore the ones compared against the floor.
STEP_FIELDS = frozenset(f.name for f in dataclasses.fields(Step))


def validate(payload: dict, catalog: set[str] | frozenset[str]) -> list[str]:
    """Every way this proposal is not a resolved workflow, not the first one.

    `catalog` is the set of step ids that exist — recipes, instruction facets, whatever the
    caller registered. Passed in rather than discovered here so that this module has no
    opinion about where components live, and so a test can state the catalog it means.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"workflow: expected an object, got {type(payload).__name__}"]

    unknown_root = sorted(set(payload) - WORKFLOW_KEYS)
    if unknown_root:
        problems.append(
            f"workflow: {', '.join(repr(k) for k in unknown_root)} is not part of {SCHEMA}. A "
            f"key this schema does not define would be dropped rather than honoured")

    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")

    steps = payload.get("steps")
    if not isinstance(steps, list):
        problems.append("steps: expected a list")
        steps = []
    elif not steps:
        # A workflow of nothing passes every check it contains, which is none.
        problems.append("steps: a workflow with no steps verifies nothing; refuse the task "
                        "instead of resolving it to an empty one")

    seen: set[str] = set()
    for index, item in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        step_id = item.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            problems.append(f"{where}: has no id")
        elif step_id not in catalog:
            # "the planner invented a step" and "the planner selected a step" look identical
            # in a resolved workflow, and only one of them is what synthesis is for.
            problems.append(
                f"{where}: {step_id!r} is not a registered component. Synthesis chooses from "
                f"the catalog that exists; it does not add to it")
        elif step_id in seen:
            problems.append(f"{where}: {step_id!r} appears more than once")
        else:
            seen.add(step_id)
        source = item.get("source")
        if source not in SOURCES:
            problems.append(f"{where}: source {source!r} is not one of {', '.join(SOURCES)}")
        if not (isinstance(item.get("reason"), str) and item["reason"].strip()):
            problems.append(f"{where}: has no reason. A step that cannot say why it was "
                            f"selected makes the workflow a list rather than evidence")
        # Accepting a key and then dropping it is the module deciding a planner did not mean
        # what it wrote. A `skip` or a `mode` nobody reads would ride through validation, be
        # discarded by `load`, and leave `floor_held` true about a step that no longer says
        # what the proposal said it said. Derived from `Step` so a field added there is
        # accepted here without anyone remembering to.
        unknown = sorted(set(item) - STEP_FIELDS)
        if unknown:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in unknown)} is not part of {SCHEMA}. A "
                f"key this schema does not define would be dropped rather than honoured")
    return problems


def load(payload: dict, catalog: set[str] | frozenset[str]) -> tuple[Step, ...]:
    """Build the steps from a validated proposal. Raises `ValueError` if it is not one."""
    problems = validate(payload, catalog)
    if problems:
        raise ValueError("not a resolved workflow:\n  " + "\n  ".join(problems))
    # Keyed off `STEP_FIELDS` rather than spelled out, so a field added to `Step` is read
    # here and accepted by `validate` together. What still has to be written by hand is that
    # field's own rule — that it is a string, that it is non-blank, whatever it needs.
    return tuple(Step(**{name: item[name] for name in STEP_FIELDS})
                 for item in payload["steps"])


@dataclasses.dataclass(frozen=True)
class Required:
    """One step the proposal may not omit, and who requires it.

    A floor of bare ids loses the one thing that makes an omission detectable: once a step is
    gone from the proposal, its source is gone with it, and "the planner dropped what the
    operator asked for" becomes indistinguishable from "nobody asked for it". So the floor
    carries the source, and it is built by the caller from the policy and the operator's
    instructions — never from the proposal, which is the thing being checked.
    """

    id: str
    source: str
    reason: str

    def __post_init__(self) -> None:
        # `resolve` puts these straight into the resolved workflow, so an entry that would
        # not pass `validate` is a way to get an invalid step past validation by putting it
        # on the floor instead of in the proposal. The floor is the stricter document, not
        # the unchecked one.
        if not (isinstance(self.id, str) and self.id.strip()):
            raise ValueError(f"floor entry has no step id: {self!r}")
        if self.source not in MANDATORY_SOURCES:
            raise ValueError(
                f"floor entry {self.id!r} claims source {self.source!r}; a floor may only be "
                f"required by {' or '.join(sorted(MANDATORY_SOURCES))}, because the others are "
                f"conclusions the planner reached on its own")
        if not (isinstance(self.reason, str) and self.reason.strip()):
            raise ValueError(f"floor entry {self.id!r} has no reason it is required")

    def as_step(self) -> "Step":
        return Step(id=self.id, source=self.source, reason=self.reason)


def floor(entries: dict[str, tuple[str, str]] | None = None,
          **policy_reasons: str) -> tuple[Required, ...]:
    """Build a floor. `entries` maps an id to `(source, reason)`; keywords are policy steps.

    The keyword form is the common case — a policy naming steps and why — and the mapping
    form is what carries an operator's request, which has a different source and therefore a
    different meaning downstream: a human's instruction can be withdrawn by that human, and a
    policy requirement cannot.
    """
    built = [Required(id=step_id, source=source, reason=reason)
             for step_id, (source, reason) in sorted((entries or {}).items())]
    built.extend(Required(id=step_id, source=POLICY_REQUIRED, reason=reason)
                 for step_id, reason in sorted(policy_reasons.items()))
    return check_floor(tuple(built))


def check_floor(required: tuple["Required", ...],
                catalog: set[str] | frozenset[str] | None = None) -> tuple["Required", ...]:
    """The floor itself, checked. Returns it unchanged or raises.

    Two ids the same is not a floor requiring a step twice — it is two authorities disagreeing
    about who requires it, and every reader downstream keys by id and would silently keep one
    of them. Which one depends on argument order, so the answer to "may a person withdraw this
    step" would turn on how the caller happened to build the tuple.

    `catalog` is optional because `floor()` is often built before the catalog is loaded; when
    it is given, a floor naming a step nobody registered is refused here rather than restored
    into the workflow, where it would produce a document this module's own `validate` rejects.
    """
    seen: dict[str, Required] = {}
    for item in required:
        if item.id in seen:
            raise ValueError(
                f"floor requires {item.id!r} twice, as {seen[item.id].source!r} and "
                f"{item.source!r}; one authority per step, or the step's provenance depends "
                f"on argument order")
        seen[item.id] = item
    if catalog is not None:
        outside = sorted(item.id for item in required if item.id not in catalog)
        if outside:
            raise ValueError(
                f"floor requires component(s) nobody registered: {', '.join(outside)}. A floor "
                f"may not add to the catalog either")
    return required


def missing_floor(steps: tuple[Step, ...],
                  required: tuple[Required, ...]) -> list[dict]:
    """The mandatory steps this proposal left out, whoever required them.

    Omission is the cheapest way for a planner to answer a question it is supposed to be
    constrained by, and it is invisible unless the floor is held somewhere the planner cannot
    write to.
    """
    present = {step.id for step in steps}
    return [{"id": item.id, "reason": item.reason, "source": item.source}
            for item in required if item.id not in present]


def weakened(steps: tuple[Step, ...],
             required: tuple[Required, ...]) -> list[dict]:
    """Mandatory steps the proposal kept but authored differently.

    Three ways to shrink the floor while appearing to hold it turned up in review, one after
    another: leave the step out; keep it under a different source; keep it under the right
    source and rewrite the reason to something weaker than what the policy said. A check
    written against a list of fields catches the ones on the list, and the fourth arrives with
    whatever field gets added to `Step` next.

    So the comparison is the whole step against the whole floor entry. Nothing on a mandatory
    step is the planner's to author, which is one rule rather than a growing list. Adding a
    field to `Step` extends the comparison, the accepted key set and `load` at once; what still
    has to be written by hand is that field's own validation rule.
    """
    by_id = {item.id: item for item in required}
    return [{"id": step.id, "claimed": step.source, "claimed_reason": step.reason,
             "reason": by_id[step.id].reason, "source": by_id[step.id].source}
            for step in steps if step.id in by_id and step != by_id[step.id].as_step()]


def resolve(payload: dict, catalog: set[str] | frozenset[str],
            required: tuple[Required, ...] = ()) -> dict:
    """The proposal with the floor restored, and a record of what had to be restored.

    Nothing is dropped and nothing is silently corrected: a mandatory step the planner left
    out is added, a mandatory step it attributed to someone else is put back to the source
    that actually requires it, and both corrections are reported. A caller reading only
    `report["workflow"]` gets a workflow that is at least the floor; one reading `report["corrections"]` learns the
    planner tried to go below it, which is the more interesting fact.
    """
    check_floor(required, catalog)
    steps = load(payload, catalog)
    absent = missing_floor(steps, required)
    relabelled = weakened(steps, required)
    by_id = {item.id: item for item in required}

    restored: list[Step] = []
    for step in steps:
        wanted = by_id.get(step.id)
        restored.append(wanted.as_step() if wanted else step)
    restored.extend(by_id[item["id"]].as_step() for item in absent)

    return {
        "schema": REPORT_SCHEMA,
        "workflow": {"schema": SCHEMA, "steps": [step.as_dict() for step in restored]},
        "corrections": {"restored": absent, "relabelled": relabelled},
        "floor_held": not absent and not relabelled,
    }


#: The keys a structured floor entry may carry. Closed for the reason a proposed step's keys
#: are: a `waivable` nobody reads would be accepted, dropped, and leave the caller believing
#: it said something about the floor.
FLOOR_ENTRY_KEYS = frozenset({"source", "reason"})


def _no_duplicate_keys(document: str):
    """A `json.loads` hook that refuses a key given twice, for a named document.

    JSON allows a key twice and `json.loads` keeps the last one, silently. Every check in this
    module compares what was authored against what is required — and a parsed dict has already
    thrown one of the two away, choosing by textual order. The check has to happen where both
    are still present, which is here, and it has to happen for the proposal as much as for the
    floor: a step whose `reason` appears twice reaches the comparison saying only the last one.
    """
    def hook(pairs: list[tuple[str, object]]) -> dict:
        seen: set[str] = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError(
                    f"{document} names {key!r} twice; JSON keeps the last one, so what the "
                    f"document says would depend on the order it happens to be written in")
            seen.add(key)
        return dict(pairs)
    return hook


def _floor_entry(step_id: str, value: object) -> Required:
    """One floor entry from its JSON form: a reason string, or a `source`/`reason` object."""
    if isinstance(value, str):
        return Required(id=step_id, source=POLICY_REQUIRED, reason=value)
    # A structured entry that omits `reason` gets `""`, which `Required` refuses. Defaulting
    # to anything readable would invent prose the file does not contain and then protect it.
    if not isinstance(value, dict):
        # `str(value)` would turn `null` into the reason `"None"` and then protect that as
        # though the prose had been written in the file.
        raise ValueError(
            f"floor entry {step_id!r}: expected a reason string or an object with 'source' and "
            f"'reason', got {type(value).__name__}")
    unknown = sorted(set(value) - FLOOR_ENTRY_KEYS)
    if unknown:
        raise ValueError(
            f"floor entry {step_id!r}: {', '.join(repr(k) for k in unknown)} is not part of a "
            f"floor entry. A key this schema does not define would be dropped rather than "
            f"honoured")
    return Required(id=step_id, source=value.get("source", POLICY_REQUIRED),
                    reason=value.get("reason", ""))


#: What a command failure answers in. Distinct from `REPORT_SCHEMA` because it is not a
#: resolution — nothing was resolved — and labelling it as one would make `status` and `error`
#: keys the resolution schema does not define.
ERROR_SCHEMA = "rig.workflow-resolution-error/v1"


def load_catalog(payload: object) -> frozenset[str]:
    """The registered component ids, or a refusal.

    `set(json.loads(...))` takes whatever iterates. A JSON object becomes its keys, so
    `{"run-arbitrary-shell": false}` registers the component it names while looking like it
    denies it; a JSON string becomes its characters. The catalog is the whole basis for "the
    planner selected a step" rather than "the planner invented one", so what registers a
    component has to be an act of registration and not a shape that merely iterates like one.
    """
    if not isinstance(payload, list):
        raise ValueError(
            f"catalog: expected a JSON array of component ids, got "
            f"{type(payload).__name__}. Anything that iterates would otherwise register "
            f"whatever it happens to yield")
    bad = [item for item in payload if not (isinstance(item, str) and item.strip())]
    if bad:
        raise ValueError(
            f"catalog: {', '.join(repr(item) for item in bad)} is not a component id")
    return frozenset(payload)


#: What `synthesise` returns. `1` covers an invalid proposal and one that went below the
#: floor: both mean the planner produced something that cannot be run as proposed.
RESOLVED, REFUSED, EXECUTION_ERROR = 0, 1, 2


def cmd_synthesis(args) -> "NoReturn":  # noqa: F821
    """Validate a proposed workflow against a catalog and a policy floor.

    Exits rather than returns, for the reason `cmd_contract` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that returned
    `1` would print its reasons and leave the shell believing the workflow was fine.
    """
    import json
    import pathlib
    import sys

    try:
        proposal = json.loads(pathlib.Path(args.workflow).read_text(encoding="utf-8"),
                              object_pairs_hook=_no_duplicate_keys("workflow"))
        catalog = load_catalog(json.loads(pathlib.Path(args.catalog).read_text(
            encoding="utf-8")))
        raw = (json.loads(pathlib.Path(args.required).read_text(encoding="utf-8"),
                          object_pairs_hook=_no_duplicate_keys("floor"))
               if args.required else {})
        # `{"id": {"source": ..., "reason": ...}}`, or `{"id": "reason"}` for a policy step,
        # which is the common case and the one worth keeping short.
        required = tuple(_floor_entry(step_id, value) for step_id, value in sorted(raw.items()))
        # Checked here, where a caller's mistake is still reportable as one. `resolve` checks
        # again, but by then the only thing left to do about it is raise.
        check_floor(required, catalog)
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": ERROR_SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    problems = validate(proposal, catalog)
    if problems:
        print("\n".join(f"[REJECTED] {p}" for p in problems), file=sys.stderr)
        sys.exit(REFUSED)

    result = resolve(proposal, catalog, required)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"resolved workflow: {len(result['workflow']['steps'])} step(s), "
              f"floor {'held' if result['floor_held'] else 'restored'}")
        # Who requires the step decides what may be done about it next: a person's request
        # can be withdrawn by that person, and a policy requirement cannot. A line that named
        # only the step would leave the reader to guess which one they are looking at.
        for item in result["corrections"]["restored"]:
            print(f"      restored  {item['id']} [{item['source']}]: {item['reason']}")
        for item in result["corrections"]["relabelled"]:
            print(f"    relabelled  {item['id']} [{item['source']}]: {item['reason']}")
            print(f"                  proposed as [{item['claimed']}]: "
                  f"{item['claimed_reason']}")
    sys.exit(RESOLVED if result["floor_held"] else REFUSED)
