"""What an intent contract is allowed to decide elsewhere (#476).

#435 gave the goal a shape that can refuse. This is what other parts of rig may read out of
it, and — more of the work — what they may not.

A contract is written before the change exists. That makes it the right place to say what the
finished work has to be true of, and the wrong place to say anything the finished work would
have to be measured against by something that does not exist yet. So each of these functions
answers one narrow question, and every one of them refuses more than it grants.

**A conclusion cannot create a requirement.** `synthesise` refuses a floor built from the
proposal it is checking; the same rule reaches back one step. A requirement rig *inferred*, or
one a planner *proposed*, may sit in the contract — recording it is the point — but it does
not put a step on anybody's floor. Only what somebody declared does, and it arrives carrying
which somebody: a user's request and a policy's requirement mean different things downstream,
and flattening them here would lose the distinction `synthesise` exists to keep.

**A contract does not get to name what it cannot see.** Its requirements say what would show
them true: a test id, a gate criterion, a step. Those names are matched against what exists —
a catalog, the gate vocabulary — and a name matching nothing grants nothing rather than
inventing it. What the contract cannot speak to at all, it is not asked: an assurance target
has axes for isolation and provenance, and no requirement's evidence is a statement about
either, so nothing here fills them in.

**And the projection copies.** `assurance.py` says a derived view re-judges nothing and copies
decisions from the records that made them. Reading the goal back beside the criteria and the
evidence is exactly such a view, so it reports what the contract said and what the gate
recorded, and never decides whether the one satisfied the other.
"""

from __future__ import annotations

from . import intent
from .synthesis import OPERATOR_REQUESTED, POLICY_REQUIRED, Required, check_floor

#: How a declared requirement's origin becomes a floor entry's source. Both are declarations
#: and they are not the same declaration: a person can withdraw what they asked for, and a
#: policy requirement is not theirs to withdraw. `synthesise` draws that line, and a mapping
#: that flattened the two would erase it one step earlier.
FLOOR_SOURCES = {
    intent.EXPLICIT_USER: OPERATOR_REQUESTED,
    intent.POLICY_REQUIRED: POLICY_REQUIRED,
}


def floor_from(contract: intent.IntentContract,
               catalog: set[str] | frozenset[str]) -> tuple[Required, ...]:
    """The steps this contract's declared requirements put on a workflow's floor.

    A requirement's `evidence` names what would show it holds. When one of those names is a
    registered component, the requirement is saying that step has to run — and because the
    requirement was declared, that is not a planner's opinion about its own work.

    `catalog` is passed rather than discovered, for the reason `synthesise.validate` takes it:
    this module has no opinion about where components live, and a test can state the catalog
    it means. A name outside it grants nothing. That is not a silent drop — a requirement's
    evidence is allowed to name a test or a query, which are not steps and were never meant to
    be; what would be silent is treating a *misspelled* step as evidence of nothing, and
    `unmatched` reports exactly that so a caller can tell the two apart.
    """
    entries: dict[str, Required] = {}
    for requirement in contract.requirements:
        source = FLOOR_SOURCES.get(requirement.origin)
        if source is None:
            continue
        for name in requirement.evidence:
            if name not in catalog:
                continue
            existing = entries.get(name)
            if existing is not None and existing.source != source:
                # Two declarations wanting one step, disagreeing about who requires it.
                # `check_floor` refuses that rather than picking by order, and building it
                # here would only move the same collision earlier.
                raise ValueError(
                    f"the contract requires {name!r} as both {existing.source!r} and "
                    f"{source!r}. One authority per step, or whether a person may withdraw it "
                    f"depends on which requirement was read first")
            entries.setdefault(name, Required(id=name, source=source,
                                              reason=requirement.text))
    return check_floor(tuple(entries[name] for name in sorted(entries)), catalog)


def unmatched(contract: intent.IntentContract,
              catalog: set[str] | frozenset[str]) -> tuple[dict, ...]:
    """Evidence naming a component whose spelling differs only in case.

    Deliberately just that. A requirement may name a test, a gate criterion or a query, and
    none of those is a component — so most names outside the catalog are exactly what they
    look like and reporting them would be noise. A case-only difference is the one shape where
    the author almost certainly meant the component and typed it differently.

    Wider similarity — one transposed letter, an underscore for a hyphen — would need a rule
    about how close is close enough, and a wrong guess there costs more than the silence it
    replaces: a contract author told "did you mean review-diff?" about a test id they wrote on
    purpose learns to stop reading these. So this is narrow and says so, rather than claiming
    to catch typos in general.
    """
    known = {name.lower(): name for name in catalog}
    found = []
    for requirement in contract.requirements:
        for name in requirement.evidence:
            if name in catalog:
                continue
            close = known.get(name.lower())
            if close is not None:
                found.append({"evidence": name, "did_you_mean": close,
                              "requirement": requirement.text})
    return tuple(found)


def resting_on(contract: intent.IntentContract,
               criteria: set[str] | frozenset[str]) -> tuple[str, ...]:
    """The gate criteria this contract's declared requirements rest on.

    `criteria` is the gate's vocabulary, passed in. A requirement naming something outside it
    is naming a test or a query, which is a perfectly good thing for evidence to be and not a
    thing this function has anything to say about.
    """
    return tuple(sorted({name for requirement in contract.requirements
                         if requirement.declared
                         for name in requirement.evidence if name in criteria}))


def target_from(contract: intent.IntentContract,
                criteria: set[str] | frozenset[str]) -> dict | None:
    """The assurance target this contract asks for, or `None` when it asks for nothing.

    Narrow on purpose. An assurance target has axes for isolation, verification, provenance,
    approval and the gate; a requirement's `evidence` names things that would *show* the
    requirement holds, and only one of those kinds is a statement about assurance: a gate
    criterion. So a contract whose declared requirements rest on gate criteria asks for a gate
    that passed, and says nothing about the rest.

    The alternative — reading "production quality" out of a goal and filling in four axes —
    is what `assurance_target.VAGUE` exists to refuse, and generating it here would route
    around that refusal by writing the words for the author.

    `None` rather than a target with no axes, because `assurance_target.validate` refuses an
    empty one: *a target that requires nothing is met by everything*, which is a way of saying
    the run was unconstrained while looking like it was constrained. A contract that asks for
    nothing should produce no document, not a document that asks for nothing.
    """
    from .assurance_target import SCHEMA as TARGET_SCHEMA

    if not resting_on(contract, criteria):
        return None
    return {"schema": TARGET_SCHEMA, "axes": {"gate": "passed"}}


def unaskable(contract: intent.IntentContract) -> tuple[str, ...]:
    """The target axes nothing in a contract could ever fill in.

    Named rather than left implied: a caller reading a two-key target back may reasonably
    wonder whether the other axes were considered and dropped, or never in scope. They were
    never in scope — no requirement's evidence is a statement about which tree the work was
    written to or whether the accept record was signed — and saying so is cheaper than letting
    each reader work it out again.
    """
    from .assurance_target import AXES

    return tuple(axis for axis in sorted(AXES) if axis != "gate")


def projection(contract_payload: dict | None, gates: dict) -> dict:
    """Goal → criteria → evidence, copied from the records that decided each part.

    `assurance.py`: *a derived view re-judges nothing; it copies decisions from the records
    that made them.* So this reads the goal and the requirements out of the contract, reads
    each named criterion's status out of the gate block, and stops. Whether the criterion
    passing *satisfies* the requirement is `intent.status`'s question and a human's after
    that; answering it here would put a second verdict on a page whose value is that it holds
    none.

    A requirement whose evidence names no criterion the gate ruled on is reported with an
    empty list, not omitted. "Nothing checks this" is the fact `intent.unverifiable` exists to
    surface, and a projection that dropped those rows would make a contract look better on
    this page than it is.
    """
    from .assurance import UNREADABLE

    if contract_payload is None:
        return intent_unobserved("no intent.json — no contract was recorded for this task")
    if contract_payload is UNREADABLE:
        # Distinct from absent. The file is there — its digest is in `sources` — and nobody
        # can read it, which is a different situation with a different next step.
        return intent_unobserved(
            "intent.json is there and cannot be read: not JSON, not an object, or naming one "
            "key twice")
    problems = intent.validate(contract_payload)
    if problems:
        # Not a projection of a document that is not one. The receipt says why rather than
        # rendering half of it, for the same reason `_import_block` returns `None` rather
        # than an `unobserved` when a field is simply absent: a reader has to be able to tell
        # "no contract" from "a contract nobody can read".
        return intent_unobserved(
            "intent.json is not a contract: " + "; ".join(problems[:3])
            + ("; …" if len(problems) > 3 else ""))

    contract = intent.load(contract_payload)
    ruled: dict = {}
    # A criterion the gate block says it recorded twice. Read from that block's own mark
    # rather than recomputed here: `_gates` builds the list and marks it, so the Gates
    # section and this one cannot come to different conclusions about the same record.
    ambiguous: set = set()
    if gates.get("observed"):
        for entry in gates.get("criteria", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("name_recorded_more_than_once"):
                # Not `ruled[name] = entry`. Indexing by name would keep whichever record
                # came last, which makes the verdict on this page depend on the order two
                # equally-recorded rulings happen to sit in.
                ambiguous.add(entry.get("name"))
            else:
                ruled[entry.get("name")] = entry
    ruled = {name: entry for name, entry in ruled.items() if name not in ambiguous}

    requirements = []
    for requirement in contract.requirements:
        checked = [
            {"criterion": name, "status": None, "overridden": False, "ambiguous": True}
            if name in ambiguous else
            {"criterion": name, "status": ruled[name].get("status"),
             "overridden": bool(ruled[name].get("overridden")), "ambiguous": False}
            for name in requirement.evidence if name in ruled or name in ambiguous]
        requirements.append({
            # From `as_dict` rather than field by field: a field added to `Requirement` is
            # accepted, loaded, serialised and projected by one rule instead of four places
            # that have to agree.
            **requirement.as_dict(),
            "declared": requirement.declared,
            # Only the names the gate actually ruled on. The rest of `evidence` is still
            # above, so a reader can see that a requirement rested on a test nobody wired to
            # this gate rather than on nothing.
            "checked_by": checked,
        })
    # From the contract's own serialisation, so a field added to `IntentContract` reaches this
    # page without anyone remembering to add it here. `requirements` is replaced by the rows
    # built above, which are the same fields plus what the gate ruled on.
    return {**{k: v for k, v in contract.as_dict().items() if k != "schema"},
            "observed": True, "requirements": requirements,
            "unverifiable": [r.text for r in intent.unverifiable(contract)],
            "undeclared": [r.text for r in intent.undeclared(contract)]}


def intent_unobserved(reason: str) -> dict:
    """`assurance.unobserved`, reached through this module so the receipt's shape is its own.

    Imported here rather than at module scope: `assurance` imports nothing from this file
    today, and a top-level import in this direction would make that a cycle the first time it
    does.
    """
    from .assurance import unobserved

    return unobserved(reason)


#: What `intent --floor` and `intent --target` return. `1` is a contract that is not one, or
#: one whose declared requirements collide: both mean nothing can be derived from it.
DERIVED, NOT_DERIVABLE, EXECUTION_ERROR = 0, 1, 2


def cmd_derive(args) -> "NoReturn":  # noqa: F821
    """Derive a workflow floor or an assurance target from a contract.

    Exits rather than returns, for the reason `cmd_synthesis` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that returned
    `1` would print its reasons and leave the shell believing a floor had been derived.
    """
    import json
    import pathlib
    import sys

    from .synthesis import load_catalog

    try:
        payload = intent.read(args.file)
        names = load_catalog(json.loads(pathlib.Path(args.against).read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": intent.SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    problems = intent.validate(payload)
    if problems:
        print("\n".join(f"[REJECTED] {p}" for p in problems), file=sys.stderr)
        sys.exit(NOT_DERIVABLE)

    contract = intent.load(payload)
    try:
        result = ({"schema": intent.SCHEMA,
                   "floor": {item.id: {"source": item.source, "reason": item.reason}
                             for item in floor_from(contract, names)},
                   "unmatched": [dict(item) for item in unmatched(contract, names)]}
                  if args.floor else
                  {"target": target_from(contract, names),
                   "because": [f"a declared requirement rests on {name}"
                               for name in resting_on(contract, names)],
                   "unaskable": list(unaskable(contract))})
    except ValueError as exc:
        print(f"[REJECTED] {exc}", file=sys.stderr)
        sys.exit(NOT_DERIVABLE)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.floor:
        print(f"floor: {len(result['floor'])} step(s) from declared requirements")
        for step, item in sorted(result["floor"].items()):
            print(f"    {step} [{item['source']}]: {item['reason']}")
        for item in result["unmatched"]:
            # Named rather than dropped: evidence may legitimately be a test id, and a
            # misspelled component looks exactly like one until somebody says otherwise.
            print(f"    (not a registered component) {item['evidence']} — did you mean "
                  f"{item['did_you_mean']}?")
    else:
        target = result["target"]
        print("target: " + (f"gate {target['axes']['gate']}" if target
                            else "nothing this contract can ask for"))
        for why in result["because"]:
            print(f"    {why}")
        print(f"  a contract says nothing about: {', '.join(result['unaskable'])}")
    sys.exit(DERIVED)
