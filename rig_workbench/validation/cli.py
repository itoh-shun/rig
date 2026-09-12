"""validation cli: entry point / report printing (split from scripts/validate.py).

rig structure validator (for CI)

Mechanically checks shipped-tier recipe frontmatter, step references, extends
chains, and persona frontmatter.
Implements the (1)(2)(3) (+ (3)-b persona schema) subset of the --validate
instruction (facets/instructions/validate.md).
No Claude required — runs entirely on the filesystem.

Exit code: 0=pass / 1=has FAIL

This module is `validation`'s **shell**, and stage 3 of
`docs/v3-architecture-design-brief.ja.md` §3 asks the same two things of it that
`govern/cli.py` and `packs/cli.py` already answer.

**Four adapters, built once and forwarded.** `cmd_validate` takes a `Presenter`, a
`ProcessRunner`, an `Env` and a `Clock`; `main()` builds one of each at the process
boundary and hands them in; and each is passed on to every call below whose signature
declares it — `run_selftest(out=…)`, `check_wiki(clock=…)`, `check_graph(proc=…, env=…)`
— and to nothing else. That is the rule `govern/cli.py` states and
`tests/test_validation_forwarded_ports.py` checks: a handler that takes a port and then
calls into the judgement layer without passing it on leaves the *callee's default* in
charge, and the callee's default is the real adapter, so the command quietly runs on two
of them.

**Words leave through the `Presenter`.** No function here calls `print`: the report
header, every accumulated result line, the tally and the verdict all go through `out`, and
so does the one line a missing PyYAML produces. Which stream a line goes to is unchanged —
everything this file said went to stdout and still does, `out.out`. The one line this file
did not say before goes to `out.err`: the refusal of an unknown flag, in the form
`gh_requirement.py` already uses for the same condition (`[ERROR] <command>: unknown flag
…`), on stderr so a caller piping the report is not handed a usage line in the middle
of it. A module-level adapter
reached for from inside any of them would be the same global under a different name, and
the point of the port is that a caller (a test, an embedding harness) can hand in a
different one.

**`main()` still takes no arguments and still ends in `sys.exit`, on purpose.**
`rig_workbench/cli.py:_run_validate` loads `scripts/validate.py` with `importlib`,
replaces `sys.argv` and calls `.main()`, and `tests/test_capability_registry_vs_cli.py`
freezes that dispatch shape. So the argv-taking, status-returning half is `cmd_validate`
and `main()` is the three lines that turn a process into a call — exactly the split
`govern/cli.py` and `packs/cli.py` use, reached from the other direction.
"""

import sys
import traceback

from rig_workbench.ports import Clock, Env, Presenter, ProcessRunner
from rig_workbench.ports.local import ConsolePresenter, OsEnv, SubprocessRunner, SystemClock

from . import state
from .accumulated import check_accumulated
from .catalog import (check_catalog_drift, check_packs_catalog, check_workbench_catalog,
                      check_workbench_routing, check_graph, check_wiki)
from .config import RECIPES
from .drill import (check_corpus_integrity, check_drill_coverage,
                    check_fixture_corpus_integrity)
from .manifest import check_manifest
from .mcp_scan import check_mcp_scan
from .personas import check_agents, check_commands, check_personas
from .recipes import check_extends_cycles, check_needs_cycles, check_recipe
from .release import check_release_metadata, check_skills_lock
from .routes import check_route_producers
from .skills_spec import check_skills_spec
from .selftest import run_selftest
from .stale_refs import check_stale_refs
from .state import _emit
from .yaml_adapter import PyYAMLMissing, require_yaml


#: `validate`'s whole surface, as `--help` prints it. Written out rather than derived
#: from the registry: this module is loaded by `importlib` from `scripts/validate.py`
#: with `rig_workbench` on `sys.path` and nothing else assumed, and a `--help` that
#: imports the capability table to answer would be a second thing that can fail before
#: it can tell anybody how to use the first.
USAGE = (
    "usage: rig-wb validate [selftest]",
    "",
    "Mechanically check the shipped tier — recipe frontmatter, step references, extends",
    "chains, persona/command/agent schemas, catalogs, the graph, and the manifest — and",
    "print a PASS / WARN / FAIL report. Reads the tree; writes nothing.",
    "",
    "  selftest    run the golden self-verification of the orchestrator instead",
    "  -h, --help  print this and exit, without running any check",
    "",
    "exit: 0 = no FAIL (there may be WARNs) / 1 = at least one FAIL / 2 = unknown flag",
)


# ── main ─────────────────────────────────────────────────────────────
def cmd_validate(argv: list[str], *, out: Presenter = ConsolePresenter(),
                 proc: ProcessRunner = SubprocessRunner(), env: Env = OsEnv(),
                 clock: Clock = SystemClock()) -> int:
    """Run the validator and return its exit status.

    The four ports are parameters rather than module-level instances this function reaches
    for: `main()` builds an adapter apiece at the process boundary and passes them in, and
    an in-process caller may hand in its own. The defaults exist so those callers keep
    working unchanged. Each is forwarded to every call below whose signature declares it,
    which is the rule the module docstring states and the tripwire checks.
    """
    if any(arg in ("-h", "--help") for arg in argv):
        # Answered before anything else this function does, including `require_yaml`:
        # `validate --help` used to be ignored as an unrecognised argument and run the
        # whole validator — 92 lines of report and about a second of filesystem walking
        # where usage belonged, and a 0 that said "no FAIL" rather than "here is how to
        # use this". Nothing below this line is reached, so `--help` answers on a machine
        # without PyYAML and reads nothing off the disk.
        for line in USAGE:
            out.out(line)
        return 0

    unknown = [arg for arg in argv if arg.startswith("-")]
    if unknown:
        # Measured before this landed: `rig-wb validate --bogus` dropped the flag, walked
        # the tree, printed 92 lines of report and exited 0 — a status that says "no FAIL"
        # to a CI step that asked for something this command does not have. Answered where
        # `--help` is answered, above `require_yaml` and above any read, so a misspelt flag
        # costs a line instead of a second and cannot be mistaken for a verdict.
        #
        # FLAGS ONLY, on purpose. `selftest` is the one word this command takes and a bare
        # run is the other shape; a token that is neither is left exactly as it was (the
        # validator runs), because nothing measured says a stray positional is a typo
        # rather than a caller's habit. A leading `-` cannot be either of those two.
        flags = ", ".join(repr(flag) for flag in unknown)
        out.err(f"[ERROR] validate: unknown flag {flags}; {USAGE[0]}")
        return 2

    try:
        # The pillar's one optional dependency. This used to be a `try/except ImportError`
        # at the top of `state.py` that printed and called `sys.exit(1)` during the import
        # itself; it is a call here so the shell can report it like anything else it
        # reports. Same text, same stream (stdout), same status.
        require_yaml()
    except PyYAMLMissing as missing:
        out.out(f"[ERROR] {missing}")
        return 1

    if argv and argv[0] == "selftest":
        # `run_selftest` ends in `sys.exit` itself and the codes are contract; the
        # `return` below is unreachable and is here so the signature stays honest.
        run_selftest(out=out)
        return 0

    recipe_files = sorted(RECIPES.glob("*.md"))
    if not recipe_files:
        out.out("[WARN] no .md files found in recipes/")
        return 0

    for recipe_path in recipe_files:
        try:
            check_recipe(recipe_path)
        except Exception:
            _emit("FAIL", f"recipe {recipe_path.stem} — unexpected error:\n{traceback.format_exc()}")

    try:
        check_route_producers()
    except Exception:
        _emit("FAIL", f"route producer check — unexpected error:\n{traceback.format_exc()}")

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
        check_skills_spec()
    except Exception:
        _emit("FAIL", f"skills spec check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_catalog_drift()
    except Exception:
        _emit("FAIL", f"§2 catalog drift check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_workbench_routing()
    except Exception:
        _emit("FAIL", f"workbench routing check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_workbench_catalog()
    except Exception:
        _emit("FAIL", f"workbench catalog check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_packs_catalog()
    except Exception:
        _emit("FAIL", f"packs catalog check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_wiki(clock=clock)
    except Exception:
        _emit("FAIL", f"wiki hygiene check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_graph(proc=proc, env=env)
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
        check_corpus_integrity()
    except Exception:
        _emit("FAIL", f"drill corpus check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_fixture_corpus_integrity()
    except Exception:
        _emit("FAIL", f"drill fixture corpus check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_release_metadata()
    except Exception:
        _emit("FAIL", f"release metadata check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_skills_lock()
    except Exception:
        _emit("FAIL", f"skills-lock check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_mcp_scan()
    except Exception:
        _emit("FAIL", f"mcp-scan check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_stale_refs()
    except Exception:
        _emit("FAIL", f"stale-refs check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_accumulated()
    except Exception:
        _emit("FAIL", f"accumulated/ schema check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_manifest()
    except Exception:
        _emit("FAIL", f"manifest check — unexpected error:\n{traceback.format_exc()}")

    out.out("## rig --validate report (CI / shipped tier)\n")
    for line in state.results:
        out.out(line)
    out.out()
    out.out(f"PASS: {state._pass} / WARN: {state._warn} / FAIL: {state._fail}")

    if state._fail > 0:
        out.out("\nFAILED: one or more FAIL results")
        return 1
    if state._warn > 0:
        out.out("\nPASSED (with WARNs to address)")
    else:
        out.out("\nPASSED")
    return 0


def main() -> None:
    sys.exit(cmd_validate(sys.argv[1:], out=ConsolePresenter(), proc=SubprocessRunner(),
                          env=OsEnv(), clock=SystemClock()))


if __name__ == "__main__":
    main()
