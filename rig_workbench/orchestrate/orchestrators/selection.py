"""Shared execution selection; optional backends load only after configuration checks."""
from dataclasses import dataclass, field, replace
import time

from ...ports.local import CONSOLE, OS_ENV
from .base import Availability, OrchestratorError, OrchestratorUnavailable, UnsupportedAgentSpec
from .bridge import AgentExecutionBridge, SaveCoordinator, selection_record
from .config import MISSING, loopback_endpoint, parse_orchestrator_config, valid_endpoint
from .credentials import t3_token


@dataclass(frozen=True)
class T3Settings:
    endpoint: str | None
    token: str | None = field(repr=False)
    project_id: str | None = None
    endpoint_source: str = "manifest"


@dataclass(frozen=True)
class Selection:
    name: str
    preferred: str
    selected_by: str
    fallback: str
    availability: Availability
    orchestrator: object = field(default=None, repr=False)
    settings: T3Settings | None = field(default=None, repr=False)

    def record(self):
        ref = {}
        caps = ("agent.run", "agent.parallel")
        if self.name == "t3":
            caps = self.orchestrator.capabilities()
            ref = {"t3": {"endpoint": self.settings.endpoint,
                          "project_id": getattr(self.orchestrator, "project_id", self.settings.project_id),
                          "contract_version": "rig-t3-v1", "threads": {}}}
        return selection_record(self.name, preferred=self.preferred, selected_by=self.selected_by,
                                fallback=self.fallback, capabilities=caps, ref=ref)


def resolve_settings(config, env=OS_ENV):
    def get(key):
        return env.get(key) or None
    endpoint = get("RIG_T3_MCP_URL")
    return T3Settings(endpoint or config.url,
                      t3_token(env), get("RIG_T3_PROJECT_ID") or config.project_id,
                      "environment" if endpoint else "manifest")


def _load_t3(settings, timeout_s=5):
    # This is the sole optional-orchestrator loader, called only after configuration checks.
    from .t3 import T3Orchestrator
    from .t3_client import McpT3Client
    deadline = time.monotonic() + timeout_s
    client = McpT3Client(settings.endpoint, settings.token, timeout_s=timeout_s)
    orchestrator = T3Orchestrator(client, settings.endpoint, settings.project_id, token=settings.token,
                             timeout_s=max(0, deadline - time.monotonic()))
    if not orchestrator.available().ok:
        client.close(timeout_s=max(0, deadline - time.monotonic()))
    return orchestrator


def close_orchestrator(orchestrator, *, timeout_s=None):
    client = getattr(orchestrator, "client", None)
    close = getattr(client, "close", None)
    if close is not None:
        try:
            if timeout_s is None:
                close()
            else:
                close(timeout_s=timeout_s)
        except TypeError:
            try:
                close()
            except Exception:
                pass
        except Exception:
            pass


def probe_t3(settings, *, factory=None):
    if not settings.endpoint or not settings.token:
        return Availability(False, "unconfigured", "T3 connection URL and bearer token are required"), None
    if not valid_endpoint(settings.endpoint):
        return Availability(False, "invalid_endpoint", "T3 connection URL configuration is invalid"), None
    if settings.endpoint_source != "environment" and not loopback_endpoint(settings.endpoint):
        return Availability(False, "manifest_endpoint_not_loopback",
                            "T3 manifest endpoint must be loopback; set RIG_T3_MCP_URL to authorize a remote endpoint"), None
    if settings.token in settings.endpoint or settings.project_id and settings.token in settings.project_id:
        return Availability(False, "secret_in_configuration", "T3 public connection identifiers contain secret material"), None
    if settings.project_id is not None and (not isinstance(settings.project_id, str)
                                            or not settings.project_id.strip()
                                            or any(ord(c) < 32 or ord(c) == 127 for c in settings.project_id)):
        return Availability(False, "invalid_project", "T3 project configuration is invalid"), None
    orchestrator = None
    deadline = time.monotonic() + 5
    try:
        orchestrator = factory(settings) if factory else _load_t3(settings, max(0, deadline - time.monotonic()))
        availability = orchestrator.available()
        if time.monotonic() > deadline:
            close_orchestrator(orchestrator, timeout_s=max(0, deadline - time.monotonic()))
            return Availability(False, "probe_timeout", "T3 compatibility probe exceeded its deadline"), None
        project = getattr(orchestrator, "project_id", settings.project_id)
        if project is not None and settings.token in project:
            close_orchestrator(orchestrator, timeout_s=max(0, deadline - time.monotonic()))
            return Availability(False, "secret_in_configuration", "T3 project identity contains secret material"), None
        if settings.token in availability.detail:
            availability = Availability(availability.ok, availability.reason_code,
                                        availability.detail.replace(settings.token, "[redacted]"))
        return availability, orchestrator
    except RuntimeError as error:
        close_orchestrator(orchestrator, timeout_s=max(0, deadline - time.monotonic()))
        if str(error) == "mcp_sdk_unavailable":
            return Availability(False, "sdk_unavailable", "Optional MCP SDK is not installed"), None
        return Availability(False, "probe_failed", "T3 compatibility probe failed"), None
    except Exception:
        close_orchestrator(orchestrator, timeout_s=max(0, deadline - time.monotonic()))
        return Availability(False, "probe_failed", "T3 compatibility probe failed"), None


def select_orchestrator(value=MISSING, *, cli=None, env=OS_ENV, factory=None,
                        requirements=(), native_only_reason=None, emit=True, out=CONSOLE):
    try:
        config = parse_orchestrator_config(value)
    except ValueError:
        raise OrchestratorUnavailable("invalid_orchestrator_config") from None
    selected_by = "cli" if cli is not None else "default" if value is MISSING else "manifest"
    preferred = config.preferred if cli is None else cli
    if preferred not in ("native", "auto", "t3"):
        raise OrchestratorUnavailable("invalid_orchestrator_choice")
    if preferred == "native":
        return Selection("native", preferred, selected_by, config.fallback,
                         Availability(True, "native_requested", "Native requested; T3 not probed"))
    settings = resolve_settings(config, env)
    if native_only_reason:
        availability, orchestrator = Availability(False, "unsupported_mode", native_only_reason), None
    else:
        availability, orchestrator = probe_t3(settings, factory=factory)
        if availability.ok:
            try:
                for task, agent in requirements:
                    orchestrator.validate_agent(task, agent)
            except UnsupportedAgentSpec:
                availability = Availability(False, "unsupported_agent_spec", "T3 cannot preserve the requested agent constraints")
            except Exception:
                availability = Availability(False, "compatibility_unknown", "T3 request compatibility could not be confirmed")
    if availability.ok:
        return Selection("t3", preferred, selected_by, config.fallback, availability, orchestrator, settings)
    close_orchestrator(orchestrator)
    if preferred == "t3" or config.fallback == "none":
        raise OrchestratorUnavailable(availability.reason_code)
    if emit:
        out.err(f"◇ orchestrator auto: {availability.detail}; falling back to native")
    return Selection("native", preferred, selected_by, config.fallback, availability, settings=settings)


def bind_selection(selection, state, path, save):
    state["orchestrator"] = selection.record()
    if selection.name == "t3" and path is None:
        raise OrchestratorUnavailable("persistent_state_required")
    coordinator = SaveCoordinator(state, path, save)
    # Only T3 needs the selection on disk before its first call. Native keeps the legacy
    # checkpoint timing: the state file must not appear before the runner's own first save,
    # and a strict run refuses to initialize over an existing one.
    if path is not None and selection.name == "t3":
        coordinator.snapshot()
    return AgentExecutionBridge(selection.orchestrator, coordinator=coordinator)


def reconnect_recorded(state, value=MISSING, *, env=OS_ENV, factory=None, report_errors=False):
    """Share namespace reconciliation and optional read-only failure reports with the CLI."""
    from .bridge import reconnect_invocations, unresolved_invocations
    try:
        return _reconnect_recorded(state, value, env=env, factory=factory)
    except OrchestratorError as error:
        if not report_errors or not unresolved_invocations(state):
            raise
        return {invocation: replace(report, detail=error.reason_code)
                for invocation, report in reconnect_invocations(state).items()}


def _reconnect_recorded(state, value=MISSING, *, env=OS_ENV, factory=None):
    """Reconnect only the recorded namespace; never select another execution orchestrator."""
    from .bridge import read_metadata, reconnect_invocations, safe_external_ref, unresolved_invocations
    metadata = read_metadata(state)
    if metadata["name"] == "native":
        return {}
    try:
        config = parse_orchestrator_config(value)
    except ValueError:
        raise OrchestratorUnavailable("invalid_orchestrator_config") from None
    settings = resolve_settings(config, env)
    ref = metadata["ref"].get("t3")
    if not isinstance(ref, dict) or set(ref) != {"endpoint", "project_id", "contract_version", "threads"}:
        raise OrchestratorUnavailable("malformed_orchestrator_namespace")
    if (not valid_endpoint(ref["endpoint"]) or not isinstance(ref["project_id"], str)
            or not ref["project_id"].strip() or not all(c.isprintable() for c in ref["project_id"])):
        raise OrchestratorUnavailable("malformed_orchestrator_namespace")
    if settings.endpoint != ref["endpoint"] or ref["contract_version"] != "rig-t3-v1":
        raise OrchestratorUnavailable("orchestrator_namespace_mismatch")
    if settings.project_id is not None and settings.project_id != ref["project_id"]:
        raise OrchestratorUnavailable("orchestrator_project_mismatch")
    # IDs are stored in the namespace, never in a call's generic ledger fields.
    threads = ref["threads"]
    if not isinstance(threads, dict):
        raise OrchestratorUnavailable("malformed_orchestrator_namespace")
    token = settings.token
    for invocation, saved in threads.items():
        if (not isinstance(invocation, str) or not isinstance(saved, dict) or safe_external_ref(saved) != saved
                or token and any(token in item for item in saved.values())
                or token and token in invocation):
            raise OrchestratorUnavailable("unsafe_orchestrator_reference")
    if not unresolved_invocations(state):
        return {}
    availability, orchestrator = probe_t3(settings, factory=factory)
    try:
        if not availability.ok:
            raise OrchestratorUnavailable(availability.reason_code)
        if getattr(orchestrator, "project_id", None) != ref["project_id"]:
            raise OrchestratorUnavailable("orchestrator_project_mismatch")
        return reconnect_invocations(state, orchestrator)
    finally:
        close_orchestrator(orchestrator)
