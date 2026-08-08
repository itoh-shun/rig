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
from rig_workbench.workbench.detection_corpus import (build_drill_row, corpus_root, load_cases,
                                                      load_corpus_meta, materialize_case,
                                                      score_review, score_violation)

IDEAL = {
    "py-mixed-violations": """
## Findings

### CRITICAL: close_account is missing the ownership check
`close_account` in service.py never calls `_require_owner`, unlike its sibling
`get_account`. Any authenticated actor can close another user's account. This is
a broken access control / authorization hole.

### CRITICAL: SQL injection in search_accounts
`search_accounts` interpolates `owner_name` directly into the query string with
an f-string. Use a parameterized query with a bind placeholder instead.

### HIGH: N+1 query introduced in list_transactions
`list_transactions` now selects the ids and then issues one query per row inside
a loop. That is a classic N+1 pattern; restore the single batch query.

### HIGH: record_audit swallows every failure
`record_audit` wraps the write in a bare `except Exception: pass`, which
silently discards audit failures. Do not suppress the error.

### MEDIUM: test_audit_records_event was disabled
`test_audit_records_event` is now decorated with `mark.skip` marked flaky, so
the audit path has no coverage. Skipping is not a fix.
""",
    "ts-mixed-violations": """
## Findings

### CRITICAL: hardcoded credential
`REPORTING_TOKEN` is a high-entropy credential committed in source and
transmitted to a third party. Move it to an environment variable and rotate it.

### HIGH: summarize is a breaking API change
`summarize` gained a required second parameter, which breaks every existing
caller. Make it optional to preserve backward compatibility.

### HIGH: sortEntries now mutates the caller's array
`sortEntries` lost its defensive copy (the spread), so `.sort()` reorders the
caller's array in place. That is an unexpected side effect.

### HIGH: floating promise in reportUsage
`reportUsage` calls `client.send` without `await` or a `.catch`, so a rejection
is unhandled and failures disappear silently.

### MEDIUM: any erases type safety
`mergeMetadata` accepts and returns `any`, which removes type safety from a
public export. Use a generic or `unknown` with narrowing.
""",
    "ts-behavioral-correctness": """
## Findings

### HIGH: preview中に確認操作が再実行できる
`dialogSubmitting` が `confirming` しか見ておらず preview 中を loading として扱いません。preview API が in-flight の間に二重 submit や close が可能になるので、previewing || confirming を busy state にする必要があります。

### HIGH: 部分成功後の未完了状態を確認なしで離脱できる
`canLeaveWithoutConfirmation` が lines だけを見ており、shortfalls が残る partial success 状態を無視しています。lines=0 / shortfalls>0 では事後処理が未完了なので cancel/leave に確認が必要です。

### HIGH: 発注数量に在庫単位を付けている
`recommendedQuantityLabel` は ORDER の recommendedQuantity にも常に inventoryUnit を表示しています。ORDER は orderUnit の数量なので、発注単位と在庫単位の unit mismatch になります。

### HIGH: 日次標準偏差をイベント粒度で計算している
`dailySigma` が same day のイベントを日ごとに aggregate せず、生イベントの sumSquares から分散を出しています。日次需要の統計なら同日複数イベントを先に group しないと集約粒度が変わります。

### HIGH: モバイルで降順へ切り替えられない
`mobileSortOptions` は key しか選べず、`applyMobileSortSelection` は同じ key の再選択で ASC/DESC を toggle する設計です。native select の onChange は同じ value の再選択では発火しないため DESC が unreachable です。
""",
}

VAGUE = """
I reviewed the changes. Overall the structure looks reasonable and the code is
readable. There may be some edge cases worth considering, and I would suggest
adding a few more tests if any behaviour is not yet covered. Nothing stood out
as an obvious blocker, though a second pair of eyes on the error handling could
be worthwhile.
"""

DECOY = {
    "py-mixed-violations": """
I read through service.py and test_service.py. The change touches
`close_account`, `search_accounts`, `list_transactions`, `record_audit`, and
`test_audit_records_event`. The diff is moderate in size and the naming is
consistent with the rest of the module. Formatting matches the project style.
""",
    "ts-mixed-violations": """
I read through cache.ts. The change touches `REPORTING_TOKEN`, `summarize`,
`sortEntries`, `reportUsage`, and `mergeMetadata`. Everything is exported from
the same module and the naming is consistent. Formatting matches the project
style and the file remains small.
""",
    "ts-behavioral-correctness": """
I read through workflow.ts. The change touches `dialogSubmitting`,
`canLeaveWithoutConfirmation`, `recommendedQuantityLabel`, `dailySigma`,
`mobileSortOptions`, and `applyMobileSortSelection`. The functions are small,
naming is consistent, and the module remains easy to scan.
""",
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
        "py-mixed-violations", "ts-mixed-violations", "ts-behavioral-correctness", CLEAN_CASE,
    }
    clean = [c for c in cases if c.get("clean")]
    assert len(clean) == 1 and clean[0]["violations"] == []
    assert sum(len(c["violations"]) for c in violation_cases()) == 15
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


def test_proximity_is_required_not_just_co_occurrence():
    violation = {"location": "mergeMetadata", "concept": r"\bany\b"}
    near = "mergeMetadata takes any and returns any."
    far = "mergeMetadata is exported." + ("filler. " * 200) + "I did not find any problems."
    assert score_violation(near, violation) == (True, True, True)
    location_hit, concept_hit, detected = score_violation(far, violation)
    assert (location_hit, concept_hit) == (True, True)
    assert detected is False


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


def test_row_attributes_planted_defects_by_perspective():
    row = build_drill_row(_reviews("security-reviewer"))
    assert row["corpus"] == "fixture"
    score = row["scores"][0]
    assert score["attribution"] == "perspective"
    # only the two security defects of that case are this reviewer's to catch
    assert (score["detected"], score["seeded"]) == (2, 2)
    assert score["clean_diffs"] == 1 and score["clean_findings"] == 0
    assert score["clean_fp_rate"] == 0.0


def test_behavioral_correctness_reviewer_has_five_accountable_seeds():
    row = build_drill_row({
        "ts-behavioral-correctness": {
            "behavioral-correctness-reviewer": IDEAL["ts-behavioral-correctness"],
        },
    })
    score = row["scores"][0]
    assert score["attribution"] == "perspective"
    assert (score["detected"], score["seeded"]) == (5, 5)


def test_missed_defects_are_recorded_by_class_and_in_detail():
    row = build_drill_row({"py-mixed-violations": {"security-reviewer": VAGUE}})
    score = row["scores"][0]
    assert (score["detected"], score["seeded"]) == (0, 2)
    assert score["missed"] == ["security", "security"]
    assert {d["violation"] for d in score["missed_detail"]} == {
        "missing-authz-on-sibling", "sql-injection",
    }


def test_reviewer_outside_the_corpus_perspectives_is_scored_on_everything():
    row = build_drill_row(_reviews("native-code-review"))
    score = row["scores"][0]
    assert score["attribution"] == "all"
    assert (score["detected"], score["seeded"]) == (5, 5)


def test_clean_case_finding_counts_as_a_false_positive():
    row = build_drill_row({CLEAN_CASE: {"security-reviewer": ALARM}})
    score = row["scores"][0]
    assert (score["clean_diffs"], score["clean_findings"]) == (1, 1)
    assert score["clean_fp_rate"] == 1.0
    assert (score["detected"], score["seeded"]) == (0, 0)


def test_unmeasured_metrics_are_absent_rather_than_zero():
    """severity/blocking/explanation quality need the judge step; a fabricated
    0.0 would read as "measured, scored zero"."""
    score = build_drill_row(_reviews("security-reviewer"))["scores"][0]
    for key in ("severity_accuracy", "blocking_accuracy", "explanation_quality"):
        assert key not in score


def test_row_is_one_line_json_and_carries_the_corpus_version():
    row = build_drill_row(_reviews("security-reviewer"))
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
