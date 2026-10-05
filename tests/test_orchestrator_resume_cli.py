"""The public resume command reports durable identities and never applies T3 results."""
import copy
import os

import pytest

from rig_workbench.orchestrate import commands, runstate
from rig_workbench.orchestrate.orchestrators.base import (
    AgentHandle, AgentStatus, Availability, OrchestratorUnavailable, ReconnectResult,
)
from rig_workbench.orchestrate.orchestrators.bridge import selection_record
from rig_workbench.orchestrate.orchestrators.selection import reconnect_recorded


@pytest.fixture(autouse=True)
def clear_t3_environment(monkeypatch):
    """Tests opt in to T3 settings instead of inheriting the developer's credentials."""
    for key in tuple(os.environ):
        if key.startswith("RIG_T3_"):
            monkeypatch.delenv(key, raising=False)


def test_t3_environment_is_isolated():
    assert not any(key.startswith("RIG_T3_") for key in os.environ)


class Environment:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _state(tmp_path, step_factory, *, phase="running", orchestrator="t3", applied=False):
    state = runstate.new_state("resume-orchestrator", [step_factory(id="implement", checks=["true"])], None)
    state["step_state"]["implement"]["status"] = "running"
    state["orchestrator"] = selection_record("t3", selected_by="cli", preferred="t3", ref={"t3": {
        "endpoint": "http://localhost/mcp", "project_id": "project",
        "contract_version": "rig-t3-v1", "threads": {"call-1": {"thread_id": "thread-1", "run_id": "run-1"}},
    }})
    state["orchestrator"]["invocations"]["call-1"] = {
        "step_id": "implement", "attempt": 1, "role": "generator", "persona": "implementer",
        "provider": "claude", "model": None, "orchestrator": orchestrator,
        "phase": phase, "result_applied": applied,
    }
    path = tmp_path / "state.json"
    runstate.save_state(state, path)
    return state, path


@pytest.mark.parametrize("flag", [["--orchestrator", "native"], ["--orchestrator=t3"], ["--orchestrator"]])
def test_resume_refuses_an_orchestrator_change_before_reading_state(flag, capsys):
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume(["missing.json", *flag])
    assert stopped.value.code == 2
    assert "cannot change the recorded orchestrator" in capsys.readouterr().out


@pytest.mark.parametrize("reconnect,phase", [
    ("ready", "running"), ("ready", "completed"), ("ready", "failed"),
    ("orchestrator_unavailable", "running"), ("agent_missing", "running"), ("unknown", "starting"),
])
def test_unresolved_t3_resume_reports_four_states_and_ids_without_checks_or_writes(
    tmp_path, step_factory, monkeypatch, capsys, reconnect, phase,
):
    state, path = _state(tmp_path, step_factory, phase=phase)
    before = path.read_bytes()
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("checks ran beside an unresolved agent"))
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_k: pytest.fail("resume started new agents"))
    monkeypatch.setattr(commands, "reconnect_recorded", lambda *_a, **_k: {
        "call-1": ReconnectResult(reconnect, AgentHandle("t3", "call-1", {
            "thread_id": "thread-1", "run_id": "run-1"}), AgentStatus(phase) if phase != "starting" else None, "diagnostic")})
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2
    output = capsys.readouterr().out
    assert f"call-1: {reconnect}" in output
    assert "thread_id=thread-1" in output and "run_id=run-1" in output
    assert path.read_bytes() == before and runstate.load_state(path) == state


@pytest.mark.parametrize("orchestrator,ref,expected", [
    ("t3", {"thread_id": "thread-1", "run_id": "run-1"}, "orchestrator_unavailable"),
    ("t3", {"thread_id": "thread-1"}, "unknown"),
    ("native", {}, "unknown"),
])
def test_resume_reports_unavailable_namespace_and_unknown_attempts_without_fallback(
    tmp_path, step_factory, monkeypatch, capsys, orchestrator, ref, expected,
):
    state, path = _state(tmp_path, step_factory, orchestrator=orchestrator)
    state["orchestrator"]["ref"]["t3"]["threads"]["call-1"] = ref
    runstate.save_state(state, path)
    before = path.read_bytes()
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    calls = []

    def reconnect(*args, **kwargs):
        calls.append(kwargs)
        return reconnect_recorded(*args, **kwargs, env=Environment(),
                                  factory=lambda _: pytest.fail("unsafe namespace was probed"))

    monkeypatch.setattr(commands, "reconnect_recorded", reconnect)
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("unresolved checks"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2
    assert f"call-1: {expected}" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert calls == [{"report_errors": True}]


def test_applied_t3_invocations_keep_verify_first_resume_without_reconnecting(tmp_path, step_factory, monkeypatch, capsys):
    _, path = _state(tmp_path, step_factory, phase="completed", applied=True)
    checks = []
    monkeypatch.setenv("RIG_T3_MCP_URL", "http://localhost/mcp")
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    monkeypatch.setattr(commands, "_run_checks", lambda values: checks.append(values) or [{"cmd": "true", "ok": True, "rc": 0}])
    commands.cmd_resume([str(path)])
    assert checks == [["true"]]
    assert "re-verify" in capsys.readouterr().out
    assert runstate.load_state(path)["orchestrator"]["invocations"]["call-1"]["result_applied"] is True


def test_confirmed_not_started_resume_clears_only_orchestrator_stop_and_verifies(
    tmp_path, step_factory, monkeypatch, capsys,
):
    state, path = _state(tmp_path, step_factory, phase="not_started")
    state["stopped"] = {"kind": "BLOCKED", "source": "orchestrator", "invocation_id": "call-1",
                        "reason": "launch_failed", "at": "implement"}
    runstate.save_state(state, path)
    monkeypatch.setenv("RIG_T3_MCP_URL", "http://localhost/mcp")
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    checks = []
    monkeypatch.setattr(commands, "_run_checks", lambda values: checks.append(values) or [
        {"cmd": "true", "ok": True, "rc": 0}])
    commands.cmd_resume([str(path)])
    assert checks == [["true"]]
    assert "re-verify" in capsys.readouterr().out
    saved = runstate.load_state(path)
    assert saved["stopped"] is None
    assert saved["orchestrator"]["invocations"]["call-1"]["phase"] == "not_started"


@pytest.mark.parametrize("phase", ["starting", "unknown"])
def test_uncertain_launch_resume_keeps_stop_and_skips_verification(
    tmp_path, step_factory, monkeypatch, phase,
):
    state, path = _state(tmp_path, step_factory, phase=phase)
    state["stopped"] = {"kind": "BLOCKED", "source": "orchestrator", "invocation_id": "call-1",
                        "reason": "launch_unknown", "at": "implement"}
    runstate.save_state(state, path)
    before = path.read_bytes()
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    monkeypatch.setattr(commands, "reconnect_recorded", lambda *_a, **_k: {
        "call-1": ReconnectResult("unknown", None, None, "launch_unknown")})
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("uncertain launch ran checks"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2 and path.read_bytes() == before


@pytest.mark.parametrize("manifest", [{"preferred": "native"}, {"preferred": "auto"}, {"preferred": "t3"}])
def test_reconnect_uses_saved_identity_without_reselection(tmp_path, step_factory, manifest):
    state, _ = _state(tmp_path, step_factory)
    events = []

    class Backend:
        project_id = "project"

        def available(self):
            return Availability(True, "compatible", "compatible")

        def resume(self, handle):
            events.append(handle)
            return ReconnectResult("ready", handle, AgentStatus("running"), "compatible")

    before = copy.deepcopy(state)
    env = Environment(RIG_T3_MCP_URL="http://localhost/mcp", RIG_T3_MCP_TOKEN="new-credential")
    assert reconnect_recorded(state, manifest, env=env, factory=lambda _: Backend())["call-1"].state == "ready"
    assert events[0].ref == {"thread_id": "thread-1", "run_id": "run-1"}
    assert state == before


@pytest.mark.parametrize("values,reason", [
    ({"RIG_T3_MCP_URL": "http://localhost/other"}, "orchestrator_namespace_mismatch"),
    ({"RIG_T3_PROJECT_ID": "other"}, "orchestrator_project_mismatch"),
    ({"RIG_T3_MCP_TOKEN": ""}, "unconfigured"),
])
def test_reconnect_rejects_changed_endpoint_project_and_unavailable_backend(tmp_path, step_factory, values, reason):
    state, _ = _state(tmp_path, step_factory)
    env = Environment(**dict({"RIG_T3_MCP_URL": "http://localhost/mcp", "RIG_T3_MCP_TOKEN": "secret"}, **values))
    before = copy.deepcopy(state)
    with pytest.raises(OrchestratorUnavailable) as stopped:
        reconnect_recorded(state, env=env, factory=lambda _: pytest.fail("unsafe namespace was probed"))
    assert stopped.value.reason_code == reason
    assert state == before


@pytest.mark.parametrize("threads,expected", [
    ([], "unknown"),
    ({"call-1": {"thread_id": []}}, "unknown"),
    ({"call-1": {"thread_id": "thread-1", "run_id": "run-1"}}, "orchestrator_unavailable"),
])
def test_shared_reconnect_failure_reporting_handles_unsafe_or_missing_references(
    tmp_path, step_factory, threads, expected,
):
    state, _ = _state(tmp_path, step_factory)
    state["orchestrator"]["ref"]["t3"]["threads"] = threads
    before = copy.deepcopy(state)
    report = reconnect_recorded(state, env=Environment(), report_errors=True,
                               factory=lambda _: pytest.fail("unsafe namespace was probed"))["call-1"]
    assert report.state == expected and report.detail == "orchestrator_namespace_mismatch"
    assert state == before


def test_resume_malformed_metadata_blocks_without_modifying_state(tmp_path, step_factory, monkeypatch):
    import json
    state, path = _state(tmp_path, step_factory)
    state["orchestrator"]["schema_version"] = 999
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("malformed state ran checks"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2 and path.read_bytes() == before


@pytest.mark.parametrize("mutation,reason", [
    (lambda state: state["orchestrator"]["ref"]["t3"].update(endpoint="http://localhost/other"), "namespace_mismatch"),
    (lambda state: state["orchestrator"]["ref"]["t3"].update(project_id=[]), "malformed_orchestrator_namespace"),
    (lambda state: state["orchestrator"]["ref"]["t3"].update(contract_version="future"), "namespace_mismatch"),
    (lambda state: state["orchestrator"]["ref"]["t3"].update(threads=[]), "malformed_orchestrator_namespace"),
])
def test_applied_t3_results_do_not_allow_malformed_or_changed_namespace_checks(
    tmp_path, step_factory, monkeypatch, capsys, mutation, reason,
):
    import json
    state, path = _state(tmp_path, step_factory, phase="completed", applied=True)
    mutation(state)
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    monkeypatch.setenv("RIG_T3_MCP_URL", "http://localhost/mcp")
    monkeypatch.setattr(commands, "load_manifest", lambda **_: {})
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("bad namespace ran checks"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2
    assert reason in capsys.readouterr().out and path.read_bytes() == before


def test_applied_t3_results_do_not_probe_the_optional_backend(tmp_path, step_factory):
    state, _ = _state(tmp_path, step_factory, phase="completed", applied=True)
    env = Environment(RIG_T3_MCP_URL="http://localhost/mcp")
    assert reconnect_recorded(state, env=env, factory=lambda _: pytest.fail("resolved state probed T3")) == {}


@pytest.mark.parametrize("phase", ["running", "starting", "unknown"])
def test_applied_marker_on_nonterminal_invocation_blocks_checks(tmp_path, step_factory, monkeypatch, phase):
    import json
    state, path = _state(tmp_path, step_factory, phase="completed", applied=True)
    state["orchestrator"]["invocations"]["call-1"]["phase"] = phase
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    monkeypatch.setattr(commands, "_run_checks", lambda _: pytest.fail("contradictory metadata ran checks"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_resume([str(path)])
    assert stopped.value.code == 2 and path.read_bytes() == before
