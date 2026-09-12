"""The pre-dispatch disjointness check, and the ways it is allowed to say no.

`rig_workbench/orchestrate/plan_dispatch.py` reads the 触るファイル and 依存 columns of a
`task-plan` table and refuses a parallel dispatch whose parallel-safe lanes declare the same
file. The four things this file pins are the four ways that check can be wrong:

1. **It must refuse an overlap.** Two lanes with dependency `—` naming one file is the
   incident this module was written for — assigned by hand, believed disjoint, and one
   lane's `git stash` took the other's edits.
2. **It must clear a genuine disjointness**, or it is a check nobody will leave switched on.
3. **A row with a real dependency is outside the check.** A task that waits on another is
   not dispatched beside it, so sharing a file with it is ordinary. A checker that counted
   those would refuse every plan that edits one file in two ordered steps.
4. **It must not pass vacuously.** This is the one that matters most, and the one a checker
   fails silently: an empty table, an unreadable table, or a lane that declares no files
   must not come back cleared. `test_an_empty_table_does_not_pass_vacuously` and its
   neighbours are the whole reason `DispatchVerdict` spells silence (`nothing-to-check`) and
   approval (`cleared`) differently instead of returning a bool.

The measurement is pure text in, verdict out: no filesystem, no subprocess, no git — safe
under `pytest -n auto`.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from rig_workbench.orchestrate import plan_dispatch

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIPPED_CONTRACT = (
    REPO_ROOT / "skills" / "engine" / "facets" / "output-contracts" / "task-plan.md"
)
#: The only real task table in the tree, and the corpus this parser is measured against:
#: `docs/v3-architecture-design-brief.ja.md` §11 (T0-T12). It is written by hand, in
#: Japanese, with `・` between paths and backticks around them — none of which the first
#: version of this parser survived.
DESIGN_BRIEF = REPO_ROOT / "docs" / "v3-architecture-design-brief.ja.md"


def brief_table() -> str:
    """The §11 task table, sliced out of the brief by its own header row."""
    lines = DESIGN_BRIEF.read_text(encoding="utf-8").splitlines()
    starts = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("|") and "触るファイル" in line and "依存" in line
    ]
    assert starts, f"no task table found in {DESIGN_BRIEF} — the corpus this parser is read against"
    start = starts[0]
    end = start
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    return "\n".join(lines[start:end])

HEADER = "| # | 目的 | 触るファイル | 手順 | 検証 | 依存 |\n|---|---|---|---|---|---|\n"


def plan(*rows: str) -> str:
    """A minimal but real `task-plan` document around the given task rows."""
    return (
        "## rig task-plan: fixture\n\n"
        "概要: fixture\n未確定/要調査: なし\nタスク数: "
        f"{len(rows)}（うち並列可: {len(rows)}）\n\n### タスク\n" + HEADER + "".join(rows)
    )


def row(task_id: str, files: str, depends: str = "—") -> str:
    return f"| {task_id} | ある目的 | {files} | 手順 | `pytest` が green | {depends} |\n"


# ── 1. overlap is refused ────────────────────────────────────────────────────


def test_two_parallel_lanes_touching_one_file_are_refused() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/auth/token.ts"), row("T2", "src/auth/token.ts"))
    )
    assert verdict.decision == "refused"
    assert verdict.dispatch_allowed is False
    assert [(o.left, o.right) for o in verdict.overlaps] == [("T1", "T2")]
    assert verdict.overlaps[0].files == ("src/auth/token.ts",)


def test_the_overlap_is_named_in_the_verdict_the_caller_prints() -> None:
    """A refusal nobody can act on sends the reader back to the plan to diff it by eye."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(
            row("T1", "src/a.ts, src/shared.ts"),
            row("T2", "src/b.ts"),
            row("T3", "src/shared.ts, src/c.ts"),
        )
    )
    assert verdict.decision == "refused"
    printed = "\n".join(verdict.lines())
    assert "T1 ∩ T3" in printed
    assert "src/shared.ts" in printed
    assert "src/b.ts" not in printed


def test_one_lane_declaring_a_directory_covers_a_file_the_other_declares() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "rig_workbench/orchestrate/"), row("T2", "rig_workbench/orchestrate/cli.py"))
    )
    assert verdict.decision == "refused"
    assert verdict.overlaps[0].files == ("rig_workbench/orchestrate/ ⊇ rig_workbench/orchestrate/cli.py",)


def test_the_same_path_written_two_ways_is_the_same_path() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "`./src/auth/token.ts`"), row("T2", "src/auth/token.ts"))
    )
    assert verdict.decision == "refused"


# ── 2. disjoint lanes pass ───────────────────────────────────────────────────


def test_two_parallel_lanes_on_disjoint_files_are_cleared() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/auth/token.ts"), row("T2", "src/auth/handler.ts"))
    )
    assert verdict.decision == "cleared"
    assert verdict.dispatch_allowed is True
    assert verdict.lanes == ("T1", "T2")
    assert verdict.overlaps == ()
    assert verdict.rows_read == 2


def test_a_cleared_verdict_still_says_what_it_could_not_see() -> None:
    """The precision bound is in the answer, not only in the docstring."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T2", "b.py"))
    )
    assert verdict.decision == "cleared"
    assert "did not list" in verdict.reason


def test_a_multi_file_lane_is_split_on_the_declared_separators() -> None:
    parsed = plan_dispatch.parse_task_plan(
        plan(row("T1", "src/a.ts, `src/b.ts`、src/c.ts・`src/d.ts`"), row("T2", "src/e.ts"))
    )
    assert parsed.rows[0].files == ("src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts")
    assert plan_dispatch.check_disjoint_dispatch(parsed).decision == "cleared"


def test_emphasis_is_stripped_without_rewriting_the_path_itself() -> None:
    """A blanket strip of `_` ate the underscores out of `rig_workbench` — caught here."""
    parsed = plan_dispatch.parse_task_plan(
        plan(row("T1", "**`rig_workbench/orchestrate/plan_dispatch.py`**"))
    )
    assert parsed.rows[0].files == ("rig_workbench/orchestrate/plan_dispatch.py",)


# ── 3. a real dependency puts a row outside the check ────────────────────────


def test_a_dependent_task_sharing_a_file_is_not_an_overlap() -> None:
    """T2 waits on T1, so it is never dispatched beside it: one file, two ordered writers."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/auth/token.ts"), row("T2", "src/auth/token.ts", depends="T1"))
    )
    assert verdict.decision == "cleared"
    assert verdict.lanes == ("T1",)
    assert verdict.rows_read == 2


def test_only_the_parallel_safe_rows_are_intersected() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(
            row("T1", "src/a.ts"),
            row("T2", "src/b.ts"),
            row("T3", "src/a.ts, src/b.ts", depends="T1,T2"),
        )
    )
    assert verdict.decision == "cleared"
    assert verdict.lanes == ("T1", "T2")


@pytest.mark.parametrize("spelling", ["—", "-", "–", "ー", "なし", "none", ""])
def test_every_spelling_of_no_dependency_enters_the_check(spelling: str) -> None:
    """A lane dropped because its dash was the wrong dash fails open, which is the bad way."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/a.ts", depends=spelling), row("T2", "src/a.ts", depends=spelling))
    )
    assert verdict.decision == "refused"


# ── 4. no vacuous pass ───────────────────────────────────────────────────────


def test_an_empty_table_does_not_pass_vacuously() -> None:
    """The check fires on nothing-to-check.

    A header and a separator and no task rows is the shape a checker passes by accident:
    zero lanes, zero intersections, nothing to complain about. It must come back as silence
    (`nothing-to-check`, dispatch NOT allowed) and say that it read zero rows — not as a
    pass, and not as the same word a real clearance uses.
    """
    verdict = plan_dispatch.check_disjoint_dispatch(plan())
    assert verdict.decision == "nothing-to-check"
    assert verdict.dispatch_allowed is False
    assert verdict.rows_read == 0
    assert verdict.lanes == ()
    assert "0 task rows" in verdict.reason
    assert "NOTHING TO CHECK" in "\n".join(verdict.lines())
    # and it is not the same answer a real clearance gives
    cleared = plan_dispatch.check_disjoint_dispatch(plan(row("T1", "a.py"), row("T2", "b.py")))
    assert cleared.decision != verdict.decision


def test_text_with_no_task_table_is_not_a_pass() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch("概要: 何かする\n\nただの散文。表は無い。\n")
    assert verdict.decision == "nothing-to-check"
    assert verdict.dispatch_allowed is False
    assert "no task table was read at all" in verdict.reason


def test_a_table_whose_columns_are_missing_is_not_read_as_a_plan() -> None:
    """Losing the 触るファイル column must not silently become 'no overlaps found'."""
    text = (
        "### タスク\n| # | 目的 | 手順 | 検証 |\n|---|---|---|---|\n"
        "| T1 | ある目的 | 手順 | `pytest` |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "nothing-to-check"
    assert verdict.rows_read == 0


def test_rows_are_still_read_when_the_number_column_has_no_header() -> None:
    """`#` is decoration; `T1` in the first cell is the numbering the contract asks for."""
    text = (
        "|  | 目的 | 触るファイル | 依存 |\n|---|---|---|---|\n"
        "| T1 | ある目的 | src/a.ts | — |\n| T2 | ある目的 | src/a.ts | — |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "refused"
    assert verdict.lanes == ("T1", "T2")


def test_a_fully_serial_plan_is_not_cleared_for_parallel_dispatch() -> None:
    """No row marked `—` means nothing was cleared, whatever else the plan says.

    The two rows point at each other, which is not a plan anyone would write — but once
    every dependency has to name a row of the same table, a plan with no parallel-safe row
    at all can only be circular. Ordering is not this check's business; not clearing it is.
    """
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/a.ts", depends="T2"), row("T2", "src/b.ts", depends="T1"))
    )
    assert verdict.decision == "nothing-to-check"
    assert verdict.rows_read == 2
    assert "none is marked parallel-safe" in verdict.reason


def test_a_lane_that_declares_no_files_is_refused_not_cleared() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/a.ts"), row("T2", "—"))
    )
    assert verdict.decision == "refused"
    assert "T2" in verdict.reason


def test_a_prose_file_column_is_refused_rather_than_compared_as_text() -> None:
    """`src/a.ts と src/b.ts` compared by equality would clear an overlap it cannot see."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/a.ts と src/b.ts"), row("T2", "src/b.ts"))
    )
    assert verdict.decision == "refused"
    assert "触るファイル" in verdict.reason


# ── the shipped contract is the corpus the parser is written against ─────────


def test_the_shipped_contracts_own_example_table_parses_and_clears() -> None:
    """The format this parser reads is the format the contract ships.

    If `facets/output-contracts/task-plan.md` changes its table shape, this fails here
    rather than the first time a real plan is dispatched.
    """
    parsed = plan_dispatch.parse_task_plan(SHIPPED_CONTRACT.read_text(encoding="utf-8"))
    assert [r.task_id for r in parsed.rows] == ["T1", "T2", "T3"]
    assert parsed.rows[0].files == ("src/auth/token.ts",)
    assert parsed.rows[0].parallel_safe is True
    assert parsed.rows[2].parallel_safe is False
    verdict = plan_dispatch.check_disjoint_dispatch(parsed)
    assert verdict.decision == "cleared"
    assert verdict.lanes == ("T1",)


# ── the tree's own plan table is the corpus ──────────────────────────────────


def test_the_design_briefs_real_table_parses_the_way_a_human_reads_it() -> None:
    """Cells copied from nobody: this is the brief's §11 table, read as it is written.

    Two separate bugs died here. `・` (U+30FB) is the separator that table uses, and a
    reader that did not split on it collapsed a whole lane's file set into one token that
    matched nothing — T0's two files became one. And a global markdown strip reached inside
    `skills/engine/recipes/*.md` and left `skills/engine/recipes/.md`, un-globbing T3. Both
    assertions below are equalities on the parsed file sets, so either bug reappearing
    fails here rather than in a dispatch.
    """
    rows = {r.task_id: r for r in plan_dispatch.parse_task_plan(brief_table()).rows}
    assert list(rows) == [f"T{n}" for n in range(13)]
    assert rows["T0"].files == ("hooks/inject-talk-mode.sh", "skills/engine/SKILL.md")
    assert rows["T3"].files[0] == "skills/engine/recipes/*.md"
    assert rows["T6"].files == (
        "skills/engine/recipes/max-bugfix.md", "skills/engine/recipes/bugfix.md",
    )
    assert rows["T9"].files == ("commands/rig.md", "skills/engine/SKILL.md")
    # The dependency column, read as the brief's own prose reads it: T0-T3 are the P1 lanes,
    # and T6/T9 are the rows the brief says it placed serially *because* they collide.
    assert [r.task_id for r in plan_dispatch.parse_task_plan(brief_table()).parallel_lanes] == [
        "T0", "T1", "T2", "T3",
    ]
    assert rows["T6"].depends_text == "T3"
    assert rows["T9"].depends_text == "T0"


def test_the_design_briefs_real_table_is_refused_on_its_own_annotated_cells() -> None:
    """The honest verdict on the corpus, and it is not `cleared`.

    T1, T2 and T3 write an annotation into the 触るファイル column glued to the path
    (`tests/x.py（新設）`, `rig_workbench/orchestrate/`（parser と dispatch 前検査）). Those
    are contract violations in the brief, not in the reader, and the reader's only honest
    answer is to refuse the plan and name the cells. Cleared would be a lie; reading the
    annotated token as a path would be a worse one, because a pseudo-path matches nothing
    and so reports a clean intersection.
    """
    verdict = plan_dispatch.check_disjoint_dispatch(brief_table())
    assert verdict.decision == "refused"
    assert verdict.dispatch_allowed is False
    for task_id in ("T1", "T2", "T3"):
        assert task_id in verdict.reason
    assert "（新設）" in verdict.reason


def test_the_briefs_colliding_pairs_are_caught_once_both_rows_are_parallel_safe() -> None:
    """T0/T9 share SKILL.md and T3/T6 share max-bugfix.md — the brief serialised both.

    In the table as written those pairs are outside the check by design: T9 depends on T0
    and T6 on T3, and a dependent row is never dispatched beside the row it waits on (the
    brief says so in as many words — 「T0 と T9 は `SKILL.md` を共有するので、直列に置く」).
    So the plan is right and the checker must not refuse it for those pairs. What this test
    pins is the other half: had either pair been marked `—`, the two bugs above would have
    cleared them, and now they do not — the first by equality across the `・` separator, the
    second by matching `recipes/*.md` against `recipes/max-bugfix.md`.
    """
    marked = brief_table().replace(
        "`pytest tests/test_capability_registry_vs_surfaces.py -q`（30 枚の凍結を 29 に下げる） | T0 |",
        "`pytest tests/test_capability_registry_vs_surfaces.py -q`（30 枚の凍結を 29 に下げる） | — |",
    ).replace(
        "`wb route --type bugfix --json` が同じ recipe を返す | T3 |",
        "`wb route --type bugfix --json` が同じ recipe を返す | — |",
    )
    rows = {r.task_id: r for r in plan_dispatch.parse_task_plan(marked).rows}
    assert rows["T9"].parallel_safe and rows["T6"].parallel_safe, "fixture failed to re-mark"

    shared_skill = plan_dispatch.check_disjoint_dispatch(
        plan_dispatch.TaskPlan(rows=(rows["T0"], rows["T9"]))
    )
    assert shared_skill.decision == "refused"
    assert [(o.left, o.right) for o in shared_skill.overlaps] == [("T0", "T9")]
    assert shared_skill.overlaps[0].files == ("skills/engine/SKILL.md",)

    # T3's second cell is `tests/…py（新設）`, an annotation glued to a path — the brief's own
    # contract violation, which refuses the row before any pair is compared. It is dropped
    # here so the pair the reviewer asked about can be compared at all; the glob cell, which
    # is what this half is about, is untouched.
    recipes = dataclasses.replace(rows["T3"], unreadable=())
    shared_recipe = plan_dispatch.check_disjoint_dispatch(
        plan_dispatch.TaskPlan(rows=(recipes, rows["T6"]))
    )
    assert shared_recipe.decision == "refused"
    assert shared_recipe.overlaps[0].files == (
        "skills/engine/recipes/*.md ⊇ skills/engine/recipes/max-bugfix.md",
        "skills/engine/recipes/*.md ⊇ skills/engine/recipes/bugfix.md",
    )


# ── fail-closed on every shape the reader cannot judge ───────────────────────


def test_a_decoy_header_does_not_shadow_the_real_file_column() -> None:
    """`File notes` matches the substring `file`; 触るファイル matches exactly, and wins."""
    text = (
        "| # | File notes | 触るファイル | 依存 |\n|---|---|---|---|\n"
        "| T1 | なにか | src/x.py | — |\n| T2 | なにか | src/x.py | — |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "refused"
    assert verdict.overlaps[0].files == ("src/x.py",)


def test_two_columns_that_could_both_be_the_file_column_are_refused_not_picked() -> None:
    text = (
        "| # | File notes | Files touched by me | 依存 |\n|---|---|---|---|\n"
        "| T1 | src/x.py | src/x.py | — |\n| T2 | src/y.py | src/x.py | — |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "refused"
    assert "more than once" in verdict.reason


def test_a_control_character_never_reaches_the_printed_verdict() -> None:
    """An ANSI escape in a path would rewrite the terminal the REFUSED banner is on."""
    crafted = "src/a.py\x1b[2K\x1b[1A"
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", crafted), row("T2", "src/b.py"))
    )
    printed = "\n".join(verdict.lines())
    assert verdict.decision == "refused"
    assert "\x1b" not in printed and "\x1b" not in verdict.reason
    assert "\ufffd" in verdict.reason


def test_the_contracts_own_word_for_parallel_safe_is_not_a_dependency() -> None:
    """`並列可` written in the 依存 column used to drop the lane out of the check."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T2", "a.py", depends="並列可"))
    )
    assert verdict.decision == "refused"
    assert "並列可" in verdict.reason


def test_an_annotated_dash_is_still_a_dash() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py", depends="—（並列可）"), row("T2", "a.py", depends="—"))
    )
    assert verdict.decision == "refused"
    assert verdict.lanes == ("T1", "T2")


def test_a_dependency_on_a_row_that_does_not_exist_is_refused() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T2", "b.py", depends="T7"))
    )
    assert verdict.decision == "refused"
    assert "T7" in verdict.reason


def test_a_brace_expansion_is_unreadable_rather_than_two_half_paths() -> None:
    """`{cli,orchestrate}` splits on its own comma into two halves that match nothing."""
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "rig_workbench/{cli,orchestrate}/x.py"), row("T2", "rig_workbench/cli/x.py"))
    )
    assert verdict.decision == "refused"
    assert "rig_workbench/{cli" in verdict.reason


def test_a_range_dependency_names_every_id_between_its_ends() -> None:
    """`T1–T3` with no `T2` row is a dependency on a task nobody wrote.

    Recording only the two ends made the middle of a range invisible: the row was taken as
    ordered work, left out of the check, and the plan cleared on the lanes that remained.
    """
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T3", "b.py", depends="T1"), row("T4", "c.py", depends="T1–T3"))
    )
    assert verdict.decision == "refused"
    assert "T2" in verdict.reason


def test_a_reversed_range_is_refused_rather_than_guessed_at() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T2", "b.py", depends="T3–T1"), row("T3", "c.py", depends="T1"))
    )
    assert verdict.decision == "refused"
    assert "T3–T1" in verdict.reason


def test_a_row_that_is_not_a_numbered_task_is_never_dropped_in_silence() -> None:
    """`| 2 | src/a.py | — |` used to vanish, and the verdict was `cleared, rows_read=1`."""
    text = (
        "| # | 触るファイル | 依存 |\n|---|---|---|\n"
        "| T1 | src/a.py | — |\n| 2 | src/a.py | — |\n| T2b | src/a.py | — |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "refused"
    assert verdict.dispatch_allowed is False
    assert "2 | src/a.py" in verdict.reason and "T2b" in verdict.reason


def test_an_empty_row_is_padding_and_not_a_dropped_task() -> None:
    text = (
        "| # | 触るファイル | 依存 |\n|---|---|---|\n"
        "| T1 | src/a.py | — |\n|  |  |  |\n| T2 | src/b.py | — |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "cleared"
    assert verdict.rows_read == 2


def test_a_duplicated_task_id_is_refused_rather_than_intersected_with_itself() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "a.py"), row("T1", "b.py"))
    )
    assert verdict.decision == "refused"
    assert "T1" in verdict.reason and verdict.overlaps == ()


def test_a_directory_without_a_trailing_slash_still_covers_what_is_under_it() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "src/auth"), row("T2", "src/auth/token.ts"))
    )
    assert verdict.decision == "refused"
    assert verdict.overlaps[0].files == ("src/auth ⊇ src/auth/token.ts",)


def test_a_glob_covers_what_it_matches() -> None:
    verdict = plan_dispatch.check_disjoint_dispatch(
        plan(row("T1", "skills/engine/recipes/*.md"), row("T2", "skills/engine/recipes/bugfix.md"))
    )
    assert verdict.decision == "refused"


# ── shapes that must keep working ────────────────────────────────────────────


def test_the_columns_may_be_reordered() -> None:
    text = (
        "| 依存 | 触るファイル | # | 検証 |\n|---|---|---|---|\n"
        "| — | src/x.py | T1 | pytest |\n| — | src/y.py | T2 | pytest |\n"
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.decision == "cleared"
    assert verdict.lanes == ("T1", "T2")


def test_rows_are_read_from_every_task_table_in_the_document() -> None:
    """Two tables, one plan: a lane in the second must be compared with one in the first."""
    text = plan(row("T1", "src/x.py")) + "\n\n### さらに\n" + HEADER + row("T2", "src/x.py")
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.rows_read == 2
    assert verdict.decision == "refused"
    assert [(o.left, o.right) for o in verdict.overlaps] == [("T1", "T2")]


def test_a_non_task_table_beside_the_plan_is_not_read_as_rows() -> None:
    text = "| 項目 | 値 |\n|---|---|\n| タスク数 | 2 |\n\n" + plan(
        row("T1", "src/x.py"), row("T2", "src/y.py")
    )
    verdict = plan_dispatch.check_disjoint_dispatch(text)
    assert verdict.rows_read == 2
    assert verdict.decision == "cleared"
