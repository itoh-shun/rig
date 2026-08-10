import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "benchmarks/writing-tasks/jp-natural-writing/material_effect_dev_eval.py"


def load_module():
    spec = importlib.util.spec_from_file_location("jp_material_effect_eval", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SimpleParity:
    @staticmethod
    def parse_judgment(raw):
        return json.loads(raw)


def test_material_effect_protocol_is_distinct_exact_and_mutation_closed(tmp_path):
    module = load_module()
    protocol = module.load_protocol()
    assert protocol["semantics_version"] == 2
    assert protocol["arms"] == ["none", "material"]
    assert protocol["max_logical_calls"] == 49
    assert protocol["screen_repeats"] == 1
    assert protocol["preregistered_later_repeats"] == 3
    assert protocol["release_aggregation"] == module.RELEASE_AGGREGATION
    assert protocol["support_safety"]["hard_gate"] == (
        "all_true_noncompensatory_both_arms_both_orders"
    )
    assert protocol["category_profiles"] == {
        "technical_explanation": "technical",
        "code_review": "technical",
        "casual": "conversation",
    }
    assert module.PROTOCOL_PATH != module.workflow.PROTOCOL_PATH
    assert set(protocol["provider_contracts"]) == {
        "reference", "candidate", "judge",
    }
    config = json.loads(module.CONFIG_PATH.read_text(encoding="utf-8"))
    config_text = json.dumps(config, ensure_ascii=False)
    assert "/home/" not in config_text
    assert "api_key" not in config_text.casefold()
    entries = {
        "reference": config["reference"],
        "candidate": config["candidate"],
        "judge": config["judges"][0],
    }
    assert set(config) == {"reference", "candidate", "judges"}
    assert len(config["judges"]) == 1
    for role, entry in entries.items():
        contract = protocol["provider_contracts"][role]
        assert entry["identity"] == contract["model"]
        assert entry["argv"] == contract["argv"]
        assert entry["input_mode"] == contract["configured_input_mode"]
        assert entry["output_mode"] == contract["output_mode"]
        assert entry["cwd_mode"] == contract["cwd_mode"]
        assert entry["timeout_sec"] == contract["timeout_sec"]
    changed = json.loads(module.PROTOCOL_PATH.read_text())
    changed["max_logical_calls"] = 50
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid material-effect"):
        module.load_protocol(path)
    changed = json.loads(module.PROTOCOL_PATH.read_text())
    changed["undeclared"] = True
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid material-effect"):
        module.load_protocol(path)


def test_material_effect_freezes_selected_bytes_and_rejects_later_resolution(monkeypatch):
    module = load_module()
    cases = [
        {"id": "technical", "split": "dev", "category": "technical_explanation", "prompt": "説明"},
        {"id": "casual", "split": "dev", "category": "casual", "prompt": "会話"},
        {"id": "other", "split": "dev", "category": "support_reply", "prompt": "返信"},
    ]
    frozen = module.freeze_material_supply(cases)
    technical = module.candidate_prompt(
        "説明", "technical_explanation", "material", frozen_materials=frozen,
    )
    assert technical == module.workflow.compose_write_prompt(
        "説明", category="technical_explanation", material_profile="technical",
    )
    assert module.candidate_prompt(
        "説明", "technical_explanation", "none", frozen_materials=frozen,
    ) == module.workflow.compose_write_prompt(
        "説明", category="technical_explanation", material_profile="none",
    )
    selected_hash = frozen["technical"]["prompt_sha256"]
    assert selected_hash

    def refuse_reread(*_args, **_kwargs):
        raise AssertionError("selected material was re-read")

    monkeypatch.setattr(module, "_resolve_material", refuse_reread)
    assert module.candidate_prompt(
        "説明", "technical_explanation", "material", frozen_materials=frozen,
    ) == technical
    assert frozen["technical"]["prompt_sha256"] == selected_hash

    tampered = json.loads(json.dumps(frozen))
    tampered["technical"]["text"] += "tamper"
    with pytest.raises(ValueError, match="frozen material hash"):
        module.candidate_prompt(
            "説明", "technical_explanation", "material", frozen_materials=tampered,
        )


def test_material_effect_provider_contract_rejects_wrong_model_and_shared_launcher():
    module = load_module()
    protocol = module.load_protocol()

    class Spec:
        def __init__(self, role, descriptor):
            contract = protocol["provider_contracts"][role]
            self.role = role
            self.identity = contract["model"]
            self.configured_argv = tuple(contract["argv"])
            self.input_mode = contract["configured_input_mode"]
            self.output_mode = contract["output_mode"]
            self.cwd_mode = contract["cwd_mode"]
            self.timeout_sec = contract["timeout_sec"]
            self.env = ()
            self.audit_role = "candidate" if role == "candidate" else "reference"
            self.launcher_fds = (descriptor,)

    specs = {
        role: Spec(role, 100 + index)
        for index, role in enumerate(("reference", "candidate", "judge"))
    }
    providers = {
        role: {
            "provider": protocol["provider_contracts"][role]["provider"],
            "requested_model": protocol["provider_contracts"][role]["model"],
            "provider_spec_sha256": str(index + 1) * 64,
        }
        for index, role in enumerate(specs)
    }
    module.validate_material_provider_protocol(specs, providers, protocol)
    providers["judge"]["requested_model"] = "wrong"
    with pytest.raises(ValueError, match="provider protocol mismatch"):
        module.validate_material_provider_protocol(specs, providers, protocol)
    providers["judge"]["requested_model"] = protocol["provider_contracts"]["judge"]["model"]
    specs["judge"].launcher_fds = specs["reference"].launcher_fds
    with pytest.raises(ValueError, match="separately sealed"):
        module.validate_material_provider_protocol(specs, providers, protocol)


def test_release_aggregation_requires_all_three_fresh_screens_and_never_selects_best():
    module = load_module()
    protocol = module.load_protocol()

    def screen(run_id, treated_naturalness, *, safety=True, fingerprint="f" * 64):
        rows = []
        for index in range(10):
            treated = index < 3
            rows.append({
                "case_id": f"case-{index}",
                "category": (
                    ("technical_explanation", "code_review", "casual")[index]
                    if treated else ("support_reply" if index == 9 else "general")
                ),
                "treated": treated,
                "overall_effect": treated_naturalness if treated else 0.0,
                "dimension_effects": {
                    name: treated_naturalness if treated else 0.0
                    for name in protocol["dimensions"]
                },
                "order_consistency": 1.0,
            })
        return {
            "schema_version": module.SCHEMA,
            "scope": "fresh material-effect dev screen",
            "screen_disposition": "exploratory_not_release_evidence",
            "release_eligible": False,
            "fingerprint": fingerprint,
            "counts": {"cases": 10, "treated": 3, "logical_provider_calls": 49},
            "scores": {
                "treated_naturalness_effect": treated_naturalness,
                "pooled_overall_effect": treated_naturalness * 0.3,
                "treated_dimension_effects": {
                    name: treated_naturalness for name in protocol["dimensions"]
                },
                "overall_order_consistency": 1.0,
            },
            "case_scores": rows,
            "gates": {
                "treated_naturalness": treated_naturalness >= 0.1,
                "pooled_nonnegative": treated_naturalness >= 0,
                "guards": treated_naturalness >= -0.05,
                "order_consistency": True,
                "zero_workflow_meta": True,
                "safety_unchanged": True,
                "support_safety": safety,
                "exploratory_pass": treated_naturalness >= 0.1 and safety,
            },
            "cases": [],
            "provenance": {
                "run_id": run_id,
                "protocol_sha256": "p" * 64,
                "config_sha256": "c" * 64,
                "selected_materials": {"technical": {"prompt_sha256": "m" * 64}},
                "support_safety_evidence_sha256": (
                    module.expected_all_true_support_evidence_sha256("case-9", protocol)
                ),
                "provider_spec_sha256": {
                    "reference": "1" * 64, "candidate": "2" * 64, "judge": "3" * 64,
                },
            },
        }

    screens = [screen("run-1", 0.4), screen("run-2", 0.0), screen("run-3", -0.05)]
    release = module.aggregate_material_effect_release(screens, protocol)
    assert release["included_run_ids"] == ["run-1", "run-2", "run-3"]
    assert release["scores"]["treated_naturalness_effect"] >= 0.1
    assert release["gates"]["nonnegative_runs_2_of_3"] is True
    assert release["release_eligible"] is True

    with pytest.raises(ValueError, match="exactly three fresh"):
        module.aggregate_material_effect_release(screens[:2], protocol)
    inconsistent = json.loads(json.dumps(screens))
    inconsistent[2]["fingerprint"] = "x" * 64
    with pytest.raises(ValueError, match="consistent provenance"):
        module.aggregate_material_effect_release(inconsistent, protocol)
    unsafe = json.loads(json.dumps(screens))
    unsafe[1]["gates"]["support_safety"] = False
    assert module.aggregate_material_effect_release(unsafe, protocol)["release_eligible"] is False
    tampered_support = json.loads(json.dumps(screens))
    for screen_row in tampered_support:
        screen_row["provenance"]["support_safety_evidence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="support evidence"):
        module.aggregate_material_effect_release(tampered_support, protocol)


def test_material_effect_fingerprint_binds_selected_bytes_and_untreated_aliases(tmp_path):
    module = load_module()
    cases = [
        {"id": "treated", "split": "dev", "category": "casual", "prompt": "会話"},
        {"id": "untreated", "split": "dev", "category": "support_reply", "prompt": "返信"},
    ]
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    providers = {
        role: {"provider_spec_sha256": str(index) * 64}
        for index, role in enumerate(("reference", "candidate", "judge"), 1)
    }

    def judge_prompt(request, reference, candidate, order):
        mapping = {"A": "reference", "B": "candidate", "draw": "draw"} \
            if order == "reference_first" else {"A": "candidate", "B": "reference", "draw": "draw"}
        return f"{order}\n{request}\n{reference}\n{candidate}", mapping

    frozen = module.freeze_material_supply(cases)
    fingerprint = module.build_fingerprint_inputs(
        cases=cases, cases_path=cases_path, protocol=module.load_protocol(),
        providers=providers, judgment_prompt_fn=judge_prompt,
        frozen_materials=frozen,
    )
    assert fingerprint["prompts"]["treated"]["untreated_byte_identical"] is False
    assert fingerprint["prompts"]["untreated"]["untreated_byte_identical"] is True
    assert fingerprint["cases"][0]["material"]["asset_sha256"]
    assert fingerprint["cases"][1]["material"]["asset_sha256"] is None
    assert set(fingerprint["selected_materials"]) == {"conversation", "none"}
    assert fingerprint["selected_materials"]["conversation"]["prompt_sha256"]
    assert "今日のテーマ" not in json.dumps(fingerprint, ensure_ascii=False)


def test_single_material_effect_screen_calls_only_treated_material_and_keeps_public_raw_free(tmp_path):
    module = load_module()
    categories = [
        "technical_explanation", "code_review", "casual", "synthetic", "synthetic",
        "synthetic", "synthetic", "general", "incident_report", "support_reply",
    ]
    cases = [
        {"id": f"case-{index}", "split": "dev", "category": category,
         "prompt": f"request-{index}"}
        for index, category in enumerate(categories)
    ]
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    protocol = module.load_protocol()

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
            self.audit_role = "candidate" if role == "candidate" else "reference"
            self.launcher_fds = (100 + index,)

    specs = {
        role: Spec(role, index)
        for index, role in enumerate(("reference", "candidate", "judge"))
    }
    providers = {
        role: {"provider": protocol["provider_contracts"][role]["provider"],
               "requested_model": protocol["provider_contracts"][role]["model"],
               "reported_model": role, "provider_spec_sha256": str(index) * 64}
        for index, role in enumerate(specs, 1)
    }
    calls = []

    def runner(spec, prompt, _attempts):
        calls.append((spec.role, prompt))
        if spec.role == "reference":
            return "reference-output"
        if spec.role == "candidate":
            return "material-output" if "style-only" in prompt else "none-output"
        order = "reference_first" if "ORDER: reference_first" in prompt else "candidate_first"
        candidate_answer = "B" if order == "reference_first" else "A"
        winner = candidate_answer if "material-output" in prompt else "draw"
        payload = {
            "winner": winner, "confidence": 1.0,
            "dimensions": {name: winner for name in protocol["dimensions"]},
            "reason": "owner-only",
        }
        if "CATEGORY: support_reply" in prompt:
            payload["support_safety"] = {
                answer: {field: True for field in module.paired.SUPPORT_SAFETY_FIELDS}
                for answer in ("A", "B")
            }
        return json.dumps(payload)

    def judge_prompt(request, reference, candidate, order):
        mapping = {"A": "reference", "B": "candidate", "draw": "draw"} \
            if order == "reference_first" else {"A": "candidate", "B": "reference", "draw": "draw"}
        return f"ORDER: {order}\nR: {reference}\nC: {candidate}", mapping

    result = module.run_material_effect_screen(
        run_dir=tmp_path / "run", run_id="material-test", cases=cases,
        cases_path=cases_path, protocol=protocol, specs=specs, providers=providers,
        runner=runner, judgment_prompt_fn=judge_prompt,
        parse_judgment_fn=lambda raw, category: module.paired.parse_raw_judgment_then_normalize(
            raw, SimpleParity(), protocol, category=category
        ), normalize_winner_fn=lambda winner, mapping: mapping[winner],
    )
    assert result["counts"] == {"cases": 10, "treated": 3, "logical_provider_calls": 49}
    assert sum(role == "reference" for role, _ in calls) == 10
    assert sum(role == "candidate" for role, _ in calls) == 13
    assert sum(role == "judge" for role, _ in calls) == 26
    assert result["scores"]["treated_naturalness_effect"] == 0.5
    assert result["gates"]["exploratory_pass"] is True
    assert result["release_eligible"] is False
    assert result["gates"]["support_safety"] is True
    public = json.dumps(result, ensure_ascii=False)
    for forbidden in ("reference-output", "none-output", "material-output", "owner-only", "今日のテーマ"):
        assert forbidden not in public
    untreated = [row for row in result["cases"] if row["profile"] == "none"]
    assert all(row["none_sha256"] == row["material_sha256"] for row in untreated)

    resumed = module.run_material_effect_screen(
        run_dir=tmp_path / "run", run_id="material-test", cases=cases,
        cases_path=cases_path, protocol=protocol, specs=specs, providers=providers,
        runner=lambda *_args, **_kwargs: pytest.fail("resume made a provider call"),
        judgment_prompt_fn=judge_prompt,
        parse_judgment_fn=lambda raw, category: module.paired.parse_raw_judgment_then_normalize(
            raw, SimpleParity(), protocol, category=category
        ), normalize_winner_fn=lambda winner, mapping: mapping[winner],
    )
    assert resumed["fingerprint"] == result["fingerprint"]

    checkpoint_path = tmp_path / "run" / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert module.evaluate_support_hard_gate(cases, checkpoint, protocol) is True
    unsafe = json.loads(json.dumps(checkpoint))
    support_key = "case-9::none::reference_first"
    unsafe["judgments"][support_key]["support_safety"]["candidate"]["masking"] = False
    assert module.evaluate_support_hard_gate(cases, unsafe, protocol) is False

    checkpoint["cases"]["case-0"]["artifacts"]["NONE"]["text"] += "tamper"
    module.save_secure_json(checkpoint_path, checkpoint)
    with pytest.raises(ValueError, match="checkpoint artifact integrity"):
        module.run_material_effect_screen(
            run_dir=tmp_path / "run", run_id="material-test", cases=cases,
            cases_path=cases_path, protocol=protocol, specs=specs, providers=providers,
            runner=lambda *_args, **_kwargs: pytest.fail("tampered resume made a provider call"),
            judgment_prompt_fn=judge_prompt,
            parse_judgment_fn=lambda raw, category: module.paired.parse_raw_judgment_then_normalize(
                raw, SimpleParity(), protocol, category=category
            ), normalize_winner_fn=lambda winner, mapping: mapping[winner],
        )

    original = json.loads((tmp_path / "run" / "checkpoint.json").read_text(encoding="utf-8"))
    original["cases"]["case-0"]["artifacts"]["NONE"]["text"] = "none-output"
    judgment_key = "case-0::none::reference_first"
    original["judgments"][judgment_key]["dimensions"]["naturalness"] = "A"
    module.save_secure_json(checkpoint_path, original)
    with pytest.raises(ValueError, match="checkpoint parsed judgment integrity"):
        module.run_material_effect_screen(
            run_dir=tmp_path / "run", run_id="material-test", cases=cases,
            cases_path=cases_path, protocol=protocol, specs=specs, providers=providers,
            runner=lambda *_args, **_kwargs: pytest.fail("parsed tamper made a provider call"),
            judgment_prompt_fn=judge_prompt,
            parse_judgment_fn=lambda raw, category: module.paired.parse_raw_judgment_then_normalize(
                raw, SimpleParity(), protocol, category=category
            ), normalize_winner_fn=lambda winner, mapping: mapping[winner],
        )
