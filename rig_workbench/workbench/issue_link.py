"""The issue a run is against, as a declaration rather than a guess (#548, slice 3).

Nothing in rig knows which issue a run is for. The queue is the closest thing and it only
knows the other direction: `queue_set_status(..., task_id=...)` writes the link onto the
*item*, and for the `gh` backend — the one this issue is actually about — even that is
dropped, because the gh branch of that function relabels, comments and closes the issue and
never records the task anywhere. So a run has no way to say what it is against, and a board
grouping runs by issue has nothing to group on.

The reference is recorded because somebody stated it, never because it was found in the
task's text. A task that says "like we discussed in #12" is not a task against #12, and a
board that treats every mention as an assignment would be reporting an inference under a
column header that reads like a fact. This is the same split `caller` keeps between what an
operator declared and what rig inferred, and the reason is the same.

The accepted forms are `#123` and `owner/repo#123`. Anything else is refused rather than
stored: this value is rendered as an identity and joined on, so a string nobody can resolve
is worse than no value at all.
"""

from __future__ import annotations

import re

#: `#123`, or `owner/repo#123` when the run is against an issue in another repository.
#: Bounded on every segment — an unbounded id or owner would let a hand-edited task record
#: put an arbitrarily long string into a board cell.
_REF = re.compile(
    r"^(?:(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}))?"
    r"#(?P<number>[1-9][0-9]{0,9})$"
)

FORMS = "#123 or owner/repo#123"


class IssueRefError(ValueError):
    """The declared issue reference is not one this can resolve."""


def parse(value: str) -> str:
    """The normalised reference, or raise.

    Normalisation is only whitespace: the two forms are kept as written, because `#12` and
    `owner/repo#12` say different things and expanding the short form would require guessing
    the repository from the directory the command happened to run in.
    """
    if not isinstance(value, str):
        raise IssueRefError(f"issue reference must be text ({FORMS})")
    candidate = value.strip()
    if not candidate:
        raise IssueRefError(f"issue reference is empty ({FORMS})")
    if not _REF.fullmatch(candidate):
        # A pasted URL is the likely mistake and worth naming, but it is not accepted:
        # deriving `owner/repo#n` from a URL means deciding which hosts map to that shape,
        # and a wrong mapping produces a reference that resolves to somebody else's issue.
        hint = " (paste the reference, not the URL)" if "://" in candidate else ""
        raise IssueRefError(f"issue reference must be {FORMS}{hint}: {candidate[:80]}")
    return candidate


def declared(value: str | None) -> dict | None:
    """The block recorded on a task, or `None` when nothing was declared.

    `None` rather than a block with an empty ref: absent means nobody said, and a present key
    holding nothing would make "no issue" and "an issue we failed to record" the same row on
    a board. The same rule `perf` and `run_id` already follow.
    """
    if value is None:
        return None
    return {"ref": parse(value), "source": "flag", "declared": True}
