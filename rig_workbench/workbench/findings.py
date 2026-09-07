"""Parse a review written to `output-contracts/review-findings` into its findings.

The drill scores reviewers by checking whether a planted defect was discussed near
where it lives. It used to do that over the *whole document*, and the whole document
is the wrong unit. A review that names every changed function and says each one is
fine puts the answer key's vocabulary next to the answer key's symbols without
claiming anything, and scored full marks on all four shipped cases. Measured, not
supposed.

The contract already carries the structure that fixes this. Every finding is a
numbered heading under `## Blocking` or `## Non-blocking`, with a fixed `Severity`
and a `file:line` anchor. Scoring inside a finding block and nowhere else makes the
narration disappear: prose that is not a finding is not scored, because the reviewer
did not claim anything. It also hands over `severity` and `blocking` for free, which
the scorer previously left absent because it had no structured place to read them.

Reviewers emit two shapes in practice and both are accepted. The contract's own:

    ## Blocking

    ### 1. Some title

    - Severity: High
    - File: `workflow.ts:24`
    - Impact: ...

and the one real personas drift to, with the fields folded into the heading:

    ## Blocking

    **B1. Some title（Severity: High）** — `workflow.ts:24`
    body text ...

A document that parses to no findings is not silently a zero — `parse_findings`
returns an empty list and the caller records that it could not read the review,
because "the reviewer found nothing" and "the scorer could not read it" are
different failures and only one of them is the reviewer's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEVERITIES = ("critical", "high", "medium", "low")

# `## Blocking` / `## Non-blocking` and their Japanese and slashed variants. A
# heading that merely starts with one of them counts: personas write
# `## Non-blocking / 残債`.
_BLOCKING_HEAD_RE = re.compile(r"(?im)^#{2,3}\s*(blocking|ブロッキング|マージ前必須)\b")
_NONBLOCKING_HEAD_RE = re.compile(
    r"(?im)^#{2,3}\s*(non[- ]?blocking|フォローアップ|残債|non[- ]?blocking\s*/)"
)
# Any `##`/`###` heading, so a section ends where the next one begins.
_ANY_HEAD_RE = re.compile(r"(?m)^(#{2,4})\s*(.+?)\s*$")

# A finding heading. The contract's own shape is `### 1. title`, but a reviewer that
# writes `### CRITICAL: title` under `## Blocking` has plainly made a finding, and a
# grammar that only accepts numbers would read it as narration and score it zero.
# Any `###`/`####` heading counts except the furniture below.
_NUMBERED_HEAD_RE = re.compile(r"(?m)^#{3,4}\s+(?!#)(.+?)\s*$")
# Headings that organise a review rather than assert something. A finding under one
# of these is still a finding; the heading itself is not.
_FURNITURE_RE = re.compile(
    r"(?i)^\s*(blocking|non[- ]?blocking|findings?|summary|総合判定|判定|条件|残債|"
    r"根拠|確信度|note|notes|備考|所見なし|マージ前必須|フォローアップ)\b"
)
# A finding heading, drifted shape: `**B1. title（Severity: High）** — path:line`.
_BOLD_HEAD_RE = re.compile(r"(?m)^\*\*\s*(?:[A-Z]{0,2}\d+)\s*[.、)]\s*(.+?)\*\*\s*(.*)$")

_SEVERITY_RE = re.compile(r"(?i)severity\s*[:：]?\s*\**\s*(critical|high|medium|low)\b")


@dataclass
class Finding:
    """One finding: the block of text a reviewer wrote under one numbered heading."""

    title: str
    body: str                       # heading line included, so anchors in it are in scope
    blocking: bool | None           # None when the review has no Blocking/Non-blocking split
    severity: str | None            # lowercased, one of SEVERITIES
    offset: int                     # start of `body` in the source document
    shape: str = "numbered"         # which grammar matched, for telemetry
    tags: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        return self.offset + len(self.body)


def _section_spans(text: str) -> list[tuple[int, int, bool | None]]:
    """(start, end, blocking) for each `## …` section, blocking=None when unclassified."""
    heads = [(m.start(), m.end(), m.group(0)) for m in _ANY_HEAD_RE.finditer(text)]
    if not heads:
        return [(0, len(text), None)]
    spans: list[tuple[int, int, bool | None]] = []
    if heads[0][0] > 0:
        spans.append((0, heads[0][0], None))
    for index, (start, _stop, line) in enumerate(heads):
        end = heads[index + 1][0] if index + 1 < len(heads) else len(text)
        if _BLOCKING_HEAD_RE.match(line):
            blocking: bool | None = True
        elif _NONBLOCKING_HEAD_RE.match(line):
            blocking = False
        else:
            # A `###` under a section inherits it; a new `##` resets to unclassified.
            blocking = spans[-1][2] if spans and line.lstrip("#").strip() and line.startswith("###") else None
        spans.append((start, end, blocking))
    return spans


def _blocking_at(spans: list[tuple[int, int, bool | None]], offset: int) -> bool | None:
    """The nearest enclosing section's blocking flag, searching outwards."""
    enclosing = [span for span in spans if span[0] <= offset < span[1]]
    for start, _end, blocking in reversed(enclosing):
        if blocking is not None:
            return blocking
    # `### 1.` sits inside its own span; walk back to the last `##` that classified.
    for start, _end, blocking in reversed(spans):
        if start <= offset and blocking is not None:
            return blocking
    return None


def parse_findings(text: str) -> list[Finding]:
    """Every finding in the review, in document order. Empty when nothing parses."""
    spans = _section_spans(text)
    heads: list[tuple[int, int, str, str, str]] = []
    for match in _NUMBERED_HEAD_RE.finditer(text):
        if _FURNITURE_RE.match(match.group(1)):
            continue
        heads.append((match.start(), match.end(), match.group(1), "numbered", ""))
    for match in _BOLD_HEAD_RE.finditer(text):
        # A numbered heading already covering this offset wins; they do not overlap
        # in practice, but a document mixing both should not double-count.
        if any(start <= match.start() < end for start, end, *_ in heads):
            continue
        heads.append((match.start(), match.end(), match.group(1), "bold", match.group(2)))
    heads.sort()

    findings: list[Finding] = []
    for index, (start, _head_end, title, shape, trailer) in enumerate(heads):
        end = heads[index + 1][0] if index + 1 < len(heads) else len(text)
        # A finding never runs past the next section heading.
        next_section = min(
            (s for s, _e, _f in spans if s > start),
            default=end,
        )
        end = min(end, next_section) if next_section > start else end
        body = text[start:end]
        severity_match = _SEVERITY_RE.search(body)
        findings.append(Finding(
            title=title.strip(),
            body=body,
            blocking=_blocking_at(spans, start),
            severity=severity_match.group(1).lower() if severity_match else None,
            offset=start,
            shape=shape,
        ))
    return findings
