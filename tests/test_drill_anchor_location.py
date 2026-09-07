"""A finding that anchors by `file:line` is located, even without the symbol.

`output-contracts/review-findings` requires every finding to carry a `file:line`
evidence anchor. The fixture answer key locates a planted defect by a regex over
the *symbol* it lives in. Those are two different notions of "where", and the
scorer only ever honoured the second: a review that pointed at the right line and
explained the right defect scored as a miss because it paraphrased the identifier
instead of quoting it.

Measured, not supposed. Two reviews of the same case, produced from the same
prompt with only the persona text differing, both found the unit-label defect at
`workflow.ts:24`. The one whose prose happened to contain `recommendedQuantityLabel`
scored 5/5; the one that did not scored 4/5. Detection rate is supposed to measure
what a reviewer catches, so a rate that moves on whether the prose quotes an
identifier trains persona authors to quote identifiers.

The line range a defect owns is derived from the corpus itself — the hunks where
`head/` differs from `base/` that carry the symbol — so no line numbers are added
to the answer keys by hand and there is no proximity constant to tune.
"""

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rig_workbench.workbench import detection_corpus  # noqa: E402

CASE_ID = "ts-behavioral-correctness"
VIOLATION_ID = "quantity-unit-confusion"


@pytest.fixture(scope="module")
def case():
    cases = detection_corpus.load_cases([CASE_ID])
    assert cases, f"fixture case {CASE_ID} is missing"
    return cases[0]


@pytest.fixture(scope="module")
def violation(case):
    for v in case["violations"]:
        if v["id"] == VIOLATION_ID:
            return v
    pytest.fail(f"{CASE_ID} no longer plants {VIOLATION_ID}")


def _review(anchor: str) -> str:
    """A finding in the shape review-findings asks for, with no symbol quoted."""
    return (
        "## Blocking\n\n"
        "### 1. ORDER モードで発注単位ではなく在庫単位が表示される\n\n"
        "- Severity: High\n"
        f"- File: `{anchor}`\n"
        "- Impact: 変更前は mode が ORDER のとき発注単位を使っていたが、"
        "変更後は常に在庫単位を表示する。単位の取り違えが発注数量の誤入力に直結する。\n"
        "- Suggested fix: mode による分岐を復元する。\n"
    )


def test_an_anchor_on_the_defect_line_counts_as_located(case, violation):
    _, _, detected = detection_corpus.score_violation(
        _review("workflow.ts:24"), violation, case=case
    )
    assert detected, (
        "a finding that anchors the right line and explains the right defect is a "
        "detection; requiring the identifier makes the rate measure prose habits"
    )


def test_the_symbol_still_locates_a_defect_on_its_own(case, violation):
    text = (
        "`recommendedQuantityLabel` が常に inventoryUnit を使うため、"
        "ORDER モードで発注単位の数量に在庫単位のラベルが付く。\n"
    )
    _, _, detected = detection_corpus.score_violation(text, violation, case=case)
    assert detected, "symbol-based location is the existing contract and must keep working"


def test_an_anchor_far_from_the_defect_does_not_count(case, violation):
    _, _, detected = detection_corpus.score_violation(
        _review("workflow.ts:1"), violation, case=case
    )
    assert not detected, (
        "accepting any anchor in the file would make location vacuous — the whole "
        "review would locate every defect in every file it names"
    )


def test_an_anchor_to_another_file_does_not_count(case, violation):
    _, _, detected = detection_corpus.score_violation(
        _review("other/elsewhere.ts:24"), violation, case=case
    )
    assert not detected, "the path has to be the file the defect was planted in"


def test_the_same_name_in_another_directory_does_not_count(case, violation):
    """The path is compared whole, not by basename.

    Every fixture file sits at the root of its case, so a suffix rule degenerates:
    `endswith("/workflow.ts")` accepts `other/workflow.ts`. Measured on the first
    draft, which is why the file this guards is not the one the test above uses.
    """
    _, _, detected = detection_corpus.score_violation(
        _review("other/workflow.ts:24"), violation, case=case
    )
    assert not detected, "a different file with the same basename is a different file"


def test_a_whole_file_range_locates_nothing(case, violation):
    """An anchor has to begin at the defect, not swallow the file it is in.

    Measured on the first draft: `service.py:1-63` overlapped every planted range at
    once, so a review that named the file and listed the concepts scored 5/5 on a case
    it had said nothing specific about.
    """
    line_count = len(
        (case["_dir"] / "head" / "workflow.ts").read_text(encoding="utf-8").splitlines()
    )
    _, _, detected = detection_corpus.score_violation(
        _review(f"workflow.ts:1-{line_count}"), violation, case=case
    )
    assert not detected, "naming the file is not locating the defect"


def test_one_finding_does_not_credit_the_defects_beside_it(case):
    """The real path: one review, scored against every violation in the case.

    Each test above scores a single violation, which is where cross-credit hides — a
    review that locates one defect must not collect the others planted near it. The
    first draft widened every hunk by three lines and two seeds thirteen lines apart
    came to share a range.
    """
    review = _review("workflow.ts:24")
    result = detection_corpus.score_review(case, review, perspective="behavioral-correctness")
    credited = [d["violation"] for d in result["detections"] if d["detected"]]
    assert credited == [VIOLATION_ID], (
        f"one finding about the unit label credited {credited}; a location that "
        f"cannot tell two defects apart is not a location"
    )


def test_the_concept_still_has_to_be_discussed_near_the_anchor(case, violation):
    text = (
        "## Blocking\n\n"
        "### 1. 命名が実態と合っていない\n\n"
        "- Severity: Low\n"
        "- File: `workflow.ts:24`\n"
        "- Impact: 関数名が返り値の意味を説明していない。\n"
    )
    _, _, detected = detection_corpus.score_violation(text, violation, case=case)
    assert not detected, (
        "a correct anchor with the wrong subject is not a detection; the concept "
        "test is what keeps the anchor from being a lucky line number"
    )


def test_scoring_without_a_case_falls_back_to_the_symbol(violation):
    """`case` is optional so existing callers keep working unchanged."""
    _, _, detected = detection_corpus.score_violation(
        _review("workflow.ts:24"), violation
    )
    assert not detected, "with no corpus to resolve lines against, only the symbol locates"


def test_the_line_beside_the_defect_does_not_count(case, violation):
    """The defect owns exactly the changed lines carrying its symbol — no window.

    An earlier draft widened every hunk by three lines and was measured to hand one
    defect's lines to the defect next to it. The window is gone, but nothing above
    pins its absence: a test-review of this change re-introduced a one-line widening
    as a mutation and every other test in this file stayed green.

    `quantity-unit-confusion` owns `workflow.ts:23-24`. Lines 22 and 25 are its
    neighbours. If either scores, the window is back.
    """
    for off_by_one in ("workflow.ts:22", "workflow.ts:25"):
        _, _, detected = detection_corpus.score_violation(
            _review(off_by_one), violation, case=case
        )
        assert not detected, (
            f"{off_by_one} is adjacent to the defect, not on it; crediting it means a "
            f"widening constant has come back and neighbouring seeds will cross-credit"
        )


def test_defects_sharing_every_line_are_located_by_the_symbol_only():
    """When seeds share all their lines, the anchor path abstains for all of them.

    An anchor scores only on a line one defect owns alone — a line inside two defects'
    ranges does not say which one is meant. That is the right call, and it has a cost
    worth pinning: in `ts-mixed-violations` four seeds sit in one changed hunk, so
    `floating-promise`, `any-on-public-api` and `hardcoded-secret` have no exclusive
    line at all and no `file:line` anchor can ever reach them. They stay reachable the
    original way, through the symbol.

    This is not a regression — before this change no anchor located anything — but a
    corpus author planting several defects in one hunk should know the anchor path
    will not fire there. `skills/engine/corpora/fixture/README.md` says so; this test
    is what makes the claim fail loudly if the ambiguity rule is loosened.
    """
    cases = detection_corpus.load_cases(["ts-mixed-violations"])
    assert cases, "fixture case ts-mixed-violations is missing"
    mixed = cases[0]
    floating = next(v for v in mixed["violations"] if v["id"] == "floating-promise")

    head = (mixed["_dir"] / "head" / "cache.ts").read_text(encoding="utf-8").splitlines()
    review_at = (
        "## Blocking\n\n"
        "### 1. 送信結果が待機されていない\n\n"
        "- Severity: High\n"
        "- File: `cache.ts:{line}`\n"
        "- Impact: 呼び出しが await されておらず、失敗しても unhandled rejection になる。\n"
    )
    for line in range(1, len(head) + 1):
        _, _, detected = detection_corpus.score_violation(
            review_at.format(line=line), floating, case=mixed
        )
        assert not detected, (
            f"cache.ts:{line} credited floating-promise through the anchor path, but "
            f"that line is shared with other seeds and cannot say which one is meant"
        )

    by_symbol = (
        "`reportUsage` の `client.send` が await されておらず、"
        "失敗が unhandled rejection になる。\n"
    )
    _, _, detected = detection_corpus.score_violation(by_symbol, floating, case=mixed)
    assert detected, "the symbol path is the only way in for a seed with no exclusive line"


CONCEPT_SALAD = (
    "- Impact: 二重に送信できる。未完了のまま離脱できる。"
    "在庫単位と発注単位が違う。集計の粒度が合わない。並び順の切り替えが効かない。\n"
)


def _one_finding_naming(locations: str) -> str:
    """One finding, one sentence, and every planted defect named in passing."""
    return (
        "## Blocking\n\n"
        "### 1. 変更範囲の複数箇所に不具合がある\n\n"
        f"- File: {locations}\n" + CONCEPT_SALAD
    )


def test_listing_every_defect_line_in_one_finding_scores_the_same_as_listing_the_symbols(case):
    """A concept salad with one anchor per defect scores full marks — as it always did.

    Requiring an anchor to *begin* inside the defect's region stops one wide range from
    covering the file. It does not stop five point anchors on one line. A single finding
    that lists `workflow.ts:9 :13 :24 :30 :44` and one sentence touching all five subjects
    scores 5/5, because `PROXIMITY_WINDOW` spans the whole paragraph and each anchor then
    satisfies its own defect's concept.

    This is pinned rather than fixed, and the reason is the parity below: the same salad
    with the answer key's five symbols pasted in place of the line numbers **already scored
    5/5 before anchors existed**. Copying five line numbers out of a diff costs what copying
    five identifiers costs, and yields the same. So this change opened a second door of the
    same width beside the one that was already open — it did not open a cheaper one. The
    thing that makes both doors work is `PROXIMITY_WINDOW`, which predates this change and
    is out of scope here.

    What this test is for: if someone narrows the proximity window, or makes the anchor path
    demand its concept closer than the symbol path does, these numbers move. They should move
    together. A change that drops the anchor score while leaving the symbol score at 5/5 has
    not closed the hole, it has only made the anchor path worse than the prose path — which
    is the bug this whole file exists to fix, running backwards.
    """
    anchors = " ".join(
        f"`workflow.ts:{n}`" for n in (9, 13, 24, 30, 44)
    )
    symbols = " ".join(
        f"`{s}`"
        for s in (
            "dialogSubmitting",
            "canLeaveWithoutConfirmation",
            "recommendedQuantityLabel",
            "dailySigma",
            "mobileSortOptions",
        )
    )

    by_anchor = detection_corpus.score_review(
        case, _one_finding_naming(anchors), perspective="behavioral-correctness"
    )
    credited = [d["violation"] for d in by_anchor["detections"] if d["detected"]]
    assert len(credited) == 5, (
        f"the point-anchor salad credited {credited}; this is the measured ceiling and "
        f"a change to it is a change to what the drill rate means"
    )

    by_symbol = sum(
        detection_corpus.score_violation(_one_finding_naming(symbols), v)[2]
        for v in case["violations"]
    )
    assert by_symbol == 5, (
        "the parity that makes the anchor ceiling acceptable is that the symbol salad "
        "already scored 5/5 with no case at all; if that stops being true, the anchor "
        "path is now the cheaper attack and needs its own narrowing"
    )
