"""orchestrate config: module-level constants/paths (split from scripts/orchestrate.py)."""

import os
import pathlib


def _env_path(name: str, default: pathlib.Path) -> pathlib.Path:
    value = os.environ.get(name)
    return pathlib.Path(value).expanduser().resolve() if value else default


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
RUNS_PATH = _env_path("RIG_RUNS_PATH", INVOCATION_CWD / ".rig" / "runs.jsonl")
GLOBAL_RUNS_PATH = _env_path(
    "RIG_GLOBAL_RUNS_PATH",
    pathlib.Path.home() / ".rig" / "runs.jsonl",
)
DRILL_PATH = INVOCATION_CWD / ".rig" / "drill-results.jsonl"  # measured /rig:drill results (detection rate)
DEFAULT_K = 2  # default acceptance-gate retry limit (SKILL §3.5)


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int from the environment; fall back on empty/invalid."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


# Convergence budget (opt-in, model-invariance lever): when set > 0, it raises the
# effective per-step retry cap so a run keeps feeding the distilled previous_failure
# (#333) back to the generator for more attempts before escalating. A weaker model
# thus gets more chances to converge on a gate-passing result instead of stopping —
# which is how the harness makes the *accepted* outcome less dependent on the model
# (measured by `rig-wb bench-invariance`). Unset/0 leaves all behavior unchanged; it
# only ever *raises* a step's K, never lowers a recipe's explicit max_retries.
CONVERGENCE_K = _env_int("RIG_CONVERGENCE_K", 0)


def effective_k(step_max_retries: int | None) -> int:
    """Resolve a step's retry cap: its own value (or DEFAULT_K), raised to the
    convergence budget when one is set. Pure except for the module-level env read."""
    base = step_max_retries or DEFAULT_K
    return max(base, CONVERGENCE_K)
