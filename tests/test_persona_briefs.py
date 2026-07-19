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


def test_load_persona_brief_strips_frontmatter_and_returns_body():
    brief = providers._load_persona_brief("security-reviewer")
    assert brief is not None
    assert not brief.startswith("---")
    assert "権限" in brief  # authorization is axis #1 of the security-reviewer brief


def test_load_persona_brief_resolves_nested_path():
    assert providers._load_persona_brief("sales/hearing-reviewer") is not None


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


def test_run_verifiers_parallel_mock_provider_is_unaffected_by_prompt_content():
    # MOCK_SRC's verifier branch reads argv (role/persona), never the prompt —
    # confirms the persona-brief injection cannot change mock's deterministic
    # pass/fail behavior (mock fails when 'fail' is IN the persona name).
    results = providers.run_verifiers_parallel(
        "mock", "irrelevant prompt text", ["security-reviewer", "some-fail-persona"], {}, max_parallel=2,
    )
    by_persona = {r["persona"]: r["ok"] for r in results}
    assert by_persona["security-reviewer"] is True
    assert by_persona["some-fail-persona"] is False
