import json
import pathlib
import subprocess
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rig_workbench import bench_tasks
from rig_workbench import bench_providers
from rig_workbench.orchestrate import providers as orchestrate_providers
from rig_workbench.bench_providers import (
    ProviderAttempt,
    build_bare_attempt,
    resolve_pair_model,
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


def test_explicit_pair_model_is_preserved_without_discovery():
    assert resolve_pair_model("codex", "gpt-5", {}) == "gpt-5"


def test_mock_pair_model_is_always_concrete():
    assert resolve_pair_model("mock", None, {}) == "mock"


def test_local_pair_model_uses_live_discovery_once(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return _HttpResponse({"data": [{"id": "discovered-model"}]})

    monkeypatch.setattr(bench_providers.urllib.request, "urlopen", fake_urlopen)

    model = resolve_pair_model(
        "ollama",
        None,
        {"base_url": "http://127.0.0.1:11434/v1", "model_timeout_s": 2},
    )

    assert model == "discovered-model"
    assert requests == [("http://127.0.0.1:11434/v1/models", 2.0)]


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("ollama", "llama3.1"), ("lmstudio", "local-model")],
)
def test_local_pair_model_uses_declared_fallback_when_discovery_is_unavailable(
    monkeypatch, provider, expected
):
    monkeypatch.setattr(
        bench_providers.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )

    assert resolve_pair_model(provider, None, {}) == expected


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_cli_pair_requires_an_explicit_concrete_model(provider):
    with pytest.raises(ValueError, match="explicit model"):
        resolve_pair_model(provider, None, {})


def test_claude_bare_argv_allows_edits_in_the_scratch_repo(tmp_path):
    invocation = build_bare_attempt("claude", "same goal", tmp_path, model="sonnet")

    assert invocation.argv[:2] == ("claude", "-p")
    assert invocation.argv[-2:] == ("--permission-mode", "acceptEdits")
    assert "--no-session-persistence" in invocation.argv
    assert invocation.ephemeral is True
    assert invocation.argv[invocation.argv.index("--model") + 1] == "sonnet"
    assert "same goal" in invocation.prompt
    assert "```" not in invocation.prompt


def test_claude_ephemeral_flag_reflects_declared_cli_support(tmp_path):
    invocation = build_bare_attempt(
        "claude",
        "same goal",
        tmp_path,
        model="sonnet",
        claude_no_session_persistence=False,
    )

    assert "--no-session-persistence" not in invocation.argv
    assert invocation.ephemeral is False


def test_rig_claude_argv_uses_declared_no_session_persistence():
    argv = orchestrate_providers.build_argv(
        "claude",
        "generator",
        "fix",
        {"claude_no_session_persistence": True},
    )

    assert "--no-session-persistence" in argv


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


def test_cli_runtime_launch_failure_is_an_explicit_counted_infra_error(
    monkeypatch, corpus_task, corpus_workspace
):
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    attempt = run_bare(corpus_task, "codex", "gpt-5", corpus_workspace, {})

    assert attempt.returncode == 126
    assert attempt.invocations == 1
    assert attempt.infra_error.startswith("runtime_launch_failure")


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


def _run_local_patch(monkeypatch, corpus_task, corpus_workspace, patch):
    monkeypatch.setattr(
        bench_providers.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _HttpResponse({"choices": [{"message": {"content": patch}}]}),
    )
    return run_bare(corpus_task, "ollama", "same-model", corpus_workspace, {})


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


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError("timed out"), "timeout"),
        (ConnectionError("refused"), "endpoint_failure"),
        (urllib.error.URLError(ConnectionRefusedError("refused")), "endpoint_failure"),
        (
            urllib.error.HTTPError("http://local", 401, "unauthorized", {}, None),
            "authentication_failure",
        ),
        (
            urllib.error.HTTPError("http://local", 500, "server error", {}, None),
            "endpoint_failure",
        ),
    ],
)
def test_local_http_failures_have_same_infra_category_for_bare_and_rig(
    monkeypatch, corpus_task, corpus_workspace, error, category
):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(bench_providers.urllib.request, "urlopen", fail)

    bare = run_bare(corpus_task, "ollama", "same-model", corpus_workspace, {})
    rig_rc, rig_output = orchestrate_providers.run_provider(
        "ollama",
        "generator",
        "prompt",
        {"model": "same-model"},
    )

    assert bare.infra_error.startswith(category)
    assert rig_rc != 0
    assert bench_providers._rig_infra_error("", rig_output, {}).startswith(category)


def test_rig_local_http_endpoint_failure_survives_full_orchestration(
    corpus_task, corpus_workspace, tmp_path
):
    artifacts = tmp_path / "artifacts"

    attempt = run_rig(
        corpus_task,
        "ollama",
        "same-model",
        corpus_workspace,
        {
            "artifact_dir": artifacts,
            "base_url": "http://127.0.0.1:1/v1",
            "rig_timeout_s": 30,
        },
    )

    assert attempt.returncode != 0
    assert attempt.invocations == 1
    assert attempt.infra_error.startswith("endpoint_failure")
    state = json.loads((artifacts / "run-state.json").read_text(encoding="utf-8"))
    assert any(item.get("action") == "EXEC_FAILED" for item in state["history"])


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


def test_local_patch_git_launch_failure_is_explicit_infra(
    monkeypatch, corpus_task, corpus_workspace
):
    patch = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+value = 1\n"
    )
    monkeypatch.setattr(
        bench_providers,
        "_run_git_apply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("git")),
    )

    attempt = _run_local_patch(monkeypatch, corpus_task, corpus_workspace, patch)

    assert attempt.invocations == 1
    assert attempt.infra_error.startswith("missing_executable")


@pytest.mark.parametrize(
    "target",
    [
        "../escaped.txt",
        "C:/Users/Public/escaped.txt",
        "/tmp/escaped.txt",
    ],
)
def test_local_patch_rejects_traversal_and_absolute_paths(
    monkeypatch, corpus_task, corpus_workspace, target
):
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unsafe patch reached git apply"),
    )
    patch = (
        f"diff --git a/{target} b/{target}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{target}\n"
        "@@ -0,0 +1 @@\n"
        "+escaped\n"
    )

    attempt = _run_local_patch(monkeypatch, corpus_task, corpus_workspace, patch)

    assert attempt.returncode != 0
    assert attempt.infra_error.startswith("malformed_output")
    assert not (corpus_workspace.parent / "escaped.txt").exists()


def test_local_patch_rejects_symlink_targets(monkeypatch, corpus_task, corpus_workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = corpus_workspace / "linked"
    linked.mkdir()
    monkeypatch.setattr(
        bench_providers,
        "_path_is_link",
        lambda path: path == linked,
        raising=False,
    )
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("symlink patch reached git apply"),
    )
    patch = (
        "diff --git a/linked/escaped.txt b/linked/escaped.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/linked/escaped.txt\n"
        "@@ -0,0 +1 @@\n"
        "+escaped\n"
    )

    attempt = _run_local_patch(monkeypatch, corpus_task, corpus_workspace, patch)

    assert attempt.returncode != 0
    assert attempt.infra_error.startswith("malformed_output")
    assert not (outside / "escaped.txt").exists()


def test_local_patch_validation_is_atomic_on_mixed_valid_and_invalid_diff(
    monkeypatch, corpus_task, corpus_workspace
):
    target = corpus_workspace / "profile_service.py"
    original = target.read_text(encoding="utf-8")
    target.write_text("# valid edit\n" + original, encoding="utf-8")
    valid_patch = subprocess.run(
        ["git", "diff", "--", target.name],
        cwd=corpus_workspace,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    target.write_text(original, encoding="utf-8")
    invalid_patch = (
        "diff --git a/missing.py b/missing.py\n"
        "--- a/missing.py\n"
        "+++ b/missing.py\n"
        "@@ -1 +1 @@\n"
        "-missing\n"
        "+changed\n"
    )
    calls = []

    def reject_check(argv, **_kwargs):
        calls.append(tuple(argv))
        if "--check" not in argv:
            pytest.fail("patch was applied before the complete diff passed validation")
        return subprocess.CompletedProcess(argv, 1, "", "invalid patch")

    monkeypatch.setattr(bench_providers.subprocess, "run", reject_check)

    attempt = _run_local_patch(
        monkeypatch,
        corpus_task,
        corpus_workspace,
        valid_patch + invalid_patch,
    )

    assert attempt.returncode != 0
    assert attempt.infra_error.startswith("malformed_output")
    assert len(calls) == 1
    assert target.read_text(encoding="utf-8") == original


def test_rig_invokes_adaptive_recipe_with_same_goal_model_and_public_check(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    calls = []
    artifacts = tmp_path / "artifacts"

    def fake_run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "run-state.json").write_text(
            json.dumps({"adaptive": {"invocations": 3}}),
            encoding="utf-8",
        )
        (artifacts / "provider-calls.jsonl").write_text("1\n2\n3\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="DONE", stderr="")

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    attempt = run_rig(
        corpus_task,
        "codex",
        "gpt-5",
        corpus_workspace,
        {"artifact_dir": artifacts, "rig_argv": ("rig-wb",), "max_steps": 12},
    )

    argv = calls[0][0]
    assert argv[:3] == ("rig-wb", "run", "adaptive-bugfix")
    assert argv[argv.index("--provider") + 1] == "codex"
    assert argv[argv.index("--goal") + 1] == corpus_task.goal
    assert argv[argv.index("--model") + 1] == "gpt-5"
    assert argv[argv.index("--check") + 1]
    assert "pytest" in argv[argv.index("--check") + 1]
    assert argv[argv.index("--out") + 1] == str(artifacts / "run-state.json")
    assert calls[0][1]["cwd"] == corpus_workspace
    assert calls[0][1]["env"]["RIG_RUNS_PATH"] == str(artifacts / "runs.jsonl")
    assert calls[0][1]["env"]["RIG_GLOBAL_RUNS_PATH"] == str(artifacts / "global-runs.jsonl")
    assert calls[0][1]["env"]["RIG_STEP_OUTPUT_DIR"] == str(artifacts / "step-outputs")
    assert attempt.invocations == 3
    assert attempt.infra_error is None


def test_rig_claude_command_declares_no_session_persistence(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    calls = []
    artifacts = tmp_path / "artifacts"

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "run-state.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    run_rig(
        corpus_task,
        "claude",
        "sonnet",
        corpus_workspace,
        {"artifact_dir": artifacts, "rig_argv": ("rig-wb",)},
    )

    assert "--no-session-persistence" in calls[0]


def test_rig_provider_launch_failure_is_explicit_and_counted(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    artifacts = tmp_path / "artifacts"

    def fake_run(_argv, **_kwargs):
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "run-state.json").write_text(
            json.dumps(
                {
                    "adaptive": {"invocations": 1},
                    "history": [{"output": "[provider not found: codex]"}],
                }
            ),
            encoding="utf-8",
        )
        (artifacts / "provider-calls.jsonl").write_text("1\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=1,
            stdout="[provider not found: codex]",
            stderr="",
        )

    monkeypatch.setattr(bench_providers.subprocess, "run", fake_run)

    attempt = run_rig(
        corpus_task,
        "codex",
        None,
        corpus_workspace,
        {"artifact_dir": artifacts, "rig_argv": ("rig-wb",)},
    )

    assert attempt.returncode == 1
    assert attempt.invocations == 1
    assert "missing_executable" in attempt.infra_error


def test_mock_rig_success_applies_canonical_fix_and_records_two_calls(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    artifacts = tmp_path / "artifacts"
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))

    attempt = run_rig(
        corpus_task,
        "mock",
        "mock",
        corpus_workspace,
        {"artifact_dir": artifacts},
    )

    assert attempt.returncode == 0
    assert attempt.invocations == 2
    assert attempt.infra_error is None
    state = json.loads((artifacts / "run-state.json").read_text(encoding="utf-8"))
    assert state["recipe"] == "adaptive-bugfix"
    assert "step_state" in state
    assert state["step_state"]["acceptance"]["checks"]
    assert state["adaptive"]["invocations"] == 2
    assert state["adaptive"]["invocations"] <= state["adaptive"]["invocation_limit"]
    assert len((artifacts / "provider-calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert (artifacts / "runs.jsonl").is_file()
    assert (artifacts / "global-runs.jsonl").is_file()
    assert not (corpus_workspace / "run-state.json").exists()
    assert not (corpus_workspace / ".rig").exists()
    assert not (corpus_workspace / "step-outputs").exists()
    assert not (isolated_home / ".rig").exists()
    expected = (corpus_task.root / "canonical" / "profile_service.py").read_text(encoding="utf-8")
    assert (corpus_workspace / "profile_service.py").read_text(encoding="utf-8") == expected


def test_rig_timeout_without_state_still_counts_the_started_provider_attempt(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    def timeout(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(bench_providers.subprocess, "run", timeout)

    attempt = run_rig(
        corpus_task,
        "codex",
        None,
        corpus_workspace,
        {
            "artifact_dir": tmp_path / "artifacts",
            "rig_argv": ("rig-wb",),
            "rig_timeout_s": 10,
        },
    )

    assert attempt.returncode == 124
    assert attempt.invocations == 0
    assert "timeout" in attempt.infra_error


def test_rig_runtime_launch_failure_reports_zero_provider_calls(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    monkeypatch.setattr(
        bench_providers.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )

    attempt = run_rig(
        corpus_task,
        "codex",
        "gpt-5",
        corpus_workspace,
        {
            "artifact_dir": tmp_path / "artifacts",
            "rig_argv": ("rig-wb",),
        },
    )

    assert attempt.returncode == 126
    assert attempt.invocations == 0
    assert attempt.infra_error.startswith("runtime_launch_failure")


def test_rig_timeout_reports_exact_external_call_journal_count(
    monkeypatch, corpus_task, corpus_workspace, tmp_path
):
    artifacts = tmp_path / "artifacts"

    def timeout(argv, **_kwargs):
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "provider-calls.jsonl").write_text("one\ntwo\nthree\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(bench_providers.subprocess, "run", timeout)

    attempt = run_rig(
        corpus_task,
        "codex",
        "gpt-5",
        corpus_workspace,
        {
            "artifact_dir": artifacts,
            "rig_argv": ("rig-wb",),
            "rig_timeout_s": 10,
        },
    )

    assert attempt.returncode == 124
    assert attempt.invocations == 3


def test_provider_call_journal_append_is_exact_under_parallel_calls(monkeypatch, tmp_path):
    counter = tmp_path / "provider-calls.jsonl"
    monkeypatch.setenv("RIG_BENCH_CALL_COUNTER", str(counter))
    monkeypatch.setattr(
        orchestrate_providers.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "ok", ""),
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: orchestrate_providers.run_provider(
                    "codex",
                    "generator",
                    "prompt",
                    {},
                ),
                range(40),
            )
        )

    assert all(result == (0, "ok") for result in results)
    records = counter.read_text(encoding="utf-8").splitlines()
    assert len(records) == 40
    assert all(json.loads(record)["provider"] == "codex" for record in records)


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
