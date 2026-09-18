"""Pure recovery policy over one runner-owned unit's ordered event history.

Shape validation cannot prove provenance or detect a truncated/re-written history.
The runner must persist the complete history and immutable limits outside AI write
access. This module supplies neither that boundary nor execution or cost budgets.
Repetition is cumulative for reporting; retry cadence counts failures since the
last replan for that failure. At the default threshold of two, a replan grants
one repair on its next recurrence; threshold one immediately requests replan.
Any failure after max_replans completed replans escalates, as does the total cap.
Version 1 has no authorized resume or upstream-completion event. BLOCKED,
AWAIT_DECISION, ESCALATE, DESIGN and REQUIREMENTS therefore end this history;
appending failures cannot stand in for resolving those decisions. REPLAN must
be followed by an explicit eligible ReplanEvent before another failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal

Classification = Literal["IMPLEMENTATION", "DESIGN", "REQUIREMENT", "ENVIRONMENT", "CHECK_ERROR", "UNKNOWN"]
Action = Literal["REPAIR", "REPLAN", "DESIGN", "REQUIREMENTS", "BLOCKED", "AWAIT_DECISION", "ESCALATE"]


@dataclass(frozen=True)
class FailureEvent:
    sequence: int
    check_id: str
    error_class: str
    target_id: str
    classification: Classification


@dataclass(frozen=True)
class ReplanEvent:
    sequence: int
    failure_id: str


@dataclass(frozen=True)
class RecoveryLimits:
    repeat_threshold: int = 2
    max_total_failures: int = 6
    max_replans: int = 2


@dataclass(frozen=True)
class RecoveryDecision:
    action: Action
    reason: str
    failure_id: str | None = None
    total_failures: int = 0
    repeated_failures: int = 0
    replans: int = 0


def _identifier(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def stable_failure_id(check_id: str, error_class: str, target_id: str) -> str:
    """Hash a versioned JSON tuple, excluding prose, time, attempt and code hash."""
    if not all(_identifier(v) for v in (check_id, error_class, target_id)):
        raise ValueError("failure identity components must be nonempty strings")
    payload = json.dumps(["rig-failure-v1", check_id, error_class, target_id],
                         ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decide_recovery(
    history: tuple[FailureEvent | ReplanEvent, ...],
    limits: RecoveryLimits = RecoveryLimits(),
) -> RecoveryDecision:
    """Return a deterministic transition; malformed history always stops.

    Sequence starts at one and is contiguous. Replans must immediately follow a
    failure whose decision was REPLAN, and identify that exact failure. Counters
    are derived here, never supplied by an AI. UNKNOWN remains a valid stopping
    classification; unrecognized classification invalidates the whole history.
    """
    if type(limits) is not RecoveryLimits or any(
        type(v) is not int or v < 1
        for v in (limits.repeat_threshold, limits.max_total_failures, limits.max_replans)
    ):
        raise ValueError("limits must be positive integers (not booleans)")
    invalid = RecoveryDecision("AWAIT_DECISION", "invalid_history")
    if type(history) is not tuple or not history or type(history[-1]) is not FailureEvent:
        return invalid
    routes: dict[str, Action] = {
        "IMPLEMENTATION": "REPAIR", "DESIGN": "DESIGN", "REQUIREMENT": "REQUIREMENTS",
        "ENVIRONMENT": "BLOCKED", "CHECK_ERROR": "BLOCKED", "UNKNOWN": "AWAIT_DECISION",
    }
    totals: dict[str, int] = {}
    since_replan: dict[str, int] = {}
    total = replans = 0
    previous: RecoveryDecision | None = None
    for sequence, event in enumerate(history, start=1):
        if type(event) not in (FailureEvent, ReplanEvent):
            return invalid
        if type(event.sequence) is not int or event.sequence != sequence:
            return invalid
        if previous is not None:
            if previous.action not in ("REPAIR", "REPLAN"):
                return invalid
            if previous.action == "REPLAN" and type(event) is not ReplanEvent:
                return invalid
        if type(event) is ReplanEvent:
            if (previous is None or previous.action != "REPLAN"
                    or not _identifier(event.failure_id)
                    or event.failure_id != previous.failure_id):
                return invalid
            replans += 1
            since_replan[event.failure_id] = 0
            previous = None
            continue
        if (type(event.classification) is not str or event.classification not in routes
                or not all(_identifier(v) for v in (event.check_id, event.error_class, event.target_id))):
            return invalid
        identity = stable_failure_id(event.check_id, event.error_class, event.target_id)
        total += 1
        totals[identity] = totals.get(identity, 0) + 1
        since_replan[identity] = since_replan.get(identity, 0) + 1
        action = routes[event.classification]
        reason = "classification"
        if total >= limits.max_total_failures:
            action, reason = "ESCALATE", "total_failure_limit"
        elif replans >= limits.max_replans:
            action, reason = "ESCALATE", "replan_limit"
        elif action == "REPAIR" and since_replan[identity] >= limits.repeat_threshold:
            action, reason = "REPLAN", "repeated_failure"
        previous = RecoveryDecision(action, reason, identity, total, totals[identity], replans)
    return previous if previous is not None else invalid
