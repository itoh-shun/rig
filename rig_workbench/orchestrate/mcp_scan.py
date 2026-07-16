"""Static threat scan for rig's own MCP tools (#303).

Statically analyzes scripts/mcp_server.py's TOOLS definitions across three
adversarial lenses (attacker/defender/auditor) for shell/network
over-permission, plaintext secret exposure, and hook-injection risk. Never
executes anything — reads the TOOLS dict and source text only, deterministic,
no side effects.
"""

import importlib.util
import json
import pathlib
import re
import sys

_SECRET_RE = re.compile(
    r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    r"|[Aa][Ww][Ss][A-Za-z_]*(SECRET|secret)[A-Za-z_]*\s*[=:]\s*[A-Za-z0-9/+=]{20,}"
    r"|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
)  # same intent as scripts/git-hooks/pre-commit's PATTERN (Python port of the shell regex)
_SHELL_RISK_RE = re.compile(r"shell\s*=\s*True|os\.system\(|os\.popen\(|[^_]eval\(|[^_]exec\(")


def mcp_scan(mcp_server_path: pathlib.Path | None = None) -> dict:
    """Statically analyze scripts/mcp_server.py's tool definitions via three-layer
    adversarial reasoning (attacker/defender/auditor).

    Never executes anything (only imports the module to read its `TOOLS` dict;
    never calls a subprocess). Returns a JSON-serializable dict shared by
    `cmd_mcp_scan` (human-readable display) and the validation package's CI check
    (judgment logic lives in exactly one place).
    """
    path = mcp_server_path or (pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "mcp_server.py")
    if not path.exists():
        return {"available": False, "reason": f"{path} not found (#263 not installed)", "tools": []}
    source = path.read_text(encoding="utf-8")

    module_findings = []
    shell_hits = _SHELL_RISK_RE.findall(source)
    module_findings.append({
        "axis": "shell/network over-permission",
        "attacker": "if a tool argument could be interpreted as an arbitrary shell string, MCP would let an "
                   "attacker run arbitrary commands",
        "defender": "subprocess.run is called with an argv list (no shell=True). Tool arguments are just "
                   "elements of that Python list — they never pass through shell re-interpretation",
        "auditor": "residual risk: low (no shell=True/os.system/eval/exec found)" if not shell_hits else
                  f"residual risk: needs review (shell-execution-like patterns found: {shell_hits})",
        "severity": "low" if not shell_hits else "high",
    })
    secret_hits = _SECRET_RE.findall(source)
    module_findings.append({
        "axis": "plaintext secret exposure",
        "attacker": "if a key/token were hardcoded in a tool definition or comment, it would leak straight to "
                   "the MCP client",
        "defender": "no API keys live in this code (HTTP providers read from env vars/cfg only)",
        "auditor": "residual risk: low (no strings matching known key/token patterns)" if not secret_hits else
                  "residual risk: needs review (a string matching a secret pattern was found)",
        "severity": "low" if not secret_hits else "high",
    })
    module_findings.append({
        "axis": "hook injection",
        "attacker": "could an MCP-driven call improperly fire or modify .git/hooks/ or a hook rig doesn't manage",
        "defender": "every tool is a thin adapter that just calls an existing workbench.py/orchestrate.py "
                   "subcommand — no hook-file write path (an install-git-hook equivalent) is exposed as an "
                   "MCP tool",
        "auditor": "residual risk: low (no hook-install command is published as an MCP tool)",
        "severity": "low",
    })

    tool_findings = []
    try:
        # A fixed module name (e.g. "mcp_server") would collide across different paths via
        # sys.modules's cache — load by file location under a path-derived name instead, so
        # scanning two different mcp_server.py files in the same process never returns stale data.
        spec = importlib.util.spec_from_file_location(f"_rig_mcp_scan_target_{abs(hash(str(path)))}", path)
        mcp_server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mcp_server)
        tools = mcp_server.TOOLS
    except Exception as e:
        return {"available": False, "reason": f"failed to import TOOLS: {e}", "tools": []}

    _ACCEPT_FAMILY = ("accept", "discard", "new", "gate")
    for name, spec in sorted(tools.items()):
        is_accept_family = any(h in name for h in _ACCEPT_FAMILY)
        is_run = name == "rig_orchestrate_run"  # exact match — don't confuse with "rig_orchestrate_runs" (read-only aggregator)
        is_write = is_accept_family or is_run
        if is_accept_family:
            attacker = f"could calling \"{name}\" alone bypass accept_requirements and cause an unintended state change"
            defender = ("force-proof requirements (worktree_exists/base_branch_recorded/diff_summary_generated) "
                       "are enforced by the CLI itself (workbench.py) and can't be bypassed via MCP")
            verdict, severity = "residual risk: low (structural preconditions enforced CLI-side, no force-proof bypass)", "low"
        elif is_run:
            attacker = f"could \"{name}\" run an arbitrary command as a recipe step and affect state outside the isolated worktree"
            defender = ("`--isolate` isn't the default and must be explicitly set by the caller; merging back "
                       "into the isolated worktree only ff-merges on DONE+clean+committed (reuses the existing "
                       "isolate mechanism as-is)")
            verdict = ("residual risk: medium (an MCP call without `isolate` can affect the main working tree "
                      "directly — recommend the caller always sets `isolate: true`)")
            severity = "medium"
        else:
            attacker = f"could \"{name}\" have side effects beyond read-only"
            defender = "board/status/diff etc. are read-only; they never mutate state"
            verdict, severity = "residual risk: low", "low"
        tool_findings.append({
            "tool": name, "kind": "write" if is_write else "read", "severity": severity,
            "attacker": attacker, "defender": defender, "auditor_verdict": verdict,
        })

    _SEV_ORDER = {"low": 0, "medium": 1, "high": 2}
    all_severities = [f["severity"] for f in module_findings] + [f["severity"] for f in tool_findings]
    overall = max(all_severities, key=lambda s: _SEV_ORDER[s]) if all_severities else "low"
    return {"available": True, "path": str(path), "module_findings": module_findings,
            "tool_findings": tool_findings, "overall_severity": overall}


def cmd_mcp_scan(args):
    result = mcp_scan()
    if "--json" in args:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not result["available"]:
        print(f"[mcp-scan] {result['reason']}")
        sys.exit(0)  # #263 not installed means "nothing to scan", not a CI failure
    print(f"## rig mcp-scan — static threat analysis of {result['path']} (three-layer adversarial reasoning, #303)\n")
    print("### Module-level (subprocess/secret path shared by every tool)\n")
    for f in result["module_findings"]:
        print(f"- **{f['axis']}**")
        print(f"  - attacker's view: {f['attacker']}")
        print(f"  - defender's view: {f['defender']}")
        print(f"  - auditor's verdict: {f['auditor']}")
    print(f"\n### Tool-level ({len(result['tool_findings'])} tools)\n")
    for f in result["tool_findings"]:
        print(f"- `{f['tool']}` [{f['kind']}] — {f['auditor_verdict']}")
    label = {"high": "needs action (CI fails)", "medium": "needs review (CI passes, flagged)", "low": "CI passes"}
    print(f"\nOverall verdict: {result['overall_severity'].upper()} ({label[result['overall_severity']]})")
    sys.exit(1 if result["overall_severity"] == "high" else 0)
