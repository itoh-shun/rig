"""Durable invocation records never imply Rig acceptance or automatic resume."""
import concurrent.futures
import copy
import hashlib
import json
import os

import pytest

from rig_workbench.orchestrate import runstate, providers
from rig_workbench.orchestrate.orchestrators.base import (
    AgentConnectionLost, AgentHandle, AgentNotStarted, AgentResult, AgentSpec,
    AgentStartUnknown, AgentStatus, Availability,
    OrchestratorUnavailable, ReconnectResult, TaskContext,
)
from rig_workbench.orchestrate.orchestrators.bridge import (
    AgentExecutionBridge, SaveCoordinator, read_metadata, reconnect_invocations,
    selection_record, unresolved_invocations,
)


def _state(name="t3"):
    state = runstate.new_state("test", [{"id": "implement", "instruction": "work", "gate": None}], "goal")
    state["orchestrator"] = selection_record(name, capabilities=("agent.run",),
                                             ref={"t3": {"endpoint": "http://127.0.0.1/mcp", "threads": {}}} if name == "t3" else {})
    return state


def _call(invocation="call-1"):
    return TaskContext("run", "implement", invocation, 1, "/workspace")


def _agent():
    return AgentSpec("mock", None, "generator", "implementer", "prompt", 5, {})


class FakeDurable:
    name = "t3"

    def __init__(self, path):
        self.path = path
        self.events = []
        self.specs = {}

    def capabilities(self):
        return frozenset({"agent.run"})

    def available(self):
        return Availability(True, "fake", "fake")

    def spawn_agent(self, task, spec):
        saved = json.loads(self.path.read_text())
        assert saved["orchestrator"]["invocations"][task.invocation_id]["phase"] == "starting"
        self.events.append("spawn")
        self.specs[task.invocation_id] = spec
        return AgentHandle("t3", task.invocation_id, {"thread_id": "thread-" + task.invocation_id, "run_id": "run-" + task.invocation_id})

    def wait(self, handle, *, timeout_s):
        saved = json.loads(self.path.read_text())
        assert saved["orchestrator"]["ref"]["t3"]["threads"][handle.invocation_id] == handle.ref
        self.events.append("wait")
        return AgentStatus("completed")

    def collect_result(self, handle):
        spec = self.specs[handle.invocation_id]
        return AgentResult(0, "STATUS: done", spec.provider, spec.model)

    def resume(self, handle):
        self.events.append("resume")
        return ReconnectResult("ready", handle, AgentStatus("completed"), "fake ready")


def _bridge(state, path, save=runstate.save_state):
    backend = FakeDurable(path)
    return AgentExecutionBridge(backend, coordinator=SaveCoordinator(state, path, save)), backend


def test_selected_identity_and_external_thread_ids_survive_round_trip(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    bridge, _ = _bridge(state, path)
    assert bridge.run(_call(), _agent(), native_dispatch=lambda *_args: pytest.fail("native ran"), record_attempt=lambda: None) == (0, "STATUS: done")
    saved = runstate.load_state(path)
    assert saved["orchestrator"] == state["orchestrator"]
    assert saved["orchestrator"]["invocations"]["call-1"]["result_applied"] is False
    result = saved["orchestrator"]["invocations"]["call-1"]
    payload = open(result["output_path"], "rb").read()
    assert hashlib.sha256(payload).hexdigest() == result["output_sha256"]


def test_orchestrator_metadata_does_not_change_execution_policy(tmp_path):
    state = _state()
    execution = copy.deepcopy(state["execution"])
    path = tmp_path / "state.json"
    runstate.save_state(state, path)
    assert runstate.load_state(path)["execution"] == execution
    assert runstate.enforce_executable_state(state) == execution


def test_missing_orchestrator_means_legacy_native_but_malformed_data_blocks(tmp_path):
    state = _state("native")
    del state["orchestrator"]
    path = tmp_path / "state.json"
    runstate.save_state(state, path)
    before = path.read_bytes()
    loaded = runstate.load_state(path)
    assert read_metadata(loaded)["selected_by"] == "legacy"
    assert "orchestrator" not in loaded
    assert path.read_bytes() == before
    state["orchestrator"] = {"schema_version": 999}
    runstate.save_state(state, path)
    loaded = runstate.load_state(path)
    assert loaded["stopped"]["source"] == "orchestrator"
    with pytest.raises(OrchestratorUnavailable):
        read_metadata(loaded)


def test_launch_intent_and_handle_are_durable_before_execution_continues(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    snapshots = []

    def save(current, target):
        snapshots.append(copy.deepcopy(current))
        runstate.save_state(current, target)

    bridge, backend = _bridge(state, path, save)
    bridge.run(_call(), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None)
    assert [snapshot["orchestrator"]["invocations"]["call-1"]["phase"] for snapshot in snapshots] == ["intent", "starting", "running", "completed"]
    assert backend.events == ["spawn", "wait"]


def test_snapshot_failure_stops_before_launch_and_cannot_retry(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    bridge, backend = _bridge(state, path, lambda *_args: (_ for _ in ()).throw(OSError("secret detail")))
    for invocation in ("call-1", "call-2"):
        with pytest.raises(OrchestratorUnavailable) as stopped:
            bridge.run(_call(invocation), _agent(), native_dispatch=lambda *_args: pytest.fail("native ran"), record_attempt=lambda: None)
        assert "secret detail" not in str(stopped.value)
    assert backend.events == []


@pytest.mark.parametrize("preferred,fallback", [("t3", "native"), ("auto", "none")])
@pytest.mark.parametrize("error_type,phase,unresolved", [
    (AgentNotStarted, "not_started", False),
    (AgentStartUnknown, "unknown", True),
    (AgentConnectionLost, "unknown", True),
])
def test_failed_launch_is_unresolved_only_if_execution_may_have_started(
    tmp_path, preferred, fallback, error_type, phase, unresolved,
):
    state = _state()
    state["orchestrator"].update(preferred=preferred, fallback=fallback)
    path = tmp_path / "state.json"
    bridge, backend = _bridge(state, path)

    def reject_launch(*_args):
        raise error_type("launch_failed")

    backend.spawn_agent = reject_launch
    with pytest.raises(error_type):
        bridge.run(_call(), _agent(), native_dispatch=lambda *_args: pytest.fail("fallback ran"),
                   record_attempt=lambda: None)
    saved = runstate.load_state(path)
    call = saved["orchestrator"]["invocations"]["call-1"]
    assert call["phase"] == phase and call["result_applied"] is False
    assert bool(unresolved_invocations(saved)) is unresolved
    if not unresolved:
        assert reconnect_invocations(saved, backend) == {}


def test_consumer_and_exact_invocation_marker_share_one_snapshot(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    snapshots = []

    def save(current, target):
        snapshots.append(copy.deepcopy(current))
        runstate.save_state(current, target)

    bridge, _ = _bridge(state, path, save)
    bridge.run(_call("old-call"), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None)

    def consume(working, cfg):
        working["step_state"]["implement"]["status"] = "consumer-partial"
        providers.run_provider("mock", "generator", "prompt", cfg, state=working, step_id="implement")
        working["step_state"]["implement"]["status"] = "consumer-finished"
        working["history"].append({"action": "EXEC", "step": "implement"})

    original_st = state["step_state"]["implement"]
    bridge.consume_transition(state, "implement", {"_orchestrator_bridge": bridge}, consume)
    assert original_st["status"] == "consumer-finished"
    assert all(snapshot["step_state"]["implement"]["status"] != "consumer-partial" for snapshot in snapshots)
    assert state["orchestrator"]["invocations"]["old-call"]["result_applied"] is False
    applied = [call for key, call in state["orchestrator"]["invocations"].items() if key != "old-call"]
    assert len(applied) == 1 and applied[0]["result_applied"] is True
    assert snapshots[-1]["step_state"]["implement"]["status"] == "consumer-finished"
    assert snapshots[-1]["history"][-1]["action"] == "EXEC"


def test_concurrent_handles_keep_distinct_invocation_records(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    bridge, _ = _bridge(state, path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: bridge.run(_call(f"call-{i}"), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None), range(8)))
    assert len(runstate.load_state(path)["orchestrator"]["invocations"]) == 8
    assert len(state["orchestrator"]["ref"]["t3"]["threads"]) == 8


def test_resume_reconnects_without_collecting_applying_or_restarting(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    bridge, backend = _bridge(state, path)
    bridge.run(_call(), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None)
    backend.collect_result = lambda *_args: pytest.fail("resume collected a result")
    before = copy.deepcopy(state)
    assert reconnect_invocations(state, backend)["call-1"].state == "ready"
    assert unresolved_invocations(state)
    assert state == before
    assert backend.events == ["spawn", "wait", "resume"]
    assert reconnect_invocations(state)["call-1"].state == "orchestrator_unavailable"
    state["orchestrator"]["ref"]["t3"]["threads"]["call-1"].pop("run_id")
    assert reconnect_invocations(state, backend)["call-1"].state == "unknown"


def test_t3_snapshot_is_fsynced_and_atomically_replaced(tmp_path, monkeypatch):
    state = _state()
    path = tmp_path / "state.json"
    runstate.save_state(state, path)
    before = path.read_bytes()
    events = []
    original_replace, original_fsync = os.replace, os.fsync

    def replace(*args, **kwargs):
        assert path.read_bytes() == before
        assert events == ["fsync"]
        events.append("replace")
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", lambda descriptor: events.append("fsync") or original_fsync(descriptor))
    state["goal"] = "new goal"
    runstate.save_state(state, path)
    assert events == ["fsync", "replace", "fsync"]
    assert runstate.load_state(path)["goal"] == "new goal"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_t3_snapshot_refuses_linked_targets(tmp_path, link):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    target = tmp_path / "state.json"
    if link == "symlink":
        target.symlink_to(victim)
    else:
        os.link(victim, target)
    with pytest.raises(OSError):
        runstate.save_state(_state(), target)
    assert victim.read_text() == "keep"


def test_native_metadata_never_persists_process_local_handles(tmp_path):
    state = _state("native")
    path = tmp_path / "state.json"
    bridge = AgentExecutionBridge()
    providers.run_provider("mock", "generator", "prompt", {"_orchestrator_bridge": bridge}, state=state)
    runstate.save_state(state, path)
    assert runstate.load_state(path)["orchestrator"]["invocations"] == {}
    assert state["orchestrator"]["ref"] == {}


def test_t3_dag_consumers_preserve_dependencies_and_canonical_gate_state(tmp_path, monkeypatch):
    from rig_workbench.orchestrate.recipes import load_steps
    steps = load_steps({"steps": [
        {"id": "first", "instruction": "work", "checks": ["true"]},
        {"id": "second", "instruction": "work", "needs": ["first"], "checks": ["true"]},
    ]})
    state = runstate.new_state("dag", steps, "goal")
    state["orchestrator"] = selection_record("t3", capabilities=("agent.run",), ref={"t3": {"threads": {}}})
    path = tmp_path / "state.json"
    bridge, _ = _bridge(state, path)
    monkeypatch.setattr(providers, "telemetry_append", lambda *_args, **_kwargs: None)
    statuses = state["step_state"]
    original = {sid: st for sid, st in statuses.items()}
    assert providers.run_loop(state, path, "mock", "mock", {"_orchestrator_bridge": bridge}, 10, quiet=True) == "DONE"
    assert state["step_state"] is statuses
    assert all(statuses[sid] is original[sid] and statuses[sid]["status"] == "passed" for sid in statuses)
    assert state["waves"] == [["first"], ["second"]]
    assert all(call["result_applied"] for call in state["orchestrator"]["invocations"].values())


def test_telemetry_adds_orchestrator_without_changing_backend_or_exposing_refs(monkeypatch):
    records = []
    monkeypatch.setattr(runstate, "append_run_record", lambda rec, **_kwargs: records.append(rec))
    state = _state()
    state["history"].append({"action": "ORCHESTRATOR_FALLBACK", "from": "t3", "to": "native"})
    runstate.telemetry_append(state, "BLOCKED")
    assert records[-1]["backend"] == "orchestrate"
    assert records[-1]["orchestrator"] == "t3"
    assert records[-1]["orchestrator_fallbacks"] == 1
    assert "endpoint" not in json.dumps(records[-1])
    del state["orchestrator"]
    runstate.telemetry_append(state, "DONE")
    assert "orchestrator" not in records[-1]
    assert "orchestrator_fallbacks" not in records[-1]


def test_handle_snapshot_failure_stops_before_wait_and_reports_known_ids(tmp_path, capsys):
    state = _state()
    path = tmp_path / "state.json"
    snapshots = []

    def save(current, target):
        snapshots.append(copy.deepcopy(current))
        if len(snapshots) == 3:
            raise OSError("private failure detail")
        runstate.save_state(current, target)

    bridge, backend = _bridge(state, path, save)
    with pytest.raises(OrchestratorUnavailable, match="orchestrator_snapshot_failed"):
        bridge.run(_call(), _agent(), native_dispatch=lambda *_args: pytest.fail("native ran"), record_attempt=lambda: None)
    assert backend.events == ["spawn"]
    assert runstate.load_state(path)["orchestrator"]["invocations"]["call-1"]["phase"] == "starting"
    assert state["orchestrator"]["ref"]["t3"]["threads"]["call-1"] == {"thread_id": "thread-call-1", "run_id": "run-call-1"}
    report = capsys.readouterr().err
    assert "thread-call-1" in report and "run-call-1" in report
    assert "private failure detail" not in report
    with pytest.raises(OrchestratorUnavailable):
        bridge.run(_call("next"), _agent(), native_dispatch=lambda *_args: pytest.fail("native ran"), record_attempt=lambda: None)
    assert backend.events == ["spawn"]


def test_unresolved_native_fallback_reconnect_is_unknown_without_using_t3(tmp_path):
    state = _state()
    path = tmp_path / "state.json"
    bridge, backend = _bridge(state, path)
    bridge.run(_call(), _agent(), native_dispatch=lambda *_args: None, record_attempt=lambda: None)
    state["orchestrator"]["invocations"]["call-1"].update(orchestrator="native", phase="starting")
    del state["orchestrator"]["ref"]["t3"]["threads"]["call-1"]
    before = copy.deepcopy(state)
    backend.resume = lambda *_args: pytest.fail("Native fallback was looked up in T3")
    report = reconnect_invocations(state, backend)["call-1"]
    assert report.state == "unknown"
    assert report.handle.orchestrator == "native"
    assert report.handle.ref == {}
    assert state == before


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_atomic_snapshot_failure_preserves_previous_state_and_removes_temporary_files(tmp_path, monkeypatch, operation):
    state = _state()
    path = tmp_path / "state.json"
    runstate.save_state(state, path)
    before = path.read_bytes()
    state["goal"] = "unsaved update"

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError):
        runstate.save_state(state, path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))
