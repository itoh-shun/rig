"""Process-local handles wrapping an invocation-bound existing provider dispatcher."""
from dataclasses import dataclass
import threading
import time
from collections.abc import Callable

from .base import (AgentHandle, AgentResult, AgentSpec, AgentStatus, Availability,
                   CancelResult, ReconnectResult, ResultNotReady, TaskContext)


@dataclass
class _Call:
    spec: AgentSpec
    phase: str = "pending"
    result: AgentResult | None = None
    error: BaseException | None = None


class NativeOrchestrator:
    name = "native"

    def __init__(self, dispatch: Callable[[AgentSpec, float], tuple[int, str]]):
        self._dispatch = dispatch
        self._calls: dict[str, _Call] = {}
        self._condition = threading.Condition()

    def available(self) -> Availability:
        return Availability(True, "builtin", "Built-in agent execution adapter")

    def capabilities(self):
        return frozenset({"agent.run", "agent.parallel"})

    def spawn_agent(self, task: TaskContext, agent: AgentSpec) -> AgentHandle:
        with self._condition:
            if task.invocation_id in self._calls:
                raise ValueError("duplicate native invocation")
            self._calls[task.invocation_id] = _Call(agent)
        return AgentHandle("native", task.invocation_id, {})

    def wait(self, handle: AgentHandle, *, timeout_s: float) -> AgentStatus:
        deadline = time.monotonic() + max(0, timeout_s)
        with self._condition:
            call = self._calls[handle.invocation_id]
            while call.phase == "running":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return AgentStatus("running")
                self._condition.wait(remaining)
            if call.error is not None:
                raise call.error
            if call.phase != "pending":
                return AgentStatus(call.phase)
            call.phase = "running"
        try:
            rc, output = self._dispatch(call.spec, min(call.spec.timeout_s, max(0, timeout_s)))
            result = AgentResult(rc, output, call.spec.provider, call.spec.model)
        except BaseException as error:
            with self._condition:
                call.error = error
                call.phase = "failed"
                self._condition.notify_all()
            raise
        with self._condition:
            call.result = result
            call.phase = "completed" if rc == 0 else "failed"
            self._condition.notify_all()
            return AgentStatus(call.phase)

    def collect_result(self, handle: AgentHandle) -> AgentResult:
        with self._condition:
            call = self._calls[handle.invocation_id]
            if call.result is None:
                raise ResultNotReady("native_result_not_ready")
            return call.result

    def resume(self, handle: AgentHandle) -> ReconnectResult:
        with self._condition:
            call = self._calls.get(handle.invocation_id)
            if call is None:
                return ReconnectResult("unknown", None, None, "Native handles are process-local")
            return ReconnectResult("ready", handle, AgentStatus(call.phase), "Native handle is available")

    def cancel(self, handle: AgentHandle) -> CancelResult:
        with self._condition:
            call = self._calls.get(handle.invocation_id)
            if call is None:
                return CancelResult("unknown")
            if call.phase == "pending":
                call.phase = "cancelled"
                return CancelResult("cancelled")
            if call.phase == "running":
                return CancelResult("unsupported")
            return CancelResult("already_terminal")
