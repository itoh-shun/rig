#!/usr/bin/env python3
"""Fresh, dev-only Japanese writing workflow evaluation adapter.

This module is deliberately separate from the frozen historical paired evaluator.
It imports that evaluator's audited filesystem, launcher, journal, and judgment
primitives without changing its source bytes or historical fingerprints.
"""

from __future__ import annotations

import argparse
import copy
import functools
import importlib.util
import json
import os
import re
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODULE_PATH = Path(__file__).resolve()
PAIRED_PATH = HERE / "paired_dev_eval.py"
PARITY_PATH = HERE / "parity.py"
PROTOCOL_PATH = HERE / "workflow_claude_review_protocol.json"
DEV_CASES = HERE / "parity_cases.dev.json"
CONFIG_PATH = HERE / "workflow_claude_review.providers.example.json"
RECIPE_PATH = REPO / "packs/domain/japanese-writing/recipes/japanese-writing.md"
EXPECTED_DEV_CASES = 10
SCHEMA = 5

# Direct script execution starts with only this benchmark directory on sys.path.
# Add the resolved repository root deterministically before importing runtime code.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_frozen_paired() -> Any:
    spec = importlib.util.spec_from_file_location("jp_frozen_paired_dev_eval", PAIRED_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import frozen paired evaluator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


paired = _load_frozen_paired()

# Audited frozen primitives used by the new adapter.
AttemptJournal = paired.AttemptJournal
RunLock = paired.RunLock
SUPPORT_SAFETY_FIELDS = paired.SUPPORT_SAFETY_FIELDS
canonical_json = paired.canonical_json
canonical_sha256 = paired.canonical_sha256
sha256_text = paired.sha256_text
sha256_file = paired.sha256_file
sha256_fd = paired.sha256_fd
load_dev_cases = paired.load_dev_cases
save_secure_json = paired.save_secure_json
secure_run_provider = paired.secure_run_provider
invoke_provider_audited = paired.invoke_provider_audited
parse_raw_judgment_then_normalize = paired.parse_raw_judgment_then_normalize
normalize_support_safety = paired.normalize_support_safety
validate_parsed_judgment = paired.validate_parsed_judgment
canonical_parsed_result_hash = paired.canonical_parsed_result_hash
_mean = paired._mean
_round = paired._round
_candidate_points = paired._candidate_points
_dimension_points = paired._dimension_points

WORKFLOW_REVIEW_ROWS = (
    "単一成果物",
    "形式",
    "事実保持",
    "推測なし",
    "日本語",
    "秘密情報",
    "障害・サポート安全性",
)
WORKFLOW_CORE_PASS_ROWS = {"単一成果物", "形式", "事実保持", "推測なし", "日本語"}
WORKFLOW_APPLICABLE_SAFETY_CATEGORIES = {"incident_report", "support_reply"}
WORKFLOW_REVIEW_ALLOWED_STATUSES = {
    "単一成果物": {"PASS", "FAIL"},
    "形式": {"PASS", "FAIL", "UNKNOWN"},
    "事実保持": {"PASS", "FAIL", "UNKNOWN"},
    "推測なし": {"PASS", "FAIL", "UNKNOWN"},
    "日本語": {"PASS", "FAIL"},
    "秘密情報": {"PASS", "FAIL", "N/A"},
    "障害・サポート安全性": {"PASS", "FAIL", "N/A", "UNKNOWN"},
}
WORKFLOW_REVIEW_BOUNDS = {
    "max_output_bytes": 16384,
    "max_target_format_codepoints": 80,
    "max_anchor_codepoints": 500,
    "max_repair_conditions": 7,
    "max_repair_codepoints": 500,
}
PARSER_VERSION = 2
PROVIDER_CONTRACTS_SHA256 = "af99bcb363f998003577257224c77e1abc9f7bab6e46ddaef7461ceacefb236f"
LOGICAL_CALL_GRAPH = {
    "always_per_case": [
        "R", "A0", "REVIEW0", "JUDGE_RAW_REFERENCE_FIRST",
        "JUDGE_RAW_CANDIDATE_FIRST",
    ],
    "if_review0_revise": ["A1", "REVIEW1"],
    "if_final_hash_differs_from_a0": [
        "JUDGE_REVIEWED_REFERENCE_FIRST", "JUDGE_REVIEWED_CANDIDATE_FIRST",
    ],
    "if_final_hash_equals_a0": [
        "ALIAS_REVIEWED_REFERENCE_FIRST", "ALIAS_REVIEWED_CANDIDATE_FIRST",
    ],
    "if_review0_contract_exhausted": [
        "JUDGE_RAW_REFERENCE_FIRST", "JUDGE_RAW_CANDIDATE_FIRST",
    ],
    "if_review1_contract_exhausted": [
        "JUDGE_RAW_REFERENCE_FIRST", "JUDGE_RAW_CANDIDATE_FIRST",
    ],
    "second_nonapproval": "NON_DELIVERABLE",
}
REVIEW_EXHAUSTION_POLICY = {
    "reason_code": "review_contract_exhausted",
    "eligible_stages": ["REVIEW0", "REVIEW1"],
    "required_attempts": 3,
    "required_finish_status": "invalid",
    "required_parse_status": "invalid",
    "transition": "NON_DELIVERABLE",
    "review0_rewrite_count": 0,
    "review1_rewrite_count": 1,
    "raw_arm_judgments": True,
    "reviewed_arm_judgments": False,
    "resume_policy": "sealed_exact_attempt_set",
    "otherwise": "abort",
}


def load_workflow_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    expected_acceptance = {
        "deliverable_cases": 10,
        "workflow_overall_effect": 0.1,
        "workflow_naturalness_effect": 0.1,
        "guard_dimensions": ["correctness", "context_fit", "conciseness", "tone"],
        "guard_dimensions_minimum": -0.05,
        "joint_nonregressing_cases": 6,
        "overall_order_consistency": 0.8,
        "reviewed_support_all_fields_both_orders": True,
        "support_preference_gate": False,
    }
    if (
        protocol.get("schema") != 1
        or protocol.get("semantics_version") != 3
        or protocol.get("name")
        != "japanese-writing-fresh-dev-workflow-claude-review"
        or protocol.get("split") != "dev"
        or protocol.get("expected_case_count") != EXPECTED_DEV_CASES
        or protocol.get("arms") != ["raw_writer", "reviewed_workflow"]
        or protocol.get("orders") != ["reference_first", "candidate_first"]
        or protocol.get("dimensions")
        != ["correctness", "naturalness", "context_fit", "conciseness", "tone"]
        or protocol.get("scoring")
        != {"candidate": 1.0, "draw": 0.5, "reference": 0.0}
        or protocol.get("state_machine")
        != ["R_READY", "A0", "REVIEW0", "A1", "REVIEW1", "FINAL", "NON_DELIVERABLE"]
        or protocol.get("semantic_rewrite_max") != 1
        or protocol.get("max_logical_calls") != 90
        or protocol.get("logical_call_graph") != LOGICAL_CALL_GRAPH
        or protocol.get("review_exhaustion") != REVIEW_EXHAUSTION_POLICY
        or set(protocol.get("provider_contracts", {}))
        != {"reference", "candidate", "reviewer", "judge"}
        or canonical_sha256(protocol.get("provider_contracts"))
        != PROVIDER_CONTRACTS_SHA256
        or protocol.get("retry_policy")
        != {
            "max_attempts_per_logical_call": 3,
            "backoff_seconds": [0, 2, 4],
            "lifetime_attempt_budget": 270,
        }
        or protocol.get("review_contract", {}).get("parser_version") != PARSER_VERSION
        or protocol.get("review_contract", {}).get("rows") != list(WORKFLOW_REVIEW_ROWS)
        or protocol.get("review_contract", {}).get("core_pass_rows")
        != ["単一成果物", "形式", "事実保持", "推測なし", "日本語"]
        or protocol.get("review_contract", {}).get("applicable_safety_categories")
        != ["incident_report", "support_reply"]
        or protocol.get("review_contract", {}).get("bounds") != WORKFLOW_REVIEW_BOUNDS
        or protocol.get("support_safety")
        != {
            "category": "support_reply",
            "fields": list(SUPPORT_SAFETY_FIELDS),
            "prompt_suffix": (
                "support_reply の場合のみ、A と B それぞれについて no_file_body, "
                "no_data_rows, structure_header_only_alternative, masking, "
                "safe_alternative を厳密な boolean で support_safety に追加してください。"
            ),
        }
        or protocol.get("acceptance") != expected_acceptance
    ):
        raise ValueError("invalid workflow dev protocol")
    role_expectations = {
        "reference": ("codex", "gpt-5.6-sol", "read-only"),
        "candidate": ("claude", "claude-sonnet-5", "safe-mode"),
        "reviewer": ("claude", "claude-opus-5", "safe-mode"),
        "judge": ("codex", "gpt-5.5", "read-only"),
    }
    for role, (provider, model, sandbox) in role_expectations.items():
        if protocol.get("provider_roles", {}).get(role) != {
            "provider": provider,
            "model": model,
            "sandbox": sandbox,
            "prompt_transport": "stdin",
        }:
            raise ValueError("invalid workflow dev provider roles")
    return protocol


def _parse_workflow_verdict(line: str) -> str:
    """Parse the exact verdict token after a narrow Japanese-equivalent delimiter."""
    if not line.startswith("判定") or len(line) <= len("判定") + 2:
        raise ValueError("workflow review contract final verdict token is invalid")
    delimiter_index = len("判定")
    if line[delimiter_index] not in {":", "："}:
        raise ValueError("workflow review contract final verdict token is invalid")
    spacing = line[delimiter_index + 1]
    if unicodedata.normalize("NFKC", spacing) != " ":
        raise ValueError("workflow review contract final verdict token is invalid")
    verdict = line[delimiter_index + 2 :]
    if verdict == "UNVERIFIED":
        raise ValueError("workflow review contract verdict is unverified")
    if verdict not in {"APPROVE", "REVISE"}:
        raise ValueError("workflow review contract final verdict token is invalid")
    return verdict


def parse_workflow_review(raw: str, *, category: str) -> dict[str, Any]:
    """Parse one bounded shipped Japanese review verdict without coercion."""
    if len(raw.encode("utf-8")) > WORKFLOW_REVIEW_BOUNDS["max_output_bytes"]:
        raise ValueError("workflow review contract exceeds its size bound")
    lines = raw.splitlines()
    if len(lines) < 12 or not re.fullmatch(r"対象形式: \S(?:.*\S)?", lines[0]):
        raise ValueError("workflow review contract is malformed")
    if len(lines[0].removeprefix("対象形式: ")) > WORKFLOW_REVIEW_BOUNDS[
        "max_target_format_codepoints"
    ] or lines[1] != "検査:":
        raise ValueError("workflow review contract is malformed")
    try:
        repair_index = lines.index("修正条件:", 2)
    except ValueError as error:
        raise ValueError("workflow review contract is malformed") from error
    row_lines = lines[2:repair_index]
    if len(row_lines) != len(WORKFLOW_REVIEW_ROWS):
        raise ValueError("workflow review contract rows are missing or duplicated")
    rows: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"- ([^:]+): (PASS|FAIL|N/A|UNKNOWN) — (\S(?:.*\S)?)")
    for line in row_lines:
        match = pattern.fullmatch(line)
        if match is None:
            raise ValueError("workflow review contract row is malformed")
        label, status, anchor = match.groups()
        if (
            label not in WORKFLOW_REVIEW_ROWS
            or label in rows
            or status not in WORKFLOW_REVIEW_ALLOWED_STATUSES[label]
            or len(anchor) > WORKFLOW_REVIEW_BOUNDS["max_anchor_codepoints"]
        ):
            raise ValueError("workflow review contract row is invalid")
        rows[label] = {"status": status, "anchor": anchor}
    if tuple(rows) != WORKFLOW_REVIEW_ROWS:
        raise ValueError("workflow review contract row order is invalid")
    repair_lines = lines[repair_index + 1 : -1]
    if (
        not repair_lines
        or len(repair_lines) > WORKFLOW_REVIEW_BOUNDS["max_repair_conditions"]
        or any(not re.fullmatch(r"- \S(?:.*\S)?", line) for line in repair_lines)
        or any(
            len(line.removeprefix("- "))
            > WORKFLOW_REVIEW_BOUNDS["max_repair_codepoints"]
            for line in repair_lines
        )
    ):
        raise ValueError("workflow review contract repair conditions are malformed")
    verdict = _parse_workflow_verdict(lines[-1])
    repair_conditions = [line.removeprefix("- ") for line in repair_lines]
    approved = all(rows[label]["status"] == "PASS" for label in WORKFLOW_CORE_PASS_ROWS)
    approved = approved and rows["秘密情報"]["status"] in {"PASS", "N/A"}
    safety_allowed = (
        {"PASS"} if category in WORKFLOW_APPLICABLE_SAFETY_CATEGORIES
        else {"PASS", "N/A"}
    )
    approved = approved and rows["障害・サポート安全性"]["status"] in safety_allowed
    if verdict == "APPROVE" and not approved:
        raise ValueError("workflow review contract approval has blocking rows")
    if verdict == "REVISE" and approved:
        raise ValueError("workflow review contract revise has no blocking row")
    if verdict == "APPROVE" and repair_conditions != ["なし"]:
        raise ValueError("workflow review contract approval has repair conditions")
    if verdict == "REVISE" and repair_conditions == ["なし"]:
        raise ValueError("workflow review contract revise lacks repair conditions")
    return {
        "parser_version": PARSER_VERSION,
        "target_format": lines[0].removeprefix("対象形式: "),
        "rows": rows,
        "repair_conditions": repair_conditions,
        "verdict": verdict,
        "approved": verdict == "APPROVE",
    }


def load_workflow_steps() -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the shipped write/review steps with their recipe-bound facets."""
    from rig_workbench.orchestrate.recipes import (
        load_steps,
        parse_frontmatter,
        resolve_extends,
    )

    recipe, warnings = resolve_extends(parse_frontmatter(RECIPE_PATH), RECIPE_PATH)
    if warnings:
        raise ValueError("workflow recipe resolution emitted warnings")
    steps = load_steps(recipe)
    if [step.get("id") for step in steps] != ["write", "review"]:
        raise ValueError("workflow recipe must contain exactly write then review")
    return steps[0], steps[1]


@functools.lru_cache(maxsize=1)
def _workflow_composition() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, list[str]], dict[str, list[str]]
]:
    from rig_workbench.orchestrate.providers import resolve_prompt_facets

    write_step, review_step = load_workflow_steps()
    review_persona_step = {
        **review_step,
        "personas": ["japanese-writing-reviewer"],
    }
    return (
        write_step,
        review_step,
        resolve_prompt_facets(write_step),
        resolve_prompt_facets(review_persona_step),
    )


@functools.lru_cache(maxsize=1)
def _runtime_start_template() -> dict[str, Any]:
    from rig_workbench.orchestrate.runstate import compute_next, new_state

    write_step, review_step, _write_facets, _review_facets = _workflow_composition()
    state = new_state(
        "japanese-writing", [write_step, review_step], "<WORKFLOW_REQUEST>"
    )
    action, _message = compute_next(state)
    if action != "START" or state["steps"][state["cursor"]]["id"] != "write":
        raise ValueError("workflow runtime could not start write")
    return state


def build_workflow_runtime_state(
    request: str,
    *,
    stage: str,
    correction_conditions: str | None = None,
) -> dict[str, Any]:
    """Construct the same state transitions the shipped runtime composes from."""
    from rig_workbench.orchestrate.runstate import compute_next

    state = copy.deepcopy(_runtime_start_template())
    state["goal"] = request
    if stage == "write":
        return state
    action, _message = compute_next(state)
    if action != "ADVANCE":
        raise ValueError("workflow runtime could not advance from write")
    action, _message = compute_next(state)
    if action != "START" or state["steps"][state["cursor"]]["id"] != "review":
        raise ValueError("workflow runtime could not start review")
    if stage == "review":
        return state
    if stage != "repair" or not correction_conditions:
        raise ValueError("workflow runtime repair requires parsed correction conditions")
    review_state = state["step_state"]["review"]
    review_state["retries"] = int(review_state.get("retries", 0)) + 1
    state["history"].append({"action": "INDEPENDENT_REVIEW", "step": "review"})
    state["history"].append(
        {"action": "REVISE", "step": "review", "producer": "write"}
    )
    write_state = state["step_state"]["write"]
    write_state["status"] = "pending"
    write_state["last_failure"] = correction_conditions
    state["cursor"] = 0
    action, _message = compute_next(state)
    if action != "START" or state["steps"][state["cursor"]]["id"] != "write":
        raise ValueError("workflow runtime could not start repair")
    return state


def compose_write_prompt(request: str) -> str:
    from rig_workbench.orchestrate.providers import compose_step_prompt

    _write_step, _review_step, write_facets, _review_facets = _workflow_composition()
    state = build_workflow_runtime_state(request, stage="write")
    write_step = state["steps"][state["cursor"]]
    return compose_step_prompt(
        state,
        write_step,
        state["step_state"]["write"],
        facets=write_facets,
    )


def compose_review_prompt(request: str, artifact: str) -> str:
    from rig_workbench.orchestrate.providers import compose_artifact_review_prompt

    _write_step, _review_step, _write_facets, review_facets = _workflow_composition()
    state = build_workflow_runtime_state(request, stage="review")
    review_step = state["steps"][state["cursor"]]
    return compose_artifact_review_prompt(
        state,
        review_step,
        "japanese-writing-reviewer",
        artifact,
        facets=review_facets,
    )


def parsed_review_corrections(
    parsed: dict[str, Any], *, category: str
) -> dict[str, Any]:
    """Return only blocking parsed rows and bounded correction conditions."""
    rows = parsed["rows"]
    blocking: dict[str, dict[str, str]] = {}
    for label in WORKFLOW_REVIEW_ROWS:
        status = rows[label]["status"]
        allowed = {"PASS"}
        if label == "秘密情報":
            allowed = {"PASS", "N/A"}
        elif label == "障害・サポート安全性":
            allowed = (
                {"PASS"}
                if category in WORKFLOW_APPLICABLE_SAFETY_CATEGORIES
                else {"PASS", "N/A"}
            )
        if status not in allowed:
            blocking[label] = {"status": status, "anchor": rows[label]["anchor"]}
    if parsed.get("verdict") != "REVISE" or not blocking:
        raise ValueError("repair requires one strictly parsed REVISE verdict")
    return {
        "parser_version": PARSER_VERSION,
        "failing_rows": blocking,
        "correction_conditions": list(parsed["repair_conditions"]),
    }


def compose_repair_prompt(
    request: str,
    artifact: str,
    parsed_review: dict[str, Any],
    *,
    category: str,
) -> str:
    from rig_workbench.orchestrate.providers import compose_repair_prompt as compose

    corrections = parsed_review_corrections(parsed_review, category=category)
    _write_step, _review_step, write_facets, _review_facets = _workflow_composition()
    correction_text = canonical_json(corrections).decode("utf-8")
    state = build_workflow_runtime_state(
        request,
        stage="repair",
        correction_conditions=correction_text,
    )
    write_step = state["steps"][state["cursor"]]
    return compose(
        state,
        write_step,
        artifact,
        correction_text,
        facets=write_facets,
    )


def _support_judge_prompt(prompt: str, category: str, protocol: dict[str, Any]) -> str:
    if category != protocol["support_safety"]["category"]:
        return prompt
    return f"{prompt}\n\nCATEGORY: support_reply\n{protocol['support_safety']['prompt_suffix']}"


def _fingerprint_revise_review(category: str) -> dict[str, Any]:
    safety = "PASS" if category in WORKFLOW_APPLICABLE_SAFETY_CATEGORIES else "N/A"
    raw = (
        "対象形式: plain-text\n検査:\n"
        "- 単一成果物: PASS — fingerprint\n"
        "- 形式: PASS — fingerprint\n"
        "- 事実保持: FAIL — fingerprint\n"
        "- 推測なし: PASS — fingerprint\n"
        "- 日本語: PASS — fingerprint\n"
        "- 秘密情報: N/A — fingerprint\n"
        f"- 障害・サポート安全性: {safety} — fingerprint\n"
        "修正条件:\n- fingerprint correction\n判定: REVISE"
    )
    return parse_workflow_review(raw, category=category)


def build_workflow_fingerprint_inputs(
    *,
    cases: list[dict[str, Any]],
    cases_path: Path,
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
) -> dict[str, Any]:
    """Bind all code, inputs, provider pins, graph, and effective prompt shapes."""
    from rig_workbench.orchestrate import providers as runtime_providers

    case_rows = []
    prompt_plan: dict[str, Any] = {}
    sentinel_reference = "<REFERENCE_OUTPUT>"
    sentinel_candidate = "<CANDIDATE_OUTPUT>"
    for case in cases:
        request = case["prompt"]
        category = case["category"]
        case_rows.append(
            {
                "id": case["id"],
                "category": category,
                "request_sha256": sha256_text(request),
                "case_sha256": canonical_sha256(case),
            }
        )
        revise = _fingerprint_revise_review(category)
        judgments = {}
        for order in protocol["orders"]:
            judge_prompt, mapping = judgment_prompt_fn(
                request, sentinel_reference, sentinel_candidate, order
            )
            judge_prompt = _support_judge_prompt(judge_prompt, category, protocol)
            judgments[order] = {
                "prompt_sha256": sha256_text(judge_prompt),
                "mapping_sha256": canonical_sha256(mapping),
            }
        prompt_plan[case["id"]] = {
            "reference_prompt_sha256": sha256_text(request),
            "write_prompt_sha256": sha256_text(compose_write_prompt(request)),
            "review_template_sha256": sha256_text(
                compose_review_prompt(request, sentinel_candidate)
            ),
            "repair_template_sha256": sha256_text(
                compose_repair_prompt(
                    request,
                    sentinel_candidate,
                    revise,
                    category=category,
                )
            ),
            "judge_templates": judgments,
        }
    provider_bindings = {
        role: {
            key: metadata.get(key)
            for key in (
                "provider", "requested_model", "provider_spec_sha256",
                "executable_sha256", "launcher_chain",
            )
        }
        for role, metadata in sorted(providers.items())
    }
    return {
        "schema": 1,
        "mode": "fresh_workflow_dev",
        "source_sha256": {
            "workflow_adapter": sha256_file(MODULE_PATH),
            "frozen_paired_evaluator": sha256_file(PAIRED_PATH),
            "parity_adapter": sha256_file(PARITY_PATH),
            "runtime_prompt_composer": sha256_file(
                Path(runtime_providers.__file__).resolve()
            ),
        },
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
        "cases_file_sha256": sha256_file(cases_path),
        "cases": case_rows,
        "logical_call_graph": protocol["logical_call_graph"],
        "prompt_plan": prompt_plan,
        "providers": provider_bindings,
    }


def _prepare_workflow_run(
    run_dir: Path,
    *,
    run_dir_fd: int,
    fingerprint_inputs: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    existing = [name for name in os.listdir(run_dir_fd) if name != "run.lock"]
    manifest_path = run_dir / "manifest.json"
    fingerprint = canonical_sha256(fingerprint_inputs)
    if "manifest.json" in existing:
        manifest = paired._read_secure_json_artifact(
            manifest_path, run_dir_fd=run_dir_fd
        )
        if manifest != {
            "schema": SCHEMA,
            "run_id": manifest.get("run_id"),
            "run_mode": "fresh_workflow_dev",
            "fingerprint": fingerprint,
            "fingerprint_inputs": fingerprint_inputs,
        }:
            raise ValueError("workflow run manifest fingerprint mismatch")
        return manifest
    if existing:
        raise ValueError("new workflow run requires an empty artifact directory")
    manifest = {
        "schema": SCHEMA,
        "run_id": run_id,
        "run_mode": "fresh_workflow_dev",
        "fingerprint": fingerprint,
        "fingerprint_inputs": fingerprint_inputs,
    }
    paired._write_secure_json_exclusive(
        manifest_path, manifest, run_dir_fd=run_dir_fd
    )
    return manifest


def _load_workflow_checkpoint(
    path: Path, fingerprint: str, *, run_dir_fd: int
) -> dict[str, Any]:
    try:
        state = paired._read_secure_json_artifact(path, run_dir_fd=run_dir_fd)
    except ValueError:
        if path.name in os.listdir(run_dir_fd):
            raise
        return {
            "schema": SCHEMA,
            "fingerprint": fingerprint,
            "workflow_cases": {},
            "judgments": {},
        }
    if (
        set(state) != {"schema", "fingerprint", "workflow_cases", "judgments"}
        or state.get("schema") != SCHEMA
        or state.get("fingerprint") != fingerprint
        or not isinstance(state.get("workflow_cases"), dict)
        or not isinstance(state.get("judgments"), dict)
    ):
        raise ValueError("workflow checkpoint fingerprint mismatch")
    return state


def _workflow_record(
    *,
    logical_call_id: str,
    phase: str,
    prompt: str,
    role: str,
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    journal: Any,
    runner: Callable[..., str],
    context: dict[str, Any],
    max_attempts: int,
    parser: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    call = invoke_provider_audited(
        journal=journal,
        logical_call_id=logical_call_id,
        phase=phase,
        prompt=prompt,
        spec=specs[role],
        provider_metadata=providers[role],
        context=context,
        runner=runner,
        parser=parser,
        max_attempts=max_attempts,
    )
    text = call["output"]
    record = {
        "logical_call_id": logical_call_id,
        "role": role,
        "prompt_sha256": sha256_text(prompt),
        "output_sha256": sha256_text(text),
        "output_size_bytes": len(text.encode("utf-8")),
        "completed_attempt_id": call["finished"]["attempt_id"],
        "provider_spec_sha256": providers[role]["provider_spec_sha256"],
        "text": text,
    }
    if parser is not None:
        record["parsed"] = call["parsed"]
        record["parsed_result_sha256"] = canonical_sha256(call["parsed"])
    return record


def _expected_artifact_prompt(
    case: dict[str, Any], artifact_name: str, artifacts: dict[str, Any]
) -> str:
    if artifact_name == "R":
        return case["prompt"]
    if artifact_name == "A0":
        return compose_write_prompt(case["prompt"])
    if artifact_name == "REVIEW0":
        return compose_review_prompt(case["prompt"], artifacts["A0"]["text"])
    if artifact_name == "A1":
        return compose_repair_prompt(
            case["prompt"],
            artifacts["A0"]["text"],
            artifacts["REVIEW0"]["parsed"],
            category=case["category"],
        )
    if artifact_name == "REVIEW1":
        return compose_review_prompt(case["prompt"], artifacts["A1"]["text"])
    raise ValueError("unknown workflow artifact stage")


def _artifact_binding(case_id: str, artifact_name: str) -> tuple[str, str, str]:
    bindings = {
        "R": (f"workflow:gen:{case_id}:R", "reference", "generation"),
        "A0": (f"workflow:gen:{case_id}:A0", "candidate", "generation"),
        "REVIEW0": (f"workflow:review:{case_id}:0", "reviewer", "review"),
        "A1": (f"workflow:gen:{case_id}:A1", "candidate", "generation"),
        "REVIEW1": (f"workflow:review:{case_id}:1", "reviewer", "review"),
    }
    return bindings[artifact_name]


def _artifact_context(
    case: dict[str, Any], artifact_name: str, artifacts: dict[str, Any]
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "case_id": case["id"],
        "stage": artifact_name,
        "request_sha256": sha256_text(case["prompt"]),
    }
    if artifact_name == "REVIEW0":
        context["candidate_output_sha256"] = artifacts["A0"]["output_sha256"]
    elif artifact_name == "A1":
        context.update(
            {
                "semantic_rewrite": 1,
                "a0_output_sha256": artifacts["A0"]["output_sha256"],
                "review_output_sha256": artifacts["REVIEW0"]["output_sha256"],
                "review_parsed_sha256": artifacts["REVIEW0"][
                    "parsed_result_sha256"
                ],
            }
        )
    elif artifact_name == "REVIEW1":
        context["candidate_output_sha256"] = artifacts["A1"]["output_sha256"]
    return context


def _journal_rows_by_attempt(
    journal_records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    starts = {
        row["attempt_id"]: row
        for row in journal_records
        if row.get("event") == "attempt_started"
    }
    finishes = {
        row["attempt_id"]: row
        for row in journal_records
        if row.get("event") == "attempt_finished"
    }
    return starts, finishes


def _review_contract_exhaustion(
    journal_records: list[dict[str, Any]],
    *,
    logical_call_id: str,
    stage: str,
    max_attempts: int,
) -> dict[str, Any] | None:
    starts = [
        row for row in journal_records
        if row.get("event") == "attempt_started"
        and row.get("logical_call_id") == logical_call_id
    ]
    finishes = [
        row for row in journal_records
        if row.get("event") == "attempt_finished"
        and row.get("logical_call_id") == logical_call_id
    ]
    starts.sort(key=lambda row: row.get("attempt_no", -1))
    finishes.sort(key=lambda row: row.get("attempt_no", -1))
    if (
        len(starts) != max_attempts
        or len(finishes) != max_attempts
        or [row.get("attempt_no") for row in starts]
        != list(range(1, max_attempts + 1))
        or [row.get("attempt_no") for row in finishes]
        != list(range(1, max_attempts + 1))
        or any(
            start.get("attempt_id") != finish.get("attempt_id")
            for start, finish in zip(starts, finishes, strict=True)
        )
        or any(
            finish.get("status") != "invalid"
            or finish.get("parse_status") != "invalid"
            or not re.fullmatch(r"[0-9a-f]{64}", str(finish.get("output_sha256", "")))
            for finish in finishes
        )
    ):
        return None
    return {
        "reason_code": "review_contract_exhausted",
        "stage": stage,
        "logical_call_id": logical_call_id,
        "attempts": [
            {
                "attempt_id": finish["attempt_id"],
                "output_sha256": finish["output_sha256"],
            }
            for finish in finishes
        ],
    }


def validate_workflow_checkpoint(
    *,
    state: dict[str, Any],
    cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    journal_records: list[dict[str, Any]],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
) -> None:
    """Recompute every checkpoint dependency and require exact journal coverage."""
    case_by_id = {case["id"]: case for case in cases}
    if not set(state["workflow_cases"]) <= set(case_by_id):
        raise ValueError("workflow checkpoint contains an unknown case")
    allowed_logical_ids = {
        logical_id
        for case in cases
        for logical_id in (
            f"workflow:gen:{case['id']}:R",
            f"workflow:gen:{case['id']}:A0",
            f"workflow:review:{case['id']}:0",
            f"workflow:gen:{case['id']}:A1",
            f"workflow:review:{case['id']}:1",
            *(
                f"workflow:judge:{case['id']}:{arm}:{order}"
                for arm in ("raw_writer", "reviewed_workflow")
                for order in protocol["orders"]
            ),
        )
    }
    if any(
        row.get("event") == "attempt_started"
        and row.get("logical_call_id") not in allowed_logical_ids
        for row in journal_records
    ):
        raise ValueError("workflow journal violates the logical call graph")
    started_ids = {
        row["logical_call_id"]
        for row in journal_records
        if row.get("event") == "attempt_started"
    }
    for case in cases:
        item = state["workflow_cases"].get(case["id"], {})
        artifacts = item.get("artifacts", {})
        review0 = artifacts.get("REVIEW0", {}).get("parsed", {})
        a1_id = f"workflow:gen:{case['id']}:A1"
        review1_id = f"workflow:review:{case['id']}:1"
        if (
            (a1_id in started_ids or review1_id in started_ids)
            and review0.get("verdict") != "REVISE"
        ) or (review1_id in started_ids and "A1" not in artifacts):
            raise ValueError("workflow journal violates the conditional call graph")
        reviewed_judges = {
            f"workflow:judge:{case['id']}:reviewed_workflow:{order}"
            for order in protocol["orders"]
        }
        if started_ids & reviewed_judges:
            final = artifacts.get(item.get("final_alias", ""), {})
            if (
                item.get("state") != "FINAL"
                or final.get("output_sha256")
                == artifacts.get("A0", {}).get("output_sha256")
            ):
                raise ValueError("workflow journal violates the conditional call graph")
    starts, finishes = _journal_rows_by_attempt(journal_records)
    referenced_attempts: list[str] = []
    allowed_artifacts = {"R", "A0", "REVIEW0", "A1", "REVIEW1"}
    for case_id, case_state in state["workflow_cases"].items():
        if set(case_state) != {
            "state", "rewrite_count", "artifacts", "final_alias",
            "reason_code", "review_exhaustion",
        }:
            raise ValueError("workflow checkpoint case state is malformed")
        artifacts = case_state.get("artifacts")
        if not isinstance(artifacts, dict) or not set(artifacts) <= allowed_artifacts:
            raise ValueError("workflow checkpoint artifacts are malformed")
        if case_state.get("rewrite_count") not in {0, 1}:
            raise ValueError("workflow semantic rewrite bound exceeded")
        if (case_state["rewrite_count"] == 1) != ("A1" in artifacts):
            raise ValueError("workflow checkpoint rewrite state mismatch")
        for name in ("A0", "REVIEW0", "A1", "REVIEW1"):
            dependency = {
                "A0": "R", "REVIEW0": "A0", "A1": "REVIEW0", "REVIEW1": "A1"
            }[name]
            if name in artifacts and dependency not in artifacts:
                raise ValueError("workflow checkpoint artifact dependency mismatch")
        for artifact_name, record in artifacts.items():
            if not isinstance(record, dict) or not isinstance(record.get("text"), str):
                raise ValueError("workflow checkpoint artifact is malformed")
            logical_id, role, phase = _artifact_binding(case_id, artifact_name)
            expected_prompt = _expected_artifact_prompt(
                case_by_id[case_id], artifact_name, artifacts
            )
            context = _artifact_context(
                case_by_id[case_id], artifact_name, artifacts
            )
            if (
                record.get("logical_call_id") != logical_id
                or record.get("role") != role
                or record.get("prompt_sha256") != sha256_text(expected_prompt)
                or record.get("output_sha256") != sha256_text(record["text"])
                or record.get("output_size_bytes")
                != len(record["text"].encode("utf-8"))
                or record.get("provider_spec_sha256")
                != providers[role]["provider_spec_sha256"]
            ):
                raise ValueError("workflow checkpoint artifact binding mismatch")
            if artifact_name.startswith("REVIEW"):
                expected_parsed = parse_workflow_review(
                    record["text"], category=case_by_id[case_id]["category"]
                )
                if (
                    record.get("parsed") != expected_parsed
                    or record.get("parsed_result_sha256")
                    != canonical_sha256(expected_parsed)
                ):
                    raise ValueError("workflow checkpoint parsed review mismatch")
            attempt_id = record.get("completed_attempt_id")
            start, finish = starts.get(attempt_id), finishes.get(attempt_id)
            if (
                start is None
                or finish is None
                or start.get("logical_call_id") != logical_id
                or start.get("phase") != phase
                or start.get("prompt_sha256") != sha256_text(expected_prompt)
                or start.get("provider_spec_sha256")
                != providers[role]["provider_spec_sha256"]
                or any(start.get(key) != value for key, value in context.items())
                or finish.get("status") != "success"
                or finish.get("output_sha256") != record["output_sha256"]
                or (
                    artifact_name.startswith("REVIEW")
                    and finish.get("parsed_result_sha256")
                    != record["parsed_result_sha256"]
                )
            ):
                raise ValueError("workflow checkpoint journal binding mismatch")
            referenced_attempts.append(attempt_id)
        exhaustion = case_state.get("review_exhaustion")
        if exhaustion is not None:
            if not isinstance(exhaustion, dict):
                raise ValueError("workflow checkpoint review exhaustion is malformed")
            stage = exhaustion.get("stage")
            stage_index = {"REVIEW0": 0, "REVIEW1": 1}.get(stage)
            if stage_index is None:
                raise ValueError("workflow checkpoint review exhaustion is malformed")
            logical_id = f"workflow:review:{case_id}:{stage_index}"
            expected = _review_contract_exhaustion(
                journal_records,
                logical_call_id=logical_id,
                stage=stage,
                max_attempts=protocol["retry_policy"]["max_attempts_per_logical_call"],
            )
            prompt = _expected_artifact_prompt(
                case_by_id[case_id], stage, artifacts
            )
            context = _artifact_context(case_by_id[case_id], stage, artifacts)
            logical_starts = [
                row for row in journal_records
                if row.get("event") == "attempt_started"
                and row.get("logical_call_id") == logical_id
            ]
            if (
                exhaustion != expected
                or any(
                    start.get("phase") != "review"
                    or start.get("prompt_sha256") != sha256_text(prompt)
                    or start.get("provider_spec_sha256")
                    != providers["reviewer"]["provider_spec_sha256"]
                    or any(start.get(key) != value for key, value in context.items())
                    for start in logical_starts
                )
            ):
                raise ValueError("workflow checkpoint review exhaustion binding mismatch")
        _validate_case_terminal_state(case_state)

    for key, record in state["judgments"].items():
        parts = key.split("::")
        if (
            len(parts) != 3
            or parts[0] not in case_by_id
            or parts[1] not in {"raw_writer", "reviewed_workflow"}
            or parts[2] not in protocol["orders"]
            or not isinstance(record, dict)
        ):
            raise ValueError("workflow checkpoint judgment is malformed")
        case_id, arm, order = parts
        case = case_by_id[case_id]
        case_state = state["workflow_cases"].get(case_id)
        if case_state is None:
            raise ValueError("workflow checkpoint judgment lacks its case")
        artifacts = case_state["artifacts"]
        candidate_alias = "A0" if arm == "raw_writer" else case_state["final_alias"]
        if candidate_alias not in artifacts:
            raise ValueError("workflow checkpoint judgment lacks its candidate")
        reference, candidate = artifacts["R"], artifacts[candidate_alias]
        judge_prompt, mapping = judgment_prompt_fn(
            case["prompt"], reference["text"], candidate["text"], order
        )
        judge_prompt = _support_judge_prompt(judge_prompt, case["category"], protocol)
        if (
            record.get("case_id") != case_id
            or record.get("category") != case["category"]
            or record.get("arm") != arm
            or record.get("order") != order
            or record.get("prompt_sha256") != sha256_text(judge_prompt)
            or record.get("mapping_sha256") != canonical_sha256(mapping)
            or record.get("reference_output_sha256") != reference["output_sha256"]
            or record.get("candidate_output_sha256") != candidate["output_sha256"]
            or record.get("parsed_result_sha256")
            != canonical_parsed_result_hash(record)
        ):
            raise ValueError("workflow checkpoint judgment binding mismatch")
        if record.get("aliased") is True:
            raw_key = f"{case_id}::raw_writer::{order}"
            raw = state["judgments"].get(raw_key)
            expected = dict(raw) if isinstance(raw, dict) else {}
            expected.update(
                {
                    "logical_call_id": f"workflow:alias:{case_id}:reviewed_workflow:{order}",
                    "arm": "reviewed_workflow",
                    "candidate_output_sha256": candidate["output_sha256"],
                    "aliased": True,
                    "alias_of": raw_key,
                }
            )
            if (
                arm != "reviewed_workflow"
                or candidate["output_sha256"] != artifacts["A0"]["output_sha256"]
                or record != expected
            ):
                raise ValueError("workflow checkpoint alias binding mismatch")
            continue
        logical_id = f"workflow:judge:{case_id}:{arm}:{order}"
        if (
            record.get("aliased") is not False
            or "alias_of" in record
            or record.get("logical_call_id") != logical_id
            or record.get("provider_spec_sha256")
            != providers["judge"]["provider_spec_sha256"]
        ):
            raise ValueError("workflow checkpoint judgment binding mismatch")
        attempt_id = record.get("completed_attempt_id")
        start, finish = starts.get(attempt_id), finishes.get(attempt_id)
        context = {
            "case_id": case_id,
            "category": case["category"],
            "arm": arm,
            "order": order,
            "reference_output_sha256": reference["output_sha256"],
            "candidate_output_sha256": candidate["output_sha256"],
            "mapping_sha256": canonical_sha256(mapping),
        }
        if (
            start is None
            or finish is None
            or start.get("logical_call_id") != logical_id
            or start.get("phase") != "judgment"
            or start.get("prompt_sha256") != sha256_text(judge_prompt)
            or start.get("provider_spec_sha256")
            != providers["judge"]["provider_spec_sha256"]
            or any(start.get(name) != value for name, value in context.items())
            or finish.get("status") != "success"
            or finish.get("output_sha256") != record["output_sha256"]
            or finish.get("parsed_result_sha256")
            != record["parsed_result_sha256"]
        ):
            raise ValueError("workflow checkpoint journal binding mismatch")
        referenced_attempts.append(attempt_id)

    successful_attempts = [
        row["attempt_id"]
        for row in journal_records
        if row.get("event") == "attempt_finished" and row.get("status") == "success"
    ]
    if (
        len(referenced_attempts) != len(set(referenced_attempts))
        or sorted(referenced_attempts) != sorted(successful_attempts)
    ):
        raise ValueError("workflow checkpoint has an orphan or multiply referenced success")


def _validate_case_terminal_state(case_state: dict[str, Any]) -> None:
    artifacts = case_state["artifacts"]
    state_name = case_state.get("state")
    final_alias = case_state.get("final_alias")
    reason_code = case_state.get("reason_code")
    exhaustion = case_state.get("review_exhaustion")
    if state_name not in {
        "R_READY", "A0", "REVIEW0", "A1", "REVIEW1", "FINAL",
        "NON_DELIVERABLE",
    }:
        raise ValueError("workflow checkpoint terminal state is invalid")
    if state_name == "FINAL":
        review_key = "REVIEW0" if final_alias == "A0" else "REVIEW1"
        expected_rewrites = 0 if final_alias == "A0" else 1
        if (
            final_alias not in {"A0", "A1"}
            or final_alias not in artifacts
            or review_key not in artifacts
            or artifacts[review_key]["parsed"].get("approved") is not True
            or case_state["rewrite_count"] != expected_rewrites
            or reason_code is not None
            or exhaustion is not None
        ):
            raise ValueError("workflow checkpoint terminal state mismatch")
    elif state_name == "NON_DELIVERABLE":
        exhausted_stage = exhaustion.get("stage") if isinstance(exhaustion, dict) else None
        rejected_after_rewrite = (
            reason_code is None
            and exhaustion is None
            and case_state["rewrite_count"] == 1
            and "REVIEW1" in artifacts
            and artifacts["REVIEW1"]["parsed"].get("approved") is False
        )
        review0_exhausted = (
            reason_code == "review_contract_exhausted"
            and exhausted_stage == "REVIEW0"
            and case_state["rewrite_count"] == 0
            and not {"REVIEW0", "A1", "REVIEW1"} & set(artifacts)
        )
        review1_exhausted = (
            reason_code == "review_contract_exhausted"
            and exhausted_stage == "REVIEW1"
            and case_state["rewrite_count"] == 1
            and "REVIEW0" in artifacts
            and artifacts["REVIEW0"]["parsed"].get("verdict") == "REVISE"
            and "A1" in artifacts
            and "REVIEW1" not in artifacts
        )
        if final_alias is not None or not (
            rejected_after_rewrite or review0_exhausted or review1_exhausted
        ):
            raise ValueError("workflow checkpoint terminal state mismatch")
    elif final_alias is not None or reason_code is not None or exhaustion is not None:
        raise ValueError("workflow checkpoint terminal state mismatch")


def _workflow_judgment(
    *,
    case: dict[str, Any],
    arm: str,
    order: str,
    reference: dict[str, Any],
    candidate: dict[str, Any],
    protocol: dict[str, Any],
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    journal: Any,
    runner: Callable[..., str],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    parse_judgment_fn: Callable[[str, str], dict[str, Any]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
    max_attempts: int,
) -> dict[str, Any]:
    prompt, mapping = judgment_prompt_fn(
        case["prompt"], reference["text"], candidate["text"], order
    )
    prompt = _support_judge_prompt(prompt, case["category"], protocol)

    def parse(raw: str) -> dict[str, Any]:
        parsed = validate_parsed_judgment(
            parse_judgment_fn(raw, case["category"]),
            protocol,
            category=case["category"],
        )
        parsed["normalized_winner"] = normalize_winner_fn(parsed["winner"], mapping)
        parsed["order"] = order
        if case["category"] == protocol["support_safety"]["category"]:
            parsed["support_safety"] = normalize_support_safety(parsed, mapping)
            del parsed["support_safety_by_answer"]
        return parsed

    logical_id = f"workflow:judge:{case['id']}:{arm}:{order}"
    mapping_sha256 = canonical_sha256(mapping)
    call = invoke_provider_audited(
        journal=journal,
        logical_call_id=logical_id,
        phase="judgment",
        prompt=prompt,
        spec=specs["judge"],
        provider_metadata=providers["judge"],
        context={
            "case_id": case["id"],
            "category": case["category"],
            "arm": arm,
            "order": order,
            "reference_output_sha256": reference["output_sha256"],
            "candidate_output_sha256": candidate["output_sha256"],
            "mapping_sha256": mapping_sha256,
        },
        runner=runner,
        parser=parse,
        max_attempts=max_attempts,
    )
    parsed = call["parsed"]
    return {
        **parsed,
        "logical_call_id": logical_id,
        "case_id": case["id"],
        "category": case["category"],
        "arm": arm,
        "order": order,
        "prompt_sha256": sha256_text(prompt),
        "mapping_sha256": mapping_sha256,
        "output_sha256": sha256_text(call["output"]),
        "parsed_result_sha256": canonical_parsed_result_hash(parsed),
        "completed_attempt_id": call["finished"]["attempt_id"],
        "provider_spec_sha256": providers["judge"]["provider_spec_sha256"],
        "reference_output_sha256": reference["output_sha256"],
        "candidate_output_sha256": candidate["output_sha256"],
        "aliased": False,
    }


def _workflow_summary(
    *,
    cases: list[dict[str, Any]],
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    state: dict[str, Any],
    manifest: dict[str, Any],
    journal: Any,
    run_dir: Path,
    run_dir_fd: int,
) -> dict[str, Any]:
    arms = ("raw_writer", "reviewed_workflow")
    orders = tuple(protocol["orders"])
    dimensions = tuple(protocol["dimensions"])
    deliverable = sum(
        item.get("state") == "FINAL" for item in state["workflow_cases"].values()
    )
    scores: dict[str, dict[str, float]] = {arm: {} for arm in arms}
    dimension_scores: dict[str, dict[str, dict[str, float]]] = {
        arm: {dimension: {} for dimension in dimensions} for arm in arms
    }
    order_rows: list[bool] = []
    paired_rows = []
    for case in cases:
        row_scores: dict[str, float | None] = {}
        for arm in arms:
            rows = [
                state["judgments"].get(f"{case['id']}::{arm}::{order}")
                for order in orders
            ]
            if any(row is None for row in rows):
                row_scores[arm] = None
                continue
            actual = [row for row in rows if row is not None]
            score = _mean(
                [_candidate_points(row["normalized_winner"], protocol) for row in actual]
            )
            scores[arm][case["id"]] = score
            row_scores[arm] = _round(score)
            order_rows.append(len({row["normalized_winner"] for row in actual}) == 1)
            for dimension in dimensions:
                dimension_scores[arm][dimension][case["id"]] = _mean(
                    [_dimension_points(row, dimension, protocol) for row in actual]
                )
        paired_rows.append({"case_id": case["id"], **row_scores})
    complete = [
        case["id"]
        for case in cases
        if case["id"] in scores["raw_writer"]
        and case["id"] in scores["reviewed_workflow"]
    ]
    overall_effect = (
        _mean(
            [
                scores["reviewed_workflow"][case_id]
                - scores["raw_writer"][case_id]
                for case_id in complete
            ]
        )
        if complete else float("-inf")
    )
    dimension_effects = {
        dimension: (
            _mean(
                [
                    dimension_scores["reviewed_workflow"][dimension][case_id]
                    - dimension_scores["raw_writer"][dimension][case_id]
                    for case_id in complete
                ]
            ) if complete else float("-inf")
        )
        for dimension in dimensions
    }
    joint = sum(
        scores["reviewed_workflow"][case_id]
        >= scores["raw_writer"][case_id] - 1e-12
        for case_id in complete
    )
    order_consistency = _mean(order_rows) if order_rows else 0.0
    support_records = [
        state["judgments"].get(f"{case['id']}::reviewed_workflow::{order}")
        for case in cases
        if case["category"] == protocol["support_safety"]["category"]
        for order in orders
    ]
    support_pass = bool(support_records) and all(
        row is not None and all(row["support_safety"]["candidate"].values())
        for row in support_records
    )
    acceptance = protocol["acceptance"]
    checks = {
        "deliverable_10_of_10": deliverable == acceptance["deliverable_cases"],
        "workflow_overall_effect": overall_effect
        >= acceptance["workflow_overall_effect"] - 1e-12,
        "workflow_naturalness_effect": dimension_effects["naturalness"]
        >= acceptance["workflow_naturalness_effect"] - 1e-12,
        "guard_dimensions": all(
            dimension_effects[name]
            >= acceptance["guard_dimensions_minimum"] - 1e-12
            for name in acceptance["guard_dimensions"]
        ),
        "joint_nonregression": joint >= acceptance["joint_nonregressing_cases"],
        "order_consistency": order_consistency
        >= acceptance["overall_order_consistency"] - 1e-12,
        "reviewed_support_safety": support_pass,
    }
    records = journal.records()
    logical_calls = {
        row["logical_call_id"]
        for row in records if row["event"] == "attempt_started"
    }
    successful = {
        row["logical_call_id"]
        for row in records
        if row["event"] == "attempt_finished" and row["status"] == "success"
    }
    if len(logical_calls) > protocol["max_logical_calls"]:
        raise ValueError("workflow logical call budget exceeded")
    aliases = sum(row.get("aliased") is True for row in state["judgments"].values())
    case_states = []
    for case in cases:
        item = state["workflow_cases"][case["id"]]
        artifacts = item["artifacts"]
        final = artifacts.get(item.get("final_alias", ""))
        case_states.append(
            {
                "case_id": case["id"],
                "state": item["state"],
                "rewrite_count": item["rewrite_count"],
                "final_alias": item["final_alias"],
                "reason_code": item["reason_code"],
                "reference_sha256": artifacts["R"]["output_sha256"],
                "reference_size_bytes": artifacts["R"]["output_size_bytes"],
                "a0_sha256": artifacts["A0"]["output_sha256"],
                "a0_size_bytes": artifacts["A0"]["output_size_bytes"],
                "final_sha256": final["output_sha256"] if final else None,
                "final_size_bytes": final["output_size_bytes"] if final else None,
            }
        )
    checkpoint_path = run_dir / "checkpoint.json"
    calls_path = run_dir / "calls.jsonl"
    return {
        "schema_version": SCHEMA,
        "scope": "fresh dedicated dev workflow evaluation",
        "fingerprint": manifest["fingerprint"],
        "counts": {
            "cases": len(cases),
            "deliverable": deliverable,
            "semantic_rewrites": sum(
                item["rewrite_count"] for item in state["workflow_cases"].values()
            ),
            "logical_provider_calls": len(logical_calls),
            "judgments": len(state["judgments"]),
            "aliased_judgments": aliases,
        },
        "case_states": case_states,
        "scores": {
            "paired_cases": paired_rows,
            "workflow_overall_effect": _round(overall_effect) if complete else None,
            "dimension_effects": {
                name: _round(value) if complete else None
                for name, value in dimension_effects.items()
            },
            "joint_nonregressing_cases": joint,
            "overall_order_consistency": _round(order_consistency),
        },
        "gates": {**checks, "accepted": all(checks.values())},
        "provenance": {
            "run_id": manifest["run_id"],
            "run_mode": manifest["run_mode"],
            "protocol_sha256": manifest["fingerprint_inputs"]["protocol_sha256"],
            "provider_spec_sha256": {
                role: metadata["provider_spec_sha256"]
                for role, metadata in sorted(providers.items())
            },
            "manifest_sha256": paired._sha256_secure_artifact(
                run_dir / "manifest.json", run_dir_fd=run_dir_fd
            ),
            "checkpoint_sha256": paired._sha256_secure_artifact(
                checkpoint_path, run_dir_fd=run_dir_fd
            ),
            "checkpoint_size_bytes": checkpoint_path.stat().st_size,
            "calls_sha256": paired._sha256_secure_artifact(
                calls_path, run_dir_fd=run_dir_fd
            ),
            "calls_size_bytes": calls_path.stat().st_size,
            "successful_logical_call_ids_sha256": canonical_sha256(sorted(successful)),
        },
    }


def _persist_checkpoint(path: Path, state: dict[str, Any], *, run_dir_fd: int) -> None:
    save_secure_json(path, state, run_dir_fd=run_dir_fd)


def _run_workflow_evaluation_unlocked(
    *,
    run_dir: Path,
    run_dir_fd: int,
    run_id: str,
    cases: list[dict[str, Any]],
    cases_path: Path,
    protocol: dict[str, Any],
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    runner: Callable[..., str],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    parse_judgment_fn: Callable[[str, str], dict[str, Any]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
    max_attempts: int,
) -> dict[str, Any]:
    paired._validate_cases(cases)
    if set(specs) != {"reference", "candidate", "reviewer", "judge"}:
        raise ValueError("workflow requires four explicit provider roles")
    if set(providers) != set(specs):
        raise ValueError("workflow provider metadata mismatch")
    if protocol != load_workflow_protocol():
        raise ValueError("workflow protocol is not the tracked definition")
    validate_workflow_provider_protocol(specs, providers, protocol)
    if max_attempts != protocol["retry_policy"]["max_attempts_per_logical_call"]:
        raise ValueError("workflow transport retry bound mismatch")
    fingerprint_inputs = build_workflow_fingerprint_inputs(
        cases=cases,
        cases_path=cases_path,
        protocol=protocol,
        providers=providers,
        judgment_prompt_fn=judgment_prompt_fn,
    )
    manifest = _prepare_workflow_run(
        run_dir,
        run_dir_fd=run_dir_fd,
        fingerprint_inputs=fingerprint_inputs,
        run_id=run_id,
    )
    checkpoint_path = run_dir / "checkpoint.json"
    journal = AttemptJournal(
        run_dir / "calls.jsonl",
        fingerprint=manifest["fingerprint"],
        lifetime_attempt_budget=protocol["retry_policy"]["lifetime_attempt_budget"],
        run_dir_fd=run_dir_fd,
    )
    state = _load_workflow_checkpoint(
        checkpoint_path, manifest["fingerprint"], run_dir_fd=run_dir_fd
    )
    validate_workflow_checkpoint(
        state=state,
        cases=cases,
        protocol=protocol,
        providers=providers,
        journal_records=journal.records(),
        judgment_prompt_fn=judgment_prompt_fn,
    )

    def persist() -> None:
        _persist_checkpoint(checkpoint_path, state, run_dir_fd=run_dir_fd)

    for case in cases:
        item = state["workflow_cases"].setdefault(
            case["id"],
            {
                "state": "R_READY",
                "rewrite_count": 0,
                "artifacts": {},
                "final_alias": None,
                "reason_code": None,
                "review_exhaustion": None,
            },
        )
        artifacts = item["artifacts"]
        if "R" not in artifacts:
            artifacts["R"] = _workflow_record(
                logical_call_id=f"workflow:gen:{case['id']}:R",
                phase="generation",
                prompt=case["prompt"],
                role="reference",
                specs=specs,
                providers=providers,
                journal=journal,
                runner=runner,
                context=_artifact_context(case, "R", artifacts),
                max_attempts=max_attempts,
            )
            persist()
        if "A0" not in artifacts:
            prompt = compose_write_prompt(case["prompt"])
            artifacts["A0"] = _workflow_record(
                logical_call_id=f"workflow:gen:{case['id']}:A0",
                phase="generation",
                prompt=prompt,
                role="candidate",
                specs=specs,
                providers=providers,
                journal=journal,
                runner=runner,
                context=_artifact_context(case, "A0", artifacts),
                max_attempts=max_attempts,
            )
            item["state"] = "A0"
            persist()
        if item["state"] == "NON_DELIVERABLE" and item["review_exhaustion"]:
            continue
        if "REVIEW0" not in artifacts:
            prompt = compose_review_prompt(case["prompt"], artifacts["A0"]["text"])
            logical_id = f"workflow:review:{case['id']}:0"
            try:
                artifacts["REVIEW0"] = _workflow_record(
                    logical_call_id=logical_id,
                    phase="review",
                    prompt=prompt,
                    role="reviewer",
                    specs=specs,
                    providers=providers,
                    journal=journal,
                    runner=runner,
                    context=_artifact_context(case, "REVIEW0", artifacts),
                    max_attempts=max_attempts,
                    parser=lambda raw, category=case["category"]: parse_workflow_review(
                        raw, category=category
                    ),
                )
            except RuntimeError:
                exhaustion = _review_contract_exhaustion(
                    journal.records(),
                    logical_call_id=logical_id,
                    stage="REVIEW0",
                    max_attempts=max_attempts,
                )
                if exhaustion is None:
                    raise
                item["state"] = "NON_DELIVERABLE"
                item["reason_code"] = "review_contract_exhausted"
                item["review_exhaustion"] = exhaustion
                persist()
                continue
            item["state"] = "REVIEW0"
            persist()
        review0 = artifacts["REVIEW0"]["parsed"]
        if review0["approved"]:
            item["state"] = "FINAL"
            item["final_alias"] = "A0"
            persist()
        else:
            if "A1" not in artifacts:
                if item["rewrite_count"] != 0:
                    raise ValueError("workflow semantic rewrite bound exceeded")
                prompt = compose_repair_prompt(
                    case["prompt"],
                    artifacts["A0"]["text"],
                    review0,
                    category=case["category"],
                )
                artifacts["A1"] = _workflow_record(
                    logical_call_id=f"workflow:gen:{case['id']}:A1",
                    phase="generation",
                    prompt=prompt,
                    role="candidate",
                    specs=specs,
                    providers=providers,
                    journal=journal,
                    runner=runner,
                    context=_artifact_context(case, "A1", artifacts),
                    max_attempts=max_attempts,
                )
                item["rewrite_count"] = 1
                item["state"] = "A1"
                persist()
            if "REVIEW1" not in artifacts:
                prompt = compose_review_prompt(
                    case["prompt"], artifacts["A1"]["text"]
                )
                logical_id = f"workflow:review:{case['id']}:1"
                try:
                    artifacts["REVIEW1"] = _workflow_record(
                        logical_call_id=logical_id,
                        phase="review",
                        prompt=prompt,
                        role="reviewer",
                        specs=specs,
                        providers=providers,
                        journal=journal,
                        runner=runner,
                        context=_artifact_context(case, "REVIEW1", artifacts),
                        max_attempts=max_attempts,
                        parser=lambda raw, category=case["category"]: parse_workflow_review(
                            raw, category=category
                        ),
                    )
                except RuntimeError:
                    exhaustion = _review_contract_exhaustion(
                        journal.records(),
                        logical_call_id=logical_id,
                        stage="REVIEW1",
                        max_attempts=max_attempts,
                    )
                    if exhaustion is None:
                        raise
                    item["state"] = "NON_DELIVERABLE"
                    item["reason_code"] = "review_contract_exhausted"
                    item["review_exhaustion"] = exhaustion
                    persist()
                    continue
                item["state"] = "REVIEW1"
                persist()
            if artifacts["REVIEW1"]["parsed"]["approved"]:
                item["state"] = "FINAL"
                item["final_alias"] = "A1"
            else:
                item["state"] = "NON_DELIVERABLE"
                item["final_alias"] = None
            persist()

    for case in cases:
        item = state["workflow_cases"][case["id"]]
        artifacts = item["artifacts"]
        for order in protocol["orders"]:
            raw_key = f"{case['id']}::raw_writer::{order}"
            if raw_key not in state["judgments"]:
                state["judgments"][raw_key] = _workflow_judgment(
                    case=case,
                    arm="raw_writer",
                    order=order,
                    reference=artifacts["R"],
                    candidate=artifacts["A0"],
                    protocol=protocol,
                    specs=specs,
                    providers=providers,
                    journal=journal,
                    runner=runner,
                    judgment_prompt_fn=judgment_prompt_fn,
                    parse_judgment_fn=parse_judgment_fn,
                    normalize_winner_fn=normalize_winner_fn,
                    max_attempts=max_attempts,
                )
                persist()
            if item["state"] != "FINAL":
                continue
            reviewed_key = f"{case['id']}::reviewed_workflow::{order}"
            if reviewed_key in state["judgments"]:
                continue
            final = artifacts[item["final_alias"]]
            if final["output_sha256"] == artifacts["A0"]["output_sha256"]:
                aliased = dict(state["judgments"][raw_key])
                aliased.update(
                    {
                        "logical_call_id": (
                            f"workflow:alias:{case['id']}:reviewed_workflow:{order}"
                        ),
                        "arm": "reviewed_workflow",
                        "candidate_output_sha256": final["output_sha256"],
                        "aliased": True,
                        "alias_of": raw_key,
                    }
                )
                state["judgments"][reviewed_key] = aliased
            else:
                state["judgments"][reviewed_key] = _workflow_judgment(
                    case=case,
                    arm="reviewed_workflow",
                    order=order,
                    reference=artifacts["R"],
                    candidate=final,
                    protocol=protocol,
                    specs=specs,
                    providers=providers,
                    journal=journal,
                    runner=runner,
                    judgment_prompt_fn=judgment_prompt_fn,
                    parse_judgment_fn=parse_judgment_fn,
                    normalize_winner_fn=normalize_winner_fn,
                    max_attempts=max_attempts,
                )
            persist()
    validate_workflow_checkpoint(
        state=state,
        cases=cases,
        protocol=protocol,
        providers=providers,
        journal_records=journal.records(),
        judgment_prompt_fn=judgment_prompt_fn,
    )
    result = _workflow_summary(
        cases=cases,
        protocol=protocol,
        providers=providers,
        state=state,
        manifest=manifest,
        journal=journal,
        run_dir=run_dir,
        run_dir_fd=run_dir_fd,
    )
    save_secure_json(run_dir / "result.json", result, run_dir_fd=run_dir_fd)
    return result


def run_workflow_evaluation(*, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    """Run one resumable workflow evaluation under a whole-run process lock."""
    with RunLock(run_dir) as lock:
        return _run_workflow_evaluation_unlocked(
            run_dir=Path(os.path.abspath(run_dir)),
            run_dir_fd=lock.dir_descriptor,
            **kwargs,
        )


def validate_trusted_executable_pins(
    raw_pins: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Seal four explicit absolute executable/interpreter pin chains."""
    roles = ("reference", "candidate", "reviewer", "judge")
    if tuple(raw_pins) != roles:
        raise ValueError("workflow pins must cover four ordered provider roles")
    validated: dict[str, dict[str, Any]] = {}
    owned_descriptors: list[int] = []
    try:
        for role in roles:
            raw = raw_pins[role]
            trusted_path = Path(raw["path"])
            digest = str(raw["sha256"]).lower()
            executable, executable_fd = paired._open_pinned_executable(
                trusted_path, digest, role=role, kind="executable"
            )
            owned_descriptors.append(executable_fd)
            prefix = os.pread(executable_fd, 4096, 0)
            interpreter_args: list[str] = []
            chain = [
                paired._launcher_entry(
                    "executable", trusted_path, executable, digest
                )
            ]
            descriptors = [executable_fd]
            if prefix.startswith(b"#!"):
                interpreter_name, interpreter_args = paired._parse_shebang(prefix, role)
                if raw.get("interpreter_path") is None or raw.get("interpreter_sha256") is None:
                    raise ValueError(f"trusted interpreter pin is required for {role}")
                interpreter_path = Path(raw["interpreter_path"])
                interpreter_digest = str(raw["interpreter_sha256"]).lower()
                if interpreter_path.name != interpreter_name:
                    raise ValueError(f"trusted interpreter basename mismatch for {role}")
                interpreter, interpreter_fd = paired._open_pinned_executable(
                    interpreter_path,
                    interpreter_digest,
                    role=role,
                    kind="interpreter",
                )
                owned_descriptors.append(interpreter_fd)
                if os.pread(interpreter_fd, 2, 0) == b"#!":
                    raise ValueError(f"nested script interpreter is unsupported for {role}")
                chain.insert(
                    0,
                    paired._launcher_entry(
                        "interpreter",
                        interpreter_path,
                        interpreter,
                        interpreter_digest,
                    ),
                )
                descriptors.insert(0, interpreter_fd)
            elif raw.get("interpreter_path") is not None or raw.get(
                "interpreter_sha256"
            ) is not None:
                raise ValueError(
                    f"native executable must not have an interpreter pin for {role}"
                )
            validated[role] = {
                "trusted_executable_path": str(trusted_path),
                "resolved_executable_path": str(executable),
                "executable_sha256": digest,
                "launcher_chain": chain,
                "launcher_fds": descriptors,
                "interpreter_args": interpreter_args,
            }
        return validated
    except Exception:
        for descriptor in owned_descriptors:
            os.close(descriptor)
        raise


def _load_workflow_provider_bundle(
    config_path: Path,
    parity: Any,
    trusted_pins: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    roles = ("reference", "candidate", "reviewer", "judge")
    if tuple(raw) != roles:
        raise ValueError("workflow config must contain four ordered provider roles")
    unresolved = {
        role: parity.ProviderSpec.from_dict(role, raw[role]) for role in roles
    }
    expected_identities = {
        "reference": "gpt-5.6-sol",
        "candidate": "claude-sonnet-5",
        "reviewer": "claude-opus-5",
        "judge": "gpt-5.5",
    }
    expected_families = {
        "reference": "codex",
        "candidate": "claude",
        "reviewer": "claude",
        "judge": "codex",
    }
    if (
        {role: spec.identity for role, spec in unresolved.items()}
        != expected_identities
        or len({spec.identity.casefold() for spec in unresolved.values()}) != 4
        or any(
            raw[role].get("model") != expected_identities[role]
            or raw[role].get("provider") != expected_families[role]
            for role in roles
        )
    ):
        raise ValueError("workflow requires four exact provider/model identities")
    audit_roles = {
        "reference": "reference",
        "candidate": "candidate",
        "reviewer": "judge",
        "judge": "reference",
    }
    specs = {
        role: paired.pin_provider_spec(
            spec, audit_roles[role], trusted_pins[role]
        )
        for role, spec in unresolved.items()
    }
    providers = {
        role: paired.provider_audit_metadata(specs[role], raw[role])
        for role in specs
    }
    return specs, providers


def _actual_provider_contract(
    spec: Any, metadata: dict[str, Any]
) -> dict[str, Any]:
    return {
        "provider": metadata["provider"],
        "model": metadata["requested_model"],
        "executable": Path(spec.configured_argv[0]).name,
        "argv": list(spec.configured_argv),
        "configured_input_mode": spec.input_mode,
        "runtime_prompt_transport": "stdin",
        "output_mode": spec.output_mode,
        "cwd_mode": spec.cwd_mode,
        "timeout_sec": spec.timeout_sec,
        "env_keys": [key for key, _value in spec.env],
        "environment_allowlist": list(
            paired.PROVIDER_ENV_ALLOWLISTS[spec.audit_role]
        ),
        "fixed_path": paired.FIXED_PROVIDER_PATH,
    }


def validate_workflow_provider_protocol(
    specs: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
) -> None:
    roles = {"reference", "candidate", "reviewer", "judge"}
    if (
        set(specs) != roles
        or set(providers) != roles
        or len({metadata.get("provider_spec_sha256")
                for metadata in providers.values()}) != 4
    ):
        raise ValueError("workflow provider roles mismatch")
    expected_identities = {
        role: contract["model"]
        for role, contract in protocol["provider_contracts"].items()
    }
    if (
        {role: spec.identity for role, spec in specs.items()}
        != expected_identities
        or len({spec.identity.casefold() for spec in specs.values()}) != 4
    ):
        raise ValueError("workflow provider identities must be exact and distinct")
    for role, contract in protocol["provider_contracts"].items():
        if _actual_provider_contract(specs[role], providers[role]) != contract:
            raise ValueError(f"workflow provider protocol mismatch for {role}")
    descriptor_sets = [set(spec.launcher_fds) for spec in specs.values()]
    if any(not descriptors for descriptors in descriptor_sets) or any(
        left & right
        for index, left in enumerate(descriptor_sets)
        for right in descriptor_sets[index + 1 :]
    ):
        raise ValueError("workflow roles must use separately sealed launchers")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fresh Japanese-writing dev workflow evaluation",
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    for role in ("reference", "candidate", "reviewer", "judge"):
        parser.add_argument(f"--{role}-executable", type=Path, required=True)
        parser.add_argument(f"--{role}-executable-sha256", required=True)
        parser.add_argument(f"--{role}-interpreter", type=Path)
        parser.add_argument(f"--{role}-interpreter-sha256")
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roles = ("reference", "candidate", "reviewer", "judge")
    trusted_pins = validate_trusted_executable_pins(
        {
            role: {
                "path": getattr(args, f"{role}_executable"),
                "sha256": getattr(args, f"{role}_executable_sha256"),
                "interpreter_path": getattr(args, f"{role}_interpreter"),
                "interpreter_sha256": getattr(args, f"{role}_interpreter_sha256"),
            }
            for role in roles
        }
    )
    try:
        protocol = load_workflow_protocol()
        cases = load_dev_cases(DEV_CASES)
        parity = paired._load_parity()
        specs, providers = _load_workflow_provider_bundle(
            args.config, parity, trusted_pins
        )
        validate_workflow_provider_protocol(specs, providers, protocol)
        fingerprint_inputs = build_workflow_fingerprint_inputs(
            cases=cases,
            cases_path=DEV_CASES,
            protocol=protocol,
            providers=providers,
            judgment_prompt_fn=parity.judgment_prompt,
        )
        fingerprint = canonical_sha256(fingerprint_inputs)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "scope": "fresh-dev-workflow",
                        "cases": len(cases),
                        "arms": protocol["arms"],
                        "fingerprint": fingerprint,
                        "logical_call_graph": protocol["logical_call_graph"],
                        "semantic_rewrite_max": protocol["semantic_rewrite_max"],
                        "max_logical_calls": protocol["max_logical_calls"],
                        "protocol_sha256": sha256_file(PROTOCOL_PATH),
                        "providers": {
                            role: {
                                "provider_spec_sha256": metadata["provider_spec_sha256"],
                                "executable_sha256": metadata["executable_sha256"],
                                "launcher_chain": metadata["launcher_chain"],
                            }
                            for role, metadata in providers.items()
                        },
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            return 0
        result = run_workflow_evaluation(
            run_dir=args.run_dir,
            run_id=args.run_id or uuid.uuid4().hex,
            cases=cases,
            cases_path=DEV_CASES,
            protocol=protocol,
            specs=specs,
            providers=providers,
            runner=secure_run_provider,
            judgment_prompt_fn=parity.judgment_prompt,
            parse_judgment_fn=lambda raw, category: parse_raw_judgment_then_normalize(
                raw, parity, protocol, category=category
            ),
            normalize_winner_fn=parity.normalized_winner,
            max_attempts=protocol["retry_policy"]["max_attempts_per_logical_call"],
        )
        print(
            json.dumps(
                {
                    "result_sha256": sha256_file(args.run_dir / "result.json"),
                    "fingerprint": result["fingerprint"],
                    "counts": result["counts"],
                    "accepted": result["gates"]["accepted"],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    finally:
        for pin in trusted_pins.values():
            for descriptor in pin["launcher_fds"]:
                os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
