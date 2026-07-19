import json
import pathlib
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest

from rig_workbench import bench_tasks
from rig_workbench import bench_providers
from rig_workbench.bench_providers import (
    ProviderAttempt,
    build_bare_attempt,
    run_bare,
    run_rig,
)


@pytest.fixture
def corpus_task():
    return bench_tasks.load_tasks()["py-auth-sibling-write"]


@pytest.fixture
def corpus_workspace(corpus_task):
    workspace = bench_tasks.materialize(corpus_task)
    try:
        yield workspace
    finally:
        bench_tasks._remove_tree(workspace)


@pytest.mark.parametrize("provider", ["claude", "codex", "ollama", "lmstudio", "mock"])
def test_bare_adapter_is_writable_and_ephemeral(provider, tmp_path):
    invocation = build_bare_attempt(provider, "fix the bug", tmp_path, model=None)

    assert invocation.cwd == tmp_path
    assert invocation.writable is True
    assert invocation.single_invocation is True
    assert invocation.ephemeral is True
    assert invocation.goal == "fix the bug"
    assert isinstance(invocation.cwd, pathlib.Path)


def test_claude_bare_argv_allows_edits_in_the_scratch_repo(tmp_path):
    invocation = build_bare_attempt("claude", "same goal", tmp_path, model="sonnet")

    assert invocation.argv[:2] == ("claude", "-p")
    assert invocation.argv[-2:] == ("--permission-mode", "acceptEdits")
    assert invocation.argv[invocation.argv.index("--model") + 1] == "sonnet"
    assert "same goal" in invocation.prompt
    assert "```" not in invocation.prompt


def test_codex_bare_argv_is_workspace_write_ephemeral_in_the_scratch_repo(tmp_path):
    invocation = build_bare_attempt("codex", "same goal", tmp_path, model="gpt-5")

    assert invocation.argv[:2] == ("codex", "exec")
    assert invocation.argv[invocation.argv.index("--sandbox") + 1] == "workspace-write"
    assert invocation.argv[invocation.argv.index("--cd") + 1] == str(tmp_path)
    assert "--ephemeral" in invocation.argv
    assert invocation.argv[invocation.argv.index("-m") + 1] == "gpt-5"
    assert invocation.argv[-1] == invocation.prompt
    assert "same goal" in invocation.prompt


@pytest.mark.parametrize(
    ("provider", "endpoint"),
    [
        ("ollama", "http://localhost:11434/v1/chat/completions"),
        ("lmstudio", "http://localhost:1234/v1/chat/completions"),
    ],
)
def test_local_bare_attempt_uses_same_goal_and_multifile_patch_contract(
    provider, endpoint, tmp_path
):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    invocation = build_bare_attempt(provider, "same goal", tmp_path, model="local")

    assert invocation.endpoint == endpoint
    assert "same goal" in invocation.prompt
    assert "unified diff" in invocation.prompt.lower()
    assert "app.py" in invocation.prompt
    assert "VALUE = 1" in invocation.prompt


def test_mock_bare_success_applies_the_canonical_external_corpus_fix(corpus_task, corpus_workspace):
    attempt = run_bare(corpus_task, "mock", None, corpus_workspace, {})

    assert isinstance(attempt, ProviderAttempt)
    assert attempt.provider == "mock"
    assert attempt.returncode == 0
    assert attempt.invocations == 1
    assert attempt.infra_error is None
    expected = (corpus_task.root / "canonical" / "profile_service.py").read_text(encoding="utf-8")
    assert (corpus_workspace / "profile_service.py").read_text(encoding="utf-8") == expected


@pytest.mark.parametrize(
    ("scenario", "returncode", "error_fragment"),
    [
        ("timeout", 124, "timeout"),
        ("malformed", 1, "malformed"),
    ],
)
def test_mock_bare_failures_are_explicit_and_counted(
    scenario, returncode, error_fragment, corpus_task, corpus_workspace
):
    attempt = run_bare(
        corpus_task,
        "mock",
        None,
        corpus_workspace,
        {"mock_scenario": scenario},
    )

    assert attempt.returncode == returncode
    assert attempt.invocations == 1
    assert error_fragment in attempt.infra_error


def test_mock_bare_can_leave_partial_edits_while_reporting_infra_error(
    corpus_task, corpus_workspace
):
    attempt = run_bare(
        corpus_task,
        "mock",
        None,
        corpus_workspace,
        {"mock_scenario": "partial"},
    )

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "provider_failure" in attempt.infra_error
    expected = (corpus_task.root / "narrow" / "profile_service.py").read_text(encoding="utf-8")
    assert (corpus_workspace / "profile_service.py").read_text(encoding="utf-8") == expected


def test_cli_bare_runs_exactly_one_writable_invocation(monkeypatch, corpus_task, corpus_workspace):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return SimpleNamespace(returncode=0, stdout="edited", stderr="")

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    attempt = run_bare(corpus_task, "codex", "gpt-5", corpus_workspace, {})

    assert attempt.returncode == 0
    assert attempt.invocations == 1
    assert attempt.infra_error is None
    assert len(calls) == 1
    assert calls[0][1]["cwd"] == corpus_workspace
    assert calls[0][0][calls[0][0].index("--sandbox") + 1] == "workspace-write"


def test_missing_cli_executable_is_an_explicit_counted_infra_error(
    monkeypatch, corpus_task, corpus_workspace
):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(bench_providers.subprocess, "run", missing)

    attempt = run_bare(corpus_task, "codex", None, corpus_workspace, {})

    assert attempt.returncode == 127
    assert attempt.invocations == 1
    assert "missing_executable" in attempt.infra_error


def test_cli_timeout_is_an_explicit_counted_infra_error(monkeypatch, corpus_task, corpus_workspace):
    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 10, output="partial", stderr="slow")

    monkeypatch.setattr(bench_providers.subprocess, "run", timeout)

    attempt = run_bare(corpus_task, "claude", None, corpus_workspace, {"timeout_s": 10})

    assert attempt.returncode == 124
    assert attempt.invocations == 1
    assert attempt.stdout == "partial"
    assert attempt.stderr == "slow"
    assert "timeout" in attempt.infra_error


def test_cli_authentication_failure_is_an_explicit_counted_infra_error(
    monkeypatch, corpus_task, corpus_workspace
):
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Authentication failed: please log in",
        ),
    )

    attempt = run_bare(corpus_task, "claude", None, corpus_workspace, {})

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "authentication_failure" in attempt.infra_error


class _HttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_local_provider_applies_a_multifile_unified_diff(
    monkeypatch, corpus_task, corpus_workspace
):
    originals = {}
    for name, comment in (
        ("profile_service.py", "# provider edit\n"),
        ("test_profile_service.py", "# provider test edit\n"),
    ):
        original = (corpus_workspace / name).read_text(encoding="utf-8")
        originals[name] = original
        (corpus_workspace / name).write_text(comment + original, encoding="utf-8")
    patch = subprocess.run(
        ["git", "diff", "--", *originals],
        cwd=corpus_workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    for name, original in originals.items():
        (corpus_workspace / name).write_text(original, encoding="utf-8")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _HttpResponse({"choices": [{"message": {"content": patch}}]})

    monkeypatch.setattr(bench_providers.urllib.request, "urlopen", fake_urlopen)

    attempt = run_bare(corpus_task, "ollama", "local", corpus_workspace, {})

    assert attempt.returncode == 0
    assert attempt.invocations == 1
    assert attempt.infra_error is None
    assert (
        (corpus_workspace / "profile_service.py")
        .read_text(encoding="utf-8")
        .startswith("# provider edit")
    )
    assert (
        (corpus_workspace / "test_profile_service.py")
        .read_text(encoding="utf-8")
        .startswith("# provider test edit")
    )
    body = json.loads(requests[0][0].data)
    assert corpus_task.goal in body["messages"][0]["content"]


def test_local_provider_endpoint_failure_is_explicit_and_counted(
    monkeypatch, corpus_task, corpus_workspace
):
    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(bench_providers.urllib.request, "urlopen", unavailable)

    attempt = run_bare(corpus_task, "lmstudio", None, corpus_workspace, {})

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "endpoint_failure" in attempt.infra_error


def test_local_provider_malformed_patch_is_explicit_and_counted(
    monkeypatch, corpus_task, corpus_workspace
):
    monkeypatch.setattr(
        bench_providers.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HttpResponse(
            {"choices": [{"message": {"content": "not a unified diff"}}]}
        ),
    )

    attempt = run_bare(corpus_task, "ollama", None, corpus_workspace, {})

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "malformed_output" in attempt.infra_error


def test_rig_invokes_adaptive_recipe_with_same_goal_model_and_public_check(
    monkeypatch, corpus_task, corpus_workspace
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        (corpus_workspace / "run-state.json").write_text(
            json.dumps({"adaptive": {"invocations": 3}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="DONE", stderr="")

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    attempt = run_rig(
        corpus_task,
        "codex",
        "gpt-5",
        corpus_workspace,
        {"rig_argv": ("rig-wb",), "max_steps": 12},
    )

    argv = calls[0][0]
    assert argv[:3] == ("rig-wb", "run", "adaptive-bugfix")
    assert argv[argv.index("--provider") + 1] == "codex"
    assert argv[argv.index("--goal") + 1] == corpus_task.goal
    assert argv[argv.index("--model") + 1] == "gpt-5"
    assert argv[argv.index("--check") + 1]
    assert "pytest" in argv[argv.index("--check") + 1]
    assert argv[argv.index("--out") + 1] == str(corpus_workspace / "run-state.json")
    assert calls[0][1]["cwd"] == corpus_workspace
    assert attempt.invocations == 3
    assert attempt.infra_error is None


def test_rig_provider_launch_failure_is_explicit_and_counted(
    monkeypatch, corpus_task, corpus_workspace
):
    def fake_run(_argv, **_kwargs):
        (corpus_workspace / "run-state.json").write_text(
            json.dumps(
                {
                    "adaptive": {"invocations": 1},
                    "history": [{"output": "[provider not found: codex]"}],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(
            returncode=1,
            stdout="[provider not found: codex]",
            stderr="",
        )

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    attempt = run_rig(corpus_task, "codex", None, corpus_workspace, {"rig_argv": ("rig-wb",)})

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "missing_executable" in attempt.infra_error


def test_mock_rig_success_applies_canonical_fix_and_records_two_calls(
    corpus_task, corpus_workspace
):
    attempt = run_rig(corpus_task, "mock", None, corpus_workspace, {})

    assert attempt.returncode == 0
    assert attempt.invocations == 2
    assert attempt.infra_error is None
    state = json.loads((corpus_workspace / "run-state.json").read_text(encoding="utf-8"))
    assert state["adaptive"]["invocations"] == 2
    expected = (corpus_task.root / "canonical" / "profile_service.py").read_text(encoding="utf-8")
    assert (corpus_workspace / "profile_service.py").read_text(encoding="utf-8") == expected


def test_rig_timeout_without_state_still_counts_the_started_provider_attempt(
    monkeypatch, corpus_task, corpus_workspace
):
    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(bench_providers.subprocess, "run", timeout)

    attempt = run_rig(
        corpus_task,
        "codex",
        None,
        corpus_workspace,
        {"rig_argv": ("rig-wb",), "rig_timeout_s": 10},
    )

    assert attempt.returncode == 124
    assert attempt.invocations == 1
    assert "timeout" in attempt.infra_error


def test_bare_claude_is_blocked_inside_claude_code_without_explicit_opt_in(
    monkeypatch, corpus_task, corpus_workspace
):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "active")
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("paid provider must not launch"),
    )

    attempt = run_bare(corpus_task, "claude", None, corpus_workspace, {})

    assert attempt.returncode != 0
    assert attempt.invocations == 0
    assert "blocked_headless_provider" in attempt.infra_error


def test_local_bare_provider_uses_the_same_configured_base_url(
    monkeypatch, corpus_task, corpus_workspace
):
    requests = []

    def fake_urlopen(request, timeout):
        assert timeout > 0
        requests.append(request.full_url)
        return _HttpResponse({"choices": [{"message": {"content": "not a unified diff"}}]})

    monkeypatch.setattr(bench_providers.urllib.request, "urlopen", fake_urlopen)

    run_bare(
        corpus_task,
        "ollama",
        "local",
        corpus_workspace,
        {"base_url": "http://127.0.0.1:9999/v1/"},
    )

    assert requests == ["http://127.0.0.1:9999/v1/chat/completions"]


def test_mock_rig_malformed_reviewer_output_is_a_product_stop_not_infra(
    corpus_task, corpus_workspace
):
    attempt = run_rig(
        corpus_task,
        "mock",
        None,
        corpus_workspace,
        {"mock_scenario": "malformed"},
    )

    assert attempt.returncode == 1
    assert attempt.invocations >= 1
    assert attempt.infra_error is None
