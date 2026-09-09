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
