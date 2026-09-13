"""The promotion lifecycle a knowledge candidate goes through to become organizational
knowledge, and the refusals that make it a lifecycle rather than a label (#440, stage 2).

`knowledge_candidate.assess` answers one question: is this candidate explicitly supported
by the records it cites. Its docstring is careful to say that a `supported` candidate is
still not approved and not organizational knowledge. This module is the distance between
those two, and it is mostly a set of refusals.

**A separate layer from instincts.** An instinct is an unverified hint that decays if
nobody confirms it; being cheap to be wrong is its value. Organizational knowledge is
evidence-backed and does not decay. Writing either into the other's store breaks the
property the destination did not have — instincts start carrying claims nobody may ignore,
or a verified lesson quietly expires after thirty days. So this never reads or writes
`.rig/instincts*`, and a test pins that by inspecting the module rather than grepping it.

**The lifecycle is a path, not a set of labels.** `candidate → evaluated → approved →
active`, then `deprecated`; `rolled_back` leaves `approved` or `active`. One step at a
time. The issue's first non-goal is turning one failure straight into policy, and a
lifecycle that can be skipped is not a control. A refused transition names the states
that *are* reachable, so the caller does not have to guess the next legal move.

**Approval is a named human act.** From `approved` onward every transition requires an
`actor` and a `reason`, and nothing in this module generates either. A model may draft a
candidate and may advance it to `evaluated`; it cannot approve. Which transitions demand a
name is the whole of that line.

**Conflicts are presented, never resolved.** An `active` entry with the same rule in an
overlapping scope blocks a second one from becoming active, naming both ids; the way
through is to deprecate the old one on purpose. Only the structural fact is detected — same
rule text, overlapping scope — because reading two differently worded rules and deciding
they contradict is a judgement a person has to make, and this is exactly where a tool
should not pretend to.

**The ledger is append-only.** Rolling back leaves "promoted, then withdrawn" in the
record; knowledge that could be rewritten cannot answer why it started and why it stopped,
which is the reason to keep it at all. Current state is derived by replaying the ledger,
so there is one record of each fact and nothing to disagree with it.

`claimed_confidence` stays what the author claimed. Counting readable records does not turn
it into a confidence rig measured, and that does not change at a module boundary.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

from .knowledge_candidate import SUPPORTED, assess, read

SCHEMA = "rig.org-knowledge/v1"
LEDGER = pathlib.PurePosixPath(".rig/org-knowledge.jsonl")

CANDIDATE = "candidate"
EVALUATED = "evaluated"
APPROVED = "approved"
ACTIVE = "active"
DEPRECATED = "deprecated"
ROLLED_BACK = "rolled_back"

#: The path. Every transition a record may take, and no others.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    CANDIDATE: (EVALUATED,),
    EVALUATED: (APPROVED,),
    APPROVED: (ACTIVE, ROLLED_BACK),
    ACTIVE: (DEPRECATED, ROLLED_BACK),
    DEPRECATED: (),
    ROLLED_BACK: (),
}

#: Transitions that are a person's act. Entering any of these needs an actor and a reason.
NAMED_TRANSITIONS = frozenset({APPROVED, ACTIVE, DEPRECATED, ROLLED_BACK})

_ID = re.compile(r"^ok-[0-9]{8}-[0-9a-f]{8}$")


class OrgKnowledgeError(ValueError):
    """A refused operation, with the legal alternatives in the message."""


def ledger_path(root: pathlib.Path) -> pathlib.Path:
    return root / LEDGER


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _new_id(candidate: dict) -> str:
    import hashlib
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode("utf-8")).hexdigest()
    return f"ok-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{digest[:8]}"


def _append(root: pathlib.Path, event: dict) -> None:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _events(root: pathlib.Path) -> list[dict]:
    path = ledger_path(root)
    if not path.exists():
        return []
    out = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OrgKnowledgeError(f"{path}:{number}: unreadable ledger line: {exc}") from exc
        if not isinstance(event, dict) or event.get("schema") != SCHEMA:
            raise OrgKnowledgeError(f"{path}:{number}: not a {SCHEMA} event")
        out.append(event)
    return out


def replay(root: pathlib.Path) -> dict[str, dict]:
    """Current state of every record, derived from the ledger and nothing else."""
    records: dict[str, dict] = {}
    for event in _events(root):
        kind = event.get("event")
        if kind == "register":
            records[event["id"]] = {
                "id": event["id"], "state": CANDIDATE,
                "registered_at": event["ts"], "updated_at": event["ts"],
                "candidate": event["candidate"], "assessment": event["assessment"],
                "history": [{"ts": event["ts"], "to": CANDIDATE}],
            }
        elif kind == "transition":
            record = records.get(event["id"])
            if record is None:
                raise OrgKnowledgeError(f"ledger transitions {event['id']!r} before registering it")
            record["state"] = event["to"]
            record["updated_at"] = event["ts"]
            record["history"].append({k: event[k] for k in ("ts", "to", "actor", "reason")
                                      if k in event})
        else:
            raise OrgKnowledgeError(f"unknown ledger event {kind!r}")
    return records


def register(root: pathlib.Path, candidate_path: pathlib.Path | str) -> dict:
    """Enter a candidate into the lifecycle, at `candidate`.

    Only a candidate `assess` calls `supported` gets in. Registering an unsupported or
    unobservable one would put a rule into the pipeline that its own citations do not back,
    and every later transition would inherit that as if it had been checked.
    """
    path = pathlib.Path(candidate_path)
    candidate = read(path, "knowledge candidate")
    assessment = assess(candidate, path)
    if assessment["status"] != SUPPORTED:
        raise OrgKnowledgeError(
            f"candidate is {assessment['status']}, not supported; only a supported candidate "
            f"enters the lifecycle (" + "; ".join(assessment["unsupported"]
                                                  + assessment["unobservable"]) + ")")
    record_id = _new_id(candidate)
    if record_id in replay(root):
        raise OrgKnowledgeError(f"{record_id} is already registered today with this content")
    _append(root, {"schema": SCHEMA, "event": "register", "id": record_id, "ts": _now(),
                   "candidate": candidate,
                   "assessment": {"status": assessment["status"],
                                  "evidence": assessment["evidence"],
                                  "claimed_confidence": assessment["confidence"]["claimed"],
                                  "verified_confidence": None}})
    return replay(root)[record_id]


def _scopes_overlap(a: list[str], b: list[str]) -> bool:
    return bool(set(a) & set(b))


def conflicts(root: pathlib.Path, record: dict, records: dict[str, dict] | None = None) -> list[str]:
    """Ids of active records with the same rule in an overlapping scope. Structural only."""
    records = replay(root) if records is None else records
    rule = record["candidate"]["proposed_rule"]
    scope = record["candidate"]["scope"]
    return sorted(other["id"] for other in records.values()
                  if other["id"] != record["id"] and other["state"] == ACTIVE
                  and other["candidate"]["proposed_rule"] == rule
                  and _scopes_overlap(other["candidate"]["scope"], scope))


def promote(root: pathlib.Path, record_id: str, to: str, *,
            actor: str | None = None, reason: str | None = None) -> dict:
    """Move one record one step along the path, or refuse and say which steps exist."""
    if not _ID.match(record_id):
        raise OrgKnowledgeError(f"{record_id!r} is not an org-knowledge id")
    records = replay(root)
    record = records.get(record_id)
    if record is None:
        raise OrgKnowledgeError(f"{record_id} is not registered; register a supported candidate first")
    allowed = TRANSITIONS[record["state"]]
    if to not in allowed:
        reachable = ", ".join(allowed) if allowed else "nothing (terminal state)"
        raise OrgKnowledgeError(
            f"{record_id} is {record['state']}; it can only move to: {reachable}")
    if to in NAMED_TRANSITIONS and not (actor and actor.strip() and reason and reason.strip()):
        raise OrgKnowledgeError(
            f"entering {to} is a named human act: --actor and --reason are required, and "
            f"nothing in rig fills them in")
    if to == ACTIVE:
        clash = conflicts(root, record, records)
        if clash:
            raise OrgKnowledgeError(
                f"{record_id} cannot become active: the same rule is already active in an "
                f"overlapping scope as {', '.join(clash)}. Deprecate that record on purpose "
                f"first; rig does not decide which of two rules wins")
    event = {"schema": SCHEMA, "event": "transition", "id": record_id, "ts": _now(),
             "from": record["state"], "to": to}
    if to in NAMED_TRANSITIONS:
        event["actor"] = actor.strip()
        event["reason"] = reason.strip()
    _append(root, event)
    return replay(root)[record_id]


def listing(root: pathlib.Path, *, state: str | None = None) -> list[dict]:
    rows = []
    for record in sorted(replay(root).values(), key=lambda r: r["id"]):
        if state and record["state"] != state:
            continue
        rows.append({"id": record["id"], "state": record["state"],
                     "rule": record["candidate"]["proposed_rule"],
                     "scope": record["candidate"]["scope"],
                     "claimed_confidence": record["assessment"]["claimed_confidence"],
                     "verified_confidence": None,
                     "updated_at": record["updated_at"]})
    return rows


def history(root: pathlib.Path, record_id: str) -> dict:
    record = replay(root).get(record_id)
    if record is None:
        raise OrgKnowledgeError(f"{record_id} is not registered")
    return {"id": record["id"], "state": record["state"], "history": record["history"],
            "candidate": record["candidate"], "assessment": record["assessment"]}


def active_rules(root: pathlib.Path) -> list[dict]:
    """What a workflow synthesis may read: only what is active, with its citations kept.

    Returned rather than injected anywhere: which consumer reads it, and how, is that
    consumer's decision (the issue's "future workflow synthesis can use active knowledge").
    """
    return [{"id": r["id"], "rule": r["candidate"]["proposed_rule"],
             "scope": r["candidate"]["scope"],
             "applicable_context": r["candidate"]["applicable_context"],
             "known_exceptions": r["candidate"]["known_exceptions"],
             "citations": r["candidate"]["triggering_evidence"],
             "claimed_confidence": r["assessment"]["claimed_confidence"]}
            for r in sorted(replay(root).values(), key=lambda r: r["id"])
            if r["state"] == ACTIVE]
