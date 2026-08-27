"""Whether a submitted knowledge candidate is supported by the records it cites (#440).

The caller writes the candidate.  This module does not discover, generate, improve, approve,
or version one.  It validates a closed candidate schema, resolves the cited records, and asks
only whether every record explicitly supports the rule, benefit, context and scope claimed.

``unsupported`` means the record was read and did not support the claim. ``unobservable``
means the record could not be read or understood.  They are separate because failing to look
is not evidence against a claim, and it is never evidence for one either.

The confidence value remains the candidate author's claim.  It is printed as
``claimed`` beside ``verified: null``; counting readable records does not turn it into a
confidence rig measured.

The guarantee is deliberately narrow: cited evidence exists and explicitly supports the
candidate at its claimed scope.  It does not guarantee that the candidate is correct, causal,
complete, generally applicable, beneficial in the future, approved, or organizational
knowledge.  Derived views copy the assessment and do not decide it again.
"""

from __future__ import annotations

import json
import pathlib

from .synthesis import _no_duplicate_keys

CANDIDATE_SCHEMA = "rig.knowledge-candidate/v1"
EVIDENCE_SCHEMA = "rig.knowledge-candidate-evidence/v1"
SCHEMA = "rig.knowledge-candidate-assessment/v1"

CANDIDATE_KEYS = frozenset({
    "schema", "triggering_evidence", "applicable_context", "proposed_rule",
    "expected_benefit", "confidence", "evidence_count", "known_exceptions", "scope",
})
CITATION_KEYS = frozenset({"path", "record"})
EVIDENCE_KEYS = frozenset({"schema", "records"})
RECORD_KEYS = frozenset({
    "id", "observation", "applicable_context", "proposed_rules", "observed_benefits",
    "known_exceptions", "scope",
})

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNOBSERVABLE = "unobservable"


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _string_list(value: object, *, nonempty: bool) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(_text(item) for item in value) and len(set(value)) == len(value))


def _unknown(problems: list[str], where: str, value: dict, allowed: frozenset[str]) -> None:
    keys = sorted(str(key) for key in value if key not in allowed)
    if keys:
        problems.append(f"{where}: unknown key(s) {', '.join(keys)}")


def validate_candidate(payload: object) -> list[str]:
    """Enumerate every reason the value is not a candidate; absence grants no default."""
    if not isinstance(payload, dict):
        return [f"candidate: expected an object, got {type(payload).__name__}"]
    problems: list[str] = []
    _unknown(problems, "candidate", payload, CANDIDATE_KEYS)
    if payload.get("schema") != CANDIDATE_SCHEMA:
        problems.append(
            f"schema: expected {CANDIDATE_SCHEMA!r}, got {payload.get('schema')!r}")

    citations = payload.get("triggering_evidence")
    if not isinstance(citations, list):
        problems.append("triggering_evidence: expected a list of record citations")
    elif not citations:
        problems.append("triggering_evidence: expected at least one citation")
    else:
        seen: set[tuple[str, str]] = set()
        for index, citation in enumerate(citations):
            where = f"triggering_evidence[{index}]"
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

    for field in ("applicable_context", "scope"):
        value = payload.get(field)
        if not isinstance(value, list):
            problems.append(f"{field}: expected a list of non-blank strings")
        elif not _string_list(value, nonempty=True):
            problems.append(f"{field}: expected a non-empty list of unique non-blank strings")
    exceptions = payload.get("known_exceptions")
    if not isinstance(exceptions, list):
        problems.append("known_exceptions: expected a list of non-blank strings (empty is explicit)")
    elif not _string_list(exceptions, nonempty=False):
        problems.append("known_exceptions: expected unique non-blank strings")
    for field in ("proposed_rule", "expected_benefit"):
        if not _text(payload.get(field)):
            problems.append(f"{field}: expected a non-blank string")
    confidence = payload.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1):
        problems.append("confidence: expected a number between 0 and 1; this remains claimed")
    count = payload.get("evidence_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        problems.append("evidence_count: expected a positive integer")
    return problems


def validate_evidence(payload: object, source: str) -> list[str]:
    """Validate the evidence envelope and every record before any record is trusted."""
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
        if not _text(record.get("id")):
            problems.append(f"{where}.id: expected a non-blank string")
        elif record["id"] in ids:
            problems.append(f"{where}.id: duplicate {record['id']!r}")
        else:
            ids.add(record["id"])
        if not _text(record.get("observation")):
            problems.append(f"{where}.observation: expected a non-blank string")
        for field in ("applicable_context", "proposed_rules", "observed_benefits", "scope"):
            if not _string_list(record.get(field), nonempty=True):
                problems.append(f"{where}.{field}: expected a non-empty list of unique strings")
        if not _string_list(record.get("known_exceptions"), nonempty=False):
            problems.append(f"{where}.known_exceptions: expected a list of unique strings")
    return problems


def read(path: pathlib.Path | str, label: str) -> object:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_no_duplicate_keys(label))


def assess(candidate: dict, candidate_path: pathlib.Path | str) -> dict:
    """Resolve citations and assess explicit support; never manufacture the candidate."""
    problems = validate_candidate(candidate)
    if problems:
        raise ValueError("not a knowledge candidate:\n  " + "\n  ".join(problems))
    base = pathlib.Path(candidate_path).resolve().parent
    readable = 0
    supporting = 0
    unsupported: list[str] = []
    unobservable: list[str] = []
    exceptions: set[str] = set()
    cache: dict[pathlib.Path, object] = {}

    for citation in candidate["triggering_evidence"]:
        source = (base / citation["path"]).resolve()
        label = f"{citation['path']}#{citation['record']}"
        try:
            if source not in cache:
                envelope = read(source, f"knowledge evidence {citation['path']}")
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
        except Exception as exc:  # noqa: BLE001 -- every cannot-read stays one outcome
            unobservable.append(f"{label}: could not be read: {type(exc).__name__}: {exc}")
            continue

        mismatches: list[str] = []
        if candidate["proposed_rule"] not in record["proposed_rules"]:
            mismatches.append("proposed_rule is not explicitly supported")
        if candidate["expected_benefit"] not in record["observed_benefits"]:
            mismatches.append("expected_benefit exceeds what the observation records")
        if not set(candidate["applicable_context"]) <= set(record["applicable_context"]):
            mismatches.append("applicable_context is wider than the record")
        if not set(candidate["scope"]) <= set(record["scope"]):
            mismatches.append("scope is wider than the record")
        exceptions.update(record["known_exceptions"])
        if mismatches:
            unsupported.extend(f"{label}: {problem}" for problem in mismatches)
        else:
            supporting += 1

    if candidate["evidence_count"] != len(candidate["triggering_evidence"]):
        unsupported.append(
            "evidence_count does not equal the number of distinct citations: "
            f"claimed {candidate['evidence_count']}, cited {len(candidate['triggering_evidence'])}")
    if set(candidate["known_exceptions"]) != exceptions and not unobservable:
        unsupported.append(
            "known_exceptions does not equal the exceptions recorded by the cited evidence")

    # A measured lack of support outranks a citation we could not read, as `unmet` outranks
    # `unobservable` in assurance_target.  Both lists remain visible; the headline must not
    # let one unreadable record hide another record's explicit contradiction.
    status = (UNSUPPORTED if unsupported else UNOBSERVABLE if unobservable else SUPPORTED)
    return {
        "schema": SCHEMA,
        "status": status,
        "evidence": {"cited": len(candidate["triggering_evidence"]), "readable": readable,
                     "supporting": supporting},
        "unsupported": unsupported,
        "unobservable": unobservable,
        "confidence": {"claimed": candidate["confidence"], "verified": None,
                       "note": "candidate-supplied; not verified by rig"},
        "guarantee": (
            "the cited evidence exists and explicitly supports the candidate at its claimed scope"
            if status == SUPPORTED else None),
        "does_not_guarantee": (
            "the candidate is correct, causal, complete, generally applicable, beneficial in "
            "the future, approved, or organizational knowledge"),
    }


def view(assessment: dict) -> dict:
    """A projection only: copy the existing assessment and make no new judgement."""
    return {"status": assessment["status"],
            "claimed_confidence": assessment["confidence"]["claimed"],
            "evidence": dict(assessment["evidence"])}


def cmd_knowledge_candidate(args) -> "NoReturn":  # noqa: F821
    """Validate and assess a candidate file, with status carried in the exit code."""
    import sys

    path = pathlib.Path(args.candidate)
    try:
        candidate = read(path, "knowledge candidate")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"schema": SCHEMA, "status": UNOBSERVABLE,
                          "error": f"candidate could not be read: {type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        sys.exit(2)
    problems = validate_candidate(candidate)
    if problems:
        print("\n".join(f"[REJECTED] {problem}" for problem in problems), file=sys.stderr)
        sys.exit(1)
    report = assess(candidate, path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"knowledge candidate: {report['status']} — "
              f"{report['evidence']['supporting']} of {report['evidence']['cited']} cited "
              "records support it")
        for problem in report["unsupported"] + report["unobservable"]:
            print(f"  {problem}")
        print(f"confidence: claimed {report['confidence']['claimed']}; verified: not measured")
        print(f"does not guarantee: {report['does_not_guarantee']}")
    sys.exit(0 if report["status"] == SUPPORTED else 2 if report["status"] == UNOBSERVABLE else 1)
