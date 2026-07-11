"""validation cli: entry point / report printing (split from scripts/validate.py).

rig structure validator (for CI)

Mechanically checks shipped-tier recipe frontmatter, step references, extends
chains, and persona frontmatter.
Implements the (1)(2)(3) (+ (3)-b persona schema) subset of the --validate
instruction (facets/instructions/validate.md).
No Claude required — runs entirely on the filesystem.

Exit code: 0=pass / 1=has FAIL
"""

import sys
import traceback

from . import state
from .catalog import check_catalog_drift, check_graph, check_wiki
from .config import RECIPES
from .drill import check_drill_coverage
from .personas import check_agents, check_commands, check_personas
from .recipes import check_extends_cycles, check_needs_cycles, check_recipe
from .release import check_release_metadata, check_skills_lock
from .selftest import run_selftest
from .state import _emit


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        run_selftest()
        return

    recipe_files = sorted(RECIPES.glob("*.md"))
    if not recipe_files:
        print("[WARN] no .md files found in recipes/")
        sys.exit(0)

    for recipe_path in recipe_files:
        try:
            check_recipe(recipe_path)
        except Exception:
            _emit("FAIL", f"recipe {recipe_path.stem} — unexpected error:\n{traceback.format_exc()}")

    try:
        check_personas()
    except Exception:
        _emit("FAIL", f"persona schema check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_commands()
    except Exception:
        _emit("FAIL", f"commands check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_agents()
    except Exception:
        _emit("FAIL", f"agents check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_catalog_drift()
    except Exception:
        _emit("FAIL", f"§2 catalog drift check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_wiki()
    except Exception:
        _emit("FAIL", f"wiki hygiene check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_graph()
    except Exception:
        _emit("FAIL", f"graph consistency check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_extends_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"extends cycle check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_needs_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"needs cycle check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_drill_coverage(recipe_files)
    except Exception:
        _emit("FAIL", f"drill coverage check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_release_metadata()
    except Exception:
        _emit("FAIL", f"release metadata check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_skills_lock()
    except Exception:
        _emit("FAIL", f"skills-lock check — unexpected error:\n{traceback.format_exc()}")

    print("## rig --validate report (CI / shipped tier)\n")
    for line in state.results:
        print(line)
    print()
    print(f"PASS: {state._pass} / WARN: {state._warn} / FAIL: {state._fail}")

    if state._fail > 0:
        print("\nFAILED: one or more FAIL results")
        sys.exit(1)
    elif state._warn > 0:
        print("\nPASSED (with WARNs to address)")
    else:
        print("\nPASSED")
