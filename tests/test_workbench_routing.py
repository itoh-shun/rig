"""The check that catches a workbench subcommand nobody wired into `/rig:go` (#478, #473).

Five issues (#261, #262, #327, #417, #473) were this same omission, found one at a time by
somebody tripping over it. The check exists so the sixth is found by CI instead — which only
holds if the check objects when it should, so every branch below is exercised with a wiring it
must reject, not only with one it must accept.
"""

import argparse

import pytest

from rig_workbench.validation.catalog import (
    INTERNAL_ONLY,
    OPS_HEADER_SECTION,
    ROUTE_TABLE_HEADERS,
    listed_subcommands,
    registered_subcommands,
    route_table,
    routed_subcommands,
    workbench_routing,
)
from rig_workbench.validation.config import ROOT
from rig_workbench.workbench.cli import build_parser


def parser_for(*names):
    """A CLI registering exactly these subcommands, plus every internal one."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    for name in sorted({*names, *INTERNAL_ONLY}):
        sub.add_parser(name)
    return parser


CLI = parser_for("status", "receipt")

_ROWS = f"{ROUTE_TABLE_HEADERS[0]}\n|---|---|\n| `status [<id>]` | `x` |\n"


def go(table_rows=_ROWS, before="", after=""):
    """A `go.md` holding `table_rows` as its route table, plus text on either side."""
    return f"# rig\n\n{before}\n### ① サブコマンド\n\n{table_rows}\n### ② 自然文タスク\n\n{after}\n"


def ops(header="**`/rig status`** の手順。", body=""):
    return f"{OPS_HEADER_SECTION[0]}\n\n{header}\n\n{OPS_HEADER_SECTION[1]}\n\n{body}\n"


ROUTED = _ROWS + "| `receipt [<id>]` | `facets/instructions/workbench-ops` |\n"
LISTED = "**`/rig status` / `/rig receipt`** の手順。"


# ── the omission the check exists to catch ───────────────────────────────────
def test_it_finds_a_subcommand_nobody_routed():
    unrouted, stale, blind = workbench_routing(CLI, go(), ops(LISTED))
    assert unrouted == ["receipt"] and stale == [] and blind == []


def test_both_surfaces_together_are_what_routes_it():
    unrouted, stale, blind = workbench_routing(CLI, go(ROUTED), ops(LISTED))
    assert unrouted == [] and stale == [] and blind == []


def test_routed_but_not_in_the_ops_list_is_still_reported():
    """#473 was the same four names missing from both surfaces, so a check covering one of
    them would have reported that issue half-fixed."""
    unrouted, _, _ = workbench_routing(CLI, go(ROUTED), ops())
    assert unrouted == ["receipt"]


def test_listed_but_not_in_the_route_table_is_still_reported():
    unrouted, _, _ = workbench_routing(CLI, go(), ops(LISTED))
    assert unrouted == ["receipt"]


# ── where each list is read from ─────────────────────────────────────────────
def test_a_mention_in_prose_is_not_a_route():
    """A subcommand named in a sentence is not one `/rig:go` will dispatch, and a check that
    accepted a mention would report success for the one thing it exists to catch."""
    unrouted, _, _ = workbench_routing(
        CLI, go(after="`receipt` は accept 後に使う。"), ops(LISTED))
    assert unrouted == ["receipt"], "a mention outside the table was read as a route"


def test_a_table_outside_the_route_table_section_is_not_a_route():
    """`go.md` holds more than the route table. A second table — flags, states, examples —
    whose first cell happens to name a subcommand is not the flow dispatching it."""
    elsewhere = "| 語 | 意味 |\n|---|---|\n| `receipt` | 受領書のこと |\n"
    unrouted, _, _ = workbench_routing(CLI, go(after=elsewhere), ops(LISTED))
    assert unrouted == ["receipt"], "a row outside the route-table section was read as a route"
    unrouted, _, _ = workbench_routing(CLI, go(before=elsewhere), ops(LISTED))
    assert unrouted == ["receipt"], "a row above the route-table section was read as a route"


def test_a_name_in_another_row_s_description_is_not_a_route():
    """The first cell is what `/rig:go` matches a leading word against; the second is prose."""
    mentions = _ROWS + "| `accept [<id>]` | `x`（`receipt` で受領書を確認できる） |\n"
    unrouted, _, _ = workbench_routing(CLI, go(mentions), ops(LISTED))
    assert unrouted == ["receipt"], "a name in a row's description was read as a route"

    orphan = _ROWS + "| （廃止） | `receipt` は使わない |\n"
    unrouted, _, _ = workbench_routing(CLI, go(orphan), ops(LISTED))
    assert unrouted == ["receipt"], "a name in a later column was read as a route"


def test_an_ops_mention_outside_the_header_is_not_a_procedure():
    """The ops instruction documents every subcommand in its body, so reading the document
    would count each `` `/rig <name>` `` heading as the header list having named it."""
    unrouted, _, _ = workbench_routing(
        CLI, go(ROUTED), ops(body="## `/rig receipt`\n\n受領書を出す。"))
    assert unrouted == ["receipt"], "a mention outside the header was read as a listing"


def test_prose_inside_the_route_table_section_is_not_a_route():
    """The section holds the table and the sentences around it. Reading every line in it
    rather than its table rows puts those sentences back in."""
    with_prose = "先頭語が `receipt` の場合の扱いは別途決める。\n\n" + _ROWS
    unrouted, _, _ = workbench_routing(CLI, go(with_prose), ops(LISTED))
    assert unrouted == ["receipt"], "a sentence inside the section was read as a route"


def test_a_first_cell_routes_the_name_it_opens_with():
    """`/rig:go` matches a leading word against the cell's opening backticked run. A name
    written later in that cell is an annotation on the row, and a name written without
    backticks is the cell talking about a subcommand rather than routing one."""
    for cell, why in [("| （receipt は廃止） | `x` |", "an unbackticked name"),
                      ("| （廃止: `receipt`） | `x` |", "an annotated name"),
                      ("| 旧 `receipt` の後継 `accept` | `x` |", "a name after prose")]:
        unrouted, _, _ = workbench_routing(CLI, go(_ROWS + cell + "\n"), ops(LISTED))
        assert unrouted == ["receipt"], f"{why} in the first cell was read as a route"
        assert routed_subcommands(cell) == [], why


def test_a_header_row_that_is_not_a_line_of_its_own_is_not_the_route_table():
    """`> | 先頭語 | 委譲先 |` contains the header row and is a blockquote. A landmark found as
    a substring is not a landmark found by structure, and slicing from the pipe inside it
    hands back rows that no longer form the table this check says it read."""
    for prefix in ("> ", "例: "):
        quoted = go().replace(ROUTE_TABLE_HEADERS[0], prefix + ROUTE_TABLE_HEADERS[0])
        unrouted, _, blind = workbench_routing(CLI, quoted, ops(LISTED))
        assert any("exactly one table headed" in why for why in blind), (prefix, blind)
        assert unrouted == []


def test_a_first_cell_whose_backticked_run_never_closes_is_not_a_route():
    """`` | `receipt | `` is a row nobody proof-read, not a route somebody wrote. Reading the
    name out of it would let a malformed row stand in for the wiring."""
    unopened = _ROWS + "| `receipt [<id>] | `x` |\n"
    unrouted, _, _ = workbench_routing(CLI, go(unopened), ops(LISTED))
    assert unrouted == ["receipt"], "an unclosed backticked run was read as a route"


def test_a_cell_carrying_an_escaped_pipe_is_still_one_cell():
    """Several shipped rows put `\\|` inside their usage string. Splitting on every pipe cuts
    those cells in half, and the half that is left holds a run that never closes — so the
    rows most likely to be right would be the ones reported missing."""
    escaped = _ROWS + "| `receipt [--period week\\|month]` | `x` |\n"
    unrouted, _, _ = workbench_routing(CLI, go(escaped), ops(LISTED))
    assert unrouted == [], "a cell was split at an escaped pipe"


def test_an_indented_pipe_line_does_not_continue_the_table():
    """A Markdown table's rows sit at the left margin. An indented `|` line belongs to a
    nested list or a code block, and treating it as a row lets text under the table keep the
    table going."""
    nested = _ROWS + "  | `receipt` | 受領書のこと |\n"
    unrouted, _, _ = workbench_routing(CLI, go(nested), ops(LISTED))
    assert unrouted == ["receipt"], "an indented pipe line was read as a row of the table"


def test_a_second_table_beside_the_route_table_is_not_the_route_table():
    """`go.md` may grow another table — flags, states, examples — and reading every row
    between two headings would take its first column for dispatch wiring."""
    beside = _ROWS + "\n| 状態 | 意味 |\n|---|---|\n| `receipt` | 受領書のこと |\n"
    unrouted, _, _ = workbench_routing(CLI, go(beside), ops(LISTED))
    assert unrouted == ["receipt"], "a row in a second table was read as a route"


def test_the_ops_list_is_read_from_its_own_notation():
    assert listed_subcommands(LISTED) == ["receipt", "status"]
    assert listed_subcommands("`/rig:go` is the entry point") == []
    # The header names subcommands, it does not spell invocations: `` `/rig receipt <id>` ``
    # is a usage example, and counting it would let one example stand in for the listing.
    assert listed_subcommands("**`/rig receipt <task_id>`** を使う") == []


# ── the allowlist, in both directions ────────────────────────────────────────
def test_an_internal_subcommand_is_not_reported():
    """`new` is called by the flow itself, never by a user typing it after `/rig:go`."""
    assert "new" in INTERNAL_ONLY
    unrouted, _, _ = workbench_routing(CLI, go(), ops(LISTED))
    assert unrouted == ["receipt"], "an internal subcommand was reported as unrouted"


def test_an_allowlist_entry_for_something_that_no_longer_exists_is_reported():
    """An entry naming a removed subcommand suppresses nothing, and hides that it stopped
    applying — which is how an allowlist quietly becomes wrong."""
    lean = argparse.ArgumentParser()
    lean.add_subparsers(dest="cmd").add_parser("status")
    _, stale, _ = workbench_routing(lean, go(), ops(LISTED))
    assert set(stale) == set(INTERNAL_ONLY)


# ── the check reads argparse, not the spelling of a registration ─────────────
def test_a_registration_spelled_differently_is_still_a_subcommand():
    """A regex over `cli.py` would lose a subcommand registered through a helper or a loop,
    and losing one silently is indistinguishable from it being routed."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")

    def register(name):
        return sub.add_parser(name)

    for name in ("status", "receipt"):
        register(name)
    assert registered_subcommands(parser) == ["receipt", "status"]


def test_a_parser_without_subcommands_reads_as_none():
    assert registered_subcommands(argparse.ArgumentParser()) == []


def test_a_top_level_option_with_choices_is_not_a_subcommand():
    """`choices` is not what makes something dispatchable — `--type` has choices and routes
    nothing. Reading anything that carries them would answer with the wrong list."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", choices=["bugfix", "feature"])
    parser.add_subparsers(dest="cmd").add_parser("status")
    assert registered_subcommands(parser) == ["status"]


# ── the route table is the table, not the text around it ─────────────────────
def test_the_table_stops_at_the_first_line_that_is_not_a_row():
    """The rows under the header, not everything after it. A sentence below the table that
    names a subcommand is not the table routing it."""
    trailing = _ROWS + "\n`receipt` は accept 後に使う。\n"
    assert routed_subcommands(route_table(go(trailing))) == ["status"]


def test_an_ops_section_whose_end_landmark_moved_is_not_the_whole_document():
    """A start landmark that still matches while the end one does not is the shape most likely
    to happen — a heading renamed below the list. Reading to the end of the file then would
    sweep in every `` `/rig <name>` `` heading in the body."""
    truncated = f"{OPS_HEADER_SECTION[0]}\n\n{LISTED}\n\n## `/rig accept`\n"
    unrouted, _, blind = workbench_routing(CLI, go(ROUTED), truncated)
    assert any("exactly one section bounded by" in why for why in blind), blind
    assert unrouted == []


@pytest.mark.parametrize("go_md,ops_md,says", [
    (go(_ROWS + f"\n{ROUTE_TABLE_HEADERS[0]}\n|---|---|\n| `receipt [<id>]` | `x` |\n"),
     None, "exactly one table headed"),
    (None, (f"{OPS_HEADER_SECTION[0]}\n\n{LISTED}\n\n{OPS_HEADER_SECTION[1]}\n\n"
            f"{OPS_HEADER_SECTION[0]}\n\n{LISTED}\n\n{OPS_HEADER_SECTION[1]}\n"),
     "exactly one section bounded by"),
    (None, f"# rig\n\n{OPS_HEADER_SECTION[1]}\n\n{OPS_HEADER_SECTION[0]}\n\n{LISTED}\n",
     "exactly one section bounded by"),
])
def test_a_landmark_that_does_not_locate_a_section_is_refused(go_md, ops_md, says):
    """A duplicated landmark does not narrow anything down — `str.find` takes whichever copy
    comes first, so the check would read one and report about the other. A test asserting the
    shipped files have unique landmarks only covers a run that executes that test; the check
    has to refuse the ambiguity itself, or `--validate` passes while reading the wrong table.
    The third case is the same failure from the other side: both landmarks present and unique,
    but the closing one above the opening one, so nothing lies between them — and an empty
    slice read as an empty list is a reading, not a refusal."""
    unrouted, _, blind = workbench_routing(
        CLI, go_md if go_md is not None else go(ROUTED),
        ops_md if ops_md is not None else ops(LISTED))
    assert any(says in why for why in blind), blind
    assert unrouted == []


# ── a check that found nothing to check has not passed ───────────────────────
@pytest.mark.parametrize("parser,go_md,ops_md,says", [
    (argparse.ArgumentParser(), go(ROUTED), ops(LISTED), "parser exposes no subcommands"),
    (CLI, "# rig\n\nno table here\n", ops(LISTED), "exactly one table headed"),
    (CLI, go(f"{ROUTE_TABLE_HEADERS[0]}\n|---|---|\n| （廃止） | `x` |\n"), ops(LISTED),
     "no routed names found"),
    (CLI, go(ROUTED), "# something else\n\nno landmarks\n", "exactly one section bounded by"),
    (CLI, go(ROUTED), ops("手順の説明はここにはない。"), "no `/rig <name>` entries"),
])
def test_a_check_that_found_nothing_to_check_has_not_passed(parser, go_md, ops_md, says):
    """Zero omissions out of zero things read is not "all clear" — it is the shape this check
    reads having moved, and saying nothing then is the failure it exists to prevent."""
    unrouted, _, blind = workbench_routing(parser, go_md, ops_md)
    assert blind and any(says in why for why in blind), blind
    assert unrouted == [], "a blind check reported findings as if it had seen the surfaces"


# ── the check is pointed at the files it claims to read ──────────────────────
def test_it_reads_the_repository_it_ships_with():
    """The synthetic cases above prove the logic; this proves it is aimed at the real files.
    A guard aimed at nothing passes every test written about its logic."""
    go_md = (ROOT / "commands" / "go.md").read_text(encoding="utf-8")
    ops_md = (ROOT / "skills" / "engine" / "facets" / "instructions"
              / "workbench-ops.md").read_text(encoding="utf-8")
    parser = build_parser()
    assert len(registered_subcommands(parser)) > 30, "the CLI this check reads has moved"

    unrouted, stale, blind = workbench_routing(parser, go_md, ops_md)
    assert blind == [], blind
    assert stale == [], f"INTERNAL_ONLY names something that is not a subcommand: {stale}"
    assert unrouted == [], (
        f"these subcommands are in neither commands/go.md's route table nor the ops "
        f"instruction's header, so /rig:go cannot dispatch them: {unrouted}")


#: The four #473 was about, plus the three shipped rows that carry `\|` inside their usage
#: string — the rows where cell-splitting is most likely to go wrong, and where a `0 unrouted`
#: line would look identical whether they were read correctly or dropped out entirely.
_ROWS_THAT_MUST_BE_READ = ("gates", "receipt", "import", "contract",
                           "digest", "instincts", "provenance")


@pytest.mark.parametrize("name", _ROWS_THAT_MUST_BE_READ)
def test_removing_a_real_row_from_the_shipped_go_md_is_reported(name):
    """#478's acceptance criterion, kept: run against the state before #473 was fixed and the
    missing names come back. The synthetic cases prove the logic on documents this test wrote;
    this proves it on the document the repository ships, where a heading, a stray table, or a
    row's own notation could each make the same logic answer differently."""
    go_md = (ROOT / "commands" / "go.md").read_text(encoding="utf-8")
    ops_md = (ROOT / "skills" / "engine" / "facets" / "instructions"
              / "workbench-ops.md").read_text(encoding="utf-8")
    parser = build_parser()

    without = "\n".join(line for line in go_md.splitlines()
                         if not line.startswith(f"| `{name}"))
    assert without != go_md, f"no route-table row starts with `{name}"
    unrouted, _, blind = workbench_routing(parser, without, ops_md)
    assert blind == [], blind
    assert unrouted == [name], f"dropping `{name}`'s row was not reported: {unrouted}"


def test_the_landmarks_it_slices_on_are_in_the_shipped_files():
    """The bounds are how this check finds each list; if they stop matching it goes blind, and
    a blind check that nobody notices is what #478 is about."""
    go_md = (ROOT / "commands" / "go.md").read_text(encoding="utf-8")
    ops_md = (ROOT / "skills" / "engine" / "facets" / "instructions"
              / "workbench-ops.md").read_text(encoding="utf-8")
    # Across every accepted spelling, not per spelling: a file carrying one table under each
    # holds two route tables, and this check would read one while reporting on the other.
    assert sum(go_md.count(header) for header in ROUTE_TABLE_HEADERS) == 1, \
        "the route table's header row is not unique"
    for landmark in OPS_HEADER_SECTION:
        assert ops_md.count(landmark) == 1, f"{landmark!r} is not unique in workbench-ops.md"


def test_two_spellings_of_the_header_are_as_ambiguous_as_two_of_one():
    """Accepting a second spelling of the header must not accept a second route table.

    While the command layer is being translated, `go.md` could carry an English table and a
    Japanese one at once. Counting per spelling would find exactly one of each and report
    success, while the check read one table and answered about the other — which is the
    failure the uniqueness rule exists to prevent, arriving through the door opened to let
    the translation in.
    """
    english, japanese = ROUTE_TABLE_HEADERS[0], ROUTE_TABLE_HEADERS[1]
    both = go(f"{english}\n|---|---|\n| `status [<id>]` | `x` |\n"
              f"\n{japanese}\n|---|---|\n| `receipt [<id>]` | `x` |\n")
    assert route_table(both) is None
