from rig_workbench.orchestrate import providers
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


def test_new_state_initializes_adaptive_budget(step_factory):
    state = new_state("adaptive-bugfix", [step_factory(id="implement")], "fix")
    assert state["adaptive"] == {
        "assessment": None,
        "invocation_limit": 3,
        "invocations": 0,
    }


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
        calls.append((role, persona))
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
    assert calls == [
        ("generator", ""),
        ("verifier", "design-reviewer"),
        ("verifier", "security-reviewer"),
    ]
    assert state["adaptive"]["invocation_limit"] == 4
    assert state["adaptive"]["invocations"] == 3


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
    calls = []
    check_calls = []

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, (
                "The regression is reproducible.\n"
                "REPRODUCTION: the boundary input still returns the wrong value\n"
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
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
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

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        if role == "verifier":
            return 0, (
                "REPRODUCTION: the boundary input still returns the wrong value\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        return 0, "STATUS: done"

    class Result:
        returncode = 1

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda cfg: "")
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
