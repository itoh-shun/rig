"""The capability table held against the surfaces §2 says should become projections of it.

`docs/v3-architecture-design-brief.ja.md` §2 names five consumers that each keep a
hand-written copy of what rig can do — the CLI, `scripts/mcp_server.py`,
`rig_workbench/remote_mcp.py`, `action.yml`, and the routing the 30 `/rig:*` commands
perform — and observes that nothing checks the copies against each other, so they drift
invisibly. Stage 2 put every capability in one table (`rig_workbench/registry/`).
`tests/test_capability_registry_vs_cli.py` holds that table and the real CLI to each
other. This file holds it to the other four surfaces, plus the two frozen tables stage 1
built.

**The frozen tables are independent counterweights and stay independent.**
`tests/test_schema_registry.py` scans source literals for `rig.<name>/v<N>` ids and pins
the set; `tests/test_exit_code_surface.py` spends a subprocess per assertion and pins what
a real process returned. Neither was written from the registry and neither may be edited
to make the registry agree. So every comparison below runs one way — the registry is held
against what those files independently recorded, never the other way round — and where the
reverse direction would be a different claim, the file says so instead of asserting it. A
registry checked against itself is a tautology, which is the failure mode both of those
files exist to eliminate.

Three consequences for how this is written:

* Both counterweights are read as they were authored. `FROZEN_SCHEMA_IDS` is an authored
  set and is imported, so a rename of it is an ImportError at collection rather than a
  silent reclassification. The exit codes were authored as `assert result.returncode == N`
  inside test bodies, not as a constant, so they are read out with `ast` — a hand-copied
  table of those numbers here would be a second copy of exactly the thing that drifts.
* Every failure names *two* surfaces and says which one to go and look at. These are
  cross-surface checks: the reader's instinct on a red run is to edit the registry, and
  for three of the four checks below that is the wrong file to open.
* Where a check cannot be made meaningful today it is recorded in a named constant with
  the reason, in the style of `NOT_PINNED` and `KNOWN_PROSE_DRIFT`, rather than written as
  an assertion that cannot fail.

Nothing here runs a rig command: every surface is enumerated statically, so this file is a
read of the tree and is safe under `-n auto`.
"""

import ast
import pathlib
import re

from conftest import REPO_ROOT
from rig_workbench.registry import CAPABILITIES, PARENTS, by_id
from test_exit_code_surface import NOT_PINNED
from test_schema_registry import FROZEN_SCHEMA_IDS

#: The surfaces this file reads. Named as paths rather than imported, because three of the
#: four are not Python modules that can be imported at all (`action.yml`, the markdown
#: commands) and the fourth (`scripts/mcp_server.py`) starts a server when run.
STDIO_MCP_SERVER = REPO_ROOT / "scripts" / "mcp_server.py"
REMOTE_MCP_SERVER = REPO_ROOT / "rig_workbench" / "remote_mcp.py"
ACTION = REPO_ROOT / "action.yml"
ACTION_ENTRYPOINT = REPO_ROOT / "scripts" / "rig-action-entrypoint.sh"
COMMANDS_DIR = REPO_ROOT / "commands"
RECIPES_DIR = REPO_ROOT / "skills" / "engine" / "recipes"
EXIT_CODE_SURFACE = REPO_ROOT / "tests" / "test_exit_code_surface.py"
ORCHESTRATE_CLI = REPO_ROOT / "rig_workbench" / "orchestrate" / "cli.py"

#: Every declared command path to the id that declares it, for the longest-prefix match
#: below. `command_path` is the words a person types, which is what all four surfaces
#: spell out — an MCP adapter's `["wb", "status", task_id]`, a slash command's
#: `rig-wb govern can accept`, the Action entrypoint's `run "$RIG_RECIPE"`.
COMMAND_PATHS = {capability.command_path: capability.id for capability in CAPABILITIES}
_LONGEST_PATH = max(len(path) for path in COMMAND_PATHS)


def capability_for(words) -> str | None:
    """The id of the capability an argv names, or `None` when the table declares none.

    Longest prefix wins, so `("govern", "can", "accept")` resolves to `govern.can` and its
    argument is not mistaken for a third word of the command. A word starting with `-` ends
    the search: everything after the first flag is arguments, never more of the verb.

    One rule keeps the longest-prefix match from swallowing a drift it should report. The
    six group words in `PARENTS` are themselves declared capabilities (`rig-wb wb` with no
    verb prints the workbench's help), so `("wb", "boards")` would otherwise fall back to
    matching the bare group and a misspelled sub-verb would resolve to something. Under a
    group, therefore, only a match that includes the sub-verb counts; a bare group matches
    only when it is the whole argv.
    """
    words = list(words)
    for index, word in enumerate(words):
        if word.startswith("-"):
            words = words[:index]
            break
    shortest = 2 if len(words) > 1 and words[0] in PARENTS else 1
    for length in range(min(_LONGEST_PATH, len(words)), shortest - 1, -1):
        found = COMMAND_PATHS.get(tuple(words[:length]))
        if found is not None:
            return found
    return None


def _leading_string_constants(node) -> list[str]:
    """The literal words at the front of a list/tuple display, stopping at the first that
    is not one. Everything after it is a runtime value — a task id, a recipe name — and
    the words before it are the command."""
    words = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            words.append(element.value)
        else:
            break
    return words


# ══ task 10: output schemas ═══════════════════════════════════════════════════
# WHY ONLY ONE DIRECTION IS ASSERTED.
#
# Every `output_schema` a capability declares must be an id tests/test_schema_registry.py
# froze. That direction is the contract: a capability claiming to emit `rig.thing/v1` when
# no such id exists in the tree is a promise no command can keep, and a consumer told to
# branch on it would wait forever.
#
# The reverse — every frozen id is declared by some capability — is NOT asserted, and must
# not be. The frozen set is the independent counterweight: it is the set of ids the *source
# trees* emit, and several of them are emitted by things that are not CLI capabilities at
# all. `rig.production-observation/v1` and `rig.knowledge-candidate/v1` name documents a
# caller hands *in*; `rig.fleet/v1` names the configuration `fleet` reads rather than
# anything it prints; `rig.mission-control/v1` and `rig.mission-worker/v1` belong to the
# web reader. Asserting the reverse would make the registry the authority over a set that
# was written to be independent of it, and the way to get such a test green is to add a
# capability that does not exist or to delete an id something really emits. Both are worse
# than the gap. test_the_frozen_ids_that_no_capability_declares_are_still_a_nonempty_set
# keeps that reason honest instead of leaving it as a claim in a comment.


def declared_output_schemas() -> dict[str, str]:
    """Every capability that declares an output schema, id to schema id."""
    return {
        capability.id: capability.output_schema
        for capability in CAPABILITIES
        if capability.output_schema is not None
    }


def test_every_output_schema_a_capability_declares_is_an_id_the_frozen_registry_pins():
    """The registry may only promise a document the tree actually emits.

    Held against tests/test_schema_registry.py's FROZEN_SCHEMA_IDS, which is a scan of
    string literals under `rig_workbench/` and `scripts/` — written before this table
    existed and not derived from it.
    """
    unknown = sorted(
        (capability_id, schema_id)
        for capability_id, schema_id in declared_output_schemas().items()
        if schema_id not in FROZEN_SCHEMA_IDS
    )
    detail = "\n".join(f"  {capability_id} declares {schema_id}"
                       for capability_id, schema_id in unknown)
    assert not unknown, (
        "the capability registry promises schema ids that tests/test_schema_registry.py "
        "does not pin:\n" + detail + "\n"
        "Two surfaces disagree and the registry is the one to look at first: "
        "FROZEN_SCHEMA_IDS is a scan of the string literals under rig_workbench/ and "
        "scripts/, so an id here that is absent there is one no code in the shipped trees "
        "contains. Either the entry names the wrong document (fix "
        "rig_workbench/registry/entries_*.py), or a real new id was added to the source "
        "without being frozen — in which case tests/test_schema_registry.py is already "
        "failing too, and that is the file to fix first."
    )


def test_the_frozen_ids_that_no_capability_declares_are_still_a_nonempty_set():
    """A guard on the stated reason for the one-direction rule, not a behaviour assertion.

    The comment above refuses the reverse direction on the grounds that frozen ids exist
    which no CLI capability emits. That is checkable, so check it: if it ever stops being
    true, the exclusion has stopped earning its place and the reverse becomes assertable.
    """
    undeclared = FROZEN_SCHEMA_IDS - set(declared_output_schemas().values())
    assert undeclared, (
        "every frozen schema id is now declared by some capability, so the reason this "
        "file gives for asserting only one direction (that ids exist which no CLI "
        "capability emits — inputs, configuration, the web reader's documents) no longer "
        "holds. Either assert both directions now, or give the one-direction rule a "
        "reason that is true."
    )


# ══ task 11: exit codes ═══════════════════════════════════════════════════════
# HOW THIS AVOIDS BEING VACUOUS.
#
# tests/test_exit_code_surface.py records its measurements as `assert x.returncode == N`
# inside test bodies — a subprocess per assertion, nothing monkeypatched. Copying those
# numbers into a table here would produce a check that agrees with a copy instead of with
# the measurement, which is the exact drift §2 describes. So the pairs are read out of that
# file's syntax tree: every `<name> = rig_cli(...)` binding, the argv it was given, and the
# code the test then asserted came back.
#
# An extractor is only worth as much as what it reaches, and one that quietly stops
# matching would turn this into a green test that compares nothing. Two things prevent
# that. MEASURED_COMMANDS pins the exact set of commands the extraction reaches today, so a
# renamed helper or a deleted test fails here loudly and says the extraction broke rather
# than that the registry is wrong. MEASURED_CODES pins the distinct statuses it reaches, so
# the comparison cannot decay into "every capability declares 0" — 1, 2, 3 and 5 are all in
# it, and 3 and 5 exist on exactly one command each.
#
# The comparison runs one way: what a process returned must be declared. The registry may
# declare a code the file never drove — `bench` exit 0 needs a paid provider and is in that
# file's own NOT_PINNED — so the reverse is not a contradiction, and
# test_every_code_recorded_as_unreachable_is_still_declared_by_the_capability_it_names ties
# that half down separately.

#: The names that stand for a subprocess run in tests/test_exit_code_surface.py. `rig_cli`
#: and `rig_cli_json` are conftest fixtures; `orchestrate` is the one-line closure the
#: human-gate test wraps `rig_cli` in, and without it the orchestrator's parked 3 — the
#: only measurement of that code anywhere — would drop out of the comparison.
CLI_CALLERS = ("rig_cli", "rig_cli_json", "orchestrate")

#: Every command the extraction below reaches, as the words handed to the CLI. Pinned so
#: that an extraction which stops matching fails here instead of silently comparing
#: nothing. Add to it when that file measures a new command; a removal means either a test
#: was deleted (say so in review) or the extractor no longer understands the shape.
MEASURED_COMMANDS = frozenset({
    "bench", "design-constraints", "gh-check", "ja-lint", "next", "check", "init",
    "verdict", "govern can", "govern init", "validate", "wb accept", "wb contract",
    "wb discard", "wb gate", "wb gates", "wb new", "wb scan-secrets", "wb status",
})

#: The distinct statuses those measurements produced. The point of pinning them is that a
#: comparison covering only 0 would pass against almost any table: 1 (a verdict), 2 (an
#: error), 3 (`gh` missing, a parked run, a denied permission, a pending contract) and 5
#: (`gh` without the stack extension) are what make it bite.
MEASURED_CODES = frozenset({0, 1, 2, 3, 5})


def _named_exit_codes(module: ast.Module) -> dict[str, int]:
    """`OK` / `REJECTED` / `ERROR` as that file assigns them, read rather than assumed.

    Read out of its source instead of written here, so that the comparison follows the
    numbers that file's own assertions use. It writes them out as integers on purpose (see
    its header); this reads them back the same way.
    """
    return {
        node.targets[0].id: node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
        and not isinstance(node.value.value, bool)
    }


def _argv_sequence(node) -> list[tuple[str, ...]]:
    """A literal sequence of literal argvs, or `[]` if it is anything else."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return []
    argvs = []
    for element in node.elts:
        if not isinstance(element, (ast.Tuple, ast.List)):
            return []
        words = _leading_string_constants(element)
        if not words:
            return []
        argvs.append(tuple(words))
    return argvs


def _module_argv_constants(module: ast.Module) -> dict[str, list[tuple[str, ...]]]:
    """Module-level names assigned a literal sequence of literal argvs.

    A `for` loop or a `parametrize` may name one of these rather than spell the argvs
    inline, and both shapes are in that file now. Without this the scan reads the name,
    finds no literal, and drops every command in it — silently, because the loop still
    parses.
    """
    return {
        node.targets[0].id: argvs
        for node in module.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and (argvs := _argv_sequence(node.value))
    }


def _loop_argvs(function, constants: dict[str, list[tuple[str, ...]]] | None = None,
                ) -> dict[str, list[tuple[str, ...]]]:
    """Names bound to a literal sequence of literal argvs, by a loop or by `parametrize`.

    Three shapes reach the same place. `for argv in ((...), (...)):` — the pair of
    `state.die()` conditions and the seven orchestrator steps that lead to the parked run.
    `for argv in NAME:` and `@pytest.mark.parametrize("argv", NAME)`, where `NAME` is a
    module-level tuple of argvs: the missing-run-state verbs are written that way, one
    test per verb so a failure on the first cannot hide the other four. Skipping any of
    the three would drop `wb status`, `init`, `check`, `verdict` and `approve` out of the
    comparison entirely, and the guard test above would be the only thing to say so.
    """
    constants = constants or {}
    found: dict[str, list[tuple[str, ...]]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            argvs = (constants.get(node.iter.id, []) if isinstance(node.iter, ast.Name)
                     else _argv_sequence(node.iter))
            if argvs:
                found[node.target.id] = argvs
    for decorator in getattr(function, "decorator_list", []):
        if not (isinstance(decorator, ast.Call) and len(decorator.args) == 2):
            continue
        target = decorator.func
        if not (isinstance(target, ast.Attribute) and target.attr == "parametrize"):
            continue
        names, values = decorator.args
        if not (isinstance(names, ast.Constant) and isinstance(names.value, str)):
            continue
        if "," in names.value:  # several parameters: the values are not argvs
            continue
        argvs = (constants.get(values.id, []) if isinstance(values, ast.Name)
                 else _argv_sequence(values))
        if argvs:
            found[names.value.strip()] = argvs
    return found


def observed_exit_codes() -> dict[str, set[int]]:
    """Every (command, status) pair tests/test_exit_code_surface.py measured, from its AST.

    Returns the command as the space-joined words handed to the CLI, mapped to the set of
    statuses that file asserted came back from running it.
    """
    module = ast.parse(EXIT_CODE_SURFACE.read_text(encoding="utf-8"),
                       filename=str(EXIT_CODE_SURFACE))
    named = _named_exit_codes(module)
    constants = _module_argv_constants(module)
    observed: dict[str, set[int]] = {}
    for function in ast.walk(module):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        loops = _loop_argvs(function, constants)
        bound: dict[str, list[tuple[str, ...]]] = {}
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id in CLI_CALLERS):
                call = node.value
                if (call.args and isinstance(call.args[0], ast.Starred)
                        and isinstance(call.args[0].value, ast.Name)):
                    argvs = loops.get(call.args[0].value.id, [])
                else:
                    words = _leading_string_constants(ast.Tuple(elts=list(call.args)))
                    argvs = [tuple(words)] if words else []
                if argvs:
                    bound[node.targets[0].id] = argvs
            if not isinstance(node, ast.Assert):
                continue
            test = node.test
            if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                    and isinstance(test.ops[0], ast.Eq)):
                continue
            left, right = test.left, test.comparators[0]
            if not (isinstance(left, ast.Attribute) and left.attr == "returncode"
                    and isinstance(left.value, ast.Name) and left.value.id in bound):
                continue
            if isinstance(right, ast.Constant) and isinstance(right.value, int):
                code = right.value
            elif isinstance(right, ast.Name) and right.id in named:
                code = named[right.id]
            else:
                continue
            for argv in bound[left.value.id]:
                command = capability_for(argv)
                if command is None:
                    observed.setdefault(" ".join(argv), set()).add(code)
                else:
                    path = " ".join(by_id(command).command_path)
                    observed.setdefault(path, set()).add(code)
    return observed


def test_the_exit_code_observations_still_reach_every_command_and_status_pinned_here():
    """A guard on the extraction, run before the comparison it feeds.

    Without it, an extractor that stopped matching would make the next test pass by
    comparing an empty set. This fails instead, and says which of the two files moved.
    """
    observed = observed_exit_codes()
    missing = sorted(MEASURED_COMMANDS - set(observed))
    codes = {code for codes in observed.values() for code in codes}
    assert not missing, (
        "these commands were measured by tests/test_exit_code_surface.py when this file "
        "was written, and the scan no longer finds them:\n"
        + "\n".join(f"  {command}" for command in missing) + "\n"
        "This is a statement about tests/test_exit_code_surface.py, not about the "
        "registry — go and look there. Either a test was removed (update MEASURED_COMMANDS "
        "and say so in review), or the shape it is written in changed and the scan in this "
        "file no longer understands it, in which case every assertion below it has quietly "
        "stopped comparing anything."
    )
    assert MEASURED_CODES <= codes, (
        f"the exit-code scan now reaches only {sorted(codes)}, and the statuses pinned as "
        f"the ones that make this comparison bite are {sorted(MEASURED_CODES)}. A "
        "comparison that only ever sees 0 would pass against almost any table. Look at "
        "tests/test_exit_code_surface.py first."
    )


def test_every_exit_code_a_real_process_returned_is_declared_by_the_capability_that_ran():
    """The measurement and the declaration, one way round.

    Every status tests/test_exit_code_surface.py observed through a subprocess has to
    appear in the `exit_codes` of the capability whose command produced it. The reverse is
    not asserted: a declared code that file could not drive is not a contradiction, and its
    own NOT_PINNED is where such a code is recorded.
    """
    observed = observed_exit_codes()
    undeclared, unmapped = [], []
    for command, codes in sorted(observed.items()):
        capability_id = capability_for(command.split(" "))
        if capability_id is None:
            unmapped.append((command, sorted(codes)))
            continue
        declared = {code.code for code in by_id(capability_id).exit_codes}
        for code in sorted(codes - declared):
            undeclared.append((command, capability_id, code, sorted(declared)))
    detail = "\n".join(
        f"  `rig-wb {command}` returned {code}; {capability_id} declares {declared}"
        for command, capability_id, code, declared in undeclared)
    assert not undeclared, (
        "the capability registry contradicts what tests/test_exit_code_surface.py measured "
        "through real processes:\n" + detail + "\n"
        "The registry is the file to open. Those codes were observed by running the "
        "command, not read off a constant, so the declaration is the half that is wrong — "
        "add the status to the entry in rig_workbench/registry/entries_*.py, with the "
        "meaning a caller may conclude from it. Do not edit "
        "tests/test_exit_code_surface.py to agree: it is the independent counterweight, "
        "and an exit code is what a CI step branches on."
    )
    assert not unmapped, (
        "tests/test_exit_code_surface.py drives commands the capability table does not "
        f"declare: {unmapped}. The registry claims to hold every capability rig has, so a "
        "command a test can run and the table does not know about is a gap in "
        "rig_workbench/registry/entries_*.py."
    )


#: Orchestrator verbs the capability table does not declare, so the second direction below
#: has no registry entry to check an attribution against, with where the code IS measured.
#: `resume` and `status` are reachable only through `scripts/orchestrate.py` (they are not
#: in `rig_workbench/cli.py`'s `_orch_delegates`), which is why no capability describes
#: them. Pinned as a tuple rather than skipped silently: a verb that quietly stops being
#: declared has to be added here by hand, in a diff a reviewer reads.
_VERBS_NO_CAPABILITY_DECLARES = {
    "resume": "driven through the shim in tests/test_cli_smoke.py",
    "status": "driven through the shim in tests/test_cli_smoke.py",
    "ab": "no capability record; not measured for a status anywhere",
    "mcp-scan": "declared as a `wb` subcommand, not as an orchestrator verb",
}


def _orchestrator_help() -> tuple[frozenset[str], frozenset[int], str, dict[int, set[str]]]:
    """The orchestrator's verbs, the codes its `--help` names, the line, and who it blames.

    The fourth value is the attribution the line makes: the verbs named inside each code's
    own segment of the sentence. `3=parked at a human gate … (`run`, `next`, `resume`)`
    attributes 3 to those three, and that claim is checkable in a way the bare set of codes
    is not — a line can name every status a verb returns and still hand one of them to a
    verb that cannot return it.

    Read out of `rig_workbench/orchestrate/cli.py` with `ast` rather than by importing it:
    the module pulls in every provider and the whole command table on import, and none of
    that is needed to read a docstring. `COMMANDS` is the dispatch table `main()` looks a
    verb up in, so its keys are exactly the words that reach a verb.
    """
    module = ast.parse(ORCHESTRATE_CLI.read_text(encoding="utf-8"), filename=str(ORCHESTRATE_CLI))
    verbs = frozenset(
        key.value
        for node in module.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "COMMANDS"
        and isinstance(node.value, ast.Dict)
        for key in node.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    )
    doc = ast.get_docstring(module) or ""
    lines = doc.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("Exit code")), None)
    assert start is not None, (
        f"{ORCHESTRATE_CLI} no longer has a line beginning `Exit code` in its docstring, "
        "which is the only place the orchestrator states what a status means. The "
        "comparison below has nothing to read.")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.strip():
            break
        block.append(line)
    sentence = "\n".join(block)
    # Each `<n>=` starts a segment and ends the previous one, so a backticked verb inside a
    # segment is that code's claim and nothing else's.
    marks = list(re.finditer(r"\b(\d+)=", sentence))
    blamed: dict[int, set[str]] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(sentence)
        segment = sentence[mark.end():end]
        blamed[int(mark.group(1))] = {word for word in re.findall(r"`([a-z][a-z-]*)`", segment)
                                      if word in verbs}
    return verbs, frozenset(blamed), sentence, blamed


def test_the_orchestrators_help_names_every_status_its_verbs_were_observed_to_return():
    """The exit-code line in `orchestrate/cli.py`'s docstring, against real processes.

    That line is what `scripts/orchestrate.py --help` prints and the only statement the
    orchestrator makes about its own statuses. It read `0=success / 1=error or ESCALATE /
    3=run parked at a human gate` while `check`, `next`, `verdict`, `approve` and `init`
    were all observed returning 2 — a refusal a caller branching on that line would read as
    a crash, or not branch on at all. It also said the 3 was `run` only, and the parked 3
    this suite measures comes out of `next`.

    Both directions, against two independent sources. Outward: every status
    tests/test_exit_code_surface.py saw an orchestrator verb return has to be a status the
    line names. Inward: every verb the line BLAMES for a status has to be able to return
    it, judged by the measurement where there is one and by the capability table where
    there is not — the line said `check` returns 3 for a whole release while `cmd_check`
    has no exit-3 path at all and the registry declared it 0/1/2, and the outward
    direction cannot see an over-claim like that, because an over-claim adds no code.

    What is still not asserted is a code the line names and nothing attributes to a verb
    (1, ESCALATE): the registry may declare a code no test drove, and so may this line."""
    verbs, named, sentence, blamed = _orchestrator_help()
    observed = observed_exit_codes()
    measured = {command: codes for command, codes in observed.items() if command in verbs}

    # The same guard the extraction gets above, for the same reason: an empty comparison
    # passes against any line at all.
    assert measured, (
        "no orchestrator verb is measured by tests/test_exit_code_surface.py any more, so "
        f"this compares nothing. The dispatch table holds {sorted(verbs)}; the scan reached "
        f"{sorted(observed)}.")

    missing = sorted((command, code) for command, codes in measured.items()
                     for code in sorted(codes - named))
    assert not missing, (
        "the orchestrator's `--help` does not name statuses its own verbs returned to a "
        "real process:\n"
        + "\n".join(f"  `{command}` returned {code}" for command, code in missing)
        + f"\n\nthe line says, in full:\n{sentence}\n\n"
        f"and it names {sorted(named)}. The line is the half to fix: those codes were "
        "observed by running the verb. Edit the docstring of "
        "rig_workbench/orchestrate/cli.py, which is the text `--help` prints.")

    # ── the other direction: what the line hands to a verb, that verb can return ──
    overclaimed, unverifiable = [], []
    for code, blamed_verbs in sorted(blamed.items()):
        for verb in sorted(blamed_verbs):
            capability_id = capability_for([verb])
            if capability_id is None:
                if verb not in _VERBS_NO_CAPABILITY_DECLARES:
                    unverifiable.append((verb, code))
                continue
            backing = observed.get(verb, set()) | {
                entry.code for entry in by_id(capability_id).exit_codes}
            if code not in backing:
                overclaimed.append((verb, code, sorted(backing)))
    assert not overclaimed, (
        "the orchestrator's `--help` hands a status to a verb that cannot return it:\n"
        + "\n".join(f"  `{verb}` is named under {code}; measured and declared: {backing}"
                     for verb, code, backing in overclaimed)
        + f"\n\nthe line says, in full:\n{sentence}\n\n"
        "Drive the verb and see for yourself before editing either side: if it really "
        "returns the code, the registry entry in rig_workbench/registry/entries_*.py is "
        "what is missing; if it does not, the line is claiming something untrue and the "
        "verb's name comes out of it.")
    assert not unverifiable, (
        "the line names verbs no capability declares, and they are not in "
        f"_VERBS_NO_CAPABILITY_DECLARES: {unverifiable}. Either the registry lost an "
        "entry, or a verb was written into the line without anywhere that measures it.")


def test_every_code_recorded_as_unreachable_is_still_declared_by_the_capability_it_names():
    """The other half: what that file admits it could not drive, the registry still claims.

    `NOT_PINNED` records a documented status no test process can produce, with the
    measurement that establishes why (`bench` exit 0 needs a paid provider and the
    network). The registry declares those codes on the strength of the same reading, so the
    two records have to agree — otherwise the only two places that know about an
    unreachable code disagree about whether it exists.
    """
    mismatched = []
    for command, code, _reason in NOT_PINNED:
        words = command.split(" ")
        if words and words[0] == "rig-wb":
            words = words[1:]
        capability_id = capability_for(words)
        if capability_id is None:
            mismatched.append((command, code, "no capability declares this command"))
            continue
        declared = {entry.code for entry in by_id(capability_id).exit_codes}
        if code not in declared:
            mismatched.append((command, code, f"{capability_id} declares {sorted(declared)}"))
    assert not mismatched, (
        "tests/test_exit_code_surface.py records these statuses as real but undrivable, "
        "and the capability registry does not declare them:\n"
        + "\n".join(f"  {command} exit {code}: {why}" for command, code, why in mismatched)
        + "\nThe two files are the only records of a code nothing can produce, so they have "
        "to say the same thing. Read that file's NOT_PINNED entry — it names the code path "
        "that emits the status — and then fix whichever of the two is wrong."
    )


# ══ task 13: the two MCP servers ══════════════════════════════════════════════
# Read-only, both of them. §2 says the two servers should become projections of this
# table; they have not been rewired yet and rewiring them is stage 3, so what is checked
# here is that the hand-maintained lists and the table still agree about what exists and
# what each tool does to the machine.
#
# Neither server is imported. `scripts/mcp_server.py` starts a JSON-RPC loop on stdin when
# run, and `remote_mcp.create_server` needs the optional `mcp` SDK, which is not installed
# everywhere the suite runs — importing either would make this file's coverage depend on
# the environment. Both are enumerated with `ast`, and each tool's command is taken from
# the argv its own dispatch function builds rather than from its name, so a tool renamed
# without being rewired is still resolved to what it really runs.

#: `scripts/mcp_server.py` shells out to the two compatibility shims. Both are four-line
#: files over the same package the installed CLI dispatches (`scripts/workbench.py` ->
#: `rig_workbench.workbench.cli:main`, reached as `rig-wb wb <verb>`; `scripts/orchestrate.py`
#: -> `rig_workbench.orchestrate.cli:main`, reached as `rig-wb <verb>` for the verbs in
#: `cli._orch_delegates`), which is what makes a tool's argv comparable with a command path.
STDIO_SCRIPT_PREFIXES = {"WORKBENCH": ("wb",), "ORCHESTRATE": ()}

#: Tools in `scripts/mcp_server.py` that no capability declares, with the reason. Recorded
#: rather than asserted away: each is a real gap between two surfaces, and the guard below
#: fails once one is closed so the constant cannot outlive its reason.
STDIO_TOOLS_WITHOUT_A_DECLARED_CAPABILITY = (
    (
        "rig_orchestrate_status",
        "It runs `scripts/orchestrate.py status`. `status` is in orchestrate's own "
        "COMMANDS dict but not in `rig_workbench/cli.py`'s `_orch_delegates`, so "
        "`rig-wb status` answers `Unknown sub-command` and exits 2 — the verb is reachable "
        "only through the historical `scripts/orchestrate.py` entrypoint, which is why the "
        "registry (derived from what `rig-wb` dispatches) declares no capability for it. "
        "The MCP tool therefore offers something the installed CLI does not. Closing it is "
        "a stage-3 decision: add `status` to `_orch_delegates` and declare it, or drop the "
        "tool.",
    ),
)


def stdio_mcp_tools() -> dict[str, tuple[str, ...]]:
    """Every tool `scripts/mcp_server.py` publishes, to the argv its handler builds.

    The `TOOLS` dict maps a tool name to a `fn`; that function builds a list whose leading
    literals are the command, and hands it to `_run` with the script it belongs to. Taking
    the argv rather than trusting the tool's name is the point: `rig_task_new` is only a
    projection of `wb new` if that is what it actually runs.
    """
    module = ast.parse(STDIO_MCP_SERVER.read_text(encoding="utf-8"),
                       filename=str(STDIO_MCP_SERVER))
    handlers = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    tools: dict[str, tuple[str, ...]] = {}
    for node in module.body:
        if not (isinstance(node, ast.Assign)
                and any(getattr(target, "id", None) == "TOOLS" for target in node.targets)
                and isinstance(node.value, ast.Dict)):
            continue
        for key, spec in zip(node.value.keys, node.value.values):
            handler_name = next(
                (value.id for field, value in zip(spec.keys, spec.values)
                 if isinstance(field, ast.Constant) and field.value == "fn"
                 and isinstance(value, ast.Name)),
                None,
            )
            handler = handlers.get(handler_name)
            if handler is None:
                continue
            script, words = None, None
            for inner in ast.walk(handler):
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id == "_run" and len(inner.args) == 2
                        and isinstance(inner.args[0], ast.Name)):
                    script = inner.args[0].id
                    if isinstance(inner.args[1], ast.List):
                        words = _leading_string_constants(inner.args[1])
                if (words is None and isinstance(inner, ast.Assign)
                        and any(getattr(t, "id", None) == "args" for t in inner.targets)
                        and isinstance(inner.value, ast.List)):
                    words = _leading_string_constants(inner.value)
            if script is not None and words:
                tools[key.value] = (*STDIO_SCRIPT_PREFIXES[script], *words)
    return tools


def remote_mcp_gateway_argvs() -> dict[str, tuple[str, ...]]:
    """Each `RigGateway` method to the CLI argv it invokes.

    `remote_mcp.py` drives `python -I -m rig_workbench.cli` rather than duplicating rig's
    routing, so the argv each method builds is literally the command path a person would
    type, and comparable with the table without any translation.
    """
    module = ast.parse(REMOTE_MCP_SERVER.read_text(encoding="utf-8"),
                       filename=str(REMOTE_MCP_SERVER))
    gateway = next(node for node in ast.walk(module)
                   if isinstance(node, ast.ClassDef) and node.name == "RigGateway")
    argvs: dict[str, tuple[str, ...]] = {}
    for method in gateway.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(method):
            words = None
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", None) == "args" for t in node.targets)
                    and isinstance(node.value, ast.List)):
                words = _leading_string_constants(node.value)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "_invoke" and node.args
                  and isinstance(node.args[0], ast.List)):
                words = _leading_string_constants(node.args[0])
            if words:
                argvs[method.name] = tuple(words)
                break
    return argvs


def remote_mcp_tools() -> dict[str, tuple[tuple[str, ...], dict[str, bool]]]:
    """Every tool `remote_mcp.create_server` registers, to (argv, hand-built annotations).

    The annotation sets are the three `ToolAnnotations(...)` constructed in that function,
    read as the literal booleans they are given.
    """
    module = ast.parse(REMOTE_MCP_SERVER.read_text(encoding="utf-8"),
                       filename=str(REMOTE_MCP_SERVER))
    create_server = next(node for node in ast.walk(module)
                         if isinstance(node, ast.FunctionDef) and node.name == "create_server")
    annotations: dict[str, dict[str, bool]] = {}
    for node in ast.walk(create_server):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "ToolAnnotations"
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            annotations[node.targets[0].id] = {
                keyword.arg: keyword.value.value
                for keyword in node.value.keywords
                if isinstance(keyword.value, ast.Constant)
            }
    gateway = remote_mcp_gateway_argvs()
    tools: dict[str, tuple[tuple[str, ...], dict[str, bool]]] = {}
    for node in ast.walk(create_server):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("rig_")):
            continue
        annotation_names = [
            keyword.value.id
            for decorator in node.decorator_list if isinstance(decorator, ast.Call)
            for keyword in decorator.keywords
            if keyword.arg == "annotations" and isinstance(keyword.value, ast.Name)
        ]
        methods = [attribute.attr for attribute in ast.walk(node)
                   if isinstance(attribute, ast.Attribute)
                   and isinstance(attribute.value, ast.Name)
                   and attribute.value.id == "gateway"]
        if not annotation_names or not methods or methods[0] not in gateway:
            continue
        tools[node.name] = (gateway[methods[0]], annotations[annotation_names[0]])
    return tools


#: What each server publishes today. Pinned for the same reason MEASURED_COMMANDS is: an
#: `ast` walk that stops matching would leave every assertion below iterating over nothing.
STDIO_TOOL_COUNT = 14
REMOTE_TOOL_COUNT = 7


def test_both_mcp_servers_still_publish_the_tool_lists_this_file_knows_how_to_read():
    """A guard on the two enumerations, before anything compares them with the table."""
    stdio, remote = stdio_mcp_tools(), remote_mcp_tools()
    assert len(stdio) == STDIO_TOOL_COUNT, (
        f"scripts/mcp_server.py's TOOLS now resolves to {len(stdio)} tools "
        f"({sorted(stdio)}), pinned here as {STDIO_TOOL_COUNT}. If a tool was added or "
        "removed, update this number and check the mapping below; if the count dropped "
        "without the file changing, this file's scan no longer understands how a handler "
        "builds its argv and the checks below have stopped comparing anything."
    )
    assert len(remote) == REMOTE_TOOL_COUNT, (
        f"rig_workbench/remote_mcp.py's create_server now registers {len(remote)} tools "
        f"({sorted(remote)}), pinned here as {REMOTE_TOOL_COUNT}. Same reading as above."
    )


def test_every_tool_the_stdio_mcp_server_hands_out_maps_to_a_declared_capability():
    """`scripts/mcp_server.py` keeps its own list of what rig can do; §2 says that list
    should be a projection of the table. Until it is, every tool in it must at least name
    something the table declares."""
    recorded = {name for name, _reason in STDIO_TOOLS_WITHOUT_A_DECLARED_CAPABILITY}
    unmapped = sorted(
        (name, " ".join(argv))
        for name, argv in stdio_mcp_tools().items()
        if capability_for(argv) is None and name not in recorded
    )
    assert not unmapped, (
        "scripts/mcp_server.py publishes tools that run commands the capability registry "
        "does not declare:\n"
        + "\n".join(f"  {name} -> `{command}`" for name, command in unmapped) + "\n"
        "Which file to open depends on which is right. If the command is real and "
        "reachable through `rig-wb`, the registry is missing an entry "
        "(rig_workbench/registry/entries_*.py). If it is not reachable through `rig-wb`, "
        "the MCP server is offering something the CLI does not, and belongs in "
        "STDIO_TOOLS_WITHOUT_A_DECLARED_CAPABILITY with the reason — not in the registry."
    )


def test_the_stdio_tools_recorded_as_undeclared_are_still_undeclared():
    """A guard on the constant above: a recorded gap that has been closed must not stay
    recorded, or the constant becomes a place where a real drift can hide."""
    tools = stdio_mcp_tools()
    for name, reason in STDIO_TOOLS_WITHOUT_A_DECLARED_CAPABILITY:
        assert len(reason) > 80, f"{name}: the reason has to be a reason"
        assert name in tools, (
            f"{name} is recorded here as a tool with no declared capability, and "
            "scripts/mcp_server.py no longer publishes it. Delete the entry."
        )
        assert capability_for(tools[name]) is None, (
            f"{name} runs `{' '.join(tools[name])}`, which the capability registry now "
            "declares. The gap this entry records is closed — remove it from "
            "STDIO_TOOLS_WITHOUT_A_DECLARED_CAPABILITY so the mapping is asserted instead."
        )


def test_every_tool_the_remote_mcp_server_hands_out_maps_to_a_declared_capability():
    """The same question of `rig_workbench/remote_mcp.py`, whose seven tools drive
    `rig_workbench.cli` directly."""
    unmapped = sorted(
        (name, " ".join(argv))
        for name, (argv, _annotations) in remote_mcp_tools().items()
        if capability_for(argv) is None
    )
    assert not unmapped, (
        "rig_workbench/remote_mcp.py publishes tools whose commands the capability "
        "registry does not declare:\n"
        + "\n".join(f"  {name} -> `rig-wb {command}`" for name, command in unmapped) + "\n"
        "This adapter invokes `python -m rig_workbench.cli` directly, so the argv above is "
        "a command the installed CLI really dispatches — which makes the registry the file "
        "to open (rig_workbench/registry/entries_*.py)."
    )


def test_no_hand_built_tool_annotation_contradicts_the_registrys_effect_and_network_axes():
    """The three `ToolAnnotations` sets in `remote_mcp.py` against `Capability.mcp_hints`.

    `mcp_hints` derives all four hints from the two axes: `readOnlyHint` and
    `idempotentHint` from `effect_class == read-only`, `destructiveHint` from its negation,
    and `openWorldHint` from `network != never` (so `sometimes` raises it — an MCP client
    is deciding whether to ask a person first, and "it might" has to be answered "it may").
    Those hand-built sets predate the table; this asserts the table reproduces them rather
    than that they reproduce the table.
    """
    contradictions = []
    for name, (argv, annotations) in sorted(remote_mcp_tools().items()):
        capability_id = capability_for(argv)
        if capability_id is None:
            continue
        capability = by_id(capability_id)
        for hint, value in sorted(annotations.items()):
            derived = capability.mcp_hints.get(hint)
            if derived != value:
                contradictions.append(
                    f"  {name} (`rig-wb {' '.join(argv)}` = {capability_id}): "
                    f"remote_mcp.py says {hint}={value}, the registry derives {hint}="
                    f"{derived} from effect_class={capability.effect_class!r} "
                    f"network={capability.network!r}")
    assert not contradictions, (
        "the annotations rig_workbench/remote_mcp.py hands to MCP clients contradict what "
        "the capability registry says the same command does:\n" + "\n".join(contradictions)
        + "\nThese hints are what a client uses to decide whether to ask a person before "
        "running something, so the two must not disagree in either direction. Read the "
        "command's code before choosing a side: `effect_class` is about what is left "
        "different on this machine, `network` is about whether anything leaves it, and the "
        "docstrings on both fields in rig_workbench/registry/model.py work through the "
        "cases that have been got wrong before."
    )


def test_a_read_only_capability_that_reaches_the_network_still_has_no_annotation_set():
    """The recorded gap, kept honest.

    `mcp_hints` can produce four distinct annotation sets; `remote_mcp.py` hand-builds
    three. The missing one is read-only *and* open-world — a capability that only fetches
    and reports, such as `gh-check` (`gh auth status` against github.com, nothing left
    behind). That combination has no counterpart in that file because none of its four read
    tools leaves the machine, so this is a gap in the source rather than a contradiction,
    and it is recorded rather than failed on. If a fourth set ever appears there, this
    fails and the record has to go.
    """
    gh_check = by_id("gh-check")
    assert gh_check.effect_class == "read-only" and gh_check.may_reach_network, (
        "gh-check is no longer the read-only capability that reaches the network, so the "
        "example this record is built on has moved. Find the capability that is now both "
        "and rewrite the record, or delete it if none is."
    )
    hand_built = [annotations for _argv, annotations in remote_mcp_tools().values()]
    matching = [annotations for annotations in hand_built
                if annotations.get("readOnlyHint") and annotations.get("openWorldHint")]
    assert not matching, (
        f"rig_workbench/remote_mcp.py now hand-builds a read-only, open-world annotation "
        f"set ({matching[0]}), which this file records as the one combination it has no "
        "counterpart for. Nothing is broken — delete this test and let "
        "test_no_hand_built_tool_annotation_contradicts_the_registrys_effect_and_network_"
        "axes cover the fourth set like the other three."
    )


# ══ task 14: the Action and the slash commands ════════════════════════════════

#: Inputs `action.yml` declares that never reach the CLI, with what each is for instead.
#: They are not a subset of any capability's flags because they are not rig's vocabulary at
#: all — they belong to GitHub Actions.
ACTION_ONLY_INPUTS = (
    ("auto_pr", "gates the composite's own `Open PR on green gate` step through an `if:` "
                "expression; it is never passed to a rig command"),
    ("anthropic_api_key", "exported into the run step's environment for the provider to "
                          "read; a secret is not a flag"),
    ("github_token", "used by `gh` in the PR step and by the gh-stack install, and "
                     "deliberately not exposed to the step that runs the model"),
)

#: Inputs that do reach the CLI, to the flag `scripts/rig-action-entrypoint.sh` passes them
#: as. Written out because one pair cannot be derived: the Action names an input for what a
#: person puts in it (`task`) and the entrypoint passes it as orchestrate's own name for
#: the same thing (`--goal`). Every pair is checked against both files by
#: test_every_wired_action_input_travels_through_the_env_var_the_entrypoint_reads.
ACTION_INPUT_FLAGS = {
    "task": "--goal",
    "recipe": "recipe",
    "provider": "--provider",
    "verifier_provider": "--verifier-provider",
    "model": "--model",
    "max_steps": "--max-steps",
}


def action_inputs() -> dict[str, dict]:
    """The `inputs:` block of `action.yml`, parsed as the YAML it is."""
    import yaml

    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))["inputs"]


def action_input_env_vars() -> dict[str, str]:
    """Each environment variable `action.yml` binds to an input, to the input's name.

    The composite hands its inputs to the entrypoint script as environment variables — this
    is the join between the two halves of the Action surface.
    """
    text = ACTION.read_text(encoding="utf-8")
    return {
        input_name: env_var
        for env_var, input_name in re.findall(
            r"^\s*([A-Z][A-Z0-9_]*):\s*\$\{\{\s*inputs\.([a-z0-9_]+)\s*\}\}\s*$",
            text, re.MULTILINE)
    }


def action_capability() -> str:
    """The capability `action.yml` is a projection of, read out of the entrypoint.

    Taken from the first word of the argv the entrypoint builds rather than from
    `action.yml`'s prose, so that the correspondence is measured on the invocation.
    """
    match = re.search(r"args=\(\s*([a-z][a-z0-9-]*)", ACTION_ENTRYPOINT.read_text(encoding="utf-8"))
    assert match, (
        "scripts/rig-action-entrypoint.sh no longer builds its invocation as "
        "`args=(<verb> ...)`, so this file cannot tell which capability the GitHub Action "
        "projects. Read the script and update this function."
    )
    capability_id = capability_for([match.group(1)])
    assert capability_id is not None, (
        f"the GitHub Action's entrypoint runs `{match.group(1)}`, which the capability "
        "registry does not declare. Either the verb moved or the table is missing it."
    )
    return capability_id


def test_every_input_the_github_action_declares_is_wired_to_a_flag_or_recorded_as_its_own():
    """A partition, so neither list can be quietly left behind by a new input.

    Without this, an input added to `action.yml` and to neither constant would be checked
    by nothing at all — the silent drift §2 is about.
    """
    declared = set(action_inputs())
    accounted = set(ACTION_INPUT_FLAGS) | {name for name, _reason in ACTION_ONLY_INPUTS}
    unaccounted = sorted(declared - accounted)
    stale = sorted(accounted - declared)
    assert not unaccounted and not stale, (
        f"action.yml's inputs and this file's two lists have diverged. Not accounted for: "
        f"{unaccounted}. Recorded but no longer declared: {stale}.\n"
        "action.yml is the surface; this file is the record of how each of its inputs "
        "reaches (or does not reach) a rig command. Add each new input to "
        "ACTION_INPUT_FLAGS with the flag the entrypoint passes it as, or to "
        "ACTION_ONLY_INPUTS with what it does instead."
    )


def test_every_wired_action_input_travels_through_the_env_var_the_entrypoint_reads():
    """A guard on the hand-written mapping above, against both halves of the Action.

    `ACTION_INPUT_FLAGS` is the one hand-written table in this file, so it is the one thing
    here that could say something untrue about the surface. Each pair has to be visible in
    both files: `action.yml` binds the input to an environment variable, and
    `scripts/rig-action-entrypoint.sh` mentions that variable and passes that flag.
    """
    env_vars = action_input_env_vars()
    entrypoint = ACTION_ENTRYPOINT.read_text(encoding="utf-8")
    broken = []
    for input_name, flag in sorted(ACTION_INPUT_FLAGS.items()):
        env_var = env_vars.get(input_name)
        if env_var is None:
            broken.append(f"  {input_name}: action.yml binds it to no environment variable")
            continue
        if env_var not in entrypoint:
            broken.append(f"  {input_name}: ${env_var} appears nowhere in the entrypoint")
        if flag.startswith("--") and flag not in entrypoint:
            broken.append(f"  {input_name}: the entrypoint never passes {flag}")
    assert not broken, (
        "this file's record of how a GitHub Action input reaches a rig flag no longer "
        "matches the two files it describes:\n" + "\n".join(broken) + "\n"
        "Read scripts/rig-action-entrypoint.sh and action.yml, then fix "
        "ACTION_INPUT_FLAGS here. Nothing about the capability registry is implicated by "
        "this failure."
    )


def test_the_github_actions_inputs_are_a_subset_of_the_flags_that_capability_declares():
    """The Action offers a slice of one capability; the slice has to be part of the whole.

    Direction: inputs -> flags. The reverse is not a claim anybody makes — the Action
    exposes six of the `run` capability's flags and says so; `--isolate` and `--out` it
    passes itself, and the rest a CI caller has no way to set.

    This check used to be run against five of the six: `--model` was reached through
    action.yml, parsed by `cmd_run`, and declared by nothing, so it was recorded as a known
    gap and subtracted here. The `run` entry now declares every flag `cmd_run` accepts, the
    record has gone with the gap it described, and all six are checked. Nothing subtracts
    from this comparison any more — a flag the Action passes and the registry drops fails
    here rather than being excused.
    """
    capability = by_id(action_capability())
    declared = {flag.name for flag in capability.flags}
    missing = sorted(
        (input_name, flag) for input_name, flag in ACTION_INPUT_FLAGS.items()
        if flag not in declared
    )
    assert not missing, (
        f"action.yml offers inputs that the `{capability.id}` capability declares no flag "
        "for:\n"
        + "\n".join(f"  input `{name}` -> `{flag}`" for name, flag in missing) + "\n"
        f"`{capability.id}` declares {sorted(declared)}. The Action passes these through "
        "scripts/rig-action-entrypoint.sh to a real command, so if the command accepts the "
        "flag the registry entry is behind the code — fix "
        "rig_workbench/registry/entries_cli.py. If the command does not accept it, the "
        "Action is passing something that is silently ignored, and action.yml is the file "
        "to open."
    )


# ── the slash commands ───────────────────────────────────────────────────────
# WHICH DIRECTION IS ASSERTED, AND WHY.
#
# The mapping is many-to-many and lopsided: `/rig:go` fans out across `hostcheck`,
# `ja-lint`, `wb route`, `wb gate` and `wb accept` in one file, while 51 `wb` capabilities
# are named by no command at all. So "every capability is offered by a slash command" is
# not a claim anybody makes — the 30 commands are a curated front door onto the whole of
# `registry.CAPABILITIES`, not a second spelling of it. How many that is, is asserted in
# tests/test_capability_registry.py and deliberately not restated here: a count copied into
# prose is a count that goes stale in prose, which is how this line drifted the last time a
# verb was removed.
#
# The direction that means something is commands -> registry: a command file that names no
# declared capability is a front door onto something the table does not know about, which
# is the drift §2 describes. That is what is asserted, with the files that genuinely name
# none recorded below rather than papered over.

#: What counts as a command file naming a capability. Each is a literal invocation in the
#: file's own text, resolved through `capability_for`:
#:   * `rig-wb <words>` — the installed CLI, the spelling the table is written in;
#:   * `scripts/workbench.py <verb>` and `scripts/orchestrate.py <verb>` — the two
#:     compatibility shims, which are the same code (see STDIO_SCRIPT_PREFIXES);
#:   * a shipped recipe name, either after `--recipe` or in backticks — a recipe is
#:     executed by `rig-wb run <recipe>`, so naming one names the `run` capability;
#:   * `commands/<name>.md` — a file that delegates wholesale to another command file
#:     inherits what that one names. `/rig:rig` is a deprecated shim for `/rig:go` (it
#:     goes in 4.0.0) and is the only file that does this. It is resolved through rather
#:     than exempted by name, because a front door that opens onto everything `/rig:go`
#:     opens onto is not a command that names no capability.
_RIG_WB = re.compile(r"rig-wb((?:[ \t]+[a-z0-9][a-z0-9.-]*)+)")
_WORKBENCH_SHIM = re.compile(r"scripts/workbench\.py[ \t]+([a-z0-9-]+)")
_ORCHESTRATE_SHIM = re.compile(r"scripts/orchestrate\.py[ \t]+([a-z0-9-]+)")
_RECIPE = re.compile(r"--recipe[ \t]+([a-z0-9][a-z0-9_-]*)|`([a-z0-9][a-z0-9_-]*)`")
_DELEGATION = re.compile(r"commands/([a-z0-9-]+)\.md")

#: The slash commands that name no capability at all, with the reason. Every one of them
#: hands its work to a `facets/instructions/<name>` facet that the model performs in the
#: session — there is no rig command underneath, so there is nothing in the table for them
#: to be a projection of. That is a finding about the surface (a third of the front door
#: does not reach the CLI at all), not a defect in any one file, and it is recorded here so
#: that the twenty that *do* reach it are actually checked.
COMMANDS_THAT_NAME_NO_CAPABILITY = (
    ("drill.md", "seeds bugs and runs the review fan-out through "
                 "`facets/instructions/drill`; the scoring is the model's, and no rig "
                 "command is named"),
    ("export.md", "writes a persona, recipe or pack out as a standalone skill through "
                  "`facets/instructions/export`; the writing is done in-session"),
    ("forge.md", "generates rig's own bricks through `facets/instructions/forge`; it names "
                 "`python3 scripts/validate.py`, which is the pack validator rather than a "
                 "declared capability"),
    ("import.md", "translates an external skill through `facets/instructions/import` and "
                  "delegates generation to /rig:forge, /rig:persona and /rig:knowledge"),
    ("init.md", "scaffolds `.claude/rig.md` and the knowledge directories through "
                "`facets/instructions/init`; note that the `init` capability is "
                "orchestrate's run-state initialiser, a different thing with the same word"),
    ("knowledge.md", "drafts wiki pages through `facets/instructions/knowledge-gen`"),
    ("orchestrate.md", "is the way into `--orchestrate`; it names `scripts/orchestrate.py` "
                       "as the runner many times but never with a verb after it, so no "
                       "single command is named"),
    ("persona.md", "drafts a reviewer persona through `facets/instructions/persona-gen`"),
    ("talk.md", "is the conversational front door: it classifies an utterance and "
                "delegates to another `/rig:*` command, naming none itself"),
)

#: The brief's §9 count of the vocabulary a newcomer meets. Pinned so that a command added
#: or removed brings someone back to this file to say which.
SLASH_COMMAND_COUNT = 30

#: How many distinct capabilities the twenty-one mapped commands reach between them. A
#: floor rather than an exact count: it fails if the scan decays or commands are unwired,
#: and a command wired to something new only pushes it up.
SLASH_COMMAND_CAPABILITY_FLOOR = 26


def shipped_recipes() -> set[str]:
    return {path.stem for path in RECIPES_DIR.glob("*.md")}


def capabilities_named_by(path: pathlib.Path, seen=()) -> set[str]:
    """Every capability id the text of one command file names."""
    text = path.read_text(encoding="utf-8")
    found = set()
    for words in _RIG_WB.findall(text):
        found.add(capability_for(words.split()))
    for verb in _WORKBENCH_SHIM.findall(text):
        found.add(capability_for(["wb", verb]))
    for verb in _ORCHESTRATE_SHIM.findall(text):
        found.add(capability_for([verb]))
    recipes = shipped_recipes()
    if any((after_flag or backticked) in recipes for after_flag, backticked in _RECIPE.findall(text)):
        found.add(capability_for(["run"]))
    for delegate in _DELEGATION.findall(text):
        target = COMMANDS_DIR / f"{delegate}.md"
        if target != path and target.exists() and delegate not in seen:
            found |= capabilities_named_by(target, seen=(*seen, path.stem))
    return {capability_id for capability_id in found if capability_id is not None}


def test_the_slash_command_surface_is_still_the_thirty_files_the_brief_counted():
    """A guard on the domain of the next test, and on §9's own measurement of the wall."""
    files = sorted(COMMANDS_DIR.glob("*.md"))
    assert len(files) == SLASH_COMMAND_COUNT, (
        f"commands/ now holds {len(files)} files, and "
        "docs/v3-architecture-design-brief.ja.md §9 counts the slash-command vocabulary as "
        f"{SLASH_COMMAND_COUNT}. Update this number, and say in review whether the wall "
        "§9 measures got taller or shorter."
    )
    without_frontmatter = [path.name for path in files
                           if not path.read_text(encoding="utf-8").startswith("---\n")]
    assert not without_frontmatter, (
        f"these command files have no frontmatter block: {without_frontmatter}. Every "
        "/rig:* command is loaded by its frontmatter, so a file without one is not a "
        "command at all."
    )


def test_every_slash_command_names_at_least_one_declared_capability():
    """A front door has to open onto something the table knows about."""
    recorded = {name for name, _reason in COMMANDS_THAT_NAME_NO_CAPABILITY}
    silent = sorted(
        path.name for path in COMMANDS_DIR.glob("*.md")
        if path.name not in recorded and not capabilities_named_by(path)
    )
    assert not silent, (
        "these /rig:* commands name no capability the registry declares:\n"
        + "\n".join(f"  {name}" for name in silent) + "\n"
        "Two surfaces disagree, and which to open depends on what the command does. If it "
        "drives a rig command, check the spelling in the markdown against the table "
        "(`rig-wb <verb>`, `scripts/workbench.py <verb>`, a recipe name) — the file is "
        "probably naming something the CLI no longer offers. If it hands its work to a "
        "`facets/instructions/*` facet with no rig command underneath, it belongs in "
        "COMMANDS_THAT_NAME_NO_CAPABILITY with that reason: that is a real gap in the "
        "surface and not something to fix by adding an entry to the registry."
    )


def test_the_commands_recorded_as_naming_no_capability_still_name_none():
    """A guard on the constant above, so a command that gets wired up leaves the list."""
    for name, reason in COMMANDS_THAT_NAME_NO_CAPABILITY:
        path = COMMANDS_DIR / name
        assert path.exists(), (
            f"commands/{name} is recorded here as naming no capability and no longer "
            "exists. Delete the entry."
        )
        assert len(reason) > 40, f"{name}: the reason has to be a reason"
        named = sorted(capabilities_named_by(path))
        assert not named, (
            f"commands/{name} now names {named}, and this file records it as naming "
            "nothing. Remove it from COMMANDS_THAT_NAME_NO_CAPABILITY so it is asserted "
            "with the others."
        )


def test_the_slash_command_scan_still_reaches_the_capability_table():
    """A guard on the scan itself: twenty-one files map today, between them naming this
    many distinct capabilities. A scan that stopped resolving invocations would leave the
    test above passing on an all-but-empty result."""
    reached = set()
    for path in COMMANDS_DIR.glob("*.md"):
        reached |= capabilities_named_by(path)
    assert len(reached) >= SLASH_COMMAND_CAPABILITY_FLOOR, (
        f"the /rig:* commands now resolve to only {len(reached)} distinct capabilities "
        f"({sorted(reached)}), and the floor pinned here is "
        f"{SLASH_COMMAND_CAPABILITY_FLOOR}. Either commands were unwired from the CLI — "
        "look at commands/ — or the invocation patterns this file scans for no longer "
        "match what those files write, in which case the coverage assertion above has "
        "stopped meaning anything."
    )
