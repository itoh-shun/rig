"""Machine evaluation gate for affected prompt surfaces."""

from __future__ import annotations

import json
import pathlib
import subprocess

from rig_workbench import __version__

from .affected import analyze_affected
from .cases import EvalCaseError, canonical_json, evaluation_spec_hash, validate_case
from .compare import validate_result
from .execution import execution_diff_sha256


def _resolve_commit(root: pathlib.Path, revision: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", revision], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve evaluation gate revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        raise EvalCaseError("cannot resolve evaluation gate revision")
    return value


def _cases(root: pathlib.Path) -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    tier = root / "evals" / "cases"
    if tier.is_dir():
        for path in sorted(tier.glob("*/case.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                value = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise EvalCaseError("cannot read evaluation gate case") from exc
            validate_case(value)
            if value["status"] != "approved" or raw != canonical_json(value):
                raise EvalCaseError("evaluation gate case must be approved canonical JSON")
            if value["id"] in loaded:
                raise EvalCaseError("duplicate evaluation gate case id")
            loaded[value["id"]] = value
    return loaded


def _evidence(evidence_root: pathlib.Path, case_id: str) -> list[dict]:
    found: list[dict] = []
    if not evidence_root.is_dir():
        return found
    for path in sorted(evidence_root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvalCaseError("evaluation gate evidence is malformed") from exc
        if isinstance(value, dict) and value.get("case_id") == case_id:
            found.append(value)
    return found


def quality_result_failures(
    result: dict, case: dict, *, expected_commit: str | None = None,
    expected_base: str | None = None, expected_diff: str | None = None,
    provider: str | None = None, model: str | None = None,
    judge_provider: str | None = None, judge_model: str | None = None,
) -> list[str]:
    """Canonical attested-current quality policy for eval gates and packs."""
    validate_case(case)
    validate_result(result)
    case_id = case["id"]
    failures: list[str] = []
    policy = case["provider_policy"]
    if result["provider"] == "mock":
        failures.append(f"mock_evidence_forbidden:{case_id}")
    if policy["mode"] == "allowlist" and result["provider"] not in policy["allowed"]:
        failures.append(f"provider_policy:{case_id}")
    if policy.get("models") and result["model"] not in policy["models"]:
        failures.append(f"model_policy:{case_id}")
    if policy.get("judge_providers") and result["judge_provider"] not in policy["judge_providers"]:
        failures.append(f"judge_provider_policy:{case_id}")
    if policy.get("judge_models") and result["judge_model"] not in policy["judge_models"]:
        failures.append(f"judge_model_policy:{case_id}")
    if provider is not None and result["provider"] != provider:
        failures.append(f"provider_mismatch:{case_id}")
    if model is not None and result["model"] != model:
        failures.append(f"model_mismatch:{case_id}")
    if result["judge_provider"] == "mock":
        failures.append(f"mock_judge_forbidden:{case_id}")
    if judge_provider is not None and result["judge_provider"] != judge_provider:
        failures.append(f"judge_provider_mismatch:{case_id}")
    if judge_model is not None and result["judge_model"] != judge_model:
        failures.append(f"judge_model_mismatch:{case_id}")
    if result["executor_version"] != __version__:
        failures.append(f"executor_version_mismatch:{case_id}")
    if result["judge_executor_version"] != __version__:
        failures.append(f"judge_executor_version_mismatch:{case_id}")
    if result["case_id"] != case_id or result["case_hash"] != evaluation_spec_hash(case):
        failures.append(f"case_hash_mismatch:{case_id}")
    if result["execution_status"] != "available":
        failures.append(f"execution_identity_unavailable:{case_id}")
    if expected_commit is not None and result["execution_commit"] != expected_commit:
        failures.append(f"execution_commit_mismatch:{case_id}")
    if expected_base is not None and result["execution_base_commit"] != expected_base:
        failures.append(f"execution_base_mismatch:{case_id}")
    if expected_diff is not None and result["execution_diff_sha256"] != expected_diff:
        failures.append(f"execution_diff_mismatch:{case_id}")
    if result["phase"] != "current" or result["repeat"] != case["repeat"]:
        failures.append(f"result_phase_or_repeat:{case_id}")
    if any(row["outcome"] != "pass" or row["infra_status"] is not None
           for row in [*result["target"], *result["clean"]]):
        failures.append(f"quality_not_green:{case_id}")
    if case["semantic_rubric"]:
        expected = [item["id"] for item in case["semantic_rubric"]]
        if result["judge"] != {"required": True, "status": "measured"}:
            failures.append(f"judge_unmeasured:{case_id}")
        for row in [*result["target"], *result["clean"]]:
            criteria = row["judge"]["criteria"]
            if (row["judge"]["status"] != "measured"
                    or [item["id"] for item in criteria] != expected
                    or any(item["status"] != "pass" for item in criteria)):
                failures.append(f"semantic_criteria_failed:{case_id}")
                break
    return sorted(set(failures))


def evaluate_gate(
    repo: pathlib.Path | str, *, base: str, head: str = "working",
    evidence_dir: pathlib.Path | str, provider: str | None = None,
    model: str | None = None, judge_provider: str | None = None,
    judge_model: str | None = None,
) -> tuple[dict, int]:
    root = pathlib.Path(repo).resolve()
    affected = analyze_affected(
        root, base=base, head=head, require_cases=True, evidence_dir=evidence_dir,
    )
    if affected["status"] == "noop":
        return ({"eval_gate_schema_version": 1, "status": "noop", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": [], "failures": []}, 0)
    if affected["status"] == "uncovered":
        return ({"eval_gate_schema_version": 1, "status": "failed", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": affected["affected_cases"],
                 "failures": [f"uncovered:{path}" for path in affected["uncovered"]]}, 1)
    resolved_head = _resolve_commit(root, "HEAD" if head == "working" else head)
    resolved_base = _resolve_commit(root, base)
    expected_diff = execution_diff_sha256(root, base=resolved_base, head=head)
    cases = _cases(root)
    failures: list[str] = []
    infra: list[str] = []
    evidence_root = pathlib.Path(evidence_dir)
    for case_id in affected["affected_cases"]:
        case = cases.get(case_id)
        if case is None:
            failures.append(f"case_absent:{case_id}")
            continue
        candidates = _evidence(evidence_root, case_id)
        if not candidates:
            failures.append(f"evidence_absent:{case_id}")
            continue
        valid: list[dict] = []
        for result in candidates:
            try:
                validate_result(result)
            except EvalCaseError as exc:
                infra.append(f"invalid_evidence:{case_id}:{exc}")
                continue
            valid.append(result)
        matching = [result for result in valid if result["phase"] == "current"]
        if len(matching) != 1:
            failures.append(f"current_evidence_count:{case_id}")
            continue
        result = matching[0]
        quality = quality_result_failures(
            result, case, expected_commit=resolved_head, expected_base=resolved_base,
            expected_diff=expected_diff, provider=provider, model=model,
            judge_provider=judge_provider, judge_model=judge_model,
        )
        if any(item.startswith(("execution_", "executor_", "judge_executor_"))
               for item in quality):
            failures.append(f"execution_identity_mismatch:{case_id}")
        failures.extend(quality)
    status = "pass" if not failures and not infra else ("infra_error" if infra else "failed")
    exit_code = 0 if status == "pass" else (2 if infra else 1)
    return ({
        "eval_gate_schema_version": 1, "status": status, "base": base, "head": head,
        "resolved_head": affected["resolved_head"], "cases": affected["affected_cases"],
        "failures": sorted(failures + infra),
    }, exit_code)
