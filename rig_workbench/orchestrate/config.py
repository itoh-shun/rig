"""orchestrate config: module-level constants/paths (split from scripts/orchestrate.py)."""

import os
import pathlib
from rig_workbench import gitroot


def _env_path(name: str, default: pathlib.Path) -> pathlib.Path:
    value = os.environ.get(name)
    return pathlib.Path(value).expanduser().resolve() if value else default


# Claude Code derives a plugin's data directory as `<plugin>-<marketplace>`. Names
# tried, in order: itoshun-local-plugins -> sito-plugins -> {sito-plugins, rig}.
# `sito-plugins` now lives in a dedicated marketplace-only repo rather than this one, so
# it stays first (still the shared, recommended install path); `rig` is this repo's own
# self-hosted single-plugin marketplace, added as a fallback so a direct install (option
# B in the README) also resolves. Every prior name stays in the tuple so an install made
# before either rename still finds its state — dropping one would orphan that data.
PLUGIN_DATA_DIRS = ("rig-sito-plugins", "rig-rig", "rig-itoshun-local-plugins")


# The engine skill's directory name. Claude Code derives a plugin skill's
# invocation id from this directory name (not SKILL.md's frontmatter `name:`),
# so `skills/engine/` is what makes `rig:engine` invocable. `rig` is the
# pre-rename layout, kept so a plugin dir installed before the rename still
# resolves (the pip package and the plugin can be at different versions).
SKILL_DIR_NAMES = ("engine", "rig")


def _skill_root(base: pathlib.Path) -> pathlib.Path | None:
    """The engine skill's directory inside `base`, or None if `base` isn't a rig home."""
    for name in SKILL_DIR_NAMES:
        if (base / "skills" / name / "SKILL.md").exists():
            return base / "skills" / name
    return None


def find_rig_home() -> pathlib.Path:
    """Resolve where the rig assets (skills/, .claude-plugin/) live.
    Priority: $RIG_HOME -> ~/.claude/plugins/data/{rig-sito-plugins, rig-rig, rig-itoshun-local-plugins}
    -> parent of __file__ (dev fallback).
    Cross-project use resolves automatically via the plugin install path, i.e. independent of the caller's cwd."""
    if env := os.environ.get("RIG_HOME"):
        p = pathlib.Path(env).expanduser()
        if _skill_root(p):
            return p
    for data_dir in PLUGIN_DATA_DIRS:
        installed = pathlib.Path.home() / ".claude" / "plugins" / "data" / data_dir
        if _skill_root(installed):
            return installed
    return pathlib.Path(__file__).resolve().parent.parent.parent


RIG_HOME = find_rig_home()
SKILL_ROOT = _skill_root(RIG_HOME) or RIG_HOME / "skills" / "engine"
RECIPES = SKILL_ROOT / "recipes"
PERSONAS = SKILL_ROOT / "facets" / "personas"
INVOCATION_CWD = pathlib.Path(os.getcwd()).resolve()
#: Where this repository keeps what it keeps once. `INVOCATION_CWD` answers a different
#: question — which directory the caller started in — and everything gitignored under
#: `.rig/` is one set per repository, not one per working tree. Bound to the invocation
#: directory, a `queue go` or an `orchestrate run` from inside a task worktree read and
#: wrote a queue, a run log and a recipe overlay that nothing else would ever look at
#: (#471). Outside a repository it is the invocation directory, which is where the rest of
#: this module already assumes it is.
#:
#: Resolved on access rather than frozen at import. It is *derived from* `INVOCATION_CWD`,
#: and freezing a derived value silently breaks that relationship for anyone who changes
#: what it derives from — which is precisely what every test that points rig at a temporary
#: project does. Cached per invocation directory, so the git call happens once.
_STATE_ROOT_CACHE: dict[pathlib.Path, pathlib.Path] = {}


def _state_root() -> pathlib.Path:
    cwd = INVOCATION_CWD
    if cwd not in _STATE_ROOT_CACHE:
        _STATE_ROOT_CACHE[cwd] = gitroot.main_worktree(cwd) or cwd
    return _STATE_ROOT_CACHE[cwd]


#: The paths derived from the state root, resolved the same way it is. Leaving these frozen
#: while their root moved was an incoherence of my own making: a process or test that rebinds
#: `INVOCATION_CWD` would get a new state root and these still pointing at the old one, which
#: is worse than either rule applied consistently. Assigning to any of them still shadows
#: this, so the environment overrides and the tests that set them directly are untouched.
_DERIVED = {
    "PROJECT_RECIPES": lambda: _state_root() / ".rig" / "recipes",
    "RUNS_PATH": lambda: _env_path("RIG_RUNS_PATH", _state_root() / ".rig" / "runs.jsonl"),
    "DRILL_PATH": lambda: _state_root() / ".rig" / "drill-results.jsonl",
    "QUEUE_PATH": lambda: _state_root() / ".rig" / "queue.json",
}


def __getattr__(name: str):
    # PEP 562. These are not stored attributes, so reading one asks the question again;
    # assigning to it (as a test does) shadows this and is still honoured.
    if name == "STATE_ROOT":
        return _state_root()
    if name in _DERIVED:
        return _DERIVED[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


GLOBAL_RUNS_PATH = _env_path(
    "RIG_GLOBAL_RUNS_PATH",
    pathlib.Path.home() / ".rig" / "runs.jsonl",
)
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
