"""Pre-dispatch disjointness check for a `task-plan` table.

`facets/output-contracts/task-plan.md` has required a 触るファイル (files touched) column
since the contract was written, and marks a task that may run alongside the others with the
dependency `—`. Until this module, nothing in the repository read either column: the plan
said which lanes were parallel-safe and which files each lane would touch, and the dispatch
that acted on it was a person holding both facts in their head. That held until it did not —
two lanes were assigned by hand onto file sets believed disjoint, and one lane's
`git stash`/`reset` swept the other's edits out of the shared tree.

What this module does
---------------------

`parse_task_plan` reads the contract's table into rows, and `check_disjoint_dispatch` takes
the rows whose dependency column says `—`, intersects their declared file sets pairwise, and
returns a verdict. A non-empty intersection is a **refusal**.

Refuse, rather than force an isolated worktree
----------------------------------------------

`isolate.setup_isolation` is right there and could be forced on instead, and this module
deliberately does not do that — it returns the facts (which lanes, which files) so a caller
that wants isolation can choose it, but the verdict it hands back is a refusal. Three
reasons, in the order they mattered:

1. A worktree per lane does not make overlapping lanes safe, it moves the collision. Two
   lanes editing one file in two worktrees merge back into one branch, and the second
   ff-merge either conflicts or silently wins. The lost edit arrives later and further from
   the cause, which is the worse failure of the two.
2. An overlap is a defect in the *plan*, not in the dispatch. The plan declared two tasks
   parallel-safe that are not. Forcing isolation would carry on dispatching from a plan
   known to be wrong and hide the wrongness behind machinery; refusing hands it back to the
   author, whose fix is one edit — split the file set, or declare the dependency that was
   always there.
3. Refusing is cheap and reversible, and forcing is neither: isolation creates branches and
   directories, and a check that silently changed the execution model of a dispatch nobody
   asked to change would be a surprise found by archaeology.

Every unreadable shape fails closed
-----------------------------------

A reader of prose has exactly one dangerous failure: reading something it does not
understand as something harmless. Every such shape here is a refusal, not a pass:

* an entry that is not written the way a path is written — prose (「a.py と b.py」), an
  annotation glued on (`tests/x.py（新設）`), a control character, half a backticked cell;
* a lane that declares no files at all;
* a dependency cell that is neither `—` nor task ids that exist in the table (`並列可`
  written where a dependency goes used to drop the lane out of the check entirely);
* two rows numbered the same, an ambiguous header (two columns that could both be the file
  column), or a table so large the reader capped it.

And where there is nothing to read — an empty table, no table, or no row marked
parallel-safe — the answer is `nothing-to-check`, which is not `cleared` either. A
checker's silence and a checker's approval must never be spelled the same way; that is the
whole failure mode this file exists to not reproduce.

The precision of the answer is the precision of the plan
--------------------------------------------------------

This is a prose path, and it is bounded by the prose it reads. **A task that touches a file
it did not list passes straight through this check.** Nothing here opens the repository,
runs the task, or observes what it writes; the only evidence is the table, and the table is
written by whoever wrote the plan. So:

* a lane that lists `src/auth/token.ts` and also edits `src/auth/handler.ts` is cleared
  against a lane that lists `handler.ts`, and the two will still collide;
* a lane that names a file by a different spelling than the other lane uses (an alias, a
  symlink, `..`, a path assembled at run time, a Unicode confusable, a different case on a
  case-insensitive filesystem) is not recognised as the same file. Text is compared as
  text, NFC-normalised and nothing more;
* two globs are never compared with each other: `src/*/a.py` and `src/foo/*.py` describe
  overlapping ground, and this check clears them. Matching pattern against pattern is a
  different problem from matching pattern against path, and guessing at it would be the
  one thing worse than the miss;
* what *is* compared beyond equality: a directory covers what is under it (`src/auth/`, and
  `src/auth` without the slash), and a glob covers what it matches (`recipes/*.md` covers
  `recipes/bugfix.md`) — by `fnmatch` on the two strings, never by expansion against a
  filesystem this module does not touch.

Judgement layer, stdlib only
----------------------------

`orchestrate` is a migrated pillar (`tests/test_layering_contract.py`), so this module
imports the standard library and nothing else — not even a port. It performs no I/O, reads
no clock and runs no process: text in, verdict out. The caller does the dispatching, the
printing and the exiting.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import unicodedata

__all__ = [
    "PlanRow",
    "TaskPlan",
    "Overlap",
    "DispatchVerdict",
    "parse_task_plan",
    "check_disjoint_dispatch",
]

#: Dependency cells that mean "no dependency — this task may run alongside the others".
#: The contract writes `—` (U+2014); the rest are the spellings a writer reaches for when
#: the em dash is inconvenient, and treating them as a dependency would quietly drop a lane
#: out of the check, which is the direction that fails open.
_NO_DEPENDENCY = frozenset({
    "", "-", "--", "---", "—", "–", "―", "ー", "─", "なし", "無し", "none", "n/a", "na",
})

#: File cells that declare nothing. A lane that declares nothing is not cleared: it is
#: refused, because "unknown" and "empty" are the same text and only one of them is safe.
_NO_FILES = frozenset({
    "", "-", "--", "—", "–", "―", "ー", "─", "なし", "無し", "none", "n/a", "na",
    "未定", "未確定", "tbd", "?", "？",
})

#: Headers this module accepts **exactly**, after emphasis, annotation and case are taken
#: off. Exact beats substring, and the reason is a decoy: in a table headed
#: `| # | File notes | 触るファイル | 依存 |`, first-substring-wins picked "File notes" as
#: the file column and cleared two lanes that both declared one file. Two columns matching
#: at the same precedence is not resolved by picking one — it is refused.
_HEADER_EXACT: dict[str, frozenset[str]] = {
    "id": frozenset({"#", "no", "no.", "id", "task", "タスク", "番号"}),
    "files": frozenset({"触るファイル", "触るfile", "files", "file", "files touched", "paths"}),
    "depends": frozenset({"依存", "依存関係", "depends", "depends on", "dependency",
                          "dependencies", "needs"}),
}

#: The substring fallback, used only when no header matched exactly. Same rule: two loose
#: matches for one column refuse rather than choose.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("#", "no", "task", "タスク"),
    "files": ("触るファイル", "触る file", "files", "file"),
    "depends": ("依存", "depends", "dependency", "dependencies", "needs"),
}

_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")
_TASK_ID = re.compile(r"^T\d+$", re.IGNORECASE)
#: A dependency range, as the tree's own plan table writes it: `T1–T3`, meaning every id
#: between the two ends. Written with any of the dashes a writer reaches for.
_TASK_RANGE = re.compile(r"^(T\d+)\s*[-–—~〜]\s*(T\d+)$", re.IGNORECASE)
#: Entry separators. `・` (U+30FB) is in the list because the tree's own plan table —
#: `docs/v3-architecture-design-brief.ja.md` §11 — separates its paths with it, and a
#: separator the corpus uses that the reader does not know collapses a lane's whole file set
#: into one token that matches nothing. That is a fail-open, so the list is widened from the
#: real corpus rather than from taste.
_FILE_SPLIT = re.compile(r"<br\s*/?>|[,;、；・･\n]")
#: A dependency cell's own separators: the same list, plus whitespace.
_DEPENDS_SPLIT = re.compile(r"<br\s*/?>|[,;、；・･\s]+")
#: Markdown emphasis, stripped **at the edges of a token only**. A global strip reached
#: inside a path and rewrote it — `skills/engine/recipes/*.md` became `.../recipes/.md`,
#: silently un-globbing a lane. Emphasis wraps a token; it never sits mid-path.
_EMPHASIS_EDGE = re.compile(r"^[*`\s]+|[*`\s]+$")
#: C0/C1 control characters, including the ESC that starts an ANSI escape sequence. They are
#: replaced (not dropped) with a character no path may contain, so a crafted entry both
#: fails the path shape below *and* cannot rewrite the terminal of whoever prints the
#: refusal. A verdict that could erase its own REFUSED banner is not a refusal.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_CONTROL_MARK = "�"
#: What an entry must look like to be compared as a path at all: ASCII path characters and
#: the glob metacharacters this module actually matches with, nothing else. Everything
#: failing this is `unreadable`, and unreadable is refused — never kept as a pseudo-path
#: that matches nothing. Braces are deliberately absent: `rig_workbench/{cli,orchestrate}/x.py`
#: splits on its own comma into two halves that would each look like a path and match
#: nothing — the `・` bug again, in a different alphabet. No plan in the tree uses brace
#: expansion, so the entry is refused rather than expanded.
_PATH_SHAPE = re.compile(r"^[A-Za-z0-9._/~@+*?\-\[\]]+$")
#: Entries carrying one of these are matched with fnmatch as well as by equality.
_GLOB_CHARS = "*?["
#: Bounds. `_PARENTHETICAL` is quadratic in a cell of open brackets and the pairwise
#: intersection is quadratic in lanes and in entries, so each is capped. Hitting a cap is
#: not a silent truncation: it marks the row or the plan unreadable, and the check refuses.
_MAX_CELL_CHARS = 2000
_MAX_ENTRIES_PER_ROW = 100
_MAX_ROWS = 200
#: How many ids one `T<n>–T<m>` range may name. A range wider than the row cap cannot be
#: describing this table, so it is refused rather than expanded.
_MAX_RANGE = _MAX_ROWS

_PARENTHETICAL = re.compile(r"[（(][^（()）]*[）)]")


@dataclasses.dataclass(frozen=True)
class PlanRow:
    """One data row of the plan's task table, as written.

    `files` is what the row declared, normalised but never invented: an entry the row did
    not carry is not here, and that is the bound the module docstring states.
    """

    task_id: str
    files: tuple[str, ...]
    depends_on: str
    unreadable: tuple[str, ...] = ()
    line_no: int = 0

    @property
    def depends_text(self) -> str:
        """The dependency cell with any annotation taken off: `—（並列可）` reads as `—`."""
        return _PARENTHETICAL.sub("", _normalise(self.depends_on)).strip()

    @property
    def parallel_safe(self) -> bool:
        """True when the dependency column says this row may run alongside the others.

        The annotation is dropped before the comparison on purpose: a lane whose dash
        carries a note would otherwise fall out of the check and be dispatched unexamined,
        and falling out of the check is the only error here that fails open.
        """
        return self.depends_text.lower() in _NO_DEPENDENCY

    @property
    def declares_files(self) -> bool:
        return bool(self.files or self.unreadable)

    def depends_ids(self) -> tuple[str, ...] | None:
        """The task ids this row waits on, or None when the cell is not a dependency at all.

        None is the answer for `並列可` — the contract's own word for "parallel-safe",
        written where a dependency goes. It is neither a dash nor an id, and reading it as
        an unknown dependency silently took the lane out of the check. It is now a refusal.

        A range is expanded to **every** id between its ends, not just the two ends: with
        only the ends recorded, `T1–T3` in a table with no `T2` row was a dependency on a
        task nobody wrote, and the row it belonged to went on being treated as ordered work
        that this check never looks at. A reversed or absurdly wide range (`T3–T1`,
        `T1–T9999`) is not a range this reader will guess at, and is refused.
        """
        if self.parallel_safe:
            return ()
        ids: list[str] = []
        for token in _DEPENDS_SPLIT.split(self.depends_text):
            piece = token.strip()
            if not piece or piece.lower() in _NO_DEPENDENCY:
                continue
            if _TASK_ID.match(piece):
                ids.append(piece.upper())
                continue
            span = _TASK_RANGE.match(piece)
            if not span:
                return None
            first, last = (int(span.group(n)[1:]) for n in (1, 2))
            if last < first or last - first > _MAX_RANGE:
                return None
            ids += [f"T{number}" for number in range(first, last + 1)]
        return tuple(ids)


@dataclasses.dataclass(frozen=True)
class TaskPlan:
    """Every row read out of one plan, plus what the reader could not read cleanly."""

    rows: tuple[PlanRow, ...] = ()
    tables_seen: int = 0
    ambiguous_columns: tuple[str, ...] = ()
    truncated: bool = False
    #: Rows of a task table that carried content but did not parse as a numbered task —
    #: `| 2 | … |`, `| T2b | … |`, a note typed into the grid. Kept because dropping them
    #: silently is the same fail-open as every other finding here: the row may have been a
    #: lane, and a verdict computed without it was `cleared` over a file set never read.
    dropped: tuple[str, ...] = ()

    @property
    def parallel_lanes(self) -> tuple[PlanRow, ...]:
        return tuple(row for row in self.rows if row.parallel_safe)


@dataclasses.dataclass(frozen=True)
class Overlap:
    """Two parallel-safe lanes that declared at least one file in common."""

    left: str
    right: str
    files: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.left} ∩ {self.right}: {', '.join(self.files)}"


@dataclasses.dataclass(frozen=True)
class DispatchVerdict:
    """The answer, and enough of the working to act on it without re-deriving anything.

    `decision` is one of `cleared`, `refused` and `nothing-to-check`, and only `cleared`
    permits the dispatch. `nothing-to-check` is deliberately not a pass: it is what an empty
    table, an unparsed table, or a plan with no parallel-safe row produces, and a dispatcher
    that treated it as approval would be approving on the strength of having read nothing.
    """

    decision: str
    reason: str
    lanes: tuple[str, ...] = ()
    overlaps: tuple[Overlap, ...] = ()
    rows_read: int = 0

    @property
    def dispatch_allowed(self) -> bool:
        return self.decision == "cleared"

    def lines(self) -> list[str]:
        """The verdict as a caller would print it; the caller owns the Presenter.

        An unknown decision renders as itself rather than raising: this text is what a
        refusal is *seen* through, and a formatter that can crash is one more way for a
        refusal not to arrive.
        """
        head = {
            "cleared": "pre-dispatch disjointness: CLEARED",
            "refused": "pre-dispatch disjointness: REFUSED",
            "nothing-to-check": "pre-dispatch disjointness: NOTHING TO CHECK",
        }.get(self.decision, f"pre-dispatch disjointness: {self.decision.upper()}")
        out = [f"{head} — {self.reason}"]
        out += [f"  overlap: {item.describe()}" for item in self.overlaps]
        return out


def _normalise(cell: str) -> str:
    """One table cell or entry, as text the rest of this module may compare.

    Three things happen, and each is deliberately narrow:

    * control characters are replaced with U+FFFD. Dropping them would let
      `src/a.py\\x1b[2K…` become a plausible path; keeping them would let it reach the
      terminal the refusal is printed on and rewrite the banner.
    * Unicode is NFC-normalised, so a decomposed spelling of a path compares equal to the
      composed one. (Confusables are a different problem and are not solved here — the
      module docstring says so.)
    * `*`, backticks and whitespace come off **the ends only**, and `_` only as a matched
      pair around the whole cell. Every character of a path is legal somewhere in a path —
      `_` is most of `rig_workbench`, `*` is the whole of a glob — so a strip that reached
      inside would rewrite the very paths this check compares. Both forms were live bugs.
    """
    text = unicodedata.normalize("NFC", _CONTROL.sub(_CONTROL_MARK, cell))
    text = _EMPHASIS_EDGE.sub("", text)
    while len(text) > 2 and text.startswith("_") and text.endswith("_"):
        text = _EMPHASIS_EDGE.sub("", text[1:-1])
    return text


def _header_key(cell: str) -> tuple[str, bool]:
    """Which column this header cell is, and whether it said so exactly.

    The cell is capped before the annotation regex runs: that pattern is quadratic on a
    cell of unmatched brackets, and a header is a header, not a document.
    """
    text = _PARENTHETICAL.sub("", _normalise(cell[:_MAX_CELL_CHARS])).strip().lower()
    if not text:
        return "", False
    for key, exact in _HEADER_EXACT.items():
        if text in exact:
            return key, True
    for key in ("files", "depends", "id"):  # files/depends first: `#` must not shadow them
        for alias in _HEADER_ALIASES[key]:
            if alias in text:
                return key, False
    return "", False


def _columns(header: list[str]) -> tuple[dict[str, int], tuple[str, ...]]:
    """Where each column sits, and which columns were too ambiguous to place.

    An exact header match wins over every substring match, so a decoy column cannot shadow
    the real one. Two candidates at the same precedence are not resolved by taking the
    first: the column is reported ambiguous and the plan is refused, because picking one of
    two possible file columns is how a checker invents its own answer.
    """
    exact: dict[str, list[int]] = {}
    loose: dict[str, list[int]] = {}
    for position, cell in enumerate(header):
        key, is_exact = _header_key(cell)
        if not key:
            continue
        (exact if is_exact else loose).setdefault(key, []).append(position)
    columns: dict[str, int] = {}
    ambiguous: list[str] = []
    for key in ("id", "files", "depends"):
        hits = exact.get(key) or loose.get(key) or []
        if len(hits) > 1:
            ambiguous.append(key)
        elif hits:
            columns[key] = hits[0]
    return columns, tuple(ambiguous)


def _split_row(line: str) -> list[str]:
    """The cells of a markdown table row.

    The leading and trailing pipes are decoration, not cells, so they are dropped before
    splitting rather than producing two empty columns that shift every index by one.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.match(cell.strip()) for cell in cells)


def _normalise_path(entry: str) -> str:
    """One file entry, as a path the check can compare.

    Backslashes become slashes and a leading `./` goes, because two rows naming the same
    file those two ways are naming the same file. Nothing else is resolved: this module
    does not touch the filesystem, so it cannot follow a symlink, collapse a `..` or expand
    a glob against real directories, and does not pretend to.
    """
    text = _normalise(entry).strip().strip('"').strip("'").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _looks_like_path(entry: str) -> bool:
    """Whether an entry can be compared as a path at all.

    The test is positive, not a blacklist: an entry is a path only if every character of it
    is one a path is written with. Prose (「src/a.ts と src/b.ts」), an annotation glued to
    a path (`tests/x.py（新設）`), an arrow, a stray backtick from a half-read cell, a
    control character — all fail it, and all are refused rather than kept as a pseudo-path.
    A pseudo-path is the worse outcome: it compares unequal to everything and so reports a
    clean intersection, which is a false negative dressed as an answer.
    """
    return bool(_PATH_SHAPE.match(entry))


def _read_files(cell: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The declared paths of one 触るファイル cell, and the entries that are not paths."""
    if len(cell) > _MAX_CELL_CHARS:
        return (), (f"<a 触るファイル cell longer than {_MAX_CELL_CHARS} characters>",)
    if _normalise(cell).lower() in _NO_FILES:
        return (), ()
    files: list[str] = []
    unreadable: list[str] = []
    for raw in _FILE_SPLIT.split(cell):
        entry = _normalise_path(raw)
        if not entry or entry.lower() in _NO_FILES:
            continue
        (files if _looks_like_path(entry) else unreadable).append(entry)
        if len(files) + len(unreadable) >= _MAX_ENTRIES_PER_ROW:
            unreadable.append(f"<more than {_MAX_ENTRIES_PER_ROW} entries in one row>")
            break
    return tuple(dict.fromkeys(files)), tuple(dict.fromkeys(unreadable))


def parse_task_plan(text: str) -> TaskPlan:
    """Read every `task-plan` task table in `text`.

    A table qualifies only if its header names both a 触るファイル column and a 依存 column,
    so the summary tables a plan may also carry are skipped rather than misread. Column
    position is taken from that header, not assumed, because a plan that reorders its
    columns is still a valid plan — and a header that names one column twice is reported
    ambiguous rather than guessed at.

    Rows whose id cell is not `T<n>` are skipped: the contract numbers every task, and a
    row that is not numbered is not a task (it is a continuation line, or a note). That is
    a hole, and it is the reason `TaskPlan.rows` is reported alongside every verdict — a
    plan whose rows all vanished shows up as zero rows read, never as a clean pass.
    """
    rows: list[PlanRow] = []
    dropped: list[str] = []
    ambiguous: list[str] = []
    truncated = False
    tables = 0
    lines = (text or "").splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("|"):
            index += 1
            continue
        columns, unclear = _columns(_split_row(lines[index]))
        ambiguous += [key for key in unclear if key not in ambiguous]
        body = index + 1
        if body < len(lines) and _is_separator(_split_row(lines[body])):
            body += 1
        usable = "files" in columns and "depends" in columns
        if usable:
            tables += 1
        while body < len(lines) and lines[body].strip().startswith("|"):
            cells = _split_row(lines[body])
            if usable and not _is_separator(cells):
                row = _read_row(cells, columns, body + 1)
                if row is None:
                    # A row of empty cells is markdown padding, not a lost task. Anything
                    # else that failed to parse is reported, never dropped in silence.
                    if any(_normalise(cell) for cell in cells):
                        dropped.append(f"line {body + 1}: {_row_label(cells)}")
                elif len(rows) >= _MAX_ROWS:
                    truncated = True
                else:
                    rows.append(row)
            body += 1
        index = body
    return TaskPlan(
        rows=tuple(rows),
        tables_seen=tables,
        ambiguous_columns=tuple(ambiguous),
        truncated=truncated,
        dropped=tuple(dropped),
    )


def _row_label(cells: list[str]) -> str:
    """A row named the way its author would recognise it, short and inert.

    Built through `_normalise`, so a control character in the cell cannot ride out on the
    refusal this row is about to cause.
    """
    text = " | ".join(_normalise(cell) for cell in cells).strip()
    return f"{text[:60]}…" if len(text) > 60 else text


def _read_row(cells: list[str], columns: dict[str, int], line_no: int) -> PlanRow | None:
    """One data row, or None when the row is not a numbered task."""
    def cell(key: str) -> str:
        # The id column falls back to the first cell: the contract heads it `#`, and a plan
        # that numbers its rows `T1` without that header is still numbering its rows. The
        # other two columns have no fallback — guessing which column held the file set is
        # how a checker invents an answer.
        position = columns.get(key, 0 if key == "id" else None)
        if position is None or position >= len(cells):
            return ""
        return cells[position]

    task_id = _normalise(cell("id"))
    if not _TASK_ID.match(task_id):
        return None
    files, unreadable = _read_files(cell("files"))
    return PlanRow(
        task_id=task_id.upper(),
        files=files,
        depends_on=cell("depends"),
        unreadable=unreadable,
        line_no=line_no,
    )


def _contains(directory: str, path: str) -> bool:
    """Whether `directory` names a directory that `path` sits under."""
    stem = directory.rstrip("/")
    return bool(stem) and path.startswith(f"{stem}/")


def _globs(pattern: str, path: str) -> bool:
    """Whether `pattern` is a glob matching `path`, by the two strings alone."""
    return any(char in pattern for char in _GLOB_CHARS) and fnmatch.fnmatchcase(path, pattern)


def _covers(one: str, other: str) -> bool:
    """Whether two declared entries name overlapping ground.

    Three ways, and all three are text against text:

    * equality;
    * containment — `src/auth/` covers `src/auth/token.ts`, and so does `src/auth` written
      without the slash. The contract asks for the slash; the slash-less form is honoured
      anyway, because a plan that omitted it has still named the directory and reading it
      as an unrelated file would clear a real overlap;
    * a glob, by `fnmatch` — `skills/engine/recipes/*.md` covers `.../recipes/bugfix.md`.
      Nothing is expanded against the filesystem, which this module never touches, and `*`
      crosses `/` here, so the match is wider than a shell's. Wider means more refusals,
      which is the side to be wrong on.
    """
    if one == other:
        return True
    if _contains(one, other) or _contains(other, one):
        return True
    return _globs(one, other) or _globs(other, one)


def _describes(one: str, other: str) -> bool:
    """Whether `one` is the wider of two entries that cover each other."""
    return _contains(one, other) or _globs(one, other)


def _shared(left: PlanRow, right: PlanRow) -> tuple[str, ...]:
    hits = [
        one if one == other else f"{one} ⊇ {other}" if _describes(one, other) else f"{other} ⊇ {one}"
        for one in left.files
        for other in right.files
        if _covers(one, other)
    ]
    return tuple(dict.fromkeys(hits))


def _refuse(reason: str, plan: TaskPlan, lanes: tuple[str, ...] = ()) -> DispatchVerdict:
    return DispatchVerdict(
        decision="refused", reason=reason, lanes=lanes, rows_read=len(plan.rows),
    )


def _unreadable_plan(parsed: TaskPlan) -> str | None:
    """Why this plan cannot be judged at all, or None when it can be."""
    if parsed.ambiguous_columns:
        return (
            f"the table's header names the {', '.join(parsed.ambiguous_columns)} column more "
            "than once, and picking one of two possible columns is how a checker invents an "
            "answer. Name each column once."
        )
    if parsed.dropped:
        return (
            f"{len(parsed.dropped)} row(s) of the task table did not parse as a numbered "
            f"task and were not read: {'; '.join(parsed.dropped)}. Each is a lane this check "
            "did not see; number every row `T<n>` or take it out of the table."
        )
    if parsed.truncated:
        return (
            f"the table has more than {_MAX_ROWS} task rows and was capped, so some lanes "
            "were never compared. Split the plan."
        )
    seen: dict[str, int] = {}
    for row in parsed.rows:
        seen[row.task_id] = seen.get(row.task_id, 0) + 1
    twice = sorted(task_id for task_id, count in seen.items() if count > 1)
    if twice:
        return (
            f"task id(s) {', '.join(twice)} are used by more than one row. Two rows with one "
            "id cannot be told apart — neither by this check nor by the dependency column "
            "that points at them."
        )
    known = set(seen)
    for row in parsed.rows:
        ids = row.depends_ids()
        if ids is None:
            return (
                f"the 依存 cell of {row.task_id} is {row.depends_text!r}, which this reader "
                "cannot read as `—` or as task ids of this table (a reversed or over-wide "
                "range such as `T3–T1` counts). A cell it cannot read takes the row out of "
                "the check without saying so; write `—` if it is parallel-safe, or the ids "
                "it waits on."
            )
        unknown = sorted(set(ids) - known)
        if unknown:
            return (
                f"{row.task_id} declares a dependency on {', '.join(unknown)}, which no row "
                "in this table defines. A dangling dependency may mean the row is actually "
                "parallel-safe, and this check will not guess which."
            )
    return None


def check_disjoint_dispatch(plan: TaskPlan | str) -> DispatchVerdict:
    """Refuse a parallel dispatch whose parallel-safe lanes do not have disjoint file sets.

    Rows carrying a real dependency are outside the check entirely: they are not dispatched
    alongside anything, so sharing a file with the task they wait on is ordinary and is not
    a collision. Only the rows whose dependency column says `—` are intersected — which is
    why a dependency cell that says neither `—` nor an id is refused rather than read as
    "some dependency": that reading is how a lane leaves the check unnoticed.

    The precision of this answer is the precision of the plan — a task that touches a file
    it did not list passes through, and the module docstring lists the rest of that bound.
    What the check will not do is call a plan it could not read a pass.
    """
    parsed = parse_task_plan(plan) if isinstance(plan, str) else plan
    rows_read = len(parsed.rows)
    unreadable = _unreadable_plan(parsed)
    if unreadable is not None:
        return _refuse(unreadable, parsed)

    lanes = parsed.parallel_lanes
    if not lanes:
        if rows_read == 0 and parsed.tables_seen == 0:
            reason = (
                "no task table was read at all (no header naming both 触るファイル and 依存), "
                "so nothing was proven disjoint. An unread plan is not an approved plan."
            )
        elif rows_read == 0:
            reason = (
                f"{parsed.tables_seen} task table(s) read and 0 task rows in them, so nothing "
                "was proven disjoint. An empty table is not a cleared table."
            )
        else:
            reason = (
                f"{rows_read} row(s) read and none is marked parallel-safe (dependency `—`); "
                "nothing here was cleared for parallel dispatch."
            )
        return DispatchVerdict(
            decision="nothing-to-check", reason=reason, rows_read=rows_read,
        )

    names = tuple(lane.task_id for lane in lanes)
    silent = [lane.task_id for lane in lanes if not lane.declares_files]
    if silent:
        return _refuse(
            f"parallel-safe lane(s) {', '.join(silent)} declare no files touched. A lane "
            "whose file set is unknown cannot be shown to be disjoint from any other; fill "
            "the 触るファイル column or give the task a dependency.",
            parsed, names,
        )

    prose = [lane.task_id for lane in lanes if lane.unreadable]
    if prose:
        detail = "; ".join(
            f"{lane.task_id}: {', '.join(lane.unreadable)}" for lane in lanes if lane.unreadable
        )
        return _refuse(
            f"the 触るファイル column of {', '.join(prose)} is not a separated list of paths "
            f"({detail}). Compared as written it would clear an overlap it cannot see, so it "
            "is refused instead.",
            parsed, names,
        )

    overlaps = tuple(
        Overlap(left=lanes[i].task_id, right=lanes[j].task_id, files=shared)
        for i in range(len(lanes))
        for j in range(i + 1, len(lanes))
        if (shared := _shared(lanes[i], lanes[j]))
    )
    if overlaps:
        return DispatchVerdict(
            decision="refused",
            reason=(
                f"{len(overlaps)} pair(s) of parallel-safe lanes declare the same file. "
                "Dispatching them together puts two writers on one file; split the file "
                "sets, or declare the dependency between them and dispatch in order."
            ),
            lanes=names,
            overlaps=overlaps,
            rows_read=rows_read,
        )
    return DispatchVerdict(
        decision="cleared",
        reason=(
            f"{len(lanes)} parallel-safe lane(s) ({', '.join(names)}) declare disjoint file "
            f"sets, out of {rows_read} row(s) read. Bounded by the plan: a task that touches "
            "a file it did not list is not visible here."
        ),
        lanes=names,
        rows_read=rows_read,
    )
