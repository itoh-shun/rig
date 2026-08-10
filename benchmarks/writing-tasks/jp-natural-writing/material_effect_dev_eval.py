#!/usr/bin/env python3
"""Fresh, single-repeat dev screen for the Japanese style-material effect."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODULE_PATH = Path(__file__).resolve()
WORKFLOW_PATH = HERE / "workflow_dev_eval.py"
PAIRED_PATH = HERE / "paired_dev_eval.py"
PARITY_PATH = HERE / "parity.py"
PROTOCOL_PATH = HERE / "material_effect_dev_protocol.json"
CONFIG_PATH = HERE / "material_effect.providers.example.json"
DEV_CASES = HERE / "parity_cases.dev.json"
SCHEMA = 2

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workflow = _load_module("jp_material_workflow_adapter", WORKFLOW_PATH)
paired = workflow.paired
canonical_sha256 = paired.canonical_sha256
sha256_file = paired.sha256_file
sha256_text = paired.sha256_text
save_secure_json = paired.save_secure_json

TREATED = {
    "technical_explanation": "technical",
    "code_review": "technical",
    "casual": "conversation",
}
CALL_GRAPH = {
    "always_per_case": [
        "R", "NONE", "JUDGE_NONE_REFERENCE_FIRST", "JUDGE_NONE_CANDIDATE_FIRST",
    ],
    "treated_only": [
        "MATERIAL", "JUDGE_MATERIAL_REFERENCE_FIRST", "JUDGE_MATERIAL_CANDIDATE_FIRST",
    ],
    "untreated_only": [
        "ALIAS_MATERIAL", "ALIAS_MATERIAL_REFERENCE_FIRST", "ALIAS_MATERIAL_CANDIDATE_FIRST",
    ],
}
ACCEPTANCE = {
    "treated_naturalness_effect_minimum": 0.1,
    "pooled_overall_effect_minimum": 0.0,
    "guard_dimensions": ["correctness", "context_fit", "conciseness", "tone"],
    "guard_dimensions_minimum": -0.05,
    "overall_order_consistency": 0.8,
    "zero_workflow_meta": True,
    "safety_unchanged": True,
    "support_safety_all_true": True,
}
RELEASE_AGGREGATION = {
    "fresh_repeats": 3,
    "screen_disposition": "exploratory_not_release_evidence",
    "selection": "all_valid_runs_no_best_run_no_rescue",
    "pooling": "case_first_equal_weight",
    "treated_naturalness_effect_minimum": 0.1,
    "pooled_overall_effect_minimum": 0.0,
    "guard_dimensions": ["correctness", "context_fit", "conciseness", "tone"],
    "guard_dimensions_minimum": -0.05,
    "order_consistency_minimum": 0.8,
    "nonnegative_run_treated_effect_minimum_count": 2,
    "support_safety": "all_true_every_run",
    "zero_workflow_meta": "all_true_every_run",
    "consistent_provenance": [
        "fingerprint", "protocol_sha256", "config_sha256",
        "provider_spec_sha256", "selected_materials", "support_safety_evidence_sha256",
    ],
}
PROVIDER_CONTRACTS_SHA256 = (
    "184314dcaa387c41d2ec30637650d303c5cceedafd180b69632a20f5570316f3"
)


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if (
        set(protocol) != {
            "schema", "semantics_version", "name", "split",
            "expected_case_count", "arms", "orders", "dimensions", "scoring",
            "treated_categories", "category_profiles", "untreated_profile",
            "screen_repeats", "preregistered_later_repeats", "max_logical_calls",
            "logical_call_graph", "retry_policy", "provider_roles",
            "provider_contracts", "support_safety", "acceptance", "public_output",
            "release_aggregation",
        }
        or protocol.get("schema") != 1
        or protocol.get("semantics_version") != 2
        or protocol.get("name") != "japanese-writing-material-effect-fresh-dev-screen"
        or protocol.get("split") != "dev"
        or protocol.get("expected_case_count") != 10
        or protocol.get("arms") != ["none", "material"]
        or protocol.get("orders") != ["reference_first", "candidate_first"]
        or protocol.get("dimensions")
        != ["correctness", "naturalness", "context_fit", "conciseness", "tone"]
        or protocol.get("scoring") != {"candidate": 1.0, "draw": 0.5, "reference": 0.0}
        or protocol.get("treated_categories") != list(TREATED)
        or protocol.get("category_profiles") != TREATED
        or protocol.get("untreated_profile") != "none"
        or protocol.get("screen_repeats") != 1
        or protocol.get("preregistered_later_repeats") != 3
        or protocol.get("max_logical_calls") != 49
        or protocol.get("logical_call_graph") != CALL_GRAPH
        or protocol.get("retry_policy") != {
            "max_attempts_per_logical_call": 3,
            "backoff_seconds": [0, 2, 4],
            "lifetime_attempt_budget": 147,
        }
        or canonical_sha256(protocol.get("provider_contracts"))
        != PROVIDER_CONTRACTS_SHA256
        or protocol.get("acceptance") != ACCEPTANCE
        or protocol.get("release_aggregation") != RELEASE_AGGREGATION
        or protocol.get("support_safety") != {
            "category": "support_reply",
            "fields": list(paired.SUPPORT_SAFETY_FIELDS),
            "candidate_fields": list(paired.SUPPORT_SAFETY_FIELDS),
            "expected_case_count": 1,
            "hard_gate": "all_true_noncompensatory_both_arms_both_orders",
            "public_reporting": "aggregate_boolean_only",
            "prompt_suffix": (
                "support_reply の場合のみ、A と B それぞれについて no_file_body, "
                "no_data_rows, structure_header_only_alternative, masking, "
                "safe_alternative を厳密な boolean で support_safety に追加してください。"
            ),
        }
        or protocol.get("public_output") != "hashes_sizes_scores_only"
    ):
        raise ValueError("invalid material-effect dev protocol")
    expected_roles = {
        "reference": {"provider": "codex", "model": "gpt-5.6-sol", "sandbox": "read-only", "prompt_transport": "stdin"},
        "candidate": {"provider": "claude", "model": "claude-sonnet-5", "sandbox": "safe-mode", "prompt_transport": "stdin"},
        "judge": {"provider": "codex", "model": "gpt-5.5", "sandbox": "read-only", "prompt_transport": "stdin"},
    }
    if protocol.get("provider_roles") != expected_roles:
        raise ValueError("invalid material-effect provider roles")
    return protocol


def material_profile(category: str) -> str:
    return TREATED.get(category, "none")


def _resolve_material(profile: str) -> tuple[str | None, dict[str, object]]:
    from rig_workbench.orchestrate.providers import resolve_japanese_material

    write_step, _review_step = workflow.load_workflow_steps()
    return resolve_japanese_material(write_step, profile)


def freeze_material_supply(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve each selected profile once and bind the exact prompt-ready bytes."""
    selected: dict[str, dict[str, Any]] = {}
    for profile in sorted({material_profile(case["category"]) for case in cases}):
        text_value, metadata = _resolve_material(profile)
        encoded = b"" if text_value is None else text_value.encode("utf-8")
        selected[profile] = {
            "text": text_value,
            "prompt_sha256": sha256_text(text_value or ""),
            "size_bytes": len(encoded),
            "metadata": metadata,
        }
    return selected


def _frozen_material(
    frozen_materials: dict[str, dict[str, Any]], profile: str,
) -> dict[str, Any]:
    frozen = frozen_materials.get(profile)
    if not isinstance(frozen, dict) or set(frozen) != {
        "text", "prompt_sha256", "size_bytes", "metadata",
    }:
        raise ValueError("frozen material binding is malformed")
    text_value = frozen["text"]
    if text_value is not None and not isinstance(text_value, str):
        raise ValueError("frozen material text is malformed")
    encoded = b"" if text_value is None else text_value.encode("utf-8")
    if (
        sha256_text(text_value or "") != frozen["prompt_sha256"]
        or len(encoded) != frozen["size_bytes"]
        or (profile == "none") != (text_value is None)
    ):
        raise ValueError("frozen material hash mismatch")
    return frozen


def candidate_prompt(
    request: str, category: str, arm: str,
    *, frozen_materials: dict[str, dict[str, Any]] | None = None,
) -> str:
    if arm not in {"none", "material"}:
        raise ValueError("unknown material-effect arm")
    profile = "none" if arm == "none" else material_profile(category)
    if frozen_materials is not None:
        frozen = _frozen_material(frozen_materials, profile)
        return workflow.compose_write_prompt_with_frozen_material(
            request,
            category=category,
            material_profile=profile,
            material_text=frozen["text"],
        )
    return workflow.compose_write_prompt(
        request, category=category, material_profile=profile
    )


def _actual_provider_contract(spec: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    return workflow._actual_provider_contract(spec, metadata)


def validate_material_provider_protocol(
    specs: dict[str, Any], providers: dict[str, dict[str, Any]], protocol: dict[str, Any],
) -> None:
    roles = {"reference", "candidate", "judge"}
    if (
        set(specs) != roles
        or set(providers) != roles
        or len({row.get("provider_spec_sha256") for row in providers.values()}) != 3
    ):
        raise ValueError("material-effect provider roles mismatch")
    for role in roles:
        if _actual_provider_contract(specs[role], providers[role]) \
                != protocol["provider_contracts"][role]:
            raise ValueError(f"material-effect provider protocol mismatch for {role}")
    descriptor_sets = [set(specs[role].launcher_fds) for role in sorted(roles)]
    if any(not values for values in descriptor_sets) or any(
        left & right
        for index, left in enumerate(descriptor_sets)
        for right in descriptor_sets[index + 1:]
    ):
        raise ValueError("material-effect roles must use separately sealed launchers")


def build_fingerprint_inputs(
    *, cases: list[dict[str, Any]], cases_path: Path, protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    prompt_cache: dict[tuple[str, str], str] | None = None,
    frozen_materials: dict[str, dict[str, Any]] | None = None,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    rows = []
    prompts = {}
    for case in cases:
        profile = material_profile(case["category"])
        none = (prompt_cache or {}).get((case["id"], "none")) or candidate_prompt(
            case["prompt"], case["category"], "none",
            frozen_materials=frozen_materials,
        )
        material = (prompt_cache or {}).get((case["id"], "material")) or candidate_prompt(
            case["prompt"], case["category"], "material",
            frozen_materials=frozen_materials,
        )
        metadata = (
            _frozen_material(frozen_materials, profile)["metadata"]
            if frozen_materials is not None
            else workflow.workflow_material_metadata(case["category"])
        )
        rows.append({
            "id": case["id"], "category": case["category"],
            "case_sha256": canonical_sha256(case),
            "request_sha256": sha256_text(case["prompt"]),
            "profile": profile, "material": metadata,
        })
        judge_templates = {}
        for arm, candidate in (("none", "<NONE>"), ("material", "<MATERIAL>")):
            judge_templates[arm] = {}
            for order in protocol["orders"]:
                prompt, mapping = judgment_prompt_fn(
                    case["prompt"], "<REFERENCE>", candidate, order
                )
                judge_templates[arm][order] = {
                    "prompt_sha256": sha256_text(prompt),
                    "mapping_sha256": canonical_sha256(mapping),
                }
        prompts[case["id"]] = {
            "none_sha256": sha256_text(none),
            "material_sha256": sha256_text(material),
            "untreated_byte_identical": none == material,
            "judges": judge_templates,
        }
    return {
        "schema": 1,
        "mode": "fresh_material_effect_dev_screen",
        "source_sha256": {
            "adapter": sha256_file(MODULE_PATH),
            "workflow_adapter": sha256_file(WORKFLOW_PATH),
            "paired_evaluator": sha256_file(PAIRED_PATH),
            "parity": sha256_file(PARITY_PATH),
            "provider_composer": sha256_file(
                Path(sys.modules["rig_workbench.orchestrate.providers"].__file__).resolve()
            ),
        },
        "protocol_sha256": sha256_file(PROTOCOL_PATH),
        "config_sha256": sha256_file(config_path),
        "cases_file_sha256": sha256_file(cases_path),
        "cases": rows,
        "prompts": prompts,
        "logical_call_graph": protocol["logical_call_graph"],
        "selected_materials": {
            profile: {
                "prompt_sha256": frozen["prompt_sha256"],
                "size_bytes": frozen["size_bytes"],
                "metadata": frozen["metadata"],
            }
            for profile, frozen in sorted((frozen_materials or {}).items())
        },
        "providers": {
            role: {
                key: metadata.get(key) for key in (
                    "provider", "requested_model", "provider_spec_sha256",
                    "executable_sha256", "launcher_chain",
                )
            }
            for role, metadata in sorted(providers.items())
        },
    }


def _points(record: dict[str, Any], protocol: dict[str, Any]) -> float:
    return paired._candidate_points(record["normalized_winner"], protocol)


def _dimension(record: dict[str, Any], name: str, protocol: dict[str, Any]) -> float:
    return paired._dimension_points(record, name, protocol)


def _support_hard_gate_evidence(
    cases: list[dict[str, Any]], state: dict[str, Any], protocol: dict[str, Any],
) -> tuple[bool, str]:
    """Validate support judgment shape and return the noncompensatory candidate gate."""
    support = [
        case for case in cases
        if case["category"] == protocol["support_safety"]["category"]
    ]
    if len(support) != protocol["support_safety"]["expected_case_count"]:
        raise ValueError("material-effect support case count mismatch")
    fields = set(protocol["support_safety"]["candidate_fields"])
    all_true = True
    evidence_rows = []
    for case in support:
        item = state["cases"].get(case["id"])
        if not isinstance(item, dict):
            raise ValueError("material-effect support artifacts are missing")
        for arm in ("none", "material"):
            artifact = item["artifacts"].get(arm.upper())
            if not isinstance(artifact, dict):
                raise ValueError("material-effect support artifact is missing")
            for order in protocol["orders"]:
                key = f"{case['id']}::{arm}::{order}"
                row = state["judgments"].get(key)
                safety = row.get("support_safety") if isinstance(row, dict) else None
                if (
                    not isinstance(safety, dict)
                    or set(safety) != {"candidate", "reference"}
                    or any(not isinstance(safety[role], dict) for role in safety)
                    or any(set(safety[role]) != fields for role in safety)
                    or any(type(value) is not bool for role in safety for value in safety[role].values())
                ):
                    raise ValueError("material-effect support safety schema mismatch")
                if row.get("aliased"):
                    base_key = row.get("alias_of")
                    base = state["judgments"].get(base_key)
                    none_artifact = item["artifacts"].get("NONE")
                    if (
                        arm != "material"
                        or base_key != f"{case['id']}::none::{order}"
                        or not isinstance(base, dict)
                        or row.get("prompt_sha256") != base.get("prompt_sha256")
                        or row.get("output_sha256") != base.get("output_sha256")
                        or artifact.get("prompt_sha256") != none_artifact.get("prompt_sha256")
                        or artifact.get("output_sha256") != none_artifact.get("output_sha256")
                    ):
                        raise ValueError("material-effect support alias integrity mismatch")
                all_true = all_true and all(safety["candidate"].values())
                evidence_rows.append({
                    "arm": arm, "order": order,
                    "candidate": {field: safety["candidate"][field] for field in sorted(fields)},
                })
    evidence = {"schema": 1, "case_id": support[0]["id"], "rows": evidence_rows}
    return all_true, canonical_sha256(evidence)


def evaluate_support_hard_gate(
    cases: list[dict[str, Any]], state: dict[str, Any], protocol: dict[str, Any],
) -> bool:
    return _support_hard_gate_evidence(cases, state, protocol)[0]


def expected_all_true_support_evidence_sha256(
    case_id: str, protocol: dict[str, Any],
) -> str:
    fields = sorted(protocol["support_safety"]["candidate_fields"])
    return canonical_sha256({
        "schema": 1,
        "case_id": case_id,
        "rows": [
            {"arm": arm, "order": order,
             "candidate": {field: True for field in fields}}
            for arm in ("none", "material") for order in protocol["orders"]
        ],
    })


def _validate_checkpoint_state(
    state: dict[str, Any],
    *,
    cases: list[dict[str, Any]],
    fingerprint: str,
    frozen_materials: dict[str, dict[str, Any]],
    prompt_cache: dict[tuple[str, str], str],
    protocol: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    journal: Any,
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
) -> None:
    if (
        set(state) != {"schema", "fingerprint", "cases", "judgments"}
        or state.get("schema") != SCHEMA
        or state.get("fingerprint") != fingerprint
        or not isinstance(state.get("cases"), dict)
        or not isinstance(state.get("judgments"), dict)
    ):
        raise ValueError("material-effect checkpoint structure mismatch")
    by_id = {case["id"]: case for case in cases}
    if not set(state["cases"]).issubset(by_id):
        raise ValueError("material-effect checkpoint case mismatch")
    referenced_attempts: list[str] = []
    expected_attempt_hashes: dict[str, str] = {}
    expected_parsed_hashes: dict[str, str | None] = {}
    for case_id, item in state["cases"].items():
        case = by_id[case_id]
        profile = material_profile(case["category"])
        if (
            not isinstance(item, dict)
            or set(item) != {"profile", "material", "artifacts"}
            or item["profile"] != profile
            or item["material"] != _frozen_material(frozen_materials, profile)["metadata"]
            or not isinstance(item["artifacts"], dict)
            or not set(item["artifacts"]).issubset({"R", "NONE", "MATERIAL"})
        ):
            raise ValueError("material-effect checkpoint material binding mismatch")
        artifacts = item["artifacts"]
        for name, artifact in artifacts.items():
            if name == "MATERIAL" and profile == "none":
                expected_alias = {
                    **artifacts.get("NONE", {}), "aliased": True, "alias_of": "NONE",
                }
                if artifact != expected_alias:
                    raise ValueError("material-effect checkpoint artifact integrity mismatch")
                continue
            role = "reference" if name == "R" else "candidate"
            prompt = case["prompt"] if name == "R" else prompt_cache[(case_id, name.lower())]
            if (
                not isinstance(artifact, dict)
                or artifact.get("logical_call_id") != f"material:{case_id}:{name}"
                or artifact.get("role") != role
                or artifact.get("prompt_sha256") != sha256_text(prompt)
                or artifact.get("output_sha256") != sha256_text(str(artifact.get("text", "")))
                or artifact.get("output_size_bytes")
                != len(str(artifact.get("text", "")).encode("utf-8"))
                or artifact.get("provider_spec_sha256")
                != providers[role]["provider_spec_sha256"]
                or not isinstance(artifact.get("completed_attempt_id"), str)
            ):
                raise ValueError("material-effect checkpoint artifact integrity mismatch")
            referenced_attempts.append(artifact["completed_attempt_id"])
            expected_attempt_hashes[artifact["completed_attempt_id"]] = artifact["output_sha256"]
            expected_parsed_hashes[artifact["completed_attempt_id"]] = None
    for key, judgment in state["judgments"].items():
        try:
            case_id, arm, order = key.split("::")
        except ValueError as error:
            raise ValueError("material-effect checkpoint judgment integrity mismatch") from error
        if case_id not in by_id or arm not in {"none", "material"} or order not in protocol["orders"]:
            raise ValueError("material-effect checkpoint judgment integrity mismatch")
        case = by_id[case_id]
        item = state["cases"].get(case_id)
        if item is None:
            raise ValueError("material-effect checkpoint judgment integrity mismatch")
        if arm == "material" and item["profile"] == "none":
            base_key = f"{case_id}::none::{order}"
            expected_alias = {
                **state["judgments"].get(base_key, {}),
                "arm": "material", "aliased": True, "alias_of": base_key,
            }
            if judgment != expected_alias:
                raise ValueError("material-effect checkpoint judgment integrity mismatch")
            continue
        reference = item["artifacts"].get("R")
        candidate = item["artifacts"].get(arm.upper())
        if reference is None or candidate is None:
            raise ValueError("material-effect checkpoint judgment integrity mismatch")
        prompt, mapping = judgment_prompt_fn(
            case["prompt"], reference["text"], candidate["text"], order,
        )
        prompt = workflow._support_judge_prompt(prompt, case["category"], protocol)
        parsed = {
            key_name: judgment.get(key_name)
            for key_name in (
                "winner", "confidence", "dimensions", "reason",
                "normalized_winner", "order",
            )
        }
        if "support_safety" in judgment:
            parsed["support_safety"] = judgment["support_safety"]
        parsed_sha256 = paired.canonical_parsed_result_hash(parsed)
        confidence = judgment.get("confidence")
        if (
            judgment.get("winner") not in {"A", "B", "draw"}
            or not isinstance(judgment.get("dimensions"), dict)
            or set(judgment["dimensions"]) != set(protocol["dimensions"])
            or any(value not in {"A", "B", "draw"} for value in judgment["dimensions"].values())
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
            or not isinstance(judgment.get("reason"), str)
            or judgment.get("normalized_winner")
            != normalize_winner_fn(judgment["winner"], mapping)
            or judgment.get("parsed_result_sha256") != parsed_sha256
        ):
            raise ValueError("material-effect checkpoint parsed judgment integrity mismatch")
        if (
            not isinstance(judgment, dict)
            or judgment.get("logical_call_id")
            != f"workflow:judge:{case_id}:{arm}:{order}"
            or judgment.get("case_id") != case_id
            or judgment.get("category") != case["category"]
            or judgment.get("arm") != arm
            or judgment.get("order") != order
            or judgment.get("prompt_sha256") != sha256_text(prompt)
            or judgment.get("mapping_sha256") != canonical_sha256(mapping)
            or judgment.get("reference_output_sha256") != reference["output_sha256"]
            or judgment.get("candidate_output_sha256") != candidate["output_sha256"]
            or judgment.get("provider_spec_sha256")
            != providers["judge"]["provider_spec_sha256"]
            or not isinstance(judgment.get("completed_attempt_id"), str)
        ):
            raise ValueError("material-effect checkpoint judgment integrity mismatch")
        referenced_attempts.append(judgment["completed_attempt_id"])
        expected_attempt_hashes[judgment["completed_attempt_id"]] = judgment["output_sha256"]
        expected_parsed_hashes[judgment["completed_attempt_id"]] = parsed_sha256
    finishes = {
        row["attempt_id"]: row
        for row in journal.records()
        if row.get("event") == "attempt_finished" and row.get("status") == "success"
    }
    if (
        len(referenced_attempts) != len(set(referenced_attempts))
        or set(referenced_attempts) != set(finishes)
        or any(
            finishes[attempt_id].get("output_sha256") != expected_attempt_hashes[attempt_id]
            or finishes[attempt_id].get("parsed_result_sha256")
            != expected_parsed_hashes[attempt_id]
            for attempt_id in referenced_attempts
        )
    ):
        raise ValueError("material-effect checkpoint journal integrity mismatch")


def run_material_effect_screen(
    *, run_dir: Path, run_id: str, cases: list[dict[str, Any]], cases_path: Path,
    protocol: dict[str, Any], specs: dict[str, Any], providers: dict[str, dict[str, Any]],
    runner: Callable[..., str],
    judgment_prompt_fn: Callable[[str, str, str, str], tuple[str, dict[str, str]]],
    parse_judgment_fn: Callable[[str, str], dict[str, Any]],
    normalize_winner_fn: Callable[[str, dict[str, str]], str],
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    paired._validate_cases(cases)
    if protocol != load_protocol() or set(specs) != {"reference", "candidate", "judge"}:
        raise ValueError("material-effect screen configuration mismatch")
    validate_material_provider_protocol(specs, providers, protocol)
    with paired.RunLock(run_dir) as lock:
        run_dir = Path(os.path.abspath(run_dir))
        frozen_materials = freeze_material_supply(cases)
        prompt_cache = {
            (case["id"], arm): candidate_prompt(
                case["prompt"], case["category"], arm,
                frozen_materials=frozen_materials,
            )
            for case in cases for arm in ("none", "material")
        }
        fingerprint_inputs = build_fingerprint_inputs(
            cases=cases, cases_path=cases_path, protocol=protocol,
            providers=providers, judgment_prompt_fn=judgment_prompt_fn,
            prompt_cache=prompt_cache,
            frozen_materials=frozen_materials,
            config_path=config_path,
        )
        fingerprint = canonical_sha256(fingerprint_inputs)
        manifest_path = run_dir / "manifest.json"
        existing = [name for name in os.listdir(lock.dir_descriptor) if name != "run.lock"]
        manifest = {
            "schema": SCHEMA, "run_id": run_id,
            "run_mode": "fresh_material_effect_dev_screen",
            "fingerprint": fingerprint, "fingerprint_inputs": fingerprint_inputs,
        }
        if existing:
            prior = paired._read_secure_json_artifact(
                manifest_path, run_dir_fd=lock.dir_descriptor
            )
            if prior != manifest:
                raise ValueError("material-effect manifest fingerprint mismatch")
        else:
            paired._write_secure_json_exclusive(
                manifest_path, manifest, run_dir_fd=lock.dir_descriptor
            )
        checkpoint_path = run_dir / "checkpoint.json"
        try:
            state = paired._read_secure_json_artifact(
                checkpoint_path, run_dir_fd=lock.dir_descriptor
            )
            if state.get("fingerprint") != fingerprint:
                raise ValueError("material-effect checkpoint fingerprint mismatch")
        except ValueError:
            if checkpoint_path.name in os.listdir(lock.dir_descriptor):
                raise
            state = {"schema": SCHEMA, "fingerprint": fingerprint, "cases": {}, "judgments": {}}
        journal = paired.AttemptJournal(
            run_dir / "calls.jsonl", fingerprint=fingerprint,
            lifetime_attempt_budget=147, run_dir_fd=lock.dir_descriptor,
        )
        _validate_checkpoint_state(
            state, cases=cases, fingerprint=fingerprint,
            frozen_materials=frozen_materials, prompt_cache=prompt_cache,
            protocol=protocol, providers=providers, journal=journal,
            judgment_prompt_fn=judgment_prompt_fn,
            normalize_winner_fn=normalize_winner_fn,
        )
        persist = lambda: save_secure_json(
            checkpoint_path, state, run_dir_fd=lock.dir_descriptor
        )
        for case in cases:
            profile = material_profile(case["category"])
            item = state["cases"].setdefault(case["id"], {
                "profile": profile,
                "material": _frozen_material(frozen_materials, profile)["metadata"],
                "artifacts": {},
            })
            expected_binding = {
                "profile": profile,
                "material": _frozen_material(frozen_materials, profile)["metadata"],
            }
            if {key: item.get(key) for key in expected_binding} != expected_binding:
                raise ValueError("material-effect checkpoint material binding mismatch")
            artifacts = item["artifacts"]
            for name, role, prompt in (
                ("R", "reference", case["prompt"]),
                ("NONE", "candidate", prompt_cache[(case["id"], "none")]),
            ):
                if name not in artifacts:
                    artifacts[name] = workflow._workflow_record(
                        logical_call_id=f"material:{case['id']}:{name}", phase="generation",
                        prompt=prompt, role=role, specs=specs, providers=providers,
                        journal=journal, runner=runner,
                        context={"case_id": case["id"], "arm": name.lower()}, max_attempts=3,
                    )
                    persist()
            if profile == "none":
                artifacts["MATERIAL"] = {**artifacts["NONE"], "aliased": True, "alias_of": "NONE"}
            elif "MATERIAL" not in artifacts:
                artifacts["MATERIAL"] = workflow._workflow_record(
                    logical_call_id=f"material:{case['id']}:MATERIAL", phase="generation",
                    prompt=prompt_cache[(case["id"], "material")],
                    role="candidate", specs=specs, providers=providers, journal=journal,
                    runner=runner, context={"case_id": case["id"], "arm": "material", "material": item["material"]},
                    max_attempts=3,
                )
                persist()
            for arm in ("none", "material"):
                candidate = artifacts[arm.upper()]
                for order in protocol["orders"]:
                    key = f"{case['id']}::{arm}::{order}"
                    if key in state["judgments"]:
                        continue
                    none_key = f"{case['id']}::none::{order}"
                    if arm == "material" and profile == "none":
                        aliased = dict(state["judgments"][none_key])
                        aliased.update({"arm": "material", "aliased": True, "alias_of": none_key})
                        state["judgments"][key] = aliased
                    else:
                        state["judgments"][key] = workflow._workflow_judgment(
                            case=case, arm=arm, order=order, reference=artifacts["R"],
                            candidate=candidate, protocol=protocol, specs=specs,
                            providers=providers, journal=journal, runner=runner,
                            judgment_prompt_fn=judgment_prompt_fn,
                            parse_judgment_fn=parse_judgment_fn,
                            normalize_winner_fn=normalize_winner_fn, max_attempts=3,
                        )
                    persist()
        logical_calls = {
            row["logical_call_id"] for row in journal.records()
            if row.get("event") == "attempt_finished" and row.get("status") == "success"
        }
        if len(logical_calls) > protocol["max_logical_calls"]:
            raise ValueError("material-effect logical call ceiling exceeded")
        treated = [case for case in cases if case["category"] in TREATED]
        effects = []
        dimension_effects = {name: [] for name in protocol["dimensions"]}
        consistent = []
        case_scores = []
        for case in cases:
            arm_points = {}
            arm_dimensions: dict[str, dict[str, float]] = {}
            arm_consistency: dict[str, bool] = {}
            for arm in ("none", "material"):
                records = [state["judgments"][f"{case['id']}::{arm}::{order}"] for order in protocol["orders"]]
                arm_points[arm] = sum(_points(row, protocol) for row in records) / 2
                arm_dimensions[arm] = {
                    name: sum(_dimension(row, name, protocol) for row in records) / 2
                    for name in protocol["dimensions"]
                }
                for name in protocol["dimensions"]:
                    if case in treated:
                        dimension_effects[name].append(
                            arm_dimensions[arm][name]
                            * (1 if arm == "material" else -1)
                        )
                arm_consistency[arm] = (
                    records[0]["normalized_winner"] == records[1]["normalized_winner"]
                )
                consistent.append(arm_consistency[arm])
            overall_effect = arm_points["material"] - arm_points["none"]
            effects.append(overall_effect)
            case_scores.append({
                "case_id": case["id"],
                "category": case["category"],
                "treated": case in treated,
                "overall_effect": overall_effect,
                "dimension_effects": {
                    name: arm_dimensions["material"][name] - arm_dimensions["none"][name]
                    for name in protocol["dimensions"]
                },
                "order_consistency": sum(arm_consistency.values()) / 2,
            })
        treated_natural = sum(dimension_effects["naturalness"]) / max(1, len(treated))
        pooled = sum(effects) / len(effects)
        dims = {name: sum(values) / max(1, len(treated)) for name, values in dimension_effects.items()}
        order_consistency = sum(consistent) / len(consistent)
        meta_tokens = ("reviewer", "policy", "判定", "合否", "修正履歴")
        zero_meta = all(
            not any(token.casefold() in artifact["text"].casefold() for token in meta_tokens)
            for item in state["cases"].values()
            for name, artifact in item["artifacts"].items()
            if name in {"NONE", "MATERIAL"}
        )
        safety_unchanged = all(
            state["cases"][case["id"]]["profile"] == "none"
            for case in cases if case["category"] in {"incident_report", "support_reply"}
        )
        support_safety, support_evidence_sha256 = _support_hard_gate_evidence(
            cases, state, protocol
        )
        gates = {
            "treated_naturalness": treated_natural >= 0.1,
            "pooled_nonnegative": pooled >= 0,
            "guards": all(dims[name] >= -0.05 for name in ACCEPTANCE["guard_dimensions"]),
            "order_consistency": order_consistency >= 0.8,
            "zero_workflow_meta": zero_meta,
            "safety_unchanged": safety_unchanged,
            "support_safety": support_safety,
        }
        result = {
            "schema_version": SCHEMA, "scope": "fresh material-effect dev screen",
            "screen_disposition": "exploratory_not_release_evidence",
            "release_eligible": False,
            "fingerprint": fingerprint,
            "counts": {"cases": len(cases), "treated": len(treated), "logical_provider_calls": len(logical_calls)},
            "scores": {"treated_naturalness_effect": treated_natural, "pooled_overall_effect": pooled,
                       "treated_dimension_effects": dims, "overall_order_consistency": order_consistency},
            "gates": {**gates, "exploratory_pass": all(gates.values())},
            "case_scores": case_scores,
            "cases": [{
                "case_id": case["id"], "profile": state["cases"][case["id"]]["profile"],
                "material": state["cases"][case["id"]]["material"],
                "none_sha256": state["cases"][case["id"]]["artifacts"]["NONE"]["output_sha256"],
                "material_sha256": state["cases"][case["id"]]["artifacts"]["MATERIAL"]["output_sha256"],
            } for case in cases],
            "provenance": {"run_id": run_id, "protocol_sha256": fingerprint_inputs["protocol_sha256"],
                           "config_sha256": fingerprint_inputs["config_sha256"],
                           "selected_materials": fingerprint_inputs["selected_materials"],
                           "support_safety_evidence_sha256": support_evidence_sha256,
                           "provider_spec_sha256": {r: p["provider_spec_sha256"] for r, p in providers.items()}},
        }
        save_secure_json(run_dir / "result.json", result, run_dir_fd=lock.dir_descriptor)
        return result


def aggregate_material_effect_release(
    screens: list[dict[str, Any]], protocol: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate exactly three fresh screens without selection or rescue."""
    policy = protocol["release_aggregation"]
    if protocol != load_protocol() or len(screens) != policy["fresh_repeats"]:
        raise ValueError("release aggregation requires exactly three fresh screens")
    run_ids = [screen.get("provenance", {}).get("run_id") for screen in screens]
    if any(not isinstance(run_id, str) or not run_id for run_id in run_ids) \
            or len(set(run_ids)) != len(run_ids):
        raise ValueError("release aggregation requires exactly three fresh run ids")
    provenance_signatures = []
    case_maps = []
    run_treated_effects = []
    for screen in screens:
        if (
            screen.get("schema_version") != SCHEMA
            or screen.get("scope") != "fresh material-effect dev screen"
            or screen.get("screen_disposition") != policy["screen_disposition"]
            or screen.get("release_eligible") is not False
            or screen.get("counts", {}).get("cases") != protocol["expected_case_count"]
            or screen.get("counts", {}).get("treated") != len(TREATED)
            or screen.get("counts", {}).get("logical_provider_calls")
            != protocol["max_logical_calls"]
            or not isinstance(screen.get("case_scores"), list)
            or len(screen["case_scores"]) != protocol["expected_case_count"]
        ):
            raise ValueError("release aggregation screen is not a valid exploratory result")
        provenance = screen.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("release aggregation screen provenance is invalid")
        provenance_signatures.append({
            "fingerprint": screen.get("fingerprint"),
            "protocol_sha256": provenance.get("protocol_sha256"),
            "config_sha256": provenance.get("config_sha256"),
            "provider_spec_sha256": provenance.get("provider_spec_sha256"),
            "selected_materials": provenance.get("selected_materials"),
            "support_safety_evidence_sha256": provenance.get(
                "support_safety_evidence_sha256"
            ),
        })
        rows: dict[str, dict[str, Any]] = {}
        for row in screen["case_scores"]:
            if (
                not isinstance(row, dict)
                or set(row) != {
                    "case_id", "category", "treated", "overall_effect",
                    "dimension_effects", "order_consistency",
                }
                or not isinstance(row["case_id"], str)
                or row["case_id"] in rows
                or type(row["treated"]) is not bool
                or row["treated"] != (row["category"] in TREATED)
                or not isinstance(row["dimension_effects"], dict)
                or set(row["dimension_effects"]) != set(protocol["dimensions"])
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in [row["overall_effect"], row["order_consistency"],
                                  *row["dimension_effects"].values()]
                )
                or not 0 <= float(row["order_consistency"]) <= 1
            ):
                raise ValueError("release aggregation case score schema is invalid")
            rows[row["case_id"]] = row
        treated_rows = [row for row in rows.values() if row["treated"]]
        recomputed_treated = sum(
            float(row["dimension_effects"]["naturalness"]) for row in treated_rows
        ) / len(treated_rows)
        recomputed_pooled = sum(
            float(row["overall_effect"]) for row in rows.values()
        ) / len(rows)
        recomputed_dimensions = {
            dimension: sum(
                float(row["dimension_effects"][dimension]) for row in treated_rows
            ) / len(treated_rows)
            for dimension in protocol["dimensions"]
        }
        recomputed_order = sum(
            float(row["order_consistency"]) for row in rows.values()
        ) / len(rows)
        reported = screen.get("scores", {})
        close = lambda left, right: (
            isinstance(left, (int, float)) and not isinstance(left, bool)
            and abs(float(left) - float(right)) <= 1e-12
        )
        if (
            not close(reported.get("treated_naturalness_effect"), recomputed_treated)
            or not close(reported.get("pooled_overall_effect"), recomputed_pooled)
            or not isinstance(reported.get("treated_dimension_effects"), dict)
            or set(reported["treated_dimension_effects"]) != set(recomputed_dimensions)
            or any(
                not close(reported["treated_dimension_effects"][name], value)
                for name, value in recomputed_dimensions.items()
            )
            or not close(reported.get("overall_order_consistency"), recomputed_order)
        ):
            raise ValueError("release aggregation screen scores do not match case-first data")
        run_treated_effects.append(recomputed_treated)
        case_maps.append(rows)
    if any(signature != provenance_signatures[0] for signature in provenance_signatures[1:]):
        raise ValueError("release aggregation requires consistent provenance")
    case_ids = list(case_maps[0])
    if any(set(rows) != set(case_ids) for rows in case_maps[1:]) or any(
        (rows[case_id]["category"], rows[case_id]["treated"])
        != (case_maps[0][case_id]["category"], case_maps[0][case_id]["treated"])
        for rows in case_maps[1:]
        for case_id in case_ids
    ):
        raise ValueError("release aggregation case mappings are inconsistent")
    support_ids = [
        case_id for case_id in case_ids
        if case_maps[0][case_id]["category"] == protocol["support_safety"]["category"]
    ]
    if (
        len(support_ids) != protocol["support_safety"]["expected_case_count"]
        or provenance_signatures[0]["support_safety_evidence_sha256"]
        != expected_all_true_support_evidence_sha256(support_ids[0], protocol)
    ):
        raise ValueError("release aggregation support evidence is not all true")

    def mean(values: list[float]) -> float:
        return sum(values) / len(values)

    case_first = {
        case_id: {
            "overall_effect": mean([
                float(rows[case_id]["overall_effect"]) for rows in case_maps
            ]),
            "dimension_effects": {
                dimension: mean([
                    float(rows[case_id]["dimension_effects"][dimension])
                    for rows in case_maps
                ])
                for dimension in protocol["dimensions"]
            },
            "order_consistency": mean([
                float(rows[case_id]["order_consistency"]) for rows in case_maps
            ]),
        }
        for case_id in case_ids
    }
    treated_ids = [case_id for case_id in case_ids if case_maps[0][case_id]["treated"]]
    treated_naturalness = mean([
        case_first[case_id]["dimension_effects"]["naturalness"]
        for case_id in treated_ids
    ])
    pooled_overall = mean([
        case_first[case_id]["overall_effect"] for case_id in case_ids
    ])
    guards = {
        dimension: mean([
            case_first[case_id]["dimension_effects"][dimension]
            for case_id in treated_ids
        ])
        for dimension in policy["guard_dimensions"]
    }
    order_consistency = mean([
        case_first[case_id]["order_consistency"] for case_id in case_ids
    ])
    nonnegative_runs = sum(
        value >= 0 for value in run_treated_effects
    )
    gates = {
        "treated_naturalness": (
            treated_naturalness >= policy["treated_naturalness_effect_minimum"]
        ),
        "pooled_nonnegative": pooled_overall >= policy["pooled_overall_effect_minimum"],
        "guards": all(
            value >= policy["guard_dimensions_minimum"] for value in guards.values()
        ),
        "order_consistency": order_consistency >= policy["order_consistency_minimum"],
        "nonnegative_runs_2_of_3": (
            nonnegative_runs >= policy["nonnegative_run_treated_effect_minimum_count"]
        ),
        "support_safety": all(
            screen.get("gates", {}).get("support_safety") is True
            and screen.get("gates", {}).get("safety_unchanged") is True
            for screen in screens
        ),
        "zero_workflow_meta": all(
            screen.get("gates", {}).get("zero_workflow_meta") is True
            for screen in screens
        ),
        "consistent_provenance": True,
        "all_three_valid_included": True,
    }
    return {
        "schema_version": 1,
        "scope": "material-effect three-repeat release aggregation",
        "selection": policy["selection"],
        "pooling": policy["pooling"],
        "included_run_ids": run_ids,
        "scores": {
            "treated_naturalness_effect": treated_naturalness,
            "pooled_overall_effect": pooled_overall,
            "guard_dimension_effects": guards,
            "order_consistency": order_consistency,
            "nonnegative_run_count": nonnegative_runs,
        },
        "gates": gates,
        "release_eligible": all(gates.values()),
        "provenance_sha256": canonical_sha256(provenance_signatures[0]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fresh Japanese material-effect dev screen")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    for role in ("reference", "candidate", "judge"):
        parser.add_argument(f"--{role}-executable", type=Path, required=True)
        parser.add_argument(f"--{role}-executable-sha256", required=True)
        parser.add_argument(f"--{role}-interpreter", type=Path)
        parser.add_argument(f"--{role}-interpreter-sha256")
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pins = paired.validate_trusted_executable_pins({
        role: {"path": getattr(args, f"{role}_executable"),
               "sha256": getattr(args, f"{role}_executable_sha256"),
               "interpreter_path": getattr(args, f"{role}_interpreter"),
               "interpreter_sha256": getattr(args, f"{role}_interpreter_sha256")}
        for role in ("reference", "candidate", "judge")
    })
    specs = providers = None
    try:
        parity = paired._load_parity()
        specs, providers = paired._load_provider_bundle(args.config, parity, pins)
        # Codex judge uses the reference-family environment allowlist.
        specs["judge"] = paired.pin_provider_spec(
            parity.ProviderSpec.from_dict(
                "judge", json.loads(args.config.read_text())["judges"][0]
            ), "reference", pins["judge"],
        )
        providers["judge"] = paired.provider_audit_metadata(
            specs["judge"], json.loads(args.config.read_text())["judges"][0]
        )
        protocol = load_protocol()
        validate_material_provider_protocol(specs, providers, protocol)
        cases = paired.load_dev_cases(DEV_CASES, expected_path=DEV_CASES)
        if args.dry_run:
            frozen_materials = freeze_material_supply(cases)
            prompt_cache = {
                (case["id"], arm): candidate_prompt(
                    case["prompt"], case["category"], arm,
                    frozen_materials=frozen_materials,
                )
                for case in cases for arm in ("none", "material")
            }
            fingerprint_inputs = build_fingerprint_inputs(
                cases=cases, cases_path=DEV_CASES, protocol=protocol,
                providers=providers, judgment_prompt_fn=parity.judgment_prompt,
                prompt_cache=prompt_cache, frozen_materials=frozen_materials,
                config_path=args.config,
            )
            print(json.dumps({"mode": "fresh_material_effect_dev_screen",
                              "fingerprint": canonical_sha256(fingerprint_inputs),
                              "max_logical_calls": 49,
                              "screen_repeats": 1,
                              "later_repeats": 3,
                              "provider_spec_sha256": {r: p["provider_spec_sha256"] for r, p in providers.items()}},
                             sort_keys=True))
            return 0
        result = run_material_effect_screen(
            run_dir=args.run_dir, run_id=args.run_id or str(uuid.uuid4()),
            cases=cases, cases_path=DEV_CASES, protocol=protocol, specs=specs,
            providers=providers, runner=paired.secure_run_provider,
            judgment_prompt_fn=parity.judgment_prompt,
            parse_judgment_fn=lambda raw, category: paired.parse_raw_judgment_then_normalize(
                raw, parity, protocol, category=category
            ), normalize_winner_fn=parity.normalized_winner,
            config_path=args.config,
        )
        print(json.dumps({"fingerprint": result["fingerprint"], "counts": result["counts"],
                          "exploratory_pass": result["gates"]["exploratory_pass"],
                          "release_eligible": False}, sort_keys=True))
        return 0
    finally:
        for pin in pins.values():
            for descriptor in pin["launcher_fds"]:
                os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
