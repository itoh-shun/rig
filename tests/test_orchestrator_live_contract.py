"""The shipped production loader refuses unverified live T3 execution."""
import json

import pytest

from rig_workbench import doctor
from rig_workbench.orchestrate.orchestrators.base import OrchestratorUnavailable
from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator
from rig_workbench.orchestrate.orchestrators.t3_client import McpT3Client
from rig_workbench.orchestrate.orchestrators.t3_contract import T3_TOOL_BINDINGS


@pytest.fixture
def connected_production_client(monkeypatch):
    """Replace network I/O while keeping the real loader's verification policy."""
    calls = []
    monkeypatch.setattr(McpT3Client, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(McpT3Client, "list_tools", lambda *args, **kwargs: {
        binding.name: binding.input_schema for binding in T3_TOOL_BINDINGS.values()
    })

    def call_tool(self, name, arguments, *, timeout_s):
        calls.append(name)
        assert name == "orchestrator_capabilities", "unverified client launched an agent"
        return {"contract_version": "rig-t3-v1", "same_checkout": True}

    monkeypatch.setattr(McpT3Client, "call_tool", call_tool)
    monkeypatch.setattr(McpT3Client, "close", lambda *args, **kwargs: None)
    assert McpT3Client.contract_verified is False
    return calls


@pytest.mark.parametrize("choice,fallback", [("auto", "native"), ("auto", "none"), ("t3", "native")])
def test_real_loader_selects_native_or_stops_after_unverified_contract(connected_production_client, choice, fallback):
    options = dict(cli=choice, env={"RIG_T3_MCP_URL": "http://localhost/mcp",
                                   "RIG_T3_MCP_TOKEN": "secret"}, emit=False)
    if choice == "auto" and fallback == "native":
        selected = select_orchestrator({"fallback": fallback}, **options)
        assert selected.name == "native"
        assert selected.availability.reason_code == "unverified_contract"
    else:
        with pytest.raises(OrchestratorUnavailable) as unavailable:
            select_orchestrator({"fallback": fallback}, **options)
        assert unavailable.value.reason_code == "unverified_contract"
    assert connected_production_client == ["orchestrator_capabilities"]


@pytest.mark.parametrize("choice,active", [("auto", "native"), ("t3", None)])
def test_doctor_reports_production_contract_limit(connected_production_client, monkeypatch, choice, active):
    class Environment:
        def get(self, key, default=None):
            return {"RIG_T3_MCP_URL": "http://localhost/mcp", "RIG_T3_MCP_TOKEN": "secret"}.get(key, default)

        def snapshot(self):
            return {}

    class Output:
        def out(self, text=""):
            self.text = text

    monkeypatch.setattr(doctor, "load_manifest", lambda **kwargs: {})
    output = Output()
    assert doctor.main(["--json", "--orchestrator", choice], env=Environment(), out=output) == 0
    data = json.loads(output.text)
    assert data["selection"]["active"] == active
    assert data["execution_backends"]["t3"]["available"] is False
    assert data["execution_backends"]["t3"]["reason_code"] == "unverified_contract"
    assert connected_production_client == ["orchestrator_capabilities"]
