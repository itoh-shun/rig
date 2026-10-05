"""Pure manifest validation, shared by execution and diagnostics."""
from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit

MISSING = object()


@dataclass(frozen=True)
class OrchestratorConfig:
    preferred: str = "auto"
    fallback: str = "native"
    url: str | None = None
    project_id: str | None = None


def valid_endpoint(value: object) -> bool:
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    try:
        url = urlsplit(value)
        host = url.hostname
        # Validate the port too: urlsplit otherwise accepts nonnumeric/out-of-range ports.
        url.port
        if not host or url.username is not None or url.password is not None or "?" in value or "#" in value:
            return False
        if url.scheme == "https":
            return True
        if url.scheme != "http":
            return False
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_orchestrator_config(value=MISSING) -> tuple[str, ...]:
    """Return key names and reasons, never any supplied values (including secrets)."""
    if value is MISSING:
        return ()
    if not isinstance(value, Mapping):
        return ("orchestrator: must be a mapping",)
    errors = []
    if any(key not in {"preferred", "fallback", "t3"} for key in value):
        errors.append("orchestrator: unknown subkey")
    for key, allowed in (("preferred", {"auto", "native", "t3"}), ("fallback", {"native", "none"})):
        if key in value and (not isinstance(value[key], str) or value[key] not in allowed):
            errors.append(f"orchestrator.{key}: invalid choice")
    if "t3" in value:
        t3 = value["t3"]
        if not isinstance(t3, Mapping):
            errors.append("orchestrator.t3: must be a mapping")
        else:
            if any(key not in {"url", "project_id"} for key in t3):
                errors.append("orchestrator.t3: unknown subkey")
            if "url" in t3 and not valid_endpoint(t3["url"]):
                errors.append("orchestrator.t3.url: HTTPS or loopback HTTP endpoint required; credentials, query and fragment prohibited")
            if "project_id" in t3:
                project = t3["project_id"]
                if not isinstance(project, str) or not project.strip() or any(ord(c) < 32 or ord(c) == 127 for c in project):
                    errors.append("orchestrator.t3.project_id: nonempty string without control characters required")
    return tuple(errors)


def parse_orchestrator_config(value=MISSING) -> OrchestratorConfig:
    errors = validate_orchestrator_config(value)
    if errors:
        raise ValueError("; ".join(errors))
    if value is MISSING:
        return OrchestratorConfig()
    t3 = value.get("t3", {})
    return OrchestratorConfig(value.get("preferred", "auto"), value.get("fallback", "native"),
                              t3.get("url"), t3.get("project_id"))
