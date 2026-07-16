"""CI wiring for orchestrate's MCP static threat scan (#303).

Silently skips when scripts/mcp_server.py isn't present, matching the other
opt-in checks' policy. Judgment logic is centralized in
rig_workbench.orchestrate.mcp_scan.mcp_scan() — this only maps severity to
FAIL/WARN/PASS, never re-implementing it.
"""

from .config import ROOT
from .state import _emit


def check_mcp_scan() -> None:
    mcp_server_path = ROOT / "scripts" / "mcp_server.py"
    if not mcp_server_path.is_file():
        return
    from rig_workbench.orchestrate.mcp_scan import mcp_scan

    result = mcp_scan(mcp_server_path)
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
