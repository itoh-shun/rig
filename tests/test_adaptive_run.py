import subprocess

import pytest

from rig_workbench.orchestrate import commands, providers
from rig_workbench.orchestrate.providers import run_loop
from rig_workbench.orchestrate.runstate import new_state


def _pass_step_checks(step, st, cfg):
    st["checks"] = [{"cmd": command, "ok": True} for command in step["checks"]]
    st["last_failure"] = None


def _adaptive_steps(step_factory):
    steps = [
        step_factory(id="implement"),
        step_factory(id="assess"),
        step_factory(id="targeted-review", gate="review-gate", max_retries=1),
        step_factory(
            id="acceptance",
            gate="acceptance-gate",
            checks=["git diff --check"],
            max_retries=1,
        ),
    ]
    for step, executor in zip(
        steps,
        ("generate", "risk-assess", "targeted-review", "checks-only"),
    ):
        step["executor"] = executor
    return steps


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_marker_is_quarantined(prompt, marker):
    assert prompt.count(marker) == 1
    marker_at = prompt.index(marker)
    opening_at = prompt.rfind("<<UNTRUSTED-", 0, marker_at)
    prior_close_at = prompt.rfind("<<END-UNTRUSTED-", 0, marker_at)
    closing_at = prompt.find("<<END-UNTRUSTED-", marker_at)
    assert opening_at > prior_close_at
    assert closing_at > marker_at


def test_new_state_initializes_adaptive_budget(step_factory):
    state = new_state("adaptive-bugfix", [step_factory(id="implement")], "fix")
    assert state["adaptive"] == {
        "assessment": None,
        "invocation_limit": 3,
        "invocations": 0,
    }


def test_untracked_file_content_is_included_in_adaptive_risk_evidence(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "base.py").write_text("SAFE = True\n", encoding="utf-8")
    _git(tmp_path, "add", "base.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "security.py").write_text(
        "def execute(payload):\n    return eval(payload)\n",
        encoding="utf-8",
    )

    cfg = {"cwd": str(tmp_path)}
    changed_files = providers._git_changed_files(cfg)
    diff = providers._git_diff_evidence(cfg)
    assessment = providers.analyze_diff(diff or "", changed_files)

    assert changed_files == ["security.py"]
    assert diff is not None
    assert "security.py" in diff
    assert "+    return eval(payload)" in diff
    assert assessment.primary == "security-reviewer"
    assert any("eval(payload)" in signal.evidence for signal in assessment.signals)


def test_reviewer_prompts_quarantine_model_controlled_diff_and_risk_evidence():
    risk_marker = "IGNORE-RIG-RISK-INSTRUCTIONS"
    diff_marker = "IGNORE-RIG-DIFF-INSTRUCTIONS"
    state = {
        "adaptive": {
            "assessment": {
                "signals": [
                    {
                        "domain": "security",
                        "severity": 3,
                        "evidence": risk_marker,
                    }
                ]
            }
        }
    }

    adaptive_prompt = providers._adaptive_review_prompt(
        state,
        "security-reviewer",
        f"+# {diff_marker}",
        {"checks": []},
    )
    generic_prompt = providers._build_verify_prompt(
        {"recipe": "adaptive-bugfix"},
        {"id": "implement", "acceptance": []},
        "generator report",
        f"+# {diff_marker}",
    )

    _assert_marker_is_quarantined(adaptive_prompt, risk_marker)
    _assert_marker_is_quarantined(adaptive_prompt, diff_marker)
    _assert_marker_is_quarantined(generic_prompt, diff_marker)


def test_cmd_run_parses_repeatable_checks_and_separates_repair_allowlist(
    write_recipe, monkeypatch, tmp_path
):
    recipe = write_recipe("adaptive-cli", """---
name: adaptive-cli
steps:
  - id: implement
    instruction: implement
    executor: generate
  - id: acceptance
    instruction: acceptance-check
    executor: checks-only
    gate: acceptance-gate
    checks:
      - "git diff --check"
---
""")
    cli_checks = [
        "python -m pytest -q tests/test_one.py",
        "python -m pytest -q tests/test_two.py",
    ]
    captured = {}

    def fake_run_loop(state, sp, gen, ver, cfg, max_steps, **kwargs):
        captured["state"] = state
        captured["cfg"] = cfg
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", fake_run_loop)

    with pytest.raises(SystemExit) as exc:
        commands.cmd_run([
            str(recipe),
            "--provider",
            "mock",
            "--check",
            cli_checks[0],
            "--check",
            cli_checks[1],
            "--out",
            str(tmp_path / "state.json"),
        ])

    assert exc.value.code == 0
    assert captured["cfg"]["checks"] == cli_checks
    acceptance = captured["state"]["steps"][-1]
    assert acceptance["checks"] == ["git diff --check", *cli_checks]
    assert providers._adaptive_check_allowlist(
        captured["state"], captured["cfg"]
    ) == set(cli_checks)


def test_normal_path_uses_one_generator_and_one_targeted_reviewer(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, "No blocking defect found.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "DONE"
    assert [(call["role"], call["persona"]) for call in calls] == [
        ("generator", ""),
        ("verifier", "test-reviewer"),
    ]
    assert state["adaptive"]["invocations"] == 2
    assert state["adaptive"]["assessment"]["primary"] == "test-reviewer"
    verdict = state["step_state"]["targeted-review"]["verdicts"][0]
    assert verdict["persona"] == "test-reviewer"
    assert verdict["risk_evidence"] == []
    assert verdict["output_criteria"]
    assert len(verdict["note"]) <= 250


def test_two_high_risk_domains_add_a_secondary_reviewer(
    step_factory, monkeypatch, tmp_path
):
    calls = []
    diff = (
        "+authenticate(request)\n"
        "+app.get('/v1/users', handler)\n"
    )

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, "No blocking defect found.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: diff)
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "DONE"
    assert [(call["role"], call["persona"]) for call in calls] == [
        ("generator", ""),
        ("verifier", "design-reviewer"),
        ("verifier", "security-reviewer"),
    ]
    assert state["adaptive"]["invocation_limit"] == 4
    assert state["adaptive"]["invocations"] == 3


def test_adaptive_multi_generator_panel_stops_before_provider_calls(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((provider, role, persona))
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock-a",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
        generators=["mock-a", "mock-b"],
    )

    assert final == "BLOCKED"
    assert calls == []
    assert state["adaptive"]["invocations"] == 0
    assert state["stopped"]["kind"] == "BLOCKED"
    assert state["stopped"]["reason"] == "adaptive executor requires exactly one generator"


def test_adaptive_budget_is_checked_before_initial_generator(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")
    state["adaptive"]["invocations"] = state["adaptive"]["invocation_limit"]

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []
    assert state["adaptive"]["invocations"] == 3
    assert state["stopped"]["reason"] == "adaptive invocation budget exhausted"
    assert not any(item["action"] == "EXEC" for item in state["history"])


def test_failed_initial_generator_stops_before_adaptive_review(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 7, "generator failed"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("generator", "")]
    assert state["adaptive"]["invocations"] == 1
    assert state["adaptive"]["assessment"] is None
    assert state["stopped"] == {
        "reason": "adaptive generator failed (exit 7)",
        "kind": "BLOCKED",
        "at": "implement",
    }
    assert not any(item["action"] == "RISK_ASSESS" for item in state["history"])


def test_adaptive_gated_generate_counts_verifier_provider_call(
    step_factory, monkeypatch, tmp_path
):
    calls = []
    steps = [
        step_factory(
            id="gated-generate",
            gate="review-gate",
            personas=["independent"],
        ),
        step_factory(id="finish"),
    ]
    steps[0]["executor"] = "generate"
    steps[1]["executor"] = "checks-only"

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            return 0, "No defect.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", steps, "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "DONE"
    assert calls == [("generator", ""), ("verifier", "independent")]
    assert state["adaptive"]["invocations"] == 2


def test_adaptive_gated_generate_enforces_budget_before_verifier_call(
    step_factory, monkeypatch, tmp_path
):
    calls = []
    steps = [
        step_factory(
            id="gated-generate",
            gate="review-gate",
            personas=["independent"],
        ),
        step_factory(id="finish"),
    ]
    steps[0]["executor"] = "generate"
    steps[1]["executor"] = "checks-only"

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", steps, "fix")
    state["adaptive"]["invocation_limit"] = 1

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == [("generator", "")]
    assert state["adaptive"]["invocations"] == 1
    assert state["stopped"]["reason"] == "adaptive invocation budget exhausted"


def test_secondary_budget_exhaustion_preserves_primary_review_evidence(
    step_factory, monkeypatch, tmp_path
):
    diff = "+authenticate(request)\n+app.get('/v1/users', handler)\n"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            state["adaptive"]["invocations"] = state["adaptive"]["invocation_limit"]
            return 0, "Primary evidence is clean.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: diff)
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", ""), ("verifier", "design-reviewer")]
    verdicts = state["step_state"]["targeted-review"]["verdicts"]
    assert verdicts[0]["by"] == "mock:design-reviewer"
    assert verdicts[0]["ok"] is True
    assert verdicts[1] == {
        "by": "adaptive-budget",
        "ok": False,
        "note": "invocation budget exhausted",
    }


def test_malformed_reviewer_output_fails_closed_without_repair(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            return 0, "This output has no parseable verdict or failure check."
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": ["python -m pytest -q"]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", ""), ("verifier", "test-reviewer")]
    verdict = state["step_state"]["targeted-review"]["verdicts"][0]
    assert verdict["ok"] is False
    assert not any(item["action"] == "INFORMED_REPAIR" for item in state["history"])


def test_adaptive_pass_with_trailing_output_fails_closed(
    step_factory, monkeypatch, tmp_path
):
    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        if role == "verifier":
            return 0, "No defect found.\nVERDICT: PASS\ntrailing text"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert state["step_state"]["targeted-review"]["verdicts"][0]["ok"] is False


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("reason\nVERDICT: PASS", "PASS"),
        ("reason\nVERDICT: PASS_WITH_CONDITIONS", "PASS_WITH_CONDITIONS"),
        ("reason\nVERDICT: FAIL", "FAIL"),
        ("reason\nVERDICT: PASS trailing", None),
        ("reason\nVERDICT: PASS\ntrailing text", None),
        ("reason\n VERDICT: PASS", None),
    ],
)
def test_adaptive_final_verdict_requires_exact_final_nonempty_line(output, expected):
    assert providers._adaptive_final_verdict(output) == expected


def test_legacy_verdict_parser_remains_permissive():
    assert providers._verdict_ok("reason\nVERDICT: PASS trailing") is True


def test_pass_with_conditions_is_declared_in_adaptive_reviewer_prompt(
    step_factory, monkeypatch, tmp_path
):
    reviewer_prompts = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        if role == "verifier":
            reviewer_prompts.append(prompt)
            return 0, "Non-blocking follow-up noted.\nVERDICT: PASS_WITH_CONDITIONS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "DONE"
    assert len(reviewer_prompts) == 1
    assert "VERDICT: PASS_WITH_CONDITIONS" in reviewer_prompts[0]


def test_unknown_executor_stops_without_provider_call(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 0, "STATUS: done"

    steps = _adaptive_steps(step_factory)
    steps[0]["executor"] = "generat"
    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", steps, "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []
    assert state["stopped"] == {
        "reason": "unknown executor: generat",
        "kind": "BLOCKED",
        "at": "implement",
    }


def test_final_iteration_stop_returns_blocked_instead_of_stale_start(
    step_factory, monkeypatch, tmp_path
):
    calls = []
    steps = _adaptive_steps(step_factory)
    steps[0]["executor"] = "generat"

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", steps, "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        1,
        quiet=True,
    )

    assert final == "BLOCKED"
    assert calls == []


def test_cmd_run_rejects_explicit_empty_executor_without_provider_call(
    write_recipe, monkeypatch, tmp_path
):
    recipe = write_recipe("empty-executor", """---
name: empty-executor
steps:
  - id: invalid
    instruction: must-not-run
    executor: ""
---""")
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)

    with pytest.raises(SystemExit) as exc:
        commands.cmd_run([
            str(recipe),
            "--provider",
            "mock",
            "--max-steps",
            "1",
            "--out",
            str(tmp_path / "state.json"),
        ])

    assert exc.value.code != 0
    assert calls == []


def test_nonzero_reviewer_exit_cannot_pass(step_factory, monkeypatch, tmp_path):
    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        if role == "verifier":
            return 1, "No blocking defect found.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    verdict = state["step_state"]["targeted-review"]["verdicts"][0]
    assert verdict["ok"] is False
    assert verdict["note"].startswith("exit 1;")


def test_allowlisted_blocking_finding_gets_one_informed_repair(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    reproduction = "boundary failure " + ("X" * 300) + " LONG_FINDING_TAIL"
    calls = []
    check_calls = []
    diff = {"value": "+before repair"}

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, (
                "The regression is reproducible.\n"
                f"REPRODUCTION: {reproduction}\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        if sum(call["role"] == "generator" for call in calls) == 2:
            diff["value"] = "+after repair"
        return 0, "STATUS: done"

    class Result:
        returncode = 0

    def fake_subprocess_run(command, **kwargs):
        check_calls.append(command)
        return Result()

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: diff["value"])
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers, "_run_step_checks", _pass_step_checks)
    monkeypatch.setattr(providers.subprocess, "run", fake_subprocess_run)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "DONE"
    assert [(call["role"], call["persona"]) for call in calls] == [
        ("generator", ""),
        ("verifier", "test-reviewer"),
        ("generator", ""),
    ]
    assert allowed_check in calls[1]["prompt"]
    assert "previous_failure:" in calls[-1]["prompt"]
    assert "REPRODUCTION:" in calls[-1]["prompt"]
    assert "MECHANICAL_CHECK:" in calls[-1]["prompt"]
    assert "LONG_FINDING_TAIL" in calls[-1]["prompt"]
    assert check_calls == [allowed_check]
    assert state["adaptive"]["invocations"] == 3
    assert state["step_state"]["targeted-review"]["verdicts"] == [{
        "by": "adaptive-repair",
        "ok": True,
        "note": f"mechanical check passed: {allowed_check}",
    }]
    repair = next(item for item in state["history"] if item["action"] == "INFORMED_REPAIR")
    assert repair["check"] == allowed_check
    assert repair["exit_status"] == 0


def test_failed_post_repair_check_retains_failing_review(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    generator_calls = 0
    diff = {"value": "+before repair"}

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        nonlocal generator_calls
        if role == "verifier":
            return 0, (
                "REPRODUCTION: the boundary input still returns the wrong value\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        generator_calls += 1
        if generator_calls == 2:
            diff["value"] = "+after repair"
        return 0, "STATUS: done"

    class Result:
        returncode = 1

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: diff["value"])
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(
        providers.subprocess,
        "run",
        lambda command, **kwargs: Result(),
    )
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    verdict = state["step_state"]["targeted-review"]["verdicts"][0]
    assert verdict["by"] == "mock:test-reviewer"
    assert verdict["ok"] is False
    repair = next(item for item in state["history"] if item["action"] == "INFORMED_REPAIR")
    assert repair["exit_status"] == 1


def test_failed_repair_generator_cannot_run_check_or_pass(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    generator_calls = 0
    check_calls = []
    diff = {"value": "+before repair"}

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        nonlocal generator_calls
        if role == "verifier":
            return 0, (
                "REPRODUCTION: boundary failure\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        generator_calls += 1
        if generator_calls == 2:
            diff["value"] = "+after failed repair"
            return 1, "generator failed"
        return 0, "STATUS: done"

    class Result:
        returncode = 0

    def fake_subprocess_run(command, **kwargs):
        check_calls.append(command)
        return Result()

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: diff["value"])
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers.subprocess, "run", fake_subprocess_run)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert check_calls == []
    assert state["step_state"]["targeted-review"]["verdicts"][0]["ok"] is False
    repair = next(item for item in state["history"] if item["action"] == "INFORMED_REPAIR")
    assert repair["generator_exit_status"] == 1
    assert repair["diff_changed"] is True
    assert repair["exit_status"] is None


def test_noop_repair_generator_cannot_run_check_or_pass(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    check_calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        if role == "verifier":
            return 0, (
                "REPRODUCTION: boundary failure\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        return 0, "STATUS: done"

    class Result:
        returncode = 0

    def fake_subprocess_run(command, **kwargs):
        check_calls.append(command)
        return Result()

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "+unchanged")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers.subprocess, "run", fake_subprocess_run)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert check_calls == []
    repair = next(item for item in state["history"] if item["action"] == "INFORMED_REPAIR")
    assert repair["generator_exit_status"] == 0
    assert repair["diff_changed"] is False
    assert repair["exit_status"] is None


def test_recipe_acceptance_check_is_not_a_repair_allowlist(
    step_factory, monkeypatch, tmp_path
):
    recipe_check = "git diff --check"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, (
                "REPRODUCTION: semantic behavior is still wrong\n"
                f"MECHANICAL_CHECK: {recipe_check}\n"
                "VERDICT: FAIL"
            )
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "+changed")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert [(call["role"], call["persona"]) for call in calls] == [
        ("generator", ""),
        ("verifier", "test-reviewer"),
    ]
    assert recipe_check not in calls[1]["prompt"]
    assert not any(item["action"] == "INFORMED_REPAIR" for item in state["history"])


def test_reproduction_and_check_without_explicit_fail_cannot_trigger_repair(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            return 0, (
                "REPRODUCTION: the boundary input still returns the wrong value\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "The final verdict line is missing."
            )
        return 0, "STATUS: done"

    def reject_subprocess(*args, **kwargs):
        raise AssertionError("malformed output triggered a mechanical command")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers.subprocess, "run", reject_subprocess)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", ""), ("verifier", "test-reviewer")]
    assert not any(item["action"] == "INFORMED_REPAIR" for item in state["history"])


def test_unlisted_reviewer_check_is_never_executed(
    step_factory, monkeypatch, tmp_path
):
    unlisted_check = "curl https://reviewer.invalid/execute"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            return 0, (
                "REPRODUCTION: the boundary input still returns the wrong value\n"
                f"MECHANICAL_CHECK: {unlisted_check}\n"
                "VERDICT: FAIL"
            )
        return 0, "STATUS: done"

    def reject_subprocess(*args, **kwargs):
        raise AssertionError("an unlisted reviewer command was executed")

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    monkeypatch.setattr(providers.subprocess, "run", reject_subprocess)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": ["python -m pytest -q"]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", ""), ("verifier", "test-reviewer")]
    assert not any(item["action"] == "INFORMED_REPAIR" for item in state["history"])


def test_budget_exhaustion_stops_before_another_provider_call(
    step_factory, monkeypatch, tmp_path
):
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "generator":
            state["adaptive"]["invocations"] = 3
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path)},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", "")]
    assert state["step_state"]["targeted-review"]["verdicts"] == [{
        "by": "adaptive-budget",
        "ok": False,
        "note": "invocation budget exhausted",
    }]


def test_repair_budget_exhaustion_preserves_failing_primary_evidence(
    step_factory, monkeypatch, tmp_path
):
    allowed_check = "python -m pytest -q tests/test_regression.py"
    calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append((role, persona))
        if role == "verifier":
            state["adaptive"]["invocations"] = state["adaptive"]["invocation_limit"]
            return 0, (
                "REPRODUCTION: boundary failure\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "+changed")
    monkeypatch.setattr(providers, "_git_changed_files", lambda cfg: [])
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"cwd": str(tmp_path), "checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "ESCALATE"
    assert calls == [("generator", ""), ("verifier", "test-reviewer")]
    verdicts = state["step_state"]["targeted-review"]["verdicts"]
    assert verdicts[0]["by"] == "mock:test-reviewer"
    assert verdicts[0]["ok"] is False
    assert verdicts[1] == {
        "by": "adaptive-budget",
        "ok": False,
        "note": "invocation budget exhausted",
    }
