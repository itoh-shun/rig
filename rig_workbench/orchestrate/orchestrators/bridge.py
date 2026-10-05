"""Common agent execution seam; provider timing remains owned by run_provider."""
from collections.abc import Callable
import copy
import hashlib
import json
import pathlib
import re
import threading
import uuid

from .base import (AgentConnectionLost, AgentNotStarted, AgentSpec, AgentStartUnknown,
                   OrchestratorError, OrchestratorUnavailable, TaskContext)
from .native import NativeOrchestrator
from .credentials import t3_token


class AgentExecutionBridge:
    def __init__(self, backend=None, *, coordinator=None):
        self.backend = backend
        self.coordinator = coordinator
        self._serial = threading.Semaphore(1)
        self._consumer_serial = threading.Semaphore(1)
        self._blocked = False

    def run(self, task: TaskContext, agent: AgentSpec, *,
            native_dispatch: Callable[[AgentSpec, float], tuple[int, str]],
            record_attempt: Callable[[], str | None], consumer_id="") -> tuple[int, str]:
        backend = self.backend or NativeOrchestrator(native_dispatch)
        if backend.name == "t3" or "agent.parallel" not in backend.capabilities():
            with self._serial:
                if self._blocked:
                    raise OrchestratorUnavailable("orchestrator_blocked")
                try:
                    return self._run(backend, task, agent, record_attempt, consumer_id)
                except AgentNotStarted as error:
                    metadata = read_metadata(self.coordinator.state) if self.coordinator else {}
                    if metadata.get("preferred") == "auto" and metadata.get("fallback") == "native" and not self.coordinator.failed:
                        try:
                            return self._fallback(task, agent, native_dispatch, record_attempt, error)
                        except OrchestratorError as fallback_error:
                            self._block(task, fallback_error)
                            raise
                        except Exception:
                            fallback_error = AgentConnectionLost("native_fallback_result_unknown")
                            self._block(task, fallback_error)
                            raise fallback_error from None
                    self._block(task, error)
                    raise
                except OrchestratorError as error:
                    self._block(task, error)
                    raise
                except (Exception, KeyboardInterrupt):
                    phase = (self.coordinator.state.get("orchestrator", {}).get("invocations", {})
                             .get(task.invocation_id, {}).get("phase")) if self.coordinator else None
                    error_type = AgentConnectionLost if phase == "running" else AgentStartUnknown
                    error = error_type("unexpected_orchestrator_failure")
                    self._block(task, error)
                    raise error from None
        return self._run(backend, task, agent, record_attempt, consumer_id)

    def _block(self, task, error):
        self._blocked = True
        if self.coordinator is None:
            return
        with self.coordinator.lock:
            state = self.coordinator.state
            state["stopped"] = {"kind": "BLOCKED", "source": "orchestrator", "at": task.step_id,
                                "invocation_id": task.invocation_id, "reason": error.reason_code}
            metadata = state.get("orchestrator", {})
            call = metadata.get("invocations", {}).get(task.invocation_id)
            if call is not None:
                call["phase"] = "unknown"
                ref = safe_external_ref(error.ref)
                if ref:
                    metadata["ref"]["t3"].setdefault("threads", {})[task.invocation_id] = ref
            if not self.coordinator.failed:
                self.coordinator.snapshot()

    def _fallback(self, task, agent, native_dispatch, record_attempt, error):
        coordinator = self.coordinator
        with coordinator.lock:
            call = coordinator.state["orchestrator"]["invocations"][task.invocation_id]
            call.update(orchestrator="native", phase="starting")
            coordinator.state["history"].append({"action": "ORCHESTRATOR_FALLBACK", "invocation_id": task.invocation_id,
                                                  "from": "t3", "to": "native", "reason": error.reason_code})
            coordinator.snapshot()
        backend = NativeOrchestrator(native_dispatch)
        journal_error = record_attempt()
        if journal_error is not None:
            raise OrchestratorUnavailable("benchmark_counter_failed")
        handle = backend.spawn_agent(task, agent)
        try:
            status = backend.wait(handle, timeout_s=agent.timeout_s)
        except KeyboardInterrupt:
            backend.cancel(handle)
            raise AgentConnectionLost("agent_interrupted", ref=handle.ref) from None
        result = backend.collect_result(handle)
        return self._cache_result(task, call, status, result)

    def ensure_quiescent(self):
        if self._blocked:
            raise OrchestratorUnavailable("orchestrator_blocked")
        if self.coordinator and self.backend and self.backend.name == "t3":
            with self.coordinator.lock:
                for call in read_metadata(self.coordinator.state)["invocations"].values():
                    if call["phase"] not in ("completed", "failed", "cancelled"):
                        raise OrchestratorUnavailable("external_agent_unresolved")

    def _cancel_known(self, backend, handle):
        from .base import CancelResult
        try:
            result = backend.cancel(handle) if "agent.cancel" in backend.capabilities() else CancelResult("unsupported")
        except Exception:
            result = CancelResult("unknown")
        if self.coordinator:
            with self.coordinator.lock:
                call = self.coordinator.state["orchestrator"]["invocations"][handle.invocation_id]
                call["cancel_state"] = result.state
                self.coordinator.snapshot()
        return result

    def _run(self, backend, task, agent, record_attempt, consumer_id):
        if backend.name == "t3":
            return self._run_durable(backend, task, agent, record_attempt, consumer_id)
        error = record_attempt()
        if error is not None:
            return 126, f"[benchmark call counter error: {error}]"
        handle = backend.spawn_agent(task, agent)
        backend.wait(handle, timeout_s=agent.timeout_s)
        result = backend.collect_result(handle)
        return result.returncode, result.output

    def _run_durable(self, backend, task, agent, record_attempt, consumer_id):
        coordinator = self.coordinator
        if coordinator is None:
            raise OrchestratorUnavailable("persistent_state_required")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", task.invocation_id):
            raise OrchestratorUnavailable("invalid_invocation_id")
        with coordinator.lock:
            metadata = read_metadata(coordinator.state)
            call = {"step_id": task.step_id, "attempt": task.attempt, "role": agent.role,
                    "persona": agent.persona, "provider": agent.provider, "model": agent.model,
                    "orchestrator": "t3", "phase": "intent", "result_applied": False,
                    "consumer": consumer_id, "input_sha256": hashlib.sha256(agent.prompt.encode()).hexdigest()}
            metadata["invocations"][task.invocation_id] = call
            coordinator.snapshot()
            call["phase"] = "starting"
            coordinator.snapshot()
        error = record_attempt()
        if error is not None:
            raise OrchestratorUnavailable("benchmark_counter_failed")
        handle = backend.spawn_agent(task, agent)
        if safe_external_ref(handle.ref) != dict(handle.ref):
            raise AgentStartUnknown("unsafe_external_reference", ref=safe_external_ref(handle.ref))
        with coordinator.lock:
            metadata["ref"]["t3"].setdefault("threads", {})[task.invocation_id] = dict(handle.ref)
            call["phase"] = "running"
            try:
                coordinator.snapshot()
            except OrchestratorUnavailable:
                # The known external identity must remain visible if disk persistence fails.
                from ...ports.local import CONSOLE
                ids = safe_external_ref(handle.ref)
                token = t3_token()
                if token:
                    ids = {key: value.replace(token, "[redacted]") for key, value in ids.items()}
                detail = json.dumps(ids)
                CONSOLE.err("orchestrator snapshot failed; external reference: " + detail)
                raise
        try:
            status = backend.wait(handle, timeout_s=agent.timeout_s)
        except KeyboardInterrupt:
            self._cancel_known(backend, handle)
            raise AgentConnectionLost("agent_interrupted", ref=handle.ref) from None
        if status.phase not in ("completed", "failed", "cancelled"):
            cancellation = self._cancel_known(backend, handle)
            raise AgentConnectionLost("agent_timeout_unresolved" if cancellation.state not in ("cancelled", "already_terminal")
                                      else "agent_timeout", ref=handle.ref)
        try:
            result = backend.collect_result(handle)
        except KeyboardInterrupt:
            self._cancel_known(backend, handle)
            raise AgentConnectionLost("agent_interrupted", ref=handle.ref) from None
        if result.provider != agent.provider or result.model != agent.model:
            raise AgentConnectionLost("agent_identity_mismatch")
        return self._cache_result(task, call, status, result)

    def _cache_result(self, task, call, status, result):
        coordinator = self.coordinator
        token = t3_token()
        if token and token in result.output:
            raise AgentConnectionLost("secret_in_agent_result")
        output_path = pathlib.Path(coordinator.path).absolute().parent / "orchestrator-results" / f"{task.invocation_id}.json"
        from ..secure_fs import atomic_write_bytes
        payload = json.dumps({"returncode": result.returncode, "output": result.output,
                              "provider": result.provider, "model": result.model}, ensure_ascii=False).encode()
        try:
            atomic_write_bytes(output_path, payload)
        except Exception:
            raise OrchestratorUnavailable("orchestrator_result_save_failed") from None
        with coordinator.lock:
            call.update(phase=status.phase, output_path=str(output_path),
                        output_sha256=hashlib.sha256(payload).hexdigest())
            coordinator.snapshot()
        return result.returncode, result.output

    def consume_transition(self, state, step_id, cfg, execute):
        """Apply a complete workflow consumer and its exact call markers atomically."""
        if self.backend is None or self.backend.name != "t3":
            return execute(state, cfg)
        if self.coordinator is None:
            raise OrchestratorUnavailable("persistent_state_required")
        with self._consumer_serial:
            if self._blocked:
                return None
            consumer = f"consumer-{uuid.uuid4().hex}"
            with self.coordinator.lock:
                working = copy.deepcopy(state)
                history_size = len(working.get("history", []))
            try:
                result = execute(working, {**cfg, "_orchestrator_consumer_id": consumer})
            except OrchestratorError:
                return None
            with self.coordinator.lock:
                metadata = state["orchestrator"]
                for key, value in working.items():
                    if key == "step_state":
                        for sid, step_state in value.items():
                            state["step_state"][sid].clear()
                            state["step_state"][sid].update(step_state)
                    elif key not in ("orchestrator", "history"):
                        state[key] = value
                state["history"].extend(working.get("history", [])[history_size:])
                applied = []
                for invocation, call in metadata["invocations"].items():
                    if call.get("consumer") == consumer and call.get("output_sha256"):
                        call["result_applied"] = True
                        applied.append(invocation)
                try:
                    self.coordinator.snapshot()
                except Exception:
                    for invocation in applied:
                        metadata["invocations"][invocation]["result_applied"] = False
                    self._blocked = True
                    state["stopped"] = {"kind": "BLOCKED", "source": "orchestrator", "at": step_id,
                                        "reason": "orchestrator_snapshot_failed"}
                    return None
                return result


_COORDINATORS = {}
_COORDINATORS_LOCK = threading.Lock()


def safe_external_ref(ref):
    token = t3_token()
    return {key: value for key, value in ref.items() if key in ("thread_id", "run_id")
            and isinstance(value, str) and value and all(c.isprintable() for c in value)
            and (not token or token not in value)}


def coordinator_lock(state):
    """One process-local lock for bridge updates and runner snapshots; no lease."""
    with _COORDINATORS_LOCK:
        return _COORDINATORS.setdefault(id(state), threading.RLock())


def selection_record(name="native", *, selected_by="default", preferred="auto",
                     fallback="native", capabilities=(), ref=None):
    return {"schema_version": 1, "name": name, "selected_by": selected_by,
            "preferred": preferred, "fallback": fallback,
            "capabilities": sorted(capabilities), "invocations": {}, "ref": dict(ref or {})}


def metadata_errors(value):
    """Validate durable metadata without reflecting untrusted values into diagnostics."""
    from .base import CAPABILITIES
    if not isinstance(value, dict):
        return ("orchestrator metadata must be a mapping",)
    required = {"schema_version", "name", "selected_by", "preferred", "fallback",
                "capabilities", "invocations", "ref"}
    if set(value) != required or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        return ("orchestrator metadata schema is unsupported or incomplete",)
    if (value["name"] not in ("native", "t3") or value["selected_by"] not in ("cli", "manifest", "default", "legacy")
            or value["preferred"] not in ("auto", "native", "t3") or value["fallback"] not in ("native", "none")):
        return ("orchestrator selection metadata is invalid",)
    caps = value["capabilities"]
    if not isinstance(caps, list) or any(not isinstance(c, str) or c not in CAPABILITIES for c in caps):
        return ("orchestrator capabilities metadata is invalid",)
    if not isinstance(value["invocations"], dict) or not isinstance(value["ref"], dict):
        return ("orchestrator invocation or reference metadata is invalid",)
    if value["name"] == "native" and (value["invocations"] or value["ref"]):
        return ("native orchestrator metadata must not persist transient handles",)
    if value["name"] == "t3" and (set(value["ref"]) != {"t3"} or not isinstance(value["ref"]["t3"], dict)):
        return ("t3 orchestrator reference metadata is invalid",)
    for invocation, call in value["invocations"].items():
        if (not isinstance(invocation, str) or not isinstance(call, dict)
                or not {"step_id", "attempt", "role", "persona", "provider", "model", "orchestrator", "phase", "result_applied"}.issubset(call)):
            return ("orchestrator invocation metadata is incomplete",)
        if (type(call["attempt"]) is not int or call["attempt"] < 1
                or call["orchestrator"] not in ("native", "t3")
                or call["phase"] not in ("intent", "starting", "pending", "running", "completed", "failed", "cancelled", "unknown")
                or type(call["result_applied"]) is not bool
                or any(not isinstance(call[k], str) for k in ("step_id", "role", "persona", "provider"))
                or call["model"] is not None and not isinstance(call["model"], str)):
            return ("orchestrator invocation metadata is invalid",)
        if call["result_applied"] and call["phase"] not in ("completed", "failed", "cancelled"):
            return ("orchestrator invocation result is applied before execution is terminal",)
    def contains_secret_key(item):
        if isinstance(item, dict):
            return any(key in ("token", "headers", "cfg", "client") or contains_secret_key(v) for key, v in item.items())
        if isinstance(item, list):
            return any(contains_secret_key(v) for v in item)
        return False
    if contains_secret_key(value):
        return ("orchestrator metadata contains prohibited private fields",)
    return ()


def read_metadata(state):
    if "orchestrator" not in state:
        return selection_record(selected_by="legacy", preferred="native", capabilities=("agent.run", "agent.parallel"))
    errors = metadata_errors(state["orchestrator"])
    if errors:
        from .base import OrchestratorUnavailable
        raise OrchestratorUnavailable("malformed_orchestrator_state")
    return state["orchestrator"]


def unresolved_invocations(state):
    metadata = read_metadata(state)
    return {key: value for key, value in metadata["invocations"].items() if not value["result_applied"]}


class SaveCoordinator:
    def __init__(self, state, path, save):
        self.state = state
        self.path = path
        self.save = save
        self.lock = coordinator_lock(state)
        self.failed = False

    def snapshot(self):
        from .base import OrchestratorUnavailable
        if self.path is None or self.failed:
            raise OrchestratorUnavailable("persistent_state_unavailable")
        with self.lock:
            try:
                self.save(self.state, self.path)
            except Exception:
                self.failed = True
                raise OrchestratorUnavailable("orchestrator_snapshot_failed") from None



def reconnect_invocations(state, backend=None):
    """Read-only v0.1 reconciliation: no collect, launch, cancel or result application."""
    from .base import AgentHandle, ReconnectResult
    metadata = read_metadata(state)
    calls = unresolved_invocations(state)
    reports = {}
    refs = metadata["ref"].get("t3", {}).get("threads", {})
    for invocation, call in calls.items():
        handle = AgentHandle(call["orchestrator"], invocation, refs.get(invocation, {}))
        if call["orchestrator"] == "native" or not (handle.ref.get("thread_id") and handle.ref.get("run_id")):
            reports[invocation] = ReconnectResult("unknown", handle, None, "Invocation execution is unresolved")
        elif backend is None or not backend.available().ok:
            reports[invocation] = ReconnectResult("orchestrator_unavailable", handle, None, "Recorded orchestrator is unavailable")
        else:
            reports[invocation] = backend.resume(handle)
    return reports
