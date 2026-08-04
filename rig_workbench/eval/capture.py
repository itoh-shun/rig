"""Capture bounded workbench evidence into an unapproved evaluation draft."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile

from .cases import EvalCaseError, canonical_json, validate_case
from .safety import unsafe_text_reason

_ARTIFACTS = ("task.json", "acceptance.json", "review.json", "final.md", "diff.md", "outcome.json")


def _load_object(path: pathlib.Path, *, required: bool = False) -> dict:
    try:
        if not path.exists():
            if required:
                raise EvalCaseError(f"missing source artifact: {path.name}")
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvalCaseError(f"invalid source artifact: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvalCaseError(f"source artifact must be an object: {path.name}")
    return value


def _safe_summary(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    text = " ".join(value.split())[:240]
    if unsafe_text_reason(text):
        return fallback
    return text


def _now_iso(now: str | None) -> str:
    if now is not None:
        return now
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def capture_case(
    repo: pathlib.Path | str,
    task_id: str,
    *,
    now: str | None = None,
    allow_nonincident: bool = False,
) -> tuple[pathlib.Path, dict]:
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving repository: {exc}") from exc
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", task_id):
        raise EvalCaseError("task id must be a safe lowercase slug")
    run = root / ".rig" / "runs" / task_id
    task = _load_object(run / "task.json", required=True)
    if task.get("task_id") != task_id:
        raise EvalCaseError("task.json task_id does not match requested task")
    outcome = _load_object(run / "outcome.json")
    acceptance = _load_object(run / "acceptance.json")
    review = _load_object(run / "review.json")

    telemetry: dict = {}
    runs_path = root / ".rig" / "runs.jsonl"
    try:
        if runs_path.is_file():
            for line in runs_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("task_id") == task_id:
                    telemetry = record
    except (OSError, UnicodeError) as exc:
        raise EvalCaseError("invalid source artifact: runs.jsonl") from exc

    commit = task.get("commit_sha") or task.get("base_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise EvalCaseError("task has no valid source commit")
    hashes: dict[str, str] = {}
    for name in _ARTIFACTS:
        path = run / name
        try:
            if path.is_file():
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise EvalCaseError(f"filesystem error reading {name}: {exc}") from exc
    try:
        if runs_path.is_file():
            hashes["runs.jsonl"] = hashlib.sha256(runs_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error reading runs.jsonl: {exc}") from exc

    incident = outcome.get("status") == "incident"
    failed_checks = [str(check.get("name")) for check in acceptance.get("checks", [])
                     if isinstance(check, dict) and check.get("status") == "failed"]
    rejected = [str(item.get("persona")) for item in review.get("verdicts", [])
                if isinstance(item, dict) and item.get("verdict") == "REJECT"]
    explicitly_successful = (
        outcome.get("status") == "ok"
        or task.get("status") in {"accepted", "gate_passed"}
    ) and not incident and not failed_checks and not rejected
    if explicitly_successful and not allow_nonincident:
        raise EvalCaseError(
            "successful task is not an incident; use --allow-nonincident to capture a draft"
        )
    raw_family = telemetry.get("failure_mode")
    if incident:
        failure_family = "production:incident"
    elif isinstance(raw_family, str) and raw_family.strip():
        failure_family = _safe_summary(raw_family, "unclassified")
    elif failed_checks:
        failure_family = "gate:failed"
    elif rejected:
        failure_family = "review:rejected"
    elif telemetry.get("final") in {"BLOCKED", "ESCALATE", "FAIL"}:
        failure_family = "orchestration:stuck"
    else:
        failure_family = "unclassified"
    fallback = (
        "Incident recorded without a safe failure summary"
        if incident else "No production incident recorded"
    )
    if incident:
        summary = _safe_summary(outcome.get("note"), fallback)
    elif failed_checks:
        summary = _safe_summary(
            "Failed acceptance checks: " + ", ".join(failed_checks[:8]), fallback
        )
    elif rejected:
        summary = _safe_summary(
            "Rejected by reviewers: " + ", ".join(rejected[:8]), fallback
        )
    else:
        summary = fallback

    captured_at = _now_iso(now)
    case = {
        "case_schema_version": 1,
        "id": task_id,
        "version": 1,
        "title": _safe_summary(task.get("input"), task_id),
        "status": "draft",
        "incident": incident,
        "provenance": {
            "source_task_id": task_id,
            "source_commit": commit,
            "source_hashes": hashes,
            "captured_at": captured_at,
        },
        "surfaces": ["cli"],
        "prompt_surfaces": [],
        "suite": "incident" if incident else "candidate",
        "tags": [str(task.get("task_type", "unknown")).lower().replace("_", "-")],
        "provider_policy": {"mode": "any", "allowed": []},
        "repeat": 3,
        "red_thresholds": {"max_success_rate": 1 / 3},
        "green_thresholds": {"min_success_rate": 1.0},
        "deterministic_checks": ["contains:task_intent"],
        "semantic_rubric": [],
        "target_inputs": {
            "task_intent": _safe_summary(
                task.get("input"), "Captured task intent unavailable"
            ),
            "failure_family": failure_family,
            "expected_fail_conditions": _safe_summary(
                summary, "Captured failure must reproduce"
            ),
        },
        "clean_controls": {
            "task_intent": "Control input without the recorded failure condition",
            "expected_pass_conditions": "No recorded failure family is triggered",
        },
        "missing_requirements": [
            "red reproduction evidence", "green fix evidence",
            "semantic rubric", "provider review",
            "prompt surface binding",
        ],
        "failure_summary": summary,
        "created_at": captured_at,
        "updated_at": captured_at,
    }
    validate_case(case)

    promoted = root / "evals" / "cases" / task_id / "case.json"
    draft_slot = root / ".rig" / "evals" / "drafts" / task_id
    destination = draft_slot / "case.json"
    temporary: pathlib.Path | None = None
    draft_created = False
    try:
        if promoted.exists() or draft_slot.exists():
            raise EvalCaseError(
                f"evaluation case '{task_id}' already exists; refusing to overwrite"
            )
        (root / ".rig" / "evals" / "results").mkdir(parents=True, exist_ok=True)
        draft_slot.mkdir(parents=True, exist_ok=False)
        draft_created = True
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".case.", suffix=".tmp", dir=draft_slot
        )
        temporary = pathlib.Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(case))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    except EvalCaseError:
        raise
    except FileExistsError as exc:
        raise EvalCaseError(
            f"evaluation case '{task_id}' already exists; refusing to overwrite"
        ) from exc
    except OSError as exc:
        raise EvalCaseError(f"filesystem error capturing evaluation case: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            destination_exists = destination.exists()
        except OSError:
            destination_exists = False
        if draft_created and not destination_exists:
            try:
                draft_slot.rmdir()
            except OSError:
                pass
    return destination, case
