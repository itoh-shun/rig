"""The set of `rig.<name>/v<N>` schema ids rig emits, pinned as it appears in the source.

A schema id is the one field a consumer is told to branch on. `jsonio.envelope` puts
it in every enveloped payload precisely so a caller can refuse a version it does not
know instead of misreading it (tests/test_json_contract.py), and the receipt, graph
and govern records carry their own. That makes the literal string a public interface:
`rig.assurance-receipt/v1` is a name somebody else's `if` statement compares against.

The tests already covering these ids compare a module constant to itself —
`assert module.SCHEMA == "rig.assurance-receipt/v1"` is written in the same repo, and
in the same commit, as the constant it checks. Rename both and the assertion still
passes. Nothing was watching the *set*: an id could be renamed, dropped, or quietly
added, and the suite would stay green while every consumer downstream broke.

So this file scans the source trees, collects every schema-id literal, and compares
the result against a set written out below. It does not care what any module says
about itself; it cares what strings the tree actually contains.
"""

import ast
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The trees that ship. `tests/` is deliberately absent, and this is the only
#: exclusion in the file: fixtures there mint ids that were never a contract —
#: placeholders for negative cases (`rig.something-else/v1`), superseded versions
#: kept to prove an old payload is rejected (`rig.policy/v1`, `rig.fleet/v0`),
#: speculative ones for forward-compatibility checks (`rig.assurance-receipt/v2`).
#: Scanning them would put strings nothing emits into a registry of what rig emits.
#: test_the_placeholder_ids_under_tests_are_what_keeps_that_tree_out_of_the_scan
#: checks that this reason is still true rather than leaving it as a claim.
SCANNED_TREES = ("rig_workbench", "scripts")

#: The shape as it appears in the wild: `rig.` then a lowercase slug, then `/v` and
#: a number. The version is part of the name, not a sibling field, so it travels
#: with the payload wherever it is copied.
SCHEMA_ID = re.compile(r"rig\.[a-z0-9_.\-]+/v\d+")

# WHAT THIS SCAN CANNOT SEE, and why `rig.gates/v1` is in the set below by accident:
#
# The scan reads string *literals*. An id composed at runtime is invisible to it, and
# there is one known instance in the tree today: `rig_workbench/jsonio.py` builds the id
# of every enveloped command as
#
#     return {"schema": f"rig.{schema}/v{ENVELOPE_VERSION}", ...}   # jsonio.envelope
#
# so `wb gates --json` publishes `rig.gates/v1` while the string `rig.gates/v1` appears
# nowhere in the code that emits it. It reaches FROZEN_SCHEMA_IDS only because two pieces
# of *prose* happen to contain it: jsonio.py's own module docstring, and the `--json` help
# text in rig_workbench/workbench/cli.py. Prose is not measurement. Set
# `ENVELOPE_VERSION = 2` and every enveloped command starts publishing a new public name
# while this scan reports no change at all; edit only those docstrings and this scan
# reports a disappearance that never happened.
#
# So a green run here does not mean "the published ids are unchanged" — it means the
# literals are unchanged, which is less. Two tests at the bottom of the file cover what
# the literals miss: one pins the envelope version those ids are composed from, and one
# hands every id in the set to tests/test_schema_cli_contract.py, which runs the real
# process and reads what it actually prints.

# WHY THIS IS FROZEN, and why the failure you are reading is not noise:
#
# These ids are a published contract. A consumer — this repo's own mission-control
# reader, the MCP adapter, a CI job somewhere that greps a receipt — dispatches on
# the exact string. Renaming one is indistinguishable, from the inside, from tidying
# a constant: every in-repo reference moves with it and every in-process assertion
# still passes, while every reader outside the repo starts seeing a schema it has
# never heard of. The break is silent at exactly the moment it is cheapest to catch.
#
# This set is the thing that makes a rename a deliberate act. Editing it is allowed
# and sometimes right; doing it without noticing is what is not. If this test fails:
#   - an id was ADDED     -> new output, new public name. Add it here on purpose.
#   - an id DISAPPEARED   -> either the output is gone (a removal consumers must be
#                            told about) or it was renamed. A rename shows up as one
#                            of each, which is why both halves are reported.
# Bumping `/vN` is the supported way to change a shape. Reusing a name for a
# different shape is not, and it looks identical to this test — so say which you did.
FROZEN_SCHEMA_IDS = {
    "rig.assurance-budget/v1",
    "rig.assurance-contract/v1",
    "rig.assurance-graph/v1",
    "rig.assurance-receipt/v1",
    "rig.assurance-target/v1",
    "rig.byoo-import/v1",
    "rig.change-graph-assessment/v1",
    "rig.change-graph/v1",
    "rig.compose-options/v1",
    "rig.development-cycles/v1",
    "rig.effective-policy/v1",
    "rig.expected-outcome/v1",
    "rig.field-study/v1",
    "rig.fleet/v1",
    "rig.gates/v1",
    "rig.intent-contract/v1",
    "rig.knowledge-candidate-assessment/v1",
    "rig.knowledge-candidate-evidence/v1",
    "rig.knowledge-candidate/v1",
    "rig.mission-control/v1",
    "rig.mission-worker/v1",
    "rig.org-knowledge/v1",
    "rig.org/v2",
    "rig.policy/v2",
    "rig.production-anomaly-event/v1",
    "rig.production-anomaly-evidence/v1",
    "rig.production-anomaly-trigger-assessment/v1",
    "rig.production-observation/v1",
    "rig.production-outcome/v1",
    "rig.provenance-graph/v1",
    "rig.queue-dependencies/v1",
    "rig.resolved-workflow/v1",
    "rig.team-routing/v1",
    "rig.waivers/v2",
    "rig.workflow-effectiveness-query/v1",
    "rig.workflow-effectiveness/v1",
    "rig.workflow-resolution-error/v1",
    "rig.workflow-resolution/v1",
}


def schema_ids_in(tree: pathlib.Path) -> dict[str, set[str]]:
    """Every schema id appearing in a string literal under `tree`, to the files it is in.

    A string literal, not a regex over the file: a comment or a stray mention in prose
    is not something rig emits, and only what is emitted is the contract. Parsing is
    `ast` on the source text — no import, no subprocess, so the whole scan is a read of
    the tree. A file that will not parse raises rather than being skipped, because a
    source file this cannot read is a source file whose ids it cannot vouch for.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(tree.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for schema_id in SCHEMA_ID.findall(node.value):
                    found.setdefault(schema_id, set()).add(
                        path.relative_to(REPO_ROOT).as_posix())
    return found


def emitted_schema_ids() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for tree in SCANNED_TREES:
        for schema_id, paths in schema_ids_in(REPO_ROOT / tree).items():
            found.setdefault(schema_id, set()).update(paths)
    return found


def test_the_schema_ids_in_the_source_are_exactly_the_ones_this_registry_pins():
    """The whole point of the file. Both halves of the difference are named, because a
    rename is an addition and a disappearance at once and reporting only one would
    describe it as something it is not."""
    found = emitted_schema_ids()
    added = sorted(set(found) - FROZEN_SCHEMA_IDS)
    disappeared = sorted(FROZEN_SCHEMA_IDS - set(found))
    detail = "\n".join(
        [f"  added:       {schema_id}  ({', '.join(sorted(found[schema_id]))})"
         for schema_id in added]
        + [f"  disappeared: {schema_id}" for schema_id in disappeared])
    assert not added and not disappeared, (
        "the set of schema ids rig emits has moved:\n" + detail + "\n"
        "One added and one disappeared together is a RENAME, and a rename of a "
        "published id breaks every consumer dispatching on the old string while every "
        "in-repo assertion keeps passing. An addition alone is a new public name; a "
        "disappearance alone is a removal consumers have to be told about. Update "
        "FROZEN_SCHEMA_IDS deliberately, and say in review which of the three it was."
    )


def test_the_placeholder_ids_under_tests_are_what_keeps_that_tree_out_of_the_scan():
    """SCANNED_TREES excludes `tests/` on the grounds that ids there are fixtures rather
    than contract. That is checkable, so check it: the exclusion has to still be earning
    its place, not be a line nobody has re-read since it was written."""
    placeholders = sorted(set(schema_ids_in(REPO_ROOT / "tests")) - FROZEN_SCHEMA_IDS)
    assert placeholders, (
        "every schema id under tests/ is now one the shipped trees also emit, so the "
        "stated reason for leaving tests/ out of SCANNED_TREES no longer holds. Either "
        "scan tests/ too and drop the exclusion, or give it a reason that is true."
    )


# ── what the literals cannot vouch for ───────────────────────────────────────
#: The other half of the pair, and the half that runs the real thing:
#: tests/test_schema_cli_contract.py drives `python -m rig_workbench.cli` and holds what
#: comes back to a schema id written out as a literal, the way a consumer's `if` writes
#: it. An id pinned there is measured on the process; an id only in FROZEN_SCHEMA_IDS
#: above is measured on the source text.
CLI_CONTRACT = REPO_ROOT / "tests" / "test_schema_cli_contract.py"

#: The constant in that file naming the ids it cannot reach, each with the code path that
#: emits it and why no command prints it. Referred to by name so a rename of it fails
#: here, loudly, instead of silently reclassifying every id it holds as unmeasured.
NOT_PINNED_CONSTANT = "NOT_PINNED"

#: `jsonio.ENVELOPE_VERSION`, and the file it lives in. See "WHAT THIS SCAN CANNOT SEE":
#: this number is the `/vN` of every enveloped id, and no scan of literals can reach it.
ENVELOPE_VERSION_SOURCE = REPO_ROOT / "rig_workbench" / "jsonio.py"
PINNED_ENVELOPE_VERSION = 1


def _schema_ids_under(node, *, skip=frozenset()) -> set[str]:
    """Every schema id in a string literal under `node`, ignoring the literals in `skip`."""
    found: set[str] = set()
    for child in ast.walk(node):
        if (isinstance(child, ast.Constant) and isinstance(child.value, str)
                and id(child) not in skip):
            found.update(SCHEMA_ID.findall(child.value))
    return found


def cli_contract_coverage() -> tuple[set[str], set[str]]:
    """The ids tests/test_schema_cli_contract.py pins, and the ids it records as unreachable.

    That file is read as source text rather than imported, for the reason the block beside
    SCHEMA_ID gives: an import runs the module and hands back whatever its constants
    evaluate to, so an id assembled at import time would count as written down without ever
    being written down — the exact blindness this file is trying not to repeat. Reading the
    text also keeps the scan independent of whether that module imports cleanly, and makes
    a rename of NOT_PINNED an error here rather than a silent change of meaning.

    Docstrings are excluded from the pinned half deliberately. That file's prose names many
    ids while explaining itself, and a mention in prose is not an assertion about a running
    process — `rig.gates/v1` sits in FROZEN_SCHEMA_IDS today for precisely that reason.
    """
    module = ast.parse(CLI_CONTRACT.read_text(encoding="utf-8"), filename=str(CLI_CONTRACT))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(module)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    declarations = [
        node for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == NOT_PINNED_CONSTANT for t in node.targets)
    ]
    assert len(declarations) == 1, (
        f"{CLI_CONTRACT.name} no longer declares exactly one {NOT_PINNED_CONSTANT}: found "
        f"{len(declarations)}. That constant is where an id nothing can drive is allowed to "
        f"be recorded with a reason; without it every such id reads here as simply "
        f"unmeasured. If it was renamed, rename NOT_PINNED_CONSTANT to match."
    )
    recorded = _schema_ids_under(declarations[0])
    pinned = _schema_ids_under(module, skip=docstrings) - recorded
    return pinned, recorded


def test_every_frozen_id_is_either_pinned_through_the_real_cli_or_recorded_as_unreachable():
    """Each id in the frozen set has to be measured on the running process, or admitted.

    This is the check that keeps the frozen set from being trusted for more than it is.
    Freezing a literal says the string still exists somewhere in the tree; it says nothing
    about whether any command emits it, and — for a runtime-composed id — nothing about
    whether the published name is still that string at all. So every id here must be either
    a literal in tests/test_schema_cli_contract.py, where it is held against what the real
    process prints, or an entry in that file's NOT_PINNED, where the reason it cannot be
    reached is written down and itself checked.

    The failure this prevents is a specific one: an id added to the frozen set and to an
    exclusion list, and to nothing that runs. Neither half of that is a test.
    """
    pinned, recorded = cli_contract_coverage()
    unmeasured = sorted(FROZEN_SCHEMA_IDS - pinned - recorded)
    assert not unmeasured, (
        "these ids are frozen here but nothing in tests/test_schema_cli_contract.py either "
        "pins them or admits it cannot:\n"
        + "\n".join(f"  {schema_id}" for schema_id in unmeasured) + "\n"
        "Freezing a literal only says the string is still in the tree. Pin the id: drive "
        f"the command that emits it in {CLI_CONTRACT.name} and assert the id it prints. If "
        "no command can reach it, add an entry to that file's "
        f"{NOT_PINNED_CONSTANT} naming the code path that does emit it and why it is out of "
        "reach — an honest gap is reviewable, an id in a list nobody runs is not."
    )


# Yes, the assertion below compares a production constant against a literal written in the
# same repository, which is the shape of assertion this file's own docstring calls a
# tautology. It is not the same move, and the difference is what the literal is. In
# `assert module.SCHEMA == "rig.assurance-receipt/v1"` both sides are the *name*: one edit
# moves both and the assertion measures nothing. Here the literal is not a name, and no
# rename of any schema touches it. The only edit that reaches it is a bump of
# ENVELOPE_VERSION — which is exactly the change nothing else in the suite can see, because
# the ids it renames are composed rather than written. Set ENVELOPE_VERSION = 2 today and
# every test in this file stays green while `wb gates` starts publishing `rig.gates/v2`.
# With this assertion it goes red. It can only add sensitivity, never mask a change, which
# is the opposite of the tautology.
def test_the_envelope_version_every_enveloped_id_carries_is_still_the_one_frozen_here():
    """`ENVELOPE_VERSION` is the `/vN` of every id `jsonio.envelope` composes, so bumping it
    renames the published id of every enveloped command at once. That is a contract change
    of the same kind as editing FROZEN_SCHEMA_IDS, and this is where it is declared.

    Read out of the source with `ast` rather than imported, to stay the read of a tree that
    the rest of the file is — and so that a version that stops being a plain literal (read
    from the environment, computed) fails here instead of quietly passing.
    """
    source = ENVELOPE_VERSION_SOURCE.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(ENVELOPE_VERSION_SOURCE))
    versions = [
        node.value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "ENVELOPE_VERSION" for t in node.targets)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int)
    ]
    assert versions == [PINNED_ENVELOPE_VERSION], (
        f"{ENVELOPE_VERSION_SOURCE.relative_to(REPO_ROOT).as_posix()} declares "
        f"ENVELOPE_VERSION as {versions!r}, pinned here as [{PINNED_ENVELOPE_VERSION}]. "
        "jsonio.envelope composes every enveloped id as f\"rig.{schema}/v{ENVELOPE_VERSION}\", "
        "so this number is not an implementation detail: changing it renames the published "
        "id of every enveloped command — `rig.gates/v1` becomes `rig.gates/v2` — for every "
        "consumer dispatching on the old string, and no scan of string literals can see it "
        "happen. If the bump is deliberate, update FROZEN_SCHEMA_IDS and the ids pinned in "
        "tests/test_schema_cli_contract.py to match, then update this number and say in "
        "review that every enveloped id was renamed. If it is not deliberate, this is the "
        "test telling you so."
    )
