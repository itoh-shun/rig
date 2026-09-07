"""fixture corpus for /rig:drill — materialize pre-built cases and score reviews.

The standard drill corpus (`facets/instructions/drill.md`) is a table of seed
*classes* that a subagent synthesizes into a fresh diff each run. This module
backs the other corpus shape: `skills/engine/corpora/fixture/`, where the diff is
already written — every case ships a `base/` tree (committed) and a `head/` tree
(uncommitted working-tree change), and the answer key ships with it.

Two jobs, both deterministic so the drill never has to eyeball a score:

  materialize   base/ committed into a throwaway git repo, head/ laid over it as
                uncommitted changes — i.e. exactly the shape "review the current
                changes" expects. The real repo is never touched.
  score         a review text vs. the answer key, **one finding at a time**. A
                planted defect counts as detected only when a single finding both
                says where it is (the answer key's symbol, or a `file:line` anchor
                on a line the defect owns) and discusses the defect class — and
                points at no other planted defect, because a finding that claims
                two has not said which one it found.

                The finding is the unit because scoring the document was measured
                to be unusable. A paragraph naming every changed function and
                calling each one correct scored 5/5 on `py-mixed-violations`, 5/5
                on `ts-mixed-violations` and 4/5 on `ts-behavioral-correctness`:
                every concept word present, beside the right symbol, asserting
                nothing. `output-contracts/review-findings` already requires the
                structure that fixes it, and drill fixes reviewers to that
                contract; the scorer simply was not reading it. Prose outside a
                finding is now invisible, and a review that parses to no findings
                is reported as `unparsed` rather than as a silent zero.

                `location_hit` / `concept_hit` are still reported separately,
                because "named the symbol but never said what was wrong with it"
                is a different failure from "never looked at it".

On the **clean** case (zero planted defects) the direction inverts: *claiming* a
finding is a false positive, and plain "looks fine" prose is not. That case is
what measures precision — a reviewer that cannot stay quiet when there is
nothing to find is as broken as one that misses defects. "Claiming" is the load-
bearing word: a conclusion that names the same vocabulary in the negative ("no
security issues", "重大な問題はありません") is the reviewer doing exactly what
this case rewards, so the keyword check is negation-aware per clause.

`severity_accuracy` and `blocking_accuracy` are computed now, and they are read
rather than judged: the contract fixes `Severity` to four values and splits
`## Blocking` from `## Non-blocking`, so scoring per finding gives the scorer a
structured place to read both. They were absent before only because scoring the
whole document had nowhere to read them from.

What the scorer cannot decide on its own is the **direction** of a claim. `concept`
is a list of topic words, so "avoids the N+1 query" and "has an N+1 query" match
identically. That is the drill judge of ③-b, and it arrives here as the `adjudicate`
callable rather than as an import — this module stays pure, offline and
provider-free, and `adjudication.py` is where a model gets called. The judge only
ever narrows: it is asked about pairs already credited here, and only `ASSERTS`
keeps the credit. A pair it could not answer becomes `unadjudicated`, and a row that
did not adjudicate everything carries `adjudicated: false` and must not be turned
into a rate.

What this scorer still deliberately does NOT compute: `explanation_quality` (a
different question for the same judge, not yet implemented) and false positives on
the *violation* cases (separating an invented finding from a real bug the reviewer
happened to spot is a judgement call; the clean case measures the same thing under
control). Those fields are left absent rather than filled with a number nobody
measured.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any, Callable

from .anchors import Anchor, extract_anchors
from .findings import Finding, parse_findings

# Bumped on every change to a scoring rule, and written onto each drill row.
# Without it a rate from one scorer is silently comparable to a rate from another,
# and `--replay` compares two different rulers. Rows written before this existed
# carry no tag and are not comparable to anything.
#
#   1  location regex x concept regex within PROXIMITY_WINDOW, over the whole document
#   2  `file:line` anchors accepted as location evidence
#   3  scored per finding (`output-contracts/review-findings`) instead of per document;
#      one finding claims at most one defect; severity and blocking measured;
#      anchor paths resolved against the materialized workspace
#   4  the direction of each credited claim adjudicated by the drill judge (③-b);
#      a row that did not adjudicate every candidate pair says so and yields no rate
SCORER_VERSION = 4

# No longer used by anything. Detection scores one finding at a time, so the window
# is a boundary the reviewer drew rather than a character count, and the clean-case
# prose test works clause by clause. Kept as the record of what the rule used to be,
# because a drill row tagged `scorer_version` 1 or 2 was produced with it.
PROXIMITY_WINDOW = 600

# On a clean case, blocking language in *prose* is the false positive. This only
# runs on reviews that filed no finding at all — filing one is the false positive by
# itself, whatever words it uses. A suggestion phrased as optional ("might be worth
# …") deliberately does not match, and `clean_false_positive` decides whether each
# hit is asserted or denied.
#
# A `severity:\s*(critical|high)` branch used to sit here and could never match:
# `_CLAUSE_SPLIT_RE` splits on `:`, so `- Severity: High` is already two clauses by
# the time this pattern sees it. Removed rather than repaired — a review that writes
# a `Severity` line has filed a finding, and the branch above it catches that.
CLEAN_FP_RE = re.compile(
    r"(?i)(critical|high severity|must fix|blocking|"
    r"security (issue|risk|vulnerab)|bugs?\b|defect|重大|要修正|ブロッ)"
)

# Each keyword hit is judged inside its own clause, so "no security issues, but
# there is a critical bug" still flags on the second half. The cue is directional
# because the two languages negate from opposite sides: English puts it before
# the word ("no bugs", "not blocking"), Japanese after it (重大な問題は*ありません*).
_CLAUSE_SPLIT_RE = re.compile(
    r"[.!?;:,\n。、]+|\b(?:but|however|though|although)\b", re.IGNORECASE
)
_NEG_BEFORE_RE = re.compile(r"(?i)\b(?:no|not|never|none|nothing|without|nor|free of)\b|n't\b")
_NEG_AFTER_RE = re.compile(r"ありません|ございません|ません|無い|無し|ない|なし|見当たら|皆無")


def _asserted(text: str, pattern: re.Pattern[str]) -> bool:
    """Does `pattern` match somewhere the surrounding clause does not negate?

    Used by `clean_false_positive` and nowhere else. It was briefly applied to the
    concept match on violation cases too, and removed after measurement: see
    `score_violation`, which records what that cost. Detection does not read
    negation.

    Directional because the two languages negate from opposite sides: English
    before the word ("no security issues"), Japanese after it (重大な問題は
    *ありません*). Clause-local, so a negation reaching across a boundary is not
    seen.
    """
    for clause in _CLAUSE_SPLIT_RE.split(text):
        for match in pattern.finditer(clause):
            if _NEG_BEFORE_RE.search(clause[: match.start()]):
                continue
            if _NEG_AFTER_RE.search(clause[match.end():]):
                continue
            return True
    return False


def clean_false_positive(text: str) -> bool:
    """True when a review of the clean case actually *claims* a finding.

    A hit that its own clause negates is not a false positive: "No security
    issues." and "重大な問題はありません" are correct conclusions on a clean diff,
    and scoring them as findings would penalise the one behaviour this case
    measures. Limit worth knowing: the cue is clause-local and directional, so a
    negation that reaches across a clause boundary is not seen.
    """
    return _asserted(text, CLEAN_FP_RE)


# drill derives expected blocking from expected severity (facets/instructions/drill.md ①).
_BLOCKING_BY_SEVERITY = {
    "critical": "Blocking",
    "high": "Blocking",
    "medium": "Non-blocking",
    "low": "Non-blocking",
}


def corpus_root() -> pathlib.Path:
    """Directory of the shipped fixture corpus (RIG_HOME wins when it is set)."""
    rel = pathlib.Path("skills") / "engine" / "corpora" / "fixture"
    env = os.environ.get("RIG_HOME")
    if env and (pathlib.Path(env) / rel).is_dir():
        return (pathlib.Path(env) / rel).resolve()
    return (pathlib.Path(__file__).resolve().parents[2] / rel).resolve()


#: The keys on a persona's score that only mean anything once the judge has run and
#: answered here. Renamed with an `_unadjudicated` suffix otherwise — see
#: `build_drill_row`. `clean_fp_rate` is absent on purpose: the clean case plants
#: nothing, so no verdict is involved.
_MEASURED_KEYS = ("detected", "seeded", "missed", "missed_detail",
                  "severity_accuracy", "blocking_accuracy")


def corpus_digest(cases: list[dict[str, Any]]) -> str:
    """sha256 over the answer keys actually scored against, in case-id order.

    `corpus_root` honours `RIG_HOME`, which is a feature — and it means a run can be
    scored against a substituted corpus whose seeds say whatever the substituter likes.
    An attacker did exactly that: a fake `ts-mixed-violations` whose summaries were
    tautologies, judged honestly by a real provider, 5/5 with severity 1.0, and a row
    indistinguishable field-for-field from a real one. This does not prevent it, which
    would break a legitimate feature. It makes it visible.

    What stays outside it: the `base/` and `head/` trees themselves. A corpus can be
    substituted with one whose answer key matches and whose code does not.
    """
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda c: c["id"]):
        digest.update(f"{case['id']}\x00{bool(case.get('clean'))}".encode("utf-8"))
        # Sorted by id: reordering the seeds in a case file changes nothing about the
        # answer key, and a digest that moved on it would cry wolf.
        for violation in sorted(case.get("violations") or [], key=lambda v: str(v.get("id"))):
            for field in ("id", "summary", "concept", "location", "severity"):
                digest.update(str(violation.get(field, "")).encode("utf-8"))
            # `perspectives` decides which persona is accountable for this seed, i.e.
            # the *denominator*. Leaving it out left the shortest route to a higher rate
            # outside the digest: narrow a perspective to the easy seeds and the row
            # still names the shipped corpus, byte for byte.
            digest.update("\x00".join(sorted(violation.get("perspectives") or [])).encode("utf-8"))
    return digest.hexdigest()[:16]


def load_corpus_meta(root: pathlib.Path | None = None) -> dict[str, Any]:
    """`corpus.json` (corpus id + corpus_version); {} when the corpus is absent."""
    path = (root or corpus_root()) / "corpus.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(
    selected: list[str] | None = None,
    root: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Answer keys of the corpus, sorted by case id. `_dir` carries base/ and head/."""
    base = root or corpus_root()
    cases_dir = base / "cases"
    if not cases_dir.is_dir():
        return []
    cases: list[dict[str, Any]] = []
    for case_dir in sorted(cases_dir.iterdir()):
        meta_path = case_dir / "case.json"
        if not case_dir.is_dir() or not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["_dir"] = case_dir
        if not selected or selected == ["all"] or meta["id"] in selected:
            cases.append(meta)
    return cases


def expected_blocking(severity: str) -> str | None:
    """Blocking / Non-blocking, derived from severity the way drill ① derives it."""
    return _BLOCKING_BY_SEVERITY.get(str(severity).lower())


def _changed_head_ranges(base_lines: list[str], head_lines: list[str]) -> list[tuple[int, int]]:
    """1-based head line ranges that differ from base. No context, no widening."""
    ranges: list[tuple[int, int]] = []
    matcher = difflib.SequenceMatcher(a=base_lines, b=head_lines, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if j2 > j1:                       # replace / insert: real head lines
            ranges.append((j1 + 1, j2))
        elif j1 < len(head_lines):        # delete: the head line the removal sits before
            ranges.append((j1 + 1, j1 + 1))
    return ranges


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Overlapping or touching spans collapsed, so a line is tested once."""
    out: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _defect_line_ranges(
    case: dict[str, Any], violation: dict[str, Any]
) -> dict[str, list[tuple[int, int]]]:
    """Where in `head/` this planted defect lives, as {relative path: [(start, end)]}.

    Derived, never declared: the answer keys carry no line numbers, so editing a case
    does not mean renumbering it.

    A defect owns its `location` symbol line plus the changed hunks that **contain or
    touch** it — a symptom is usually the line under the declaration the answer key
    names. Touching means exactly that, with no line in between; there is no window to
    widen. An earlier draft widened each hunk by three lines and was measured to hand
    one defect's lines to the defect next to it: two seeds thirteen lines apart shared
    a range, and an anchor in the overlap scored both. `drill.md` names that failure —
    credit for a concept mentioned in some other paragraph — as the thing the location
    test exists to prevent.

    When `base/` has no counterpart file the whole of `head/` differs, which would make
    every line in it the defect's line. Fall back to the symbol line alone.
    """
    case_dir = case.get("_dir")
    if not case_dir:
        return {}
    head_root = pathlib.Path(case_dir) / "head"
    base_root = pathlib.Path(case_dir) / "base"
    if not head_root.is_dir():
        return {}
    location = re.compile(violation["location"])
    out: dict[str, list[tuple[int, int]]] = {}
    for head_file in sorted(head_root.rglob("*")):
        if head_file.is_symlink() or not head_file.is_file():
            continue
        try:
            head_lines = head_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        symbol_lines = [n for n, line in enumerate(head_lines, 1) if location.search(line)]
        if not symbol_lines:
            continue
        rel = head_file.relative_to(head_root).as_posix()
        base_file = base_root / rel
        if not base_file.is_file():
            out[rel] = _merge([(n, n) for n in symbol_lines])
            continue
        try:
            base_lines = base_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            out[rel] = _merge([(n, n) for n in symbol_lines])
            continue
        hunks = _changed_head_ranges(base_lines, head_lines)
        spans: list[tuple[int, int]] = []
        for n in symbol_lines:
            touching = [(h1, h2) for h1, h2 in hunks if h1 - 1 <= n <= h2 + 1]
            spans.extend((min(h1, n), max(h2, n)) for h1, h2 in touching)
            if not touching:
                spans.append((n, n))
        out[rel] = _merge(spans)
    return out


def _relative_anchor_path(anchor: Anchor, workspace: str | None) -> str:
    """The anchor's path as the case sees it.

    `/rig:drill` materializes a case into a throwaway directory, so a reviewer
    naturally writes `/tmp/drill-x9/repo/inventory.ts:6` and means `inventory.ts`.
    Measured on a real review: absolute paths scored 0/5 where the same review with
    the workspace prefix removed scored 5/5 — the anchor path went silent for every
    finding at once.

    Only a prefix the caller vouches for is stripped. Going back to a suffix rule
    would reopen the hole it was closed for: every fixture file sits at the root of
    its case, so `endswith("/" + rel)` is a basename match and `other/workflow.ts`
    locates a defect planted in `workflow.ts`.
    """
    path = anchor.path
    if not workspace:
        return path
    root = workspace.rstrip("/") + "/"
    return path[len(root):] if path.startswith(root) else path


def _anchor_locates(
    anchor: Anchor,
    ranges: dict[str, list[tuple[int, int]]],
    workspace: str | None = None,
) -> bool:
    """Does this `file:line` anchor *begin* inside the defect's region?

    Two rules, both there because the looser version was measured to be gameable.

    The path is compared whole. A suffix rule was tried and removed: every fixture file
    sits at the root of its case, so `endswith("/" + rel)` degenerates into a basename
    match and `other/workflow.ts` locates a defect planted in `workflow.ts`.

    The anchor must *start* in the region, not merely overlap it. Overlap lets one
    whole-file range — `service.py:1-63` — sit on top of every defect in the file at
    once, so a review that names the file and lists the concepts scores as though it
    had found each one. Beginning at the defect is what pointing at something is.
    """
    anchor_path = _relative_anchor_path(anchor, workspace)
    for rel, spans in ranges.items():
        if anchor_path != rel:
            continue
        if any(start <= anchor.start <= end for start, end in spans):
            return True
    return False


def scoreable_findings(findings: list[Finding]) -> list[Finding]:
    """The findings that are findings under the contract, not just headings.

    `output-contracts/review-findings` fixes `Severity` to four values and requires
    it on every finding. A heading with no severity is a section title, and treating
    it as a claim is what let an independent attempt on this scorer take a narration
    paragraph, put `### ` in front of each sentence, and go from 0/5 back to 4/5 —
    the sentences said the code was correct, and the concept vocabulary in them was
    incidental. Grading is what makes a remark a finding.

    Deliberately not also requiring `File:`. The answer key's symbol has been valid
    location evidence since before anchors were read at all, and dropping it would
    turn a scorer change into a silent tightening of what counts as a review.
    """
    return [finding for finding in findings if finding.severity is not None]


def _case_ranges(case: dict[str, Any] | None) -> dict[str, dict[str, list[tuple[int, int]]]]:
    """Every planted defect's line ranges, keyed by violation id.

    Computed once for the whole case so ambiguity is judged against the same set
    whichever defect is being scored. Deriving one defect's siblings on its own left
    the test asymmetric: an anchor sitting in two defects' ranges was rejected for
    the first and accepted for the second, so a contract-compliant finding on
    `hardcoded-secret` with `File: cache.ts:8` scored nothing while the same finding
    without the anchor scored. Following the contract lost points.
    """
    if case is None:
        return {}
    return {
        violation["id"]: _defect_line_ranges(case, violation)
        for violation in (case.get("violations") or [])
    }


def _finding_claims(
    finding: Finding,
    violations: list[dict[str, Any]],
    all_ranges: dict[str, dict[str, list[tuple[int, int]]]],
    workspace: str | None,
) -> set[str]:
    """Which of these planted defects this one finding says the location of.

    `violations` is the whole case when there is one. A caller scoring a single
    violation with no corpus behind it passes just that violation, and then the only
    question is whether the finding names its symbol — there are no siblings to be
    ambiguous between.
    """
    claims: set[str] = set()
    for violation in violations:
        vid = violation.get("id")
        if re.search(violation["location"], finding.body):
            claims.add(vid)
            continue
        mine = all_ranges.get(vid) or {}
        if not mine:
            continue
        for anchor in extract_anchors(finding.body):
            if not _anchor_locates(anchor, mine, workspace):
                continue
            # A line owned by two defects does not say which one is meant.
            if any(_anchor_locates(anchor, other, workspace)
                   for other_id, other in all_ranges.items() if other_id != vid):
                continue
            claims.add(vid)
            break
    return claims


def score_violation(
    text: str,
    violation: dict[str, Any],
    case: dict[str, Any] | None = None,
    findings: list[Finding] | None = None,
    workspace: str | None = None,
    all_ranges: dict[str, dict[str, list[tuple[int, int]]]] | None = None,
) -> tuple[bool, bool, bool]:
    """Credit a planted defect only when one finding *asserts* it, where it lives.

    Three conditions, and each one is there because dropping it was measured to be
    gameable.

    **A finding, not the document.** Scoring the whole text let a paragraph naming
    every changed function and calling each one correct score 5/5 on
    `py-mixed-violations`, 5/5 on `ts-mixed-violations` and 4/5 on
    `ts-behavioral-correctness`. Every concept word present, beside the right symbol,
    asserting nothing. `output-contracts/review-findings` already carries the
    structure that separates a claim from a remark, and drill fixes reviewers to it.

    **Graded, not merely written.** Structure alone is not a claim: an independent
    attempt on this scorer took that same narration, put `### ` in front of each
    sentence, and went from 0/5 back to 4/5. `scoreable_findings` requires the
    `Severity` the contract fixes on every finding, which is what makes a remark a
    finding.

    What this still cannot do is read the *direction* of the claim. A clause-level
    negation test was written for it and removed after measurement: it cost more than
    it bought. "`reportUsage` does not await `client.send`" is the natural way to
    report a missing await and scored zero under it, while an attacker only had to
    phrase the same narration positively ("`reportUsage` awaits the send") to walk
    past. Separating "has an N+1 query" from "avoids the N+1 query" is a semantic
    call over a `concept` that is a list of topic words, and it needs the judge in
    drill ③-b. See `test_the_deterministic_layer_alone_still_credits_a_claim_of_correctness`.

    **One finding, one defect.** A finding pointing at two planted defects has not
    said which one it found. Measured: one finding listing five point anchors and a
    sentence touching all five subjects scored 5/5 with no symbol named, and the same
    sentence with the answer key's five identifiers scored 5/5 too.

    Returns (location_hit, concept_hit, detected). `concept_hit` stays document-wide,
    because "never raised this class anywhere" and "raised it but not as a finding"
    are different reports and the scoreboard separates them.
    """
    concept = re.compile(violation["concept"])
    concept_hit = bool(concept.search(text))
    if findings is None:
        findings = scoreable_findings(parse_findings(text))
    if all_ranges is None:
        all_ranges = _case_ranges(case)

    vid = violation.get("id")
    siblings = list(case.get("violations") or []) if case is not None else [violation]
    location_hit = False
    detected = False
    for finding in findings:
        claims = _finding_claims(finding, siblings, all_ranges, workspace)
        if vid not in claims or len(claims) != 1:
            continue
        location_hit = True
        if concept.search(finding.body):
            detected = True
            break
    return location_hit, concept_hit, detected


def perspective_of(persona: str) -> str:
    """Reviewer perspective a persona speaks for (`security-reviewer` → `security`)."""
    base = persona.rsplit("/", 1)[-1].strip()
    return base.removesuffix("-reviewer")


def corpus_perspectives(cases: list[dict[str, Any]]) -> set[str]:
    """Every perspective some planted defect is attributed to."""
    return {
        p
        for case in cases
        for violation in case.get("violations") or []
        for p in violation.get("perspectives") or []
    }


def accountable_violations(
    case: dict[str, Any], perspective: str | None
) -> list[dict[str, Any]]:
    """Planted defects this perspective is expected to catch (all of them when None)."""
    violations = case.get("violations") or []
    if perspective is None:
        return list(violations)
    return [v for v in violations if perspective in (v.get("perspectives") or [])]


def score_review(
    case: dict[str, Any],
    text: str,
    perspective: str | None = None,
    workspace: str | None = None,
    adjudicate: Callable[..., str | None] | None = None,
) -> dict[str, Any]:
    """Score one reviewer's output for one case against that case's answer key.

    `workspace` is the directory the case was materialized into, when the caller
    knows it. Anchors under it are read as case-relative; without it an absolute
    anchor simply does not match, which silently zeroed a real review's every
    finding at once.

    `adjudicate(case, violation, finding) -> "ASSERTS" | "DENIES" | "NEITHER" | None`
    is the drill judge of ③-b, and it is a plain callable rather than an import so
    that this module stays pure and offline. It is asked only about pairs already
    credited here, and only `ASSERTS` keeps the credit — the judge narrows, never
    widens. `None` means the pair could not be judged, which is neither a detection
    nor a miss: the id lands in `unadjudicated` and the row is not rateable.
    Passing no adjudicator at all leaves `adjudicated` False for the same reason.
    """
    findings = scoreable_findings(parse_findings(text))
    all_ranges = _case_ranges(case)
    result: dict[str, Any] = {
        "case": case["id"],
        "clean": bool(case.get("clean")),
        "detections": [],
        "seeded": 0,
        "detected": 0,
        "findings_parsed": len(findings),
    }
    if not findings and not case.get("clean"):
        # "the reviewer found nothing" and "the scorer could not read the review"
        # are different failures, and only one of them is the reviewer's. Say which.
        # Not on the clean case: there the correct review *has* no findings, and
        # flagging it would put the two back together from the other side.
        result["unparsed"] = True
    if case.get("clean"):
        # On a case with nothing planted, **filing a finding is the false positive**.
        # `drill.md` defines `clean_fp_rate` as the share of clean diffs the reviewer
        # answered with a finding or a REJECT, and `add_false_positive_guard` keys off
        # that number, so the question here is whether the reviewer filed one — not
        # what vocabulary the filing happens to contain.
        #
        # Reading the words instead was measured to lose real false positives: three
        # fabricated `Severity: High` blocking findings, phrased without any of the
        # alarm vocabulary, scored `flagged=False` while the same text scored True
        # under the rule this replaced. A persona that invents high-severity findings
        # on clean code would have measured at a 0% false-positive rate and the guard
        # would never have fired.
        #
        # Prose is still read the old way when there are no findings at all, because
        # a review that files nothing can still assert a bug in passing, and "looks
        # fine" must stay free — that is the one behaviour this case exists to reward.
        result["flagged"] = bool(findings) or clean_false_positive(text)
        return result

    unadjudicated: list[str] = []
    for violation in accountable_violations(case, perspective):
        location_hit, concept_hit, detected = score_violation(
            text, violation, case=case, findings=findings,
            workspace=workspace, all_ranges=all_ranges,
        )
        entry: dict[str, Any] = {
            "violation": violation["id"],
            "category": violation.get("category"),
            "severity": violation.get("severity"),
            "expected_blocking": expected_blocking(violation.get("severity", "")),
            "location_hit": location_hit,
            "concept_hit": concept_hit,
        }
        claiming = (_claiming_findings(violation, findings, case, all_ranges, workspace)
                    if detected else [])
        claim = claiming[0] if claiming else None
        if detected and adjudicate is not None:
            # ③-b. The pair reached here because a graded finding, anchored in this
            # defect's own lines, used this defect's vocabulary. All of that is true of
            # "`reportUsage` awaits the send" as well, which is why the last question —
            # is the claim pointed at the defect or away from it — is put to a judge
            # that sees only the finding and the seed's own summary.
            #
            # Every claiming finding is asked, and one `ASSERTS` is enough. A reviewer
            # who says the same thing twice, once as a report and once as a waiver,
            # has still reported it; crediting only the first made the outcome depend
            # on which one they happened to write first. Asking all of them cannot
            # widen past the deterministic layer, because every finding here was
            # already credited by it.
            verdicts = [adjudicate(case, violation, f) for f in claiming]
            entry["adjudication"] = ("ASSERTS" if "ASSERTS" in verdicts
                                     else next((v for v in verdicts if v), None))
            if "ASSERTS" in verdicts:
                claim = next(f for f, v in zip(claiming, verdicts) if v == "ASSERTS")
            elif any(v is None for v in verdicts):
                # Not a verdict either way. `detected` keeps the deterministic value
                # so the entry stays readable, but the row is marked unrateable below
                # rather than letting an outage read as a score.
                unadjudicated.append(violation["id"])
            else:
                detected = False
                claim = None
        entry["detected"] = detected
        if claim is not None:
            entry["severity_given"] = claim.severity
            entry["blocking_given"] = claim.blocking
        result["detections"].append(entry)
    result["seeded"] = len(result["detections"])
    result["detected"] = sum(1 for d in result["detections"] if d["detected"])
    # Whether the direction of every credited claim was actually established. False
    # both when no judge ran and when one ran but could not answer some pair, because
    # a reader of the row cannot act differently on those two: neither is a measured
    # detection rate. `drill.md` already forbids writing a number that was not
    # measured, and this is the flag that keeps the rate off the row.
    result["adjudicated"] = adjudicate is not None and not unadjudicated
    if unadjudicated:
        result["unadjudicated"] = unadjudicated

    # Severity and blocking are read off the contract's own fields, so they are
    # measured rather than judged. The module used to leave both absent because the
    # scorer had no structured place to read them; scoring per finding gives it one.
    # `explanation_quality` still needs the judge in drill ③-b and stays absent.
    graded = [d for d in result["detections"] if d.get("severity_given")]
    if graded:
        result["severity_accuracy"] = round(
            sum(1 for d in graded if d["severity_given"] == d["severity"]) / len(graded), 4
        )
    blocked = [d for d in result["detections"] if d.get("blocking_given") is not None]
    if blocked:
        result["blocking_accuracy"] = round(
            sum(1 for d in blocked
                if ("Blocking" if d["blocking_given"] else "Non-blocking") == d["expected_blocking"])
            / len(blocked), 4
        )
    return result


def _claiming_findings(
    violation: dict[str, Any],
    findings: list[Finding],
    case: dict[str, Any] | None,
    all_ranges: dict[str, dict[str, list[tuple[int, int]]]],
    workspace: str | None,
) -> list[Finding]:
    """Every finding that credits this defect, in the order the reviewer wrote them.

    All of them, not the first. Taking the first made the credit depend on the order of
    a reviewer's own findings: a review containing both "`reportUsage` sends without
    awaiting, and that is by design" and "`reportUsage` never awaits `client.send`, so
    failures vanish" scored the defect only when the honest one came first. Measured on
    `ts-mixed-violations`: waiver first, `DENIES`, no credit; honest first, `ASSERTS`,
    credit — same review, same judge.

    That is the judge harming an honest reviewer, which the calibration set treats as
    disqualifying when it happens inside the judge; it must not be reachable from
    outside it either.
    """
    concept = re.compile(violation["concept"])
    vid = violation.get("id")
    siblings = list(case.get("violations") or []) if case is not None else [violation]
    return [f for f in findings
            if _finding_claims(f, siblings, all_ranges, workspace) == {vid}
            and concept.search(f.body)]


def _claiming_finding(
    violation: dict[str, Any],
    findings: list[Finding],
    case: dict[str, Any] | None,
    all_ranges: dict[str, dict[str, list[tuple[int, int]]]],
    workspace: str | None,
) -> Finding | None:
    """The first finding that credits this defect — kept for callers wanting just one."""
    claiming = _claiming_findings(violation, findings, case, all_ranges, workspace)
    return claiming[0] if claiming else None


def build_drill_row(
    reviews: dict[str, dict[str, str]],
    cases: list[dict[str, Any]] | None = None,
    root: pathlib.Path | None = None,
    workspaces: dict[str, str] | None = None,
    adjudicate: Callable[..., str | None] | None = None,
) -> dict[str, Any]:
    """One `.rig/drill-results.jsonl` row from {case-id: {persona: review text}}.

    Attribution: a persona is scored on the planted defects whose `perspectives`
    name its perspective. A persona whose perspective appears nowhere in the
    corpus (a generalist reviewer, say) would otherwise score 0/0 — those rows
    are scored on every planted defect instead and marked `attribution: "all"`,
    so a scoreboard reader can tell the two apart.

    `adjudicate` is the drill judge (③-b). The row carries `adjudicated`, true only
    when every credited pair on every persona got a verdict.

    When it is false the detection-derived keys on each score are renamed with an
    `_unadjudicated` suffix rather than dropped. Filtering at the consumer was tried
    first and only reached `aggregate_drill_confidence`; `digest`, `dashboard` and
    `fleet` all sum `scores[].detected/seeded` directly and print a percentage, and none
    of them looks at `scorer_version` either. Renaming closes all four at the writer,
    because each of them skips a score with no `seeded`. The numbers stay on the row so
    a run is still auditable — they just stop answering to the name of a measurement.

    `judge` carries the provenance of the verdicts when the adjudicator can supply it.
    """
    all_cases = cases if cases is not None else load_cases(root=root)
    by_id = {c["id"]: c for c in all_cases}
    known = corpus_perspectives(all_cases)
    meta = load_corpus_meta(root)

    personas = sorted({p for per_case in reviews.values() for p in per_case})
    scored_cases = [by_id[cid] for cid in reviews if cid in by_id]
    scores: list[dict[str, Any]] = []
    row_unadjudicated = 0

    for persona in personas:
        perspective = perspective_of(persona)
        attribution = "perspective" if perspective in known else "all"
        effective = perspective if attribution == "perspective" else None
        detected = seeded = 0
        clean_diffs = clean_findings = 0
        unreadable: list[str] = []
        unadjudicated_pairs: list[dict[str, Any]] = []
        severity_right = severity_graded = 0
        blocking_right = blocking_graded = 0
        missed: list[str] = []
        missed_detail: list[dict[str, Any]] = []
        for case in scored_cases:
            text = reviews[case["id"]].get(persona)
            if text is None:
                continue
            row = score_review(
                case, text, effective,
                workspace=(workspaces or {}).get(case["id"]),
                adjudicate=adjudicate,
            )
            for vid in row.get("unadjudicated") or []:
                unadjudicated_pairs.append({"case": case["id"], "violation": vid})
            # A review the scorer could not read is not a reviewer that found
            # nothing, and a rate that mixes them is unreadable itself.
            if row.get("unparsed") and not row["clean"]:
                unreadable.append(case["id"])
            if row["clean"]:
                clean_diffs += 1
                clean_findings += int(row["flagged"])
                continue
            detected += row["detected"]
            seeded += row["seeded"]
            for d in row["detections"]:
                if d.get("severity_given"):
                    severity_graded += 1
                    severity_right += int(d["severity_given"] == d["severity"])
                if d.get("blocking_given") is not None:
                    blocking_graded += 1
                    blocking_right += int(
                        ("Blocking" if d["blocking_given"] else "Non-blocking")
                        == d["expected_blocking"]
                    )
            for d in row["detections"]:
                if d["detected"]:
                    continue
                missed.append(str(d["category"]))
                missed_detail.append({
                    "case": case["id"],
                    "violation": d["violation"],
                    "category": d["category"],
                    "severity": d["severity"],
                })
        score: dict[str, Any] = {
            "reviewer": persona,
            "detected": detected,
            "seeded": seeded,
            "missed": missed,
            "missed_detail": missed_detail,
            "attribution": attribution,
            "clean_diffs": clean_diffs,
            "clean_findings": clean_findings,
        }
        if unreadable:
            score["unreadable_cases"] = unreadable
        if unadjudicated_pairs:
            # Named, not counted. "the judge could not answer" is actionable only if
            # you can see which pair it choked on and re-run just that one.
            score["unadjudicated"] = unadjudicated_pairs
            row_unadjudicated += len(unadjudicated_pairs)
        # Read off the contract's own fields rather than judged, so they belong on
        # the row: a persona that finds everything and grades it Low has not caught
        # it in any sense an operator cares about, and detection alone hides that.
        # Computed before the rename below, not after — writing them afterwards made
        # two entries of `_MEASURED_KEYS` dead code and left `severity_accuracy: 1.0`
        # on an unadjudicated persona, under the measured name, over a pre-judge
        # denominator. That number is the one the waiver attack posts.
        if severity_graded:
            score["severity_accuracy"] = round(severity_right / severity_graded, 3)
        if blocking_graded:
            score["blocking_accuracy"] = round(blocking_right / blocking_graded, 3)
        # `clean_fp_rate` is deliberately not in `_MEASURED_KEYS`: the clean case has
        # nothing planted, so no verdict is involved and no judge can spoil it.
        if clean_diffs:
            score["clean_fp_rate"] = round(clean_findings / clean_diffs, 3)
        scores.append(score)

    judge = (adjudicate.provenance()
             if adjudicate is not None and hasattr(adjudicate, "provenance") else None)
    # One decision, made after the judge's own account of the run is in, because two of
    # the three ways a row fails to be a measurement are only visible there.
    #
    #   no judge at all                        — the pre-judge count, plainly
    #   a pair it could not answer             — an outage, not a score
    #   any verdict that came from the ledger  — someone else's measurement, or nobody's
    #
    # The third arrived from a verifier who defeated the first version of this guard
    # end to end. Re-deriving a verdict from its recorded output raised the price of
    # forging a ledger; it did not stop it, because `ledger_key` is public and a
    # plausible `raw` satisfies the check. So they forged four pairs of five, let one
    # reach a real provider, and the row came back `calls: 1, offline: false,
    # adjudicated: true` — through every guard, publishing 80% detection on a review
    # whose every finding said "no action required". A ledger hit is a replay of a
    # measurement or a fabrication of one; a rate needs neither.
    measured = (
        adjudicate is not None
        and row_unadjudicated == 0
        and judge is not None
        and not judge.get("offline")
        and judge.get("calls")
        and not judge.get("cache_hits")
    )
    if not measured:
        # Renamed rather than dropped, and at the writer rather than at each reader:
        # `aggregate_drill_confidence` filters the row, but `digest`, `dashboard` and
        # `fleet` sum `scores[].detected/seeded` straight and print a percentage, and
        # none of them reads `scorer_version` either. All three skip a score with no
        # `seeded`, so renaming closes every reader at once. The numbers stay on the
        # row — they just stop answering to the name of a measurement.
        for score in scores:
            for key in _MEASURED_KEYS:
                if key in score:
                    score[f"{key}_unadjudicated"] = score.pop(key)

    planted = sum(len(c.get("violations") or []) for c in scored_cases if not c.get("clean"))
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "corpus": meta.get("corpus", "fixture"),
        "corpus_version": meta.get("corpus_version"),
        "scorer_version": SCORER_VERSION,
        # Which answer key this run was scored against, and where it came from. A row
        # naming neither could have been produced by a substituted corpus (`RIG_HOME`)
        # and read as the shipped one.
        "corpus_root": str(root or corpus_root()),
        "corpus_digest": corpus_digest(scored_cases),
        # What produced the verdicts, when anything did. Absent means no judge ran, which
        # `adjudicated` already says; present, it names the provider, the prompt and the
        # ledger, so a real run and a hand-written one stop looking alike.
        **({"judge": judge} if judge is not None else {}),
        # Whether this row's detection numbers are a measurement. Narrower than
        # `adjudicated`, which only asks whether every pair got a verdict — this also
        # asks whether the verdicts were produced here.
        "measured": bool(measured),
        # False when no judge ran, and false when one ran but left a pair unanswered.
        # `detected`/`seeded` are still on the row so the run is auditable, but a
        # consumer must not divide them: see `aggregate_drill_confidence`.
        "adjudicated": adjudicate is not None and row_unadjudicated == 0,
        "seeds": planted,
        "valid_seeds": planted,
        "clean_diffs": sum(1 for c in scored_cases if c.get("clean")),
        "cases": [c["id"] for c in scored_cases],
        "scores": scores,
    }


def materialize_case(
    case_dir: pathlib.Path, into: pathlib.Path | None = None
) -> pathlib.Path:
    """Commit base/, then lay head/ over it as uncommitted working-tree changes."""
    if into is None:
        workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"drill-{case_dir.name}-"))
    else:
        workspace = pathlib.Path(into)
        workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case_dir / "base", workspace, dirs_exist_ok=True)
    git = ["git", "-c", "user.name=rig-drill", "-c", "user.email=drill@rig.local"]
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        [*git, "commit", "-q", "-m", "base"], cwd=workspace, check=True, capture_output=True
    )
    for path in (case_dir / "head").iterdir():
        if path.is_dir():
            shutil.copytree(path, workspace / path.name, dirs_exist_ok=True)
        else:
            shutil.copy2(path, workspace / path.name)
    return workspace


def calibration_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or corpus_root()) / "judge-calibration.json"


def calibration_ledger_path(root: pathlib.Path | None = None) -> pathlib.Path:
    """The recorded verdicts of one real calibration run, shipped with the corpus.

    It is a record, not an answer key: it says what one judge said on one day, which
    is what makes the measurement auditable and replayable offline. Anyone can re-run
    `calibrate-judge` against a live provider and compare.
    """
    return (root or corpus_root()) / "judge-calibration-ledger.jsonl"


def load_calibration(root: pathlib.Path | None = None) -> list[dict[str, Any]]:
    """The judge's own answer key (`judge-calibration.json`).

    Every entry is a (seed, finding) pair the deterministic layer already credits, so
    each one is a question the judge will really be asked. See the file's `_about`.
    """
    path = calibration_path(root)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("entries") or []


def calibrate_judge(
    adjudicate: Callable[..., str | None],
    root: pathlib.Path | None = None,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the judge over its calibration set and report where it disagrees.

    `policies/independent-verification` clauses 5-7 apply to the instrument, and the
    judge is one. Two failure directions, reported separately because they mean
    opposite things and averaging them hides both:

      a missed `ideal`/`negative` -> the judge is taking credit from honest reviewers
      a credited `attack`         -> the hole it was built to close is still open

    Either one disqualifies the verdicts. Neither is a score to be improved by
    rerunning until it looks better.
    """
    entries = load_calibration(root)
    all_cases = {c["id"]: c for c in (cases if cases is not None else load_cases(root=root))}
    results: list[dict[str, Any]] = []
    for entry in entries:
        case = all_cases.get(entry["case"])
        violation = next(
            (v for v in (case or {}).get("violations") or [] if v["id"] == entry["violation"]),
            None,
        )
        if case is None or violation is None:
            # The corpus moved out from under the calibration set. Louder than a wrong
            # verdict: the set is no longer asking about anything.
            results.append({**{k: entry[k] for k in ("case", "violation", "family", "expect")},
                            "got": None, "agreed": False, "missing": True})
            continue
        verdict = adjudicate(case, violation, Finding(
            title="", body=entry["body"], blocking=None, severity=None, offset=0))
        results.append({
            "case": entry["case"], "violation": entry["violation"],
            "family": entry["family"], "expect": entry["expect"],
            "got": verdict, "agreed": verdict == entry["expect"],
        })

    families: dict[str, dict[str, Any]] = {}
    for r in results:
        fam = families.setdefault(r["family"], {"n": 0, "agreed": 0, "disagreed": []})
        fam["n"] += 1
        if r["agreed"]:
            fam["agreed"] += 1
        else:
            fam["disagreed"].append({"case": r["case"], "violation": r["violation"],
                                     "expect": r["expect"], "got": r["got"]})
    for fam in families.values():
        fam["agreement"] = round(fam["agreed"] / fam["n"], 3) if fam["n"] else None
    return {
        "prompt_version": (json.loads(calibration_path(root).read_text(encoding="utf-8"))
                           .get("prompt_version") if calibration_path(root).exists() else None),
        "n": len(results),
        "families": families,
        # The judge is usable only if it harms no honest reviewer AND credits no attack.
        "usable": bool(results) and all(r["agreed"] for r in results),
        "results": results,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _resolve_review(value: str) -> str:
    """A review is inline text, or `@path` to read it from a file."""
    if value.startswith("@"):
        return pathlib.Path(value[1:]).read_text(encoding="utf-8")
    return value


def cmd_drill_corpus(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases or None)
    if not cases:
        print(f"no cases found under {corpus_root()}")
        return

    if args.action == "list":
        meta = load_corpus_meta()
        if args.json:
            print(json.dumps({
                "corpus": meta.get("corpus", "fixture"),
                "corpus_version": meta.get("corpus_version"),
                "scorer_version": SCORER_VERSION,
                "root": str(corpus_root()),
                "cases": [
                    {
                        "id": c["id"],
                        "language": c.get("language"),
                        "clean": bool(c.get("clean")),
                        "violations": [v["id"] for v in c.get("violations") or []],
                    }
                    for c in cases
                ],
            }, ensure_ascii=False))
            return
        print(f"## drill fixture corpus v{meta.get('corpus_version')} ({corpus_root()})")
        for case in cases:
            kind = ("clean (measures false positives)" if case.get("clean")
                    else f"{len(case['violations'])} planted defects")
            print(f"  {case['id']:24s} {case.get('language', ''):11s} {kind}")
            for violation in case.get("violations") or []:
                perspectives = ", ".join(violation.get("perspectives") or []) or "-"
                print(f"      {violation['id']:26s} {violation['severity']:9s} {perspectives}")
        return

    if args.action == "materialize":
        if not args.case:
            print("materialize needs a case id (see `list`)")
            return
        target = [c for c in cases if c["id"] == args.case]
        if not target:
            print(f"unknown case: {args.case}")
            return
        workspace = materialize_case(target[0]["_dir"], args.into)
        print(workspace)
        return

    if args.action == "calibrate-judge":
        from .adjudication import DEFAULT_JUDGE_PROVIDER, make_adjudicator
        adjudicate = make_adjudicator(
            getattr(args, "judge", None) or DEFAULT_JUDGE_PROVIDER,
            model=getattr(args, "judge_model", None),
            ledger=getattr(args, "judge_ledger", None),
            offline=bool(getattr(args, "judge_offline", False)),
        )
        # `--cases` narrows the corpus, never the calibration set: the set is one fixed
        # instrument check, and `calibrate_judge` reports a case it cannot find as a
        # disagreement on purpose (a corpus that moved out from under the set is louder
        # than a wrong verdict). Honouring the flag here would turn every unselected
        # case into a false `usable: NO`, which is the safe direction and still a lie.
        if args.cases:
            print(f"note: --cases does not narrow the calibration set; running all "
                  f"{len(load_calibration())} pairs against the whole corpus")
        report = calibrate_judge(adjudicate, cases=load_cases())
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
            return
        if not report["n"]:
            print(f"no calibration set at {calibration_path()}")
            return
        print(f"## drill judge calibration (prompt v{report['prompt_version']}, "
              f"{report['n']} pairs)")
        for family in sorted(report["families"]):
            fam = report["families"][family]
            print(f"  {family:9s} {fam['agreed']:2d}/{fam['n']:<2d} "
                  f"agreement {fam['agreement']:.0%}")
            for bad in fam["disagreed"]:
                print(f"      {bad['case']}/{bad['violation']}: "
                      f"expected {bad['expect']}, got {bad['got']}")
        print("usable: " + ("yes" if report["usable"]
                            else "NO — see the disagreements above"))
        return

    # action == "score"
    if not args.reviews:
        print("score needs --reviews <path.json> ({case-id: {persona: review text or @path}})")
        return
    raw = json.loads(pathlib.Path(args.reviews).read_text(encoding="utf-8"))
    reviews = {
        case_id: {p: _resolve_review(text) for p, text in per_case.items()}
        for case_id, per_case in raw.items()
    }
    unknown = sorted(set(reviews) - {c["id"] for c in cases})
    if unknown:
        print(f"unknown case id(s) in --reviews: {', '.join(unknown)}")
        return
    # `--workspace case=dir` for every case that was materialized somewhere, so a
    # reviewer's absolute anchors resolve. Without it an absolute path is simply a
    # different path and the anchor route goes silent for that whole review.
    workspaces: dict[str, str] = {}
    for pair in getattr(args, "workspace", None) or []:
        case_id, _, directory = pair.partition("=")
        if not directory:
            print(f"--workspace expects CASE=DIR (got: {pair!r})")
            raise SystemExit(2)
        workspaces[case_id] = directory
    # The judge is opt-in, and the row says which way it went. `--judge-offline`
    # replays a recorded run from the ledger without reaching a provider at all.
    from .adjudication import make_adjudicator  # local: keeps this module provider-free
    adjudicate = make_adjudicator(
        getattr(args, "judge", None),
        model=getattr(args, "judge_model", None),
        ledger=getattr(args, "judge_ledger", None),
        offline=bool(getattr(args, "judge_offline", False)),
    )
    row = build_drill_row(reviews, cases, workspaces=workspaces or None,
                          adjudicate=adjudicate)
    line = json.dumps(row, ensure_ascii=False)
    if args.append:
        path = pathlib.Path(args.append)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(f"appended to {path}")
    print(line)
