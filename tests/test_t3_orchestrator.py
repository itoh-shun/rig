"""Logical fake T3 contracts and failure boundaries; no live server is contacted."""
import asyncio
import copy
import json
import threading
import time
import concurrent.futures
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from rig_workbench.orchestrate import providers, runstate
from rig_workbench.orchestrate.orchestrators.base import (
    AgentConnectionLost, AgentNotStarted, AgentSpec, AgentStartUnknown, AgentStatus,
    Availability,
    OrchestratorUnavailable, TaskContext, UnsupportedAgentSpec,
)
from rig_workbench.orchestrate.orchestrators.bridge import AgentExecutionBridge, SaveCoordinator, selection_record
from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
from rig_workbench.orchestrate.orchestrators.t3 import T3Orchestrator
from rig_workbench.orchestrate.orchestrators.t3_client import McpT3Client, _Sdk
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
    assert "agent.parallel" not in selected.orchestrator.capabilities()
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


def _tool_fixture():
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures/t3/rig-t3-v1-fake.json").read_text())["tools"]


class ToolClient:
    """Public-tool fake matching the committed schema fixture; not a live API."""
    def __init__(self, logical, tools=None):
        self.logical = logical
        self.tools = _tool_fixture() if tools is None else tools
        self.tool_calls = []
        self.closed = False

    def list_tools(self, *, timeout_s):
        self.tool_calls.append("tools/list")
        return self.tools

    def call_tool(self, name, arguments, *, timeout_s):
        self.tool_calls.append(name)
        operations = {"orchestrator_capabilities": "capabilities", "t3_thread_launch": "launch",
                      "t3_thread_wait": "wait", "t3_thread_read": "read", "t3_thread_interrupt": "interrupt",
                      "t3_thread_list": "list"}
        projected = {key: value for key, value in arguments.items() if key != "timeout_ms"}
        return self.logical.call(operations[name], projected, timeout_s=timeout_s)

    def close(self):
        self.closed = True


def test_tool_bindings_pin_workspace_provider_run_and_deadline_units(tmp_path):
    from rig_workbench.orchestrate.orchestrators.t3_contract import T3_TOOL_BINDINGS, validate_tools
    tools = _tool_fixture()
    assert validate_tools(tools) == ()
    wait = T3_TOOL_BINDINGS["wait"].encoder({"thread_id": "t", "run_id": "r"}, 0.25)
    assert wait == {"thread_id": "t", "run_id": "r", "timeout_ms": 250}
    logical = LogicalClient(tmp_path)
    client = ToolClient(logical)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp", contract_verified=True)
    handle = backend.spawn_agent(_task(tmp_path), _agent())
    assert logical.calls[-1][1]["cwd"] == str(tmp_path)
    assert logical.calls[-1][1]["provider"] == "claude"
    backend.wait(handle, timeout_s=5)
    assert backend.collect_result(handle).output == "STATUS: done"
    assert client.tool_calls == ["tools/list", "orchestrator_capabilities", "t3_thread_launch", "t3_thread_wait", "t3_thread_read"]


@pytest.mark.parametrize("change", ["missing", "required", "type", "unknown_composition"])
def test_probe_requires_all_tools_and_exact_compatible_fake_schemas(tmp_path, change):
    tools = _tool_fixture()
    if change == "missing":
        del tools["t3_thread_interrupt"]
    elif change == "required":
        tools["t3_thread_launch"]["required"].append("credential")
    elif change == "type":
        tools["t3_thread_wait"]["properties"]["timeout_ms"]["type"] = "string"
    else:
        tools["t3_thread_launch"]["allOf"] = []
    client = ToolClient(LogicalClient(tmp_path), tools)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp", contract_verified=True)
    assert backend.available().ok is False
    assert backend.available().reason_code == "incompatible_schema"
    assert client.tool_calls == ["tools/list"]


def test_unverified_live_contract_is_unavailable_even_after_mcp_tools_and_capabilities(tmp_path):
    client = ToolClient(LogicalClient(tmp_path))
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    assert backend.available().ok is False
    assert backend.available().reason_code == "unverified_contract"
    assert client.tool_calls == ["tools/list", "orchestrator_capabilities"]
    assert backend.capabilities() == frozenset()


def test_probe_budget_includes_tool_listing_and_read_only_capabilities(tmp_path, monkeypatch):
    from rig_workbench.orchestrate.orchestrators import t3
    clock = [0.0]
    monkeypatch.setattr(t3.time, "monotonic", lambda: clock[0])
    client = ToolClient(LogicalClient(tmp_path))
    received = []
    original_tools, original_call = client.list_tools, client.call_tool

    def listing(*, timeout_s):
        received.append(timeout_s)
        clock[0] += 2
        return original_tools(timeout_s=timeout_s)

    def call(name, arguments, *, timeout_s):
        received.append(timeout_s)
        clock[0] += 4
        return original_call(name, arguments, timeout_s=timeout_s)

    client.list_tools, client.call_tool = listing, call
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp", contract_verified=True)
    assert received == [5, 3]
    assert backend.available().ok is False
    assert client.tool_calls == ["tools/list", "orchestrator_capabilities"]


def test_pagination_collects_complete_output_only_from_the_pinned_run(tmp_path):
    client = LogicalClient(tmp_path)
    original = client.call

    def call(operation, arguments, *, timeout_s):
        response = original(operation, arguments, timeout_s=timeout_s)
        if operation == "read":
            if "cursor" not in arguments:
                response.update(output="part1 ", complete=False, truncated=True, next_cursor="page2")
            else:
                response.update(output="part2", complete=True, truncated=False)
        return response

    client.call = call
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    handle = backend.spawn_agent(_task(tmp_path), _agent())
    backend.wait(handle, timeout_s=5)
    assert backend.collect_result(handle).output == "part1 part2"
    assert [arguments["run_id"] for operation, arguments, _ in client.calls if operation == "read"] == ["1", "1"]


@pytest.mark.parametrize("second", [{"phase": "failed"}, {"returncode": 1}])
def test_result_pages_cannot_change_terminal_status_or_returncode(tmp_path, second):
    client = LogicalClient(tmp_path)
    original = client.call
    def call(operation, arguments, *, timeout_s):
        response = original(operation, arguments, timeout_s=timeout_s)
        if operation == "read":
            if "cursor" not in arguments:
                response.update(output="part1", complete=False, truncated=True, next_cursor="page2")
            else:
                response.update(second)
        return response
    client.call = call
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    handle = backend.spawn_agent(_task(tmp_path), _agent())
    backend.wait(handle, timeout_s=5)
    with pytest.raises(AgentConnectionLost, match="inconsistent_result_pages"):
        backend.collect_result(handle)


def test_interrupted_collection_cancels_known_run_and_retains_blocked_reference(tmp_path):
    state, path, bridge, client = _durable(tmp_path)
    original = client.call
    def call(operation, arguments, *, timeout_s):
        if operation == "read":
            raise KeyboardInterrupt()
        return original(operation, arguments, timeout_s=timeout_s)
    client.call = call
    with pytest.raises(AgentConnectionLost, match="agent_interrupted"):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    saved = runstate.load_state(path)
    assert saved["stopped"]["kind"] == "BLOCKED"
    assert saved["orchestrator"]["invocations"]["call-1"]["cancel_state"] == "cancelled"
    assert saved["orchestrator"]["ref"]["t3"]["threads"]["call-1"]["run_id"] == "1"


def _fake_sdk(events, *, initialize_delay=0, operation_delay=0):
    def event(name):
        events.append((name, threading.get_ident(), id(asyncio.get_running_loop()), id(asyncio.current_task())))

    class Http:
        def __init__(self, **kwargs):
            self.options = kwargs
            assert kwargs["follow_redirects"] is False
            assert kwargs["headers"] == {"Authorization": "Bearer secret"}

        async def __aenter__(self):
            event("http-enter")
            return self

        async def __aexit__(self, *_args):
            event("http-exit")

    @asynccontextmanager
    async def transport(endpoint, *, http_client, terminate_on_close):
        assert endpoint == "http://127.0.0.1/mcp"
        assert terminate_on_close is True
        event("transport-enter")
        try:
            yield ("read", "write", lambda: "session")
        finally:
            event("transport-exit")

    class Session:
        def __init__(self, read, write):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            event("session-enter")
            return self

        async def __aexit__(self, *_args):
            event("session-exit")

        async def initialize(self):
            event("initialize")
            await asyncio.sleep(initialize_delay)

        async def list_tools(self, *, params=None):
            event("list-tools")
            await asyncio.sleep(operation_delay)
            if params is None:
                return SimpleNamespace(tools=[SimpleNamespace(name="first", inputSchema={"type": "object"})], nextCursor="page2")
            assert params.cursor == "page2"
            return SimpleNamespace(tools=[SimpleNamespace(name="second", inputSchema={"type": "object"})], nextCursor=None)

        async def call_tool(self, name, *, arguments, read_timeout_seconds):
            event("call-tool")
            assert read_timeout_seconds.total_seconds() > 0
            await asyncio.sleep(operation_delay)
            return SimpleNamespace(isError=False, structuredContent={"name": name, **arguments}, content=[])

    return _Sdk(Session, transport, Http, lambda **kwargs: SimpleNamespace(**kwargs))


def test_mcp_client_uses_one_owned_loop_and_same_task_closes_all_contexts():
    events = []
    client = McpT3Client("http://127.0.0.1/mcp", "secret", sdk_factory=lambda: _fake_sdk(events))
    try:
        assert client.list_tools(timeout_s=1) == {"first": {"type": "object"}, "second": {"type": "object"}}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: client.call_tool("tool", {"index": i}, timeout_s=1), range(8)))
        assert [result["index"] for result in results] == list(range(8))
    finally:
        client.close()
        client.close()
    assert not client._thread.is_alive()
    assert len({thread for _, thread, _, _ in events}) == 1
    assert len({loop for _, _, loop, _ in events}) == 1
    for context in ("http", "transport", "session"):
        opening = next(item for item in events if item[0] == f"{context}-enter")
        closing = next(item for item in events if item[0] == f"{context}-exit")
        assert opening[1:] == closing[1:]
    assert not any(thread.name == "rig-t3-mcp" for thread in threading.enumerate())


def test_initialize_timeout_closes_owned_contexts_and_does_not_leak_thread():
    events = []
    started = time.monotonic()
    with pytest.raises(RuntimeError) as error:
        McpT3Client("http://127.0.0.1/mcp", "secret", timeout_s=0.02,
                    sdk_factory=lambda: _fake_sdk(events, initialize_delay=10))
    assert "secret" not in str(error.value)
    assert time.monotonic() - started < 0.5
    # Exhausted probe deadlines return immediately; the owned task finishes cleanup.
    for thread in threading.enumerate():
        if thread.name == "rig-t3-mcp":
            thread.join(timeout=0.2)
    assert [name for name, *_ in events][-3:] == ["session-exit", "transport-exit", "http-exit"]
    assert not any(thread.name == "rig-t3-mcp" for thread in threading.enumerate())


def test_operation_timeout_cancels_request_but_session_can_close():
    events = []
    client = McpT3Client("http://127.0.0.1/mcp", "secret", sdk_factory=lambda: _fake_sdk(events, operation_delay=10))
    try:
        with pytest.raises(RuntimeError) as error:
            client.call_tool("slow", {}, timeout_s=0.02)
        assert "secret" not in str(error.value)
    finally:
        client.close()
    assert not client._thread.is_alive()


def test_sdk_import_is_delayed_until_client_factory_and_missing_sdk_is_reported(monkeypatch):
    from rig_workbench.orchestrate.orchestrators import t3_client
    monkeypatch.setattr(t3_client, "_sdk_factory", lambda: (_ for _ in ()).throw(ImportError("secret")))
    with pytest.raises(RuntimeError, match="mcp_sdk_unavailable") as error:
        McpT3Client("http://127.0.0.1/mcp", "secret")
    assert "secret" not in str(error.value)


def test_sdk_tool_errors_and_malformed_content_do_not_echo_payloads():
    for response in (SimpleNamespace(isError=True),
                     SimpleNamespace(isError=False, structuredContent=None, content=[]),
                     SimpleNamespace(isError=False, structuredContent=["secret"], content=[])):
        with pytest.raises(ValueError) as error:
            McpT3Client._decode_result(response)
        assert "secret" not in str(error.value)


def test_keyboard_interrupt_from_sync_future_reaches_the_agent_bridge(monkeypatch):
    from rig_workbench.orchestrate.orchestrators import t3_client
    events = []
    client = McpT3Client("http://127.0.0.1/mcp", "secret", sdk_factory=lambda: _fake_sdk(events))
    original_future = concurrent.futures.Future
    class InterruptedFuture(original_future):
        def result(self, timeout=None):
            raise KeyboardInterrupt()
    try:
        monkeypatch.setattr(t3_client.concurrent.futures, "Future", InterruptedFuture)
        with pytest.raises(KeyboardInterrupt):
            client.call_tool("tool", {}, timeout_s=1)
    finally:
        client.close()
    assert not client._thread.is_alive()


def test_cleanup_never_adds_a_new_wait_budget_after_probe_deadline():
    events = []
    sdk = _fake_sdk(events)
    original_session = sdk.session
    class SlowCloseSession(original_session):
        async def __aexit__(self, *args):
            await asyncio.sleep(10)
            return await super().__aexit__(*args)
    slow = _Sdk(SlowCloseSession, sdk.transport, sdk.http_client, sdk.pagination)
    client = McpT3Client("http://127.0.0.1/mcp", "secret", sdk_factory=lambda: slow)
    started = time.monotonic()
    client.close(timeout_s=0.02)
    assert time.monotonic() - started < 0.15
    client._thread.join(timeout=0.3)
    assert not client._thread.is_alive()


def test_endpoint_guard_removes_bearer_and_refuses_other_endpoint():
    events, instances = [], []
    sdk = _fake_sdk(events)
    def http_client(**kwargs):
        instance = sdk.http_client(**kwargs)
        instances.append(instance)
        return instance
    wrapped = _Sdk(sdk.session, sdk.transport, http_client, sdk.pagination)
    client = McpT3Client("http://127.0.0.1/mcp", "secret", sdk_factory=lambda: wrapped)
    try:
        request = SimpleNamespace(url="https://another.example/mcp", headers={"Authorization": "Bearer secret"})
        guard = instances[0].options["event_hooks"]["request"][0]
        with pytest.raises(RuntimeError, match="mcp_endpoint_changed"):
            asyncio.run(guard(request))
        assert "Authorization" not in request.headers
    finally:
        client.close()


def test_sdk_factory_time_counts_toward_probe_deadline_and_never_opens_after_timeout():
    events = []
    def factory():
        time.sleep(0.4)
        return _fake_sdk(events)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        McpT3Client("http://127.0.0.1/mcp", "secret", timeout_s=0.02, sdk_factory=factory)
    # The factory takes 0.4s; returning well before that proves the deadline did not wait for
    # it, and the wide margin keeps a loaded machine from reading scheduling delay as a wait.
    assert time.monotonic() - started < 0.25
    for thread in threading.enumerate():
        if thread.name == "rig-t3-mcp":
            thread.join(timeout=2)
    assert events == []
    assert not any(thread.name == "rig-t3-mcp" for thread in threading.enumerate())


@pytest.mark.parametrize("failure", ["unconfigured", "sdk_unavailable", "authentication_failed", "probe_timeout"])
@pytest.mark.parametrize("preferred,fallback", [("auto", "native"), ("auto", "none"), ("t3", "native")])
def test_probe_failure_falls_back_only_when_auto_allows_it(failure, preferred, fallback, capsys):
    calls = []

    def factory(settings):
        calls.append(settings)
        if failure == "sdk_unavailable":
            raise RuntimeError("mcp_sdk_unavailable")
        return SimpleNamespace(available=lambda: Availability(False, failure, "Connection unavailable"))

    env = {} if failure == "unconfigured" else {
        "RIG_T3_MCP_URL": "http://127.0.0.1/mcp", "RIG_T3_MCP_TOKEN": "secret",
    }
    if preferred == "auto" and fallback == "native":
        selected = select_orchestrator({"fallback": fallback}, cli=preferred, env=env, factory=factory)
        assert selected.name == "native"
        assert selected.availability.reason_code == failure
        assert "falling back to native" in capsys.readouterr().err
    else:
        with pytest.raises(OrchestratorUnavailable, match=failure):
            select_orchestrator({"fallback": fallback}, cli=preferred, env=env, factory=factory)
    assert len(calls) == (0 if failure == "unconfigured" else 1)


@pytest.mark.parametrize("mismatch", ["provider", "model", "cwd", "role", "constraint", "ambiguous_target"])
def test_unverified_agent_identity_workspace_and_constraints_are_refused_before_launch(tmp_path, mismatch):
    client = LogicalClient(tmp_path)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    task, spec = _task(tmp_path), _agent()
    if mismatch == "provider":
        spec = _agent(provider="unknown-provider")
    elif mismatch == "model":
        spec = AgentSpec("claude", "unknown-model", "generator", "implementer", "prompt", 5, {})
    elif mismatch == "cwd":
        task = _task(tmp_path / "different-checkout")
    elif mismatch == "role":
        spec = _agent("scheduler")
    elif mismatch == "constraint":
        spec = AgentSpec("claude", None, "verifier", "reviewer", "prompt", 5, {"network": False})
    else:
        backend._catalog["another-claude"] = {**backend._catalog["claude"], "instance_id": "another-claude"}
    with pytest.raises(UnsupportedAgentSpec):
        backend.spawn_agent(task, spec)
    assert [operation for operation, _, _ in client.calls] == ["capabilities"]


@pytest.mark.parametrize("failure", ["unknown", "disconnected", "wrong_run"])
def test_timeout_with_cancel_failure_keeps_the_external_run_unresolved(tmp_path, failure, monkeypatch):
    state, path, bridge, client = _durable(tmp_path)
    monkeypatch.setattr(bridge.backend, "wait", lambda *_args, **_kwargs: AgentStatus("running"))
    original = client.call

    def call(operation, arguments, *, timeout_s):
        if operation == "interrupt":
            client.calls.append((operation, copy.deepcopy(arguments), timeout_s))
            if failure == "disconnected":
                raise OSError("secret transport detail")
            return {**arguments, "state": "unknown"} if failure == "unknown" else {**arguments, "run_id": "another", "state": "cancelled"}
        return original(operation, arguments, timeout_s=timeout_s)

    client.call = call
    with pytest.raises(AgentConnectionLost, match="agent_timeout_unresolved"):
        bridge.run(_task(tmp_path), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)
    saved = runstate.load_state(path)
    assert saved["orchestrator"]["invocations"]["call-1"]["cancel_state"] == "unknown"
    assert saved["orchestrator"]["ref"]["t3"]["threads"]["call-1"] == {"thread_id": "thread-1", "run_id": "1"}
    assert saved["stopped"]["kind"] == "BLOCKED"
    assert not saved["orchestrator"]["invocations"]["call-1"]["result_applied"]
    assert not any(operation == "read" for operation, _, _ in client.calls)
    with pytest.raises(OrchestratorUnavailable):
        bridge.run(_task(tmp_path, "next"), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: None)


@pytest.mark.parametrize("state_name", ["ready", "orchestrator_unavailable", "agent_missing", "unknown"])
def test_t3_reconnect_distinguishes_four_states_without_launching_or_collecting(tmp_path, state_name):
    client = LogicalClient(tmp_path)
    backend = T3Orchestrator(client, "http://127.0.0.1/mcp")
    handle = backend.spawn_agent(_task(tmp_path), _agent())
    calls_before = len(client.calls)
    if state_name == "orchestrator_unavailable":
        backend._availability = Availability(False, "authentication_failed", "Unavailable")
    else:
        original = client.call

        def call(operation, arguments, *, timeout_s):
            response = original(operation, arguments, timeout_s=timeout_s)
            if state_name == "agent_missing":
                return {"missing": True}
            if state_name == "unknown":
                response["run_id"] = "unrelated"
            return response

        client.call = call
    result = backend.resume(handle)
    assert result.state == state_name
    assert result.handle == handle
    assert [operation for operation, _, _ in client.calls[calls_before:]] == ([] if state_name == "orchestrator_unavailable" else ["read"])
    assert backend._results == {}


def test_mixed_t3_and_native_fallback_calls_keep_distinct_invocation_records(tmp_path):
    state, path, bridge, client = _durable(tmp_path, failure="prestart")
    attempts = []

    def native(*_args):
        return 0, "native output"

    assert bridge.run(_task(tmp_path, "fallback"), _agent(), native_dispatch=native, record_attempt=lambda: attempts.append(1)) == (0, "native output")
    client.failure = None
    assert bridge.run(_task(tmp_path, "remote"), _agent(), native_dispatch=lambda *_args: pytest.fail("native launched"), record_attempt=lambda: attempts.append(1)) == (0, "STATUS: done")
    saved = runstate.load_state(path)["orchestrator"]
    assert saved["name"] == "t3"
    assert {key: call["orchestrator"] for key, call in saved["invocations"].items()} == {"fallback": "native", "remote": "t3"}
    assert set(saved["ref"]["t3"]["threads"]) == {"remote"}
    assert len(attempts) == 3
    assert [event["invocation_id"] for event in state["history"] if event["action"] == "ORCHESTRATOR_FALLBACK"] == ["fallback"]


@pytest.mark.parametrize("review_model", [None, "writer-model", "independent-model"])
def test_t3_threads_preserve_alias_separation_of_duty_and_explicit_model_requirements(tmp_path, monkeypatch, review_model):
    state, path, bridge, client = _durable(tmp_path)
    steps = load_steps({"steps": [
        {"id": "write", "instruction": "missing-legacy-write"},
        {"id": "review", "instruction": "parallel-review", "gate": "acceptance-gate",
         "personas": ["security-reviewer"], "policies": ["independent-verification"],
         "output_contract": "review-verdict", **({"verifier_model": review_model} if review_model else {})},
    ]})
    original_metadata = state["orchestrator"]
    state.clear()
    state.update(runstate.new_state("writing", steps, "write"))
    state["orchestrator"] = original_metadata
    target = bridge.backend._catalog["claude"]
    bridge.backend._catalog = {model: {**target, "instance_id": model, "model": model}
                              for model in ("writer-model", "independent-model")}
    client.result_updates = {"output": "判定: APPROVE"}
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    result = providers.run_loop(state, path, "rig", "claude", {
        "cwd": str(tmp_path), "model": "writer-model", "_orchestrator_bridge": bridge,
    }, 10, quiet=True)
    launches = [arguments for operation, arguments, _ in client.calls if operation == "launch"]
    if review_model == "independent-model":
        assert result == "DONE"
        assert [(call["role"], call["model"]) for call in launches] == [
            ("generator", "writer-model"), ("verifier", "independent-model")]
    else:
        assert result == "BLOCKED"
        assert [(call["role"], call["model"]) for call in launches] == [("generator", "writer-model")]
        assert "effective backend" in state["stopped"]["reason"]
    assert all(call["provider"] == "claude" and call["new_thread"] for call in launches)
