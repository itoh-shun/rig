"""Reviewer persona diversity for headless providers (#332).

Discovered via a live #330 bench run: review-diff's 3 personas
(security-reviewer/design-reviewer/test-reviewer) disagreed (1/3, 2/3 PASS)
on code that was already objectively correct. Root cause: for real (non-mock)
providers, `run_verifiers_parallel` sent every persona the exact same generic
verify prompt — the persona name was recorded for telemetry but never
actually communicated to the model, so "3-way review" was 3 identical
samples of one question, not 3 distinct lenses. Fixed by prefixing each
verifier's prompt with its facets/personas/<name>.md brief when one resolves.
"""

from rig_workbench.orchestrate import providers


def _run_mock_verifier(prompt, persona="independent"):
    return providers.run_provider("mock", "verifier", prompt, {}, persona=persona)[1]


def test_load_persona_brief_strips_frontmatter_and_returns_body():
    brief = providers._load_persona_brief("security-reviewer")
    assert brief is not None
    assert not brief.startswith("---")
    assert "権限" in brief  # authorization is axis #1 of the security-reviewer brief


def test_load_persona_brief_resolves_nested_path():
    assert providers._load_persona_brief("design/ux-reviewer") is not None


def test_dialogue_tech_explainer_injects_observable_writing_knowledge():
    facets = providers.resolve_prompt_facets({
        "personas": ["styles/dialogue-tech-explainer"],
    })

    assert len(facets["persona"]) == 1
    assert "質問役" in facets["persona"][0]
    assert "年齢や性別" in facets["persona"][0]
    assert "専門persona" in facets["persona"][0]
    assert "内部名" in facets["persona"][0]
    assert len(facets["knowledge"]) == 1
    assert "理解の遷移" in facets["knowledge"][0]
    assert "固有の口癖" in facets["knowledge"][0]
    assert "初学者" in facets["knowledge"][0]


def test_dialogue_style_composes_with_an_existing_specialist_persona():
    facets = providers.resolve_prompt_facets({
        "personas": ["styles/dialogue-tech-explainer", "security-reviewer"],
    })

    assert len(facets["persona"]) == 2
    assert any("質問役" in body for body in facets["persona"])
    assert any("権限" in body for body in facets["persona"])
    assert len(facets["knowledge"]) == 2
    assert any("理解の遷移" in body for body in facets["knowledge"])
    assert any("認証" in body for body in facets["knowledge"])


def test_load_persona_brief_unknown_persona_returns_none():
    assert providers._load_persona_brief("no-such-persona") is None


def test_load_persona_brief_independent_has_no_file_and_falls_back():
    # "independent" is the default when a step declares no personas; there is
    # deliberately no facets/personas/independent.md, so callers must fall
    # back to the shared generic prompt rather than injecting garbage.
    assert providers._load_persona_brief("independent") is None


def test_run_verifiers_parallel_injects_distinct_briefs_per_persona(monkeypatch):
    captured = {}

    def fake_run_provider(provider, role, prompt, cfg, persona="", state=None, step_id=None):
        captured[persona] = prompt
        return 0, "VERDICT: PASS"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    providers.run_verifiers_parallel(
        "claude", "generic verify prompt",
        ["security-reviewer", "design-reviewer", "independent"], {}, max_parallel=3,
    )
    # Each resolvable persona gets its OWN brief prefixed — not the same text.
    assert captured["security-reviewer"] != captured["design-reviewer"]
    assert "権限" in captured["security-reviewer"]
    assert captured["security-reviewer"].endswith("generic verify prompt")
    assert captured["design-reviewer"].endswith("generic verify prompt")
    # No persona file -> unchanged generic prompt (no silent injection of nothing).
    assert captured["independent"] == "generic verify prompt"


def test_mock_verifier_without_acceptance_criteria_emits_no_criterion_lines():
    output = _run_mock_verifier("Output format (strict):\nFinally, emit a verdict.")

    assert "CRITERION " not in output


def test_mock_verifier_emits_one_line_per_declared_acceptance_criterion():
    prompt = providers._build_verify_prompt(
        {},
        {"id": "verify", "acceptance": ["first", "second", "third"]},
        "product",
    )

    output = _run_mock_verifier(prompt)

    assert [line.split(":", 1)[0] for line in output.splitlines()
            if line.startswith("CRITERION ")] == [
        "CRITERION 1", "CRITERION 2", "CRITERION 3",
    ]


def test_mock_verifier_distinguishes_malformed_list_from_no_declared_criteria():
    absent = _run_mock_verifier("Output format (strict):\nFinally, emit a verdict.")
    malformed = _run_mock_verifier(
        "Acceptance criteria:\n  not-a-number. broken\nOutput format (strict):"
    )

    assert absent.endswith("VERDICT: PASS\n")
    assert malformed.endswith("VERDICT: FAIL\n")
    assert "malformed acceptance criteria list" in malformed
    assert "CRITERION " not in malformed


def test_mock_verifier_fail_persona_still_fails_every_declared_criterion():
    prompt = providers._build_verify_prompt(
        {}, {"id": "verify", "acceptance": ["first", "second"]}, "product",
    )
    passing = _run_mock_verifier(prompt, persona="security-reviewer")
    failing = _run_mock_verifier(prompt, persona="some-fail-persona")

    assert passing.count(": PASS - mock.py:1") == 2
    assert failing.count(": FAIL - mock.py:1") == 2
    assert passing.endswith("VERDICT: PASS\n")
    assert failing.endswith("VERDICT: FAIL\n")


def test_run_verifiers_parallel_returns_an_ordinary_verdict_after_provider_output(
    monkeypatch,
):
    monkeypatch.setattr(providers, "_load_persona_brief", lambda _persona: None)
    monkeypatch.setattr(
        providers,
        "run_provider",
        lambda *_args, **_kwargs: (
            0,
            "CRITERION 1: PASS — ordinary evidence\nVERDICT: PASS",
        ),
    )

    assert providers.run_verifiers_parallel(
        "ordinary-provider", "review this", ["ordinary-reviewer"], {}, 1,
    ) == [{
        "by": "ordinary-provider:ordinary-reviewer",
        "persona": "ordinary-reviewer",
        "provider": "ordinary-provider",
        "ok": True,
        "criteria": [
            {"n": 1, "verdict": "PASS", "anchor": "ordinary evidence"},
        ],
        "note": "exit 0; CRITERION 1: PASS — ordinary evidence VERDICT: PASS",
    }]
