"""What an assurance target is allowed to decide elsewhere (#479).

#434 gave the asking half a shape that can refuse. This is what other parts of rig may read
out of it, and — as in #476, more of the work — what they may not.

A target names outcomes: a tree the work was written to, a signature that verifies, a gate
that passed. A workflow names steps. Turning one into the other is a claim that *this step is
how you get that outcome*, and that claim is not in the target and not in this module. It is a
policy, and it is passed in, for the same reason `synthesise`'s floor is built by the caller
and never read from the proposal it is checking.

**An axis nobody said how to reach is refused, not skipped.** The whole failure this module
could produce is a workflow that looks like it satisfies a target while nothing in it does. If
the mapping is silent about `provenance: signed-and-verified`, that silence means nobody wrote
down which step signs — and reading it as "no step is needed" would put the target's own
guarantee below the floor while reporting that the floor held.

**And it is keyed on the pair, not the axis.** `gate: skipped` is not a weaker `gate: passed`
and does not inherit its steps. An axis's values are different outcomes, and a mapping that
answered by axis alone would hand a target asking for one the steps declared for another.

**And the receipt evaluates once.** `assurance_target.evaluate` is called from one place — the
receipt, which already owns what a task achieved — and every other view copies its answer.
Two readers of one record eventually disagree about it, and a dashboard disagreeing with the
receipt about whether an assurance held is worse than either being wrong alone.
"""

from __future__ import annotations

from . import assurance_target
from .synthesis import Required, check_floor

#: The keys one entry of a mapping may carry. Closed for the reason every other schema in this
#: package is: a key accepted here and dropped by the loader leaves the author believing the
#: mapping said something it no longer says.
ENTRY_KEYS = frozenset({"id", "source", "reason"})


def read_requires(path) -> dict[tuple[str, str], tuple[Required, ...]]:
    """A mapping document from disk, refusing what no reader of one should accept.

    The one place a mapping is parsed, and it refuses a duplicated key. JSON allows a key twice
    and `json.loads` keeps the last one silently, so
    `{"gate": {"passed": [...], "passed": []}}` would be read as the empty declaration — and
    an empty declaration means *this needs no step of its own*, which is exactly the answer
    this module refuses to reach by accident. The distinction between an absent pair and an
    empty one is the module's whole safety rule, and a parser that can turn one into the other
    hands it away.
    """
    import json as _json
    import pathlib as _pathlib

    from .synthesis import _no_duplicate_keys

    return load_requires(_json.loads(_pathlib.Path(path).read_text(encoding="utf-8"),
                                     object_pairs_hook=_no_duplicate_keys("requires")))


def load_requires(payload: object) -> dict[tuple[str, str], tuple[Required, ...]]:
    """A declared axis-value → steps mapping, or `ValueError` saying why it is not one.

    Shaped `{axis: {value: [{id, source, reason}, ...]}}`. The nesting is the point: an axis
    and the value asked for are one key, so a mapping cannot answer for `gate: passed` and be
    read as having answered for `gate: skipped`.

    An empty list is a declaration and is kept: "reaching this needs no step of its own" is a
    thing a policy can truthfully say, and somebody wrote it. An *absent* pair is not that —
    `floor_from` refuses it — because absence is the one shape that means nobody has decided.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"requires: expected an object, got {type(payload).__name__}")
    built: dict[tuple[str, str], tuple[Required, ...]] = {}
    for axis, values in sorted(payload.items(), key=lambda kv: str(kv[0])):
        if axis not in assurance_target.AXES:
            raise ValueError(
                f"requires.{axis}: rig's receipt does not report that axis, so no step could "
                f"be checked against it. It reports {', '.join(sorted(assurance_target.AXES))}")
        if not isinstance(values, dict):
            raise ValueError(f"requires.{axis}: expected an object mapping a required value "
                             f"to the steps that reach it, got {type(values).__name__}")
        for value, entries in sorted(values.items(), key=lambda kv: str(kv[0])):
            if value not in assurance_target.AXES[axis]:
                raise ValueError(
                    f"requires.{axis}.{value}: not one of "
                    f"{', '.join(assurance_target.AXES[axis])} — a mapping for a value no "
                    f"target may ask for is a mapping nothing will ever read")
            if not isinstance(entries, list):
                raise ValueError(f"requires.{axis}.{value}: expected a list of steps, got "
                                 f"{type(entries).__name__}")
            built[(axis, value)] = tuple(
                _entry(f"requires.{axis}.{value}[{i}]", raw) for i, raw in enumerate(entries))
    return built


def _entry(where: str, raw: object) -> Required:
    """One step of a mapping. `Required` does the refusing; this only gets it there intact."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected an object, got {type(raw).__name__}")
    unknown = sorted(str(key) for key in raw if key not in ENTRY_KEYS)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}; a floor entry carries "
                         f"{', '.join(sorted(ENTRY_KEYS))}")
    missing = sorted(ENTRY_KEYS - set(raw))
    if missing:
        # Not defaulted. `Required` refuses a blank source or reason, and filling one in here
        # would be this module deciding who requires a step — which is the one thing it is
        # written not to do.
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    return Required(id=raw["id"], source=raw["source"], reason=raw["reason"])


def floor_from(target: dict,
               requires: dict[tuple[str, str], tuple[Required, ...]],
               catalog: set[str] | frozenset[str]) -> tuple[Required, ...]:
    """The steps a workflow must run for this target to be reachable at all.

    Not "for it to be met" — whether it was met is the receipt's answer after the run. This is
    the weaker and checkable claim: the target asks for outcomes, somebody declared which steps
    reach them, and a workflow missing those steps cannot reach the target however it ends.

    Raises rather than reporting, on every path. A caller that got a floor back would put it
    under `synthesise` and read `floor_held: true`, so an answer shaped like a floor is the one
    thing a refusal here must not look like.
    """
    problems = assurance_target.validate(target)
    if problems:
        raise ValueError("not an assurance target:\n  " + "\n  ".join(problems))

    entries: dict[str, Required] = {}
    unmapped = []
    for axis, value in sorted(target["axes"].items()):
        wanted = requires.get((axis, value))
        if wanted is None:
            unmapped.append(f"{axis}: {value}")
            continue
        for item in wanted:
            existing = entries.get(item.id)
            if existing is not None and existing != item:
                # Two axes reaching for one step and disagreeing about who requires it or why.
                # `check_floor` refuses that rather than picking by order, and resolving it
                # here would only move the same collision earlier.
                raise ValueError(
                    f"the mapping requires {item.id!r} as both {existing.source!r} "
                    f"({existing.reason!r}) and {item.source!r} ({item.reason!r}). One "
                    f"authority per step, or whether a person may withdraw it depends on "
                    f"which axis was read first")
            entries[item.id] = item
    if unmapped:
        raise ValueError(
            f"nothing says which step reaches {'; '.join(unmapped)}. A target asking for an "
            f"outcome no step is declared to produce cannot be planned for, and treating the "
            f"silence as 'no step is needed' would put the target's own guarantee below the "
            f"floor while reporting that the floor held")
    return check_floor(tuple(entries[name] for name in sorted(entries)), catalog)


def unreachable(requires: dict[tuple[str, str], tuple[Required, ...]]) -> tuple[str, ...]:
    """The axis-value pairs a target may ask for that this mapping cannot plan for.

    Named rather than left to be discovered by a refusal. `floor_from` refuses one target at a
    time, so an operator learns about a gap only when they happen to ask for that value; this
    answers "what could I ask for that you could not plan" before they do.
    """
    return tuple(f"{axis}: {value}"
                 for axis in sorted(assurance_target.AXES)
                 for value in assurance_target.AXES[axis]
                 if (axis, value) not in requires)


#: Why a receipt has no comparison to show. A word rather than the reason's prose, because a
#: reader that had to tell "nobody wrote one" from "one is there and nothing can read it" by
#: matching sentences would get it wrong the first time either sentence is edited — and those
#: two are different situations with different next steps.
ABSENT, UNREADABLE_FILE, INVALID = "absent", "unreadable", "invalid"
NOT_RECORDED = (ABSENT, UNREADABLE_FILE, INVALID)


def _unobserved(state: str, reason: str) -> dict:
    from .assurance import unobserved

    return {**unobserved(reason), "not_recorded": state}


def projection(target_payload, receipt: dict) -> dict:
    """What was asked for, beside what the receipt recorded — evaluated once, here.

    `assurance.py`: *a derived view re-judges nothing; it copies decisions from the records
    that made them.* This is the one place `assurance_target.evaluate` is called, so every
    other view — the Markdown page, Mission Control — copies this block rather than reading
    the same files and reaching its own answer.

    `unobservable` stays its own outcome all the way out. `unmet` says rig looked and what it
    found does not satisfy the target; `unobservable` says it cannot look. A caller that folded
    them together would read "we do not measure that" as "we measured it and it was
    insufficient", and act on it.
    """
    from .assurance import UNREADABLE

    if target_payload is None:
        return _unobserved(ABSENT,
                           "no assurance-target.json — nothing was asked for in writing, so "
                           "there is nothing to have fallen short of")
    if target_payload is UNREADABLE:
        return _unobserved(UNREADABLE_FILE,
                           "assurance-target.json is there and cannot be read: not JSON, not "
                           "an object, or naming one key twice")
    problems = assurance_target.validate(target_payload)
    if problems:
        # The receipt says why rather than rendering half of it. A target it cannot read is a
        # different situation from a target nobody wrote, with a different next step.
        return _unobserved(INVALID,
                           "assurance-target.json is not a target: " + "; ".join(problems[:3])
                           + ("; …" if len(problems) > 3 else ""))
    result = assurance_target.evaluate(target_payload, receipt)
    return {"observed": True,
            **{key: value for key, value in result.items() if key != "schema"}}


#: What `assurance-derive` returns. `1` covers both an invalid target and a mapping that
#: cannot plan for it, because both mean the same thing to a caller deciding whether to
#: proceed: no floor was derived. The message says which.
DERIVED, NOT_DERIVABLE, EXECUTION_ERROR = 0, 1, 2


def cmd_assurance_derive(args) -> "NoReturn":  # noqa: F821
    """Derive the workflow floor a target needs, from a declared axis→steps mapping.

    Exits rather than returns, for the reason `cmd_derive` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that returned
    `1` would print its reasons and leave the shell believing a floor had been derived.
    """
    import json
    import pathlib
    import sys

    from .synthesis import load_catalog

    try:
        target = assurance_target.read(args.target)
        requires = read_requires(args.requires)
        catalog = load_catalog(
            json.loads(pathlib.Path(args.against).read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": assurance_target.SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    try:
        built = floor_from(target, requires, catalog)
    except ValueError as exc:
        print(f"[REJECTED] {exc}", file=sys.stderr)
        sys.exit(NOT_DERIVABLE)

    result = {"schema": assurance_target.SCHEMA,
              "floor": {item.id: {"source": item.source, "reason": item.reason}
                        for item in built},
              "unreachable": list(unreachable(requires))}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"floor: {len(result['floor'])} step(s) this target needs")
        for step, item in sorted(result["floor"].items()):
            print(f"  {step}  [{item['source']}] {item['reason']}")
        if result["unreachable"]:
            print(f"  this mapping cannot plan for: {', '.join(result['unreachable'])}")
    sys.exit(DERIVED)
