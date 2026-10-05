"""Transport-independent agent execution contract (Python 3.10 compatible)."""
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol

Capability = Literal[
    "agent.run", "agent.parallel", "agent.cancel", "thread.durable", "thread.resume",
    "delegate.child", "provider.switch", "fork_merge", "schedule",
]
CAPABILITIES = frozenset({
    "agent.run", "agent.parallel", "agent.cancel", "thread.durable", "thread.resume",
    "delegate.child", "provider.switch", "fork_merge", "schedule",
})
OrchestratorName = Literal["native", "t3"]


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason_code: str
    detail: str


@dataclass(frozen=True)
class TaskContext:
    run_id: str
    step_id: str
    invocation_id: str
    attempt: int
    cwd: str


@dataclass(frozen=True)
class AgentSpec:
    provider: str
    model: str | None
    role: str
    persona: str
    prompt: str
    timeout_s: float
    constraints: Mapping[str, object]


@dataclass(frozen=True)
class AgentHandle:
    orchestrator: OrchestratorName
    invocation_id: str
    ref: Mapping[str, object]


@dataclass(frozen=True)
class AgentStatus:
    phase: Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class AgentResult:
    returncode: int
    output: str
    provider: str
    model: str | None


@dataclass(frozen=True)
class ReconnectResult:
    state: Literal["ready", "orchestrator_unavailable", "agent_missing", "unknown"]
    handle: AgentHandle | None
    status: AgentStatus | None
    detail: str


@dataclass(frozen=True)
class CancelResult:
    state: Literal["cancelled", "already_terminal", "unsupported", "unknown"]


class OrchestratorError(RuntimeError):
    """Only locally controlled reason codes enter exception text, never transport data."""
    def __init__(self, reason_code: str = "orchestrator_error", *, ref=None):
        self.reason_code = reason_code
        self.ref = dict(ref or {})
        # Reasons must be selected by adapters rather than copied from SDK exceptions.
        super().__init__(reason_code)


class OrchestratorUnavailable(OrchestratorError):
    pass


class AgentNotStarted(OrchestratorError):
    pass


class UnsupportedAgentSpec(AgentNotStarted):
    pass


class AgentStartUnknown(OrchestratorError):
    pass


class AgentConnectionLost(OrchestratorError):
    pass


class ResultNotReady(OrchestratorError):
    pass


class OrchestratorBackend(Protocol):
    name: OrchestratorName
    def available(self) -> Availability: ...
    def capabilities(self) -> frozenset[Capability]: ...
    def spawn_agent(self, task: TaskContext, agent: AgentSpec) -> AgentHandle: ...
    def wait(self, handle: AgentHandle, *, timeout_s: float) -> AgentStatus: ...
    def resume(self, handle: AgentHandle) -> ReconnectResult: ...
    def cancel(self, handle: AgentHandle) -> CancelResult: ...
    def collect_result(self, handle: AgentHandle) -> AgentResult: ...
