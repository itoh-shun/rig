"""Promoting an evidence-backed lesson into versioned organizational knowledge (#440).

`knowledge_candidate` answers one question: do the cited records explicitly support this
claim?  It says in as many words that a supported candidate is still not "approved, or
organizational knowledge".  This module is the distance between those two, and it is
deliberately made of refusals rather than of machinery.

**A separate layer from instincts, on purpose.**  Instincts are light, unverified hints that
decay when nothing refreshes them — their whole value is that they cost nothing to be wrong.
Organizational knowledge is evidence-backed and does not decay.  Writing one into the other
would destroy whichever property the destination did not have: instincts would start carrying
claims nobody may ignore, or verified lessons would quietly expire after thirty days.

**Nothing reaches `active` without passing through every state before it.**  The lifecycle is
a path, not a set of labels: `candidate → evaluated → approved → active`, then `deprecated`
or `rolled_back`.  A single incident cannot become an organization default in one move, which
is the first thing #440 lists among its non-goals.

**Approval is a recorded human act.**  `approved` requires an actor and a reason, and no code
path anywhere in this module produces that transition on its own.  An LLM may draft a
candidate and may evaluate it; it may not approve it.

**Conflicts are shown, never resolved.**  Registering a rule that already has an `active`
version at an overlapping scope is refused with both versions named.  What this module detects
is structural — same rule text, overlapping scope — and it says so: rig does not read two
rules and decide whether they contradict each other, and a module that claimed to would be
inventing the one judgement a human is here to make.

**Versions accumulate; nothing is edited.**  The store is append-only, so a rolled-back
promotion leaves the record that it happened.  Knowledge whose history can be rewritten
cannot answer "why did we start doing this?", which is the question the whole feature exists
to serve.
"""

from __future__ import annotations

import datetime
import json
import pathlib

SCHEMA = "rig.organization-knowledge/v1"
STORE_NAME = "organization-knowledge.jsonl"

CANDIDATE = "candidate"
EVALUATED = "evaluated"
APPROVED = "approved"
ACTIVE = "active"
DEPRECATED = "deprecated"
ROLLED_BACK = "rolled_back"

#: The only moves that exist. A state absent from a value's list cannot be reached from it,
#: however good the reason — the point of a lifecycle is that it cannot be short-circuited by
#: whoever is in a hurry.
TRANSITIONS = {
    CANDIDATE: (EVALUATED, ROLLED_BACK),
    EVALUATED: (APPROVED, ROLLED_BACK),
    APPROVED: (ACTIVE, ROLLED_BACK),
    ACTIVE: (DEPRECATED, ROLLED_BACK),
    DEPRECATED: (),
    ROLLED_BACK: (),
}

#: Transitions nobody may make without saying who they are and why. Approval is the moment
#: organizational knowledge stops being a proposal, and an unattributed approval is not one.
ATTRIBUTED = frozenset({APPROVED, ACTIVE, DEPRECATED, ROLLED_BACK})


class PromotionRefused(Exception):
    """A promotion that would skip a state, lack attribution, or overwrite active knowledge."""


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def store_path(root: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(root) / ".rig" / STORE_NAME


def load(root: pathlib.Path | str) -> list[dict]:
    """Every recorded event, oldest first. A malformed line is skipped, never guessed at."""
    path = store_path(root)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("schema") == SCHEMA:
            events.append(event)
    return events


def _append(root: pathlib.Path | str, event: dict) -> dict:
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def current(root: pathlib.Path | str) -> dict[str, dict]:
    """The latest event per knowledge id, which is the state each one is in now.

    Derived by replaying the log rather than stored beside it: two records of the same fact
    disagree eventually, and the log is the one that cannot be edited into agreement.
    """
    latest: dict[str, dict] = {}
    for event in load(root):
        latest[event["id"]] = event
    return latest


def _version_of(root: pathlib.Path | str, rule: str) -> int:
    seen = {event["version"] for event in load(root) if event["rule"] == rule}
    return (max(seen) + 1) if seen else 1


def _overlaps(left: list, right: list) -> bool:
    return bool(set(left) & set(right))


def conflicts(root: pathlib.Path | str, rule: str, scope: list) -> list[dict]:
    """Active knowledge this registration would collide with.

    Structural only, and this module says so rather than implying more: same rule text at an
    overlapping scope. Rig does not read two differently-worded rules and decide whether they
    contradict each other — that is the judgement a human is here to make, and a tool that
    claimed it would be wrong precisely where being wrong is expensive.
    """
    return [event for event in current(root).values()
            if event["state"] == ACTIVE and event["rule"] == rule
            and _overlaps(event["scope"], scope)]


def register(root: pathlib.Path | str, candidate: dict, assessment: dict) -> dict:
    """Record a supported candidate as version N of a rule, in state `candidate`.

    An unsupported or unobservable assessment is refused. `unobservable` is refused for the
    same reason `knowledge_candidate` keeps it distinct from `unsupported`: failing to read a
    record is not evidence against a claim, and it is never evidence for one either — and
    registering organizational knowledge is a use of evidence *for*.
    """
    status = assessment.get("status")
    if status != "supported":
        raise PromotionRefused(
            f"a candidate whose evidence assessment is {status!r} is not registrable; only a "
            f"'supported' assessment is evidence for a claim")
    rule = candidate["proposed_rule"]
    scope = list(candidate["scope"])
    existing = conflicts(root, rule, scope)
    if existing:
        raise PromotionRefused(
            "active organizational knowledge already covers this rule at an overlapping "
            f"scope: {', '.join(sorted(e['id'] for e in existing))}. Deprecate it explicitly "
            "rather than registering a second active version — rig will not decide which of "
            "two rules a team meant to follow")
    version = _version_of(root, rule)
    return _append(root, {
        "schema": SCHEMA,
        "id": f"{_slug(rule)}-v{version}",
        "rule": rule,
        "version": version,
        "state": CANDIDATE,
        "scope": scope,
        "applicable_context": list(candidate["applicable_context"]),
        "known_exceptions": list(candidate["known_exceptions"]),
        "expected_benefit": candidate["expected_benefit"],
        # Copied, never recomputed: the claim stays the candidate author's, exactly as the
        # assessment records it. Counting readable records did not turn it into a measurement
        # there, and moving it here does not either.
        "claimed_confidence": candidate["confidence"],
        "evidence": dict(assessment["evidence"]),
        # Where this came from, kept so the provenance graph (#436) can reach the records
        # rather than only the conclusion drawn from them.
        "citations": [dict(item) for item in candidate["triggering_evidence"]],
        "at": _now(),
        "actor": None,
        "reason": None,
    })


def _slug(rule: str) -> str:
    keep = [character.lower() if character.isalnum() else "-" for character in rule]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:60] or "rule"


def transition(root: pathlib.Path | str, knowledge_id: str, to_state: str, *,
               actor: str | None = None, reason: str | None = None) -> dict:
    """Move one piece of knowledge one step along the lifecycle.

    One step: the path is `candidate → evaluated → approved → active`, and a caller that
    wants `active` has to walk it. Allowing a jump would make the states decorative, and a
    lifecycle nobody has to pass through is not a control.
    """
    latest = current(root).get(knowledge_id)
    if latest is None:
        raise PromotionRefused(f"no organizational knowledge with id {knowledge_id!r}")
    allowed = TRANSITIONS[latest["state"]]
    if to_state not in allowed:
        reachable = ", ".join(allowed) if allowed else "nothing; this is a terminal state"
        raise PromotionRefused(
            f"{knowledge_id} is {latest['state']!r}; from there it can become {reachable}. "
            f"{to_state!r} is not reachable in one step and rig will not take several")
    if to_state in ATTRIBUTED and not (actor and reason):
        raise PromotionRefused(
            f"{to_state!r} requires an actor and a reason. Approval is the moment this stops "
            f"being a proposal, and an unattributed one is not an approval")
    if to_state == ACTIVE:
        blocking = [event for event in conflicts(root, latest["rule"], latest["scope"])
                    if event["id"] != knowledge_id]
        if blocking:
            raise PromotionRefused(
                "another version of this rule is already active at an overlapping scope: "
                + ", ".join(sorted(event["id"] for event in blocking)))
    return _append(root, {**latest, "state": to_state, "at": _now(),
                          "actor": actor, "reason": reason})


def active(root: pathlib.Path | str, scope: list | None = None) -> list[dict]:
    """Knowledge a future workflow may rely on: `active`, optionally within a scope.

    Only `active`. An approved-but-not-activated rule is a decision that has been made and not
    yet applied, and a workflow that treated the two alike would start enforcing rules nobody
    had switched on.
    """
    rows = [event for event in current(root).values() if event["state"] == ACTIVE]
    if scope is not None:
        rows = [event for event in rows if _overlaps(event["scope"], scope)]
    return sorted(rows, key=lambda event: event["id"])


def history(root: pathlib.Path | str, knowledge_id: str) -> list[dict]:
    """Every state this knowledge has been in, oldest first — including a rollback.

    The rolled-back entry stays. Knowledge whose history can be rewritten cannot answer "why
    did we start doing this, and why did we stop?", which is the question this feature exists
    to serve.
    """
    return [event for event in load(root) if event["id"] == knowledge_id]


def cmd_org_knowledge(args) -> "NoReturn":  # noqa: F821
    """`register` / `promote` / `list` / `history` over the organizational knowledge store."""
    import sys

    from .knowledge_candidate import assess, read
    from .state import repo_root

    root = repo_root()
    try:
        if args.action == "register":
            path = pathlib.Path(args.candidate)
            candidate = read(path, "knowledge candidate")
            event = register(root, candidate, assess(candidate, path))
            print(f"registered {event['id']} (version {event['version']}, state candidate)")
            print("  next: rig-wb wb org-knowledge promote "
                  f"{event['id']} --to evaluated")
        elif args.action == "promote":
            event = transition(root, args.id, args.to, actor=args.actor, reason=args.reason)
            print(f"{event['id']}: now {event['state']}"
                  + (f" (by {event['actor']}: {event['reason']})" if event["actor"] else ""))
        elif args.action == "list":
            rows = active(root, args.scope or None) if args.active_only else list(
                current(root).values())
            if args.json:
                print(json.dumps(sorted(rows, key=lambda e: e["id"]),
                                 ensure_ascii=False, indent=2, sort_keys=True))
            elif not rows:
                print("no organizational knowledge recorded")
            else:
                for event in sorted(rows, key=lambda e: e["id"]):
                    print(f"  {event['state']:<11} {event['id']}  "
                          f"scope={','.join(event['scope'])}")
        elif args.action == "history":
            events = history(root, args.id)
            if not events:
                print(f"no organizational knowledge with id {args.id!r}", file=sys.stderr)
                sys.exit(1)
            for event in events:
                actor = f" by {event['actor']}" if event["actor"] else ""
                reason = f" — {event['reason']}" if event["reason"] else ""
                print(f"  {event['at']}  {event['state']:<11}{actor}{reason}")
    except PromotionRefused as refusal:
        print(f"[REFUSED] {refusal}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
