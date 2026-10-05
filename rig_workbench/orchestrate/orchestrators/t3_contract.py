"""One tool binding table for the Rig fake contract; no live revision is verified.

SDK transport compatibility does not certify these external tool schemas. This
fixture contract is intentionally unavailable in production until a real T3
revision, identities, workspace binding and role constraints are verified.
"""
from dataclasses import dataclass
import math
from collections.abc import Mapping

CONTRACT_VERSION = "rig-t3-v1"
VERIFIED_T3_REVISIONS = frozenset()


def _schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties if required is None else required), "additionalProperties": False}


def _identity_schema():
    return {"thread_id": {"type": "string"}, "run_id": {"type": "string"}}


def _encode(arguments, timeout_s):
    return dict(arguments)


def _encode_wait(arguments, timeout_s):
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("invalid operation deadline")
    return {**arguments, "timeout_ms": max(1, math.ceil(timeout_s * 1000))}


def _decode(response):
    if not isinstance(response, Mapping):
        raise ValueError("invalid tool response")
    return dict(response)


@dataclass(frozen=True)
class ToolBinding:
    name: str
    input_schema: Mapping[str, object]
    encoder: object = _encode
    decoder: object = _decode
    required: bool = True

    def compatible(self, advertised):
        # Exact fake fixture matching rejects extra required inputs, type changes,
        # unknown compositions/ref indirections and default-changing schemas.
        return isinstance(advertised, Mapping) and dict(advertised) == dict(self.input_schema)


T3_TOOL_BINDINGS = {
    "capabilities": ToolBinding("orchestrator_capabilities", _schema({})),
    "launch": ToolBinding("t3_thread_launch", _schema({
        "prompt": {"type": "string"}, "provider_instance": {"type": "string"},
        "provider": {"type": "string"}, "model": {"type": ["string", "null"]},
        "role": {"type": "string", "enum": ["generator", "verifier"]},
        "persona": {"type": "string"}, "cwd": {"type": "string"},
        "project_id": {"type": "string"}, "constraints": {"type": "object"},
        "new_thread": {"type": "boolean", "const": True},
    })),
    "wait": ToolBinding("t3_thread_wait", _schema({**_identity_schema(), "timeout_ms": {"type": "integer", "minimum": 1}}), _encode_wait),
    "read": ToolBinding("t3_thread_read", _schema({**_identity_schema(), "cursor": {"type": "string"}}, ["thread_id", "run_id"])),
    "interrupt": ToolBinding("t3_thread_interrupt", _schema(_identity_schema())),
    "list": ToolBinding("t3_thread_list", _schema({"project_id": {"type": "string"}})),
    "configure": ToolBinding("t3_thread_configure", _schema({}), required=False),
    "delegate": ToolBinding("delegate_task", _schema({}), required=False),
    "batch": ToolBinding("create_threads", _schema({}), required=False),
}


def validate_tools(tools):
    if not isinstance(tools, Mapping):
        return ("invalid_tool_catalog",)
    return tuple("incompatible_tool_schema" for binding in T3_TOOL_BINDINGS.values()
                 if binding.required and not binding.compatible(tools.get(binding.name)))


def call_logical(client, operation, arguments, *, timeout_s):
    binding = T3_TOOL_BINDINGS[operation]
    return binding.decoder(client.call_tool(binding.name, binding.encoder(arguments, timeout_s), timeout_s=timeout_s))
