"""CI wiring for orchestrate's MCP static threat scan (#303).

Silently skips when scripts/mcp_server.py isn't present, matching the other
opt-in checks' policy. The judgement is centralized in the scanner this module is
handed — `McpScanner` below says so in the signature — and this only maps severity
to FAIL/WARN/PASS, never re-implementing it.
"""

import pathlib
from typing import Protocol, runtime_checkable

from .config import ROOT
from .rig_surfaces import MCP_SCANNER
from .state import _emit


@runtime_checkable
class McpScanner(Protocol):
    """The static threat scan of an MCP server, as a verdict this module only reports.

    The docstring above already said the judgement is not made here; this states it in the
    signature. What a tool description may contain, what counts as residual risk and how
    the per-tool findings roll up to one severity is `orchestrate.mcp_scan`'s reasoning,
    reached by `rig-wb orchestrate mcp-scan` as well as by CI. All this module adds is the
    mapping from that severity to FAIL / WARN / PASS.
    """

    def __call__(self, path: pathlib.Path) -> dict:
        ...


def check_mcp_scan(*, scanner: McpScanner = MCP_SCANNER) -> None:
    mcp_server_path = ROOT / "scripts" / "mcp_server.py"
    if not mcp_server_path.is_file():
        return
    result = scanner(mcp_server_path)
    if not result["available"]:
        _emit("WARN", f"mcp-scan — {result['reason']}")
        return
    sev = result["overall_severity"]
    n_tools = len(result["tool_findings"])
    if sev == "high":
        _emit("FAIL", f"mcp-scan — overall verdict HIGH ({n_tools} tools has a residual risk needing action; "
                      "run `orchestrate.py mcp-scan` for details)")
    elif sev == "medium":
        _emit("WARN", f"mcp-scan — overall verdict MEDIUM ({n_tools} tools has a residual risk needing review; "
                      "run `orchestrate.py mcp-scan` for details)")
    else:
        _emit("PASS", f"mcp-scan — overall verdict LOW ({n_tools} tools, low residual risk under three-layer "
                      "adversarial reasoning)")
