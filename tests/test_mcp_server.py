"""stdlib-only MCP server so rig can be driven outside a Claude Code session (#263).

Exercises the JSON-RPC handler directly (no real stdio loop) and the thin
subprocess wrappers around workbench.py/orchestrate.py.
"""

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mcp_server", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mcp_server.py"
)
mcp_server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mcp_server)


def test_opt_skips_none_and_false():
    assert mcp_server._opt(["x"], "--flag", None) == ["x"]
    assert mcp_server._opt(["x"], "--flag", False) == ["x"]


def test_opt_adds_bare_flag_for_true():
    assert mcp_server._opt(["x"], "--flag", True) == ["x", "--flag"]


def test_opt_adds_flag_and_value():
    assert mcp_server._opt(["x"], "--flag", "v") == ["x", "--flag", "v"]


def test_initialize_returns_protocol_and_server_info():
    reply = mcp_server._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert reply["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert reply["result"]["serverInfo"] == mcp_server.SERVER_INFO


def test_tools_list_exposes_every_registered_tool():
    reply = mcp_server._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == set(mcp_server.TOOLS)
    for t in reply["result"]["tools"]:
        assert "inputSchema" in t and "description" in t


def test_notification_returns_none():
    assert mcp_server._handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_jsonrpc_error():
    reply = mcp_server._handle({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
    assert reply["error"]["code"] == -32601


def test_unknown_notification_returns_none():
    assert mcp_server._handle({"jsonrpc": "2.0", "method": "bogus"}) is None


def test_tools_call_unknown_tool_is_an_error_result():
    reply = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "does_not_exist", "arguments": {}}}
    )
    assert reply["result"]["isError"] is True


def test_tools_call_orchestrate_runs_shells_out(tmp_path, monkeypatch):
    # A real subprocess round-trip through the thin wrapper, in an empty cwd
    # (read-only command; no .rig/ state exists yet — that's the expected path).
    monkeypatch.chdir(tmp_path)
    reply = mcp_server._handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "rig_orchestrate_runs", "arguments": {}}}
    )
    assert reply["result"]["isError"] is False
    assert "No run records yet" in reply["result"]["content"][0]["text"]


@pytest.mark.parametrize("force_proof_tool", ["rig_task_accept", "rig_task_discard"])
def test_force_proof_tools_delegate_to_workbench_cli(force_proof_tool):
    # accept/discard must go through workbench.py's own code path (not reimplement
    # accept_requirements/gate checks in the MCP layer) so they can't be bypassed via MCP.
    spec = mcp_server.TOOLS[force_proof_tool]
    args = spec["fn"]({"task_id": "no-such-task"})
    assert args["ok"] is False  # workbench.py itself rejects the unknown task id
