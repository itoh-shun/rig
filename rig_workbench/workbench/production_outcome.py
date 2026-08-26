"""Did the numbers somebody declared this change would move actually move (#437).

`record-outcome` has answered one question since #289: did anything go wrong after this
landed — `ok` or `incident`, written to `.rig/runs/<id>/outcome.json`. That is a flag, and a
flag cannot say that p95 was supposed to fall from 820 ms to 574 ms and reached 787. Passing
the development gate proves the change met its assurance requirements; it does not prove the
real-world goal was achieved, and rig had no shape in which to write the difference down.

This module is that shape, and nothing more: schema, validation, refusal. Two documents come
in — an expectation somebody declared *before* the change shipped, and observations an adapter
wrote *after* — and one comparison goes out.

**The caller supplies the bar; the observation never states it.** `target`, `baseline`,
`at_most`, `at_least`, `limit`, `direction`, `window`, `role` and `status` are refused **by
name** on the observation document and on every entry in it, with that reason attached. A
telemetry adapter — or an agent writing one — does not get to name the bar its own numbers
are measured against.

**Each role names its own bar, and neither role reads the other's field.** An objective
declares `baseline`, `target` and the `direction` improvement runs in: `decrease` means the
number is meant to fall, so the target is reached at or below it. A guardrail declares one
bound and has no direction at all — `at_most` is a ceiling the value must not exceed,
`at_least` a floor it must not fall below.

It said `direction` + `limit` until a reviewer read that pair two ways. On an objective a
direction is where *improvement* lies; on a guardrail the same field had to mean where *harm*
lies, so `{direction: decrease, limit: 0.5}` — written by an author who meant "lower is
better, stay under 0.5" — was a floor to the code, and an error rate of 100.0 pct reported
`achieved`. No test could object: both branches are correct by construction, so neither is
the mutation. The bound's **name** is what closed that, not a sentence about it: `at_most:
0.5` observed at 100.0 is `regressed` and cannot be read the other way, `value <= at_most`
turned around is a mutation the suite catches, and a guardrail still carrying `direction` or
`limit` is refused by name with what to write instead.

**A conclusion cannot create a requirement.** `declared_by` admits `intent.DECLARED` and
nothing else, imported from `intent.py` rather than restated, so the rule has one home. And
`declared_at` has to be at or before the window opens: an expectation is a floor only if it
was written before the answer was available, and a schema with no field for when it was
written cannot tell a declared target from a retrofitted one.

**"Not observed" is its own outcome.** `unmeasured` (nobody looked) and `inconclusive`
(somebody looked and the looking settles nothing) are never folded into `not-achieved`, and
neither is ever `achieved`. `PRECEDENCE` orders the vocabulary once, in the order its two
siblings use: a measured negative outranks a cannot-look, exactly as `intent.py` ranks
`unsatisfied` above `unverifiable` and `assurance_target.evaluate` ranks `unmet` above
`unobservable`. A dashboard showing both must not rank the same pair of ideas two ways.

**Confirmed and speculated never merge.** `measured` and `reported` settle a metric;
`estimated` is carried to the report and reaches the verdict not at all. Nothing outside the
window settles anything either — and, because a discarded measurement that disagreed is the
one thing a green report must not hide, out-of-window observations are **counted and their
sources named** on the metric they were about, rather than dropped.

**The change is an object, not a name.** `change` has to be a full git object id
(`provenance_graph.OBJECT_ID`, imported), and the command resolves it with git before
comparing anything. An expectation about a branch name is an expectation about whatever that
name points at today — the roadmap's third invariant, which `assurance.py::_target` already
applies to receipts.

What it does not do, stated because prose claiming more than the code holds is the defect this
repository finds most often:

* **It does not fetch.** No telemetry client, no HTTP, no monitoring integration, no adapter
  registry. Observations arrive as a file the caller wrote.
* **It does not estimate.** No interpolation, no filling a gap, no defaulting a baseline to
  the observed value.
* **It does not attribute cause.** Every report carries `claim: observational-not-causal`.
* **It does not decide what "meaningful" is.** A 0.4 % improvement is `partially-achieved`
  with both numbers printed, not rounded in rig's favour.
* **It does not aggregate.** Two settling observations of one metric is refused, never
  averaged: which p95 changes the verdict, and that choice belongs to whoever knows what the
  interval means.
* **It does not re-judge assurance.** The receipt's `final_status` and `outcome.json`'s
  `ok`/`incident` are *copied* beside the production status and never combined with it.
* **`reported` settles exactly as `measured` does**, at the level of both `status` and exit
  code. `declared_by` is narrowed to two origins because a claim is not a fact; the
  observation side applies no equivalent narrowing, and a `reported` number from a named human
  source can carry a metric to `achieved`. `kind` is in the report so a reader can see which.
* **The comparison is a pure function of the expectation, the observations and `--as-of`.**
  That is `compare`, and only `compare`. The *command* is not pure: it resolves `change`
  through git, and with `--task` it builds the receipt, which stamps its own generation time
  and shells out to git itself. It writes no run state except the one thing it computed — with
  `--task`, the report is recorded to `.rig/runs/<id>/production-outcome.json` so a later
  reader copies the comparison instead of making a second one. That file sits beside
  `outcome.json` and is not it: `outcome.json` is the human incident flag, this is the
  measured comparison.
* **Nothing renders the recorded report yet.** The assurance receipt carries no
  `production_outcome` block, Mission Control does not read the file, and #436 does not hang a
  node off it. The artifact and its schema are what those can be built on; none of them is
  built here.
* **Nothing here measures whether the expectations people write are the right ones.** That a
  target was beyond its baseline and that a number reached it is all this checks.
"""

from __future__ import annotations

import datetime as dt
import math

from .assurance_wiring import INVALID, UNREADABLE_FILE
from .intent import DECLARED
from .provenance_graph import OBJECT_ID

#: What was declared, before the change shipped.
EXPECTATION = "rig.expected-outcome/v1"
#: What an adapter measured, after it shipped.
OBSERVATION = "rig.production-observation/v1"
#: The comparison this module emits.
SCHEMA = "rig.production-outcome/v1"

#: Where the recorded comparison lands under a run directory. Deliberately not `outcome.json`,
#: which `record-outcome` owns and which answers a different question — *did anything break* —
#: with a two-valued human judgement.
RECORD_NAME = "production-outcome.json"

ACHIEVED = "achieved"
PARTIALLY_ACHIEVED = "partially-achieved"
NOT_ACHIEVED = "not-achieved"
REGRESSED = "regressed"
#: Somebody looked and the looking cannot settle it: every observation outside the window, or
#: every one of them an estimate.
INCONCLUSIVE = "inconclusive"
#: Nobody looked. Not a softer `not-achieved`: that one says rig read a number and the number
#: was short, and this one says there is no number.
UNMEASURED = "unmeasured"

OUTCOMES = (ACHIEVED, PARTIALLY_ACHIEVED, NOT_ACHIEVED, REGRESSED, INCONCLUSIVE, UNMEASURED)

#: Worst wins, declared once so no view can order them differently.
#:
#: The measured verdicts come first and the unlookable ones after, which is the order both
#: siblings use for the same pair of ideas: `intent.py` takes `unsatisfied` over
#: `unverifiable`, and `assurance_target.evaluate` takes `unmet` over `unobservable`. Ranking
#: `unmeasured` above `not-achieved` would make a measured shortfall vanish from the headline
#: word — and a learner told that `unmeasured` means "nobody looked, do not learn from this"
#: would then throw away a report containing a real, measured failure.
PRECEDENCE = (REGRESSED, NOT_ACHIEVED, PARTIALLY_ACHIEVED, UNMEASURED, INCONCLUSIVE, ACHIEVED)


def _vocabulary_gaps() -> list[str]:
    """Every outcome word `PRECEDENCE` fails to rank exactly once, and vice versa.

    Checked at import rather than in a test, for the reason `intent._codec_gaps` is: a test
    can be deselected by the person adding a value, and an unranked outcome would make
    `status` depend on which metric happened to be read first.
    """
    return ([f"{o!r} is in OUTCOMES and not in PRECEDENCE" for o in OUTCOMES
             if o not in PRECEDENCE]
            + [f"{o!r} is in PRECEDENCE and not in OUTCOMES" for o in PRECEDENCE
               if o not in OUTCOMES]
            + [f"{o!r} appears in PRECEDENCE {PRECEDENCE.count(o)} times" for o in set(PRECEDENCE)
               if PRECEDENCE.count(o) != 1])


if _gaps := _vocabulary_gaps():  # pragma: no cover - import-time invariant
    raise RuntimeError("production outcome vocabulary and PRECEDENCE disagree: "
                       + "; ".join(_gaps))

OBJECTIVE, GUARDRAIL = "objective", "guardrail"
ROLES = (OBJECTIVE, GUARDRAIL)

#: Where improvement lies, on an objective. Not a field of a guardrail: see `ROLE_KEYS`.
DECREASE, INCREASE = "decrease", "increase"
DIRECTIONS = (DECREASE, INCREASE)

#: The two sides a guardrail can be bounded on, named after what each one constrains. Exactly
#: one per guardrail: a metric bounded on neither side constrains nothing, and one bounded on
#: both is two bars for one number, which `validate_expectation` refuses to guess between.
AT_MOST, AT_LEAST = "at_most", "at_least"
BOUNDS = (AT_MOST, AT_LEAST)

MEASURED, REPORTED, ESTIMATED = "measured", "reported", "estimated"
KINDS = (MEASURED, REPORTED, ESTIMATED)
#: The kinds that settle a metric. An estimate is carried and never settles — #437's non-goal
#: is that rig must not *generate* a value it could not obtain, which says nothing about a
#: caller labelling their own extrapolation honestly.
SETTLING = frozenset({MEASURED, REPORTED})

EXPECTATION_KEYS = frozenset({"schema", "change", "declared_by", "declared_at", "source",
                              "window", "metrics"})
OBSERVATION_KEYS = frozenset({"schema", "change", "observations"})
WINDOW_KEYS = frozenset({"opens", "closes"})

#: Closed **per role**, not per document. A guardrail carrying a `baseline` is refused rather
#: than ignored, because a field accepted and never read is a field its author believes says
#: something — and `partially-achieved` is then structurally impossible for a guardrail (there
#: is no baseline to be partway from) rather than suppressed by a comment somebody can delete.
#:
#: `direction` is an **objective's** field and appears on no guardrail. It says where
#: improvement lies, and a guardrail has nowhere for improvement to lie: it says how far the
#: number may go, on the side its bound is named after. Sharing one field between "where the
#: good direction is" and "which way harm runs" is what let an inverted guardrail read as
#: `achieved`, so the two ideas do not share a name here.
ROLE_KEYS = {
    OBJECTIVE: frozenset({"id", "role", "unit", "direction", "baseline", "target"}),
    GUARDRAIL: frozenset({"id", "role", "unit", AT_MOST, AT_LEAST}),
}

#: The shape a guardrail used to have, and the one an author reaching for `direction` will
#: write. Refused by name **with what to write instead**, for the reason `BAR_KEYS` carries
#: its own: the bare closure sentence says the key is not read and leaves the author to guess
#: that the whole idea moved, rather than that they misspelled something.
SUPERSEDED_GUARDRAIL_KEYS = frozenset({"direction", "limit"})

_BOUND_REASON = ("a guardrail names the side it bounds and nothing else: `at_most: 0.5` is a "
                 "ceiling and `at_least: 99.9` is a floor. `direction` says where improvement "
                 "lies and belongs to an objective; read on a guardrail it would have to mean "
                 "where harm runs, and one field meaning both is a bar nothing can check")

ENTRY_KEYS = frozenset({"metric", "value", "unit", "observed_at", "kind", "source"})

#: Keys that state the bar a value is measured against, or the verdict that follows from it.
#: Refused by name **with the reason** wherever they appear on the observation side — the
#: document or an entry — because a schema that merely ignored them would let an adapter
#: believe it had declared a target that nothing ever read.
#:
#: Exactly these, and no wider. `declared_by` and `declared_at` are refused too, by the closure
#: alone: they belong to the expectation, but an adapter that wrote one was confused about
#: which document owns a declaration rather than about who names the bar, and giving both
#: mistakes the same sentence would explain one of them wrongly.
BAR_KEYS = frozenset({"target", "baseline", AT_MOST, AT_LEAST, "limit", "direction", "role",
                      "window", "status"})

_BAR_REASON = ("an observation states a value, not the bar it is measured against nor the "
               "verdict that follows from it: target, baseline, at_most, at_least, direction, "
               "role and window are the expectation's to declare, and status is the report's. "
               "limit is refused by that name too — it was a guardrail's bound before the "
               "bound was named after the side it holds")


def _refuse(problems: list[str], where: str, why: str) -> None:
    problems.append(f"{where}: {why}")


def _number(value: object) -> bool:
    """A finite number, and not a bool.

    `bool` first: `isinstance(True, int)` is True, so `true` would arrive as the number 1 and
    become a baseline. `math.isfinite` after: `json.loads` accepts `NaN`, and every comparison
    against NaN is False — `value <= target` fails, `improvement > 0` fails, `== 0` fails — so
    a NaN that survived would land the metric on `regressed` with a straight face.
    """
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _time(value: object) -> dt.datetime | None:
    """An ISO 8601 timestamp *with an offset*, or `None`.

    A naive timestamp is refused rather than assumed to be UTC or local: a window bound and an
    observation compared across two different assumptions is a comparison whose answer depends
    on where the process ran.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unknown(problems: list[str], where: str, item: dict, allowed: frozenset[str],
             what: str, explain: tuple[tuple[frozenset[str], str], ...] = ()) -> None:
    """Keys nothing reads, refused by name — and each *kind* of them in its own sentence.

    One message per reason, never one message carrying a reason true of only some of the keys
    it names. An observation carrying both `target` and `declared_by` is two different
    mistakes: the first named a bar the observation does not get to set, and the second put a
    declaration in the document that records what was seen. Joining them under the bar
    sentence explains the second one wrongly — which is what `BAR_KEYS` says must not happen,
    and what it did until a reviewer wrote both keys on one document.
    """
    unknown = sorted(str(key) for key in item if key not in allowed)
    if not unknown:
        return
    for keys, why in explain:
        named = [key for key in unknown if key in keys]
        if named:
            _refuse(problems, where, f"{', '.join(named)} is not part of {what} — {why}")
            unknown = [key for key in unknown if key not in keys]
    if unknown:
        _refuse(problems, where, f"{', '.join(unknown)} is not part of {what}")


def _object_id(problems: list[str], where: str, value: object, what: str) -> None:
    """`change`, on either document: present, and shaped like a git object.

    Shape here and resolution in the command, which is how `provenance_graph` splits the same
    question (it refuses a non-object-shaped `git:` authority before it will ask git at all).
    Any non-empty string would otherwise be accepted — `main`, `HEAD`, `the tuesday deploy`,
    or a 12-character abbreviation that a longer sha will never compare equal to.
    """
    if not _text(value):
        _refuse(problems, where, f"{what} the immutable change it is about")
    elif not OBJECT_ID.fullmatch(value.strip()):
        _refuse(problems, where,
                f"{value!r} is not a full git object id (40 hex characters, or 64 for "
                f"sha-256). An expectation about a branch name is an expectation about "
                f"whatever that name points at today, and an abbreviation is not the object "
                f"rig can compare a receipt against")


def _bound(problems: list[str], where: str, metric: dict) -> None:
    """Exactly one of `at_most` / `at_least`, and a finite number.

    All four ways a bound can fail to be one are refusals, none of them silence: **neither**
    named (a guardrail that constrains nothing would hold against every value), **both**
    (two bars for one number, and rig does not pick), a **non-finite** value, and — through
    `ROLE_KEYS` — the superseded `direction`/`limit` pair, refused by name with what to write.
    `_compare_one` branches on the same `at_most in metric` this guarantees, so there is no
    fifth case where the comparison reads a key nothing checked.
    """
    named = [bound for bound in BOUNDS if bound in metric]
    if len(named) != 1:
        # Self-contained, and *not* `_BOUND_REASON` again: a guardrail carrying the
        # superseded pair collects both refusals, and repeating one sentence inside the other
        # is how a refusal stops being read.
        _refuse(problems, where,
                f"a guardrail states exactly one of {', '.join(BOUNDS)}, and this one states "
                f"{', '.join(named) if named else 'neither'}: `at_most` is a ceiling the "
                f"value must not exceed and `at_least` a floor it must not fall below. "
                f"Bounded on no side it holds against every value; bounded on both it is two "
                f"bars for one number, and rig does not pick between them")
    for bound in named:
        if not _number(metric.get(bound)):
            _refuse(problems, where, f"{bound} {metric.get(bound)!r} is not a finite number")


def validate_expectation(payload: object) -> list[str]:
    """Every way this payload is not an expected outcome, not the first one.

    Collected rather than short-circuited, the rule `intent.validate` and
    `assurance_target.validate` both follow: an author who fixes one problem and is refused
    again for the next learns nothing from the second refusal the first could not have told
    them.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"expectation: expected an object, got {type(payload).__name__}"]

    _unknown(problems, "expectation", payload, EXPECTATION_KEYS, f"a {EXPECTATION} document")
    if payload.get("schema") != EXPECTATION:
        _refuse(problems, "schema", f"expected {EXPECTATION!r}, got {payload.get('schema')!r}")
    _object_id(problems, "change", payload.get("change"),
               "an expectation has to name")

    origin = payload.get("declared_by")
    if origin not in DECLARED:
        _refuse(problems, "declared_by",
                f"{origin!r} is not one of {', '.join(sorted(DECLARED))} — a conclusion cannot "
                f"create a requirement; a proposed expectation belongs in the intent contract, "
                f"which has 'proposed' for it")
    if not _text(payload.get("source")):
        _refuse(problems, "source", "says someone declared this, so it has to say where")

    declared_at = _time(payload.get("declared_at"))
    if declared_at is None:
        _refuse(problems, "declared_at",
                f"{payload.get('declared_at')!r} is not an ISO 8601 timestamp with an offset. "
                f"When the bar was set is what makes 'this was declared, not concluded' a "
                f"checkable claim rather than a word in a field")

    window = payload.get("window")
    opens = closes = None
    if not isinstance(window, dict):
        _refuse(problems, "window", "expected an object with 'opens' and 'closes'")
    else:
        _unknown(problems, "window", window, WINDOW_KEYS, "an observation window")
        opens, closes = _time(window.get("opens")), _time(window.get("closes"))
        if opens is None:
            _refuse(problems, "window.opens",
                    f"{window.get('opens')!r} is not an ISO 8601 timestamp with an offset")
        if closes is None:
            _refuse(problems, "window.closes",
                    f"{window.get('closes')!r} is not an ISO 8601 timestamp with an offset")
        if opens is not None and closes is not None and closes <= opens:
            _refuse(problems, "window",
                    "closes at or before it opens, so nothing can fall inside it")
    if declared_at is not None and opens is not None and declared_at > opens:
        _refuse(problems, "declared_at",
                f"{payload['declared_at']} is after the window opens ({window['opens']}): a bar "
                f"chosen once the window was already running was chosen with some of the answer "
                f"in hand, and a floor written from a conclusion is not a floor")

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        _refuse(problems, "metrics", "expected a list")
        metrics = []
    elif not metrics:
        _refuse(problems, "metrics",
                "an expectation that requires nothing is met by everything; omit it instead of "
                "declaring an empty one")

    seen: set[str] = set()
    objectives = 0
    for index, metric in enumerate(metrics):
        where = f"metrics[{index}]"
        if not isinstance(metric, dict):
            _refuse(problems, where, f"expected an object, got {type(metric).__name__}")
            continue
        identifier = metric.get("id")
        if not _text(identifier):
            _refuse(problems, where, f"has no id: {identifier!r} names no metric an adapter "
                                     f"could report a value for")
        elif identifier in seen:
            _refuse(problems, where,
                    f"declares {identifier!r} twice; two bars for one metric is two answers")
        else:
            seen.add(identifier)

        role = metric.get("role")
        if role == OBJECTIVE:
            objectives += 1
        if role not in ROLE_KEYS:
            _refuse(problems, where,
                    f"role {role!r} is not one of {', '.join(ROLES)}")
        else:
            _unknown(problems, where, metric, ROLE_KEYS[role], f"a {role} metric",
                     ((SUPERSEDED_GUARDRAIL_KEYS, _BOUND_REASON),) if role == GUARDRAIL else ())

        if not _text(metric.get("unit")):
            _refuse(problems, where,
                    "has no unit, and a number without one cannot be compared to another")

        # A guardrail is bounded on one named side; everything else is judged as an objective
        # is — including a metric whose `role` is not a role at all, so that a bad `role` does
        # not hide four other problems from an author who is about to fix it and be refused
        # again.
        if role == GUARDRAIL:
            _bound(problems, where, metric)
            continue
        if metric.get("direction") not in DIRECTIONS:
            _refuse(problems, where,
                    f"direction {metric.get('direction')!r} is not one of "
                    f"{', '.join(DIRECTIONS)}")
        needed = ("baseline", "target")
        for field in needed:
            if not _number(metric.get(field)):
                _refuse(problems, where, f"{field} {metric.get(field)!r} is not a finite number")
        if (role == OBJECTIVE and all(_number(metric.get(f)) for f in needed)
                and metric.get("direction") in DIRECTIONS):
            beyond = (metric["target"] < metric["baseline"]
                      if metric["direction"] == DECREASE else
                      metric["target"] > metric["baseline"])
            if not beyond:
                _refuse(problems, where,
                        f"target {metric['target']!r} is not beyond baseline "
                        f"{metric['baseline']!r} in direction {metric['direction']!r}: a bar "
                        f"already cleared before the change makes 'achieved' free")

    if isinstance(metrics, list) and metrics and not objectives:
        # `metrics: []`'s refusal, one step along. A document declaring only what must not
        # break declares nothing that had to move, and the question this module answers is
        # whether the number moved — so every guardrail holding would otherwise reach
        # `achieved`, the strongest word in the vocabulary and the only green exit code, for a
        # change that was required to do nothing. `intent.py` refuses to let an empty positive
        # requirement set reach `satisfied` for the same reason.
        _refuse(problems, "metrics",
                "declares no objective. A guardrail says what must not break; an expectation "
                "with none of the other kind requires nothing to move, and 'achieved' would "
                "mean only that nothing got worse")
    return problems


def validate_observation(payload: object) -> list[str]:
    """Every way this payload is not a production observation document, not the first one."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"observation: expected an object, got {type(payload).__name__}"]

    _unknown(problems, "observation", payload, OBSERVATION_KEYS,
             f"a {OBSERVATION} document", ((BAR_KEYS, _BAR_REASON),))
    if payload.get("schema") != OBSERVATION:
        _refuse(problems, "schema", f"expected {OBSERVATION!r}, got {payload.get('schema')!r}")
    _object_id(problems, "change", payload.get("change"),
               "observations have to name")

    entries = payload.get("observations")
    if not isinstance(entries, list):
        _refuse(problems, "observations", "expected a list")
        entries = []
    for index, entry in enumerate(entries):
        where = f"observations[{index}]"
        if not isinstance(entry, dict):
            _refuse(problems, where, f"expected an object, got {type(entry).__name__}")
            continue
        _unknown(problems, where, entry, ENTRY_KEYS, "an observation",
                 ((BAR_KEYS, _BAR_REASON),))
        if not _text(entry.get("metric")):
            _refuse(problems, where, "names no metric")
        if not _number(entry.get("value")):
            _refuse(problems, where, f"value {entry.get('value')!r} is not a finite number")
        if not _text(entry.get("unit")):
            _refuse(problems, where, "has no unit")
        if entry.get("kind") not in KINDS:
            _refuse(problems, where,
                    f"kind {entry.get('kind')!r} is not one of {', '.join(KINDS)}")
        if _time(entry.get("observed_at")) is None:
            _refuse(problems, where,
                    f"observed_at {entry.get('observed_at')!r} is not an ISO 8601 timestamp "
                    f"with an offset")
        if not _text(entry.get("source")):
            _refuse(problems, where, "does not say where the number came from")
    return problems


def read(path, what: str) -> dict:
    """One of the two documents from disk, refusing what no reader of one should accept.

    The one place either is parsed. `intent.read` and `assurance_target.read` exist because a
    reviewer found three `json.loads` calls disagreeing about a duplicated key: JSON allows a
    key twice and `json.loads` keeps the last one silently, so `"target": 574, "target": 820`
    would be refused by whichever caller thought to check and read as a bar nobody wrote by
    the rest. A rule each caller has to remember is a rule one of them will not.
    """
    import json as _json
    import pathlib as _pathlib

    from .synthesis import _no_duplicate_keys

    return _json.loads(_pathlib.Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=_no_duplicate_keys(what))


def _compare_one(metric: dict, value: float) -> str:
    """One value against one declared bar. Total, and it invents no threshold.

    What each role's bar means, written out because the two roles do not read one field two
    ways:

    * a **guardrail** is bounded on the side its bound is named after. `at_most` is a ceiling:
      the value holds while `value <= at_most`, and exceeding it is `regressed`. `at_least` is
      a floor: it holds while `value >= at_least`. Nothing about a direction enters, and
      turning either comparison around is a mutation that fails the suite — which is the whole
      of why the bound is named rather than derived from a shared `direction`.
    * an **objective** declares `baseline`, `target` and the `direction` improvement runs in.
      `decrease` means the number is meant to fall, so `value <= target` is the target reached
      and a value above `baseline` is `regressed`; `increase` is the same sentence mirrored.

    Reaching the number exactly counts as reaching it, on either role and on either bound.
    rig does not decide what "meaningful movement" is: a 0.4 % improvement is
    `partially-achieved` with both numbers printed, and any threshold rig picked would be rig
    choosing the bar.
    """
    if metric["role"] == GUARDRAIL:
        held = (value <= metric[AT_MOST] if AT_MOST in metric else value >= metric[AT_LEAST])
        return ACHIEVED if held else REGRESSED
    direction = metric["direction"]
    baseline, target = metric["baseline"], metric["target"]
    if (value <= target) if direction == DECREASE else (value >= target):
        return ACHIEVED
    improvement = (baseline - value) if direction == DECREASE else (value - baseline)
    if improvement > 0:
        return PARTIALLY_ACHIEVED
    return NOT_ACHIEVED if improvement == 0 else REGRESSED


def compare(expectation: dict, observation: dict, as_of: str) -> dict:
    """The comparison. A pure function of these three arguments and nothing else.

    Called from exactly one place — :func:`projection` — so that every view of this question
    copies one answer instead of reaching its own. Two readers of one record eventually
    disagree, and a dashboard disagreeing with the recorded report about whether an outcome
    was reached is worse than either being wrong alone.

    `validate_*` is not re-run as a courtesy: a caller handing over an invalid document would
    otherwise get an answer shaped like a verdict, so this raises instead.
    """
    problems = validate_expectation(expectation) + validate_observation(observation)
    if problems:
        raise ValueError("not a production outcome comparison:\n  " + "\n  ".join(problems))
    stamp = _time(as_of)
    if stamp is None:
        raise ValueError(f"--as-of {as_of!r} is not an ISO 8601 timestamp with an offset")
    if expectation["change"] != observation["change"]:
        raise ValueError(f"these observations are about {observation['change']!r} and the "
                         f"expectation is about {expectation['change']!r} — the wrong change's "
                         f"numbers are not weak evidence, they are evidence about something "
                         f"else")

    opens = _time(expectation["window"]["opens"])
    closes = _time(expectation["window"]["closes"])

    by_metric: dict[str, list[dict]] = {}
    for entry in observation["observations"]:
        by_metric.setdefault(entry["metric"], []).append(entry)

    declared = {metric["id"]: metric for metric in expectation["metrics"]}
    metrics: dict[str, dict] = {}
    for identifier, metric in sorted(declared.items()):
        entry = {"role": metric["role"], "unit": metric["unit"],
                 # Whichever bar this role declared, and only that one: a guardrail has no
                 # `direction`, and copying one onto the report would put the field back that
                 # a reader could invert the meaning of.
                 **{key: metric[key] for key in ("direction", "baseline", "target",
                                                 AT_MOST, AT_LEAST) if key in metric},
                 "carried_estimates": 0, "discarded_out_of_window": 0,
                 "discarded_sources": []}
        metrics[identifier] = entry
        seen = by_metric.get(identifier, [])
        if not seen:
            entry.update(outcome=UNMEASURED, value=None,
                         reason="no observation names this metric")
            continue
        inside, outside = [], []
        for observed in seen:
            # Partitioned by position rather than by value: two entries can be equal dicts —
            # the same number from the same source twice — and `e not in inside` would then
            # count neither of them as discarded.
            (inside if opens <= _time(observed["observed_at"]) <= closes else
             outside).append(observed)
        # Counted and named rather than dropped. A measurement outside the window settles
        # nothing — but a report in which three measured regressions disappeared is
        # byte-for-byte a report of one clean measurement, and a reader has to be able to tell
        # "one measurement" from "one measurement and four that disagree".
        entry["discarded_out_of_window"] = len(outside)
        entry["discarded_sources"] = sorted({e["source"] for e in outside})

        # Over the in-window observations alone, and after the partition rather than before
        # it. An observation outside the window settles nothing — this module says so
        # everywhere else, counting it and reading it no further — so letting one refuse the
        # whole comparison would say the opposite: an adapter exporting history across a
        # seconds → milliseconds migration would make a comparison every in-window number can
        # answer unrunnable. Inside the window a unit rig would have to convert is still a
        # refusal, on the metric it was about.
        mismatched = [e for e in inside if e["unit"] != metric["unit"]]
        if mismatched:
            raise ValueError(f"{identifier}: observed in {mismatched[0]['unit']!r}, declared "
                             f"in {metric['unit']!r} — rig does not convert units")

        settling = [e for e in inside if e["kind"] in SETTLING]
        estimates = [e for e in inside if e["kind"] == ESTIMATED]
        entry["carried_estimates"] = len(estimates)
        if len(settling) > 1:
            raise ValueError(
                f"{identifier}: {len(settling)} settling observations inside the window "
                f"({', '.join(sorted(e['source'] for e in settling))}) — rig does not choose "
                f"between them and does not average them; aggregate before declaring the "
                f"observation")
        if not settling:
            entry.update(
                outcome=INCONCLUSIVE, value=None,
                reason=("the only observations for this metric inside the window are "
                        "estimates, and an estimate does not settle whether the target was "
                        "reached" if estimates else
                        "every observation for this metric falls outside the declared window"))
            continue
        only = settling[0]
        entry.update(outcome=_compare_one(metric, only["value"]), value=only["value"],
                     observed_at=only["observed_at"], kind=only["kind"],
                     source=only["source"])

    outcomes = [entry["outcome"] for entry in metrics.values()]
    status = next(outcome for outcome in PRECEDENCE if outcome in outcomes)
    counts = {role: {outcome: sum(1 for entry in metrics.values()
                                  if entry["role"] == role and entry["outcome"] == outcome)
                     for outcome in PRECEDENCE}
              for role in ROLES}
    return {
        "schema": SCHEMA,
        "status": status,
        "final": stamp >= closes,
        "change": expectation["change"],
        "declared_by": expectation["declared_by"],
        "declared_at": expectation["declared_at"],
        "declared_source": expectation["source"],
        "window": {"opens": expectation["window"]["opens"],
                   "closes": expectation["window"]["closes"],
                   "as_of": as_of, "closed": stamp >= closes},
        "metrics": metrics,
        "counts": counts,
        # Neither refused — that would make every real telemetry export unusable — nor dropped,
        # which would let a *misspelled* metric id look like a clean run with a bonus number
        # while the metric it meant to name goes `unmeasured`. Listed, and it changes nothing.
        "unrequested": sorted(set(by_metric) - set(declared)),
        "claim": "observational-not-causal",
    }


#: What the cross-check against a task's receipt can say. `confirmed` is the receipt naming
#: this exact object; `unobservable` is rig having nothing to compare against, which is its own
#: outcome and not a softer contradiction — the same three-way discipline the metrics get.
CONFIRMED, UNOBSERVABLE = "confirmed", "unobservable"


def change_cross_check(receipt: dict, change: str) -> dict:
    """Does the run's receipt agree that this is the change? Raises only when it disagrees.

    Three receipt shapes, and only one of them is two documents contradicting each other:

    * no commit linked — `record-commit` is what writes `commit_sha`, nothing in the flow runs
      it (the ops instruction has the agent *suggest* it), so this is the ordinary case. It is
      reported as `unobservable`, carrying the receipt's own reason, and the metrics still
      decide the exit code. Refusing here would put the common case behind an execution error
      and make `--task`'s two useful fields unreachable for an ordinary task.
    * a commit recorded that is not a full object id, or that git cannot resolve — rig cannot
      establish the identity either. Also `unobservable`, for the same reason: an identity that
      cannot be established is not an identity that was contradicted.
    * a full object id that is a **different** object — the one case where two records
      disagree about which change these numbers are about. That raises.
    """
    target = receipt.get("target") if isinstance(receipt.get("target"), dict) else {}
    head = target.get("head") if isinstance(target.get("head"), dict) else {}
    commit = head.get("commit")
    source = head.get("source")
    if not head.get("observed") or not _text(commit):
        return {"outcome": UNOBSERVABLE, "commit": None, "source": source,
                "reason": head.get("reason") or "the receipt records no commit for this task"}
    commit = str(commit).strip()
    if not OBJECT_ID.fullmatch(commit):
        return {"outcome": UNOBSERVABLE, "commit": commit, "source": source,
                "reason": f"the receipt records {commit!r} (source: {source}), which is an "
                          f"abbreviation rather than a full object id — a prefix that matches "
                          f"is not the same fact as an object that is the same"}
    if not head.get("resolvable"):
        return {"outcome": UNOBSERVABLE, "commit": commit, "source": source,
                "reason": f"the receipt records {commit} (source: {source}) and git cannot "
                          f"resolve it, so the change these observations are about is not a "
                          f"fixed object rig can point at"}
    if commit != change:
        task = (receipt.get("task") or {}).get("id")
        raise ValueError(f"task {task} verified {commit} (source: {source}), and these "
                         f"observations are about {change} — two records disagreeing about "
                         f"which change this is")
    return {"outcome": CONFIRMED, "commit": commit, "source": source, "reason": None}


def projection(expectation: dict, observation: dict, as_of: str, *,
               inputs: dict | None = None, receipt: dict | None = None,
               recorded_outcome: dict | None = None) -> dict:
    """The comparison, plus what other records already decided — copied, never re-judged.

    The one caller of :func:`compare`, and the shape that gets recorded, so a later view
    (Mission Control, #436's graph, #433's learner) copies this document instead of reading the
    two inputs and reaching a second answer.

    `assurance` and `recorded_outcome` are quotations. `final_status` is the receipt's word for
    whether the change was acceptable and `outcome.json` is a human's word for whether anything
    broke; neither is combined with the production status, because #437 asks for exactly that
    separation and because folding three questions into one word is how a dashboard comes to
    say a thing no record says.
    """
    report = compare(expectation, observation, as_of)
    report["inputs"] = dict(inputs or {})
    if receipt is None:
        report["assurance"] = {
            "observed": False,
            "reason": "no --task was given, so no receipt was read; what the gate ruled about "
                      "this change is a different question and is not answered here"}
        report["change_cross_check"] = {
            "outcome": UNOBSERVABLE, "commit": None, "source": None,
            "reason": "no --task was given, so nothing was cross-checked against a receipt. "
                      "The change itself was resolved as a git object"}
    else:
        report["assurance"] = {"observed": True, "task": (receipt.get("task") or {}).get("id"),
                               "final_status": receipt.get("final_status")}
        report["change_cross_check"] = change_cross_check(receipt, report["change"])
    report["recorded_outcome"] = recorded_outcome if recorded_outcome is not None else {
        "observed": False,
        "reason": "no outcome.json for this task — `record-outcome` writes the ok/incident "
                  "flag, and it has not been run for this one"}
    return report


def recorded(run: "pathlib.Path") -> dict | None:  # noqa: F821
    """The comparison recorded for a run, for a view that must not make its own.

    Three facts, not two, in the three words `assurance_wiring` already gives this exact
    question at this layer — imported from it, and used to mean what they mean there:

    * **absent** — `None`. Nothing was recorded, which is a different fact from a comparison
      that says nothing was achieved; a caller defaulting one to the other reports a run
      nobody measured as a run that failed.
    * **unreadable** — a file that is there and will not parse: not JSON, naming one key
      twice, or not readable at all. `assurance_wiring._target` uses the word for that same
      set, and `build_receipt` reaches it for a target naming one key twice.
    * **invalid** — a file that parsed and is not this comparison.

    The last two come back as a dict carrying `not_recorded` and the reason, because "one is
    there and cannot be read" is not "nobody recorded one": the next step is to look at the
    file, not to make the comparison for the first time.

    Read through :func:`read`, the one parser both input documents already go through, so a
    record with a key written twice is *refused* rather than silently resolved to the last
    one — `{"status": "regressed", "status": "achieved"}` handed a bare `json.loads` the
    stronger of the two words, and this is the record a dashboard shows.

    A marker carries no `status` and no `schema`, so a caller that used one as a report fails
    loudly instead of rendering a verdict no record holds.
    """
    path = run / RECORD_NAME
    if not path.is_file():
        return None
    try:
        report = read(path, RECORD_NAME)
    except (OSError, ValueError) as exc:
        return {"not_recorded": UNREADABLE_FILE, "path": str(path),
                "reason": f"{RECORD_NAME} is there and cannot be read — not JSON, naming one "
                          f"key twice, or not readable at all: {exc}"}
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        found = report.get("schema") if isinstance(report, dict) else type(report).__name__
        return {"not_recorded": INVALID, "path": str(path),
                "reason": f"{RECORD_NAME} parsed and is not a {SCHEMA} comparison "
                          f"({found!r}), so nothing here can be copied instead of recomputed"}
    return report


#: What `expected-outcome` returns. `1` covers an invalid document and a shortfall alike:
#: to a caller deciding whether to proceed, both mean the declared outcome has not been shown.
#: The JSON says which. `0` needs `status == achieved` **and** a closed window — an `achieved`
#: reading taken on day three of a fourteen-day window is an interim reading, not a result.
#:
#: `2` is every way the comparison could not be *set up*, and the command has to work to keep
#: that: the state helpers report failure by calling `die()`, which raises `SystemExit` and is
#: not an `Exception`. An unknown `--task` and a working directory outside any repository
#: therefore left by the door marked "looked and came up short" — this module's own
#: *unobservable is not unmet* rule, broken at the process boundary. The root is asked for
#: with the non-dying `maybe_repo_root`, the run directory is checked here, and a `die()`
#: deeper in is caught and turned into this code rather than allowed past.
SHOWN, NOT_SHOWN, EXECUTION_ERROR = 0, 1, 2


def _resolve_change(root, change: str) -> None:
    """Is this object in this repository? Raises if not.

    Shape was refused in `validate_expectation`; this is the other half, and it is where the
    module stops being a pure function of its documents. `provenance_graph` asks git the same
    question the same way, after the same shape check.
    """
    import subprocess

    ok = subprocess.run(["git", "cat-file", "-e", f"{change}^{{object}}"],
                        cwd=str(root), capture_output=True).returncode == 0
    if not ok:
        raise ValueError(f"git cannot resolve {change} in this repository, so the change these "
                         f"numbers are about is not an object rig can point at. An expectation "
                         f"names the change it is about; it is not a comparison rig can set up "
                         f"against a name nothing here holds")


def _render(report: dict) -> None:
    print(f"production outcome: {report['status']} — "
          f"{'final' if report['final'] else 'not final (the window is still open)'}")
    print(f"  change {report['change']} — declared {report['declared_at']} "
          f"by {report['declared_by']} ({report['declared_source']})")
    for identifier, entry in sorted(report["metrics"].items()):
        # The whole bar, direction included. A verdict line a reader cannot check the
        # comparison against is a verdict they have to trust.
        bar = (f"baseline {entry['baseline']} → target {entry['target']} "
               f"({entry['direction']})" if entry["role"] == OBJECTIVE else
               f"at most {entry[AT_MOST]}" if AT_MOST in entry else
               f"at least {entry[AT_LEAST]}")
        value = "no value" if entry.get("value") is None else f"value {entry['value']}"
        print(f"  {entry['outcome']:>19}  {identifier} ({entry['role']}, {entry['unit']}) "
              f"{value} — {bar}")
        if entry.get("reason"):
            print(f"                       {entry['reason']}")
        if entry["carried_estimates"]:
            print(f"                       {entry['carried_estimates']} estimate(s) carried, "
                  f"settling nothing")
        if entry["discarded_out_of_window"]:
            print(f"                       {entry['discarded_out_of_window']} observation(s) "
                  f"outside the window settled nothing: "
                  f"{', '.join(entry['discarded_sources'])}")
    if report["unrequested"]:
        print(f"  unrequested (judges nothing): {', '.join(report['unrequested'])}")
    assurance = report["assurance"]
    if assurance.get("observed"):
        cross = report["change_cross_check"]
        # Only under `--task`. Without one nothing was asked of a receipt, and a line saying
        # so on every run would read as a check that ran and could not answer.
        print(f"  change cross-check: {cross['outcome']}"
              + (f" — {cross['reason']}" if cross["reason"] else
                 f" — the receipt records this object (source: {cross['source']})"))
        final_status = assurance["final_status"]
        if isinstance(final_status, dict):
            final_status = f"{final_status.get('value')} ({final_status.get('basis')})"
        print(f"  assurance (copied, never combined): {final_status}")
        outcome = report["recorded_outcome"]
        print("  recorded outcome (copied, never combined): "
              + (str(outcome.get("status")) if outcome.get("observed") is not False
                 else f"none — {outcome['reason']}"))
    print(f"  claim: {report['claim']}")


def cmd_production_outcome(args) -> "NoReturn":  # noqa: F821
    """Compare a declared expected outcome against what production was observed to do.

    Exits rather than returns, like `cmd_assurance_target`: the dispatcher calls subcommands
    for their effect and discards what they hand back, so a shortfall that returned `1` would
    print itself and leave the shell believing the outcome held.
    """
    import json
    import sys

    from .state import load_json, maybe_repo_root, runs_dir, save_json

    def stop(error: str) -> "NoReturn":  # noqa: F821
        print(json.dumps({"schema": SCHEMA, "status": "execution-error", "error": error},
                         ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    try:
        expectation = read(args.expected, "expectation")
        observation = read(args.observed, "observation")
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        stop(f"{type(exc).__name__}: {exc}")

    problems = validate_expectation(expectation) + validate_observation(observation)
    if problems:
        print("\n".join(f"[REJECTED] {problem}" for problem in problems), file=sys.stderr)
        sys.exit(NOT_SHOWN)

    receipt = None
    outcome = None
    run = None
    try:
        root = maybe_repo_root()
        if root is None:
            raise ValueError(
                "this working directory is not inside a git repository, and the change an "
                "expectation names is resolved as a git object before anything is compared. "
                "Nothing was measured and nothing fell short: the comparison could not be "
                "set up")
        _resolve_change(root, expectation["change"])
        if args.task:
            from .assurance import build_receipt

            # `run_dir` answers this by calling `die()`, and a `SystemExit` would leave here
            # as exit 1 — the code that means the outcome was not shown — with no JSON to say
            # otherwise. The same question, asked so that the answer is a value.
            run = runs_dir(root) / args.task
            if not run.is_dir():
                raise ValueError(
                    f"task {args.task!r} has no run directory ({run.relative_to(root)}), so "
                    f"there is no receipt to cross-check the change against and nowhere to "
                    f"record the comparison. List tasks with `workbench.py log`")
            receipt = build_receipt(root, args.task)
            recorded_file = run / "outcome.json"
            if recorded_file.is_file():
                outcome = {"observed": True, **load_json(recorded_file)}
        report = projection(expectation, observation, args.as_of,
                            inputs={"expectation": str(args.expected),
                                    "observations": str(args.observed)},
                            receipt=receipt, recorded_outcome=outcome)
        if run is not None:
            # Recorded, so that the next reader copies this comparison instead of making a
            # second one from the same two files. Written only under `--task`, because without
            # a run there is nowhere that belongs to.
            save_json(run / RECORD_NAME, report)
    except SystemExit as exc:  # a `die()` deeper in: still a setup failure, not a shortfall
        stop(f"a step of the setup exited with status {exc.code} instead of returning; its "
             f"reason was printed above. The comparison was never made")
    except Exception as exc:  # noqa: BLE001 — every failure to set the comparison up is one
        stop(f"{type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _render(report)
        if run is not None:
            print(f"  recorded: {(run / RECORD_NAME)}")
    sys.exit(SHOWN if report["status"] == ACHIEVED and report["final"] else NOT_SHOWN)
