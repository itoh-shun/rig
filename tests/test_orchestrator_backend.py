"""The Native execution seam preserves provider behavior and accounting."""
import concurrent.futures
import json
import subprocess
import threading
import urllib.request

import pytest

from rig_workbench.orchestrate import perf, providers
from rig_workbench.orchestrate.orchestrators.base import (
    AgentSpec, ResultNotReady, TaskContext,
)
from rig_workbench.orchestrate.orchestrators.bridge import AgentExecutionBridge
from rig_workbench.orchestrate.orchestrators.config import (
    MISSING, parse_orchestrator_config, validate_orchestrator_config,
)
from rig_workbench.orchestrate.orchestrators.native import NativeOrchestrator


def _task(invocation="call-1"):
    return TaskContext("run", "step", invocation, 1, "/workspace")


def _spec():
    return AgentSpec("mock", "model", "generator", "implementer", "prompt", 600, {})


@pytest.mark.parametrize("provider,role,rc", [
    ("claude", "generator", 0), ("codex", "verifier", 1),
    ("mock", "generator", 124), ("cmd", "generator", 127),
    ("ollama", "verifier", 0), ("anthropic", "verifier", 0),
])
def test_native_keeps_existing_provider_outputs_timeouts_and_progress(monkeypatch, provider, role, rc):
    calls, events, benchmark = [], [], []
    cfg = {"timeout": 13, "model": "chosen", "_perf": perf.accumulator(),
           "_progress_observer": events.append, "_orchestrator_bridge": AgentExecutionBridge()}
    state = {"run_id": "run-1"}

    def dispatch(*args):
        calls.append(args)
        return rc, "provider output"

    monkeypatch.setattr(providers, "_dispatch_provider", dispatch)
    monkeypatch.setattr(providers, "_record_benchmark_provider_call",
                        lambda *args: benchmark.append(args))
    assert providers.run_provider(provider, role, "prompt", cfg, "persona", state, "step") == (rc, "provider output")
    assert calls == [(provider, role, "prompt", cfg, "persona", state, "step")]
    assert benchmark == [(provider, role, "persona", "step")]
    assert [event["event"] for event in events] == ["operation_started", "operation_finished"]
    assert events[-1]["outcome"] == ("RETURNED" if rc == 0 else "FAILED")
    assert cfg["_perf"]["context_calls"] == 1
    assert f"provider_{role}" in cfg["_perf"]["phases"]


def test_native_handle_executes_once_and_short_wait_limits_dispatch_timeout():
    seen = []
    native = NativeOrchestrator(lambda spec, timeout: seen.append(timeout) or (0, spec.prompt))
    handle = native.spawn_agent(_task(), _spec())
    assert handle.ref == {}
    with pytest.raises(ResultNotReady):
        native.collect_result(handle)
    assert native.wait(handle, timeout_s=3).phase == "completed"
    assert native.wait(handle, timeout_s=3).phase == "completed"
    assert native.collect_result(handle).output == "prompt"
    assert native.collect_result(handle).output == "prompt"
    assert seen == [3]
    assert native.cancel(handle).state == "already_terminal"


def test_native_pending_cancel_never_dispatches_and_restart_is_unknown():
    native = NativeOrchestrator(lambda *_args: pytest.fail("cancelled invocation ran"))
    handle = native.spawn_agent(_task(), _spec())
    assert native.cancel(handle).state == "cancelled"
    assert native.wait(handle, timeout_s=1).phase == "cancelled"
    assert native.resume(handle).state == "ready"
    assert NativeOrchestrator(lambda *_args: (0, "")).resume(handle).state == "unknown"


def test_concurrent_waits_never_repeat_native_dispatch():
    started, release = threading.Event(), threading.Event()
    calls = []

    def dispatch(*_args):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return 0, "done"

    native = NativeOrchestrator(dispatch)
    handle = native.spawn_agent(_task(), _spec())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(native.wait, handle, timeout_s=600)
        assert started.wait(5)
        assert native.cancel(handle).state == "unsupported"
        assert native.wait(handle, timeout_s=0).phase == "running"
        release.set()
        assert first.result().phase == "completed"
    assert calls == [1]


def test_shared_bridge_binds_each_native_invocation_cfg(monkeypatch):
    bridge = AgentExecutionBridge()
    seen = []
    monkeypatch.setattr(providers, "_dispatch_provider", lambda *args: seen.append(args[3]["model"]) or (0, "done"))
    for model in ("generator-model", "review-model"):
        assert providers.run_provider("mock", "generator", "prompt", {
            "model": model, "_orchestrator_bridge": bridge,
        }) == (0, "done")
    assert seen == ["generator-model", "review-model"]


@pytest.mark.parametrize("explicit_env", [False, True])
def test_native_subprocess_does_not_receive_t3_bearer_token(monkeypatch, explicit_env):
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", "secret-token")
    seen = []
    monkeypatch.setattr(providers.subprocess, "run", lambda argv, **kwargs:
                        seen.append(kwargs["env"]) or subprocess.CompletedProcess(argv, 0, "done", ""))
    cfg = {"env": {"RIG_T3_MCP_TOKEN": "explicit-secret", "KEEP": "yes"}} if explicit_env else {}
    assert providers.run_provider("mock", "generator", "prompt", cfg)[0] == 0
    assert "RIG_T3_MCP_TOKEN" not in seen[0]
    assert seen[0]["RIG_PROVIDER_SUBPROCESS"] == "1"


@pytest.mark.parametrize("value", [MISSING, {}, {"preferred": "native", "fallback": "none"},
    {"t3": {"url": "http://127.0.0.1:3773/mcp", "project_id": "project"}},
    {"t3": {"url": "https://example.org/mcp"}},
    {"t3": {"url": "http://[::1]/mcp"}},
])
def test_orchestrator_config_accepts_only_declared_settings(value):
    assert validate_orchestrator_config(value) == ()
    assert parse_orchestrator_config(value).preferred in {"auto", "native", "t3"}


@pytest.mark.parametrize("value", [None, [], True, "native", {"preferred": []},
    {"fallback": None}, {"preferred": "wrong"}, {"prefered": "native"},
    {"t3": None}, {"t3": {"token": "SECRET"}}, {"t3": {"headers": {}}},
    {"t3": {"tools": {}}}, {"t3": {"args": []}},
    {"t3": {"url": "http://example.org/mcp"}}, {"t3": {"url": "https://user:SECRET@example.org/mcp"}},
    {"t3": {"url": "https://example.org/mcp?SECRET"}}, {"t3": {"url": "https://example.org/mcp#SECRET"}},
    {"t3": {"url": ""}}, {"t3": {"url": "https://example.org:bad"}},
    {"t3": {"project_id": ""}}, {"t3": {"project_id": "bad\nSECRET"}},
])
def test_orchestrator_config_rejects_invalid_settings_without_echoing_values(value):
    errors = validate_orchestrator_config(value)
    assert errors
    assert "SECRET" not in str(errors)
    with pytest.raises(ValueError) as refused:
        parse_orchestrator_config(value)
    assert "SECRET" not in str(refused.value)


@pytest.mark.parametrize("preferred", [None, "native"])
def test_unconfigured_auto_and_explicit_native_do_not_load_t3(preferred, monkeypatch, capsys):
    from rig_workbench.orchestrate.orchestrators import selection
    monkeypatch.setattr(selection, "_load_t3", lambda *_args: pytest.fail("optional backend loaded"))
    selected = selection.select_orchestrator(cli=preferred, env={})
    assert selected.name == "native"
    assert selected.selected_by == ("default" if preferred is None else "cli")
    if preferred is None:
        assert "falling back to native" in capsys.readouterr().err


def test_explicit_native_never_probes_configured_t3():
    from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
    selected = select_orchestrator(cli="native", env={"RIG_T3_MCP_URL": "https://example.org/mcp", "RIG_T3_MCP_TOKEN": "secret"},
                                  factory=lambda *_args: pytest.fail("T3 probed"))
    assert selected.name == "native"


def test_cli_choice_overrides_manifest_and_default_but_invalid_manifest_is_not_hidden():
    from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
    from rig_workbench.orchestrate.orchestrators.base import OrchestratorUnavailable
    chosen = select_orchestrator({"preferred": "t3", "fallback": "none"}, cli="native", env={})
    assert (chosen.name, chosen.preferred, chosen.selected_by, chosen.fallback) == ("native", "native", "cli", "none")
    with pytest.raises(OrchestratorUnavailable):
        select_orchestrator({"token": "secret"}, cli="native", env={})


@pytest.mark.parametrize("cli,fallback,success", [("auto", "native", True), ("auto", "none", False), ("t3", "native", False)])
def test_strict_secure_and_managed_agents_modes_require_native(cli, fallback, success):
    from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
    from rig_workbench.orchestrate.orchestrators.base import OrchestratorUnavailable
    options = dict(cli=cli, env={}, native_only_reason="This execution mode requires Native", emit=False)
    if success:
        assert select_orchestrator({"fallback": fallback}, **options).name == "native"
    else:
        with pytest.raises(OrchestratorUnavailable):
            select_orchestrator({"fallback": fallback}, **options)


@pytest.mark.parametrize("provider,role", [("claude", "generator"), ("mock", "generator"), ("cmd", "generator"), ("ollama", "verifier")])
@pytest.mark.parametrize("timed_out", [False, True])
def test_native_seam_preserves_real_dispatcher_transport_outputs_and_timeouts(monkeypatch, provider, role, timed_out):
    transports, events, attempts = [], [], []
    cfg = {"timeout": 7, "model": "chosen", "provider_cmd": "custom-provider", "_perf": perf.accumulator(),
           "_progress_observer": events.append, "_orchestrator_bridge": AgentExecutionBridge()}

    def run(argv, **kwargs):
        transports.append(("process", kwargs["timeout"]))
        if timed_out:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1, "output", "failure detail")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "output"}}]}).encode()

    def urlopen(request, timeout):
        transports.append(("http", timeout))
        if timed_out:
            raise TimeoutError()
        assert json.loads(request.data)["messages"][0]["content"] == "prompt"
        return Response()

    monkeypatch.delenv("RIG_BENCH_MOCK_SCENARIO", raising=False)
    monkeypatch.setattr(providers.subprocess, "run", run)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(providers, "_record_benchmark_provider_call", lambda *_args: attempts.append(1))
    expected = (124, "[provider timed out after 7 seconds]") if timed_out else (0, "output") if provider == "ollama" else (1, "output\nfailure detail")
    assert providers.run_provider(provider, role, "prompt", cfg) == expected
    assert transports == [("http" if provider == "ollama" else "process", 7)]
    assert attempts == [1]
    assert [event["event"] for event in events] == ["operation_started", "operation_finished"]
    assert events[-1]["outcome"] == ("RETURNED" if expected[0] == 0 else "FAILED")
    assert cfg["_perf"]["context_calls"] == 1


def test_t3_settings_use_nonempty_environment_before_manifest_and_never_manifest_credentials():
    from rig_workbench.orchestrate.orchestrators.selection import resolve_settings
    config = parse_orchestrator_config({"t3": {"url": "https://manifest.example/mcp", "project_id": "manifest-project"}})
    settings = resolve_settings(config, {"RIG_T3_MCP_URL": "https://environment.example/exact-endpoint",
        "RIG_T3_PROJECT_ID": "environment-project", "RIG_T3_MCP_TOKEN": "private-bearer"})
    assert (settings.endpoint, settings.project_id, settings.token) == (
        "https://environment.example/exact-endpoint", "environment-project", "private-bearer")
    assert "private-bearer" not in repr(settings)
    empty = resolve_settings(config, {"RIG_T3_MCP_URL": "", "RIG_T3_PROJECT_ID": "", "RIG_T3_MCP_TOKEN": ""})
    assert (empty.endpoint, empty.project_id, empty.token) == ("https://manifest.example/mcp", "manifest-project", None)
