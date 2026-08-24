"""The check that catches a workbench subcommand missing from SKILL.md §2 (#491, #470).

§2 is what a session reads to find out what rig has, so a surface missing from it does not
exist from there. Three times one went missing and each time a person found it: #395 was
`rig-evidence` / `rig-mission-control`, #470 was `receipt` / `import` / `contract` and the run
graph, and fixing #470 turned up nine more subcommands added in the meantime.

What is checked here is #470's class only — subcommands of the `rig-wb wb` parser. #395's
two are separate console scripts (`pyproject.toml` entry points, `rig_workbench/evidence.py`
and `rig_workbench/mission_control.py`), outside `build_parser()` and spelled `rig-evidence` /
`rig-mission-control` in §2, so a recurrence of that shape is still found by hand.

The obvious check does not work, and that is why this file is mostly positive controls.
Measured against §2 as it stood before #470 was fixed, a check asking whether the subcommand's
name appears anywhere in §2 answered *yes* for `import` (the `/rig:import` pack row) and *yes*
for `contract` ("output-contract facet") — reporting the exact omission it existed to catch as
covered. Every branch below is therefore exercised with a catalogue it must object to, not
only with one it must accept.
"""

import argparse

import pytest

from rig_workbench.validation.catalog import (
    CATALOG_SECTION,
    INTERNAL_ONLY,
    PACK_ROW_ONLY,
    catalogued_subcommands,
    workbench_catalog,
)
from rig_workbench.validation.config import ROOT
from rig_workbench.workbench.cli import build_parser


def parser_for(*names):
    """A CLI registering exactly these subcommands, plus every internal and pack-row one."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    for name in sorted({*names, *INTERNAL_ONLY, *PACK_ROW_ONLY}):
        sub.add_parser(name)
    return parser


CLI = parser_for("receipt", "contract")

_LISTED = "> | **assurance** | 受領書と BYOO 契約 | `rig_workbench/workbench/assurance.py` |"


def skill(section_body=_LISTED, before="", after=""):
    """A `SKILL.md` holding `section_body` as its §2, plus text on either side."""
    return (f"# rig\n\n## 1. Overview\n\n{before}\n\n{CATALOG_SECTION[0]}\n\n"
            f"{section_body}\n\n{CATALOG_SECTION[1]}\n\n{after}\n")


BOTH = _LISTED + "\n> | **assurance** | `rig-wb wb {receipt,contract}` の2本 | `x` |"


# ── the omission the check exists to catch ───────────────────────────────────
def test_it_finds_a_subcommand_the_catalog_does_not_name():
    uncatalogued, stale, blind = workbench_catalog(
        CLI, skill(_LISTED + "\n> | x | `rig-wb wb receipt` | `y` |"))
    assert uncatalogued == ["contract"] and stale == [] and blind == []


def test_a_catalogued_subcommand_is_not_reported():
    uncatalogued, stale, blind = workbench_catalog(CLI, skill(BOTH))
    assert uncatalogued == [] and stale == [] and blind == []


# ── the two false passes measured before #470 was fixed ──────────────────────
def test_a_pack_row_naming_the_same_word_is_not_a_catalog_entry():
    """`import` is both a workbench subcommand and the name of the `/rig:import` pack. Before
    #470 was fixed the subcommand was absent from §2 while the pack row was there, and asking
    whether the word appears in §2 answered yes — the check reporting success for the one
    thing it exists to catch."""
    pack_row = ("> | **skill-import**（`/rig:import`・generator） | 外部 skill を取り込む | "
                "`facets/instructions/skill-import` |")
    cli = parser_for("import")
    uncatalogued, _, blind = workbench_catalog(cli, skill(BOTH + "\n" + pack_row))
    assert blind == []
    assert uncatalogued == ["import"], "the /rig:import pack row was read as a catalog entry"


def test_a_facet_named_after_a_subcommand_is_not_a_catalog_entry():
    """`contract` is a workbench subcommand and also the tail of "output-contract facet",
    which §2 has carried since long before the subcommand existed. Same measured false pass,
    from a row about something else entirely."""
    facet_row = ("> | **output-contract facet** | subagent 出力の形式定義 | "
                 "`facets/output-contracts/review-verdict` |")
    uncatalogued, _, blind = workbench_catalog(
        CLI, skill(_LISTED + "\n> | x | `rig-wb wb receipt` | `y` |\n" + facet_row))
    assert blind == []
    assert uncatalogued == ["contract"], "the output-contract facet row was read as an entry"


def test_a_name_inside_another_row_s_file_list_is_not_a_catalog_entry():
    """The shipped assurance row lists `rig_workbench/workbench/{assurance,contract,…}.py`, so
    the third false pass in the same family is a module named after its subcommand. A brick
    path says which file implements a surface; it does not say the catalogue names it."""
    uncatalogued, _, blind = workbench_catalog(
        CLI, skill(_LISTED + "\n> | x | `rig-wb wb receipt` | "
                   "`rig_workbench/workbench/{assurance,contract}.py` |"))
    assert blind == []
    assert uncatalogued == ["contract"], "a module path was read as a catalog entry"


def test_a_mention_in_prose_is_not_a_catalog_entry():
    """Not even the name on its own in backticks: §2's rows are full of backticked words."""
    for prose in ("contract で契約を出す", "`contract` で契約を出す", "`wb contract`"):
        uncatalogued, _, blind = workbench_catalog(
            CLI, skill(_LISTED + f"\n> | x | `rig-wb wb receipt` — {prose} | `y` |"))
        assert blind == []
        assert uncatalogued == ["contract"], f"{prose!r} was read as a catalog entry"


# ── where the catalog is read from ───────────────────────────────────────────
def test_an_entry_outside_section_2_does_not_catalog_anything():
    """§2 is the brick catalogue; the rest of SKILL.md is the engine's procedure and names
    commands as it explains them. An invocation written in §6 is rig telling a session how to
    run something, not the catalogue saying rig has it."""
    entry = "`rig-wb wb {receipt,contract}` を使う。"
    uncatalogued, _, blind = workbench_catalog(CLI, skill(BOTH, after=entry))
    assert uncatalogued == [] and blind == []
    for where in ("before", "after"):
        uncatalogued, _, blind = workbench_catalog(CLI, skill(_LISTED, **{where: entry}))
        assert blind and any("no `rig-wb wb <name>` entries found in §2" in why
                             for why in blind), (where, blind)


def test_an_invocation_written_with_arguments_is_a_usage_example():
    """The name must be the whole of what follows `rig-wb wb`. §2 identifies a surface by its
    bare invocation; a spelled-out call with its arguments is an example inside a description,
    and counting one would let a sentence about how to use a subcommand stand in for the
    catalogue listing it — the same rule `listed_subcommands` applies to the ops header."""
    for spelled in ("`rig-wb wb contract <task_id>`", "`rig-wb wb contract --json`"):
        uncatalogued, _, _ = workbench_catalog(
            CLI, skill(_LISTED + f"\n> | x | `rig-wb wb receipt` と {spelled} | `y` |"))
        assert uncatalogued == ["contract"], f"{spelled} was read as a catalog entry"


def test_the_brace_notation_the_catalog_already_uses_is_expanded():
    """§2 groups a surface's subcommands into one entry, which is how all three shipped rows
    are written. Reading the run literally would report every name in every group missing."""
    assert catalogued_subcommands("`rig-wb wb {receipt,import,contract}`") == [
        "contract", "import", "receipt"]
    assert catalogued_subcommands("`rig-wb wb receipt`") == ["receipt"]
    assert catalogued_subcommands("受領書は receipt で出す") == []
    assert catalogued_subcommands("`rig-wb hostcheck` `rig-evidence`") == []


# ── the allowlists, in both directions ───────────────────────────────────────
def test_an_internal_subcommand_is_not_reported():
    """`new` and `intent` are called by the flow itself. §2 catalogues what a session can
    reach; a subcommand no user types is not a missing listing."""
    assert {"new", "intent"} <= INTERNAL_ONLY
    uncatalogued, _, _ = workbench_catalog(CLI, skill(BOTH))
    assert uncatalogued == []


def test_a_run_operation_is_not_reported():
    """§2 catalogues surfaces, and the operations on a run — `status`, `diff`, `accept` — are
    one surface, the `/rig:go` workbench pack row. `commands/go.md`'s route table is where
    each of them is written down individually, and `check_workbench_routing` checks that."""
    assert {"status", "diff", "accept"} <= PACK_ROW_ONLY
    uncatalogued, _, _ = workbench_catalog(CLI, skill(BOTH))
    assert uncatalogued == []


def test_a_new_subcommand_on_neither_list_is_reported():
    """The allowlist is a boundary somebody drew, not a wildcard: something added tomorrow is
    reported until a person decides which side of it the addition belongs on."""
    uncatalogued, _, _ = workbench_catalog(parser_for("brand-new"), skill(BOTH))
    assert uncatalogued == ["brand-new"]


def test_an_allowlist_entry_for_something_that_no_longer_exists_is_reported():
    """An entry naming a removed subcommand suppresses nothing and hides that it stopped
    applying — which is how an allowlist quietly becomes wrong."""
    lean = argparse.ArgumentParser()
    lean.add_subparsers(dest="cmd").add_parser("receipt")
    _, stale, _ = workbench_catalog(lean, skill(BOTH))
    assert set(stale) == set(PACK_ROW_ONLY)


def test_the_two_allowlists_do_not_overlap():
    """A name on both lists would be claiming two contradictory things: that no user types it,
    and that §2 catalogues it through the row for the entry point users type it at."""
    assert INTERNAL_ONLY & PACK_ROW_ONLY == frozenset()


# ── a check that found nothing to check has not passed ───────────────────────
@pytest.mark.parametrize("parser,skill_md,says", [
    (argparse.ArgumentParser(), skill(BOTH), "parser exposes no subcommands"),
    (CLI, "# rig\n\nno headings here\n", "exactly one section bounded by"),
    (CLI, skill(BOTH) + f"\n{CATALOG_SECTION[0]}\n\n{BOTH}\n\n{CATALOG_SECTION[1]}\n",
     "exactly one section bounded by"),
    (CLI, f"# rig\n\n{CATALOG_SECTION[1]}\n\n{BOTH}\n\n{CATALOG_SECTION[0]}\n",
     "exactly one section bounded by"),
    (CLI, skill(_LISTED), "no `rig-wb wb <name>` entries found in §2"),
])
def test_a_check_that_found_nothing_to_check_has_not_passed(parser, skill_md, says):
    """Zero omissions out of zero things read is not "all clear" — it is the shape this check
    reads having moved, and saying nothing then is the failure it exists to prevent. A
    duplicated heading is the same failure from the other side: it does not narrow anything
    down, so the check would read one section and report about the other."""
    uncatalogued, _, blind = workbench_catalog(parser, skill_md)
    assert blind and any(says in why for why in blind), blind
    assert uncatalogued == [], "a blind check reported findings as if it had seen the catalog"


def test_a_heading_that_is_not_a_line_of_its_own_is_not_the_section_bound():
    """A landmark found as a substring is not a landmark found by structure: a heading quoted
    inside a sentence would let the slice start somewhere that is not §2."""
    quoted = skill(BOTH).replace(CATALOG_SECTION[0], f"（{CATALOG_SECTION[0]} を参照）")
    uncatalogued, _, blind = workbench_catalog(CLI, quoted)
    assert any("exactly one section bounded by" in why for why in blind), blind
    assert uncatalogued == []


# ── the check is pointed at the files it claims to read ──────────────────────
def _shipped():
    return (ROOT / "skills" / "engine" / "SKILL.md").read_text(encoding="utf-8")


def test_it_reads_the_repository_it_ships_with():
    """The synthetic cases above prove the logic; this proves it is aimed at the real file. A
    guard aimed at nothing passes every test written about its logic."""
    uncatalogued, stale, blind = workbench_catalog(build_parser(), _shipped())
    assert blind == [], blind
    assert stale == [], f"PACK_ROW_ONLY names something that is not a subcommand: {stale}"
    assert uncatalogued == [], (
        f"SKILL.md §2 names no `rig-wb wb <name>` for these subcommands, so a session reading "
        f"the brick catalog cannot find out they exist: {uncatalogued}")


def test_the_landmarks_it_slices_on_are_in_the_shipped_file():
    """The bounds are how this check finds §2; if they stop matching it goes blind, and a
    blind check nobody notices is what #491 is about."""
    lines = _shipped().splitlines()
    for landmark in CATALOG_SECTION:
        assert lines.count(landmark) == 1, f"{landmark!r} is not a unique line of SKILL.md"


#: The three rows #492 added to §2, and the user-facing names each one carries. Both halves
#: are written out rather than read off the document, so the expectation cannot shrink when
#: the document does — and `intent` is absent from the second row's list because the flow
#: calls it itself, not because anything derived that.
_SHIPPED_ROWS = [
    ("{receipt,import,contract}", ["contract", "import", "receipt"]),
    ("{intent,intent-derive,assurance-target,assurance-derive}",
     ["assurance-derive", "assurance-target", "intent-derive"]),
    ("{synthesise,dev-loop,route-team,budget-plan,provenance}",
     ["budget-plan", "dev-loop", "provenance", "route-team", "synthesise"]),
]

#: The thirteen §2 was missing when #470 was filed, minus `intent` and minus the run graph,
#: which is a schema rather than a subcommand. Written out, and checked against the rows
#: above, so neither list can quietly become the other's shadow.
_MISSING_BEFORE_470 = [
    "assurance-derive", "assurance-target", "budget-plan", "contract", "dev-loop", "import",
    "intent-derive", "provenance", "receipt", "route-team", "synthesise",
]


def test_the_shipped_rows_carry_exactly_the_names_470_was_missing():
    assert sorted(n for _, names in _SHIPPED_ROWS for n in names) == _MISSING_BEFORE_470


@pytest.mark.parametrize("braces,carried", _SHIPPED_ROWS)
def test_dropping_one_shipped_row_reports_exactly_the_names_it_carried(braces, carried):
    """#491's premise, measured on the document that shipped rather than on a fixture this
    file wrote — where a heading, a stray table, or a row's own notation could each make the
    same logic answer differently. One row at a time, so a pass cannot come from the three
    being read as a block.

    `import` and `contract` are in the first row, and their decoys — the `/rig:import` pack
    row and "output-contract facet" — are still in the §2 this test hands the check. That is
    the measurement #470 made: a check asking whether the name is *mentioned* called both of
    them covered while they were missing."""
    shipped = _shipped()
    rows = [line for line in shipped.splitlines() if f"`rig-wb wb {braces}`" in line]
    assert len(rows) == 1, f"§2 does not hold exactly one row for `{braces}`: {len(rows)}"
    without = shipped.replace(rows[0] + "\n", "")
    for decoy in ("/rig:import", "output-contract facet"):
        assert decoy in without, f"the {decoy!r} false pass is not in the document under test"

    uncatalogued, _, blind = workbench_catalog(build_parser(), without)
    assert blind == [], blind
    assert uncatalogued == carried, uncatalogued


def test_the_document_before_470_was_fixed_is_refused_rather_than_passed():
    """Strip all three rows and §2 has the property it had when #470 was filed: no `rig-wb wb`
    entry anywhere in it. The check cannot tell "every surface was dropped" from "the notation
    moved", so it says so — a FAIL, louder than the eleven WARNs, and not the silence that let
    this omission ship three times."""
    shipped = _shipped()
    without = "\n".join(line for line in shipped.splitlines() if "`rig-wb wb {" not in line)
    assert len(without.splitlines()) == len(shipped.splitlines()) - 3, "the rows have moved"

    uncatalogued, _, blind = workbench_catalog(build_parser(), without)
    assert any("no `rig-wb wb <name>` entries found in §2" in why for why in blind), blind
    assert uncatalogued == []
