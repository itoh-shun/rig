"""Release notes for `gh release create`, cut to fit GitHub's body limit (#624).

`.github/workflows/release.yml` used to `awk` the `## [<version>]` section out of
CHANGELOG.md and hand the whole thing to `--notes-file`. That worked until the
3.0.0 entry reached 129,059 bytes and the API refused it:

    HTTP 422: Validation Failed
    body is too long (maximum is 125000 characters)

so the tag was created (`gh release create` creates it first) and the release was
not. The entry is the record and is not the thing to shorten; the notes are a
*rendering* of it, and a rendering may be cut as long as the reader can tell it
was cut and can reach the rest. That is what this module does.

Two decisions worth keeping written down:

**The budget is counted in bytes.** GitHub's message says "characters" and does
not say which encoding it counts in. For any UTF-8 text the byte length is
greater than or equal to the length in code points *and* to the length in UTF-16
code units, so a byte budget is under the limit on every reading of it. It costs
an over-cut on CJK-heavy notes (3 bytes per character) and never an under-cut,
and an under-cut is the failure that loses a release.

**The cut lands on a block boundary.** A body sliced at byte N lands mid-sentence,
mid-table or mid-fence about as often as not, and a release page that ends in the
middle of a table row reads as broken rather than as shortened. Blocks here are
blank-line separated, with fenced code held whole, so the notes always end on a
finished paragraph, list, table or fence. A trailing heading is dropped too: a
`###` with nothing under it reads as a section that lost its body.
"""

import re

#: The API's maximum release body. Measured from the 422 the v3.0.0 run got.
GITHUB_BODY_LIMIT = 125_000

#: The newline the notes file ends with. It is declared here, and counted here,
#: because the budget has to bound *the file* `--notes-file` reads. Budgeting the
#: string and then writing one byte more is how a cut that reported success still
#: handed GitHub 125,001 bytes at the boundary.
TRAILING = "\n"

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _size(text: str) -> int:
    """The budget unit: UTF-8 bytes (see the module docstring for why)."""
    return len(text.encode("utf-8"))


def extract_section(changelog: str, version: str) -> str | None:
    """The body of `## [<version>]`, or None when the CHANGELOG has no such section.

    Deliberately the same reading as the `awk` this replaces: everything after the
    heading line up to the next `## [`, with the surrounding blank lines trimmed.
    The heading is not included — `gh release create --title` carries the version.
    """
    heading = re.compile(r"^## \[" + re.escape(version) + r"\]")
    collected: list[str] = []
    inside = False
    for line in changelog.splitlines():
        if inside and line.startswith("## ["):
            break
        if inside:
            collected.append(line)
            continue
        if heading.match(line):
            inside = True
    if not inside:
        return None
    return "\n".join(collected).strip("\n")


def split_blocks(body: str) -> list[str]:
    """Markdown blocks, in order, such that "\\n".join(blocks) rebuilds `body`.

    A block is a run of non-blank lines plus the blank lines that follow it. A
    fenced code block is one block however many blank lines it contains, because
    a cut inside a fence leaves the fence open and the rest of the page renders as
    code.
    """
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        marker = _FENCE.match(line)
        if fence is None and marker:
            fence = marker.group(1)[:3]
            current.append(line)
            continue
        if fence is not None:
            current.append(line)
            if marker and marker.group(1).startswith(fence):
                fence = None
            continue
        if line.strip():
            # A non-blank line after a blank one starts the next block.
            if current and not current[-1].strip():
                blocks.append("\n".join(current))
                current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_heading(block: str) -> bool:
    return block.lstrip().startswith("#")


def _open_fence(lines: list[str]) -> str | None:
    """The marker that would close a fence these lines leave open, or None.

    One reading of "a fence is held whole", used by every place that has to decide
    whether a cut left one hanging. A hanging fence does not merely lose the code —
    it renders everything after it, the pointer to the full entry included, as code.
    """
    fence: str | None = None
    for line in lines:
        marker = _FENCE.match(line)
        if marker is None:
            continue
        if fence is None:
            fence = marker.group(1)
        elif marker.group(1).startswith(fence[:3]):
            fence = None
    return fence


def _hard_cut(block: str, budget: int) -> str:
    """Last resort: one block on its own is over budget.

    Whole lines first, then a word boundary, and either way a fence the cut leaves
    open is closed. Unreachable at the real 125,000-byte limit for anything this
    CHANGELOG has ever held — the word-boundary path needs a single line of 124KB —
    but reachable, and tested, under a smaller `--limit`, and "fences are held whole"
    is a rule this module states rather than a rule it holds where convenient.
    """
    lines = block.split("\n")
    # Room for a closing fence, reserved *before* the lines are counted, because adding
    # it afterwards is what leaves a fence open once the budget is spent. Taken for any
    # fence marker in the block, not only one on the first line: `intro\n```console\n…`
    # needs the same closing line, and reserving only for the first-line case overshot
    # the budget by exactly the length of the fence it then appended anyway.
    markers = [m.group(1) for m in (_FENCE.match(line) for line in lines) if m]
    reserve = 1 + max((len(marker) for marker in markers), default=0) if markers else 0
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = _size(line) + (1 if kept else 0)
        if used + cost > budget - reserve:
            break
        kept.append(line)
        used += cost
    if not kept:
        # Not one whole line fits. Cut at a word, inside the same reserve, so that the
        # close below is paid for rather than added on top of a spent budget.
        room = max(budget - reserve, 0)
        text = block
        while text and _size(text) > room:
            head, _, _ = text.rpartition(" ")
            text = head if head else text[: len(text) - 1]
        kept = text.rstrip().split("\n")
    closing = _open_fence(kept)
    if closing is not None:
        kept.append(closing)
    return "\n".join(kept).rstrip()


def _footer(version: str, repo: str, tag: str, chars: int, size: int) -> str:
    """The pointer that makes a cut body readable as shortened rather than as broken.

    It names both counts. A reader who sees only "128,266 characters" against a
    "125,000-character limit" can do the subtraction; a reader given only the
    character count of a CJK entry sees a number *under* the limit next to a claim
    that it was over, and has no way to tell which half is wrong.
    """
    both = f"{chars:,} characters" + (f" ({size:,} bytes in UTF-8)" if size != chars else "")
    return (
        "\n\n---\n\n"
        f"**These notes were shortened.** The full {version} entry is {both}, and a GitHub "
        f"release body holds at most {GITHUB_BODY_LIMIT:,}. What is above is the first part "
        "of it, cut at a section boundary. Read the entry in full in "
        f"[CHANGELOG.md at {tag}](https://github.com/{repo}/blob/{tag}/CHANGELOG.md)."
    )


def shorten(body: str, footer: str, limit: int = GITHUB_BODY_LIMIT) -> str:
    """`body` cut to `limit` bytes including `footer`, on a block boundary."""
    budget = limit - _size(footer)
    if budget <= 0:  # pragma: no cover - only a limit smaller than the pointer itself
        raise ValueError(f"limit {limit} leaves no room for the {_size(footer)}-byte footer")
    blocks = split_blocks(body)
    kept: list[str] = []
    used = 0
    for block in blocks:
        cost = _size(block) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(block)
        used += cost
    fitted = len(kept)
    while kept and (not kept[-1].strip() or _is_heading(kept[-1])):
        kept.pop()
    if kept:
        return "\n".join(kept).rstrip() + footer

    # Everything that fit was a heading. Hard-cutting `blocks[0]` here would publish
    # that heading with nothing under it — the exact thing the pop above exists to
    # prevent — so the budget goes to the first block that has a body instead, cut at
    # a line if it does not fit whole. A mid-paragraph cut says something; a lone
    # `###` says nothing, and the footer explains either.
    prefix = blocks[:fitted]
    spent = _size("\n".join(prefix)) + (1 if prefix else 0)
    tail = _hard_cut(blocks[fitted], budget - spent) if (
        fitted < len(blocks) and budget - spent > 0) else ""
    if not tail:
        # Only when the budget is too tight for any body at all.
        return _hard_cut(blocks[0], budget) + footer
    return "\n".join([*prefix, tail]).rstrip() + footer


def notes_for(body: str, version: str, repo: str, tag: str,
              limit: int = GITHUB_BODY_LIMIT) -> tuple[str, bool]:
    """(file text, was_shortened) for an already-extracted section body.

    What comes back is exactly what `--notes-file` will hold, `TRAILING` included,
    and `limit` bounds that — so the caller writes this text and adds nothing.
    """
    room = limit - _size(TRAILING)
    if _size(body) <= room:
        return body + TRAILING, False
    footer = _footer(version, repo, tag, len(body), _size(body))
    return shorten(body, footer, room) + TRAILING, True


def build_notes(changelog: str, version: str, repo: str, tag: str,
                limit: int = GITHUB_BODY_LIMIT) -> tuple[str, dict[str, str]]:
    """The notes for `version`, and what the workflow should report about them.

    The status dict is what `release.yml` appends to `$GITHUB_OUTPUT`: `source`
    (`changelog` or `auto` — a missing section is not an error here, the create step
    falls back to `--generate-notes`, and rig_workbench/validation/release.py is what
    fails a version with no entry), `shortened`, and the two character counts the
    warning quotes.

    No file is read or written here on purpose: this module holds the arithmetic of
    the cut, and `scripts/release_notes.py` is the process that has the paths.
    """
    body = extract_section(changelog, version)
    if body is None or not body.strip():
        return "", {"source": "auto", "shortened": "false", "entry_chars": "0",
                    "entry_bytes": "0", "notes_chars": "0", "notes_bytes": "0"}
    notes, shortened = notes_for(body, version, repo, tag, limit)
    # Both units, because the two disagree and only one of them is the budget. A
    # CJK-heavy entry is 77,038 characters and 230,638 bytes; reporting only the
    # first produces "77038 characters, over the 125000 limit", which is false on
    # its face and teaches the reader to distrust the rest of the line.
    return notes, {"source": "changelog", "shortened": "true" if shortened else "false",
                   "entry_chars": str(len(body)), "entry_bytes": str(_size(body)),
                   "notes_chars": str(len(notes)), "notes_bytes": str(_size(notes))}
