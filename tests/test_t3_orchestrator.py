"""Logical fake T3 contracts and failure boundaries; no live server is contacted."""
import copy
import json
import threading
import time
import concurrent.futures

import pytest

from rig_workbench.orchestrate import providers, runstate
from rig_workbench.orchestrate.orchestrators.base import (
    AgentConnectionLost, AgentNotStarted, AgentSpec, AgentStartUnknown,
    OrchestratorUnavailable, TaskContext, UnsupportedAgentSpec,
)
from rig_workbench.orchestrate.orchestrators.bridge import AgentExecutionBridge, SaveCoordinator, selection_record
from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
from rig_workbench.orchestrate.orchestrators.t3 import T3Orchestrator
from rig_workbench.orchestrate.recipes import load_steps


class LogicalClient:
    """Rig logical contract fixture, deliberately not evidence of a live T3 build."""
    def __init__(self, cwd, *, failure=None, result_updates=None, token=None):
        self.cwd = str(cwd)
        self.failure = failure
        self.result_updates = result_updates or {}
        self.token = token
        self.calls = []
        self.agents = {}

    def call(self, operation, arguments, *, timeout_s):
        self.calls.append((operation, copy.deepcopy(arguments), timeout_s))
        if operation == "capabilities":
            return {"contract_version": "rig-t3-v1", "same_checkout": True,
                    "projects": ["project"], "capabilities": ["agent.run", "agent.parallel", "agent.cancel", "thread.durable", "thread.resume"],
                    "providers": [{"instance_id": provider, "provider": provider, "model": None,
                                   "roles": ["generator", "verifier"], "workspaces": [self.cwd],
                                   "same_checkout": True, "verifier_confinement": confinement, "constraints": {"read_only": True}}
                                  for provider, confinement in (("claude", "tool-allowlist"), ("codex", "os-sandbox"))]}
        if operation == "launch":
            if self.failure == "prestart":
                raise AgentNotStarted("confirmed_prestart")
            if self.failure == "ambiguous":
                raise TimeoutError("secret detail")
            if self.failure == "partial":
                return {"thread_id": "thread-partial"}
            if self.failure == "secret_id":
                return {"thread_id": self.token, "run_id": "run"}
            identity = str(len(self.agents) + 1)
            self.agents[identity] = arguments
            return {"thread_id": "thread-" + identity, "run_id": identity}
        if operation == "wait":
            if self.failure == "poststart":
                raise OSError("secret detail")
            if self.failure == "interrupt":
                raise KeyboardInterrupt()
            return {**arguments, "phase": "completed"}
        if operation == "interrupt":
            return {**arguments, "state": "unknown" if self.failure == "interrupt" else "cancelled"}
        if operation == "read":
            agent = self.agents[arguments["run_id"]]
            return {**arguments, "phase": "completed", "complete": True, "truncated": False,
                    "provider": agent["provider"], "model": agent["model"], "returncode": 0,
                    "output": "VERDICT: PASS\nCRITERION 1: PASS — covered" if agent["role"] == "verifier" else "STATUS: done",
                    **self.result_updates}
        raise AssertionError(operation)


def _agent(role="generator", provider="claude"):
    return AgentSpec(provider, None, role, "implementer", "prompt", 5, {})


def _task(cwd, invocation="call-1"):
    return TaskContext("run", "implement", invocation, 1, str(cwd))


def _durable(tmp_path, *, preferred="auto", fallback="native", failure=None, result_updates=None, token=None):
    state = runstate.new_state("flow", load_steps({"steps": [{"id": "implement", "instruction": "work"}]}), "goal")
    state["orchestrator"] = selection_record("t3", preferred=preferred, fallback=fallback,
                                             capabilities=("agent.run", "agent.cancel", "thread.durable", "thread.resume"),
                                             ref={"t3": {"endpoint": "http://127.0.0.1/mcp", "project_id": "project", "threads": {}}})
    client = LogicalClient(tmp_path, failure=failure, result_updates=result_updates, token=token)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp", token=token)
    path = tmp_path / "state.json"
    bridge = AgentExecutionBridge(backend, coordinator=SaveCoordinator(state, path, runstate.save_state))
    return state, path, bridge, client


def test_auto_selects_a_compatible_t3_client(tmp_path):
    client = LogicalClient(tmp_path)
    selected = select_orchestrator(env={"RIG_T3_MCP_URL": "http://127.0.0.1/mcp", "RIG_T3_MCP_TOKEN": "secret"},
                                  factory=lambda settings: T3Orchestrator(client, settings.endpoint, settings.project_id))
    assert selected.name == "t3"
    assert selected.record()["ref"]["t3"]["project_id"] == "project"
    assert "agent.parallel" not in selected.backend.capabilities()
    assert [call[0] for call in client.calls] == ["capabilities"]


def test_confirmed_pre_start_failure_retries_the_same_call_once_on_native(tmp_path):
    state, path, bridge, client = _durable(tmp_path, failure="prestart")
    attempts, native = [], []
    spec = _agent()
    result = bridge.run(_task(tmp_path), spec, native_dispatch=lambda agent, timeout: native.append((agent, timeout)) or (0, "native output"),
                        record_attempt=lambda: attempts.append(1))
    assert result == (0, "native output")
    assert native == [(spec, spec.timeout_s)]
    assert attempts == [1, 1]
    assert [call[0] for call in client.calls] == ["capabilities", "launch"]
    assert state["orchestrator"]["name"] == "t3"
    assert state["orchestrator"]["invocations"]["call-1"]["orchestrator"] == "native"
    assert state["history"][-1]["action"] == "ORCHESTRATOR_FALLBACK"
    assert runstate.load_state(path)["orchestrator"] == state["orchestrator"]


@pytest.mark.parametrize("preferred,fallback", [("t3", "native"), ("t3", "none"), ("auto", "none")])
def test_explicit_or_disabled_fallback_never_downgrades(tmp_path, preferred, fallback):
    state, _, bridge, _ = _durable(tmp_path, preferred=preferred, fallback=fallback, failure="prestart")
    with pytest.raises(AgentNotStarted):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    assert state["stopped"]["source"] == "orchestrator"
    assert state["history"] == []


@pytest.mark.parametrize("failure,exception,refs", [
    ("ambiguous", AgentStartUnknown, {}), ("partial", AgentStartUnknown, {"thread_id": "thread-partial"}),
    ("poststart", AgentConnectionLost, {"thread_id": "thread-1", "run_id": "1"}),
])
def test_unknown_or_poststart_failure_preserves_ids_and_blocks_new_work(tmp_path, failure, exception, refs):
    state, path, bridge, client = _durable(tmp_path, failure=failure)
    for invocation, expected in (("call-1", exception), ("call-2", OrchestratorUnavailable)):
        with pytest.raises(expected) as stopped:
            bridge.run(_task(tmp_path, invocation), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
        assert "secret detail" not in str(stopped.value)
    assert len([call for call in client.calls if call[0] == "launch"]) == 1
    assert state["stopped"]["kind"] == "BLOCKED"
    assert runstate.load_state(path)["orchestrator"]["ref"]["t3"]["threads"].get("call-1", {}) == refs
    with pytest.raises(OrchestratorUnavailable):
        bridge.ensure_quiescent()


def test_interrupt_attempts_cancel_preserves_unknown_outcome_and_blocks(tmp_path):
    state, path, bridge, client = _durable(tmp_path, failure="interrupt")
    with pytest.raises(AgentConnectionLost):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    assert [call[0] for call in client.calls] == ["capabilities", "launch", "wait", "interrupt"]
    saved = runstate.load_state(path)
    assert saved["stopped"]["kind"] == "BLOCKED"
    assert saved["orchestrator"]["invocations"]["call-1"]["cancel_state"] == "unknown"
    assert saved["orchestrator"]["ref"]["t3"]["threads"]["call-1"]["run_id"] == "1"


@pytest.mark.parametrize("updates", [{"truncated": True}, {"complete": False}, {"run_id": "wrong"},
                                      {"provider": "codex"}, {"model": "different"}])
def test_truncated_wrong_run_or_identity_output_is_not_collected(tmp_path, updates):
    state, _, bridge, _ = _durable(tmp_path, result_updates=updates)
    with pytest.raises(AgentConnectionLost):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    assert state["stopped"]["kind"] == "BLOCKED"
    assert not list(tmp_path.glob("orchestrator-results/*"))


def test_agent_failure_is_not_a_transport_fallback(tmp_path):
    state, _, bridge, client = _durable(tmp_path, result_updates={"returncode": 1, "phase": "failed", "output": "agent failed"})
    assert bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None) == (1, "agent failed")
    assert state["stopped"] is None
    assert [call[0] for call in client.calls] == ["capabilities", "launch", "wait", "read"]


def test_t3_does_not_launder_alias_identity_or_weaken_verifier_confinement(tmp_path):
    client = LogicalClient(tmp_path)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    handle = backend.spawn_agent(_task(tmp_path), _agent(provider="rig"))
    assert client.calls[-1][1]["provider"] == "claude"
    backend.wait(handle, timeout_s=5)
    assert backend.collect_result(handle).provider == "rig"
    backend._catalog["claude"]["verifier_confinement"] = "prompt-only"
    with pytest.raises(UnsupportedAgentSpec):
        backend.spawn_agent(_task(tmp_path, "verifier"), _agent("verifier"))
    assert len([call for call in client.calls if call[0] == "launch"]) == 1


def test_t3_serializes_all_calls_even_when_remote_advertises_parallel(tmp_path):
    state, _, bridge, client = _durable(tmp_path)
    active, maximum = 0, 0
    lock = threading.Lock()
    original = client.call

    def call(operation, arguments, *, timeout_s):
        nonlocal active, maximum
        if operation == "launch":
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
        result = original(operation, arguments, timeout_s=timeout_s)
        if operation == "read":
            with lock:
                active -= 1
        return result

    client.call = call
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: bridge.run(_task(tmp_path, f"call-{i}"), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None), range(4)))
    assert maximum == 1
    assert len(state["orchestrator"]["invocations"]) == 4


@pytest.mark.parametrize("failure", ["secret_id", None])
def test_bearer_echo_with_quotes_or_backslashes_never_reaches_state_artifacts_or_errors(tmp_path, monkeypatch, failure):
    token = 'special"secret\\value'
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", token)
    state, path, bridge, _ = _durable(tmp_path, failure=failure, result_updates={"output": token}, token=token)
    with pytest.raises((AgentStartUnknown, AgentConnectionLost)) as stopped:
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    assert token not in str(stopped.value)
    assert token not in json.dumps(state)
    assert json.dumps(token)[1:-1] not in path.read_text()
    assert not list(tmp_path.glob("orchestrator-results/*"))


def test_completed_threads_still_require_rig_verifier_and_checks(tmp_path, monkeypatch):
    state, path, bridge, client = _durable(tmp_path)
    state["steps"] = load_steps({"steps": [{"id": "implement", "instruction": "work", "gate": "acceptance-gate", "acceptance": ["covered"], "max_retries": 1}]})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    client.result_updates = {"output": "VERDICT: FAIL\nCRITERION 1: FAIL — not covered"}
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    assert providers.run_loop(state, path, "claude", "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 10, quiet=True) == "ESCALATE"
    assert {arguments["role"] for operation, arguments, _ in client.calls if operation == "launch"} == {"generator", "verifier"}
    assert state["step_state"]["implement"]["status"] != "passed"
    assert not state["done"]


def test_blocked_t3_run_never_runs_checks_or_another_dag_step(tmp_path, monkeypatch):
    state, path, bridge, client = _durable(tmp_path, failure="poststart")
    state["steps"] = load_steps({"steps": [
        {"id": "implement", "instruction": "work", "checks": ["true"]},
        {"id": "other", "instruction": "work", "checks": ["true"]},
        {"id": "dependent", "instruction": "work", "needs": ["implement"]},
    ]})
    state["step_state"].update({sid: {"status": "pending", "retries": 0, "checks": [], "verdicts": [], "approvals": []} for sid in ("other", "dependent")})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    monkeypatch.setattr(providers, "_run_step_checks", lambda *_args, **_kwargs: pytest.fail("checks ran"))
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    assert providers.run_loop(state, path, "claude", "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 10, quiet=True) == "BLOCKED"
    assert len([call for call in client.calls if call[0] == "launch"]) == 1
    assert not state["done"]


def test_operation_deadline_includes_launch_wait_and_collect(tmp_path, monkeypatch):
    from rig_workbench.orchestrate.orchestrators import t3
    clock = [0.0]
    monkeypatch.setattr(t3.time, "monotonic", lambda: clock[0])
    client = LogicalClient(tmp_path)
    original = client.call
    durations = {"launch": 0.4, "wait": 0.3, "read": 0.2}

    def call(operation, arguments, *, timeout_s):
        assert timeout_s > 0
        result = original(operation, arguments, timeout_s=timeout_s)
        clock[0] += durations.get(operation, 0)
        return result

    client.call = call
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    spec = AgentSpec("claude", None, "generator", "implementer", "prompt", 1, {})
    handle = backend.spawn_agent(_task(tmp_path), spec)
    backend.wait(handle, timeout_s=1)
    assert backend.collect_result(handle).returncode == 0
    timeouts = {operation: timeout for operation, _, timeout in client.calls}
    assert timeouts["launch"] == 1
    assert timeouts["wait"] == pytest.approx(0.6)
    assert timeouts["read"] == pytest.approx(0.3)
    assert clock[0] == pytest.approx(0.9)


@pytest.mark.parametrize("generators,personas", [(["claude"], ["security", "correctness"]), (["claude", "codex"], ["independent"])])
def test_generators_verifier_panels_and_judge_panels_use_selected_seam(tmp_path, monkeypatch, generators, personas):
    state, path, bridge, client = _durable(tmp_path)
    state["steps"] = load_steps({"steps": [{"id": "implement", "instruction": "work", "gate": "acceptance-gate", "acceptance": ["covered"], "personas": personas}]})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    assert providers.run_loop(state, path, generators[0], "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 10,
                              generators=generators, quiet=True) == "DONE"
    launches = [arguments for operation, arguments, _ in client.calls if operation == "launch"]
    assert {call["role"] for call in launches} == {"generator", "verifier"}
    assert len([call for call in launches if call["role"] == "generator"]) == len(generators)
    assert len([call for call in launches if call["role"] == "verifier"]) >= len(personas)
    assert all(call["new_thread"] is True for call in launches)


def test_completed_t3_generator_with_failed_checks_does_not_accept(tmp_path, monkeypatch):
    state, path, bridge, client = _durable(tmp_path)
    state["steps"] = load_steps({"steps": [{"id": "implement", "instruction": "work", "checks": ["false"], "max_retries": 1}]})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    assert providers.run_loop(state, path, "claude", "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 10, quiet=True) == "ESCALATE"
    assert state["step_state"]["implement"]["checks"][0]["ok"] is False
    assert not state["done"]
    assert not any(h.get("action") == "ORCHESTRATOR_FALLBACK" for h in state["history"])


def test_completed_t3_calls_do_not_bypass_pending_human_approval(tmp_path, monkeypatch):
    state, path, bridge, client = _durable(tmp_path)
    state["steps"] = load_steps({"steps": [{"id": "implement", "instruction": "work", "gate": "acceptance-gate",
                                          "acceptance": ["covered"], "human_gate": True}]})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    monkeypatch.chdir(tmp_path)
    assert providers.run_loop(state, path, "claude", "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 10, quiet=True) == "AWAIT_APPROVAL"
    assert state["step_state"]["implement"]["approvals"] == []
    assert not state["done"]
    assert all(call["result_applied"] for call in state["orchestrator"]["invocations"].values())


def test_adaptive_targeted_reviewer_uses_selected_t3_seam(tmp_path, monkeypatch):
    state, path, bridge, client = _durable(tmp_path)
    client.result_updates = {"output": "CRITERION 1: PASS — covered\nVERDICT: PASS"}
    state["steps"] = load_steps({"steps": [
        {"id": "implement", "instruction": "work"},
        {"id": "assess", "executor": "risk-assess"},
        {"id": "review", "executor": "targeted-review", "gate": "review-gate", "acceptance": ["covered"]},
    ]})
    state["step_state"].update({sid: {"status": "pending", "retries": 0, "checks": [], "verdicts": [], "approvals": []} for sid in ("assess", "review")})
    state["execution"] = runstate.validate_executable_steps(state["steps"])
    monkeypatch.setattr(providers, "_git_diff_evidence", lambda *_args: "diff --git a/a.py b/a.py\n+pass")
    monkeypatch.setattr(providers, "_git_changed_files", lambda *_args: ["a.py"])
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    assert providers.run_loop(state, path, "claude", "codex", {"cwd": str(tmp_path), "_orchestrator_bridge": bridge}, 20, quiet=True) == "DONE"
    assert {arguments["role"] for operation, arguments, _ in client.calls if operation == "launch"} == {"generator", "verifier"}
    assert state["step_state"]["review"]["verdicts"]


@pytest.mark.parametrize("operation,interrupted", [("launch", False), ("read", False), ("launch", True)])
def test_malformed_response_or_interrupted_launch_blocks_without_native(tmp_path, operation, interrupted):
    state, path, bridge, client = _durable(tmp_path)
    original = client.call

    def call(name, arguments, *, timeout_s):
        if name == operation:
            if interrupted:
                raise KeyboardInterrupt()
            return []
        return original(name, arguments, timeout_s=timeout_s)

    client.call = call
    with pytest.raises((AgentStartUnknown, AgentConnectionLost)):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    assert runstate.load_state(path)["stopped"]["kind"] == "BLOCKED"
    with pytest.raises(OrchestratorUnavailable):
        bridge.run(_task(tmp_path, "next"), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)


def test_native_only_invocation_configuration_is_refused_before_t3_launch(tmp_path):
    state, _, bridge, client = _durable(tmp_path, preferred="t3")
    with pytest.raises(UnsupportedAgentSpec):
        providers.run_provider("claude", "generator", "prompt", {
            "cwd": str(tmp_path), "_orchestrator_bridge": bridge, "env": {}, "reuse_session": True,
        }, state=state, step_id="implement")
    assert not any(call[0] == "launch" for call in client.calls)


def test_capability_probe_cannot_persist_a_bearer_echo_as_project(tmp_path):
    client = LogicalClient(tmp_path)
    original = client.call

    def call(operation, arguments, *, timeout_s):
        response = original(operation, arguments, timeout_s=timeout_s)
        if operation == "capabilities":
            response["projects"] = ['special"secret\\value']
        return response

    client.call = call
    selected = select_orchestrator(env={"RIG_T3_MCP_URL": "http://127.0.0.1/mcp", "RIG_T3_MCP_TOKEN": 'special"secret\\value'},
                                  factory=lambda settings: T3Orchestrator(client, settings.endpoint), emit=False)
    assert selected.name == "native"
    assert selected.record()["ref"] == {}
