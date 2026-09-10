"""The capability registry against the CLI people actually type (v3 stage 2, tasks 8 and 9).

Stage 2 declares all 139 capabilities in `rig_workbench/registry/`, and stage 2 changes no
execution. `rig-wb` still dispatches through the three hand-written registration mechanisms
`docs/v3-architecture-design-brief.ja.md` §2 counts — an if/elif chain in
`rig_workbench/cli.py`, ~51 `add_parser` calls in `rig_workbench/workbench/cli.py`, a
`COMMANDS` dict in `rig_workbench/orchestrate/cli.py` — plus the govern / pack / eval /
baseline / githooks parsers. That is two sources of truth for one surface, deliberately and
for one stage only: generating either side from the other is rewiring, which is stage 3, and
introspecting argparse to build the table would make the CLI the source of truth again, which
§9 rejects in as many words ("正本は意図であり、CLI はその投影である").

So the two are held together *by test*, and this file is that test. It is the whole defence
against the registry and the CLI drifting apart while both are hand-maintained.

**Nothing here is a frozen copy of the verb list.** Both sides are read at run time — the
registry by import, the CLI out of the real `python -m rig_workbench.cli` process (or, at the
top level, out of the dispatcher's own source) — and compared for equality. A verb that
appears on one side and not the other fails here with a message naming which side has it. The
only literals in this file are the known gap of task 9, where the literals *are* the
assertion.

**The reader below is deliberately a second, independent one.**
`tests/test_cli_surface_contract.py` parses the same `--help` output for a different purpose,
and none of its parsing is imported or shared here. Two independent readings of one surface
are what made the fifteen-verb gap visible in the first place; collapsing them into one shared
helper would throw that away, because a bug in the shared helper would then agree with itself.

## How each surface is read

*The five argparse groups* (`wb`, `govern`, `pack`, `eval`, `baseline`) — from the real
process. `<group> --help` prints its subparser choices as `{a,b,c} ...` in the usage block,
and that trailing `...` is argparse's own rendering of a subparser rather than of a plain
`choices=` positional (`--scope {project,user,org}` and `[{show,lint}]` do not get it). So the
brace group followed by `...` is the one read, at both levels.

*`githooks`* — also from the real process, but it is not argparse: it prints a hand-written
`Usage:` block and parses argv itself, which is why `githooks install --help` exits 2 rather
than printing help (pinned in tests/test_cli_surface_contract.py). Its verbs are read out of
that block, and its verbs are never probed for nesting because there is no parser to nest.

*Two-word verbs.* `pack source list|add|remove` is declared in the registry as three two-word
verbs, and `govern policy` / `approve` / `waiver` / `audit` as one word each, because the
first is an argparse sub-subparser with `required=True` while the others take an optional
positional (`rig_workbench/registry/entries_subgroups.py` explains the split). This reader
does not take the registry's word for that. Every verb whose own `--help` shows a nested
subparser is probed by running it bare: if the process refuses it as incomplete, only the
two-word paths are real; if it runs, the one-word path is real too and is kept alongside.
Today exactly one verb in the whole surface is shaped that way, and it refuses.

*The top level* — from source, not from help, and that difference is task 9's subject.
`rig-wb --help` lists 24 verbs while 39 are dispatchable, so help cannot be the reader here.
`main()` in `rig_workbench/cli.py` is parsed with `ast`: every `sub == "<verb>"` branch plus
every member of the `_orch_delegates` set it falls through to. `--version` is dropped, being a
flag spelling of `version` rather than a verb of its own.

## The known gap this file freezes (task 9)

Fifteen top-level verbs are dispatchable and appear in neither `rig-wb --help` nor the frozen
contract in tests/test_cli_surface_contract.py. They are written out as literals below and
asserted to be *exactly* that set.

Reducing that number to zero is a stage-3 decision — either the fifteen join the help text, or
the ones nobody wants are removed from the dispatcher — and it is not made here. Until it is
made, this test exists so the number cannot drift unnoticed: a sixteenth undocumented verb, or
one of these fifteen quietly disappearing, fails rather than passes silently.

Two of the fifteen are worse than undocumented. `list` and `review` sit in `_orch_delegates`
but were never added to orchestrate's `COMMANDS`, so `rig-wb list` prints the orchestrator's
module docstring and exits 1 — a verb the dispatcher accepts and nothing implements. That is
asserted here from both ends, statically and by running them, so that a dead verb starting to
work and a live one dying both fail this file.

One more disagreement is recorded rather than asserted away: `validate` is in
`_orch_delegates` and in no `COMMANDS` dict, but `main()` answers `sub == "validate"` in an
earlier branch, so the delegate entry is unreachable rather than dead. It is subtracted where
the dead verbs are counted, with the reason written at the subtraction.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

import pytest

from rig_workbench.registry import PARENTS, children

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOP_LEVEL_CLI_SOURCE = REPO_ROOT / "rig_workbench" / "cli.py"
ORCHESTRATE_CLI_SOURCE = REPO_ROOT / "rig_workbench" / "orchestrate" / "cli.py"

# Measured, not guessed: `rig-wb wb gate --help` — the heaviest of these, because the
# workbench parser builds all 51 subparsers — costs 0.21s on a developer machine, and every
# other probe here is under 0.1s. The floor is what the suite's other subprocess call sites
# use, and RIG_TEST_TIMEOUT_FACTOR is the same knob conftest exposes for a slower runner.
HELP_MEASURED_SECONDS = 0.3
SUBPROCESS_TIMEOUT = max(
    30.0, HELP_MEASURED_SECONDS * float(os.environ.get("RIG_TEST_TIMEOUT_FACTOR", "6"))
)

# argparse renders a nested subparser as `{a,b,c} ...` and nothing else in this surface gets
# the trailing ellipsis — a `choices=` flag prints `--scope {project,user,org}` and an
# optional `choices=` positional prints `[{show,lint}]`. So this pattern means "a level of
# nesting", at the group level and one level further down, and it is the only thing read.
SUBPARSER_CHOICES = re.compile(r"\{([^{}]+)\}\s+\.\.\.")

# The fifteen of task 9. Dispatchable, documented nowhere. See the module docstring: this is
# a recorded gap awaiting a stage-3 decision, not an approval of the gap.
TOP_LEVEL_VERBS_MISSING_FROM_HELP = frozenset({
    "approve", "bench-invariance", "check", "fleet", "graph", "init", "install-shim",
    "list", "models", "next", "otel", "perf", "probe", "review", "verdict",
})

# Delegated to orchestrate by `rig_workbench/cli.py` and absent from orchestrate's own
# `COMMANDS`, so the dispatcher accepts them and nothing implements them.
DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED = frozenset({"list", "review"})


# ── reading the CLI, through the real process ────────────────────────────────

@pytest.fixture
def rig_wb(tmp_path):
    """Run `rig-wb` as a process and hand back the CompletedProcess, judged by nobody.

    Its own runner rather than the suite's `rig_cli` fixture for the reason the module
    docstring gives: this file is the second, independent reader of the CLI surface, and a
    reader that borrows the other reader's machinery is not independent of it. Defaults to
    `tmp_path` so nothing it runs can see or touch the repo's own `.rig/`.
    """

    def run(*argv, cwd=None):
        env = dict(
            os.environ,
            # The tree under test, not whatever `rig-wb` happens to be installed.
            PYTHONPATH=os.pathsep.join(
                path for path in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if path
            ),
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
        )
        return subprocess.run(
            [sys.executable, "-m", "rig_workbench.cli", *argv],
            cwd=str(cwd if cwd is not None else tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=SUBPROCESS_TIMEOUT,
        )

    return run


def _usage_block(help_text: str) -> str:
    """argparse's wrapped usage, rejoined into one line.

    argparse breaks the usage across lines at whatever width the terminal claims, so the
    subcommand choices and the `...` that identifies them as a subparser routinely land on
    different lines. Rejoining first means the reader does not depend on the wrapping.
    """
    parts, inside = [], False
    for line in help_text.splitlines():
        if line.startswith("usage:"):
            inside = True
        elif inside and not line.strip():
            break
        if inside:
            parts.append(line.strip())
    return " ".join(parts)


def _subparser_verbs(help_text: str) -> tuple[str, ...]:
    """The verbs of the one nested subparser in this help, or `()` when it has none."""
    matches = SUBPARSER_CHOICES.findall(_usage_block(help_text))
    if not matches:
        return ()
    assert len(matches) == 1, (
        "expected one level of subparser nesting in this usage block, found "
        f"{len(matches)}: {matches}. A second level means the surface grew a shape this "
        "reader was not written for; teach it the new shape rather than taking the first."
    )
    return tuple(matches[0].split(","))


def _githooks_verbs(help_text: str) -> tuple[str, ...]:
    """Verbs out of githooks' hand-written `Usage:` block.

    `githooks` never reaches argparse — it reads argv itself — so there is no choice list to
    find. Its usage block is a run of `rig-wb githooks <verb> ...` lines, ending at the first
    blank line, and the third word of each is the verb.
    """
    verbs, inside = [], False
    for line in help_text.splitlines():
        if line.strip() == "Usage:":
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            break
        words = line.split()
        if words[:2] == ["rig-wb", "githooks"] and len(words) > 2:
            verbs.append(words[2])
    return tuple(verbs)


def _top_level_help_verbs(help_text: str) -> tuple[str, ...]:
    """Verbs out of the top level's hand-written `Sub-commands:` block.

    Same shape as githooks and for the same reason — `rig_workbench/cli.py` prints prose and
    dispatches by hand — but laid out as one entry per two-space-indented line, with
    continuation lines indented further, so those are skipped rather than read as verbs.
    """
    verbs, inside = [], False
    for line in help_text.splitlines():
        if line.startswith("Sub-commands:"):
            inside = True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        if not line.startswith("  "):  # a new section at column 0 closes the block
            break
        if line.startswith("   "):  # a continuation of the entry above
            continue
        verbs.append(line.split()[0])
    return tuple(verbs)


def _cli_verbs_under(parent: str, rig_wb) -> frozenset[str]:
    """Every verb path a person can actually type under `rig-wb <parent>`.

    One word per verb, except where the surface nests a level `registry.PARENTS` does not
    name. Nesting is decided by asking the process twice, never by consulting the registry:
    `<parent> <verb> --help` says whether a sub-subparser exists, and running `<parent>
    <verb>` bare says whether it is required. A required one means the one-word form is not a
    command at all — it is a usage error — so only the two-word paths are returned for it.
    """
    group_help = rig_wb(parent, "--help")
    assert group_help.returncode == 0, (
        f"`rig-wb {parent} --help` exited {group_help.returncode}; the CLI side of this "
        f"comparison could not be read at all.\n--- stderr ---\n{group_help.stderr}"
    )

    if parent == "githooks":
        # Hand-written usage, hand-rolled argv parsing, and its verbs reject `--help`
        # outright — so there is nothing here to probe for nesting.
        verbs = _githooks_verbs(group_help.stdout)
        assert verbs, (
            "read no verbs out of `rig-wb githooks --help`; its hand-written Usage: block "
            f"has changed shape.\n--- stdout ---\n{group_help.stdout}"
        )
        return frozenset(verbs)

    verbs = _subparser_verbs(group_help.stdout)
    assert verbs, (
        f"read no subcommand choices out of `rig-wb {parent} --help`; the CLI side of this "
        f"comparison could not be read at all.\n--- stdout ---\n{group_help.stdout}"
    )

    paths: set[str] = set()
    for verb in verbs:
        verb_help = rig_wb(parent, verb, "--help")
        assert verb_help.returncode == 0, (
            f"`rig-wb {parent} {verb} --help` exited {verb_help.returncode}, so whether it "
            f"nests could not be read.\n--- stderr ---\n{verb_help.stderr}"
        )
        nested = _subparser_verbs(verb_help.stdout)
        if not nested:
            paths.add(verb)
            continue
        paths.update(f"{verb} {inner}" for inner in nested)
        bare = rig_wb(parent, verb)
        incomplete = bare.returncode == 2 and "required" in bare.stderr
        if not incomplete:
            # An optional sub-subparser: `rig-wb <parent> <verb>` is a complete command in
            # its own right, so it is a verb too and the two-word paths sit beside it.
            paths.add(verb)
    return frozenset(paths)


# ── reading the top-level dispatcher, out of its source ──────────────────────

def _set_literal(tree: ast.Module, name: str) -> frozenset[str]:
    """A module-level `name = {"a", "b"}` read as data."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets and isinstance(node.value, ast.Set):
            return frozenset(
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    raise AssertionError(f"no module-level `{name} = {{...}}` set literal to read")


def _dict_keys_literal(tree: ast.Module, name: str) -> frozenset[str]:
    """A module-level `name = {"a": ..., "b": ...}` read for its keys alone."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets and isinstance(node.value, ast.Dict):
            return frozenset(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise AssertionError(f"no module-level `{name} = {{...}}` dict literal to read")


def _orch_delegates() -> frozenset[str]:
    """The verbs `rig_workbench/cli.py` hands straight to the orchestrator."""
    return _set_literal(ast.parse(TOP_LEVEL_CLI_SOURCE.read_text(encoding="utf-8")),
                        "_orch_delegates")


def _branched_top_level_verbs() -> frozenset[str]:
    """Verbs `main()` handles in a branch of its own, read out of `main()`.

    Source rather than `--help`, because the two disagree by fifteen verbs and help is the
    side that is wrong (task 9). `main()` is a chain of `if sub == "<verb>"` branches ending
    in `if sub in _orch_delegates`; this is the first shape. `--version` is dropped: it is
    the flag spelling of `version`, handled in the same branch, not a verb of its own.

    Kept apart from the delegated set because the order of the chain matters. A verb in both
    is served by its branch and never reaches the orchestrator — `validate` is exactly that
    today, which is why it is not counted among the dead verbs further down even though
    orchestrate has no `validate` command either.
    """
    tree = ast.parse(TOP_LEVEL_CLI_SOURCE.read_text(encoding="utf-8"))
    main = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    assert main is not None, f"no `def main()` in {TOP_LEVEL_CLI_SOURCE}"

    compared: set[str] = set()
    delegate_branch = False
    for node in ast.walk(main):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "sub"):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    compared.add(comparator.value)
            elif isinstance(op, ast.In) and isinstance(comparator, ast.Name):
                if comparator.id == "_orch_delegates":
                    delegate_branch = True

    assert compared, (
        f"no `sub == \"<verb>\"` branches found in {TOP_LEVEL_CLI_SOURCE}'s main(); the "
        "dispatcher has been restructured and this reader no longer sees what it accepts. "
        "Fix the reader — an empty read here would silently pass every comparison below."
    )
    assert delegate_branch, (
        f"no `sub in _orch_delegates` branch found in {TOP_LEVEL_CLI_SOURCE}'s main(); the "
        "orchestrator delegation has moved and twenty verbs would be read as unreachable."
    )
    return frozenset(verb for verb in compared if not verb.startswith("-"))


def _dispatched_top_level_verbs() -> frozenset[str]:
    """Every verb `rig-wb <verb>` accepts: its own branch, or the fall-through to orchestrate."""
    return _branched_top_level_verbs() | _orch_delegates()


# ── the comparison ───────────────────────────────────────────────────────────

def _registry_verbs(parent: str | None) -> frozenset[str]:
    return frozenset(capability.verb for capability in children(parent))


def _drift(surface: str, registry: frozenset[str], cli: frozenset[str]) -> str:
    """What to say when the two sides disagree, and to whom.

    The natural mistake when this test goes red is to edit the registry until it is green,
    because the registry is the side that is easy to edit. It is the side that is *supposed*
    to be right (§9: intent is the source of truth), so the message says which side each
    stray verb is on and what each case means before it says anything else.
    """
    lines = [f"the capability registry and the real `{surface}` surface disagree."]
    only_registry = sorted(registry - cli)
    only_cli = sorted(cli - registry)
    if only_registry:
        lines.append(
            f"  declared in rig_workbench/registry/ but NOT offered by the CLI: "
            f"{only_registry}\n"
            "    Either the verb was removed from the CLI and its entry should go with it, "
            "or the entry names a verb that never existed. The registry records what rig can "
            "do; a capability nobody can invoke is not one of them."
        )
    if only_cli:
        lines.append(
            f"  offered by the CLI but declared NOWHERE in rig_workbench/registry/: "
            f"{only_cli}\n"
            "    Add an entry for each (id, parent, verb, intent, preconditions, "
            "effect_line, effect_class, network, exit_codes) in the matching entries module. "
            "Deleting the verb from the CLI to make this pass takes a capability away from "
            "people who have it."
        )
    lines.append(
        "  This test holds no list of its own: both sides are read at run time, the registry "
        "by import and the CLI out of the real process. There is nothing here to edit — the "
        "fix is on whichever of the two sides is wrong."
    )
    return "\n".join(lines)


@pytest.mark.parametrize("parent", sorted(PARENTS))
def test_each_parent_group_offers_exactly_the_verbs_the_registry_declares_under_it(
        parent, rig_wb):
    """The six grouped surfaces, read from the process and compared for equality.

    Equality, not containment, in both directions: a verb added to a parser without an entry
    fails, and an entry for a verb the parser no longer has fails too. Stage 2 leaves the
    parsers in charge of execution, so this is the only thing keeping the declaration honest.
    """
    registry = _registry_verbs(parent)
    cli = _cli_verbs_under(parent, rig_wb)
    assert registry == cli, _drift(f"rig-wb {parent}", registry, cli)


def test_the_verbs_the_top_level_dispatcher_accepts_are_exactly_the_ones_the_registry_declares(
):
    """The top level, read from the dispatcher's source rather than from its help.

    `rig-wb --help` is fifteen verbs short of what `main()` accepts, so comparing the
    registry against help would fail for a reason that is nothing to do with drift. What the
    registry claims to describe is what a person can *run*, so what a person can run is what
    it is compared against; the shortfall in help is task 9's own test below.
    """
    registry = _registry_verbs(None)
    dispatched = _dispatched_top_level_verbs()
    assert registry == dispatched, _drift("rig-wb <verb>", registry, dispatched)


def test_pack_source_is_read_as_three_two_word_verbs_because_its_sub_subparser_is_required(
        rig_wb):
    """Why `pack source list` is a verb and `pack source` is not, checked against the process.

    The registry declares three two-word verbs here and one-word verbs for `govern policy`
    and friends. That asymmetry is a claim about argparse — `required=True` versus an
    optional positional — and it is worth proving against the real CLI rather than against
    `entries_subgroups.py`'s prose, because the equality test above would pass just as
    happily if both sides were wrong in the same way.
    """
    incomplete = rig_wb("pack", "source")
    assert incomplete.returncode == 2, (
        "`rig-wb pack source` no longer refuses to run without a sub-verb. If its "
        "sub-subparser became optional, `pack source` is now a command in its own right and "
        "needs its own capability entry beside the three two-word ones."
    )
    assert "required" in incomplete.stderr, incomplete.stderr

    complete = rig_wb("govern", "policy", "--help")
    assert complete.returncode == 0, complete.stderr
    assert not _subparser_verbs(complete.stdout), (
        "`rig-wb govern policy` has grown a sub-subparser. Its second word used to be an "
        "optional positional, which is why the registry declares `govern policy` as one "
        "verb; if that changed, the entry has to split the way `pack source` did."
    )


# ── task 9: the fifteen verbs no help text mentions ──────────────────────────

def test_exactly_fifteen_dispatchable_top_level_verbs_are_missing_from_the_help_text(rig_wb):
    """The known gap, frozen at fifteen so it cannot grow or shrink unnoticed.

    Reducing this to zero is a stage-3 decision — put them in the help text, or take the
    unwanted ones out of the dispatcher — and this test does not make it. It makes the number
    impossible to change by accident: a new verb wired into `main()` without a help entry
    turns this red, and so does one of the fifteen vanishing.
    """
    result = rig_wb("--help")
    assert result.returncode == 0, result.stderr
    documented = frozenset(_top_level_help_verbs(result.stdout))
    assert documented, (
        "read no verbs out of `rig-wb --help`; its hand-written Sub-commands: block has "
        f"changed shape.\n--- stdout ---\n{result.stdout}"
    )

    registry = _registry_verbs(None)
    undocumented = registry - documented
    assert undocumented == TOP_LEVEL_VERBS_MISSING_FROM_HELP, (
        "the set of dispatchable-but-undocumented top-level verbs has changed.\n"
        f"  no longer undocumented (welcome, and delete them from the literal): "
        f"{sorted(TOP_LEVEL_VERBS_MISSING_FROM_HELP - undocumented)}\n"
        f"  newly undocumented (a verb was wired up without a line in `_print_help`): "
        f"{sorted(undocumented - TOP_LEVEL_VERBS_MISSING_FROM_HELP)}\n"
        "  Adding a verb to the literal is the right fix only when the verb is genuinely new "
        "and the gap is genuinely accepted; the intended direction is the other one, and it "
        "is a stage-3 decision (see this module's docstring)."
    )

    assert documented <= registry, (
        "`rig-wb --help` advertises verbs the capability registry does not declare: "
        f"{sorted(documented - registry)}. Help is prose and the registry is the "
        "declaration; a verb documented but undeclared is the drift this file exists to "
        "catch, in its most user-visible direction."
    )


def test_the_two_delegated_verbs_orchestrate_never_registered_are_still_exactly_list_and_review(
):
    """`rig-wb list` and `rig-wb review` are accepted by the dispatcher and implemented by
    nobody.

    Read statically from both ends — `_orch_delegates` in `rig_workbench/cli.py` against
    `COMMANDS` in `rig_workbench/orchestrate/cli.py` — so this catches the gap growing as
    well as it catches the gap closing. A live verb dropping out of `COMMANDS` shows up here
    as a third dead verb, which is the failure worth having.

    `validate` is subtracted rather than counted: it is listed in `_orch_delegates` and
    orchestrate has no command by that name either, but `main()` answers `sub == "validate"`
    in a branch several lines earlier and hands it to `scripts/validate.py`, so the delegate
    entry is unreachable rather than broken. Recorded here rather than asserted away, because
    an unreachable entry is a real (if harmless) piece of drift inside the dispatcher.
    """
    delegated = _orch_delegates()
    implemented = _dict_keys_literal(
        ast.parse(ORCHESTRATE_CLI_SOURCE.read_text(encoding="utf-8")), "COMMANDS")
    dead = delegated - implemented - _branched_top_level_verbs()
    assert dead == DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED, (
        "the set of top-level verbs delegated to the orchestrator with nothing behind them "
        "has changed.\n"
        f"  now implemented (good — drop them from the literal): "
        f"{sorted(DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED - dead)}\n"
        f"  newly dead (a verb was removed from orchestrate's COMMANDS but is still "
        f"delegated to it): {sorted(dead - DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED)}\n"
        "  Every one of these is a verb `rig-wb` accepts and then answers with a module "
        "docstring and exit 1."
    )
    assert dead <= _dispatched_top_level_verbs()


@pytest.mark.parametrize("verb", sorted(DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED))
def test_a_delegated_verb_with_no_command_behind_it_prints_the_module_docstring_and_exits_one(
        verb, rig_wb):
    """The static gap above, confirmed by running it — the behaviour a user actually meets.

    Pinned as what happens, not as what should happen. `orchestrate.cli.main()` falls through
    to `print(__doc__); sys.exit(1)` for any argv it does not recognise, so the person who
    typed `rig-wb list` gets ninety lines of orchestrator usage and a failure, with no
    mention of the word they typed. If either of these verbs is implemented, or removed from
    `_orch_delegates`, this fails and the decision gets recorded in the diff.
    """
    result = rig_wb(verb)
    assert result.returncode == 1, (
        f"`rig-wb {verb}` exited {result.returncode}, not 1. It is delegated to the "
        "orchestrator, which has no command by that name, so it should still be falling "
        f"through to the docstring.\n--- stdout ---\n{result.stdout[:400]}"
        f"\n--- stderr ---\n{result.stderr}"
    )
    assert "rig computational orchestrator" in result.stdout, (
        f"`rig-wb {verb}` no longer answers with orchestrate's module docstring. If the verb "
        "gained an implementation, remove it from "
        "DELEGATED_VERBS_ORCHESTRATE_NEVER_REGISTERED and from "
        f"TOP_LEVEL_VERBS_MISSING_FROM_HELP if it is documented now.\n--- stdout ---\n"
        f"{result.stdout[:400]}"
    )
    assert not result.stderr.strip(), (
        f"`rig-wb {verb}` now says something on stderr. Today it fails silently there — the "
        "whole answer is a docstring on stdout with no line naming the verb the person typed "
        f"— so a diagnostic appearing is a change worth recording.\n{result.stderr}"
    )
