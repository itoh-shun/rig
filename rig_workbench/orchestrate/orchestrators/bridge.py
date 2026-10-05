"""Common agent execution seam; provider timing remains owned by run_provider."""
from collections.abc import Callable
import threading

from .base import AgentSpec, TaskContext
from .native import NativeOrchestrator


class AgentExecutionBridge:
    def __init__(self, backend=None):
        self.backend = backend
        self._serial = threading.Semaphore(1)

    def run(self, task: TaskContext, agent: AgentSpec, *,
            native_dispatch: Callable[[AgentSpec, float], tuple[int, str]],
            record_attempt: Callable[[], str | None]) -> tuple[int, str]:
        backend = self.backend or NativeOrchestrator(native_dispatch)
        if "agent.parallel" not in backend.capabilities():
            with self._serial:
                return self._run(backend, task, agent, record_attempt)
        return self._run(backend, task, agent, record_attempt)

    @staticmethod
    def _run(backend, task, agent, record_attempt):
        error = record_attempt()
        if error is not None:
            return 126, f"[benchmark call counter error: {error}]"
        handle = backend.spawn_agent(task, agent)
        backend.wait(handle, timeout_s=agent.timeout_s)
        result = backend.collect_result(handle)
        return result.returncode, result.output
