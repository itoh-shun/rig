import json
import os
import pathlib
import subprocess

import pytest

from rig_workbench.orchestrate import commands, config, providers
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits newlines in filenames")
def test_untracked_newline_filename_is_escaped_without_losing_content(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "base")
    relative = "security\nreview.py"
    (tmp_path / relative).write_text("value = eval(payload)\n", encoding="utf-8")

    cfg = {"cwd": str(tmp_path)}
    changed_files = providers._git_changed_files(cfg)
    diff = providers._git_diff_evidence(cfg)

    assert changed_files == [r"security\x0areview.py"]
    assert diff is not None
    assert "diff --git a/security\\x0areview.py b/security\\x0areview.py" in diff
    assert "+value = eval(payload)" in diff
    assert relative not in diff
    assert all(character.isprintable() for character in changed_files[0])


@pytest.mark.skipif(os.name == "nt", reason="POSIX permits non-UTF-8 filename bytes")
def test_untracked_non_utf8_filename_bytes_are_losslessly_escaped(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "base")
    raw_relative = b"security-\xff.py"
    descriptor = os.open(
        os.path.join(os.fsencode(tmp_path), raw_relative),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(descriptor, b"value = eval(payload)\n")
    finally:
        os.close(descriptor)

    cfg = {"cwd": str(tmp_path)}
    changed_files = providers._git_changed_files(cfg)
    diff = providers._git_diff_evidence(cfg)

    assert changed_files == [r"security-\xff.py"]
    assert diff is not None
    assert "diff --git a/security-\\xff.py b/security-\\xff.py" in diff
    assert "+value = eval(payload)" in diff
    assert not any(0xDC80 <= ord(character) <= 0xDCFF for character in diff)


def test_git_reported_unsafe_bytes_are_retained_as_bounded_omitted_evidence(
    monkeypatch, tmp_path
):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "base")
    unsafe = b"missing\n-\xff.py"
    oversized = b"x" * 4_000 + b"\n-\xfe.py"
    real_run = providers.subprocess.run

    def git_reported_paths(args, **kwargs):
        if args[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=unsafe + b"\0" + oversized + b"\0",
                stderr=b"",
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr(providers.subprocess, "run", git_reported_paths)
    cfg = {"cwd": str(tmp_path)}

    changed_files = providers._git_changed_files(cfg)
    diff = providers._git_diff_evidence(cfg)

    assert r"missing\x0a-\xff.py" in changed_files
    truncated = next(path for path in changed_files if path.startswith("x"))
    assert len(truncated) <= 1_024
    assert "path truncated" in truncated
    assert diff is not None
    assert "diff --git a/missing\\x0a-\\xff.py b/missing\\x0a-\\xff.py" in diff
    assert diff.count("untracked content omitted") >= 2
    assert "missing\n-\ufffd.py" not in diff
    assert all(character.isprintable() for path in changed_files for character in path)


def test_untracked_path_is_retained_before_repository_has_head(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "first.py").write_text("value = 1\n", encoding="utf-8")

    assert providers._git_changed_files({"cwd": str(tmp_path)}) == ["first.py"]


def test_untracked_symlink_path_is_retained_without_reading_linked_content(
    monkeypatch, tmp_path
):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "base.py").write_text("SAFE = True\n", encoding="utf-8")
    _git(tmp_path, "add", "base.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    target = tmp_path.parent / f"{tmp_path.name}-linked-security.py"
    secret = "LINKED-CONTENT-MUST-NOT-BE-READ"
    target.write_text(secret, encoding="utf-8")
    link = tmp_path / "security.py"
    try:
        link.symlink_to(target)
    except OSError:
        link.write_text(secret, encoding="utf-8")
        real_is_symlink = pathlib.Path.is_symlink
        monkeypatch.setattr(
            pathlib.Path,
            "is_symlink",
            lambda path: path == link or real_is_symlink(path),
        )

    cfg = {"cwd": str(tmp_path)}
    changed_files = providers._git_changed_files(cfg)
    diff = providers._git_diff_evidence(cfg)

    assert changed_files == ["security.py"]
    assert diff is not None
    assert "security.py" in diff
    assert "linked content omitted" in diff
    assert secret not in diff


@pytest.mark.skipif(os.name != "nt", reason="Windows junction coverage")
def test_untracked_junction_child_is_retained_without_following_target(
    monkeypatch, tmp_path
):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "commit", "--allow-empty", "-q", "-m", "base")
    outside = tmp_path.parent / f"{tmp_path.name}-junction-target"
    outside.mkdir()
    secret = "JUNCTION-CONTENT-MUST-NOT-BE-READ"
    (outside / "security.py").write_text(secret, encoding="utf-8")
    junction = tmp_path / "linked"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")

    real_run = providers.subprocess.run

    def git_reported_path(args, **kwargs):
        if args[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(args, 0, stdout=b"linked/security.py\0", stderr=b"")
        return real_run(args, **kwargs)

    monkeypatch.setattr(providers.subprocess, "run", git_reported_path)
    cfg = {"cwd": str(tmp_path)}

    assert providers._git_changed_files(cfg) == ["linked/security.py"]
    diff = providers._git_diff_evidence(cfg)
    assert diff is not None
    assert "linked/security.py" in diff
    assert "linked content omitted" in diff
    assert secret not in diff


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


@pytest.mark.parametrize(
    ("file_name", "triggering_line", "expected_primary"),
    [
        ("auth_service.py", "password = current_user.password\n", "security-reviewer"),
        ("migrations.py", "ALTER TABLE users ADD COLUMN age INT\n", "design-reviewer"),
    ],
)
def test_real_cli_path_routes_risk_to_the_matching_reviewer_without_cfg_cwd(
    monkeypatch, tmp_path, file_name, triggering_line, expected_primary
):
    """End-to-end through the actual `commands.cmd_run` CLI path (not synthetic
    state), with cfg["cwd"] deliberately unset (the real shape of every non-
    `--isolate` headless run — see bench_providers.py). Before the
    config.INVOCATION_CWD fallback this always fell back to test-reviewer
    regardless of diff content, since _git_diff_evidence/_git_changed_files
    silently saw an empty diff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "base.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / file_name).write_text(triggering_line, encoding="utf-8")

    monkeypatch.setattr(config, "INVOCATION_CWD", repo)
    out_path = tmp_path / "state.json"

    with pytest.raises(SystemExit):
        commands.cmd_run([
            "adaptive-bugfix",
            "--provider",
            "mock",
            "--check",
            "true",
            "--max-steps",
            "20",
            "--out",
            str(out_path),
        ])

    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["adaptive"]["assessment"]["primary"] == expected_primary
    assert saved["adaptive"]["assessment"]["fallback_reason"] is None


def test_local_provider_generator_falls_back_to_invocation_cwd_without_cfg_cwd(
    monkeypatch, tmp_path
):
    """Same audit as the git-diff-evidence bug: orchestrate.providers.run_provider only
    routed an ollama/lmstudio "generator" call through _run_local_patch_generator (the
    code path that actually applies a patch to disk) when cfg["cwd"] was explicitly set.
    Since the real CLI never sets cfg["cwd"] outside `--isolate`, every non-isolated rig
    run with a local provider silently fell through to a plain chat completion that wrote
    nothing at all -- claude/codex don't have this gap because their subprocess calls
    already inherit the parent process's cwd (config.INVOCATION_CWD) for free."""
    monkeypatch.setattr(config, "INVOCATION_CWD", tmp_path)
    seen = {}

    def fake_local_patch_generator(provider, prompt, cfg):
        seen["provider"] = provider
        seen["cwd"] = cfg["cwd"]
        return 0, "ok"

    def fail_http_provider(provider, prompt, cfg):
        raise AssertionError("should not fall through to a plain chat completion")

    monkeypatch.setattr(providers, "_run_local_patch_generator", fake_local_patch_generator)
    monkeypatch.setattr(providers, "run_http_provider", fail_http_provider)

    rc, out = providers.run_provider("ollama", "generator", "prompt", {})

    assert rc == 0 and out == "ok"
    assert seen == {"provider": "ollama", "cwd": str(tmp_path)}


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


@pytest.mark.parametrize(
    ("wrapped", "expected"),
    [
        ("`/usr/bin/python3 -m pytest -q`", "/usr/bin/python3 -m pytest -q"),
        ('"node --test test_settings.ts"', "node --test test_settings.ts"),
        ("'true'", "true"),
        ("/usr/bin/python3 -m pytest -q", "/usr/bin/python3 -m pytest -q"),
    ],
)
def test_adaptive_finding_fields_unwraps_markdown_wrapped_mechanical_check(
    wrapped, expected
):
    """Reproduced live: gpt-5.5/codex reliably echoes the allowlisted command verbatim
    but wraps it in backticks (claude/sonnet does not, in the same contract), which
    broke the exact-string allowlist match and drove Codex's safe-stop rate over the
    20% acceptance threshold even though the underlying FAIL was well-formed."""
    output = f"REPRODUCTION: some scenario\nMECHANICAL_CHECK: {wrapped}\nVERDICT: FAIL"
    _, mechanical_check = providers._adaptive_finding_fields(output)
    assert mechanical_check == expected


def test_adaptive_finding_fields_does_not_strip_mismatched_delimiters():
    output = (
        "REPRODUCTION: some scenario\n"
        "MECHANICAL_CHECK: `python -m pytest -q\n"
        "VERDICT: FAIL"
    )
    _, mechanical_check = providers._adaptive_finding_fields(output)
    assert mechanical_check == "`python -m pytest -q"


def test_codex_style_backtick_wrapped_mechanical_check_still_gets_repaired(
    step_factory, monkeypatch, tmp_path
):
    """End-to-end reproduction of the actual Codex safe-stop mechanism: a reviewer FAIL
    with a REPRODUCTION and an allowlisted MECHANICAL_CHECK -- both wrapped in
    Markdown backticks, exactly as gpt-5.5/codex produced live for py-transaction-
    rollback and ts-stale-cache-mutation -- must still be treated as repair-eligible
    and actually trigger the informed repair, not silently fall through to safe_stop."""
    allowed_check = "/usr/bin/python3 -m pytest -q"
    calls = []
    check_calls = []
    diff = {"value": "+before repair"}

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona, "prompt": prompt})
        if role == "verifier":
            return 0, (
                "Blocking finding: the diff is correct but untested.\n"
                "REPRODUCTION: `Ledger().transfer(\"alice\", \"bob\", -10)` should raise.\n"
                f"MECHANICAL_CHECK: `{allowed_check}`\n"
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
    assert check_calls == [allowed_check]
    assert state["step_state"]["targeted-review"]["verdicts"] == [{
        "by": "adaptive-repair",
        "ok": True,
        "note": f"mechanical check passed: {allowed_check}",
    }]


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


def test_informed_repair_detects_diff_via_invocation_cwd_without_explicit_cfg_cwd(
    step_factory, monkeypatch, tmp_path
):
    """cfg["cwd"] is never set outside `--isolate` (see commands.cmd_run) — the real
    shape of every benchmarked headless run. Before the config.INVOCATION_CWD fallback
    in _git_diff_evidence/_git_changed_files, this made diff_changed always False,
    so a repair generator's real file edit was never detected and the mechanical
    check never even ran."""
    allowed_check = "true"
    calls = []

    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "base.py").write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "base.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.setattr(config, "INVOCATION_CWD", tmp_path)

    def fake_run_provider(
        provider, role, prompt, cfg, persona="", state=None, step_id=None
    ):
        calls.append({"role": role, "persona": persona})
        if role == "verifier":
            return 0, (
                "REPRODUCTION: the sibling-write path has no test pinning it\n"
                f"MECHANICAL_CHECK: {allowed_check}\n"
                "VERDICT: FAIL"
            )
        if sum(call["role"] == "generator" for call in calls) == 2:
            (tmp_path / "test_regression.py").write_text(
                "def test_x():\n    pass\n", encoding="utf-8"
            )
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state("adaptive-bugfix", _adaptive_steps(step_factory), "fix")

    # No cfg["cwd"] at all -- the real headless-run shape (see bench_providers.py /
    # commands.cmd_run, which only ever sets cfg["cwd"] behind --isolate).
    final = run_loop(
        state,
        None,
        "mock",
        "mock",
        {"checks": [allowed_check]},
        20,
        quiet=True,
    )

    assert final == "DONE"
    repair = next(item for item in state["history"] if item["action"] == "INFORMED_REPAIR")
    assert repair["diff_changed"] is True
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


def test_implement_contract_permits_scoped_verification_test_on_first_pass(
    step_factory,
):
    step = step_factory(id="implement")
    st = {"retries": 0}
    contract = providers._build_step_contract(
        {"recipe": "adaptive-bugfix", "goal": "fix"}, step, st
    )

    assert "unstated default/edge-case value" in contract
    assert "do not modify, weaken, or delete existing tests" in contract
    assert "missing regression test" not in contract


def test_implement_contract_permits_named_test_during_informed_repair(
    step_factory,
):
    step = step_factory(id="implement")
    st = {
        "retries": 0,
        "last_failure": (
            "REVIEWER: mock:test-reviewer\n"
            "REPRODUCTION: the ownership check has no test pinning the sibling-write case\n"
            "MECHANICAL_CHECK: python -m pytest -q\n"
        ),
    }
    contract = providers._build_step_contract(
        {"recipe": "adaptive-bugfix", "goal": "fix"}, step, st
    )

    assert "missing regression test for a" in contract
    assert "Do not modify, weaken, or delete any existing test" in contract
    assert "unstated default/edge-case value" not in contract


def test_adaptive_review_prompt_permits_allowlisted_check_for_coverage_findings():
    state = {"adaptive": {"assessment": {"signals": []}}}
    prompt = providers._adaptive_review_prompt(
        state, "test-reviewer", "+diff", {"checks": ["python -m pytest -q"]}
    )

    assert "missing-coverage finding" in prompt
    assert "narrowly-scoped test pinning the named input/behavior" in prompt


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
