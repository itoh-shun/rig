"""Fail-closed comparison of baseline and current evaluation results."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from typing import Any

from .attestation import verify_result_attestation
from .cases import (
    ISOLATION_LEVELS,
    EvalCaseError,
    canonical_json,
    evaluation_spec_hash,
    isolation_floor_violations,
    validate_case,
)
from .safety import unsafe_path_reason, unsafe_text_reason

# 3 adds `prompt_surface_digests` and the two isolation levels. Bumped rather than
# accepted as optional fields: a result written before them carries no content
# binding and no account of what confined the run, and a refusal by version names
# that, where the exact-field-set check would only say "schema fields are invalid".
# Defined next to the validator and re-exported by `runner`, which writes it.
RESULT_SCHEMA_VERSION = 3
MAX_RESULT_AGE = dt.timedelta(days=30)
FUTURE_TOLERANCE = dt.timedelta(minutes=5)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_result(
    result: Any, *, now: dt.datetime | None = None, verify_attestation: bool = True,
) -> dict:
    if not isinstance(result, dict):
        raise EvalCaseError("evaluation result must be an object")
    required = {
        "eval_result_schema_version", "case_id", "case_hash", "source_commit",
        "source_base_commit", "provider", "model", "executor_version", "phase",
        "provider_isolation", "judge_isolation",
        "judge_provider", "judge_model", "judge_executor_version",
        "started_at", "elapsed_s", "repeat", "target", "clean", "judge", "summary",
        "execution_commit", "execution_base_commit", "execution_status",
        "execution_diff_sha256",
        "prompt_binding_sha256",
        "pack_tree_sha256",
        "prompt_surface_digests",
        "result_sha256", "attestation",
    }
    # Version before field set: a result from an older schema is missing fields this
    # one requires, so the field check would report it as malformed and hide the only
    # thing that is actually wrong with it — that it predates the current schema.
    version = result.get("eval_result_schema_version")
    if isinstance(version, bool) or version != RESULT_SCHEMA_VERSION:
        raise EvalCaseError("unsupported eval_result_schema_version")
    unknown = set(result) - required
    missing = required - set(result)
    if unknown or missing:
        raise EvalCaseError("evaluation result schema fields are invalid")
    if verify_attestation and not verify_result_attestation(result):
        raise EvalCaseError("evaluation result attestation is invalid")
    for field in (
        "case_id", "provider", "model", "executor_version", "judge_provider",
        "judge_model", "judge_executor_version",
    ):
        if (not isinstance(result[field], str) or not result[field]
                or unsafe_text_reason(result[field])):
            raise EvalCaseError(f"evaluation result {field} is invalid")
    if result["provider"] not in {"mock", "claude", "codex", "command"}:
        raise EvalCaseError("evaluation result provider is invalid")
    for field in ("provider_isolation", "judge_isolation"):
        if not isinstance(result[field], str) or result[field] not in ISOLATION_LEVELS:
            raise EvalCaseError(f"evaluation result {field} is invalid")
    if (not isinstance(result["case_hash"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["case_hash"])):
        raise EvalCaseError("evaluation result case_hash is invalid")
    for field in ("source_commit", "source_base_commit"):
        if (not isinstance(result[field], str)
                or not re.fullmatch(r"[0-9a-f]{7,64}", result[field])):
            raise EvalCaseError(f"evaluation result {field} is invalid")
    if result["execution_status"] not in {"available", "unavailable"}:
        raise EvalCaseError("evaluation result execution_status is invalid")
    for field in ("execution_commit", "execution_base_commit"):
        value = result[field]
        if value is not None and (not isinstance(value, str)
                                  or not re.fullmatch(r"[0-9a-f]{40}", value)):
            raise EvalCaseError(f"evaluation result {field} is invalid")
    if result["execution_status"] == "available" and (
        result["execution_commit"] is None or result["execution_base_commit"] is None
    ):
        raise EvalCaseError("available execution identity is incomplete")
    if (not isinstance(result["execution_diff_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["execution_diff_sha256"])):
        raise EvalCaseError("evaluation result execution_diff_sha256 is invalid")
    if (not isinstance(result["prompt_binding_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["prompt_binding_sha256"])):
        raise EvalCaseError("evaluation result prompt_binding_sha256 is invalid")
    if (result["pack_tree_sha256"] is not None
            and (not isinstance(result["pack_tree_sha256"], str)
                 or not re.fullmatch(r"[0-9a-f]{64}", result["pack_tree_sha256"]))):
        raise EvalCaseError("evaluation result pack_tree_sha256 is invalid")
    digests = result["prompt_surface_digests"]
    if digests is not None:
        # Object ids, so both git hash algorithms are legal widths. `None` is the
        # shape a result measured outside a repository takes; the gate refuses it
        # on its own, because there the map is the binding rather than a detail.
        # The keys are paths, so they are held to the path rule: escapes out of
        # the tree still refused, secret-value scanning dropped — it can only ever
        # be wrong about a filename `git ls-tree` handed us.
        if not isinstance(digests, dict) or any(
            not isinstance(path, str) or not path or unsafe_path_reason(path)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", digest)
            for path, digest in digests.items()
        ):
            raise EvalCaseError("evaluation result prompt_surface_digests is invalid")
    if not isinstance(result["phase"], str) or result["phase"] not in {"baseline", "current"}:
        raise EvalCaseError("evaluation result phase is invalid")
    if (isinstance(result["elapsed_s"], bool)
            or not isinstance(result["elapsed_s"], (int, float))
            or not math.isfinite(result["elapsed_s"]) or result["elapsed_s"] < 0):
        raise EvalCaseError("evaluation result elapsed_s is invalid")
    repeat = result["repeat"]
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 3:
        raise EvalCaseError("evaluation result has insufficient repeat count")
    for kind in ("target", "clean"):
        samples = result[kind]
        if not isinstance(samples, list) or len(samples) != repeat:
            raise EvalCaseError(f"evaluation result {kind} sample count mismatch")
        if any(not isinstance(row, dict) or row.get("outcome") not in {"pass", "fail"}
               for row in samples):
            raise EvalCaseError(f"evaluation result {kind} samples are invalid")
        sample_fields = {
            "index", "outcome", "returncode", "elapsed_s", "infra_status", "checks",
            "judge", "stdout", "stderr",
        }
        for index, row in enumerate(samples, 1):
            if set(row) != sample_fields or row["index"] != index:
                raise EvalCaseError(f"evaluation result {kind} sample schema is invalid")
            if isinstance(row["returncode"], bool) or not isinstance(row["returncode"], int):
                raise EvalCaseError("evaluation sample returncode is invalid")
            if row["infra_status"] not in {None, "timeout", "unavailable", "provider_error"}:
                raise EvalCaseError("evaluation sample infra_status is invalid")
            if not isinstance(row["checks"], list) or not isinstance(row["judge"], dict):
                raise EvalCaseError("evaluation sample evidence is invalid")
            for check in row["checks"]:
                if (not isinstance(check, dict)
                        or set(check) != {"spec", "status", "detail"}
                        or not isinstance(check["spec"], str)
                        or check["status"] not in {"pass", "fail", "unmeasured"}
                        or not isinstance(check["detail"], str)):
                    raise EvalCaseError("evaluation deterministic check is invalid")
            expected_outcome = (
                "pass" if row["returncode"] == 0
                and all(check["status"] == "pass" for check in row["checks"])
                and row["infra_status"] is None else "fail"
            )
            if row["outcome"] != expected_outcome:
                raise EvalCaseError("evaluation sample outcome is inconsistent with evidence")
            judge = row["judge"]
            if (set(judge) != {"status", "criteria"}
                    or judge["status"] not in {
                        "measured", "unmeasured", "not_required", "error"
                    }
                    or not isinstance(judge["criteria"], list)):
                raise EvalCaseError("evaluation sample judge evidence is invalid")
            for criterion in judge["criteria"]:
                if (not isinstance(criterion, dict)
                        or set(criterion) != {"id", "status", "score"}
                        or not isinstance(criterion["id"], str)
                        or criterion["status"] not in {"pass", "fail"}
                        or isinstance(criterion["score"], bool)
                        or not isinstance(criterion["score"], (int, float))
                        or not math.isfinite(criterion["score"])):
                    raise EvalCaseError("evaluation semantic criterion is invalid")
            for output_name in ("stdout", "stderr"):
                output = row[output_name]
                if not isinstance(output, dict) or set(output) != {
                    "text", "sha256", "truncated", "redacted"
                }:
                    raise EvalCaseError("evaluation output schema is invalid")
                if (not isinstance(output["text"], str)
                        or not isinstance(output["sha256"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", output["sha256"])
                        or not isinstance(output["truncated"], bool)
                        or not isinstance(output["redacted"], bool)):
                    raise EvalCaseError("evaluation output values are invalid")
                if len(output["text"].encode("utf-8")) > 4096:
                    raise EvalCaseError("evaluation output exceeds persisted byte cap")
                if output["redacted"] and output["text"] != "[REDACTED]":
                    raise EvalCaseError("redacted evaluation output has invalid placeholder")
                if not output["redacted"] and unsafe_text_reason(output["text"]):
                    raise EvalCaseError("evaluation output contains unsafe unredacted text")
                if not output["truncated"] and not output["redacted"]:
                    derived = hashlib.sha256(output["text"].encode("utf-8")).hexdigest()
                    if derived != output["sha256"]:
                        raise EvalCaseError("evaluation output hash is inconsistent")
    summary = result["summary"]
    summary_fields = {
        "target_success_rate", "target_failure_rate", "clean_success_rate",
        "clean_false_positive_rate",
    }
    if not isinstance(summary, dict) or set(summary) != summary_fields:
        raise EvalCaseError("evaluation result summary schema is invalid")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or not 0 <= value <= 1
           for value in summary.values()):
        raise EvalCaseError("evaluation result summary rates are invalid")
    target_rate = sum(row["outcome"] == "pass" for row in result["target"]) / repeat
    clean_rate = sum(row["outcome"] == "pass" for row in result["clean"]) / repeat
    expected = {
        "target_success_rate": target_rate,
        "target_failure_rate": 1 - target_rate,
        "clean_success_rate": clean_rate,
        "clean_false_positive_rate": 1 - clean_rate,
    }
    if any(abs(summary[key] - value) > 1e-12 for key, value in expected.items()):
        raise EvalCaseError("evaluation result summary is inconsistent with samples")
    judge = result["judge"]
    if (not isinstance(judge, dict) or set(judge) != {"required", "status"}
            or not isinstance(judge["required"], bool)
            or judge["status"] not in {"measured", "unmeasured", "not_required"}):
        raise EvalCaseError("evaluation result judge summary is invalid")
    integrity = dict(result)
    integrity.pop("attestation")
    claimed = integrity.pop("result_sha256")
    if not isinstance(claimed, str) or claimed != _sha(integrity):
        raise EvalCaseError("evaluation result integrity mismatch")
    try:
        started = dt.datetime.fromisoformat(str(result["started_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvalCaseError("evaluation result started_at is invalid") from exc
    if started.tzinfo is None:
        raise EvalCaseError("evaluation result started_at must include timezone")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise EvalCaseError("comparison time must include timezone")
    if started > current + FUTURE_TOLERANCE:
        raise EvalCaseError("evaluation result is from the future")
    if current - started > MAX_RESULT_AGE:
        raise EvalCaseError("evaluation result is stale")
    return result


def compare_results(
    baseline: dict, current: dict, *, case: dict, now: dt.datetime | None = None
) -> dict:
    validate_case(case)
    validate_result(baseline, now=now)
    validate_result(current, now=now)
    if baseline["execution_status"] != "available" or current["execution_status"] != "available":
        raise EvalCaseError("git execution identity is unavailable")
    if any(row["infra_status"] is not None
           for result in (baseline, current)
           for row in [*result["target"], *result["clean"]]):
        raise EvalCaseError("infrastructure failures are not valid evaluation evidence")
    if baseline["phase"] != "baseline" or current["phase"] != "current":
        raise EvalCaseError("comparison requires baseline and current phases")
    for field in (
        "case_id", "case_hash", "source_commit", "source_base_commit", "provider", "model",
        "executor_version", "provider_isolation", "judge_isolation",
        "judge_provider", "judge_model", "judge_executor_version",
    ):
        if baseline[field] != current[field]:
            raise EvalCaseError(f"evaluation result identity mismatch: {field}")
    expected_hash = evaluation_spec_hash(case)
    if baseline["case_id"] != case["id"] or baseline["case_hash"] != expected_hash:
        raise EvalCaseError("evaluation result case identity/hash mismatch")
    expected_commit = case["provenance"]["source_commit"]
    if (baseline["source_commit"] != expected_commit
            or baseline["source_base_commit"] != expected_commit):
        raise EvalCaseError("evaluation result source commit/base mismatch")
    policy = case["provider_policy"]
    if policy["mode"] == "allowlist" and baseline["provider"] not in policy["allowed"]:
        raise EvalCaseError("evaluation result violates provider policy")
    if policy.get("models") and baseline["model"] not in policy["models"]:
        raise EvalCaseError("evaluation result violates model policy")
    if (policy.get("judge_providers")
            and baseline["judge_provider"] not in policy["judge_providers"]):
        raise EvalCaseError("evaluation result violates judge provider policy")
    if policy.get("judge_models") and baseline["judge_model"] not in policy["judge_models"]:
        raise EvalCaseError("evaluation result violates judge model policy")
    if isolation_floor_violations(policy, baseline):
        raise EvalCaseError("evaluation result violates isolation policy")
    if baseline["repeat"] != case["repeat"] or current["repeat"] != case["repeat"]:
        raise EvalCaseError("evaluation result repeat does not match case repeat")
    if baseline["judge_provider"] == "mock" or current["judge_provider"] == "mock":
        raise EvalCaseError("mock judge is not valid quality evidence")
    if case["semantic_rubric"]:
        expected_ids = [item["id"] for item in case["semantic_rubric"]]
        for result in (baseline, current):
            if (result["judge"] != {"required": True, "status": "measured"}):
                raise EvalCaseError("required semantic judge is unmeasured")
            for sample in [*result["target"], *result["clean"]]:
                judge = sample["judge"]
                criteria = judge["criteria"]
                ids = [item["id"] for item in criteria]
                if (judge["status"] != "measured" or ids != expected_ids
                        or len(ids) != len(set(ids))):
                    raise EvalCaseError("semantic judge rubric criteria are incomplete")
                if any(item["status"] != "pass" for item in criteria):
                    raise EvalCaseError("semantic judge rubric criterion failed")
    red_max = case["red_thresholds"].get("max_success_rate")
    green_min = case["green_thresholds"].get("min_success_rate")
    if red_max is None or green_min is None:
        raise EvalCaseError("case thresholds do not define red/green success rates")
    baseline_red = baseline["summary"]["target_success_rate"] <= red_max
    target_green = current["summary"]["target_success_rate"] >= green_min
    clean_green = current["summary"]["clean_success_rate"] >= green_min
    status = "pass" if baseline_red and target_green and clean_green else "regression"
    return {
        "eval_compare_schema_version": 1,
        "case_id": case["id"], "provider": baseline["provider"],
        "model": baseline["model"], "baseline_red": baseline_red,
        "current_target_green": target_green, "current_clean_green": clean_green,
        "status": status,
    }
