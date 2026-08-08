"""Strict, standard-library-only schema for promoted evaluation cases."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from typing import Any

from .safety import unsafe_key_reason, unsafe_text_reason


class EvalCaseError(ValueError):
    """An evaluation case is malformed or unsafe."""


_TOP_FIELDS = {
    "case_schema_version", "id", "version", "title", "status", "incident",
    "provenance", "surfaces", "suite", "tags", "provider_policy", "repeat",
    "red_thresholds", "green_thresholds", "deterministic_checks", "semantic_rubric",
    "target_inputs", "clean_controls", "missing_requirements", "failure_summary",
    "created_at", "updated_at", "prompt_surfaces",
    "prompt_entrypoint", "prompt_composition", "target_expectations",
    "clean_expectations",
}
_REQUIRED = _TOP_FIELDS - {
    "failure_summary", "prompt_surfaces", "prompt_entrypoint", "prompt_composition",
    "target_expectations", "clean_expectations",
}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_RUBRIC_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
# Must stay in step with `eval.affected._surface`: an id that analysis can produce
# and a case cannot declare is debt nobody is allowed to pay off, which turns the
# ratchet's warning into a permanent one. `engine` (the engine's own prose) is
# registry v2; the name segment accepts uppercase because ids are the file's stem
# verbatim and `SKILL.md` is spelled that way — lowercasing it here would mint a
# name that matches no file.
_PROMPT_SURFACE_ID = re.compile(
    r"^(?:recipe|instruction|persona|policy|wiki|pattern|contract|agent|command|engine):"
    r"[A-Za-z0-9][A-Za-z0-9/_-]{0,127}$"
)
_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_SPEC_EXCLUDED_FIELDS = frozenset({
    "status", "title", "failure_summary", "missing_requirements", "created_at", "updated_at",
})


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ) + "\n"


def evaluation_spec_payload(case: dict) -> dict:
    """Return behavior/provenance fields whose hash survives draft promotion lifecycle edits."""
    return {key: value for key, value in case.items() if key not in _SPEC_EXCLUDED_FIELDS}


def evaluation_spec_hash(case: dict) -> str:
    return hashlib.sha256(
        canonical_json(evaluation_spec_payload(case)).encode("utf-8")
    ).hexdigest()


def _exact(obj: Any, fields: set[str], where: str, required: set[str] | None = None) -> dict:
    if not isinstance(obj, dict):
        raise EvalCaseError(f"{where} must be an object")
    unknown = set(obj) - fields
    missing = (required if required is not None else fields) - set(obj)
    if unknown:
        raise EvalCaseError(f"{where} has unknown field: {next(iter(unknown))}")
    if missing:
        raise EvalCaseError(f"{where} is missing field: {sorted(missing)[0]}")
    return obj


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise EvalCaseError(f"{where} must be a non-empty string")
    reason = unsafe_text_reason(value)
    if reason:
        raise EvalCaseError(f"{where} contains unsafe text ({reason})")
    return value


def _timestamp(value: Any, where: str) -> None:
    text = _text(value, where)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvalCaseError(f"{where} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise EvalCaseError(f"{where} must include a timezone")


def _rate_map(value: Any, where: str) -> None:
    obj = _exact(value, {"max_success_rate", "min_success_rate"}, where, set())
    if not obj:
        raise EvalCaseError(f"{where} must not be empty")
    for key, number in obj.items():
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number)):
            raise EvalCaseError(f"{where}.{key} must be finite")
        if not 0 <= number <= 1:
            raise EvalCaseError(f"{where}.{key} must be between 0 and 1")


def _reject_unsafe(value: Any, where: str = "case") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if unsafe_key_reason(key):
                raise EvalCaseError(f"{where}.{key} looks like a secret field")
            _reject_unsafe(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe(item, f"{where}[{index}]")
    elif isinstance(value, str):
        _text(value, where, allow_empty=True)
        reason = unsafe_text_reason(value)
        if reason:
            raise EvalCaseError(f"{where} contains unsafe text ({reason})")


def validate_case(case: Any) -> dict:
    obj = _exact(case, _TOP_FIELDS, "case", _REQUIRED)
    _reject_unsafe(obj)
    if (isinstance(obj["case_schema_version"], bool)
            or not isinstance(obj["case_schema_version"], int)
            or obj["case_schema_version"] != 1):
        raise EvalCaseError("unsupported case_schema_version")
    if (isinstance(obj["version"], bool) or not isinstance(obj["version"], int)
            or obj["version"] < 1):
        raise EvalCaseError("version must be a positive integer")
    if not isinstance(obj["id"], str) or not _ID.fullmatch(obj["id"]):
        raise EvalCaseError("id must be a safe lowercase slug")
    _text(obj["title"], "title")
    if not isinstance(obj["status"], str) or obj["status"] not in {"draft", "approved"}:
        raise EvalCaseError("status must be draft or approved")
    if not isinstance(obj["incident"], bool):
        raise EvalCaseError("incident must be boolean")
    provenance = _exact(
        obj["provenance"],
        {"source_task_id", "source_commit", "source_hashes", "captured_at"},
        "provenance",
    )
    source_task_id = _text(provenance["source_task_id"], "provenance.source_task_id")
    if not _ID.fullmatch(source_task_id):
        raise EvalCaseError("provenance.source_task_id must be a safe lowercase slug")
    if (not isinstance(provenance["source_commit"], str)
            or not _SHA.fullmatch(provenance["source_commit"])):
        raise EvalCaseError("provenance.source_commit must be a git hash")
    hashes = provenance["source_hashes"]
    if not isinstance(hashes, dict) or not hashes:
        raise EvalCaseError("provenance.source_hashes must not be empty")
    for name, digest in hashes.items():
        if name not in {
            "task.json", "acceptance.json", "review.json", "final.md", "diff.md",
            "runs.jsonl", "outcome.json",
        }:
            raise EvalCaseError("provenance.source_hashes contains an unsupported artifact")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvalCaseError(f"provenance.source_hashes.{name} must be sha256")
    _timestamp(provenance["captured_at"], "provenance.captured_at")
    if (not isinstance(obj["surfaces"], list) or not obj["surfaces"]
            or any(not isinstance(x, str)
                   or (x not in {"cli", "codex", "claude-code", "cursor", "api"}
                       and not _PROMPT_SURFACE_ID.fullmatch(x))
                   for x in obj["surfaces"])):
        raise EvalCaseError("surfaces contains an invalid value")
    if len(obj["surfaces"]) != len(set(obj["surfaces"])):
        raise EvalCaseError("surfaces contains a duplicate")
    prompt_surfaces = obj.get("prompt_surfaces", [])
    if (not isinstance(prompt_surfaces, list)
            or any(not isinstance(value, str) or not _PROMPT_SURFACE_ID.fullmatch(value)
                   for value in prompt_surfaces)):
        raise EvalCaseError("prompt_surfaces contains an invalid registry id")
    if len(prompt_surfaces) != len(set(prompt_surfaces)):
        raise EvalCaseError("prompt_surfaces contains a duplicate")
    if "prompt_entrypoint" in obj:
        if not isinstance(obj["prompt_entrypoint"], str) or not _ID.fullmatch(
            obj["prompt_entrypoint"]
        ):
            raise EvalCaseError("prompt_entrypoint must be a safe entrypoint id")
    if "prompt_composition" in obj:
        composition = obj["prompt_composition"]
        if (not isinstance(composition, list) or not composition
                or any(not isinstance(value, str) or not _PROMPT_SURFACE_ID.fullmatch(value)
                       for value in composition)
                or len(composition) != len(set(composition))):
            raise EvalCaseError("prompt_composition must contain unique prompt registry ids")
    for field in ("target_expectations", "clean_expectations"):
        if field in obj and (not isinstance(obj[field], list) or not obj[field]
                             or any(not isinstance(value, str) or not value.strip()
                                    for value in obj[field])):
            raise EvalCaseError(f"{field} must contain deterministic checks")
    if ("target_expectations" in obj and "clean_expectations" in obj
            and obj["target_expectations"] == obj["clean_expectations"]):
        raise EvalCaseError("target and clean expectations must be distinct")
    _text(obj["suite"], "suite")
    if (not isinstance(obj["tags"], list)
            or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in obj["tags"])):
        raise EvalCaseError("tags must contain safe slugs")
    if len(obj["tags"]) != len(set(obj["tags"])):
        raise EvalCaseError("tags contains a duplicate")
    policy = _exact(
        obj["provider_policy"],
        {"mode", "allowed", "models", "judge_providers", "judge_models"},
        "provider_policy", {"mode", "allowed"},
    )
    if (not isinstance(policy["mode"], str)
            or policy["mode"] not in {"allowlist", "any"}):
        raise EvalCaseError("provider_policy.mode is invalid")
    if (not isinstance(policy["allowed"], list)
            or any(not isinstance(x, str) or not _ID.fullmatch(x)
                   for x in policy["allowed"])):
        raise EvalCaseError("provider_policy.allowed is invalid")
    if policy["mode"] == "allowlist" and not policy["allowed"]:
        raise EvalCaseError("allowlist provider policy must name a provider")
    for field in ("models", "judge_providers", "judge_models"):
        values = policy.get(field, [])
        if (not isinstance(values, list) or len(values) != len(set(values))
                or any(not isinstance(value, str)
                       or not (_ID if field == "judge_providers" else _MODEL_ID).fullmatch(value)
                       for value in values)):
            raise EvalCaseError(f"provider_policy.{field} is invalid")
    if (isinstance(obj["repeat"], bool) or not isinstance(obj["repeat"], int)
            or not 1 <= obj["repeat"] <= 100):
        raise EvalCaseError("repeat must be an integer from 1 to 100")
    _rate_map(obj["red_thresholds"], "red_thresholds")
    _rate_map(obj["green_thresholds"], "green_thresholds")
    if (not isinstance(obj["deterministic_checks"], list)
            or any(not isinstance(x, str) or not x.strip()
                   for x in obj["deterministic_checks"])):
        raise EvalCaseError("deterministic_checks must contain commands")
    if not isinstance(obj["semantic_rubric"], list):
        raise EvalCaseError("semantic_rubric must be a list")
    rubric_ids: set[str] = set()
    for index, item in enumerate(obj["semantic_rubric"]):
        rubric = _exact(item, {"id", "description", "weight"}, f"semantic_rubric[{index}]")
        if not isinstance(rubric["id"], str) or not _RUBRIC_ID.fullmatch(rubric["id"]):
            raise EvalCaseError("semantic rubric id is invalid")
        if rubric["id"] in rubric_ids:
            raise EvalCaseError("semantic_rubric contains a duplicate id")
        rubric_ids.add(rubric["id"])
        _text(rubric["description"], "semantic rubric description")
        if (isinstance(rubric["weight"], bool)
                or not isinstance(rubric["weight"], (int, float))
                or not math.isfinite(rubric["weight"]) or rubric["weight"] <= 0):
            raise EvalCaseError("semantic rubric weight must be finite and positive")
    for field in ("target_inputs", "clean_controls"):
        values = obj[field]
        if (not isinstance(values, dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in values.items())):
            raise EvalCaseError(f"{field} must be a string map")
    if (not isinstance(obj["missing_requirements"], list)
            or any(not isinstance(x, str) or not x.strip()
                   for x in obj["missing_requirements"])):
        raise EvalCaseError("missing_requirements must contain strings")
    if "failure_summary" in obj:
        _text(obj["failure_summary"], "failure_summary")
    _timestamp(obj["created_at"], "created_at")
    _timestamp(obj["updated_at"], "updated_at")
    if obj["status"] == "approved":
        if obj["missing_requirements"]:
            raise EvalCaseError("approved case must have no missing requirements")
        for field in (
            "target_inputs", "clean_controls", "deterministic_checks", "semantic_rubric"
        ):
            if not obj[field]:
                raise EvalCaseError(f"approved case requires non-empty {field}")
    canonical_json(obj)
    return obj
