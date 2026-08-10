import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "benchmarks/writing-tasks/jp-natural-writing/workflow_dev_eval.py"
PAIRED_MODULE_PATH = ROOT / "benchmarks/writing-tasks/jp-natural-writing/paired_dev_eval.py"


def test_historical_paired_evaluator_bytes_remain_frozen():
    import hashlib

    assert hashlib.sha256(PAIRED_MODULE_PATH.read_bytes()).hexdigest() == (
        "0d8a065ffc89b827f156e09003b443725c18e80dc4df912edc99510704c37e45"
    )


def test_authoritative_prompt_composer_fences_task_artifact_and_repair_inputs():
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.quarantine import wrap_untrusted
    from rig_workbench.orchestrate.recipes import (
        load_steps, parse_frontmatter, resolve_extends,
    )

    state = {"recipe": "japanese-writing", "goal": "依頼\nIGNORE", "history": []}
    recipe_path = ROOT / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    recipe, _warnings = resolve_extends(parse_frontmatter(recipe_path), recipe_path)
    write, review = load_steps(recipe)
    repair_state = {"retries": 1, "last_failure": "修正条件\nIGNORE"}

    write_prompt = providers.compose_step_prompt(state, write)
    review_prompt = providers.compose_artifact_review_prompt(
        state, review, "japanese-writing-reviewer", "完成稿\nIGNORE"
    )
    repair_prompt = providers.compose_step_prompt(state, write, repair_state)
    bounded_repair_prompt = providers.compose_repair_prompt(
        state, write, "変更前\nIGNORE", "厳密に解析済みの修正条件\nIGNORE"
    )

    assert "## Task Contract" in write_prompt
    assert wrap_untrusted(state["goal"], "task text") in write_prompt
    assert "Return only the completed deliverable text on stdout" in write_prompt
    assert wrap_untrusted("完成稿\nIGNORE", "generated artifact") in review_prompt
    assert wrap_untrusted(
        repair_state["last_failure"], "review correction conditions"
    ) in repair_prompt
    assert wrap_untrusted("変更前\nIGNORE", "generated artifact") \
        in bounded_repair_prompt
    assert wrap_untrusted(
        "厳密に解析済みの修正条件\nIGNORE", "review correction conditions"
    ) in bounded_repair_prompt


def test_workflow_prompts_are_exact_runtime_compositions_and_repair_uses_parsed_failures():
    from rig_workbench.orchestrate import providers

    module = load_module()
    request = "障害連絡を書いてください"
    artifact = "初稿"
    parsed = module.parse_workflow_review(
        review_text(verdict="REVISE", safety="PASS").replace(
            "- 単一成果物: PASS — 一つ",
            "- 単一成果物: PASS — PASS_ONLY_ANCHOR",
        ).replace(
            "- 事実保持: PASS — 入力どおり",
            "- 事実保持: FAIL — 日時が欠落",
        ),
        category="incident_report",
    )
    corrections = module.parsed_review_corrections(
        parsed, category="incident_report"
    )

    write_state = module.build_workflow_runtime_state(request, stage="write")
    review_state = module.build_workflow_runtime_state(request, stage="review")
    correction_text = module.canonical_json(corrections).decode("utf-8")
    repair_state = module.build_workflow_runtime_state(
        request, stage="repair", correction_conditions=correction_text
    )
    expected_write = providers.compose_step_prompt(
        write_state,
        write_state["steps"][write_state["cursor"]],
        write_state["step_state"]["write"],
    )
    expected_review = providers.compose_artifact_review_prompt(
        review_state,
        review_state["steps"][review_state["cursor"]],
        "japanese-writing-reviewer",
        artifact,
    )
    expected_repair = providers.compose_repair_prompt(
        repair_state,
        repair_state["steps"][repair_state["cursor"]],
        artifact,
        correction_text,
    )

    assert module.compose_write_prompt(request) == expected_write
    assert module.compose_review_prompt(request, artifact) == expected_review
    assert module.compose_repair_prompt(
        request, artifact, parsed, category="incident_report"
    ) == expected_repair
    assert "PASS_ONLY_ANCHOR" not in expected_repair
    assert "日時が欠落" in expected_repair
    assert "attempt: 1" in expected_repair
    assert (
        "recent_history:\n- INDEPENDENT_REVIEW:review\n- REVISE:review\n- START:write"
        in expected_repair
    )
    assert module.sha256_text(module.compose_write_prompt(request)) \
        == module.sha256_text(expected_write)


def test_workflow_a0_prompt_equals_runtime_after_real_start_transition():
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.runstate import compute_next, new_state

    module = load_module()
    request = "利用者向けのお知らせを書く"
    write, review = module.load_workflow_steps()
    state = new_state("japanese-writing", [write, review], request)
    action, _message = compute_next(state)
    assert action == "START"
    expected = providers.compose_step_prompt(
        state, state["steps"][0], state["step_state"]["write"]
    )

    actual = module.compose_write_prompt(request)
    assert actual == expected
    assert module.sha256_text(actual) == module.sha256_text(expected)
    assert "attempt: 1" in actual
    assert "recent_history:\n- START:write" in actual


def load_module():
    spec = importlib.util.spec_from_file_location("paired_workflow_eval", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_text(*, verdict="APPROVE", safety="N/A", duplicate=""):
    repair = "なし" if verdict == "APPROVE" else "事実保持を修正する"
    return (
        "対象形式: plain-text\n"
        "検査:\n"
        "- 単一成果物: PASS — 一つ\n"
        "- 形式: PASS — 指定どおり\n"
        "- 事実保持: PASS — 入力どおり\n"
        "- 推測なし: PASS — 追加なし\n"
        "- 日本語: PASS — 自然\n"
        "- 秘密情報: N/A — 該当なし\n"
        f"- 障害・サポート安全性: {safety} — 該当確認\n"
        f"{duplicate}"
        "修正条件:\n"
        f"- {repair}\n"
        f"判定: {verdict}"
    )


def test_workflow_review_contract_requires_unique_complete_approval_rows():
    module = load_module()

    approved = module.parse_workflow_review(review_text(), category="business_chat")
    assert approved["approved"] is True
    assert approved["verdict"] == "APPROVE"
    revised = module.parse_workflow_review(
        review_text(verdict="REVISE").replace(
            "- 事実保持: PASS", "- 事実保持: FAIL"
        ),
        category="business_chat",
    )
    assert revised["approved"] is False
    assert revised["repair_conditions"] == ["事実保持を修正する"]

    for colon in (":", "："):
        for equivalent_spacing in (" ", "\u3000", "\u00a0"):
            delimiter_normalized = module.parse_workflow_review(
                review_text(verdict="REVISE").replace(
                    "- 推測なし: PASS", "- 推測なし: FAIL"
                ).replace(
                    "判定: REVISE", f"判定{colon}{equivalent_spacing}REVISE"
                ),
                category="business_chat",
            )
            assert delimiter_normalized["verdict"] == "REVISE"
            assert delimiter_normalized["approved"] is False

    for malformed in (
        review_text(verdict="UNVERIFIED"),
        review_text(verdict="UNVERIFIED").replace(
            "判定: UNVERIFIED", "判定：\u3000UNVERIFIED"
        ),
        review_text(verdict="REVISE").replace(
            "- 事実保持: PASS", "- 事実保持: FAIL"
        ).replace("判定: REVISE", "判定： REVIEW"),
        review_text(verdict="REVISE").replace(
            "- 事実保持: PASS", "- 事実保持: FAIL"
        ).replace("判定: REVISE", "判定： ＲＥＶＩＳＥ"),
        review_text(verdict="REVISE").replace(
            "- 事実保持: PASS", "- 事実保持: FAIL"
        ) + "\n補足",
        review_text().replace("- 事実保持: PASS", "- 事実保持: UNKNOWN"),
        review_text(verdict="REVISE").replace(
            "- 単一成果物: PASS", "- 単一成果物: N/A"
        ),
        review_text(verdict="REVISE").replace(
            "- 形式: PASS", "- 形式: N/A"
        ),
        review_text(duplicate="- 日本語: PASS — 重複\n"),
        review_text().replace("- 形式: PASS — 指定どおり\n", ""),
    ):
        with pytest.raises(ValueError, match="review contract"):
            module.parse_workflow_review(malformed, category="business_chat")

    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(review_text(safety="N/A"), category="support_reply")
    assert module.parse_workflow_review(
        review_text(safety="PASS"), category="incident_report"
    )["approved"] is True
    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(
            review_text().replace("一つ", "x" * 501), category="business_chat"
        )
    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(
            review_text(verdict="REVISE").replace(
                "- 事実保持: PASS", "- 事実保持: FAIL"
            ).replace(
                "- 事実保持を修正する",
                "\n".join(f"- repair-{index}" for index in range(8)),
            ),
            category="business_chat",
        )


def test_workflow_protocol_is_separate_and_preregisters_exact_bounds():
    module = load_module()
    workflow = module.load_workflow_protocol()
    historical = module.paired.load_protocol()

    assert module.PAIRED_PATH != module.PROTOCOL_PATH
    assert list(historical["arms"]) == [
        "base_writer", "framework", "language", "combined",
    ]
    assert workflow["arms"] == ["raw_writer", "reviewed_workflow"]
    assert workflow["semantic_rewrite_max"] == 1
    assert workflow["semantics_version"] == 2
    assert workflow["review_exhaustion"] == {
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
    assert workflow["logical_call_graph"]["if_review0_contract_exhausted"] == [
        "JUDGE_RAW_REFERENCE_FIRST", "JUDGE_RAW_CANDIDATE_FIRST",
    ]
    assert workflow["logical_call_graph"]["if_review1_contract_exhausted"] == [
        "JUDGE_RAW_REFERENCE_FIRST", "JUDGE_RAW_CANDIDATE_FIRST",
    ]
    assert workflow["max_logical_calls"] == 90
    assert set(workflow["provider_contracts"]) == {
        "reference", "candidate", "reviewer", "judge",
    }
    assert workflow["provider_contracts"]["reviewer"] \
        == workflow["provider_contracts"]["reference"]
    assert workflow["review_contract"]["bounds"] == {
        "max_output_bytes": 16384,
        "max_target_format_codepoints": 80,
        "max_anchor_codepoints": 500,
        "max_repair_conditions": 7,
        "max_repair_codepoints": 500,
    }
    assert workflow["acceptance"] == {
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


def test_workflow_protocol_rejects_every_preregistered_contract_mutation(tmp_path):
    module = load_module()
    pristine = module.load_workflow_protocol()
    mutations = [
        (None, "semantics_version", 1),
        ("scoring", "candidate", 0.9),
        ("dimensions", 4, "verbosity"),
        ("acceptance", "guard_dimensions", ["correctness", "tone"]),
        ("acceptance", "workflow_overall_effect", 0.09),
        ("retry_policy", "max_attempts_per_logical_call", 4),
        ("support_safety", "category", "incident_report"),
        ("support_safety", "fields", ["masking"]),
        ("provider_roles", "reviewer", {"provider": "codex", "model": "other"}),
        ("provider_contracts", "reviewer", {"argv": ["codex"]}),
        ("review_contract", "parser_version", 1),
        ("review_exhaustion", "otherwise", "continue"),
        (
            "logical_call_graph",
            "if_review0_contract_exhausted",
            ["JUDGE_REVIEWED_REFERENCE_FIRST"],
        ),
        ("logical_call_graph", "second_nonapproval", "FINAL"),
        (None, "max_logical_calls", 91),
    ]
    for index, (section, key, value) in enumerate(mutations):
        mutated = json.loads(json.dumps(pristine))
        target = mutated if section is None else mutated[section]
        target[key] = value
        path = tmp_path / f"protocol-{index}.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        with pytest.raises(ValueError, match="workflow dev"):
            module.load_workflow_protocol(path)


def _workflow_fixture(
    module, tmp_path, *, review0="APPROVE", review1="APPROVE",
    mid_run_protocol_path=None, normalize_equivalent_verdict_delimiter=False,
    review0_invalid_cases=(), review1_invalid_cases=(), invalid_review_verdict="UNVERIFIED",
    review0_error_cases=(), review0_mixed_cases=(),
):
    module.paired.time.sleep = lambda _seconds: None
    cases_path = tmp_path / "parity_cases.dev.json"
    cases_path.write_text(json.dumps({"cases": [
        {
            "id": f"case-{index}",
            "split": "dev",
            "category": "support_reply" if index == 9 else "synthetic",
            "prompt": f"request-{index}",
        }
        for index in range(10)
    ]}), encoding="utf-8")
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_workflow_protocol()

    class Spec:
        def __init__(self, role):
            self.role = role

    specs = {role: Spec(role) for role in ("reference", "candidate", "reviewer", "judge")}
    providers = {
        role: {
            "provider": f"fake-{role}",
            "requested_model": f"model-{role}",
            "reported_model": f"model-{role}",
            "provider_spec_sha256": role[0] * 64,
        }
        for role in specs
    }
    calls = []
    protocol_swapped = False
    review0_attempts = {}

    def runner(spec, prompt, _attempts):
        nonlocal protocol_swapped
        if mid_run_protocol_path is not None and not protocol_swapped:
            module.PROTOCOL_PATH = mid_run_protocol_path
            protocol_swapped = True
        calls.append((spec.role, prompt))
        if spec.role == "reference":
            return "reference:" + module.sha256_text(prompt)[:8]
        if spec.role == "candidate":
            prefix = "revised-draft:" if "## Artifact to repair" in prompt else "draft:"
            return prefix + module.sha256_text(prompt)[:8]
        if spec.role == "reviewer":
            is_rereview = "revised-draft:" in prompt
            verdict = review1 if is_rereview else review0
            matching_case = next(
                (
                    index for index in range(10)
                    if f"request-{index}" in prompt
                ),
                None,
            )
            if not is_rereview and matching_case is not None:
                review0_attempts[matching_case] = review0_attempts.get(matching_case, 0) + 1
                if matching_case in review0_error_cases:
                    raise TimeoutError("synthetic transport timeout")
                if (
                    matching_case in review0_mixed_cases
                    and review0_attempts[matching_case] > 1
                ):
                    raise TimeoutError("synthetic mixed transport timeout")
            if not is_rereview and any(
                f"request-{index}" in prompt for index in review0_invalid_cases
            ):
                verdict = invalid_review_verdict
            if (
                not is_rereview
                and matching_case in review0_mixed_cases
                and review0_attempts[matching_case] == 1
            ):
                verdict = "UNVERIFIED"
            if is_rereview and any(
                f"request-{index}" in prompt for index in review1_invalid_cases
            ):
                verdict = "UNVERIFIED"
            safety = "PASS" if "request-9" in prompt else "N/A"
            review = review_text(verdict=verdict, safety=safety).replace(
                "- 事実保持: PASS", "- 事実保持: FAIL"
            ) if verdict == "REVISE" else review_text(verdict=verdict, safety=safety)
            if normalize_equivalent_verdict_delimiter:
                review = review.replace(f"判定: {verdict}", f"判定： {verdict}")
            return review
        order = "reference_first" if "ORDER: reference_first" in prompt else "candidate_first"
        winner = "B" if order == "reference_first" else "A"
        payload = {
            "winner": winner,
            "confidence": 1.0,
            "dimensions": {name: winner for name in protocol["dimensions"]},
            "reason": "owner-only-reason",
        }
        if "CATEGORY: support_reply" in prompt:
            payload["support_safety"] = {
                answer: {field: True for field in module.SUPPORT_SAFETY_FIELDS}
                for answer in ("A", "B")
            }
        return json.dumps(payload)

    def judgment_prompt(request, reference, candidate, order):
        mapping = (
            {"A": "reference", "B": "candidate", "draw": "draw"}
            if order == "reference_first"
            else {"A": "candidate", "B": "reference", "draw": "draw"}
        )
        return (
            f"ORDER: {order}\nREQUEST_HASH: {module.sha256_text(request)}\n"
            f"R: {reference}\nC: {candidate}",
            mapping,
        )

    result = module.run_workflow_evaluation(
        run_dir=tmp_path / "run",
        run_id="workflow-test",
        cases=cases,
        cases_path=cases_path,
        protocol=protocol,
        specs=specs,
        providers=providers,
        runner=runner,
        judgment_prompt_fn=judgment_prompt,
        parse_judgment_fn=lambda raw, category: module.parse_raw_judgment_then_normalize(
            raw, SimpleParity(), protocol, category=category
        ),
        normalize_winner_fn=lambda winner, mapping: mapping[winner],
        max_attempts=3,
    )
    return result, calls, tmp_path / "run"


def test_workflow_fingerprint_binds_sources_cases_prompts_graph_and_provider_pins(
    tmp_path, monkeypatch,
):
    module = load_module()
    cases_path = tmp_path / "parity_cases.dev.json"
    cases_path.write_text(json.dumps({"cases": [
        {
            "id": f"case-{index}", "split": "dev", "category": "synthetic",
            "prompt": f"request-{index}",
        }
        for index in range(10)
    ]}), encoding="utf-8")
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_workflow_protocol()
    providers = {
        role: {
            "provider": role,
            "requested_model": f"model-{role}",
            "provider_spec_sha256": role[0] * 64,
            "executable_sha256": role[-1] * 64,
            "launcher_chain": [{"sha256": role[-1] * 64}],
        }
        for role in ("reference", "candidate", "reviewer", "judge")
    }

    def judgment_prompt(request, reference, candidate, order):
        return (
            f"{order}\n{request}\n{reference}\n{candidate}",
            {"A": "reference", "B": "candidate", "draw": "draw"},
        )

    inputs = module.build_workflow_fingerprint_inputs(
        cases=cases, cases_path=cases_path, protocol=protocol,
        providers=providers, judgment_prompt_fn=judgment_prompt,
    )
    assert set(inputs["source_sha256"]) == {
        "workflow_adapter", "frozen_paired_evaluator", "parity_adapter",
        "runtime_prompt_composer",
    }
    assert inputs["cases_file_sha256"] == module.sha256_file(cases_path)
    assert len(inputs["cases"]) == 10
    assert inputs["logical_call_graph"] == protocol["logical_call_graph"]
    original = module.canonical_sha256(inputs)

    changed_providers = json.loads(json.dumps(providers))
    changed_providers["reviewer"]["provider_spec_sha256"] = "0" * 64
    changed = module.build_workflow_fingerprint_inputs(
        cases=cases, cases_path=cases_path, protocol=protocol,
        providers=changed_providers, judgment_prompt_fn=judgment_prompt,
    )
    assert module.canonical_sha256(changed) != original

    real_review = module.compose_review_prompt
    monkeypatch.setattr(
        module, "compose_review_prompt",
        lambda request, artifact: real_review(request, artifact) + "\nchanged",
    )
    prompt_changed = module.build_workflow_fingerprint_inputs(
        cases=cases, cases_path=cases_path, protocol=protocol,
        providers=providers, judgment_prompt_fn=judgment_prompt,
    )
    assert module.canonical_sha256(prompt_changed) != original


class SimpleParity:
    @staticmethod
    def parse_judgment(raw):
        return json.loads(raw)


def test_workflow_approve_aliases_a0_without_rewrite_and_keeps_public_result_raw_free(tmp_path):
    module = load_module()
    result, calls, run_dir = _workflow_fixture(module, tmp_path)

    assert result["counts"] == {
        "cases": 10,
        "deliverable": 10,
        "semantic_rewrites": 0,
        "logical_provider_calls": 50,
        "judgments": 40,
        "aliased_judgments": 20,
    }
    assert all(row["state"] == "FINAL" and row["final_alias"] == "A0"
               for row in result["case_states"])
    assert sum(role == "candidate" for role, _prompt in calls) == 10
    assert sum(role == "reference" for role, _prompt in calls) == 10
    assert sum(role == "reviewer" for role, _prompt in calls) == 10
    assert sum(role == "judge" for role, _prompt in calls) == 20
    reviewer_prompts = [prompt for role, prompt in calls if role == "reviewer"]
    for prompt in reviewer_prompts:
        for forbidden in (
            "reference answer", "candidate answer", "answer a", "answer b",
            "claude", "codex", "gpt",
        ):
            assert forbidden not in prompt.lower()
    public = json.dumps(result, ensure_ascii=False)
    for raw in ("draft:", "reference:", "owner-only-reason", "対象形式:"):
        assert raw not in public
    assert set(result["provenance"]["provider_spec_sha256"]) == {
        "reference", "candidate", "reviewer", "judge",
    }
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    for artifact in run_dir.iterdir():
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_result_protocol_provenance_uses_manifest_binding_after_midrun_path_swap(tmp_path):
    module = load_module()
    swapped = tmp_path / "swapped-protocol.json"
    swapped.write_text("{}", encoding="utf-8")
    result, _calls, run_dir = _workflow_fixture(
        module, tmp_path, mid_run_protocol_path=swapped
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())

    assert result["provenance"]["protocol_sha256"] \
        == manifest["fingerprint_inputs"]["protocol_sha256"]
    assert result["provenance"]["protocol_sha256"] != module.sha256_file(swapped)


def test_workflow_revise_allows_exactly_one_rewrite_and_fresh_rereview(tmp_path):
    module = load_module()
    result, calls, _run_dir = _workflow_fixture(
        module, tmp_path, review0="REVISE", review1="APPROVE"
    )

    assert result["counts"]["semantic_rewrites"] == 10
    assert result["counts"]["logical_provider_calls"] == 90
    assert result["counts"]["aliased_judgments"] == 0
    assert all(row["state"] == "FINAL" and row["final_alias"] == "A1"
               for row in result["case_states"])
    assert sum(role == "candidate" for role, _prompt in calls) == 20
    assert sum(role == "reviewer" for role, _prompt in calls) == 20
    repair_prompts = [prompt for role, prompt in calls
                      if role == "candidate" and "## Artifact to repair" in prompt]
    assert len(repair_prompts) == 10
    assert all("## Task Contract" in prompt and "Artifact to repair" in prompt
               for prompt in repair_prompts)

    journal = [
        json.loads(line)
        for line in (tmp_path / "run" / "calls.jsonl").read_text().splitlines()
    ]
    review_finishes = [
        row for row in journal
        if row.get("event") == "attempt_finished"
        and row.get("logical_call_id", "").startswith("workflow:review:")
    ]
    assert len(review_finishes) == 20
    assert all(
        row["status"] == "success" and row["parse_status"] == "valid"
        for row in review_finishes
    )


def test_equivalent_japanese_verdict_delimiter_is_a_valid_revise_semantic_result(
    tmp_path,
):
    module = load_module()
    result, calls, run_dir = _workflow_fixture(
        module,
        tmp_path,
        review0="REVISE",
        review1="APPROVE",
        normalize_equivalent_verdict_delimiter=True,
    )

    assert result["counts"]["semantic_rewrites"] == 10
    assert all(row["state"] == "FINAL" and row["final_alias"] == "A1"
               for row in result["case_states"])
    assert sum(role == "reviewer" for role, _prompt in calls) == 20
    journal = [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]
    review_finishes = [
        row for row in journal
        if row.get("event") == "attempt_finished"
        and row.get("logical_call_id", "").startswith("workflow:review:")
    ]
    assert len(review_finishes) == 20
    assert all(row["status"] == "success" for row in review_finishes)


def test_review0_invalid_exhaustion_is_terminal_and_other_cases_continue(tmp_path):
    module = load_module()
    result, calls, run_dir = _workflow_fixture(
        module, tmp_path, review0_invalid_cases={0}
    )

    case0 = next(row for row in result["case_states"] if row["case_id"] == "case-0")
    assert result["schema_version"] == 5
    assert case0["state"] == "NON_DELIVERABLE"
    assert case0["reason_code"] == "review_contract_exhausted"
    assert case0["rewrite_count"] == 0
    assert result["counts"] == {
        "cases": 10,
        "deliverable": 9,
        "semantic_rewrites": 0,
        "logical_provider_calls": 50,
        "judgments": 38,
        "aliased_judgments": 18,
    }
    assert result["gates"]["deliverable_10_of_10"] is False
    assert result["gates"]["accepted"] is False
    assert sum(role == "reviewer" for role, _prompt in calls) == 12
    assert sum(role == "judge" for role, _prompt in calls) == 20

    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    assert checkpoint["schema"] == 5
    contained = checkpoint["workflow_cases"]["case-0"]["review_exhaustion"]
    assert contained["reason_code"] == "review_contract_exhausted"
    assert contained["stage"] == "REVIEW0"
    assert len(contained["attempts"]) == 3
    assert all(set(attempt) == {"attempt_id", "output_sha256"}
               for attempt in contained["attempts"])
    assert sum("::raw_writer::" in key for key in checkpoint["judgments"]) == 20
    assert sum("::reviewed_workflow::" in key for key in checkpoint["judgments"]) == 18
    public = json.dumps(result, ensure_ascii=False)
    assert "対象形式:" not in public
    assert "UNVERIFIED" not in public
    assert all(attempt["attempt_id"] not in public
               and attempt["output_sha256"] not in public
               for attempt in contained["attempts"])


def test_review1_invalid_exhaustion_preserves_rewrite_and_resume_is_sealed(tmp_path):
    module = load_module()
    result, calls, run_dir = _workflow_fixture(
        module,
        tmp_path,
        review0="REVISE",
        review1="APPROVE",
        review1_invalid_cases={0},
    )

    case0 = next(row for row in result["case_states"] if row["case_id"] == "case-0")
    assert case0["state"] == "NON_DELIVERABLE"
    assert case0["reason_code"] == "review_contract_exhausted"
    assert case0["rewrite_count"] == 1
    assert result["counts"] == {
        "cases": 10,
        "deliverable": 9,
        "semantic_rewrites": 10,
        "logical_provider_calls": 88,
        "judgments": 38,
        "aliased_judgments": 0,
    }
    assert sum(role == "reviewer" for role, _prompt in calls) == 22
    assert sum(role == "judge" for role, _prompt in calls) == 38
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    contained = checkpoint["workflow_cases"]["case-0"]["review_exhaustion"]
    assert contained["stage"] == "REVIEW1"
    assert len(contained["attempts"]) == 3
    assert sum("::raw_writer::" in key for key in checkpoint["judgments"]) == 20
    assert sum("::reviewed_workflow::" in key for key in checkpoint["judgments"]) == 18

    resumed, resumed_calls, _same_run_dir = _workflow_fixture(
        module,
        tmp_path,
        review0="REVISE",
        review1="APPROVE",
        review1_invalid_cases={0},
    )
    assert resumed["fingerprint"] == result["fingerprint"]
    assert resumed_calls == []


def test_review_exhaustion_resume_rejects_a_later_success_for_same_call(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(
        module, tmp_path, review0_invalid_cases={0}
    )
    calls_path = run_dir / "calls.jsonl"
    rows = [json.loads(line) for line in calls_path.read_text().splitlines()]
    logical_id = "workflow:review:case-0:0"
    old_start = next(
        row for row in rows
        if row.get("event") == "attempt_started"
        and row.get("logical_call_id") == logical_id
    )
    old_finish = next(
        row for row in rows
        if row.get("event") == "attempt_finished"
        and row.get("logical_call_id") == logical_id
    )
    replacement_id = "e" * 32
    new_start = {
        **old_start,
        "sequence": rows[-1]["sequence"] + 1,
        "attempt_id": replacement_id,
        "attempt_no": 4,
        "recorded_ns": rows[-1]["recorded_ns"] + 1,
    }
    new_finish = {
        **old_finish,
        "sequence": rows[-1]["sequence"] + 2,
        "attempt_id": replacement_id,
        "attempt_no": 4,
        "recorded_ns": rows[-1]["recorded_ns"] + 2,
        "status": "success",
        "parse_status": "valid",
        "error_type": None,
        "parsed_result_sha256": "d" * 64,
    }
    calls_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True)
                  for row in [*rows, new_start, new_finish]) + "\n",
        encoding="utf-8",
    )
    calls_path.chmod(0o600)

    with pytest.raises(ValueError, match="review exhaustion binding mismatch"):
        _workflow_fixture(module, tmp_path, review0_invalid_cases={0})


def test_workflow_second_reject_is_nondeliverable(
    tmp_path,
):
    module = load_module()
    result, _calls, _run_dir = _workflow_fixture(
        module, tmp_path, review0="REVISE", review1="REVISE"
    )

    assert result["counts"]["deliverable"] == 0
    assert result["counts"]["semantic_rewrites"] == 10
    assert result["counts"]["logical_provider_calls"] == 70
    assert result["counts"]["judgments"] == 20
    assert all(row["state"] == "NON_DELIVERABLE" and row["final_alias"] is None
               for row in result["case_states"])
    assert result["gates"]["deliverable_10_of_10"] is False
    assert result["gates"]["accepted"] is False


@pytest.mark.parametrize("invalid_verdict", ["MALFORMED", "UNVERIFIED"])
def test_malformed_review_is_journal_invalid_and_never_consumes_semantic_rewrite(
    tmp_path, invalid_verdict,
):
    module = load_module()
    result, _calls, _run_dir = _workflow_fixture(
        module,
        tmp_path,
        review0_invalid_cases={0},
        invalid_review_verdict=invalid_verdict,
    )

    run_dir = tmp_path / "run"
    journal = [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]
    review_finishes = [
        row for row in journal
        if row.get("event") == "attempt_finished"
        and row.get("logical_call_id") == "workflow:review:case-0:0"
    ]
    assert len(review_finishes) == 3
    assert all(row["status"] == "invalid" for row in review_finishes)
    state = json.loads((run_dir / "checkpoint.json").read_text())
    assert state["workflow_cases"]["case-0"]["rewrite_count"] == 0
    assert "REVIEW0" not in state["workflow_cases"]["case-0"]["artifacts"]
    assert state["workflow_cases"]["case-0"]["reason_code"] \
        == "review_contract_exhausted"
    assert result["counts"]["deliverable"] == 9


@pytest.mark.parametrize("mode", ["transport", "mixed"])
def test_review_exhaustion_with_transport_error_still_aborts(tmp_path, mode):
    module = load_module()
    options = (
        {"review0_error_cases": {0}}
        if mode == "transport"
        else {"review0_mixed_cases": {0}}
    )
    with pytest.raises(RuntimeError, match="provider failed after 3 attempts"):
        _workflow_fixture(module, tmp_path, **options)

    state = json.loads((tmp_path / "run" / "checkpoint.json").read_text())
    assert state["workflow_cases"]["case-0"]["review_exhaustion"] is None


def test_workflow_resume_makes_no_duplicate_calls_and_tamper_fails_closed(tmp_path):
    module = load_module()
    first, first_calls, run_dir = _workflow_fixture(module, tmp_path)
    resumed, resumed_calls, _same_dir = _workflow_fixture(module, tmp_path)

    assert first["fingerprint"] == resumed["fingerprint"]
    assert len(first_calls) == 50
    assert resumed_calls == []

    checkpoint = run_dir / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    state["workflow_cases"]["case-0"]["artifacts"]["A0"]["text"] += "tampered"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)
    with pytest.raises(ValueError, match="artifact binding mismatch"):
        _workflow_fixture(module, tmp_path)


def test_workflow_checkpoint_cannot_rehash_away_journal_tamper(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(module, tmp_path)
    checkpoint = run_dir / "checkpoint.json"
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    record = state["workflow_cases"]["case-0"]["artifacts"]["A0"]
    record["text"] += "tampered"
    record["output_sha256"] = module.sha256_text(record["text"])
    record["output_size_bytes"] = len(record["text"].encode())
    for order in ("reference_first", "candidate_first"):
        state["judgments"][
            f"case-0::raw_writer::{order}"
        ]["candidate_output_sha256"] = record["output_sha256"]
        state["judgments"][
            f"case-0::reviewed_workflow::{order}"
        ]["candidate_output_sha256"] = record["output_sha256"]
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)

    with pytest.raises(ValueError, match="journal binding"):
        _workflow_fixture(module, tmp_path)


def test_workflow_checkpoint_binds_terminal_state_and_aliased_judgments(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(module, tmp_path)
    checkpoint = run_dir / "checkpoint.json"
    pristine = json.loads(checkpoint.read_text(encoding="utf-8"))

    state = json.loads(json.dumps(pristine))
    state["workflow_cases"]["case-0"]["final_alias"] = "A1"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)
    with pytest.raises(ValueError, match="terminal state"):
        _workflow_fixture(module, tmp_path)

    checkpoint.write_text(json.dumps(pristine), encoding="utf-8")
    checkpoint.chmod(0o600)
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    alias = state["judgments"][
        "case-0::reviewed_workflow::reference_first"
    ]
    alias["logical_call_id"] = "workflow:alias:tampered"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)
    with pytest.raises(ValueError, match="alias binding"):
        _workflow_fixture(module, tmp_path)

    checkpoint.write_text(json.dumps(pristine), encoding="utf-8")
    checkpoint.chmod(0o600)
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    state["workflow_cases"]["case-0"]["artifacts"]["A0"]["role"] = "reference"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)
    with pytest.raises(ValueError, match="artifact binding"):
        _workflow_fixture(module, tmp_path)

    checkpoint.write_text(json.dumps(pristine), encoding="utf-8")
    checkpoint.chmod(0o600)
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    for arm in ("raw_writer", "reviewed_workflow"):
        state["judgments"][
            f"case-0::{arm}::reference_first"
        ]["normalized_winner"] = "reference"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    checkpoint.chmod(0o600)
    with pytest.raises(ValueError, match="judgment binding mismatch"):
        _workflow_fixture(module, tmp_path)


def test_workflow_resume_rejects_orphan_journal_success(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(module, tmp_path)
    calls_path = run_dir / "calls.jsonl"
    rows = [json.loads(line) for line in calls_path.read_text().splitlines()]
    start, finish = dict(rows[-2]), dict(rows[-1])
    orphan_id = "f" * 32
    for offset, row in enumerate((start, finish), 1):
        row["sequence"] = rows[-1]["sequence"] + offset
        row["attempt_id"] = orphan_id
        row["attempt_no"] = 2
        row["recorded_ns"] += offset
    calls_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in [*rows, start, finish]) + "\n",
        encoding="utf-8",
    )
    calls_path.chmod(0o600)

    with pytest.raises(ValueError, match="orphan or multiply referenced success"):
        _workflow_fixture(module, tmp_path)


def test_workflow_resume_recomputes_prompts_context_mappings_and_dependencies(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(module, tmp_path)
    checkpoint_path = run_dir / "checkpoint.json"
    calls_path = run_dir / "calls.jsonl"
    pristine_state = json.loads(checkpoint_path.read_text())
    pristine_calls = [json.loads(line) for line in calls_path.read_text().splitlines()]

    def write(state, rows):
        checkpoint_path.write_text(json.dumps(state), encoding="utf-8")
        checkpoint_path.chmod(0o600)
        calls_path.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        calls_path.chmod(0o600)

    state = json.loads(json.dumps(pristine_state))
    rows = json.loads(json.dumps(pristine_calls))
    next(
        row for row in rows
        if row.get("event") == "attempt_started"
        and row.get("logical_call_id") == "workflow:review:case-0:0"
    )["candidate_output_sha256"] = "0" * 64
    write(state, rows)
    with pytest.raises(ValueError, match="journal binding mismatch"):
        _workflow_fixture(module, tmp_path)

    state = json.loads(json.dumps(pristine_state))
    rows = json.loads(json.dumps(pristine_calls))
    state["workflow_cases"]["case-0"]["artifacts"]["A0"]["prompt_sha256"] = "0" * 64
    next(
        row for row in rows
        if row.get("event") == "attempt_started"
        and row.get("logical_call_id") == "workflow:gen:case-0:A0"
    )["prompt_sha256"] = "0" * 64
    write(state, rows)
    with pytest.raises(ValueError, match="artifact binding mismatch"):
        _workflow_fixture(module, tmp_path)

    state = json.loads(json.dumps(pristine_state))
    rows = json.loads(json.dumps(pristine_calls))
    for arm in ("raw_writer", "reviewed_workflow"):
        state["judgments"][
            f"case-0::{arm}::reference_first"
        ]["mapping_sha256"] = "0" * 64
    next(
        row for row in rows
        if row.get("event") == "attempt_started"
        and row.get("logical_call_id")
        == "workflow:judge:case-0:raw_writer:reference_first"
    )["mapping_sha256"] = "0" * 64
    write(state, rows)
    with pytest.raises(ValueError, match="judgment binding mismatch"):
        _workflow_fixture(module, tmp_path)


def test_workflow_cli_dry_run_requires_four_separate_pins_without_calls(
    tmp_path, capsys,
):
    module = load_module()
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    for path in (codex, claude):
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        path.chmod(0o700)
    interpreter = Path("/bin/sh")
    interpreter_hash = module.sha256_file(interpreter.resolve())

    args = [
        "--dry-run",
        "--run-dir", str(tmp_path / "run"),
    ]
    for role, executable in (
        ("reference", codex), ("candidate", claude),
        ("reviewer", codex), ("judge", claude),
    ):
        args.extend([
            f"--{role}-executable", str(executable.resolve()),
            f"--{role}-executable-sha256", module.sha256_file(executable),
            f"--{role}-interpreter", str(interpreter),
            f"--{role}-interpreter-sha256", interpreter_hash,
        ])

    module.paired.load_protocol = lambda: (_ for _ in ()).throw(
        AssertionError("workflow mode must not reinterpret the historical protocol")
    )
    assert module.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scope"] == "fresh-dev-workflow"
    assert report["max_logical_calls"] == 90
    assert len(report["fingerprint"]) == 64
    assert set(report["providers"]) == {"reference", "candidate", "reviewer", "judge"}
    assert report["providers"]["reference"]["launcher_chain"] \
        == report["providers"]["reviewer"]["launcher_chain"]
    assert report["providers"]["reference"]["provider_spec_sha256"] \
        != report["providers"]["reviewer"]["provider_spec_sha256"]


def test_direct_workflow_cli_dry_run_works_without_pythonpath(tmp_path):
    module = load_module()
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    for path in (codex, claude):
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        path.chmod(0o700)
    interpreter = Path("/bin/sh")
    interpreter_hash = module.sha256_file(interpreter.resolve())
    argv = [
        sys.executable, str(MODULE_PATH), "--dry-run",
        "--run-dir", str(tmp_path / "run"),
    ]
    for role, executable in (
        ("reference", codex), ("candidate", claude),
        ("reviewer", codex), ("judge", claude),
    ):
        argv.extend([
            f"--{role}-executable", str(executable.resolve()),
            f"--{role}-executable-sha256", module.sha256_file(executable),
            f"--{role}-interpreter", str(interpreter),
            f"--{role}-interpreter-sha256", interpreter_hash,
        ])
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "PYTHONPATH": "",
        "LANG": "C.UTF-8",
    }

    completed = subprocess.run(
        argv, cwd=tmp_path, env=environment, text=True,
        capture_output=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["scope"] == "fresh-dev-workflow"
