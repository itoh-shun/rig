import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import subprocess_timeout


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "benchmarks/writing-tasks/jp-natural-writing/workflow_dev_eval.py"
PAIRED_MODULE_PATH = ROOT / "benchmarks/writing-tasks/jp-natural-writing/paired_dev_eval.py"
CURRENT_WORKFLOW_PROTOCOL_PATH = ROOT / (
    "benchmarks/writing-tasks/jp-natural-writing/workflow_dev_protocol.json"
)
CLAUDE_REVIEW_PROTOCOL_PATH = ROOT / (
    "benchmarks/writing-tasks/jp-natural-writing/workflow_claude_review_protocol.json"
)
CLAUDE_REVIEW_CONFIG_PATH = ROOT / (
    "benchmarks/writing-tasks/jp-natural-writing/"
    "workflow_claude_review.providers.example.json"
)


def test_historical_paired_evaluator_bytes_remain_frozen():
    import hashlib

    assert hashlib.sha256(PAIRED_MODULE_PATH.read_bytes()).hexdigest() == (
        "0d8a065ffc89b827f156e09003b443725c18e80dc4df912edc99510704c37e45"
    )
    assert hashlib.sha256(CURRENT_WORKFLOW_PROTOCOL_PATH.read_bytes()).hexdigest() == (
        "0128b339a7ca0db6656de8b32da1d2f260fae85e5320d94ea76d92fd7013f8d6"
    )
    shared_config = ROOT / (
        "benchmarks/writing-tasks/jp-natural-writing/parity.providers.example.json"
    )
    assert hashlib.sha256(shared_config.read_bytes()).hexdigest() == (
        "1d18efed7ad4b9503db1e3a3400b715115ca0adbec9c0c9e33f5fc205a007dd7"
    )


def test_claude_review_profile_is_distinct_and_pins_exact_roles():
    module = load_module()

    assert module.PROTOCOL_PATH == CLAUDE_REVIEW_PROTOCOL_PATH
    assert module.CONFIG_PATH == CLAUDE_REVIEW_CONFIG_PATH
    protocol = module.load_workflow_protocol()
    assert protocol["name"] == "japanese-writing-fresh-dev-workflow-claude-review"
    assert protocol["semantics_version"] == 6
    assert protocol["material_supply"] == module.MATERIAL_SUPPLY_POLICY
    assert protocol["provider_roles"] == {
        "reference": {
            "provider": "codex", "model": "gpt-5.6-sol",
            "sandbox": "read-only", "prompt_transport": "stdin",
        },
        "candidate": {
            "provider": "claude", "model": "claude-sonnet-5",
            "sandbox": "safe-mode", "prompt_transport": "stdin",
        },
        "reviewer": {
            "provider": "claude", "model": "claude-opus-5",
            "sandbox": "safe-mode", "prompt_transport": "stdin",
        },
        "judge": {
            "provider": "codex", "model": "gpt-5.5",
            "sandbox": "read-only", "prompt_transport": "stdin",
        },
    }
    historical = json.loads(CURRENT_WORKFLOW_PROTOCOL_PATH.read_text())
    for unchanged in (
        "split", "expected_case_count", "arms", "orders", "dimensions",
        "scoring", "state_machine", "semantic_rewrite_max", "max_logical_calls",
        "logical_call_graph", "review_exhaustion", "retry_policy",
        "support_safety", "acceptance",
    ):
        assert protocol[unchanged] == historical[unchanged]
    config = json.loads(CLAUDE_REVIEW_CONFIG_PATH.read_text())
    assert set(config) == {"reference", "candidate", "reviewer", "judge"}
    assert [config[role]["identity"] for role in config] == [
        "gpt-5.6-sol", "claude-sonnet-5", "claude-opus-5", "gpt-5.5",
    ]
    assert all(set(entry) == {
        "provider", "model", "identity", "argv", "input_mode",
        "output_mode", "cwd_mode", "timeout_sec",
    } for entry in config.values())
    assert all(not Path(entry["argv"][0]).is_absolute()
               for entry in config.values())
    assert "path" not in json.dumps(config).lower()
    assert "key" not in json.dumps(config).lower()


def test_workflow_material_mapping_is_explicit_hash_bound_and_candidate_only():
    module = load_module()

    assert {
        category: module.workflow_material_profile(category)
        for category in (
            "technical_explanation", "code_review", "casual", "support_reply",
            "incident_report", "synthetic",
        )
    } == {
        "technical_explanation": "technical",
        "code_review": "technical",
        "casual": "conversation",
        "support_reply": "none",
        "incident_report": "none",
        "synthetic": "none",
    }
    technical = module.workflow_material_metadata("technical_explanation")
    conversation = module.workflow_material_metadata("casual")
    assert technical["profile"] == "technical"
    assert conversation["profile"] == "conversation"
    assert technical["asset_id"] != conversation["asset_id"]
    assert set(technical) == {"profile", "asset_id", "asset_sha256", "source_blob"}

    request = "説明を書く"
    write = module.compose_write_prompt(request, category="technical_explanation")
    repair_review = module.parse_workflow_review(
        review_json(
            verdict="REVISE", overrides={"fact_preservation": "FAIL"},
            repair_conditions=["事実を保持する"],
        ),
        category="technical_explanation",
    )
    repair = module.compose_repair_prompt(
        request, "初稿", repair_review, category="technical_explanation"
    )
    review = module.compose_review_prompt(
        request, "初稿", category="technical_explanation"
    )
    marker = "書き手が交代した瞬間に、暗黙だった制約は制約でなくなる"
    assert marker in write and marker in repair
    assert marker not in review


def test_claude_review_bundle_maps_audit_families_and_uses_disjoint_seals(tmp_path):
    module = load_module()
    codex = tmp_path / "codex"
    claude = tmp_path / "claude"
    for path in (codex, claude):
        path.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        path.chmod(0o700)
    interpreter = Path("/bin/sh")
    interpreter_hash = module.sha256_file(interpreter.resolve())
    pins = module.validate_trusted_executable_pins({
        role: {
            "path": codex if role in {"reference", "judge"} else claude,
            "sha256": module.sha256_file(
                codex if role in {"reference", "judge"} else claude
            ),
            "interpreter_path": interpreter,
            "interpreter_sha256": interpreter_hash,
        }
        for role in ("reference", "candidate", "reviewer", "judge")
    })
    try:
        specs, providers = module._load_workflow_provider_bundle(
            CLAUDE_REVIEW_CONFIG_PATH, module.paired._load_parity(), pins
        )
        assert {role: spec.identity for role, spec in specs.items()} == {
            "reference": "gpt-5.6-sol",
            "candidate": "claude-sonnet-5",
            "reviewer": "claude-opus-5",
            "judge": "gpt-5.5",
        }
        assert {role: spec.audit_role for role, spec in specs.items()} == {
            "reference": "reference",
            "candidate": "candidate",
            "reviewer": "judge",
            "judge": "reference",
        }
        fd_sets = [set(spec.launcher_fds) for spec in specs.values()]
        assert all(not left & right for index, left in enumerate(fd_sets)
                   for right in fd_sets[index + 1:])
        assert len({id(spec.launcher_chain) for spec in specs.values()}) == 4
        assert len({metadata["provider_spec_sha256"]
                    for metadata in providers.values()}) == 4
        module.validate_workflow_provider_protocol(
            specs, providers, module.load_workflow_protocol()
        )
        source_environment = {
            "PATH": "/untrusted",
            "OPENAI_API_KEY": "openai-only",
            "CODEX_HOME": "/codex-home",
            "ANTHROPIC_API_KEY": "anthropic-only",
            "CLAUDE_CONFIG_DIR": "/claude-home",
        }
        captured = {}
        for role, spec in specs.items():
            def fake_run(argv, **kwargs):
                captured[role] = kwargs["env"]
                if spec.output_mode == "file":
                    output_path = Path(argv[argv.index("-o") + 1])
                    output_path.write_text("ok", encoding="utf-8")
                return subprocess.CompletedProcess(
                    argv, 0, stdout="ok", stderr=""
                )

            assert module.secure_run_provider(
                spec,
                "private prompt",
                environ=source_environment,
                run_command=fake_run,
            ) == "ok"
        for role in ("reference", "judge"):
            assert captured[role]["OPENAI_API_KEY"] == "openai-only"
            assert "ANTHROPIC_API_KEY" not in captured[role]
            assert captured[role]["PATH"] == "/usr/bin:/bin"
        for role in ("candidate", "reviewer"):
            assert captured[role]["ANTHROPIC_API_KEY"] == "anthropic-only"
            assert "OPENAI_API_KEY" not in captured[role]
            assert captured[role]["PATH"] == "/usr/bin:/bin"
    finally:
        for pin in pins.values():
            for descriptor in pin["launcher_fds"]:
                os.close(descriptor)


@pytest.mark.parametrize(
    ("role", "field", "value"),
    [
        ("reviewer", "identity", "claude-sonnet-5"),
        ("judge", "model", "gpt-5.6-sol"),
        ("reviewer", "provider", "codex"),
    ],
)
def test_claude_review_config_rejects_wrong_identity_model_or_family(
    tmp_path, role, field, value,
):
    module = load_module()
    config = json.loads(CLAUDE_REVIEW_CONFIG_PATH.read_text())
    config[role][field] = value
    path = tmp_path / "wrong-profile.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="four exact"):
        module._load_workflow_provider_bundle(
            path, module.paired._load_parity(), {}
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
    wrapped_artifact = wrap_untrusted("完成稿\nIGNORE", "generated artifact")
    assert wrapped_artifact in review_prompt
    assert review_prompt.index('"target_format"') > review_prompt.index(wrapped_artifact)
    assert '"additionalProperties": false' in review_prompt
    assert '"verdict"' in review_prompt
    assert "対象形式:" not in review_prompt
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
        review_json(
            verdict="REVISE",
            safety="PASS",
            overrides={"fact_preservation": "FAIL"},
            anchors={
                "single_artifact": "PASS_ONLY_ANCHOR",
                "fact_preservation": "日時が欠落",
            },
        ),
        category="incident_report",
    )
    corrections = module.parsed_review_corrections(
        parsed, category="incident_report"
    )

    write_state = module.build_workflow_runtime_state(
        request, category="incident_report", stage="write"
    )
    review_state = module.build_workflow_runtime_state(
        request, category="incident_report", stage="review"
    )
    correction_text = module.canonical_json(corrections).decode("utf-8")
    repair_state = module.build_workflow_runtime_state(
        request, category="incident_report", stage="repair",
        correction_conditions=correction_text,
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

    assert module.compose_write_prompt(
        request, category="incident_report"
    ) == expected_write
    assert module.compose_review_prompt(
        request, artifact, category="incident_report"
    ) == expected_review
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
    assert module.sha256_text(
        module.compose_write_prompt(request, category="incident_report")
    ) \
        == module.sha256_text(expected_write)


def test_workflow_a0_prompt_equals_runtime_after_real_start_transition():
    from rig_workbench.orchestrate import providers
    from rig_workbench.orchestrate.runstate import compute_next, new_state

    module = load_module()
    request = "利用者向けのお知らせを書く"
    write, review = module.load_workflow_steps()
    state = new_state("japanese-writing", [write, review], request)
    state["review_category"] = "general"
    state["history"].append({
        "action": "BIND_REVIEW_CATEGORY", "category": "general",
    })
    action, _message = compute_next(state)
    assert action == "START"
    expected = providers.compose_step_prompt(
        state, state["steps"][0], state["step_state"]["write"]
    )

    actual = module.compose_write_prompt(request, category="general")
    assert actual == expected
    assert module.sha256_text(actual) == module.sha256_text(expected)
    assert "attempt: 1" in actual
    assert (
        "recent_history:\n- BIND_REVIEW_CATEGORY:None\n- START:write" in actual
    )


def load_module():
    spec = importlib.util.spec_from_file_location("paired_workflow_eval", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_json(
    *, verdict="APPROVE", safety="N/A", overrides=None, anchors=None,
    repair_conditions=None,
):
    statuses = {
        "single_artifact": "PASS",
        "format": "PASS",
        "fact_preservation": "PASS",
        "no_inference": "PASS",
        "japanese_quality": "PASS",
        "secret_handling": "N/A",
        "incident_support_safety": safety,
    }
    statuses.update(overrides or {})
    anchor_values = {
        key: f"anchor-{key}" for key in statuses
    }
    anchor_values.update(anchors or {})
    repair = repair_conditions or (
        ["なし"] if verdict == "APPROVE" else ["事実保持を修正する"]
    )
    return json.dumps(
        {
            "target_format": "plain-text",
            "checks": {
                key: {"status": status, "anchor": anchor_values[key]}
                for key, status in statuses.items()
            },
            "repair_conditions": repair,
            "verdict": verdict,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_workflow_review_contract_parses_strict_json_without_semantic_loss():
    from rig_workbench.orchestrate import providers

    module = load_module()
    assert module.parse_workflow_review is providers.parse_japanese_writing_review
    assert module.parsed_review_corrections is providers.japanese_review_corrections
    parsed = module.parse_workflow_review(
        review_json(
            verdict="REVISE",
            overrides={"fact_preservation": "FAIL"},
        ),
        category="business_chat",
    )

    assert parsed["parser_version"] == 3
    assert parsed["verdict"] == "REVISE"
    assert parsed["approved"] is False
    assert parsed["rows"]["事実保持"]["status"] == "FAIL"
    assert parsed["repair_conditions"] == ["事実保持を修正する"]


def test_workflow_review_contract_requires_unique_complete_approval_rows():
    module = load_module()

    approved = module.parse_workflow_review(review_json(), category="business_chat")
    assert approved["approved"] is True
    assert approved["verdict"] == "APPROVE"
    revised = module.parse_workflow_review(
        review_json(
            verdict="REVISE", overrides={"fact_preservation": "FAIL"}
        ),
        category="business_chat",
    )
    assert revised["approved"] is False
    assert revised["repair_conditions"] == ["事実保持を修正する"]

    valid_payload = json.loads(review_json())
    missing = json.loads(review_json())
    del missing["checks"]["format"]
    extra = json.loads(review_json())
    extra["checks"]["extra"] = {"status": "PASS", "anchor": "extra"}
    unknown_top = json.loads(review_json())
    unknown_top["extra"] = True
    wrong_status = json.loads(review_json(verdict="REVISE"))
    wrong_status["checks"]["single_artifact"]["status"] = "N/A"
    invalid_format_status = json.loads(review_json(verdict="REVISE"))
    invalid_format_status["checks"]["format"]["status"] = "N/A"
    blocking_approve = json.loads(review_json())
    blocking_approve["checks"]["fact_preservation"]["status"] = "UNKNOWN"
    extra_check_field = json.loads(review_json())
    extra_check_field["checks"]["format"]["reason"] = "not allowed"
    malformed = (
        review_json(verdict="UNVERIFIED"),
        review_json(verdict="UNKNOWN"),
        review_json(
            verdict="REVISE", overrides={"fact_preservation": "FAIL"}
        ) + "\n補足",
        "```json\n" + review_json() + "\n```",
        (
            "対象形式: plain-text\n検査:\n"
            "- 単一成果物: PASS — legacy\n判定: APPROVE"
        ),
        json.dumps(missing, ensure_ascii=False),
        json.dumps(extra, ensure_ascii=False),
        json.dumps(unknown_top, ensure_ascii=False),
        json.dumps(wrong_status, ensure_ascii=False),
        json.dumps(invalid_format_status, ensure_ascii=False),
        json.dumps(blocking_approve, ensure_ascii=False),
        json.dumps(extra_check_field, ensure_ascii=False),
        review_json().replace(
            '"verdict":"APPROVE"',
            '"verdict":"APPROVE","verdict":"APPROVE"',
        ),
        review_json().replace(
            '"status":"PASS"',
            '"status":"PASS","status":"PASS"',
            1,
        ),
        review_json().replace('"target_format":"plain-text"', '"target_format":NaN'),
        review_json().replace('"target_format":"plain-text"', '"target_format":"pdf"'),
        review_json(anchors={"single_artifact": ""}),
        review_json(anchors={"single_artifact": " leading"}),
        review_json(anchors={"single_artifact": "trailing "}),
        review_json(anchors={"single_artifact": "\t"}),
        review_json(
            verdict="REVISE", overrides={"fact_preservation": "FAIL"},
            repair_conditions=[" "],
        ),
        review_json(
            verdict="REVISE", overrides={"fact_preservation": "FAIL"},
            repair_conditions=["\t"],
        ),
        review_json(
            verdict="REVISE",
            overrides={"fact_preservation": "FAIL"},
            repair_conditions=["なし", "事実保持を修正する"],
        ),
    )
    assert set(valid_payload) == {
        "target_format", "checks", "repair_conditions", "verdict",
    }
    for invalid in malformed:
        with pytest.raises(ValueError, match="review contract"):
            module.parse_workflow_review(invalid, category="business_chat")

    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(review_json(safety="N/A"), category="support_reply")
    assert module.parse_workflow_review(
        review_json(safety="PASS"), category="incident_report"
    )["approved"] is True
    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(
            review_json(anchors={"single_artifact": "x" * 501}),
            category="business_chat",
        )
    with pytest.raises(ValueError, match="review contract"):
        module.parse_workflow_review(
            review_json(
                verdict="REVISE",
                overrides={"fact_preservation": "FAIL"},
                repair_conditions=[f"repair-{index}" for index in range(8)],
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
    assert workflow["semantic_rewrite_max"] == module.WORKFLOW_SEMANTIC_REWRITE_MAX
    assert workflow["semantics_version"] == 6
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
    assert workflow["provider_contracts"]["reference"]["argv"] == [
        "codex", "exec", "--model", "gpt-5.6-sol", "--ephemeral",
        "--skip-git-repo-check", "--ignore-user-config", "--sandbox",
        "read-only", "-o", "{output_file}",
    ]
    assert workflow["provider_contracts"]["candidate"]["argv"] == [
        "claude", "-p", "--safe-mode", "--no-session-persistence",
        "--model", "claude-sonnet-5", "--output-format", "text",
    ]
    assert workflow["provider_contracts"]["reviewer"]["argv"] == [
        "claude", "-p", "--safe-mode", "--no-session-persistence",
        "--model", "claude-opus-5", "--output-format", "text",
    ]
    assert workflow["provider_contracts"]["judge"]["argv"] == [
        "codex", "exec", "--model", "gpt-5.5", "--ephemeral",
        "--skip-git-repo-check", "--ignore-user-config", "--sandbox",
        "read-only", "-o", "{output_file}",
    ]
    assert workflow["review_contract"]["bounds"] == {
        "max_output_bytes": 16384,
        "max_target_format_codepoints": 80,
        "max_anchor_codepoints": 500,
        "max_repair_conditions": 7,
        "max_repair_codepoints": 500,
    }
    assert workflow["review_contract"]["parser_version"] == 3
    assert workflow["review_contract"]["format"] == "strict_json"
    assert workflow["review_contract"]["top_level_keys"] == [
        "target_format", "checks", "repair_conditions", "verdict",
    ]
    assert workflow["review_contract"]["check_keys"] == [
        "single_artifact", "format", "fact_preservation", "no_inference",
        "japanese_quality", "secret_handling", "incident_support_safety",
    ]
    assert workflow["review_contract"]["target_formats"] == [
        "email", "plain-text", "markdown", "ticket", "other",
    ]
    assert workflow["review_contract"]["status_enums"] == {
        "single_artifact": ["FAIL", "PASS"],
        "format": ["FAIL", "PASS", "UNKNOWN"],
        "fact_preservation": ["FAIL", "PASS", "UNKNOWN"],
        "no_inference": ["FAIL", "PASS", "UNKNOWN"],
        "japanese_quality": ["FAIL", "PASS"],
        "secret_handling": ["FAIL", "N/A", "PASS"],
        "incident_support_safety": ["FAIL", "N/A", "PASS", "UNKNOWN"],
    }
    assert workflow["review_contract"]["verdict_enum"] == [
        "APPROVE", "REVISE", "UNVERIFIED",
    ]
    assert workflow["review_contract"]["unverified_policy"] == "parser_invalid"
    assert workflow["review_contract"]["runtime_categories"] == [
        "general", "incident_report", "support_reply",
    ]
    assert workflow["review_contract"]["runtime_invalid_retry_budget"] == 3
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
        ("review_contract", "format", "text_lines"),
        ("review_contract", "top_level_keys", ["verdict"]),
        ("review_contract", "check_keys", ["format"]),
        ("review_contract", "target_formats", ["plain-text"]),
        ("review_contract", "status_enums", {"format": ["PASS"]}),
        ("review_contract", "verdict_enum", ["APPROVE"]),
        ("review_contract", "unverified_policy", "success"),
        ("review_contract", "runtime_categories", ["general"]),
        ("review_contract", "runtime_invalid_retry_budget", 2),
        ("review_exhaustion", "otherwise", "continue"),
        ("material_supply", "default_profile", "technical"),
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
    mid_run_protocol_path=None,
    review0_invalid_cases=(), review1_invalid_cases=(), invalid_review_verdict="UNVERIFIED",
    review0_error_cases=(), review0_mixed_cases=(),
):
    module.paired.time.sleep = lambda _seconds: None
    cases_path = tmp_path / "parity_cases.dev.json"
    cases_path.write_text(json.dumps({"cases": [
        {
            "id": f"case-{index}",
            "split": "dev",
            "category": {
                0: "technical_explanation", 1: "code_review", 2: "casual",
                9: "support_reply",
            }.get(index, "synthetic"),
            "prompt": f"request-{index}",
        }
        for index in range(10)
    ]}), encoding="utf-8")
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_workflow_protocol()

    class Spec:
        def __init__(self, role, index):
            contract = protocol["provider_contracts"][role]
            self.role = role
            self.identity = contract["model"]
            self.configured_argv = tuple(contract["argv"])
            self.input_mode = contract["configured_input_mode"]
            self.output_mode = contract["output_mode"]
            self.cwd_mode = contract["cwd_mode"]
            self.timeout_sec = contract["timeout_sec"]
            self.env = ()
            self.audit_role = (
                "reference" if role in {"reference", "judge"}
                else "candidate" if role == "candidate" else "judge"
            )
            self.launcher_fds = (100 + index,)

    specs = {
        role: Spec(role, index)
        for index, role in enumerate(
            ("reference", "candidate", "reviewer", "judge")
        )
    }
    providers = {
        role: {
            "provider": protocol["provider_contracts"][role]["provider"],
            "requested_model": protocol["provider_contracts"][role]["model"],
            "reported_model": protocol["provider_contracts"][role]["model"],
            "provider_spec_sha256": str(index + 1) * 64,
        }
        for index, role in enumerate(specs)
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
            return review_json(
                verdict=verdict,
                safety=safety,
                overrides=(
                    {"fact_preservation": "FAIL"}
                    if verdict == "REVISE" else None
                ),
            )
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

    changed_pin = json.loads(json.dumps(providers))
    changed_pin["reviewer"]["executable_sha256"] = "f" * 64
    changed_pin["reviewer"]["launcher_chain"] = [{"sha256": "f" * 64}]
    pin_changed = module.build_workflow_fingerprint_inputs(
        cases=cases, cases_path=cases_path, protocol=protocol,
        providers=changed_pin, judgment_prompt_fn=judgment_prompt,
    )
    assert module.canonical_sha256(pin_changed) != original

    real_review = module.compose_review_prompt
    monkeypatch.setattr(
        module, "compose_review_prompt",
        lambda request, artifact, *, category: (
            real_review(request, artifact, category=category) + "\nchanged"
        ),
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
    by_case = {row["case_id"]: row["material"] for row in result["case_states"]}
    assert by_case["case-0"]["profile"] == "technical"
    assert by_case["case-1"]["profile"] == "technical"
    assert by_case["case-2"]["profile"] == "conversation"
    assert by_case["case-3"] == {
        "profile": "none", "asset_id": None, "asset_sha256": None,
        "source_blob": None,
    }
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
    for raw in ("draft:", "reference:", "owner-only-reason", "target_format"):
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


def test_claude_review_profile_rejects_a_historical_protocol_manifest(tmp_path):
    module = load_module()
    _result, _calls, run_dir = _workflow_fixture(module, tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fingerprint_inputs"]["protocol_sha256"] = module.sha256_file(
        CURRENT_WORKFLOW_PROTOCOL_PATH
    )
    manifest["fingerprint"] = module.canonical_sha256(
        manifest["fingerprint_inputs"]
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(ValueError, match="run manifest fingerprint mismatch"):
        _workflow_fixture(module, tmp_path)


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


def test_strict_json_revise_is_a_valid_semantic_result(tmp_path):
    module = load_module()
    result, calls, run_dir = _workflow_fixture(
        module,
        tmp_path,
        review0="REVISE",
        review1="APPROVE",
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
    assert result["schema_version"] == 6
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
    assert checkpoint["schema"] == 6
    contained = checkpoint["workflow_cases"]["case-0"]["review_exhaustion"]
    assert contained["reason_code"] == "review_contract_exhausted"
    assert contained["stage"] == "REVIEW0"
    assert len(contained["attempts"]) == 3
    assert all(set(attempt) == {"attempt_id", "output_sha256"}
               for attempt in contained["attempts"])
    assert sum("::raw_writer::" in key for key in checkpoint["judgments"]) == 20
    assert sum("::reviewed_workflow::" in key for key in checkpoint["judgments"]) == 18
    public = json.dumps(result, ensure_ascii=False)
    assert "target_format" not in public
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
        ("reviewer", claude), ("judge", codex),
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
        == report["providers"]["judge"]["launcher_chain"]
    assert report["providers"]["candidate"]["launcher_chain"] \
        == report["providers"]["reviewer"]["launcher_chain"]
    assert len({row["provider_spec_sha256"]
                for row in report["providers"].values()}) == 4


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
        ("reviewer", claude), ("judge", codex),
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

    # ~16s of pure single-threaded CPU locally, so a busy runner scales it
    # directly. The 30s this used to carry was under 2x that and timed out on
    # CI's 3.12 job while 3.10 passed — the same test, decided by scheduling.
    completed = subprocess.run(
        argv, cwd=tmp_path, env=environment, text=True,
        capture_output=True, timeout=subprocess_timeout(16.0), check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["scope"] == "fresh-dev-workflow"
