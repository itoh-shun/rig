"""Unit tests for the /rig:drill fixture corpus (skills/engine/corpora/fixture/).

The heart of this file is the scorer's own validation: three review fixtures per
planted-defect case, each with a known correct score.

  ideal  a review that names every planted defect properly -> 100%
  vague  plausible review prose that identifies nothing    ->   0%
  decoy  every symbol named, but no defect described       ->   0%

`decoy` is the one that matters. Without it a scorer that merely greps for
symbol names looks perfect, and any reviewer would score full marks for listing
the files it read.

On the clean case the direction inverts: `praise` must not register a false
positive and `alarm` must.
"""

import json
import shutil
import subprocess

import pytest

from rig_workbench.validation import state
from rig_workbench.validation.drill import check_fixture_corpus_integrity
from rig_workbench.workbench.detection_corpus import (_defect_line_ranges, build_drill_row,
                                                      corpus_root, load_cases,
                                                      load_corpus_meta, materialize_case,
                                                      score_review, score_violation)

IDEAL = {
    "py-mixed-violations": """
## Blocking

### 1. close_account is missing the ownership check

- Severity: Critical
`close_account` in service.py never calls `_require_owner`, unlike its sibling
`get_account`. Any authenticated actor can close another user's account. This is
a broken access control / authorization hole.

### 2. SQL injection in search_accounts

- Severity: Critical
`search_accounts` interpolates `owner_name` directly into the query string with
an f-string. Use a parameterized query with a bind placeholder instead.

### 3. N+1 query introduced in list_transactions

- Severity: High
`list_transactions` now selects the ids and then issues one query per row inside
a loop. That is a classic N+1 pattern; restore the single batch query.

### 4. record_audit swallows every failure

- Severity: High
`record_audit` wraps the write in a bare `except Exception: pass`, which
silently discards audit failures. Do not suppress the error.

### 5. test_audit_records_event was disabled

- Severity: Medium
`test_audit_records_event` is now decorated with `mark.skip` marked flaky, so
the audit path has no coverage. Skipping is not a fix.
""",
    "ts-mixed-violations": """
## Blocking

### 1. hardcoded credential

- Severity: Critical
`REPORTING_TOKEN` is a high-entropy credential committed in source and
transmitted to a third party. Move it to an environment variable and rotate it.

### 2. summarize is a breaking API change

- Severity: High
`summarize` gained a required second parameter, which breaks every existing
caller. Make it optional to preserve backward compatibility.

### 3. sortEntries now mutates the caller's array

- Severity: High
`sortEntries` lost its defensive copy (the spread), so `.sort()` reorders the
caller's array in place. That is an unexpected side effect.

### 4. floating promise in reportUsage

- Severity: High
`reportUsage` calls `client.send` without `await` or a `.catch`, so a rejection
is unhandled and failures disappear silently.

### 5. any erases type safety

- Severity: Medium
`mergeMetadata` accepts and returns `any`, which removes type safety from a
public export. Use a generic or `unknown` with narrowing.
""",
    "ts-behavioral-correctness": """
## Blocking

### 1. preview中に確認操作が再実行できる

- Severity: High
`dialogSubmitting` が `confirming` しか見ておらず preview 中を loading として扱いません。preview API が in-flight の間に二重 submit や close が可能になるので、previewing || confirming を busy state にする必要があります。

### 2. 部分成功後の未完了状態を確認なしで離脱できる

- Severity: High
`canLeaveWithoutConfirmation` が lines だけを見ており、shortfalls が残る partial success 状態を無視しています。lines=0 / shortfalls>0 では事後処理が未完了なので cancel/leave に確認が必要です。

### 3. 発注数量に在庫単位を付けている

- Severity: High
`recommendedQuantityLabel` は ORDER の recommendedQuantity にも常に inventoryUnit を表示しています。ORDER は orderUnit の数量なので、発注単位と在庫単位の unit mismatch になります。

### 4. 日次標準偏差をイベント粒度で計算している

- Severity: High
`dailySigma` が same day のイベントを日ごとに aggregate せず、生イベントの sumSquares から分散を出しています。日次需要の統計なら同日複数イベントを先に group しないと集約粒度が変わります。

### 5. モバイルで降順へ切り替えられない

- Severity: High
`mobileSortOptions` は key しか選べず、`applyMobileSortSelection` は同じ key の再選択で ASC/DESC を toggle する設計です。native select の onChange は同じ value の再選択では発火しないため DESC が unreachable です。
""",
    "js-layout-gate": """
## Blocking

### 1. 溢れ判定に許容値を足して通している

- Severity: High
`FIT_SLACK_PX` の 24px を箱の高さに足したうえで `overflowsBox` が比較しているため、宣言した箱を超えた資料が「収まっている」と報告されます。落ちたあとで閾値を緩めて通すのは、許容誤差を広げて検査を骨抜きにする形です。

### 2. 環境変数で gate ごと外せる

- Severity: High
`LAYOUT_GATE_OFF` が立っていると `enforce` は一切測らずに合格を返します。外から検査を無効にできる抜け道であり、未計測のまま出荷されます。

### 3. 折り返さずに本文を切り捨てている

- Severity: High
`clampLine` は段落を1行分の文字数へ truncate して末尾を省略記号に置き換えます。枠に入れるために本文が削られており、収まったのではなく情報が欠落しています。

### 4. renderer が無い場合を「検査済み合格」にしている

- Severity: High
`rendererAbsent` が真のとき `measureDeck` は `checked: true` のまま ok を返します。一度も測っていないのに検査が走ったことにしており、未検査を合格に読み替えています。UNVERIFIED として扱うべきです。

### 5. 見出しの高さを実測せず固定値にしている

- Severity: High
`HEADING_BOX_PX` の 88 を `requiredBodyHeight` がそのまま使い、renderer での実測をやめています。見出しが2行に折り返しても必要高さが増えないため、固定値の決め打ちで必要空間を過小に見積もります。
""",
}

VAGUE = """
I reviewed the changes. Overall the structure looks reasonable and the code is
readable. There may be some edge cases worth considering, and I would suggest
adding a few more tests if any behaviour is not yet covered. Nothing stood out
as an obvious blocker, though a second pair of eyes on the error handling could
be worthwhile.
"""

def _decoy(entries: list[tuple[str, str]]) -> str:
    """A review that follows the contract and says nothing.

    One finding per planted symbol, each naming the symbol and its line and then
    describing the code instead of a defect. This is the shape that has to score
    zero: the reviewer looked at the right places, wrote them down in the right
    format, and never claimed anything was wrong. `location_hit` is true for each
    seed and `detected` is false, which is the distinction the scoreboard keeps —
    "named the symbol but never said what was wrong" is not "never looked".
    """
    blocks = [
        f"### {index}. {symbol} を確認した\n\n"
        f"- Severity: Low\n- File: `{anchor}`\n"
        f"- Suggested fix: 命名はモジュールの慣習どおりで、整形も揃っている。\n"
        for index, (symbol, anchor) in enumerate(entries, 1)
    ]
    return "## Non-blocking\n\n" + "\n".join(blocks)


DECOY = {
    "py-mixed-violations": _decoy([
        ("close_account", "service.py:30"),
        ("search_accounts", "service.py:36"),
        ("list_transactions", "service.py:43"),
        ("record_audit", "service.py:59"),
        ("test_audit_records_event", "test_service.py:41"),
    ]),
    "ts-mixed-violations": _decoy([
        ("REPORTING_TOKEN", "cache.ts:7"),
        ("summarize", "cache.ts:24"),
        ("sortEntries", "cache.ts:21"),
        ("reportUsage", "cache.ts:27"),
        ("mergeMetadata", "cache.ts:33"),
    ]),
    "ts-behavioral-correctness": _decoy([
        ("dialogSubmitting", "workflow.ts:9"),
        ("canLeaveWithoutConfirmation", "workflow.ts:13"),
        ("recommendedQuantityLabel", "workflow.ts:24"),
        ("dailySigma", "workflow.ts:30"),
        ("mobileSortOptions", "workflow.ts:44"),
    ]),
    "js-layout-gate": _decoy([
        ("clampLine", "deck-fit.js:11"),
        ("HEADING_BOX_PX", "deck-fit.js:26"),
        ("FIT_SLACK_PX", "deck-fit.js:34"),
        ("rendererAbsent", "deck-fit.js:54"),
        ("LAYOUT_GATE_OFF", "deck-fit.js:59"),
    ]),
}

PRAISE = """
This is a clean refactor. Validation was extracted into `_validate_discount`,
and the accumulator loop was replaced with `sum()` over the existing
`line_total_cents` helper, which removes the duplicated arithmetic. Behaviour is
preserved. Looks good to me.
"""

ALARM = """
CRITICAL: this refactor introduces a security issue in the discount handling and
must fix before merge. The bug is blocking.
"""

CLEAN_CASE = "py-clean-refactor"


def violation_cases():
    return [c for c in load_cases() if not c.get("clean")]


@pytest.fixture
def emitted():
    """Return the [level] lines emitted by the check under test (state is module-global)."""
    start = len(state.results)
    return lambda: state.results[start:]


# ── the shipped corpus ───────────────────────────────────────────────────────


def test_corpus_ships_planted_cases_and_one_clean_case():
    cases = load_cases()
    assert {c["id"] for c in cases} == {
        "py-mixed-violations", "ts-mixed-violations", "ts-behavioral-correctness",
        "js-layout-gate", CLEAN_CASE,
    }
    clean = [c for c in cases if c.get("clean")]
    assert len(clean) == 1 and clean[0]["violations"] == []
    assert sum(len(c["violations"]) for c in violation_cases()) == 20
    assert isinstance(load_corpus_meta()["corpus_version"], int)


def test_every_case_ships_both_trees():
    for case in load_cases():
        assert (case["_dir"] / "base").is_dir()
        assert (case["_dir"] / "head").is_dir()


def test_planted_credential_carries_no_vendor_prefix():
    """The ts case plants a bare high-entropy value on purpose: a reviewer has to
    reason from the name and the outbound send, not pattern-match a token shape
    (and a vendor-formatted value trips secret scanning on every push)."""
    source = (corpus_root() / "cases" / "ts-mixed-violations" / "head" / "cache.ts").read_text(
        encoding="utf-8")
    line = next(ln for ln in source.splitlines() if "REPORTING_TOKEN =" in ln)
    value = line.split('"')[1]
    assert len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


# ── scorer validation (ideal / vague / decoy, and the clean case) ────────────


@pytest.mark.parametrize("case_id", sorted(IDEAL))
def test_ideal_review_detects_every_planted_defect(case_id):
    case = next(c for c in load_cases([case_id]))
    row = score_review(case, IDEAL[case_id])
    missed = [d["violation"] for d in row["detections"] if not d["detected"]]
    assert not missed, f"ideal review missed {missed}"
    assert row["detected"] == row["seeded"] == len(case["violations"])


@pytest.mark.parametrize("case_id", sorted(IDEAL))
def test_vague_review_detects_nothing(case_id):
    case = next(c for c in load_cases([case_id]))
    row = score_review(case, VAGUE)
    caught = [d["violation"] for d in row["detections"] if d["detected"]]
    assert not caught, f"vague prose scored {caught}"


@pytest.mark.parametrize("case_id", sorted(DECOY))
def test_decoy_review_names_every_symbol_and_still_detects_nothing(case_id):
    """A scorer that only greps for symbol names would score this 100%."""
    case = next(c for c in load_cases([case_id]))
    row = score_review(case, DECOY[case_id])
    caught = [d["violation"] for d in row["detections"] if d["detected"]]
    assert not caught, f"decoy prose scored {caught}"
    assert any(d["location_hit"] for d in row["detections"]), "decoy should hit locations"


def test_clean_case_praise_is_not_a_false_positive():
    case = next(c for c in load_cases([CLEAN_CASE]))
    assert score_review(case, PRAISE)["flagged"] is False


def test_clean_case_alarm_is_a_false_positive():
    case = next(c for c in load_cases([CLEAN_CASE]))
    assert score_review(case, ALARM)["flagged"] is True


NEGATED_CONCLUSIONS = [
    "No bugs found; looks good.",
    "I found no bug in this refactor.",
    "No blocking issues found.",
    "No security issues.",
    "No critical problems.",
    "I did not find any defect.",
    "重大な問題はありません。",
    "指摘なし、要修正箇所もありません。",
]

# The same vocabulary, asserted rather than denied — these must still cost the
# reviewer its precision score.
ASSERTED_FINDINGS = [
    "There is a critical bug in `_validate_discount`.",
    "No security issues, but there is a critical bug in foo().",
    "セキュリティ上の重大な問題は見当たりませんが、パフォーマンスに要修正箇所があります。",
]


@pytest.mark.parametrize("text", NEGATED_CONCLUSIONS)
def test_clean_case_negated_conclusion_is_not_a_false_positive(text):
    """A reviewer that correctly reports nothing must score zero findings —
    this case exists to measure precision, so miscounting here corrupts exactly
    the number it is for."""
    case = next(c for c in load_cases([CLEAN_CASE]))
    assert score_review(case, text)["flagged"] is False


@pytest.mark.parametrize("text", ASSERTED_FINDINGS)
def test_clean_case_asserted_finding_still_scores(text):
    """Negation-awareness must not become blanket suppression: a claim in the
    same sentence as a denial is still a claim."""
    case = next(c for c in load_cases([CLEAN_CASE]))
    assert score_review(case, text)["flagged"] is True


def test_clean_conclusion_scores_zero_findings_in_the_drill_row():
    row = build_drill_row({CLEAN_CASE: {"security-reviewer": NEGATED_CONCLUSIONS[0]}})
    score = row["scores"][0]
    assert (score["clean_diffs"], score["clean_findings"]) == (1, 0)
    assert score["clean_fp_rate"] == 0.0


def test_the_finding_is_the_window_not_a_character_count():
    """A concept discussed in a *different* finding does not credit this one.

    The scorer used to ask whether a concept appeared within 600 characters of the
    symbol anywhere in the document. The finding is the unit now, so the constant is
    gone and the boundary is one the reviewer drew: two findings are two claims.
    """
    violation = {"location": "mergeMetadata", "concept": r"\bany\b"}
    together = (
        "## Blocking\n\n"
        "### 1. mergeMetadata の戻り値が any になっている\n\n"
        "- Severity: High\n- File: `cache.ts:33`\n"
        "- Impact: any を返すため呼び出し側で型が失われる。\n"
    )
    assert score_violation(together, violation) == (True, True, True)

    apart = (
        "## Blocking\n\n"
        "### 1. mergeMetadata の命名が実態と合っていない\n\n"
        "- Severity: Low\n- File: `cache.ts:33`\n"
        "- Impact: 関数名が返り値の意味を説明していない。\n\n"
        "### 2. 別の関数の型が緩い\n\n"
        "- Severity: Low\n- File: `cache.ts:21`\n"
        "- Impact: any を使っているところがある。\n"
    )
    location_hit, concept_hit, detected = score_violation(apart, violation)
    assert (location_hit, concept_hit) == (True, True)
    assert detected is False, "the concept was raised, but not in the finding that located it"


def test_narration_that_asserts_nothing_scores_nothing():
    """The attack that made every earlier number meaningless.

    Measured before the finding scope existed: a paragraph naming each changed
    function and calling it correct scored 5/5 on `py-mixed-violations`, 5/5 on
    `ts-mixed-violations` and 4/5 on `ts-behavioral-correctness`. Every concept word
    was there, next to the right symbol, asserting nothing. Prose that is not a
    finding is not scored, so the whole family dies at the parser.
    """
    narrations = {
        "ts-behavioral-correctness": (
            "`dialogSubmitting` correctly reflects the confirming state, "
            "`canLeaveWithoutConfirmation` checks the pending lines, "
            "`recommendedQuantityLabel` shows the inventory unit as before, `dailySigma` "
            "aggregates the same way per day, and `mobileSortOptions` still offers every "
            "sort key with the toggle. Nothing here changes behaviour."
        ),
        "ts-mixed-violations": (
            "`REPORTING_TOKEN` is an environment credential, `summarize` keeps a backward "
            "compatible signature, `sortEntries` makes a defensive copy, `reportUsage` "
            "awaits the send, and `mergeMetadata` is type safe with no explicit any. "
            "Nothing here changes behaviour."
        ),
        "py-mixed-violations": (
            "`close_account` keeps the ownership authorization, `search_accounts` uses a "
            "parameterized query with no injection, `list_transactions` avoids the N+1 "
            "query, `record_audit` does not swallow the exception, and "
            "`test_audit_records_event` is not skipped. Nothing is wrong."
        ),
        "js-layout-gate": (
            "`FIT_SLACK_PX` keeps the tolerance at the declared box height, "
            "`LAYOUT_GATE_OFF` cannot disable the gate, `clampLine` wraps the paragraph "
            "instead of truncating it, `rendererAbsent` reports UNVERIFIED and never a "
            "pass, and `HEADING_BOX_PX` is not hard-coded. Nothing is wrong."
        ),
    }
    for case_id, text in narrations.items():
        case = next(c for c in load_cases([case_id]))
        row = score_review(case, text)
        caught = [d["violation"] for d in row["detections"] if d["detected"]]
        assert not caught, f"{case_id}: narration scored {caught}"
        assert row["unparsed"] is True, (
            f"{case_id}: a zero has to say whether the reviewer found nothing or the "
            f"scorer could not read the review"
        )


NARRATION_LINES = {
    "ts-behavioral-correctness": [
        "`dialogSubmitting` correctly reflects the confirming state",
        "`canLeaveWithoutConfirmation` checks the pending lines",
        "`recommendedQuantityLabel` shows the inventory unit as before",
        "`dailySigma` aggregates the same way per day",
        "`mobileSortOptions` still offers every sort key with the toggle",
    ],
    "ts-mixed-violations": [
        "`REPORTING_TOKEN` is an environment credential",
        "`summarize` keeps a backward compatible signature",
        "`sortEntries` makes a defensive copy",
        "`reportUsage` awaits the send",
        "`mergeMetadata` is type safe with no explicit any",
    ],
    "py-mixed-violations": [
        "`close_account` keeps the ownership authorization",
        "`search_accounts` uses a parameterized query with no injection",
        "`list_transactions` avoids the N+1 query",
        "`record_audit` does not swallow the exception",
        "`test_audit_records_event` is not skipped",
    ],
    "js-layout-gate": [
        "`FIT_SLACK_PX` keeps the tolerance at the declared box height",
        "`LAYOUT_GATE_OFF` cannot disable the gate",
        "`clampLine` wraps the paragraph instead of truncating it",
        "`rendererAbsent` reports UNVERIFIED and never a pass",
        "`HEADING_BOX_PX` is not hard-coded, it tracks the measured title height",
    ],
}


@pytest.mark.parametrize("case_id", sorted(NARRATION_LINES))
def test_narration_dressed_as_headings_still_scores_nothing(case_id):
    """Structure without grading is not a finding.

    An independent attempt to break this scorer took the narration paragraph that
    used to score 5/5, put `### ` in front of each sentence, and got 4/5 back. A
    heading is a section title; the contract fixes `Severity` on every finding, and
    grading is what turns a remark into a claim. Both shapes score nothing now.
    """
    case = next(c for c in load_cases([case_id]))
    lines = NARRATION_LINES[case_id]
    plain = "\n".join(f"{line}. Nothing here changes behaviour." for line in lines)
    headed = "## Non-blocking\n\n" + "\n".join(
        f"### {index}. {line}. Nothing changes.\n" for index, line in enumerate(lines, 1)
    )
    for shape, text in (("prose", plain), ("headings", headed)):
        row = score_review(case, text)
        caught = [d["violation"] for d in row["detections"] if d["detected"]]
        assert not caught, f"{case_id} {shape}: narration scored {caught}"


CORRECTNESS_CLAIMS = {
    "py-mixed-violations": [
        ("close_account requires the owner", "service.py:30"),
        ("search_accounts uses a parameterized query", "service.py:36"),
        ("list_transactions batches instead of N+1", "service.py:43"),
        ("record_audit propagates the exception", "service.py:59"),
        ("test_audit_records_event runs, skip removed", "test_service.py:41"),
    ],
    "ts-behavioral-correctness": [
        ("dialogSubmitting covers the previewing state", "workflow.ts:9"),
        ("canLeaveWithoutConfirmation checks shortfalls", "workflow.ts:13"),
        ("recommendedQuantityLabel uses the order unit", "workflow.ts:24"),
        ("dailySigma aggregates per day", "workflow.ts:30"),
        ("mobileSortOptions offers ASC and DESC", "workflow.ts:44"),
    ],
    "ts-mixed-violations": [
        ("REPORTING_TOKEN is an environment credential", "cache.ts:8"),
        ("summarize keeps a backward compatible signature", "cache.ts:24"),
        ("sortEntries makes a defensive copy", "cache.ts:21"),
        ("reportUsage awaits the send", "cache.ts:33"),
        ("mergeMetadata is type safe", "cache.ts:33"),
    ],
    "js-layout-gate": [
        ("FIT_SLACK_PX keeps the tolerance at the declared box height", "deck-fit.js:34"),
        ("LAYOUT_GATE_OFF cannot disable the gate", "deck-fit.js:59"),
        ("clampLine wraps the paragraph instead of truncating it", "deck-fit.js:11"),
        ("rendererAbsent reports UNVERIFIED and never a pass", "deck-fit.js:54"),
        ("HEADING_BOX_PX is not hard-coded, it tracks the measured title height",
         "deck-fit.js:26"),
    ],
}


@pytest.mark.parametrize(
    "case_id,open_at",
    [("py-mixed-violations", 3), ("ts-behavioral-correctness", 5), ("ts-mixed-violations", 5),
     ("js-layout-gate", 5)],
)
def test_the_deterministic_layer_alone_still_credits_a_claim_of_correctness(case_id, open_at):
    """What the *deterministic* layer alone still scores, recorded at its measured value.

    This number is no longer what the drill reports. The judge in ③-b closes the hole,
    and `test_drill_judge_adjudication.py` measures that with recorded verdicts. What
    stays pinned here is the size of the gap the judge is carrying: if this moves down,
    something deterministic closed part of it and the judge is carrying less than this
    test claims; if it moves up, the deterministic layer got looser and the judge is
    carrying more. Either way the judge's load changed and the calibration is stale.

    A finding that carries a `Severity`, a `File:` anchor on the defect's own line,
    one subject, and a sentence saying that subject is *correct* passes every test
    the scorer can run. It scores as a detection. Nothing here is a ceiling and this
    test does not claim one: it records where an open hole currently sits so that
    closing it fails loudly instead of passing quietly.

    Two attempts to close it deterministically were made and both removed after
    measurement. A cue list for correctness verbs cost six true positives across the
    ideal reviews. A clause-level negation test cost more: "`reportUsage` does not
    await `client.send`" is the natural way to report a missing await and scored
    zero under it, while the attacker only had to write "awaits the send" to walk
    past. Both punished honest reviewers and neither stopped the attack.

    The reason is not the finding scope, which does hold — the same claims as loose
    prose, and the same claims as bare headings, both score zero. It is that a
    `concept` is a list of topic words. "avoids the N+1 query" and "has an N+1
    query" share every topic word and make opposite claims. Reading the direction is
    a semantic call, and drill already scopes it: the judge in ③-b, given the
    finding and the seed's `summary`.
    """
    case = next(c for c in load_cases([case_id]))
    text = "## Non-blocking\n\n" + "\n".join(
        f"### {index}. {claim}\n\n- Severity: Low\n- File: `{anchor}`\n"
        f"- Impact: {claim}, so behaviour is unchanged.\n"
        for index, (claim, anchor) in enumerate(CORRECTNESS_CLAIMS[case_id], 1)
    )
    row = score_review(case, text)
    assert row["detected"] == open_at, (
        f"{case_id}: the open attack moved from {open_at} to {row['detected']}. If it "
        f"went down, say what closed it; if it went up, something got looser."
    )
    # A case already at its seed count can only move down here, so for those this is a
    # one-sided tripwire: it catches a regex being tightened, not one being loosened.
    assert open_at <= row["seeded"]


@pytest.mark.parametrize("case_id", sorted(CORRECTNESS_CLAIMS))
def test_the_same_claims_without_the_contract_score_nothing(case_id):
    """What the finding scope does hold: the same words, not written as findings."""
    case = next(c for c in load_cases([case_id]))
    claims = [claim for claim, _ in CORRECTNESS_CLAIMS[case_id]]
    prose = "\n".join(f"{claim}, so behaviour is unchanged." for claim in claims)
    headings = "## Non-blocking\n\n" + "\n".join(
        f"### {index}. {claim}\n" for index, claim in enumerate(claims, 1)
    )
    for shape, text in (("prose", prose), ("ungraded headings", headings)):
        row = score_review(case, text)
        caught = [d["violation"] for d in row["detections"] if d["detected"]]
        assert not caught, f"{case_id} as {shape}: scored {caught}"


def test_reporting_an_absence_is_not_penalised_for_saying_not():
    """A missing call is reported by saying it is missing. Both phrasings must score.

    A negation test on the concept was tried here and removed: it made "does not
    await" a miss and "is missing an await" a hit, for the same defect, in the same
    finding, from the same reviewer. A rate that moves on that is measuring grammar.
    """
    case = next(c for c in load_cases(["ts-mixed-violations"]))
    violation = next(v for v in case["violations"] if v["id"] == "floating-promise")
    for body in (
        "`reportUsage` does not await `client.send`.",
        "`reportUsage` is missing an await on `client.send`.",
        "`client.send` の結果が await されていない。",
    ):
        text = (
            "## Blocking\n\n### 1. 送信結果が待機されていない\n\n"
            f"- Severity: High\n- File: `cache.ts:33`\n- Impact: {body}\n"
        )
        _, _, detected = score_violation(text, violation, case=case)
        assert detected, f"the same defect went unscored when phrased: {body}"


def test_the_contract_required_anchor_does_not_cost_a_point():
    """Following the contract must not lose points — it did, and that is the bug.

    `hardcoded-secret` shares a changed hunk with three other seeds, and the
    ambiguity test used to be built from one seed's siblings rather than from all of
    them. The same anchor was ambiguous seen from one defect and unambiguous seen
    from another, so a finding carrying the `File:` the contract requires scored
    nothing while the same finding without it scored. Found by an independent attempt
    to break this scorer, not by its author.
    """
    case = next(c for c in load_cases(["ts-mixed-violations"]))
    violation = next(v for v in case["violations"] if v["id"] == "hardcoded-secret")
    impact = "`REPORTING_TOKEN` is a hard-coded credential committed to source."
    without = (
        "## Blocking\n\n### 1. 機密が直書きされている\n\n"
        f"- Severity: Critical\n- Impact: {impact}\n"
    )
    with_anchor = (
        "## Blocking\n\n### 1. 機密が直書きされている\n\n"
        f"- Severity: Critical\n- File: `cache.ts:8`\n- Impact: {impact}\n"
    )
    assert score_violation(without, violation, case=case)[2] is True
    assert score_violation(with_anchor, violation, case=case)[2] is True, (
        "the anchor the contract requires must not be the thing that loses the point"
    )


def test_an_absolute_anchor_resolves_against_the_materialized_workspace():
    """Drill materializes a case into a throwaway directory; reviewers write that path.

    Measured on a real review: every finding carried `/tmp/drill-x9/repo/...` and the
    whole anchor path went silent, 0/5 where the same text relative scored 5/5. Only
    a prefix the caller vouches for is stripped — a suffix rule would let
    `other/workflow.ts` locate a defect planted in `workflow.ts`.
    """
    case = next(c for c in load_cases(["ts-behavioral-correctness"]))
    workspace = "/tmp/drill-abc123/repo"
    text = (
        "## Blocking\n\n### 1. ORDER モードで在庫単位が表示される\n\n"
        f"- Severity: High\n- File: `{workspace}/workflow.ts:24`\n"
        "- Impact: 発注単位ではなく在庫単位のラベルが付く。\n"
    )
    violation = next(v for v in case["violations"] if v["id"] == "quantity-unit-confusion")
    assert score_violation(text, violation, case=case)[2] is False, (
        "without a workspace the absolute path is simply a different path"
    )
    assert score_violation(text, violation, case=case, workspace=workspace)[2] is True
    # And the suffix rule stays closed: a same-basename path elsewhere under the
    # workspace is still a different file.
    elsewhere = text.replace(f"{workspace}/workflow.ts:24", f"{workspace}/other/workflow.ts:24")
    assert score_violation(elsewhere, violation, case=case, workspace=workspace)[2] is False


def test_filing_a_finding_on_the_clean_case_is_the_false_positive():
    """On a clean diff the false positive is the *filing*, not the wording.

    `drill.md` defines `clean_fp_rate` as the share of clean diffs answered with a
    finding or a REJECT, and `add_false_positive_guard` keys its ten-percent
    threshold off that number. Judging the words instead was measured to lose real
    false positives: three fabricated `Severity: High` blocking findings, written
    without any alarm vocabulary, scored clean while the rule they replaced caught
    them. A persona inventing high-severity findings on correct code would have
    measured at a zero-percent false-positive rate and the guard would never fire.
    """
    case = next(c for c in load_cases([CLEAN_CASE]))
    fabricated = "## Blocking\n\n" + "\n".join(
        f"### {index}. 変更で挙動が変わっている\n\n"
        f"- Severity: High\n- File: `service.py:{10 + index}`\n"
        f"- Impact: 金額がずれる。\n"
        for index in (1, 2, 3)
    )
    assert score_review(case, fabricated)["flagged"] is True, (
        "a finding filed against a clean diff is a false positive whatever it says"
    )
    row = build_drill_row({CLEAN_CASE: {"security-reviewer": fabricated}})
    assert row["scores"][0]["clean_fp_rate"] == 1.0


def test_saying_there_is_nothing_to_report_is_not_a_false_positive():
    """The one behaviour the clean case exists to reward has to stay free.

    The contract says a section with nothing in it is dropped heading and all, and a
    review with neither leaves the single line `所見なし`. That files no finding.
    """
    case = next(c for c in load_cases([CLEAN_CASE]))
    for text in ("所見なし\n", PRAISE):
        assert score_review(case, text)["flagged"] is False, (
            f"a review that filed nothing was counted as a false positive: {text[:40]!r}"
        )


def test_a_row_says_which_reviews_it_could_not_read():
    """A zero has to distinguish the reviewer from the scorer, on the row too.

    `score_review` reports `unparsed`, and the row used to drop it — so a review the
    scorer could not read arrived at the scoreboard as a reviewer that found nothing.
    """
    prose = (
        "I read through cache.ts. The change touches `mergeMetadata` and the "
        "surrounding helpers, and the formatting matches the project style."
    )
    row = build_drill_row({"ts-mixed-violations": {"security-reviewer": prose}})
    assert row["scores"][0]["unreadable_cases"] == ["ts-mixed-violations"]

    contract_shaped = (
        "## Blocking\n\n### 1. 機密が直書きされている\n\n"
        "- Severity: Critical\n- File: `cache.ts:8`\n"
        "- Impact: `REPORTING_TOKEN` がソースにコミットされている。\n"
    )
    row = build_drill_row({"ts-mixed-violations": {"security-reviewer": contract_shaped}})
    assert "unreadable_cases" not in row["scores"][0]


def test_one_finding_cannot_claim_two_defects():
    """A finding that points at two seeds has not said which one it found.

    Measured on the previous scorer: one finding listing five point anchors and one
    sentence touching all five subjects scored 5/5 with no symbol named, and the same
    sentence with the answer key's five identifiers pasted in scored 5/5 as well. The
    contract asks for one `File:` anchor per finding, and one problem per finding.
    """
    case = next(c for c in load_cases(["ts-behavioral-correctness"]))
    salad = ("二重に送信できる。未完了のまま離脱できる。在庫単位と発注単位が違う。"
             "集計の粒度が合わない。並び順の切り替えが効かない。")
    for locator in (
        " ".join(f"`workflow.ts:{line}`" for line in (9, 13, 24, 30, 44)),
        " ".join(f"`{symbol}`" for symbol in (
            "dialogSubmitting", "canLeaveWithoutConfirmation", "recommendedQuantityLabel",
            "dailySigma", "mobileSortOptions")),
    ):
        text = (f"## Blocking\n\n### 1. 変更範囲の複数箇所に不具合がある\n\n"
                f"- Severity: High\n- File: {locator}\n- Impact: {salad}\n")
        row = score_review(case, text, perspective="behavioral-correctness")
        caught = [d["violation"] for d in row["detections"] if d["detected"]]
        assert not caught, f"an undifferentiated finding credited {caught}"


def test_severity_and_blocking_are_read_off_the_contract():
    """Both were left absent because the scorer had nowhere structured to read them."""
    case = next(c for c in load_cases(["ts-behavioral-correctness"]))
    text = (
        "## Blocking\n\n"
        "### 1. ORDER モードで発注単位ではなく在庫単位が表示される\n\n"
        "- Severity: High\n- File: `workflow.ts:24`\n"
        "- Impact: 単位の取り違えが発注数量の誤入力に直結する。\n"
    )
    row = score_review(case, text, perspective="behavioral-correctness")
    hit = next(d for d in row["detections"] if d["violation"] == "quantity-unit-confusion")
    assert hit["detected"] is True
    assert hit["severity_given"] == "high" and hit["severity"] == "high"
    assert hit["blocking_given"] is True and hit["expected_blocking"] == "Blocking"
    assert row["severity_accuracy"] == 1.0 and row["blocking_accuracy"] == 1.0


def test_location_and_concept_hits_are_reported_separately():
    """"named the symbol but never said what was wrong" is a distinct failure."""
    case = next(c for c in load_cases(["ts-mixed-violations"]))
    row = score_review(case, DECOY["ts-mixed-violations"])
    by_id = {d["violation"]: d for d in row["detections"]}
    assert by_id["caller-array-mutation"]["location_hit"] is True
    assert by_id["caller-array-mutation"]["detected"] is False


# ── materialization ─────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required to materialize a case")
def test_materialize_lays_head_over_a_committed_base(tmp_path):
    case = next(c for c in load_cases(["py-mixed-violations"]))
    workspace = materialize_case(case["_dir"], tmp_path / "ws")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=workspace,
                            capture_output=True, text=True, check=True).stdout
    assert " M service.py" in status, status
    committed = subprocess.run(["git", "show", "HEAD:service.py"], cwd=workspace,
                               capture_output=True, text=True, check=True).stdout
    assert "close_account" not in committed  # base/ is what is committed
    assert "close_account" in (workspace / "service.py").read_text(encoding="utf-8")


# ── the drill-results row ───────────────────────────────────────────────────


def _reviews(persona: str) -> dict:
    return {
        "py-mixed-violations": {persona: IDEAL["py-mixed-violations"]},
        CLEAN_CASE: {persona: PRAISE},
    }


def _judged(*args, **kwargs):
    """`build_drill_row` as a real drill run calls it: with a judge that answered.

    These tests are about the row builder's own mechanics — attribution, what a miss
    records, how the clean case counts — none of which the judge touches. But a row
    scored without one is deliberately not a measurement: its detection fields are
    renamed with an `_unadjudicated` suffix so that `digest`, `dashboard` and `fleet`,
    which sum them and print a rate, skip it. Asserting on `detected` therefore means
    asking for the shape a real run produces, which is this one.
    """
    def adjudicate(case, violation, finding):
        return "ASSERTS"
    adjudicate.provenance = lambda: {
        "provider": "fake", "model": None, "prompt_version": "test", "ledger": None,
        "ledger_sha256": None, "offline": False, "calls": 1, "cache_hits": 0,
    }
    return build_drill_row(*args, adjudicate=adjudicate, **kwargs)


def test_row_attributes_planted_defects_by_perspective():
    row = _judged(_reviews("security-reviewer"))
    assert row["corpus"] == "fixture"
    score = row["scores"][0]
    assert score["attribution"] == "perspective"
    # only the two security defects of that case are this reviewer's to catch
    assert (score["detected"], score["seeded"]) == (2, 2)
    assert score["clean_diffs"] == 1 and score["clean_findings"] == 0
    assert score["clean_fp_rate"] == 0.0


def test_behavioral_correctness_reviewer_has_five_accountable_seeds():
    row = _judged({
        "ts-behavioral-correctness": {
            "behavioral-correctness-reviewer": IDEAL["ts-behavioral-correctness"],
        },
    })
    score = row["scores"][0]
    assert score["attribution"] == "perspective"
    assert (score["detected"], score["seeded"]) == (5, 5)


def test_missed_defects_are_recorded_by_class_and_in_detail():
    row = _judged({"py-mixed-violations": {"security-reviewer": VAGUE}})
    score = row["scores"][0]
    assert (score["detected"], score["seeded"]) == (0, 2)
    assert score["missed"] == ["security", "security"]
    assert {d["violation"] for d in score["missed_detail"]} == {
        "missing-authz-on-sibling", "sql-injection",
    }


def test_reviewer_outside_the_corpus_perspectives_is_scored_on_everything():
    row = _judged(_reviews("native-code-review"))
    score = row["scores"][0]
    assert score["attribution"] == "all"
    assert (score["detected"], score["seeded"]) == (5, 5)


def test_clean_case_finding_counts_as_a_false_positive():
    """The clean case needs no judge: nothing is planted, so nothing has a direction.

    The row still carries no `seeded`, because it was scored without one and that is
    what an unjudged row looks like now. Nothing is lost — a clean-only run has no
    detection rate to offer either way.
    """
    row = build_drill_row({CLEAN_CASE: {"security-reviewer": ALARM}})
    score = row["scores"][0]
    assert (score["clean_diffs"], score["clean_findings"]) == (1, 1)
    assert score["clean_fp_rate"] == 1.0
    assert "seeded" not in score and score["seeded_unadjudicated"] == 0


def test_only_explanation_quality_is_still_left_to_the_judge():
    """A fabricated 0.0 would read as "measured, scored zero" — so absent means absent.

    `severity_accuracy` and `blocking_accuracy` used to be on this list. They are not
    judged now and never needed to be: the contract fixes `Severity` to four values
    and splits `## Blocking` from `## Non-blocking`, so scoring one finding at a time
    gives the scorer somewhere to read both. They were absent because scoring a whole
    document had nowhere to read them from. `explanation_quality` is a different kind
    of question — whether an Impact and a fix are specific enough to act on — and it
    still needs the judge in drill ③-b.
    """
    score = _judged(_reviews("security-reviewer"))["scores"][0]
    assert "explanation_quality" not in score
    assert score["severity_accuracy"] == 1.0
    assert score["blocking_accuracy"] == 1.0


def test_row_is_one_line_json_and_carries_the_corpus_version():
    row = _judged(_reviews("security-reviewer"))
    line = json.dumps(row, ensure_ascii=False)
    assert "\n" not in line
    assert row["corpus_version"] == load_corpus_meta()["corpus_version"]
    assert row["seeds"] == row["valid_seeds"] == 5


# ── validate.py integrity check ─────────────────────────────────────────────


def test_shipped_fixture_corpus_passes_integrity(emitted):
    check_fixture_corpus_integrity()
    lines = emitted()
    assert any(line.startswith("[PASS]") and "drill fixture corpus" in line for line in lines)
    assert not any(line.startswith(("[WARN]", "[FAIL]")) for line in lines)


def _synthetic_corpus(tmp_path):
    root = tmp_path / "fixture"
    (root / "cases" / "c1" / "base").mkdir(parents=True)
    (root / "cases" / "c1" / "head").mkdir(parents=True)
    (root / "cases" / "clean" / "base").mkdir(parents=True)
    (root / "cases" / "clean" / "head").mkdir(parents=True)
    (root / "corpus.json").write_text(json.dumps({"corpus": "fixture", "corpus_version": 1}),
                                      encoding="utf-8")
    (root / "cases" / "c1" / "case.json").write_text(json.dumps({
        "id": "c1", "clean": False,
        "violations": [{"id": "v1", "category": "security", "severity": "high",
                        "perspectives": ["security"], "location": "x", "concept": "y"}],
    }), encoding="utf-8")
    (root / "cases" / "clean" / "case.json").write_text(
        json.dumps({"id": "clean", "clean": True, "violations": []}), encoding="utf-8")
    return root


def test_integrity_passes_on_a_well_formed_synthetic_corpus(tmp_path, emitted):
    check_fixture_corpus_integrity(_synthetic_corpus(tmp_path))
    assert any(line.startswith("[PASS]") for line in emitted())


def test_integrity_warns_when_the_clean_case_is_gone(tmp_path, emitted):
    root = _synthetic_corpus(tmp_path)
    shutil.rmtree(root / "cases" / "clean")
    check_fixture_corpus_integrity(root)
    warns = [line for line in emitted() if line.startswith("[WARN]")]
    assert any("clean case" in line for line in warns)


def test_integrity_warns_on_an_uncompilable_answer_key(tmp_path, emitted):
    root = _synthetic_corpus(tmp_path)
    path = root / "cases" / "c1" / "case.json"
    case = json.loads(path.read_text(encoding="utf-8"))
    case["violations"][0]["concept"] = "(unclosed"
    path.write_text(json.dumps(case), encoding="utf-8")
    check_fixture_corpus_integrity(root)
    assert any("not a valid regex" in line for line in emitted() if line.startswith("[WARN]"))


def test_integrity_warns_on_a_missing_tree_and_never_fails(tmp_path, emitted):
    root = _synthetic_corpus(tmp_path)
    shutil.rmtree(root / "cases" / "c1" / "head")
    check_fixture_corpus_integrity(root)
    lines = emitted()
    assert any("head/ tree is missing" in line for line in lines if line.startswith("[WARN]"))
    assert not any(line.startswith("[FAIL]") for line in lines)  # guidance, not schema


NEUTRAL_MECHANISM = {
    "js-layout-gate": [
        ("clampLine truncates the paragraph to perLine characters and appends an ellipsis",
         "deck-fit.js:11"),
        ("HEADING_BOX_PX is a module-level constant used for the heading box",
         "deck-fit.js:26"),
        ("FIT_SLACK_PX is the tolerance constant read by overflowsBox", "deck-fit.js:34"),
        ("rendererAbsent returns true when the renderer is unavailable", "deck-fit.js:54"),
        ("LAYOUT_GATE_OFF is the switch that disables the gate", "deck-fit.js:59"),
    ],
}


@pytest.mark.parametrize("case_id", sorted(NEUTRAL_MECHANISM))
def test_describing_the_mechanism_and_claiming_nothing_is_wrong(case_id):
    """The shape a tamper corpus is uniquely exposed to, recorded at its measured value.

    Elsewhere in this corpus the symbol carries a domain name and the defect is in the
    logic, so a neutral sentence about the symbol shares no vocabulary with the answer
    key: this shape scores 0/5 on both ts cases and 1/5 on py-mixed. Here the symbols
    *are* the defect, and an independent test reviewer took 5/5 with five sentences that
    describe each mechanism and claim nothing is wrong.

    Two were closed at the answer key by dropping words that only ever describe. The
    other three ride on `tolerance`, `disable` and `truncate`, which an honest reviewer
    needs, so they are left for the judge and calibrated as `waiver` — measured in
    `test_drill_judge_adjudication.py`, not assumed here.
    """
    case = next(c for c in load_cases([case_id]))
    text = "## Non-blocking\n\n" + "\n".join(
        f"### {index}. {claim}\n\n- Severity: Low\n- File: `{anchor}`\n"
        f"- Impact: {claim}. 現状のままで問題ない。\n"
        for index, (claim, anchor) in enumerate(NEUTRAL_MECHANISM[case_id], 1))
    row = score_review(case, text)
    caught = {d["violation"] for d in row["detections"] if d["detected"]}
    assert caught == {"slack-added-to-overflow-test", "gate-disabled-by-switch",
                      "body-clipped-to-fit"}, (
        "the deterministic layer's share of this class moved; if it grew, a concept "
        "regex started crediting pure description, and if it shrank say which word went "
        "and check an honest reviewer did not lose it")


def test_the_layout_gate_seeds_never_share_a_line():
    """Pairwise disjoint owned lines, pinned because one blank line would end it.

    Where several seeds sit in one changed hunk none of them owns a line alone and no
    anchor reaches any of them — the corpus README records that cost for the two mixed
    cases. This case was written to avoid it, and it stays avoided only by accident of
    spacing: replacing the blank line between `rendererAbsent` and `enforce` fuses their
    hunks, after which `deck-fit.js:59` is owned by two seeds and scores neither. The
    anchor path would die silently, because the symbol path still credits both.
    """
    case = next(c for c in load_cases(["js-layout-gate"]))
    owned = {}
    for violation in case["violations"]:
        lines = set()
        for spans in _defect_line_ranges(case, violation).values():
            for start, end in spans:
                lines.update(range(start, end + 1))
        assert lines, f"{violation['id']} owns no line at all"
        owned[violation["id"]] = lines
    for vid, lines in owned.items():
        others = set().union(*(v for k, v in owned.items() if k != vid))
        assert not (lines & others), (
            f"{vid} now shares {sorted(lines & others)} with another seed; an anchor in "
            f"the overlap scores neither of them")
