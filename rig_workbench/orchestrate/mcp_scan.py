"""Static threat scan for rig's own MCP tools (#303).

Statically analyzes scripts/mcp_server.py's TOOLS definitions across three
adversarial lenses (attacker/defender/auditor) for shell/network
over-permission, plaintext secret exposure, and hook-injection risk. Never
executes anything — reads the TOOLS dict and source text only, deterministic,
no side effects.
"""

import ast
import importlib.util
import json
import pathlib
import re
import sys

from typing import Protocol

from ..ports import Presenter
from ..ports.local import CONSOLE
from .package_surfaces import SCRIPT_LOCATOR


class ScriptLocator(Protocol):
    """What this module needs in order to find `scripts/mcp_server.py`.

    The package is installed without a `scripts/` sibling, so finding one means searching
    for a checkout — `RIG_HOME`, then the install source, then the current directory and its
    parents — rather than computing a path relative to this file. Computing it was the bug
    (#263): a module one level deeper wrote one too few `.parent`s and skipped `RIG_HOME`
    entirely, so an installed rig reported the MCP server as "not installed" with a checkout
    sitting right where `RIG_HOME` pointed. That search is `rig_workbench.repo_paths`' rule
    and there is exactly one copy of it, which is the point.

    Stated as a protocol rather than imported, because the import is what
    `tests/test_layering_contract.py` forbids: a judgement module may reach the standard
    library, its own pillar and the six ports, and `rig_workbench.repo_paths` is none of the
    three. `package_surfaces.SCRIPT_LOCATOR` satisfies this shape and is what every shipped
    caller passes.
    """

    def find(self, name: str) -> pathlib.Path | None:
        """The repository's `scripts/<name>`, or None when no checkout holds one."""
        ...

    def expected(self, name: str) -> pathlib.Path:
        """Where `scripts/<name>` would be — the path an error message names."""
        ...

_SECRET_RE = re.compile(
    r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    r"|[Aa][Ww][Ss][A-Za-z_]*(SECRET|secret)[A-Za-z_]*\s*[=:]\s*[A-Za-z0-9/+=]{20,}"
    r"|(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
)  # same intent as scripts/git-hooks/pre-commit's PATTERN (Python port of the shell regex)
_SHELL_RISK_RE = re.compile(r"shell\s*=\s*True|os\.system\(|os\.popen\(|[^_]eval\(|[^_]exec\(")


def _isolate_default_on(source: str) -> bool:
    """Does the adapter pass `--isolate` when the caller says nothing about it? (#419)

    Read out of the adapter rather than asserted. The verdict for `rig_orchestrate_run`
    used to be a hardcoded "medium" that inspected nothing, which meant it would go on
    reporting whatever it was written to report after the default changed either way.

    Parsed rather than grepped, unlike the two regexes above. Those look for a pattern
    that is damning wherever it appears; this one looks for a pattern that *clears* the
    tool, and a text search over the whole file is far too easy to satisfy by accident —
    the same line in a comment, a docstring or an unrelated helper would buy a LOW. So
    the question asked is narrow: in the `t_orchestrate_run` that import actually leaves
    behind, what is handed to `_opt` as the value of `--isolate`, everywhere it appears?
    `ast.parse` never executes the module.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    # The *last* module-level definition, because that is the one that survives import.
    # Taking the first would let a safe-looking definition followed by an unsafe one read
    # as LOW while the unsafe one is what actually runs.
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "t_orchestrate_run"]
    if not defs:
        return False
    values = [call.args[2] for call in ast.walk(defs[-1])
              if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
              and call.func.id == "_opt" and len(call.args) == 3
              and isinstance(call.args[1], ast.Constant) and call.args[1].value == "--isolate"]
    # Every one of them, not the first: a second `--isolate` on another branch decides the
    # behaviour just as much, and one safe branch is not a statement about the function.
    return bool(values) and all(_reads_as_on_unless_false(v) for v in values)


def _reads_as_on_unless_false(expr: ast.expr) -> bool:
    """`a.get("isolate") is not False` or `a.get("isolate", True)` — and nothing else.

    A bare `a.get("isolate")` is the pre-#419 default and must not read as on. Anything
    the scan doesn't recognize reads as off: an unfamiliar spelling costs a WARN, which
    is the direction to be wrong in.
    """
    if (isinstance(expr, ast.Compare) and len(expr.ops) == 1
            and isinstance(expr.ops[0], ast.IsNot)
            and _is_isolate_get(expr.left, argc=1)
            and isinstance(expr.comparators[0], ast.Constant)
            and expr.comparators[0].value is False):
        return True
    return (_is_isolate_get(expr, argc=2)
            and isinstance(expr.args[1], ast.Constant) and expr.args[1].value is True)


def _is_isolate_get(node: ast.expr, *, argc: int) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and len(node.args) == argc
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == "isolate")


def mcp_scan(mcp_server_path: pathlib.Path | None = None, *,
             scripts: ScriptLocator = SCRIPT_LOCATOR) -> dict:
    """Statically analyze scripts/mcp_server.py's tool definitions via three-layer
    adversarial reasoning (attacker/defender/auditor).

    Never executes anything (only imports the module to read its `TOOLS` dict;
    never calls a subprocess). Returns a JSON-serializable dict shared by
    `cmd_mcp_scan` (human-readable display) and the validation package's CI check
    (judgment logic lives in exactly one place).
    """
    # Shared resolver: RIG_HOME, then the install source, then cwd. Deriving the
    # path from this file's parents pointed at a site-packages/scripts that never
    # exists, so an installed rig reported #263 as "not installed" even with
    # RIG_HOME pointing at a checkout that has it.
    path = mcp_server_path or scripts.find("mcp_server.py") \
        or scripts.expected("mcp_server.py")
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
    isolate_by_default = _isolate_default_on(source)
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
            if isolate_by_default:
                defender = ("`--isolate` is the default — the adapter adds it unless the caller passes "
                           "`isolate: false`, so an absent or null argument still isolates; merging back "
                           "out of the isolated worktree only ff-merges on DONE+clean+committed (reuses "
                           "the existing isolate mechanism as-is)")
                verdict = ("residual risk: low (isolated unless the caller explicitly opts out with "
                          "`isolate: false`, which stays available and is then the caller's own decision)")
                severity = "low"
            else:
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


def cmd_mcp_scan(args, *, out: Presenter = CONSOLE):
    result = mcp_scan()
    if "--json" in args:
        out.out(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if not result["available"]:
        out.out(f"[mcp-scan] {result['reason']}")
        sys.exit(0)  # #263 not installed means "nothing to scan", not a CI failure
    out.out(f"## rig mcp-scan — static threat analysis of {result['path']} (three-layer adversarial reasoning, #303)\n")
    out.out("### Module-level (subprocess/secret path shared by every tool)\n")
    for f in result["module_findings"]:
        out.out(f"- **{f['axis']}**")
        out.out(f"  - attacker's view: {f['attacker']}")
        out.out(f"  - defender's view: {f['defender']}")
        out.out(f"  - auditor's verdict: {f['auditor']}")
    out.out(f"\n### Tool-level ({len(result['tool_findings'])} tools)\n")
    for f in result["tool_findings"]:
        out.out(f"- `{f['tool']}` [{f['kind']}] — {f['auditor_verdict']}")
    label = {"high": "needs action (CI fails)", "medium": "needs review (CI passes, flagged)", "low": "CI passes"}
    out.out(f"\nOverall verdict: {result['overall_severity'].upper()} ({label[result['overall_severity']]})")
    sys.exit(1 if result["overall_severity"] == "high" else 0)
