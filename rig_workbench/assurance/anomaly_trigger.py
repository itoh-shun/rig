"""Assess whether a caller-authored production anomaly event can start investigation.

This module validates and resolves an external event; it does not detect anomalies.  A ready
assessment guarantees only that the event declares the minimum investigation material and
that every cited record explicitly supports it.  It does not guarantee that the event is a
real anomaly or regression, that its severity or confidence is correct, that any repository,
component, or change caused it, that it can be reproduced, or that a fix exists.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib

from .synthesis import _no_duplicate_keys

EVENT_SCHEMA = "rig.production-anomaly-event/v1"
EVIDENCE_SCHEMA = "rig.production-anomaly-evidence/v1"
SCHEMA = "rig.production-anomaly-trigger-assessment/v1"

READY = "ready"
UNMET = "unmet"
UNOBSERVABLE = "unobservable"

GUARANTEE = (
    "the event declares the minimum investigation material and every cited record "
    "explicitly supports it"
)
DOES_NOT_GUARANTEE = (
    "the event represents a real anomaly or regression, that its severity or confidence is "
    "correct, that any repository, component, or change caused it, that it can be reproduced, "
    "or that a fix exists"
)

EVENT_KEYS = frozenset({
    "schema", "id", "source", "detected_at", "window", "signal", "scope", "evidence",
    "severity", "confidence",
})
CITATION_KEYS = frozenset({"path", "record"})
SOURCE_KEYS = frozenset({"system", "event"})
WINDOW_KEYS = frozenset({"opens", "closes"})
SIGNAL_KEYS = frozenset({"kind", "observation", "comparison"})
SCOPE_KEYS = frozenset({"environment", "components"})
EVIDENCE_KEYS = frozenset({"schema", "records"})
RECORD_KEYS = frozenset({
    "id", "source", "observed_at", "observations", "comparisons", "environments",
    "components",
})


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _time(value: object) -> dt.datetime | None:
    if not _text(value):
        return None
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _string_list(value: object) -> bool:
    return (isinstance(value, list) and bool(value) and all(_text(item) for item in value)
            and len(set(value)) == len(value))


def _unknown(problems: list[str], where: str, value: dict, allowed: frozenset[str]) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)}")


def _closed_object(problems: list[str], where: str, value: object,
                   allowed: frozenset[str]) -> dict | None:
    if not isinstance(value, dict):
        problems.append(f"{where}: expected an object")
        return None
    _unknown(problems, where, value, allowed)
    return value


def validate_event(payload: object) -> list[str]:
    """Enumerate every reason the value is not a production anomaly event."""
    if not isinstance(payload, dict):
        return [f"event: expected an object, got {type(payload).__name__}"]
    problems: list[str] = []
    _unknown(problems, "event", payload, EVENT_KEYS)
    if payload.get("schema") != EVENT_SCHEMA:
        problems.append(f"schema: expected {EVENT_SCHEMA!r}, got {payload.get('schema')!r}")
    if not _text(payload.get("id")):
        problems.append("id: expected a non-blank string")

    source = _closed_object(problems, "source", payload.get("source"), SOURCE_KEYS)
    if source is not None:
        for field in SOURCE_KEYS:
            if not _text(source.get(field)):
                problems.append(f"source.{field}: expected a non-blank string")

    detected_at = _time(payload.get("detected_at"))
    if detected_at is None:
        problems.append("detected_at: expected an ISO 8601 timestamp with a timezone offset")
    window = _closed_object(problems, "window", payload.get("window"), WINDOW_KEYS)
    opens = closes = None
    if window is not None:
        opens, closes = _time(window.get("opens")), _time(window.get("closes"))
        if opens is None:
            problems.append("window.opens: expected an ISO 8601 timestamp with a timezone offset")
        if closes is None:
            problems.append("window.closes: expected an ISO 8601 timestamp with a timezone offset")
        if opens is not None and closes is not None and opens > closes:
            problems.append("window: opens must be at or before closes")
    if detected_at is not None and opens is not None and detected_at < opens:
        problems.append("detected_at: expected a timestamp at or after window.opens")

    signal = _closed_object(problems, "signal", payload.get("signal"), SIGNAL_KEYS)
    if signal is not None:
        for field in SIGNAL_KEYS:
            if not _text(signal.get(field)):
                problems.append(f"signal.{field}: expected a non-blank string")

    scope = _closed_object(problems, "scope", payload.get("scope"), SCOPE_KEYS)
    if scope is not None:
        if not _text(scope.get("environment")):
            problems.append("scope.environment: expected a non-blank string")
        if not _string_list(scope.get("components")):
            problems.append(
                "scope.components: expected a non-empty list of unique non-blank strings")

    if not _text(payload.get("severity")):
        problems.append("severity: expected a non-blank string; this remains source-claimed")
    confidence = payload.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        problems.append(
            "confidence: expected a finite number between 0 and 1; this remains source-claimed")

    citations = payload.get("evidence")
    if not isinstance(citations, list):
        problems.append("evidence: expected a list of record citations")
    elif not citations:
        problems.append("evidence: expected at least one citation")
    else:
        seen: set[tuple[str, str]] = set()
        for index, citation in enumerate(citations):
            where = f"evidence[{index}]"
            if not isinstance(citation, dict):
                problems.append(f"{where}: expected an object")
                continue
            _unknown(problems, where, citation, CITATION_KEYS)
            path, record = citation.get("path"), citation.get("record")
            if not _text(path):
                problems.append(f"{where}.path: expected a non-blank string")
            elif pathlib.PurePath(path).is_absolute() or ".." in pathlib.PurePath(path).parts:
                problems.append(f"{where}.path: expected a relative path without '..'")
            if not _text(record):
                problems.append(f"{where}.record: expected a non-blank string")
            if _text(path) and _text(record):
                key = (path, record)
                if key in seen:
                    problems.append(f"{where}: duplicate citation {path}#{record}")
                seen.add(key)
    return problems


def validate_evidence(payload: object, source: str) -> list[str]:
    """Validate a closed evidence envelope before any record in it is trusted."""
    if not isinstance(payload, dict):
        return [f"{source}: expected an evidence object, got {type(payload).__name__}"]
    problems: list[str] = []
    _unknown(problems, source, payload, EVIDENCE_KEYS)
    if payload.get("schema") != EVIDENCE_SCHEMA:
        problems.append(
            f"{source}.schema: expected {EVIDENCE_SCHEMA!r}, got {payload.get('schema')!r}")
    records = payload.get("records")
    if not isinstance(records, list):
        problems.append(f"{source}.records: expected a list")
        return problems
    if not records:
        problems.append(f"{source}.records: expected at least one record")
    ids: set[str] = set()
    for index, record in enumerate(records):
        where = f"{source}.records[{index}]"
        if not isinstance(record, dict):
            problems.append(f"{where}: expected an object")
            continue
        _unknown(problems, where, record, RECORD_KEYS)
        record_id = record.get("id")
        if not _text(record_id):
            problems.append(f"{where}.id: expected a non-blank string")
        elif record_id in ids:
            problems.append(f"{where}.id: duplicate {record_id!r}")
        else:
            ids.add(record_id)
        record_source = _closed_object(
            problems, f"{where}.source", record.get("source"), SOURCE_KEYS)
        if record_source is not None:
            for field in SOURCE_KEYS:
                if not _text(record_source.get(field)):
                    problems.append(f"{where}.source.{field}: expected a non-blank string")
        if _time(record.get("observed_at")) is None:
            problems.append(
                f"{where}.observed_at: expected an ISO 8601 timestamp with a timezone offset")
        for field in ("observations", "comparisons", "environments", "components"):
            if not _string_list(record.get(field)):
                problems.append(
                    f"{where}.{field}: expected a non-empty list of unique non-blank strings")
    return problems


def read(path: pathlib.Path | str, label: str) -> object:
    return json.loads(
        pathlib.Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_no_duplicate_keys(label),
    )


def assess(event: dict, event_path: pathlib.Path | str) -> dict:
    """Resolve every citation and report explicit support without verifying source claims."""
    problems = validate_event(event)
    if problems:
        raise ValueError("not a production anomaly event:\n  " + "\n  ".join(problems))
    base = pathlib.Path(event_path).resolve().parent
    readable = 0
    supporting = 0
    unmet: list[str] = []
    unobservable: list[str] = []
    cache: dict[pathlib.Path, object] = {}

    for citation in event["evidence"]:
        source = (base / citation["path"]).resolve()
        label = f"{citation['path']}#{citation['record']}"
        try:
            if source not in cache:
                envelope = read(source, f"anomaly evidence {citation['path']}")
                evidence_problems = validate_evidence(envelope, citation["path"])
                if evidence_problems:
                    raise ValueError("; ".join(evidence_problems))
                cache[source] = envelope
            envelope = cache[source]
            matches = [record for record in envelope["records"]
                       if record["id"] == citation["record"]]
            if len(matches) != 1:
                raise LookupError(f"record {citation['record']!r} does not resolve exactly once")
            record = matches[0]
            readable += 1
        except Exception as exc:  # noqa: BLE001 -- every cannot-read stays observable in output
            unobservable.append(f"{label}: could not be read: {type(exc).__name__}: {exc}")
            continue

        mismatches: list[str] = []
        if record["source"] != event["source"]:
            mismatches.append("source does not match the event source")
        observed_at = dt.datetime.fromisoformat(record["observed_at"])
        opens = dt.datetime.fromisoformat(event["window"]["opens"])
        closes = dt.datetime.fromisoformat(event["window"]["closes"])
        if not opens <= observed_at <= closes:
            mismatches.append("observed_at is outside the event window")
        if event["signal"]["observation"] not in record["observations"]:
            mismatches.append("signal.observation is not explicitly supported")
        if event["signal"]["comparison"] not in record["comparisons"]:
            mismatches.append("signal.comparison is not explicitly supported")
        if event["scope"]["environment"] not in record["environments"]:
            mismatches.append("scope.environment is not explicitly supported")
        if not set(event["scope"]["components"]) <= set(record["components"]):
            mismatches.append("scope.components are wider than the record")
        if mismatches:
            unmet.extend(f"{label}: {problem}" for problem in mismatches)
        else:
            supporting += 1

    status = UNMET if unmet else UNOBSERVABLE if unobservable else READY
    return {
        "schema": SCHEMA,
        "status": status,
        "event": {
            "id": event["id"],
            "source": {"claimed": event["source"], "verified": None},
        },
        "evidence": {
            "cited": len(event["evidence"]),
            "readable": readable,
            "supporting": supporting,
        },
        "unmet": unmet,
        "unobservable": unobservable,
        "claims": {
            "kind": {"claimed": event["signal"]["kind"], "verified": None},
            "severity": {"claimed": event["severity"], "verified": None},
            "confidence": {"claimed": event["confidence"], "verified": None},
        },
        "guarantee": GUARANTEE if status == READY else None,
        "does_not_guarantee": DOES_NOT_GUARANTEE,
    }


def cmd_anomaly_trigger(args) -> "NoReturn":  # noqa: F821
    """Validate and assess an external anomaly event; never detect one."""
    import sys

    path = pathlib.Path(args.event)
    try:
        event = read(path, "production anomaly event")
    except ValueError as exc:
        if str(exc).startswith("production anomaly event names ") and " twice;" in str(exc):
            print(f"[REJECTED] {exc}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({
            "schema": SCHEMA,
            "status": UNOBSERVABLE,
            "error": f"event could not be read: {type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "schema": SCHEMA,
            "status": UNOBSERVABLE,
            "error": f"event could not be read: {type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        sys.exit(2)
    problems = validate_event(event)
    if problems:
        print("\n".join(f"[REJECTED] {problem}" for problem in problems), file=sys.stderr)
        sys.exit(1)
    report = assess(event, path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"production anomaly event: {report['status']}")
        for problem in report["unmet"]:
            print(f"  unmet: {problem}")
        for problem in report["unobservable"]:
            print(f"  unobservable: {problem}")
        source = report["event"]["source"]
        print(f"source: claimed {source['claimed']}; verified: not measured")
        for name, claim in report["claims"].items():
            print(f"{name}: claimed {claim['claimed']}; verified: not measured")
        print(f"does not guarantee: {report['does_not_guarantee']}")
    sys.exit(0 if report["status"] == READY else 1 if report["status"] == UNMET else 2)
