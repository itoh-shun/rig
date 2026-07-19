"""orchestrate config: module-level constants/paths (split from scripts/orchestrate.py)."""

import os
import pathlib

def find_rig_home() -> pathlib.Path:
    """Resolve where the rig assets (skills/, .claude-plugin/) live.
    Priority: $RIG_HOME -> ~/.claude/plugins/data/rig-itoshun-local-plugins -> parent of __file__ (dev fallback).
    Cross-project use resolves automatically via the plugin install path, i.e. independent of the caller's cwd."""
    if env := os.environ.get("RIG_HOME"):
        p = pathlib.Path(env).expanduser()
        if (p / "skills" / "rig" / "SKILL.md").exists():
            return p
    installed = pathlib.Path.home() / ".claude" / "plugins" / "data" / "rig-itoshun-local-plugins"
    if (installed / "skills" / "rig" / "SKILL.md").exists():
        return installed
    return pathlib.Path(__file__).resolve().parent.parent.parent


RIG_HOME = find_rig_home()
RECIPES = RIG_HOME / "skills" / "rig" / "recipes"
PERSONAS = RIG_HOME / "skills" / "rig" / "facets" / "personas"
INVOCATION_CWD = pathlib.Path(os.getcwd()).resolve()
PROJECT_RECIPES = INVOCATION_CWD / ".rig" / "recipes"  # project overlay
RUNS_PATH = INVOCATION_CWD / ".rig" / "runs.jsonl"     # run telemetry (an execution log on par with run-state)
GLOBAL_RUNS_PATH = pathlib.Path.home() / ".rig" / "runs.jsonl"  # cross-project mirror (rebindable, e.g. in tests)
DRILL_PATH = INVOCATION_CWD / ".rig" / "drill-results.jsonl"  # measured /rig:drill results (detection rate)
DEFAULT_K = 2  # default acceptance-gate retry limit (SKILL §3.5)
