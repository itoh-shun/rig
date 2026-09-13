"""What was asked for, in a shape something can check against (#435).

Ten runs in this repository carry a hand-written `requirements.md`: the goal in the user's
own words, then a table of the Issue's criteria copied verbatim, each with the thing that
satisfies it and the test that pins it. They are all the same artifact, written ten times by
hand, and every one of them is a claim nobody can check — that the criterion quoted is what
the Issue said, that the test named exists, that a requirement attributed to the user really
came from them.

This module is that artifact with a shape, and nothing more. Three lines it does not cross.

**It does not generate.** Turning a sentence into requirements is reading, judging and
deciding — an agent's work. A module that called a model to do it would have nothing left
that a gate could check and nothing a mutation could falsify. So generation happens
elsewhere and arrives here as a payload; what lives here is the schema, the validation, and
the refusals.

**It does not promote a guess into a requirement.** `build_acceptance` already marks where a
criterion came from (`origin="project"`, `origin="policy"`, absent for a preset), and
`caller.Caller` already separates what someone *declared* from what rig *inferred*. The same
distinction runs through here: a requirement rig concluded on its own is never recorded as
one the user asked for, and :func:`undeclared` is how a caller finds the ones still waiting
on a human.

**It does not hide what it does not know.** An ambiguity is kept as an ambiguity, with what
would settle it. A criterion with nothing to check it against is `unverifiable` and says so,
which is a different answer from `unsatisfied` — the receipt's rule, that a derived view
records what was measured and marks the rest unobserved, applies to intent too.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

SCHEMA = "rig.intent-contract/v1"

#: Where a requirement came from. An extension of the vocabulary `build_acceptance` uses for
#: acceptance criteria rather than a second one: `policy-required` is that function's
#: `origin="policy"`, and `repository-derived` is the read-it-from-the-repo case its
#: `origin="project"` covers. The three that are new are the ones intent needs and a gate
#: preset never had to express.
EXPLICIT_USER = "explicit-user"
REPOSITORY_DERIVED = "repository-derived"
POLICY_REQUIRED = "policy-required"
INFERRED = "inferred"
PROPOSED = "proposed"

ORIGINS = (EXPLICIT_USER, REPOSITORY_DERIVED, POLICY_REQUIRED, INFERRED, PROPOSED)

#: The origins rig may act on without asking. Exactly `caller.Caller.declared`'s rule —
#: someone said so, rather than rig having concluded it — applied to requirements instead of
#: to callers. Everything else is a proposal until a human agrees, however confident the
#: sentence that produced it sounded.
DECLARED = frozenset({EXPLICIT_USER, POLICY_REQUIRED})

#: A requirement is satisfied, not satisfied, or there is nothing to check it against. The
#: third is not a softer second: `unsatisfied` is a measurement and `unverifiable` is the
#: absence of one, and a caller that collapses them will read "nobody looked" as "it failed"
#: or, worse, the other way round.
SATISFIED = "satisfied"
UNSATISFIED = "unsatisfied"
UNVERIFIABLE = "unverifiable"

#: What a piece of evidence is recorded as. `unobserved` is the default for anything the
#: caller does not mention, and it is a state rather than a gap: a test that has not run and
#: a test that failed are different facts, and only one of them says anything about the work.
PASSED = "passed"
FAILED = "failed"
UNOBSERVED = "unobserved"
EVIDENCE_STATES = (PASSED, FAILED, UNOBSERVED)


@dataclasses.dataclass(frozen=True)
class Requirement:
    """One thing the finished work has to be true of, and where that came from.

    `source` is not decoration. A requirement claiming a human asked for it has to say where
    they said it — the Issue and its line, the message it was quoted from — because
    "explicit-user" is the strongest claim in this vocabulary and the one most worth being
    able to check. `evidence` names what would show it holds: a test id, a gate criterion, a
    query. Empty means nothing checks this yet, which is a fact worth recording rather than
    a gap to leave implied.
    """

    text: str
    origin: str
    source: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        """Did someone say so, or did rig conclude it?"""
        return self.origin in DECLARED

    def as_dict(self) -> dict:
        """Every field the dataclass has, in the shapes JSON holds.

        Derived rather than spelled out, for the reason `REQUIREMENT_KEYS` and `load` are: a
        field added here and forgotten in one of the three would be validated, loaded, and
        then vanish on the way back out.
        """
        import dataclasses as _dc

        return {f.name: list(v) if isinstance(v := getattr(self, f.name), tuple) else v
                for f in _dc.fields(self)}


@dataclasses.dataclass(frozen=True)
class IntentContract:
    """The goal as it was given, and what would make it true.

    `goal` is kept verbatim. A paraphrase is already an interpretation, and the whole point
    of writing this down is to be able to ask later whether the interpretation was right.
    """

    goal: str
    requirements: tuple[Requirement, ...] = ()
    non_goals: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    #: Questions the goal did not settle, each with what would settle it. Kept rather than
    #: resolved by guessing: an ambiguity recorded is a question someone can answer, and an
    #: ambiguity guessed at is a requirement nobody agreed to.
    ambiguities: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        """Every field the dataclass has, plus the wire-only `schema`.

        Derived for the reason `Requirement.as_dict` is: a field added here and forgotten in
        one of `CONTRACT_KEYS`, `load` or the receipt's projection would be refused, dropped
        or omitted depending on which place was missed.
        """
        out: dict = {"schema": SCHEMA}
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            out[field.name] = ([v.as_dict() if isinstance(v, Requirement) else dict(v)
                                if isinstance(v, Mapping) else v for v in value]
                               if isinstance(value, tuple) else value)
        return out


def _refuse(problems: list[str], where: str, why: str) -> None:
    problems.append(f"{where}: {why}")


#: The keys each object in a contract may carry, derived from the records that hold them so a
#: field added to one is accepted by the other without anyone remembering to. Closed, because a
#: key this schema does not define would be accepted here, dropped by `load`, and leave the
#: author believing the contract said something it no longer says — and a consumer copying the
#: contract onto a receipt copying most of it.
CONTRACT_KEYS = frozenset({"schema"}) | frozenset(
    f.name for f in dataclasses.fields(IntentContract))
REQUIREMENT_KEYS = frozenset(f.name for f in dataclasses.fields(Requirement))
AMBIGUITY_KEYS = frozenset({"question", "resolved_by"})


def _unknown(problems: list[str], where: str, item: dict, allowed: frozenset,
             what: str) -> None:
    """Refuse keys this schema does not define, and non-string keys it could not sort."""
    unreadable = [k for k in item if not isinstance(k, str)]
    if unreadable:
        _refuse(problems, where,
                f"{', '.join(repr(k) for k in unreadable)} is not a key {what} can have")
    unknown = sorted(set(item) - allowed - set(unreadable), key=str)
    if unknown:
        _refuse(problems, where,
                f"{', '.join(repr(k) for k in unknown)} is not part of {what}. A key this "
                f"schema does not define would be dropped rather than honoured")


def validate(payload: dict) -> list[str]:
    """Every way this payload is not an intent contract, not the first one.

    Collected rather than short-circuited, for the reason `_untrusted_source_reasons` collects
    them: an author who fixes one problem and is refused again for the next learns nothing
    from the second refusal that the first could not have told them.

    What is checked is what can be checked here — shape, vocabulary, and claims that contradict
    themselves. Whether a requirement is *the right* requirement is not a question this or any
    other function answers; it is why `goal` is kept verbatim and why `source` is mandatory on
    the origins that assert someone else said something.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"contract: expected an object, got {type(payload).__name__}"]

    _unknown(problems, "contract", payload, CONTRACT_KEYS, f"a {SCHEMA} document")

    schema = payload.get("schema")
    if schema != SCHEMA:
        _refuse(problems, "schema", f"expected {SCHEMA!r}, got {schema!r}")

    goal = payload.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        # Without the goal there is nothing for any of the rest to be an interpretation *of*.
        _refuse(problems, "goal", "the goal is missing, and it is the one field that is not "
                                  "derived from anything else")

    raw = payload.get("requirements")
    if not isinstance(raw, list):
        # Refused, and then the rest of the payload is still read. Returning here reported
        # one problem out of however many there were, from the function whose docstring
        # promises the opposite — an author would have fixed this field and been refused
        # again for the next, which is exactly what collecting them exists to prevent.
        _refuse(problems, "requirements", "expected a list")
        raw = []

    for index, item in enumerate(raw):
        where = f"requirements[{index}]"
        if not isinstance(item, dict):
            _refuse(problems, where, f"expected an object, got {type(item).__name__}")
            continue
        _unknown(problems, where, item, REQUIREMENT_KEYS, "a requirement")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            _refuse(problems, where, "has no text")
        origin = item.get("origin")
        if origin not in ORIGINS:
            _refuse(problems, where, f"origin {origin!r} is not one of {', '.join(ORIGINS)}")
        elif origin in DECLARED and not (isinstance(item.get("source"), str)
                                         and item["source"].strip()):
            # The strongest claim in the vocabulary is the one that has to be checkable.
            _refuse(problems, where,
                    f"origin {origin!r} asserts that someone said this, so it has to say "
                    f"where — an Issue and its line, or the message it was quoted from")
        evidence = item.get("evidence", [])
        # Blank ids are refused with the wrong-typed ones: an evidence entry that names
        # nothing looks like a link and resolves to no record, so a requirement carrying one
        # would read as checked while nothing checks it.
        if not isinstance(evidence, list) or any(
                not isinstance(e, str) or not e.strip() for e in evidence):
            _refuse(problems, where, "evidence must be a list of non-empty strings naming "
                                     "what would show this holds (a test id, a gate "
                                     "criterion, a query)")

    for field in ("non_goals", "assumptions"):
        value = payload.get(field, [])
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            _refuse(problems, field, "expected a list of strings")

    ambiguities = payload.get("ambiguities", [])
    if not isinstance(ambiguities, list):
        _refuse(problems, "ambiguities", "expected a list")
    else:
        for index, item in enumerate(ambiguities):
            where = f"ambiguities[{index}]"
            if not isinstance(item, dict):
                _refuse(problems, where, f"expected an object, got {type(item).__name__}")
                continue
            _unknown(problems, where, item, AMBIGUITY_KEYS, "an ambiguity")
            if not (isinstance(item.get("question"), str) and item["question"].strip()):
                _refuse(problems, where, "has no question")
            if not (isinstance(item.get("resolved_by"), str)
                    and item["resolved_by"].strip()):
                # An ambiguity nobody can act on is a note. Saying what would settle it is
                # what makes it a question someone can answer.
                _refuse(problems, where,
                        "does not say what would settle it, so nobody can close it")
    return problems


def load(payload: dict) -> IntentContract:
    """Build a contract from a validated payload. Raises `ValueError` if it is not one."""
    problems = validate(payload)
    if problems:
        raise ValueError("not an intent contract:\n  " + "\n  ".join(problems))
    # Every field the dataclass declares, read the way `_CODEC` says to read it. Spelling the
    # five out here is what five review rounds kept finding one layer at a time: a field is
    # accepted by `CONTRACT_KEYS` because that is derived, and then dropped here because this
    # was not, so the document validates and the contract silently does not say it.
    return IntentContract(**{field.name: _CODEC[field.name](payload[field.name])
                             for field in dataclasses.fields(IntentContract)
                             if field.name in payload})


def _frozen(mapping: dict):
    """A read-only view of a copy. What was validated has to be what gets read."""
    import types as _types

    return _types.MappingProxyType(dict(mapping))


def _requirement(raw: dict) -> Requirement:
    """One requirement from its JSON form, field by field as the dataclass declares them."""
    import dataclasses as _dc

    values = {}
    for field in _dc.fields(Requirement):
        default = () if field.name == "evidence" else field.default
        value = raw.get(field.name, default)
        values[field.name] = tuple(value) if isinstance(value, list) else value
    return Requirement(**values)


def _strings(value) -> tuple:
    """A list of strings as the dataclass holds it. `validate` has already refused anything else."""
    return tuple(value)


def _requirements(value) -> tuple:
    return tuple(_requirement(r) for r in value)


def _ambiguities(value) -> tuple:
    # Frozen, not merely copied: `frozen=True` protects the tuple and not the dicts inside it,
    # so a caller could otherwise replace a question with something `validate` would have
    # refused, after it had been validated.
    return tuple(_frozen(a) for a in value)


def _verbatim(value):
    """Kept as it was written. A paraphrase is already an interpretation."""
    return value


#: How each field of a contract is read out of its JSON form.
#:
#: Declared rather than inferred from the annotations. `from __future__ import annotations`
#: makes every `dataclasses.fields(...)[i].type` a *string*, so choosing a converter by
#: declared type would mean matching annotation text — an approximation that needs reinforcing
#: the first time somebody writes `Sequence[str]`, or `tuple[Requirement, ...] | None`, or
#: renames an import. This repository has paid for that lesson once already: approximating a
#: language's own rules is the thing that keeps almost working.
#:
#: The point is not the table. The point is the check under it: a field this table does not
#: mention cannot be read, and saying so at import is the difference between a rule somebody
#: has to remember and one nobody can forget.
_CODEC = {
    "goal": _verbatim,
    "requirements": _requirements,
    "non_goals": _strings,
    "assumptions": _strings,
    "ambiguities": _ambiguities,
}

def _codec_gaps(field_names, declared) -> str | None:
    """Why this codec does not describe that record, or `None` when it does.

    A function rather than the comparison written inline, so a test can hand it a field it has
    not been told how to read and see the refusal. A check nothing can exercise is a check
    nobody knows still works.
    """
    fields = frozenset(field_names)
    undeclared = sorted(fields - frozenset(declared))
    stale = sorted(frozenset(declared) - fields)
    if not undeclared and not stale:
        return None
    return "intent._CODEC does not describe IntentContract: " + "; ".join(filter(None, [
        f"{', '.join(undeclared)} would be validated and then dropped by load — say how each "
        f"is read" if undeclared else "",
        f"{', '.join(stale)} name no field of IntentContract" if stale else ""]))


_CONTRACT_FIELDS = frozenset(f.name for f in dataclasses.fields(IntentContract))
# At import, not in a test. A test can be skipped, deselected, or simply not run by the person
# adding the field; this fails the first time anything imports the module, which is every path
# that could read a contract. Both directions: an undeclared field would be validated and then
# dropped by `load`, and a declared one naming no field is a converter somebody kept for a
# field they deleted — which is how a table starts describing a shape that no longer exists.
_gap = _codec_gaps(_CONTRACT_FIELDS, _CODEC)
if _gap:
    raise RuntimeError(_gap)


def read(path) -> dict:
    """A contract document from disk, refusing what no reader of one should accept.

    The one place a contract is parsed. Three entry points read them — `intent`,
    `intent-derive`, and the receipt — and each was written with its own `json.loads` until a
    reviewer pointed out that a duplicated `origin` was refused by two of them and reported as
    a valid declaration by the third. A rule each caller has to remember is a rule one of them
    will not.

    JSON allows a key twice and `json.loads` keeps the last one silently, so
    `"origin": "inferred", "origin": "explicit-user"` would promote a conclusion into
    something somebody said.
    """
    import json as _json
    import pathlib as _pathlib

    from .synthesis import _no_duplicate_keys

    return _json.loads(_pathlib.Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=_no_duplicate_keys("contract"))


def undeclared(contract: IntentContract) -> tuple[Requirement, ...]:
    """The requirements nobody has agreed to yet.

    `inferred` and `proposed` are rig's own reading of what was asked. Acting on them without
    saying so is how a run ends up having built something correct against a specification the
    user never gave — which looks like success from the inside and is not.
    """
    return tuple(r for r in contract.requirements if not r.declared)


def unverifiable(contract: IntentContract) -> tuple[Requirement, ...]:
    """The requirements with nothing to check them against."""
    return tuple(r for r in contract.requirements if not r.evidence)


def status(contract: IntentContract, evidence: dict[str, str] | None = None) -> dict:
    """Whether the intent is met, which is not the same question as whether the gate passed.

    `evidence` maps an evidence id to what was *recorded* about it — `passed`, `failed`, or
    `unobserved`. Anything absent is `unobserved`, because a caller that did not mention a
    test has not told us it failed.

    Nothing here re-judges anything; this copies and counts. That distinction is the reason
    the parameter is a mapping of states rather than a set of ids that passed: from a set,
    absence means both "ran and failed" and "never ran", and folding those together would
    remake a decision the evidence record has not made — the one thing a derived view in this
    repository is not allowed to do. A review round caught this module doing exactly that.

    So a requirement is `unsatisfied` only when something that checks it is recorded as
    having failed. Evidence merely unobserved leaves it `unverifiable`, which is the honest
    answer: nobody looked yet.
    """
    states = dict(evidence or {})
    # A state outside the vocabulary is not a weaker verdict, it is not a verdict. Reading
    # `{"t1": "banana"}` as `unobserved` would manufacture a plausible summary out of a
    # record that says nothing — the same manufacture the parameter itself was changed to
    # stop, one level down. `EVIDENCE_STATES` existed and was not being used.
    unknown = sorted(f"{key}={value!r}" for key, value in states.items()
                     if value not in EVIDENCE_STATES)
    if unknown:
        raise ValueError("evidence states must be one of "
                         f"{', '.join(EVIDENCE_STATES)}; got {', '.join(unknown)}")
    verdicts: list[str] = []
    for requirement in contract.requirements:
        recorded = [states.get(e, UNOBSERVED) for e in requirement.evidence]
        if not recorded:
            verdicts.append(UNVERIFIABLE)
        elif FAILED in recorded:
            verdicts.append(UNSATISFIED)
        elif all(state == PASSED for state in recorded):
            verdicts.append(SATISFIED)
        else:
            verdicts.append(UNVERIFIABLE)
    if UNSATISFIED in verdicts:
        overall = UNSATISFIED
    elif UNVERIFIABLE in verdicts or not verdicts:
        overall = UNVERIFIABLE
    else:
        overall = SATISFIED
    return {
        "schema": SCHEMA,
        "status": overall,
        "requirements": len(verdicts),
        "satisfied": verdicts.count(SATISFIED),
        "unsatisfied": verdicts.count(UNSATISFIED),
        "unverifiable": verdicts.count(UNVERIFIABLE),
        "undeclared": len(undeclared(contract)),
        "open_ambiguities": len(contract.ambiguities),
    }


#: What `intent --validate` returns. `1` for a payload that is not a contract, `0` for one
#: that is — and nothing in between, because "it parsed but I have opinions" is what the
#: `undeclared` and `unverifiable` counts are for, not an exit code.
VALID, INVALID, EXECUTION_ERROR = 0, 1, 2


def cmd_intent(args) -> "NoReturn":  # noqa: F821
    """Validate an intent contract and say what it leaves open.

    Exits rather than returns, because the dispatcher calls subcommands for their effect and
    discards what they hand back — a refusal that returned `1` into that would print its
    reasons and leave the shell believing the contract was fine. `cmd_contract` exits for the
    same reason and this follows it.

    Never raises past this frame either: a traceback on stderr and some other exit code is
    the same ambiguity this command exists to remove, wearing a different hat.
    """
    import json
    import sys

    try:
        payload = read(args.file)
    except Exception as exc:  # noqa: BLE001 — any failure to read is one status, not many
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    problems = validate(payload)
    if problems:
        report = {"schema": SCHEMA, "status": "invalid", "problems": problems}
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
              else "\n".join(f"[REJECTED] {p}" for p in problems), file=sys.stderr)
        sys.exit(INVALID)

    contract = load(payload)
    # Structural counts only. `status()` answers a question this command cannot — it needs a
    # record of what each piece of evidence did, and this command runs nothing. Calling it
    # with no observations returns every evidenced requirement as `unverifiable`, which is
    # true of *this moment* and false as a description of the contract; reporting that number
    # as "nothing checks them" would say a requirement naming three tests names none.
    summary = {
        "schema": SCHEMA,
        "status": "valid",
        "requirements": len(contract.requirements),
        "unchecked": len(unverifiable(contract)),
        "undeclared": len(undeclared(contract)),
        "open_ambiguities": len(contract.ambiguities),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"intent contract: valid — {summary['requirements']} requirement(s), "
              f"{summary['unchecked']} with nothing to check them, "
              f"{summary['undeclared']} not declared by a human, "
              f"{summary['open_ambiguities']} open question(s)")
    sys.exit(VALID)
