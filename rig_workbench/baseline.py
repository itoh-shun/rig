"""Versioned outcome baselines derived from benchmark schema v2 evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys
from typing import Any


BASELINE_SCHEMA_VERSION = 1
DEFAULT_FRESHNESS_DAYS = 90
DEFAULT_MIN_SAMPLES = 3
FUTURE_TOLERANCE = dt.timedelta(minutes=5)
VALID_OUTCOMES = {"clean_pass", "silent_defect", "safe_stop", "stopped_wrong"}
ALL_OUTCOMES = VALID_OUTCOMES | {"infra_error", "invalid"}
SCORE_VERDICTS = {"pass", "fail", "invalid", "inconclusive"}
THRESHOLD_KEYS = (
    "min_samples_per_identity", "max_task_success_rate_drop",
    "max_silent_defect_rate_increase", "max_safe_stop_rate_increase",
    "max_invalid_sample_rate_increase",
    "max_elapsed_p95_ratio", "max_calls_mean_ratio",
    "max_tokens_total_ratio", "max_cost_total_ratio", "freshness_days",
)


class BaselineError(ValueError):
    """The input cannot be used as trustworthy baseline evidence."""


def _timestamp(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise BaselineError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise BaselineError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise BaselineError(f"{field} must include a timezone")
    return parsed


def _clock(now: dt.datetime | None) -> dt.datetime:
    value = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise BaselineError("now must be a timezone-aware datetime")
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BaselineError(f"{field} must be a non-empty string")
    return value


def _number(value: object, field: str, *, integer: bool = False) -> int | float:
    valid_type = isinstance(value, int) if integer else isinstance(value, (int, float))
    if isinstance(value, bool) or not valid_type:
        kind = "integer" if integer else "number"
        raise BaselineError(f"{field} must be a finite non-negative {kind}")
    if not math.isfinite(float(value)) or value < 0:
        raise BaselineError(f"{field} must be a finite non-negative number")
    return int(value) if integer else float(value)


def _rate(numerator: int | float, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[rank], 6)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha256(value: object) -> str:
    try:
        return hashlib.sha256(_canonical_bytes(value)).hexdigest()
    except (TypeError, ValueError) as error:
        raise BaselineError(f"source is not canonical JSON: {error}") from error


def _normalize_source(source: object, *, label: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate schema-v2 evidence and return it plus normalized arm evidence."""
    if not isinstance(source, dict) or source.get("schema_version") != 2:
        raise BaselineError(f"{label} schema_version 2 is required")
    provider = _identity(source.get("provider"), f"{label} provider")
    _timestamp(source.get("generated"), f"{label} generated")
    score = source.get("score")
    if not isinstance(score, dict) or score.get("verdict") not in SCORE_VERDICTS:
        raise BaselineError(f"{label} schema v2 score verdict is invalid")
    tasks = source.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise BaselineError(f"{label} tasks must be a non-empty list")
    default_bare = source.get("bare_model") or source.get("model")
    default_rig = source.get("rig_model") or source.get("model")
    evidence: list[dict[str, Any]] = []
    seen_pairs: set[str] = set()
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise BaselineError(f"{label} task[{task_index}] must be an object")
        task_id = _identity(task.get("task_id"), f"{label} task[{task_index}].task_id")
        runs = task.get("runs")
        if not isinstance(runs, list):
            raise BaselineError(f"{label} task {task_id} runs must be a list")
        for run_index, pair in enumerate(runs):
            prefix = f"{label} task {task_id} run[{run_index}]"
            if not isinstance(pair, dict):
                raise BaselineError(f"{prefix} must be an object")
            pair_id = _identity(pair.get("pair_id"), f"{prefix}.pair_id")
            if pair_id in seen_pairs:
                raise BaselineError(f"duplicate pair_id: {pair_id}")
            seen_pairs.add(pair_id)
            if pair.get("task_id") != task_id:
                raise BaselineError(f"{prefix}.task_id must match containing task")
            _number(pair.get("run"), f"{prefix}.run", integer=True)
            pair_provider = _identity(pair.get("provider"), f"{prefix}.provider")
            if pair_provider != provider:
                raise BaselineError(
                    f"{prefix}.provider {pair_provider!r} does not match top-level provider {provider!r}"
                )
            if "elapsed_s" in pair:
                _number(pair["elapsed_s"], f"{prefix}.elapsed_s")
            arms = pair.get("arms")
            if not isinstance(arms, dict) or set(arms) != {"bare", "rig"}:
                raise BaselineError(f"{prefix}.arms must contain exactly bare and rig")
            for mode, default_model in (("bare", default_bare), ("rig", default_rig)):
                model = pair.get(f"{mode}_model") or default_model
                model = _identity(model, f"{prefix}.{mode}_model")
                arm = arms[mode]
                if not isinstance(arm, dict):
                    raise BaselineError(f"{prefix}.arms.{mode} must be an object")
                outcome = arm.get("outcome")
                if outcome not in ALL_OUTCOMES:
                    raise BaselineError(f"{prefix}.arms.{mode}.outcome is invalid")
                for result_name in ("public_test", "hidden_check"):
                    result = arm.get(result_name)
                    if result is not None and (
                        not isinstance(result, dict) or not isinstance(result.get("passed"), bool)
                    ):
                        raise BaselineError(f"{prefix}.arms.{mode}.{result_name}.passed must be boolean")
                if "completed" in arm and not isinstance(arm["completed"], bool):
                    raise BaselineError(f"{prefix}.arms.{mode}.completed must be boolean")
                elapsed = None
                if arm.get("elapsed_s") is not None:
                    elapsed = _number(arm["elapsed_s"], f"{prefix}.arms.{mode}.elapsed_s")
                calls = None
                if arm.get("invocation_count") is not None:
                    calls = _number(arm["invocation_count"],
                                    f"{prefix}.arms.{mode}.invocation_count", integer=True)
                tokens = None
                if arm.get("token_usage") is not None:
                    usage = arm["token_usage"]
                    if not isinstance(usage, dict):
                        raise BaselineError(f"{prefix}.arms.{mode}.token_usage must be an object")
                    prompt = _number(usage.get("prompt_tokens"),
                                     f"{prefix}.arms.{mode}.token_usage.prompt_tokens", integer=True)
                    completion = _number(usage.get("completion_tokens"),
                                         f"{prefix}.arms.{mode}.token_usage.completion_tokens", integer=True)
                    tokens = {"prompt_tokens": prompt, "completion_tokens": completion}
                cost = None
                if arm.get("cost_usd") is not None:
                    cost = _number(arm["cost_usd"], f"{prefix}.arms.{mode}.cost_usd")
                evidence.append({
                    "provider": provider, "model": model, "mode": mode, "task_id": task_id,
                    "pair_id": pair_id, "outcome": outcome, "elapsed_s": elapsed,
                    "invocation_count": calls, "token_usage": tokens, "cost_usd": cost,
                })
    if not evidence:
        raise BaselineError(f"{label} contains no benchmark arms")
    return source, evidence


def _scorecard_from_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for item in evidence:
        identity = tuple(item[field] for field in ("provider", "model", "mode", "task_id"))
        groups.setdefault(identity, []).append(item)
    rows = []
    for identity, samples in sorted(groups.items()):
        valid = [item for item in samples if item["outcome"] in VALID_OUTCOMES]
        valid_count = len(valid)
        outcomes = [item["outcome"] for item in valid]
        elapsed = [item["elapsed_s"] for item in valid if item["elapsed_s"] is not None]
        calls = [item["invocation_count"] for item in valid if item["invocation_count"] is not None]
        token_items = [item["token_usage"] for item in valid if item["token_usage"] is not None]
        costs = [item["cost_usd"] for item in valid if item["cost_usd"] is not None]
        elapsed_metric = (
            {"status": "measured", "p50": _percentile(elapsed, .5), "p95": _percentile(elapsed, .95)}
            if valid_count and len(elapsed) == valid_count else
            {"status": "unmeasured", "reason": "valid arms do not consistently record elapsed time"}
        )
        calls_metric = (
            {"status": "measured", "mean_per_valid_sample": _rate(sum(calls), valid_count)}
            if valid_count and len(calls) == valid_count else
            {"status": "unmeasured", "reason": "valid arms do not consistently record invocation count"}
        )
        if valid_count and len(token_items) == valid_count:
            prompt = sum(item["prompt_tokens"] for item in token_items)
            completion = sum(item["completion_tokens"] for item in token_items)
            tokens = {
                "status": "measured",
                "prompt_tokens_per_valid_sample": _rate(prompt, valid_count),
                "completion_tokens_per_valid_sample": _rate(completion, valid_count),
                "total_tokens_per_valid_sample": _rate(prompt + completion, valid_count),
            }
        else:
            tokens = {"status": "unmeasured",
                      "reason": "valid arms do not consistently record token usage"}
        cost = (
            {"status": "measured", "currency": "USD",
             "total_per_valid_sample": _rate(sum(costs), valid_count)}
            if valid_count and len(costs) == valid_count else
            {"status": "unmeasured",
             "reason": "no complete provider pricing or billed cost in valid source evidence"}
        )
        rows.append({
            "provider": identity[0], "model": identity[1], "mode": identity[2],
            "task_id": identity[3], "samples": len(samples), "valid_samples": valid_count,
            "invalid_samples": len(samples) - valid_count,
            "sample_status": "sufficient" if valid_count >= DEFAULT_MIN_SAMPLES else "insufficient",
            "task_success_rate": _rate(outcomes.count("clean_pass"), valid_count),
            "silent_defect_rate": _rate(outcomes.count("silent_defect"), valid_count),
            "safe_stop_rate": _rate(outcomes.count("safe_stop"), valid_count),
            "invalid_sample_rate": _rate(len(samples) - valid_count, len(samples)),
            "elapsed_s": elapsed_metric, "calls": calls_metric, "tokens": tokens, "cost": cost,
        })
    return rows


def _scorecard(source: object, *, label: str = "benchmark") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _, evidence = _normalize_source(source, label=label)
    return _scorecard_from_evidence(evidence), evidence


def _quality_reason(provider: object, verdict: object, rows: list[dict[str, Any]]) -> str | None:
    if provider == "mock" or any(row.get("provider") == "mock" for row in rows):
        return "mock provider is wiring-only evidence"
    if any(row.get("provider") != provider for row in rows):
        return "scorecard provider does not match source provider"
    if verdict != "pass":
        return f"source score verdict is {verdict!r}, not 'pass'"
    return None


def _integrity_payload(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        key: baseline.get(key)
        for key in (
            "baseline_schema_version", "source", "thresholds", "scorecard", "provenance",
        )
    }


def capture_baseline(source: dict[str, Any], *, source_path: str,
                     now: dt.datetime | None = None) -> dict[str, Any]:
    """Convert one benchmark schema-v2 report into deterministic baseline JSON."""
    clock = _clock(now)
    normalized, evidence = _normalize_source(source, label="benchmark")
    generated = _timestamp(normalized["generated"], "benchmark generated")
    if generated > clock + FUTURE_TOLERANCE:
        raise BaselineError("benchmark generated timestamp is implausibly in the future")
    scorecard = _scorecard_from_evidence(evidence)
    verdict = normalized["score"]["verdict"]
    quality_reason = _quality_reason(normalized["provider"], verdict, scorecard)
    result = {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "source": {
            "path": source_path, "content_sha256": _sha256(source),
            "bench_schema_version": 2, "generated": generated.isoformat(),
            "fresh_until": (generated + dt.timedelta(days=DEFAULT_FRESHNESS_DAYS)).isoformat(),
            "provider": normalized["provider"],
            "provider_version": normalized.get("provider_version") or "unmeasured",
            "model": normalized.get("model"),
            "bare_model": normalized.get("bare_model") or normalized.get("model"),
            "rig_model": normalized.get("rig_model") or normalized.get("model"),
            "rig_wb_version": normalized.get("rig_wb_version") or "unmeasured",
            "recipe": normalized.get("recipe"), "recipe_version": normalized.get("recipe_version"),
            "corpus_version": normalized.get("corpus_version"), "score_verdict": verdict,
            "quality_evidence": quality_reason is None,
            "quality_evidence_reason": quality_reason,
        },
        "thresholds": {
            "min_samples_per_identity": DEFAULT_MIN_SAMPLES,
            "max_task_success_rate_drop": .05, "max_silent_defect_rate_increase": 0.0,
            "max_safe_stop_rate_increase": .05, "max_invalid_sample_rate_increase": 0.0,
            "max_elapsed_p95_ratio": 1.25,
            "max_calls_mean_ratio": 1.25, "max_tokens_total_ratio": 1.25,
            "max_cost_total_ratio": 1.25, "freshness_days": DEFAULT_FRESHNESS_DAYS,
        },
        "scorecard": scorecard,
        "provenance": {"format": "rig-wb-benchmark-v2", "source_score": normalized["score"],
                       "normalized_evidence": evidence},
    }
    result["integrity_sha256"] = _sha256(_integrity_payload(result))
    return result


def _validate_thresholds(baseline: dict[str, Any]) -> dict[str, int | float]:
    thresholds = baseline.get("thresholds")
    if not isinstance(thresholds, dict):
        raise BaselineError("baseline thresholds are required")
    missing = [key for key in THRESHOLD_KEYS if key not in thresholds]
    if missing:
        raise BaselineError(f"baseline threshold schema is incomplete: {', '.join(missing)}")
    for key in THRESHOLD_KEYS:
        _number(thresholds[key], f"baseline threshold {key}", integer=key == "min_samples_per_identity")
    if thresholds["min_samples_per_identity"] < 1:
        raise BaselineError("baseline min_samples_per_identity must be at least 1")
    return thresholds


def _baseline_rows(baseline: object, *, verify_integrity: bool = True) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not isinstance(baseline, dict) or baseline.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        raise BaselineError(f"baseline schema_version {BASELINE_SCHEMA_VERSION} is required")
    source = baseline.get("source")
    if not isinstance(source, dict) or source.get("bench_schema_version") != 2:
        raise BaselineError("baseline source bench schema_version 2 is required")
    for field in ("provider", "content_sha256", "generated", "fresh_until", "score_verdict"):
        if field not in source:
            raise BaselineError(f"baseline source {field} is required")
    _identity(source["provider"], "baseline source provider")
    if not isinstance(source["content_sha256"], str) or len(source["content_sha256"]) != 64:
        raise BaselineError("baseline source content_sha256 is invalid")
    _timestamp(source["generated"], "baseline generated")
    _timestamp(source["fresh_until"], "baseline fresh_until")
    _validate_thresholds(baseline)
    rows = baseline.get("scorecard")
    if not isinstance(rows, list) or not rows:
        raise BaselineError("baseline scorecard must be a non-empty list")
    indexed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BaselineError("baseline scorecard rows must be objects")
        identity = tuple(_identity(row.get(field), f"baseline scorecard[{index}].{field}")
                         for field in ("provider", "model", "mode", "task_id"))
        if identity[2] not in {"bare", "rig"}:
            raise BaselineError("baseline scorecard mode is invalid")
        if identity in indexed:
            raise BaselineError(f"duplicate baseline identity: {identity}")
        indexed[identity] = row
    provenance = baseline.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("format") != "rig-wb-benchmark-v2":
        raise BaselineError("baseline provenance is invalid")
    evidence = provenance.get("normalized_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise BaselineError("baseline provenance evidence is required")
    try:
        derived = _scorecard_from_evidence(evidence)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise BaselineError(f"baseline provenance evidence is malformed: {error}") from error
    if derived != rows:
        raise BaselineError("baseline scorecard does not match provenance evidence")
    source_score = provenance.get("source_score")
    if not isinstance(source_score, dict) or source_score.get("verdict") != source.get("score_verdict"):
        raise BaselineError("baseline source score does not match provenance")
    reason = _quality_reason(source.get("provider"), source.get("score_verdict"), rows)
    if source.get("quality_evidence") is not (reason is None) or source.get("quality_evidence_reason") != reason:
        raise BaselineError("baseline quality evidence metadata is inconsistent")
    if verify_integrity and baseline.get("integrity_sha256") != _sha256(_integrity_payload(baseline)):
        raise BaselineError("baseline integrity check failed")
    return indexed


def compare_baseline(baseline: dict[str, Any], current: dict[str, Any], *,
                     now: dt.datetime | None = None) -> dict[str, Any]:
    """Compare current benchmark v2 evidence with an identity-compatible baseline."""
    clock = _clock(now)
    baseline_rows = _baseline_rows(baseline)
    source = baseline["source"]
    quality_reason = _quality_reason(source["provider"], source["score_verdict"], list(baseline_rows.values()))
    if quality_reason:
        raise BaselineError(quality_reason)
    current_rows_list, _ = _scorecard(current, label="current benchmark")
    current_score = current["score"]
    current_quality = _quality_reason(current["provider"], current_score["verdict"], current_rows_list)
    if current_quality:
        raise BaselineError(f"current {current_quality}")
    current_generated = _timestamp(current["generated"], "current benchmark generated")
    baseline_generated = _timestamp(source["generated"], "baseline generated")
    fresh_until = _timestamp(source["fresh_until"], "baseline fresh_until")
    if baseline_generated > clock + FUTURE_TOLERANCE or current_generated > clock + FUTURE_TOLERANCE:
        raise BaselineError("benchmark timestamp is implausibly in the future")
    if clock > fresh_until:
        raise BaselineError(f"baseline is stale (fresh until {fresh_until.isoformat()})")
    if current_generated < baseline_generated:
        raise BaselineError("current benchmark predates the baseline")
    current_rows = {(r["provider"], r["model"], r["mode"], r["task_id"]): r for r in current_rows_list}
    if set(current_rows) != set(baseline_rows):
        missing = sorted(set(baseline_rows) - set(current_rows))
        extra = sorted(set(current_rows) - set(baseline_rows))
        raise BaselineError(f"benchmark identity mismatch (missing={missing}, extra={extra})")
    thresholds = _validate_thresholds(baseline)
    minimum = int(thresholds["min_samples_per_identity"])
    for label, rows in (("current", current_rows), ("baseline", baseline_rows)):
        insufficient = sorted(identity for identity, row in rows.items()
                              if row.get("valid_samples", 0) < minimum)
        if insufficient:
            raise BaselineError(f"{label} evidence has insufficient valid samples: {insufficient}")
    regressions: list[dict[str, Any]] = []

    def record(identity, metric, old, new, limit, rule):
        regressions.append({"provider": identity[0], "model": identity[1], "mode": identity[2],
                            "task_id": identity[3], "metric": metric, "baseline": old,
                            "current": new, "limit": limit, "rule": rule})

    for identity in sorted(baseline_rows):
        old, new = baseline_rows[identity], current_rows[identity]
        for metric, key in (("task_success_rate", "max_task_success_rate_drop"),):
            limit = float(thresholds[key])
            if old[metric] is not None and new[metric] is not None and old[metric] - new[metric] > limit:
                record(identity, metric, old[metric], new[metric], limit, "maximum decrease")
        for metric, key in (("silent_defect_rate", "max_silent_defect_rate_increase"),
                            ("safe_stop_rate", "max_safe_stop_rate_increase"),
                            ("invalid_sample_rate", "max_invalid_sample_rate_increase")):
            limit = float(thresholds[key])
            if old[metric] is not None and new[metric] is not None and new[metric] - old[metric] > limit:
                record(identity, metric, old[metric], new[metric], limit, "maximum increase")
        for metric, path, key in (
            ("elapsed_s.p95", ("elapsed_s", "p95"), "max_elapsed_p95_ratio"),
            ("calls.mean_per_valid_sample", ("calls", "mean_per_valid_sample"), "max_calls_mean_ratio"),
            ("tokens.total_tokens_per_valid_sample", ("tokens", "total_tokens_per_valid_sample"), "max_tokens_total_ratio"),
            ("cost.total_per_valid_sample", ("cost", "total_per_valid_sample"), "max_cost_total_ratio"),
        ):
            old_value, new_value = old[path[0]].get(path[1]), new[path[0]].get(path[1])
            limit = float(thresholds[key])
            if old_value is not None and new_value is not None and old_value > 0 and new_value / old_value > limit:
                record(identity, metric, old_value, new_value, limit, "maximum ratio")
    return {"comparison_schema_version": 1, "status": "fail" if regressions else "pass",
            "identity_count": len(baseline_rows), "regressions": regressions}


def _read_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BaselineError(f"cannot read {label} {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise BaselineError(f"invalid JSON in {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise BaselineError(f"{label} must be a JSON object")
    return value


def canonical_json(value: dict[str, Any]) -> str:
    """Stable JSON representation used by files and --json output."""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as error:
        raise BaselineError(f"value cannot be represented as canonical JSON: {error}") from error


def render_baseline(baseline: dict[str, Any]) -> str:
    rows = _baseline_rows(baseline)
    source = baseline["source"]
    lines = ["## rig benchmark baseline", f"source: {source.get('path', '?')}",
             f"generated: {source['generated']}", f"fresh-until: {source['fresh_until']}",
             f"quality-evidence: {'yes' if source['quality_evidence'] else 'no'}", "", "Scorecard:"]
    for identity, row in sorted(rows.items()):
        elapsed, calls, tokens, cost = row["elapsed_s"], row["calls"], row["tokens"], row["cost"]
        lines.append(
            f"  {' / '.join(identity)}  samples={row['samples']} valid={row['valid_samples']} "
            f"invalid={row['invalid_samples']} success={row['task_success_rate']} "
            f"silent-defect={row['silent_defect_rate']} safe-stop={row['safe_stop_rate']} "
            f"elapsed-p50={elapsed.get('p50', 'unmeasured')} elapsed-p95={elapsed.get('p95', 'unmeasured')} "
            f"calls/valid={calls.get('mean_per_valid_sample', 'unmeasured')} "
            f"tokens/valid={tokens.get('total_tokens_per_valid_sample', 'unmeasured')} "
            f"cost-usd/valid={cost.get('total_per_valid_sample', 'unmeasured')}"
        )
    return "\n".join(lines) + "\n"


def cmd_baseline(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="rig-wb baseline")
    sub = parser.add_subparsers(dest="action", required=True)
    capture = sub.add_parser("capture", help="capture benchmark schema v2 as a versioned baseline")
    capture.add_argument("--input", required=True, type=pathlib.Path)
    capture.add_argument("--output", required=True, type=pathlib.Path)
    show = sub.add_parser("show", help="show a versioned baseline")
    show.add_argument("baseline", type=pathlib.Path)
    show.add_argument("--json", action="store_true")
    compare = sub.add_parser("compare", help="compare benchmark schema v2 evidence to a baseline")
    compare.add_argument("--baseline", required=True, type=pathlib.Path)
    compare.add_argument("--current", required=True, type=pathlib.Path)
    compare.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "capture":
            source = _read_json(args.input, "benchmark")
            result = capture_baseline(source, source_path=str(args.input))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(canonical_json(result), encoding="utf-8")
            print(f"Captured baseline: {args.output}\nIdentities: {len(result['scorecard'])}")
            if not result["source"]["quality_evidence"]:
                print(f"Warning: {result['source']['quality_evidence_reason']}")
            return 0
        baseline = _read_json(args.baseline, "baseline")
        if args.action == "show":
            _baseline_rows(baseline)
            output = canonical_json(baseline) if args.json else render_baseline(baseline)
            print(output, end="")
            return 0
        current = _read_json(args.current, "current benchmark")
        report = compare_baseline(baseline, current)
        if args.json:
            print(canonical_json(report), end="")
        else:
            print(f"Baseline comparison: {report['status'].upper()}\nIdentities: {report['identity_count']}")
            for item in report["regressions"]:
                identity = " / ".join(str(item[k]) for k in ("provider", "model", "mode", "task_id"))
                print(f"  REGRESSION {identity}: {item['metric']} {item['baseline']} -> {item['current']}")
        return 0 if report["status"] == "pass" else 1
    except (BaselineError, KeyError, TypeError, ValueError, OverflowError) as error:
        print(f"[ERROR] baseline: {error}", file=sys.stderr)
        return 2
