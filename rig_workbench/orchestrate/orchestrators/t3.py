"""Run-pinned T3 logical adapter; injected contracts remain distinct from live validation."""
import time

from ..agent_runtime import REGISTRY, RIG_GEN_PREFIX, RIG_VER_PREFIX, backend_for
from .base import (
    AgentConnectionLost, AgentHandle, AgentNotStarted, AgentResult, AgentStartUnknown,
    AgentStatus, Availability, CancelResult, ReconnectResult, ResultNotReady,
    UnsupportedAgentSpec,
)

_TERMINAL = {"completed", "failed", "cancelled"}


class T3Orchestrator:
    name = "t3"

    def __init__(self, client, endpoint, project_id=None, *, token=None):
        self.client = client
        self.endpoint = endpoint
        self.project_id = project_id
        self._token = token
        self._catalog = {}
        self._specs = {}
        self._targets = {}
        self._results = {}
        self._deadlines = {}
        self._availability = Availability(False, "unverified_contract", "Live T3 API contract has not been verified")
        if client is not None:
            self._probe()

    def _call(self, operation, arguments, timeout_s):
        return self.client.call(operation, arguments, timeout_s=timeout_s)

    def _probe(self):
        deadline = time.monotonic() + 5
        try:
            response = self._call("capabilities", {}, max(0, deadline - time.monotonic()))
            if response.get("contract_version") != "rig-t3-v1" or response.get("same_checkout") is not True:
                return
            projects = response.get("projects", [])
            if self.project_id is None:
                if not isinstance(projects, list) or len(projects) != 1 or not isinstance(projects[0], str):
                    self._availability = Availability(False, "project_ambiguous", "T3 project is unspecified or ambiguous")
                    return
                self.project_id = projects[0]
            elif self.project_id not in projects:
                self._availability = Availability(False, "project_missing", "Configured T3 project is unavailable")
                return
            if not isinstance(self.project_id, str) or not self.project_id.strip() or any(not c.isprintable() for c in self.project_id) or self._token and self._token in self.project_id:
                self._availability = Availability(False, "invalid_project", "T3 project identity is invalid")
                return
            required = {"agent.run", "agent.cancel", "thread.durable", "thread.resume"}
            if not required.issubset(response.get("capabilities", [])) or time.monotonic() > deadline:
                return
            catalog = response.get("providers")
            if not isinstance(catalog, list) or not catalog:
                return
            if any(not isinstance(entry, dict) or not isinstance(entry.get("instance_id"), str)
                   or not entry["instance_id"] or not isinstance(entry.get("provider"), str)
                   or not isinstance(entry.get("roles"), list) or not isinstance(entry.get("workspaces"), list)
                   or not isinstance(entry.get("constraints", {}), dict)
                   or entry.get("model") is not None and not isinstance(entry["model"], str)
                   for entry in catalog):
                return
            if len({entry["instance_id"] for entry in catalog}) != len(catalog):
                return
            self._catalog = {entry["instance_id"]: entry for entry in catalog}
            self._availability = Availability(True, "compatible_contract", "MCP connected; compatible contract; live execution unverified")
        except Exception:
            self._availability = Availability(False, "probe_failed", "T3 compatibility probe failed")

    def available(self):
        return self._availability

    def capabilities(self):
        if not self._availability.ok:
            return frozenset()
        return frozenset({"agent.run", "agent.cancel", "thread.durable", "thread.resume"})

    def validate_agent(self, task, agent):
        if (not self._availability.ok or agent.role not in ("generator", "verifier")
                or agent.provider == "cmd" or agent.constraints.get("native_configuration")):
            raise UnsupportedAgentSpec("unsupported_agent_spec")
        effective = backend_for(agent.provider)
        adapter = REGISTRY.get(agent.provider)
        confinement = adapter.capabilities.verifier_confinement if adapter else None
        requested = dict(agent.constraints)
        if agent.role == "verifier":
            requested["read_only"] = True
        matches = []
        for entry in self._catalog.values():
            if (isinstance(entry.get("provider"), str) and backend_for(entry["provider"]) == effective and entry.get("model") == agent.model
                    and task.cwd in entry.get("workspaces", []) and agent.role in entry.get("roles", [])
                    and entry.get("same_checkout") is True
                    and (agent.role != "verifier" or entry.get("verifier_confinement") == confinement and confinement is not None)
                    and isinstance(entry.get("constraints", {}), dict)
                    and all(entry.get("constraints", {}).get(key) == value for key, value in requested.items())):
                matches.append(entry)
        if len(matches) != 1:
            raise UnsupportedAgentSpec("unsupported_agent_spec")
        return matches[0]

    def spawn_agent(self, task, agent):
        deadline = time.monotonic() + agent.timeout_s
        target = self.validate_agent(task, agent)
        prompt = agent.prompt
        if agent.provider == "rig":
            prompt = (RIG_VER_PREFIX if agent.role == "verifier" else RIG_GEN_PREFIX) + prompt
        constraints = dict(agent.constraints)
        if agent.role == "verifier":
            constraints["read_only"] = True
        arguments = {"prompt": prompt, "provider_instance": target["instance_id"],
                     "provider": target["provider"], "model": agent.model, "role": agent.role,
                     "persona": agent.persona, "cwd": task.cwd, "project_id": self.project_id,
                     "constraints": constraints, "new_thread": True}
        try:
            response = self._call("launch", arguments, min(5, agent.timeout_s))
        except AgentNotStarted:
            raise
        except Exception:
            raise AgentStartUnknown("launch_response_lost") from None
        refs = {key: response[key] for key in ("thread_id", "run_id") if isinstance(response.get(key), str)
                and response[key] and all(c.isprintable() for c in response[key])
                and (not self._token or self._token not in response[key])}
        if response.get("started") is False and response.get("side_effects") is False:
            raise AgentNotStarted("launch_refused_without_side_effects")
        if len(refs) != 2:
            raise AgentStartUnknown("launch_identity_incomplete", ref=refs)
        handle = AgentHandle("t3", task.invocation_id, refs)
        self._specs[task.invocation_id] = agent
        self._targets[task.invocation_id] = target
        self._deadlines[task.invocation_id] = deadline
        return handle

    def wait(self, handle, *, timeout_s):
        deadline = min(time.monotonic() + timeout_s, self._deadlines.get(handle.invocation_id, float("inf")))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return AgentStatus("running")
            try:
                result = self._call("wait", dict(handle.ref), min(5, remaining))
                if result.get("run_id") != handle.ref["run_id"] or result.get("thread_id") != handle.ref["thread_id"]:
                    raise ValueError("unpinned wait")
                phase = result.get("phase")
                if phase not in {"pending", "running", *_TERMINAL}:
                    raise ValueError("unknown phase")
            except Exception:
                raise AgentConnectionLost("wait_connection_lost", ref=handle.ref) from None
            if phase in _TERMINAL:
                return AgentStatus(phase)

    def collect_result(self, handle):
        if handle.invocation_id in self._results:
            return self._results[handle.invocation_id]
        remaining = self._deadlines.get(handle.invocation_id, time.monotonic()) - time.monotonic()
        if remaining <= 0:
            raise AgentConnectionLost("agent_timeout", ref=handle.ref)
        try:
            response = self._call("read", dict(handle.ref), min(5, remaining))
        except Exception:
            raise AgentConnectionLost("read_connection_lost", ref=handle.ref) from None
        if response.get("phase") not in _TERMINAL:
            raise ResultNotReady("t3_result_not_ready")
        spec = self._specs[handle.invocation_id]
        if (response.get("thread_id") != handle.ref["thread_id"] or response.get("run_id") != handle.ref["run_id"]
                or response.get("complete") is not True or response.get("truncated") is not False
                or response.get("provider") != self._targets[handle.invocation_id]["provider"] or response.get("model") != spec.model
                or type(response.get("returncode")) is not int or not isinstance(response.get("output"), str)):
            raise AgentConnectionLost("invalid_or_incomplete_result", ref=handle.ref)
        if self._token and self._token in response["output"]:
            raise AgentConnectionLost("secret_in_agent_result", ref=handle.ref)
        result = AgentResult(response["returncode"], response["output"], spec.provider, spec.model)
        self._results[handle.invocation_id] = result
        return result

    def resume(self, handle):
        if not self.available().ok:
            return ReconnectResult("orchestrator_unavailable", handle, None, "T3 is unavailable")
        try:
            response = self._call("read", dict(handle.ref), 5)
            if response.get("missing") is True:
                return ReconnectResult("agent_missing", handle, None, "Recorded T3 run is missing")
            if (response.get("thread_id") != handle.ref.get("thread_id") or response.get("run_id") != handle.ref.get("run_id")
                    or response.get("phase") not in {"pending", "running", *_TERMINAL}):
                raise ValueError("invalid reconnect response")
            return ReconnectResult("ready", handle, AgentStatus(response["phase"]), "Recorded T3 run reconnected")
        except Exception:
            return ReconnectResult("unknown", handle, None, "T3 run could not be confirmed")

    def cancel(self, handle):
        try:
            response = self._call("interrupt", dict(handle.ref), 5)
            if response.get("thread_id") != handle.ref.get("thread_id") or response.get("run_id") != handle.ref.get("run_id"):
                return CancelResult("unknown")
            state = response.get("state")
            return CancelResult(state if state in ("cancelled", "already_terminal") else "unknown")
        except Exception:
            return CancelResult("unknown")
