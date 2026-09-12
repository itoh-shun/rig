"""The capability registry against the CLI people actually type (v3 tasks 8, 9 and 10).

Stage 2 declares every capability in `rig_workbench/registry/`, and stage 2 changes no
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
only literals in this file are the four verbs held out of help on purpose (task 10),
where the literals *are* the assertion.

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

*The top level* — from source, not from help, and that difference is task 10's subject.
Help lists every dispatchable verb but the four below, so it cannot be the reader here.
`main()` in `rig_workbench/cli.py` is parsed with `ast`: every `sub == "<verb>"` branch plus
every member of the `_orch_delegates` set it falls through to. `--version` is dropped, being a
flag spelling of `version` rather than a verb of its own.

## What this file freezes now: four verbs hidden on purpose (task 9, then task 10)

It used to freeze a gap. A gap is what a set of dispatchable-but-undocumented verbs is while
nobody has looked at them, and for two stages that was the honest word: thirteen verbs
answered when typed and appeared in neither `rig-wb --help` nor the frozen contract in
tests/test_cli_surface_contract.py, and the literal below existed so the number could not
drift unnoticed.

Task 10 looked at them, one at a time, and the literal changed meaning rather than size
alone. Nine went into `--help` — for each of those nine, some document or test already
handed a person the `rig-wb <verb>` spelling, so the omission was in the help text and
nowhere else. Not one was removed: unlike `list` and `review` below, every one of the
thirteen had a document or a test behind it, so the DROP that closed those two was available
to none of these.

What is left is four verbs — `graph`, `install-shim`, `models`, `probe` — that stay
dispatchable and stay out of the help text on purpose, each because another rig surface
reaches it under another spelling. They are a dict of verb to reason, not a set of names,
so the reason is data this file asserts on rather than a comment that can be deleted with
the suite still green; the set the equality check uses is derived from its keys. The
assertion is doing a different job than it was: it no longer records an unmade decision, it
holds a made one. A fifth verb going undocumented fails here, and so does one of these four
being quietly advertised or quietly removed — and each one is also *run*, because nothing
else in the suite spawns them, and hiding a verb must not be how it stops working.

They were fifteen. Two of them, `list` and `review`, were worse than undocumented: both sat
in `_orch_delegates` and neither was ever added to orchestrate's `COMMANDS`, so `rig-wb list`
printed the orchestrator's module docstring and exited 1 — a verb the dispatcher accepted and
nothing implemented. Stage 3 took that decision in the removal direction, so the two names are
gone from `_orch_delegates` and from the registry, and the tests that pinned their dead
behaviour went with them: there is no longer a behaviour to pin. What now catches the shape
of that bug is
`test_every_verb_delegated_to_the_orchestrator_is_a_command_the_orchestrator_registers`, which
demands the delegate set carry no name `COMMANDS` lacks — so a third dead verb fails here
rather than being frozen as a known gap. `rig-wb wb review` is a different verb under a
different parent (it records a per-persona verdict) and was never in question.

One more disagreement is recorded rather than asserted away: `validate` is in
`_orch_delegates` and in no `COMMANDS` dict, but `main()` answers `sub == "validate"` in an
earlier branch, so the delegate entry is unreachable rather than dead. It is excepted by name
where the delegates are checked, with the reason written at the exception.
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

# Dispatchable, and left out of `rig-wb --help` deliberately — the four survivors of task
# 10's verb-by-verb audit, each kept because another rig surface reaches it under another
# spelling. It was thirteen before nine of them earned a help line, and fifteen before
# `list` and `review` left the dispatcher.
#
# A dict rather than a set of names with the reasons in comments beside them, which is what
# this was first written as. The reason is the whole substance of the decision — what makes
# one of these defensible is particular to it, and a reader deciding whether to advertise
# one needs that sentence, not the count — and a comment carrying substance can be deleted
# with every test still green. As values they are data this file asserts on, so emptying one
# fails rather than passes quietly.
TOP_LEVEL_VERBS_HIDDEN_WITH_REASON = {
    "graph": (
        "`rig-wb validate`'s check_graph spawns `scripts/orchestrate.py graph --json` "
        "(rig_workbench/validation/catalog.py; the argv is pinned in "
        "tests/test_validation_catalog_ports.py), and `/rig:catalog --graph` is the entry "
        "a person is given. Advertising `rig-wb graph` would offer a third spelling of "
        "machinery."
    ),
    "install-shim": (
        "Installs an entry point for people who have none. Anyone who can type `rig-wb "
        "install-shim` already has the entry point it provides, and a bare invocation "
        "writes into `~/.local/bin` — outside the repo, from a verb the help text would "
        "have invited."
    ),
    "models": (
        "Configures the `orchestrate` surface for `run --auto-model`, which reads what "
        "`models --save` wrote. Nothing spells it `rig-wb models`, and that is the reason. "
        "Its `--help` also answers with the orchestrator's whole ninety-line docstring, "
        "but that belongs to `_usage_for`'s fallback and not to this verb: the advertised "
        "`queue` does the same, which is pinned in tests/test_cli_smoke.py as a defect "
        "owed a fix."
    ),
    "probe": (
        "Cited eight times across the two READMEs, always as `scripts/orchestrate.py "
        "probe`, because what it evidences is about that process: the read-only verifier "
        "sandbox is applied per provider. `selftest` covers the same ground on this "
        "surface and is advertised. Its `--help` falls back like `models`, and for the "
        "same shared reason."
    ),
}

#: The same four, as the set the equality assertion below compares against. Derived from the
#: dict rather than written a second time, so a verb can only be added or removed by writing
#: or deleting its reason.
TOP_LEVEL_VERBS_MISSING_FROM_HELP = frozenset(TOP_LEVEL_VERBS_HIDDEN_WITH_REASON)

# `validate` is delegated to orchestrate and orchestrate has no command by that name, but
# `main()` answers `sub == "validate"` in a branch several lines before the delegation and
# hands it to `scripts/validate.py`. So the delegate entry is unreachable rather than dead,
# and it is the one name the check below excepts — by name and with the reason, not by a
# blanket subtraction of every branched verb, because a branch is what makes this one
# harmless and a future dead delegate may well have no branch at all.
DELEGATE_UNREACHABLE_BECAUSE_A_BRANCH_ANSWERS_FIRST = frozenset({"validate"})

# Removed from `_orch_delegates` rather than implemented, because each reached no handler.
# Kept here so the refusal a person now meets is pinned, not merely assumed.
_REMOVED_TOP_LEVEL_VERBS = frozenset({"list", "review"})


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

    Source rather than `--help`, because the two disagree by every verb in
    TOP_LEVEL_VERBS_MISSING_FROM_HELP, which help leaves out on purpose (task 10). `main()`
    is a chain of `if sub == "<verb>"` branches ending in `if sub in _orch_delegates`; this
    is the first shape. `--version` is dropped: it is the flag spelling of `version`,
    handled in the same branch, not a verb of its own.

    Kept apart from the delegated set because the order of the chain matters. A verb in both
    is served by its branch and never reaches the orchestrator — `validate` is exactly that
    today, which is why it is the one name in
    DELEGATE_UNREACHABLE_BECAUSE_A_BRANCH_ANSWERS_FIRST even though orchestrate has no
    `validate` command either.
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

    `rig-wb --help` is short of what `main()` accepts by exactly
    TOP_LEVEL_VERBS_MISSING_FROM_HELP, so comparing the registry against help would fail for
    a reason that is nothing to do with drift. What the
    registry claims to describe is what a person can *run*, so what a person can run is what
    it is compared against; what help deliberately leaves out is task 10's own test below.
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


# ── task 10: the verbs no help text mentions, and why each one ───────────────

def test_the_verbs_missing_from_the_help_text_are_exactly_the_ones_kept_hidden_on_purpose(
        rig_wb):
    """Hidden on purpose is a claim, and this is where it is held to the exact set.

    Every verb `main()` dispatches is either advertised in `rig-wb --help` or named in
    TOP_LEVEL_VERBS_MISSING_FROM_HELP with the reason it is not. There is no third
    category, which is the point: a verb wired into `main()` without a help entry fails
    here rather than joining an unexamined remainder, and it can only be quieted by writing
    down why — next to the name, where the next reader will argue with it.

    It fails in the other direction too. One of the four gaining a help line fails here, and
    so does one of them leaving the dispatcher, because either is a decision about the
    command surface and neither should be discoverable from a user's bug report.
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
        f"  now advertised (welcome, and delete them from the literal, taking the reason "
        f"written beside each with them): "
        f"{sorted(TOP_LEVEL_VERBS_MISSING_FROM_HELP - undocumented)}\n"
        f"  newly unadvertised (a verb was wired up without a line in `_print_help`): "
        f"{sorted(undocumented - TOP_LEVEL_VERBS_MISSING_FROM_HELP)}\n"
        "  A verb in the second list belongs in `_print_help`, which is what task 10 did "
        "for nine of these. Adding it to the literal instead is the right answer only when "
        "another rig surface is the way in and this spelling should stay unadvertised — and "
        "then the reason goes beside the name, because that is the part a later reader "
        "needs in order to disagree with it."
    )

    assert documented <= registry, (
        "`rig-wb --help` advertises verbs the capability registry does not declare: "
        f"{sorted(documented - registry)}. Help is prose and the registry is the "
        "declaration; a verb documented but undeclared is the drift this file exists to "
        "catch, in its most user-visible direction."
    )


@pytest.mark.parametrize("verb", sorted(TOP_LEVEL_VERBS_HIDDEN_WITH_REASON))
def test_a_verb_kept_out_of_the_help_text_still_answers_its_own_help(verb, rig_wb):
    """Hidden is not dropped, and only running them says which one this is.

    These four are the one part of the command surface `tests/test_cli_surface_contract.py`
    cannot reach: that file spawns `--help` for every verb it freezes, and freezing these
    would mean advertising them. So nothing was spawning them at all, and a verb whose
    module stopped importing or whose parser stopped building would have gone on passing
    every test in the suite — the decision recorded above would have quietly become the
    other one, removal, with no line of the diff saying so.

    The same cheapest-possible proof the contract file uses: `--help` touches the dispatcher
    and the parser and then exits, doing none of the verb's work. `install-shim` is here on
    that argv and no other — run bare it writes a symlink into `~/.local/bin`, outside the
    repo and outside anything a fixture can clean up, which is part of why it is hidden.
    """
    result = rig_wb(verb, "--help")

    assert result.returncode == 0, (
        f"`rig-wb {verb} --help` exited {result.returncode}. It is kept out of the help "
        "text deliberately and is still expected to work; if the verb is meant to be gone, "
        "this test and its entry in TOP_LEVEL_VERBS_HIDDEN_WITH_REASON go with it.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.stdout.strip(), f"`rig-wb {verb} --help` printed nothing"


def test_every_verb_held_out_of_the_help_text_carries_a_reason_somebody_wrote():
    """A reason that can be deleted with the suite still green is not a record of anything.

    This is the failure the dict shape exists to catch, and it is the likely one: the names
    are load-bearing everywhere else in this file, so nobody drops one by accident, while a
    reason is the part a hurried edit strips. Emptying one now fails here, by name.

    It checks that a sentence is present, not that it is a good one — no test can do the
    second — so the reasons stay review's business. What it removes is the case where there
    is nothing for review to look at.
    """
    missing = sorted(
        verb for verb, reason in TOP_LEVEL_VERBS_HIDDEN_WITH_REASON.items()
        if not isinstance(reason, str) or not reason.strip()
    )
    assert not missing, (
        f"these verbs are held out of `rig-wb --help` with no reason recorded: {missing}. "
        "Being hidden is a decision, and the sentence saying why is the whole of what was "
        "decided — without it the entry says only that somebody once left the verb out."
    )


def test_every_verb_delegated_to_the_orchestrator_is_a_command_the_orchestrator_registers(
):
    """No name in `_orch_delegates` may be missing from orchestrate's `COMMANDS`.

    `list` and `review` used to be exactly that — accepted by the dispatcher, implemented by
    nobody, answering with ninety lines of orchestrate's module docstring and exit 1 — and
    this file froze the pair as a known gap. They were removed instead, so the assertion
    turns around: the set is now empty and is required to stay empty, which catches a third
    verb falling into the same hole on the commit that digs it rather than a stage later.

    Read statically from both ends — the `_orch_delegates` set literal in
    `rig_workbench/cli.py` against the `COMMANDS` dict literal in
    `rig_workbench/orchestrate/cli.py` — so a name can be caught before anyone runs it.

    `validate` is excepted by name: it is in `_orch_delegates`, orchestrate has no command by
    that name either, but `main()` answers `sub == "validate"` in a branch several lines
    earlier and hands it to `scripts/validate.py`. The delegate entry is unreachable rather
    than broken. The exception is one named verb rather than "any verb with a branch", so a
    future dead delegate that happens to share a branch cannot slip through with it.
    """
    delegated = _orch_delegates()
    implemented = _dict_keys_literal(
        ast.parse(ORCHESTRATE_CLI_SOURCE.read_text(encoding="utf-8")), "COMMANDS")
    dead = delegated - implemented - DELEGATE_UNREACHABLE_BECAUSE_A_BRANCH_ANSWERS_FIRST
    assert dead == frozenset(), (
        "these top-level verbs are delegated to the orchestrator with nothing behind them: "
        f"{sorted(dead)}.\n"
        "  `rig-wb <verb>` accepts each one, falls through to orchestrate, matches no command "
        "and answers with that module's docstring and exit 1 — no mention of the word the "
        "person typed. Either register the name in orchestrate's COMMANDS or take it out of "
        "`_orch_delegates` (and out of rig_workbench/registry/entries_cli.py with it), which "
        "is what was done to `list` and `review`."
    )
    assert DELEGATE_UNREACHABLE_BECAUSE_A_BRANCH_ANSWERS_FIRST <= _branched_top_level_verbs(), (
        "`validate` is excepted above because a `sub == \"validate\"` branch answers before "
        "the delegation is reached. That branch is gone, so the exception now hides a dead "
        "verb rather than recording a harmless one."
    )


@pytest.mark.parametrize("verb", sorted(_REMOVED_TOP_LEVEL_VERBS))
def test_a_verb_removed_from_the_dispatcher_is_refused_by_name_instead_of_half_answered(
        verb, rig_wb):
    """`rig-wb list` and `rig-wb review` now say what happened, in one line, on stderr.

    The old behaviour is what this replaces and is worth naming: exit 1, ninety lines of
    orchestrate's module docstring on stdout, nothing on stderr, and no mention of the word
    typed. What a person meets now is `main()`'s unknown-sub-command path — exit 2, the verb
    quoted back, and a pointer to `--help`. Pinned so that re-adding either name to
    `_orch_delegates` without a command behind it fails here.

    `rig-wb wb review` is untouched and is checked alongside, because it is the live verb
    these two are easiest to confuse with: removing a top-level name must not reach it.
    """
    result = rig_wb(verb)
    assert result.returncode == 2, (
        f"`rig-wb {verb}` exited {result.returncode}, not 2. It was removed from the "
        "dispatcher, so it should be refused as an unknown sub-command.\n"
        f"--- stdout ---\n{result.stdout[:400]}\n--- stderr ---\n{result.stderr}"
    )
    assert f"Unknown sub-command: {verb!r}" in result.stderr, result.stderr
    assert not result.stdout.strip(), (
        f"`rig-wb {verb}` writes to stdout. Measured, it writes nothing at all: the whole "
        "answer is the two stderr lines above. The failure this guards against is the verb "
        "reaching the orchestrator again and dumping its module docstring, but the "
        "assertion is emptiness rather than the absence of that one string — a refusal "
        f"should not print a page of anything.\n--- stdout ---\n{result.stdout[:400]}"
    )


def test_the_workbench_review_verb_is_untouched_by_the_top_level_removal(rig_wb):
    """`rig-wb wb review` records a per-persona verdict and is a different verb.

    Two verbs spelled `review` existed: a top-level delegate that reached no handler, and
    this one, under `wb`, which `scripts/workbench.py` implements and which the engine's
    flows call. Only the first was removed. This runs the second to say so.
    """
    result = rig_wb("wb", "review", "--help")
    assert result.returncode == 0, result.stderr
    assert "--set" in result.stdout, (
        "`rig-wb wb review --help` no longer shows its `--set PERSONA=VERDICT` flag; the "
        f"live workbench verb has been disturbed.\n--- stdout ---\n{result.stdout}"
    )
    assert "review" in _registry_verbs("wb"), (
        "the registry no longer declares `wb review`; the top-level removal reached the "
        "wrong entry."
    )
