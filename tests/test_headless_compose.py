"""Provider-agnostic prompt composition for headless generator steps."""

import json
import pathlib
import hashlib

import pytest

from rig_workbench.orchestrate import providers
from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.recipes import (
    load_steps,
    parse_frontmatter,
    resolve_extends,
)
from rig_workbench.orchestrate.runstate import new_state
from rig_workbench.orchestrate.runstate import save_state


def _step(**overrides):
    raw = {
        "id": "review-diff",
        "instruction": "parallel-review",
        "personas": ["security-reviewer"],
        "output_contract": "review-verdict",
        "policies": ["pre-push-review"],
    }
    raw.update(overrides)
    return load_steps({"steps": [raw]})[0]


def _japanese_review_json(
    *, verdict="APPROVE", safety="PASS", fact="PASS",
):
    return json.dumps({
        "target_format": "plain-text",
        "checks": {
            "single_artifact": {"status": "PASS", "anchor": "完成稿"},
            "format": {"status": "PASS", "anchor": "plain-text"},
            "fact_preservation": {"status": fact, "anchor": "依頼内容"},
            "no_inference": {"status": "PASS", "anchor": "追加なし"},
            "japanese_quality": {"status": "PASS", "anchor": "自然"},
            "secret_handling": {"status": "N/A", "anchor": "該当なし"},
            "incident_support_safety": {"status": safety, "anchor": "安全"},
        },
        "repair_conditions": (
            ["なし"] if verdict == "APPROVE" else ["事実保持を修正する"]
        ),
        "verdict": verdict,
    }, ensure_ascii=False)


def test_generator_prompt_composes_resolved_facets_in_canonical_order():
    prompt = providers._build_prompt(
        {"recipe": "review-only", "goal": "Review the current diff", "history": []},
        _step(),
        {"retries": 0},
    )

    positions = [
        prompt.index("## Persona"),
        prompt.index("## Knowledge"),
        prompt.index("## Instruction"),
        prompt.index("## Task Contract"),
        prompt.index("## Output Contract"),
        prompt.index("## Policy"),
    ]
    assert positions == sorted(positions)
    assert "# persona: security-reviewer" in prompt
    assert "攻撃面カタログ" in prompt
    assert "# instruction: parallel-review" in prompt
    assert "recipe: review-only" in prompt
    assert "# output-contract: review-verdict" in prompt
    assert "# policy: pre-push-review" in prompt
    assert prompt.rstrip().endswith("明示的な許可がある場合を除く）。")


def test_generator_prompt_composes_multiple_personas_in_recipe_order():
    prompt = providers._build_prompt(
        {"recipe": "release-flow", "goal": None, "history": []},
        _step(
            id="intake",
            instruction="intake",
            personas=["orchestrator", "implementer"],
            output_contract=None,
            policies=[],
        ),
    )

    assert prompt.index("# persona: orchestrator") < prompt.index("# persona: implementer")
    assert "## Output Contract" not in prompt
    assert "## Policy" not in prompt


def test_generator_prompt_uses_trusted_project_persona_and_injected_knowledge(
    tmp_path, monkeypatch,
):
    persona = tmp_path / ".claude/rig/personas/project-reviewer.md"
    persona.parent.mkdir(parents=True)
    persona.write_text(
        "---\nname: project-reviewer\ninject: ['[[project-facts]]']\n---\n"
        "PROJECT PERSONA BODY\n",
        encoding="utf-8",
    )
    knowledge = tmp_path / ".claude/rig/knowledge/wiki/project-facts.md"
    knowledge.parent.mkdir(parents=True)
    knowledge.write_text("PROJECT KNOWLEDGE BODY\n", encoding="utf-8")
    trust_store = tmp_path / "trust.json"
    trust_store.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", tmp_path)
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(trust_store))

    prompt = providers._build_prompt(
        {"recipe": "project", "goal": None, "history": []},
        _step(
            personas=["project-reviewer"],
            instruction="missing-optional-instruction",
            output_contract="missing-optional-contract",
            policies=["missing-optional-policy"],
        ),
    )

    assert "PROJECT PERSONA BODY" in prompt
    assert "PROJECT KNOWLEDGE BODY" in prompt
    assert "## Instruction" not in prompt
    assert "## Output Contract" not in prompt
    assert "## Policy" not in prompt


def test_unknown_optional_facets_preserve_the_legacy_generic_prompt():
    state = {"recipe": "legacy", "goal": None, "history": []}
    step = _step(
        id="legacy-step",
        instruction="missing-optional-instruction",
        personas=["missing-optional-persona"],
        output_contract="missing-optional-contract",
        policies=["missing-optional-policy"],
    )

    assert providers._build_prompt(state, step) == (
        "You are a rig subagent (in charge of legacy-step).\n"
        "recipe: legacy\n"
        "step: legacy-step (missing-optional-instruction)\n"
        "goal: (none)\n"
        "must: actually move the request forward; do not stop at analysis.\n"
        "Keep output concise. When the work is complete, end with 'STATUS: done'."
    )


def test_installed_pack_facets_cannot_be_shadowed_by_an_unrelated_project_asset(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs import resolver
    from rig_workbench.packs.model import ResolvedAsset

    attacker = tmp_path / "attacker.md"
    attacker.write_text("ATTACKER PERSONA\n", encoding="utf-8")
    owned = tmp_path / "owned.md"
    owned.write_text("# persona: owned-writer\n", encoding="utf-8")
    instruction = tmp_path / "instruction.md"
    instruction.write_text("# instruction: owned-write\n", encoding="utf-8")
    source = tmp_path / "installed-pack/recipes/writing.md"
    shadow = ResolvedAsset("persona", "writer", attacker, "core", "attacker", "attacker")
    binding = ResolvedAsset("persona", "writer", owned, "core", "owner", "owner")
    instruction_binding = ResolvedAsset(
        "instruction", "owned-write", instruction, "core", "owner", "owner",
    )
    monkeypatch.setattr(resolver, "resolve_asset", lambda *_args, **_kwargs: shadow)
    monkeypatch.setattr(
        resolver, "resolve_bound_asset",
        lambda kind, *_args, **_kwargs: binding if kind == "persona" else instruction_binding,
    )
    step = _step(
        personas=["writer"], instruction="owned-write", output_contract=None, policies=[],
    )
    step["recipe_source"] = str(source)

    prompt = providers._build_prompt(
        {"recipe": "writing", "goal": "write", "history": []}, step,
    )

    assert "# persona: owned-writer" in prompt
    assert "ATTACKER PERSONA" not in prompt


def test_resolved_recipe_fails_closed_when_a_declared_facet_is_missing(
    tmp_path,
):
    from rig_workbench.packs.model import PackError

    recipe = tmp_path / "recipes/missing.md"
    recipe.parent.mkdir()
    recipe.write_text(
        "---\nname: missing\nsteps:\n  - id: run\n"
        "    instruction: definitely-missing\n---\n",
        encoding="utf-8",
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)

    with pytest.raises(PackError, match="required instruction.*definitely-missing"):
        providers._build_prompt(
            {"recipe": "missing", "goal": None, "history": []},
            load_steps(resolved)[0],
        )


def test_pack_owned_recipe_never_falls_back_to_shadow_for_an_unbound_reference(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs import resolver
    from rig_workbench.packs.model import PackError, ResolvedAsset

    attacker = tmp_path / "attacker.md"
    attacker.write_text("ATTACKER PERSONA\n", encoding="utf-8")
    shadow = ResolvedAsset("persona", "writer", attacker, "core", "attacker", "attacker")
    monkeypatch.setattr(resolver, "resolve_bound_asset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resolver, "resolve_asset", lambda *_args, **_kwargs: shadow)
    monkeypatch.setattr(
        providers, "_recipe_pack_owner", lambda _source: "owner", raising=False,
    )
    step = _step(
        personas=["writer"], instruction="owned-write",
        output_contract=None, policies=[],
    )
    step["recipe_source"] = str(tmp_path / "pack/recipes/writing.md")

    with pytest.raises(PackError, match="owner.*persona.*writer"):
        providers._build_prompt(
            {"recipe": "writing", "goal": None, "history": []}, step,
        )


def test_write_artifact_is_persisted_handed_to_actual_reviewer_and_returned(
    tmp_path, monkeypatch,
):
    artifact = "PRIVATE DRAFT " + "本文" * 20_000 + " FULL TAIL"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role, persona, prompt))
        if role == "generator":
            return 0, artifact
        return 0, "根拠: artifact reviewed\n判定: APPROVE"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {
            "id": "write", "instruction": "missing-legacy-write",
            "personas": [],
        },
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification", "pre-push-review"],
            "output_contract": "review-verdict",
            "acceptance": ["artifact is grounded"],
        },
    ]})
    state = new_state("writing", steps, "produce the requested draft")
    state_path = tmp_path / "run-state.json"

    final = providers.run_loop(
        state, state_path, "writer-provider", "review-provider",
        {"model": "shared-model"}, 10, quiet=True,
    )

    assert final == "DONE"
    assert [role for _provider, role, _persona, _prompt in calls] == [
        "generator", "verifier",
    ]
    review_prompt = calls[1][3]
    assert "# persona: security-reviewer" in review_prompt
    assert "# instruction: parallel-review" in review_prompt
    assert "# output-contract: review-verdict" in review_prompt
    assert "# policy: independent-verification" in review_prompt
    assert artifact in review_prompt
    result = state["result_artifact"]
    artifact_path = pathlib.Path(result["path"])
    assert artifact_path.read_text(encoding="utf-8") == artifact
    assert artifact_path.stat().st_mode & 0o777 == 0o600
    assert state["step_state"]["review"]["reviewed_artifact"]["sha256"] == result["sha256"]
    assert "PRIVATE DRAFT" not in json.dumps(state["history"], ensure_ascii=False)


def test_independent_review_rejects_the_same_provider_and_model(
    tmp_path, monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role, cfg.get("model")))
        return (0, "draft") if role == "generator" else (0, "判定: APPROVE")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "same-provider", "same-provider",
        {"model": "same-model"}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("same-provider", "generator", "same-model")]
    assert "same provider/model" in state["stopped"]["reason"]


def test_independent_review_allows_same_provider_with_a_distinct_model(
    tmp_path, monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role, cfg.get("model")))
        return (0, "draft") if role == "generator" else (0, "判定: APPROVE")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "verifier_model": "review-model",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "same-provider", "same-provider",
        {"model": "writer-model"}, 10, quiet=True,
    )

    assert final == "DONE"
    assert calls == [
        ("same-provider", "generator", "writer-model"),
        ("same-provider", "verifier", "review-model"),
    ]


def test_independent_review_fails_closed_when_no_artifact_store_is_configured(
    monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role))
        return 0, "draft" if role == "generator" else "判定: APPROVE"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, None, "writer-provider", "review-provider", {}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("writer-provider", "generator")]
    assert "artifact" in state["stopped"]["reason"]


def test_japanese_pack_runs_writer_then_its_bound_independent_reviewer(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    steps = load_steps(resolved)
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role, persona, prompt))
        if role == "generator":
            return 0, "利用者へ渡す完成稿"
        return 0, json.dumps({
            "target_format": "plain-text",
            "checks": {
                "single_artifact": {"status": "PASS", "anchor": "完成稿"},
                "format": {"status": "PASS", "anchor": "plain-text"},
                "fact_preservation": {"status": "PASS", "anchor": "依頼内容"},
                "no_inference": {"status": "PASS", "anchor": "追加なし"},
                "japanese_quality": {"status": "PASS", "anchor": "自然"},
                "secret_handling": {"status": "N/A", "anchor": "該当なし"},
                "incident_support_safety": {"status": "PASS", "anchor": "安全"},
            },
            "repair_conditions": ["なし"],
            "verdict": "APPROVE",
        }, ensure_ascii=False)

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", steps, "障害連絡を書く")
    state["review_category"] = "incident_report"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer-provider", "review-provider",
        {"model": "shared-model", "secure_runtime": True}, 10, quiet=True,
    )

    assert final == "DONE"
    assert [(provider, role, persona) for provider, role, persona, _prompt in calls] == [
        ("writer-provider", "generator", ""),
        ("review-provider", "verifier", "japanese-writing-reviewer"),
    ]
    writer_prompt = calls[0][3]
    assert "Return only the completed deliverable text on stdout" in writer_prompt
    assert "STATUS: done" not in writer_prompt
    review_prompt = calls[1][3]
    assert "# persona: japanese-writing-reviewer" in review_prompt
    assert "# instruction: japanese-writing-review" in review_prompt
    assert "# output contract: japanese-writing-verdict" in review_prompt
    assert "利用者へ渡す完成稿" in review_prompt


def test_japanese_material_profile_is_bounded_to_write_knowledge_only():
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    write, review = load_steps(resolved)
    base = new_state("japanese-writing", [write, review], "技術説明を書く")
    base["review_category"] = "general"

    none_prompt = providers.compose_step_prompt(base, write)
    explicit_none = {**base, "material_profile": "none"}
    assert providers.compose_step_prompt(explicit_none, write) == none_prompt

    technical = {**base, "material_profile": "technical"}
    technical_prompt = providers.compose_step_prompt(technical, write)
    assert "## Knowledge" in technical_prompt
    assert "<<UNTRUSTED-" in technical_prompt
    assert "do not use it as a source of facts, do not quote it" in technical_prompt
    assert "書き手が交代した瞬間に、暗黙だった制約は制約でなくなる" in technical_prompt
    assert "今日のテーマ、これです" not in technical_prompt

    conversation = {**base, "material_profile": "conversation"}
    conversation_prompt = providers.compose_step_prompt(conversation, write)
    assert "今日のテーマ、これです" in conversation_prompt
    assert "書き手が交代した瞬間に、暗黙だった制約は制約でなくなる" not in conversation_prompt

    review_prompt = providers.compose_artifact_review_prompt(
        conversation, review, "japanese-writing-reviewer", "完成稿"
    )
    assert "style-only" not in review_prompt
    assert "今日のテーマ、これです" not in review_prompt


@pytest.mark.parametrize("attack", ["oversize", "provenance"])
def test_japanese_material_profile_fails_closed_on_asset_contract_drift(
    monkeypatch, attack,
):
    from rig_workbench.packs.model import PackError

    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    write = load_steps(resolved)[0]
    original = providers._load_composition_asset

    def drifted(kind, name, **kwargs):
        frontmatter, body = original(kind, name, **kwargs)
        if kind == "wiki" and name == "japanese-style-material-technical":
            if attack == "oversize":
                body = "あ" * 1000
            else:
                frontmatter = dict(frontmatter)
                provenance = dict(frontmatter["material_provenance"])
                provenance["source_sha256"] = "0" * 64
                frontmatter["material_provenance"] = provenance
        return frontmatter, body

    monkeypatch.setattr(providers, "_load_composition_asset", drifted)
    with pytest.raises(PackError):
        providers.japanese_material_metadata(write, "technical")


def test_japanese_runtime_retries_parser_invalid_review_without_rewriting_writer(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    steps = load_steps(resolved)
    reviews = iter(["not-json", "{}", _japanese_review_json()])
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((role, step_id))
        if role == "generator":
            return 0, "利用者へ渡す完成稿"
        return 0, next(reviews)

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", steps, "一般向け告知を書く")
    state["review_category"] = "general"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer-provider", "review-provider",
        {"model": "shared-model", "secure_runtime": True}, 10, quiet=True,
    )

    assert final == "DONE"
    assert calls.count(("generator", "write")) == 1
    assert calls.count(("verifier", "review")) == 3
    assert state["step_state"]["write"]["retries"] == 0


def test_japanese_runtime_exhausts_only_invalid_reviews_without_writer_rewrite(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((role, step_id))
        return (0, "初稿") if role == "generator" else (0, "not-json")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", load_steps(resolved), "一般向け告知を書く")
    state["review_category"] = "general"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer",
        {"secure_runtime": True}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls.count(("generator", "write")) == 1
    assert calls.count(("verifier", "review")) == 3
    assert state["step_state"]["write"]["retries"] == 0
    assert "not-json" not in json.dumps(state, ensure_ascii=False)
    assert "parser-invalid after 3 attempts" in state["stopped"]["reason"]


def test_japanese_runtime_mixed_invalid_then_transport_aborts_without_rewrite(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    review_calls = 0
    writer_calls = 0

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        nonlocal review_calls, writer_calls
        if role == "generator":
            writer_calls += 1
            return 0, "初稿"
        review_calls += 1
        return (0, "not-json") if review_calls == 1 else (75, "transport detail")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", load_steps(resolved), "一般向け告知を書く")
    state["review_category"] = "general"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer",
        {"secure_runtime": True}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert (writer_calls, review_calls) == (1, 2)
    assert state["step_state"]["write"]["retries"] == 0
    assert "transport detail" not in json.dumps(state, ensure_ascii=False)
    assert "transport failed" in state["stopped"]["reason"]


def test_japanese_runtime_valid_revise_consumes_one_writer_rewrite(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    drafts = iter(["初稿", "修正版"])
    reviews = iter([
        _japanese_review_json(verdict="REVISE", fact="FAIL"),
        _japanese_review_json(),
    ])
    calls = []
    generator_prompts = []
    canonical_runtime_repair_prompts = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((role, step_id))
        if role == "generator":
            generator_prompts.append(prompt)
            if len(generator_prompts) == 2:
                context = state["step_state"]["write"]["repair_context"]
                correction_text = json.dumps(
                    context["corrections"], ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                )
                canonical_runtime_repair_prompts.append(
                    providers.compose_repair_prompt(
                        state, state["steps"][0], "初稿", correction_text,
                    )
                )
            return 0, next(drafts)
        return 0, next(reviews)

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", load_steps(resolved), "一般向け告知を書く")
    state["review_category"] = "general"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer",
        {"secure_runtime": True}, 10, quiet=True,
    )

    assert final == "DONE"
    assert calls == [
        ("generator", "write"), ("verifier", "review"),
        ("generator", "write"), ("verifier", "review"),
    ]
    assert state["step_state"]["review"]["retries"] == 1
    assert generator_prompts[1] == canonical_runtime_repair_prompts[0]
    parsed_revise = providers.parse_japanese_writing_review(
        _japanese_review_json(verdict="REVISE", fact="FAIL"),
        category="general",
    )
    correction_text = json.dumps(
        providers.japanese_review_corrections(
            parsed_revise, category="general",
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert providers.wrap_untrusted(
        "初稿", "generated artifact",
    ) in generator_prompts[1]
    assert providers.wrap_untrusted(
        correction_text, "review correction conditions",
    ) in generator_prompts[1]
    assert "exit 0; verdict=fail" not in generator_prompts[1]
    assert "repair_context" not in state["step_state"]["write"]
    assert "事実保持を修正する" not in json.dumps(state, ensure_ascii=False)


def test_japanese_runtime_second_valid_revise_is_terminal_without_a2(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    drafts = iter(["A0", "A1", "A2 must not run"])
    reviews = iter([
        _japanese_review_json(verdict="REVISE", fact="FAIL"),
        _japanese_review_json(verdict="REVISE", fact="FAIL"),
        _japanese_review_json(),
    ])
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((role, step_id))
        return (0, next(drafts)) if role == "generator" else (0, next(reviews))

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("japanese-writing", load_steps(resolved), "一般向け告知を書く")
    state["review_category"] = "general"

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer",
        {"secure_runtime": True}, 20, quiet=True,
    )

    assert final == "NON_DELIVERABLE"
    assert calls == [
        ("generator", "write"), ("verifier", "review"),
        ("generator", "write"), ("verifier", "review"),
    ]
    assert state["step_state"]["review"]["retries"] == 1
    assert "semantic rewrite limit" in state["stopped"]["reason"]


def test_secure_japanese_runtime_requires_bound_category_before_provider(
    tmp_path, monkeypatch,
):
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    calls = []
    monkeypatch.setattr(
        providers, "run_provider",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (0, "unexpected"),
    )
    state = new_state("japanese-writing", load_steps(resolved), "一般向け告知を書く")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer",
        {"secure_runtime": True}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []
    assert "review category" in state["stopped"]["reason"]


def test_revise_routes_back_to_writer_and_reviews_only_the_changed_artifact(
    tmp_path, monkeypatch,
):
    drafts = iter(["初稿", "修正版"])
    verdicts = iter(["判定: REVISE", "判定: APPROVE"])
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((role, step_id))
        return (0, next(drafts)) if role == "generator" else (0, next(verdicts))

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "max_retries": 2,
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer", {}, 20,
        quiet=True,
    )

    assert final == "DONE"
    assert calls == [
        ("generator", "write"), ("verifier", "review"),
        ("generator", "write"), ("verifier", "review"),
    ]
    assert state["step_state"]["review"]["retries"] == 1
    hashes = state["step_state"]["review"]["reviewed_hashes"]
    assert len(hashes) == 2 and len(set(hashes)) == 2
    assert pathlib.Path(state["result_artifact"]["path"]).read_text(encoding="utf-8") == "修正版"


def test_revise_escalates_after_bounded_writer_review_attempts(
    tmp_path, monkeypatch,
):
    attempt = 0

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        nonlocal attempt
        if role == "generator":
            attempt += 1
            return 0, f"draft-{attempt}"
        return 0, "判定: REVISE"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "max_retries": 2,
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer", {}, 30,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert attempt == 3
    hashes = state["step_state"]["review"]["reviewed_hashes"]
    assert len(hashes) == 3 and len(set(hashes)) == 3


def test_identical_rewrite_is_not_sent_to_the_reviewer_twice(
    tmp_path, monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append(role)
        return (0, "unchanged") if role == "generator" else (0, "判定: REVISE")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "max_retries": 2,
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer", {}, 20,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == ["generator", "verifier", "generator"]
    assert "identical artifact" in state["stopped"]["reason"]


def test_rig_and_claude_aliases_cannot_self_review(
    tmp_path, monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role))
        return (0, "draft") if role == "generator" else (0, "判定: APPROVE")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "rig", "claude",
        {"model": "same-explicit-model"}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("rig", "generator")]
    assert "effective backend" in state["stopped"]["reason"]


def test_same_backend_requires_two_explicit_unequal_models(
    tmp_path, monkeypatch,
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role, cfg.get("model")))
        return (0, "draft") if role == "generator" else (0, "判定: APPROVE")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "verifier_model": "review-model",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "claude", "rig",
        {"model": "writer-model"}, 10, quiet=True,
    )

    assert final == "DONE"
    assert calls[-1] == ("rig", "verifier", "review-model")


def test_symlinked_artifact_directory_is_rejected_without_touching_target(tmp_path):
    run_dir = tmp_path / "run"
    outside = tmp_path / "outside"
    run_dir.mkdir()
    outside.mkdir(mode=0o755)
    (run_dir / "step-outputs").symlink_to(outside, target_is_directory=True)
    before_mode = outside.stat().st_mode & 0o777

    providers._capture_output("secret draft", {"run_dir": str(run_dir)}, "write-provider")

    assert not (outside / "write-provider.txt").exists()
    assert outside.stat().st_mode & 0o777 == before_mode


def test_symlinked_artifact_parent_is_rejected_without_writing_outside_run(tmp_path):
    real_run = tmp_path / "real-run"
    real_run.mkdir(mode=0o755)
    linked_run = tmp_path / "linked-run"
    linked_run.symlink_to(real_run, target_is_directory=True)
    before_mode = real_run.stat().st_mode & 0o777

    providers._capture_output(
        "secret draft", {"run_dir": str(linked_run)}, "write-provider",
    )

    assert not (real_run / "step-outputs" / "write-provider.txt").exists()
    assert real_run.stat().st_mode & 0o777 == before_mode


def test_run_state_and_new_run_directory_are_owner_only(tmp_path):
    run_dir = tmp_path / "private-run"
    path = run_dir / "run-state.json"
    state = new_state("writing", [], "PII: user@example.com")

    save_state(state, path)

    assert path.stat().st_mode & 0o777 == 0o600
    assert run_dir.stat().st_mode & 0o777 == 0o700
    assert "user@example.com" in path.read_text(encoding="utf-8")


def test_cmd_run_displays_the_completed_japanese_deliverable(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.orchestrate import commands

    recipe = tmp_path / "japanese-writing.md"
    recipe.write_text(
        "---\nname: japanese-writing\nsteps:\n  - id: write\n"
        "    instruction: missing-legacy-write\n---\n",
        encoding="utf-8",
    )
    artifact = tmp_path / "step-outputs/write.txt"
    artifact.parent.mkdir()
    content = "利用者へ返す最終日本語本文"
    artifact.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    def fake_run_loop(state, out, gen, ver, cfg, max_steps, **kwargs):
        state["result_artifact"] = {
            "path": str(artifact), "sha256": digest, "bytes": len(artifact.read_bytes()),
            "provider": gen, "model": cfg.get("model"),
        }
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", fake_run_loop)
    with pytest.raises(SystemExit) as exited:
        commands.cmd_run([
            str(recipe), "--provider", "mock", "--out", str(tmp_path / "run-state.json"),
        ])

    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == content
    assert f"deliverable: {artifact}" in captured.err
    assert content not in captured.err


def test_nonzero_generator_exit_cannot_publish_parseable_success_text(
    tmp_path, monkeypatch,
):
    def failed_generator(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        assert role == "generator"
        return 7, "apparently complete\nSTATUS: done"

    monkeypatch.setattr(providers, "run_provider", failed_generator)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer", {}, 5,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert state.get("result_artifact") is None
    assert "generator failed (exit 7)" in state["stopped"]["reason"]


def test_nonzero_independent_verifier_exit_cannot_approve_artifact(
    tmp_path, monkeypatch,
):
    calls = []
    drafts = 0

    def provider_result(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        nonlocal drafts
        calls.append(role)
        if role == "generator":
            drafts += 1
            return 0, f"completed draft {drafts}"
        return 9, "判定: APPROVE"

    monkeypatch.setattr(providers, "run_provider", provider_result)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict", "max_retries": 1,
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "reviewer", {}, 10,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == ["generator", "verifier", "generator", "verifier"]
    assert state["step_state"]["review"]["verdicts"][0]["ok"] is False


def test_opaque_cmd_provider_cannot_independently_verify_an_artifact(
    tmp_path, monkeypatch,
):
    calls = []

    def provider_result(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role))
        return 0, "completed draft"

    monkeypatch.setattr(providers, "run_provider", provider_result)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "writer", "cmd",
        {"provider_cmd": "opaque-review {prompt}"}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("writer", "generator")]
    assert "cmd provider identity cannot be proven" in state["stopped"]["reason"]


def test_opaque_cmd_generator_is_blocked_before_independent_workflow_calls(
    tmp_path, monkeypatch,
):
    calls = []

    def should_not_run(*args, **kwargs):
        calls.append((args, kwargs))
        return 0, "must not execute"

    monkeypatch.setattr(providers, "run_provider", should_not_run)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {
            "id": "review", "instruction": "parallel-review",
            "gate": "acceptance-gate", "personas": ["security-reviewer"],
            "policies": ["independent-verification"],
            "output_contract": "review-verdict",
        },
    ]})
    state = new_state("writing", steps, "write")

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "cmd", "codex",
        {"provider_cmd": "opaque-generate {prompt}"}, 10, quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []
    assert "cmd generator identity cannot be proven" in state["stopped"]["reason"]


def test_legacy_cmd_generator_without_independent_review_still_runs(
    tmp_path, monkeypatch,
):
    calls = []

    def legacy_cmd(
        provider, role, prompt, cfg, persona="", state=None, step_id=None,
    ):
        calls.append((provider, role))
        return 0, "legacy output"

    monkeypatch.setattr(providers, "run_provider", legacy_cmd)
    state = new_state(
        "legacy", load_steps({"steps": [
            {"id": "write", "instruction": "missing-legacy-write"},
        ]}), "write",
    )

    final = providers.run_loop(
        state, tmp_path / "run-state.json", "cmd", "codex",
        {"provider_cmd": "legacy {prompt}"}, 5, quiet=True,
    )

    assert final == "DONE"
    assert calls == [("cmd", "generator")]


def test_installed_recipe_owner_provenance_is_persisted_in_run_state():
    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)

    state = new_state("japanese-writing", load_steps(resolved), "write")

    assert state["recipe_provenance"] == [{
        "source": str(recipe.resolve()),
        "owner": "japanese-writing",
        "root": str(recipe.resolve().parents[1]),
        "source_sha256": hashlib.sha256(recipe.read_bytes()).hexdigest(),
    }]
    assert state["steps"][0]["recipe_owner"] == "japanese-writing"


def test_resume_blocks_when_persisted_recipe_owner_disappears(
    tmp_path, monkeypatch,
):
    from rig_workbench.orchestrate import runstate

    recipe = (
        pathlib.Path(__file__).resolve().parents[1]
        / "packs/domain/japanese-writing/recipes/japanese-writing.md"
    )
    resolved, _warnings = resolve_extends(parse_frontmatter(recipe), recipe)
    state = new_state("japanese-writing", load_steps(resolved), "write")
    path = tmp_path / "run-state.json"
    save_state(state, path)
    monkeypatch.setattr(runstate, "_recipe_owner_provenance", lambda _source: None)

    resumed = runstate.load_state(path)

    assert resumed["stopped"]["kind"] == "BLOCKED"
    assert "recipe owner disappeared" in resumed["stopped"]["reason"]


def test_disappeared_persisted_owner_never_uses_unqualified_asset_fallback(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs import resolver
    from rig_workbench.packs.model import PackError, ResolvedAsset

    attacker = tmp_path / "attacker.md"
    attacker.write_text("ATTACKER PERSONA", encoding="utf-8")
    monkeypatch.setattr(providers, "_recipe_pack_owner", lambda _source: None)
    monkeypatch.setattr(
        resolver, "resolve_asset",
        lambda *_args, **_kwargs: ResolvedAsset(
            "persona", "writer", attacker, "project", "attacker", "attacker",
        ),
    )
    step = _step(
        personas=["writer"], instruction="owned-write",
        output_contract=None, policies=[],
    )
    step.update({
        "recipe_source": str(tmp_path / "gone-pack/recipes/writing.md"),
        "recipe_owner": "trusted-owner",
        "recipe_owner_root": str(tmp_path / "gone-pack"),
    })

    with pytest.raises(PackError, match="owner.*unavailable"):
        providers._build_prompt(
            {"recipe": "writing", "goal": None, "history": []}, step,
        )
