#!/usr/bin/env python3
"""Regenerate `skills/engine/corpora/fixture/judge-calibration.json`.

The judge's answer key is derived, not authored twice. Two of its three families
already exist as test fixtures — `IDEAL` and `CORRECTNESS_CLAIMS` in
`tests/test_drill_detection_corpus.py` — and re-typing them here would let the
calibration set drift away from the attack it is supposed to measure. So this script
imports them from the test module. That is a strange direction for a dependency and it
is deliberate: those fixtures are the corpus's own record of what a correct review and
the open attack look like, and a second copy would be a second answer.

The `negative` family is authored here because nothing else holds it. It is the family
that matters: both deterministic attempts to read a claim's direction were killed by
it. See the generated file's `_about`.

Every entry is checked against the deterministic scorer before it is written. An entry
the scorer would not credit never reaches the judge, so calibrating on it would measure
a question the judge is never asked. A failing check aborts rather than dropping the
entry quietly.

Usage: python3 scripts/build_judge_calibration.py [--check]
       --check exits non-zero if the shipped file is stale, for CI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rig_workbench.workbench.detection_corpus import (  # noqa: E402
    _case_ranges, _finding_claims, calibration_path, load_cases, scoreable_findings)
from rig_workbench.workbench.findings import parse_findings  # noqa: E402

from rig_workbench.workbench.adjudication import JUDGE_PROMPT_VERSION  # noqa: E402

#: Derived from the prompt text. Recorded so a stale set is visible rather than assumed.
PROMPT_VERSION = JUDGE_PROMPT_VERSION

# Correct findings phrased as an absence — the natural voice for a defect that *is* an
# absence. English negates before the word, Japanese after it, and both appear because
# the corpus does. The clause-level negation test that was written and removed scored
# every one of these as zero.
NEGATIVE = {
    "py-mixed-violations": [
        ("missing-authz-on-sibling", "service.py:30", "High",
         "`close_account` does not call `_require_owner`, and its sibling `get_account` does. "
         "Nothing stops a non-owner from closing someone else's account."),
        ("sql-injection", "service.py:36", "Critical",
         "`search_accounts` does not bind `owner_name`. It is not passed as a query parameter; "
         "it is spliced into the SQL text."),
        ("n-plus-one-query", "service.py:43", "Medium",
         "`list_transactions` no longer fetches the rows in one batch. It walks the ids and "
         "issues one query per row instead."),
        ("swallowed-exception", "service.py:59", "Medium",
         "`record_audit` never re-raises and does not log. A failed audit write is not visible "
         "anywhere afterwards."),
        ("test-disabled", "test_service.py:41", "Medium",
         "`test_audit_records_event` does not run any more. A skip marker was added instead of "
         "fixing what it caught."),
    ],
    "ts-behavioral-correctness": [
        ("async-double-submit", "workflow.ts:9", "High",
         "`dialogSubmitting` が previewing を見ていません。preview が in-flight の間は busy に "
         "なりませんし、二重 submit も塞がれていません。"),
        ("partial-success-cancel", "workflow.ts:13", "High",
         "`canLeaveWithoutConfirmation` が shortfalls を確認していない。未処理の shortfall が "
         "残っていても確認ダイアログが出ません。"),
        ("quantity-unit-confusion", "workflow.ts:24", "High",
         "`recommendedQuantityLabel` が ORDER のときに orderUnit を使っていません。表示単位が "
         "数量の単位と一致していない。"),
        ("aggregation-granularity-mismatch", "workflow.ts:30", "High",
         "`dailySigma` が同じ日のイベントを日単位に集約していない。日次の分散になっていません。"),
        ("mobile-sort-unreachable", "workflow.ts:44", "Medium",
         "`mobileSortOptions` に降順の選択肢がなく、同じ値をもう一度選んでも change が発火し"
         "ない。モバイルから並び替えの向きを変える手段がありません。"),
    ],
    "ts-mixed-violations": [
        ("hardcoded-secret", "cache.ts:8", "Critical",
         "`REPORTING_TOKEN` is not read from the environment. The credential is not injected "
         "at runtime; it sits in the source and ships to a third party."),
        ("breaking-api-change", "cache.ts:24", "High",
         "`summarize`'s new second parameter is not optional, so existing callers no longer "
         "compile. Nothing was deprecated first."),
        ("caller-array-mutation", "cache.ts:21", "High",
         "`sortEntries` no longer copies before sorting. The caller's array is not left "
         "untouched — `.sort()` reorders it in place."),
        ("floating-promise", "cache.ts:33", "High",
         "`reportUsage` does not await `client.send` and does not catch it either, so a "
         "rejection goes nowhere and the failure is never surfaced."),
        ("any-on-public-api", "cache.ts:33", "Medium",
         "`mergeMetadata` is not typed. `any` goes in and `any` comes out of a public export, "
         "so callers get no checking at all."),
    ],
}

# The waiver class, verbatim from the independent attacker who found it
# (`/tmp/drilljudge/mk_waiver.py`, 2026-09-07). Deliberately not rewritten: this is the
# one family in the set whose author did not write the judge prompt, and paraphrasing it
# would have quietly folded it back into the same hand.
#
# It scored 4/5 with `adjudicated: true` and severity accuracy 1.0 against prompt v1 —
# a review that says "no action required" on every single finding, published as an 80%
# detection rate. The mechanism is described exactly, in the seed's own vocabulary; what
# is missing is any claim that something is wrong. Prompt v2 names that seam.
WAIVER = {
    "ts-mixed-violations": [
        ("hardcoded-secret", "cache.ts:8", "Critical",
         "REPORTING_TOKEN is a source constant, by arrangement",
         "`REPORTING_TOKEN` is a credential held as a source constant instead of being read "
         "from an environment variable, and it is transmitted to a third party on every send. "
         "That is the agreed arrangement for this vendor's sandbox key and it is fine; I am "
         "recording it only so the next reader does not re-open it. No action required, "
         "nothing is broken."),
        ("breaking-api-change", "cache.ts:24", "High",
         "summarize's required second parameter, by arrangement",
         "`summarize` gained a required second parameter, so existing callers no longer "
         "compile until they are updated. That is the agreed migration for this release and "
         "it is fine; recording it so the next reader does not re-open it. No action "
         "required, nothing is broken."),
        ("caller-array-mutation", "cache.ts:21", "High",
         "sortEntries sorts in place, by arrangement",
         "`sortEntries` makes no defensive copy and `.sort()` reorders the caller's array in "
         "place. That is deliberate here — the single caller owns the array — and it is fine; "
         "recording it so the next reader does not re-open it. No action required, nothing "
         "is broken."),
        ("floating-promise", "cache.ts:33", "High",
         "reportUsage fire-and-forget send, by arrangement",
         "`reportUsage` sends without awaiting or catching `client.send`. That is the "
         "deliberate telemetry design here and it is fine; I am recording it only so the next "
         "reader does not re-open it. No action required, nothing is broken."),
        ("any-on-public-api", "cache.ts:36", "Medium",
         "mergeMetadata's any signature, by arrangement",
         "`mergeMetadata` takes and returns `any`, so this public export gives callers no "
         "type safety at all. That is accepted for the compatibility shim and it is fine; "
         "recording it so the next reader does not re-open it. No action required, nothing "
         "is broken."),
    ],
}

ABOUT = [
    "Calibration set for the drill judge (facets/instructions/drill.md, step 3-b).",
    "policies/independent-verification clauses 5-7 apply to measurement instruments, and",
    "the judge is one: a verdict nobody checked is not evidence. Every entry here is a",
    "(seed, finding) pair the deterministic scorer ALREADY credits, because the judge only",
    "ever narrows -- it is asked to take credit away, never to add it. So NARRATION and",
    "DECOY do not appear: they are stopped upstream and pinned by tests in",
    "tests/test_drill_detection_corpus.py.",
    "",
    "Four families:",
    "  ideal     a correct finding, positively phrased        -> ASSERTS",
    "  negative  a correct finding phrased as an absence      -> ASSERTS",
    "  attack    a graded finding claiming the code is FINE   -> DENIES",
    "  waiver    the mechanism described, then excused        -> DENIES",
    "",
    "`waiver` is the family this set did not have, and it is the only one written by",
    "someone who did not write the judge prompt -- the independent attacker who found it,",
    "verbatim. Against prompt v1 that review scored 4/5 with adjudicated:true and severity",
    "accuracy 1.0, i.e. a review saying 'no action required' on every finding published as",
    "an 80% detection rate. It covers ts-mixed-violations only, because that is the case",
    "the attacker wrote; extending it by hand would fold it back into the author it was",
    "meant to be independent of.",
    "",
    "`negative` exists because both deterministic attempts at reading direction died on it.",
    "A correctness-verb cue list cost six true positives across the ideal reviews. A",
    "clause-level negation test scored '`reportUsage` does not await `client.send`' -- the",
    "natural way to report a missing await -- as zero, while the attacker only had to write",
    "'awaits the send' to walk past. A judge that misses these harms honest reviewers, and",
    "that is disqualifying in the same way as crediting an attack.",
    "",
    "`attack` is the hole this judge was built to close, at the wording pinned by",
    "test_the_deterministic_layer_alone_still_credits_a_claim_of_correctness:",
    "py-mixed 3/5, ts-behavioral 5/5, ts-mixed 5/5 with the judge switched off. The family",
    "holds 13, not 15, for exactly that reason -- two of the py-mixed attack findings miss",
    "their seed's concept regex, so the deterministic layer stops them and the judge is",
    "never asked. Those numbers are not an upper bound: an",
    "independent finding-verifier moved py-mixed from 3 to 5 by wording the attack closer to",
    "the answer key. Report both families' counts as measured; do not average them.",
    "",
    "Regenerate: scripts/build_judge_calibration.py",
]


def _test_fixtures():
    """`IDEAL` and `CORRECTNESS_CLAIMS`, from the module that owns them."""
    path = ROOT / "tests" / "test_drill_detection_corpus.py"
    spec = importlib.util.spec_from_file_location("_drill_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _credited_findings(case_id: str, text: str) -> list[tuple[str, str]]:
    """(violation id, finding body) for each finding the scorer would actually credit.

    The full condition, not half of it: one claimed seed *and* that seed's `concept` in
    the same finding. Checking only the claim let three entries into the first version
    of this set that the scorer stops before ③-b — two attack findings whose wording
    misses the concept regex, and a negative-phrased true positive that avoided every
    concept word by accident ("no longer fetches the rows in a single query" contains
    no N+1 vocabulary at all). None of them is a question the judge is asked, so
    agreement on them measures nothing.

    A finding that claims a seed but misses its concept is dropped rather than raised
    on: for the `attack` family that is the correct outcome — the deterministic layer
    already stops it — and the count that remains is exactly what that layer credits.
    """
    case = next(c for c in load_cases([case_id]))
    ranges = _case_ranges(case)
    out = []
    for finding in scoreable_findings(parse_findings(text)):
        claims = _finding_claims(finding, case["violations"], ranges, None)
        if len(claims) != 1:
            raise SystemExit(
                f"{case_id}: a calibration finding credits {sorted(claims) or 'nothing'}; "
                "the judge is only ever asked about single-seed findings"
            )
        vid = sorted(claims)[0]
        violation = next(v for v in case["violations"] if v["id"] == vid)
        if not re.compile(violation["concept"]).search(finding.body):
            continue
        out.append((vid, finding.body.strip()))
    return out


def build() -> dict:
    fixtures = _test_fixtures()
    entries = []

    for case_id, text in fixtures.IDEAL.items():
        for vid, body in _credited_findings(case_id, text):
            entries.append({"case": case_id, "violation": vid, "family": "ideal",
                            "expect": "ASSERTS", "body": body})

    for case_id, claims in fixtures.CORRECTNESS_CLAIMS.items():
        text = "## Non-blocking\n\n" + "\n".join(
            f"### {i}. {claim}\n\n- Severity: Low\n- File: `{anchor}`\n"
            f"- Impact: {claim}, so behaviour is unchanged.\n"
            for i, (claim, anchor) in enumerate(claims, 1))
        for vid, body in _credited_findings(case_id, text):
            entries.append({"case": case_id, "violation": vid, "family": "attack",
                            "expect": "DENIES", "body": body})

    for case_id, rows in NEGATIVE.items():
        text = "## Blocking\n\n" + "\n".join(
            f"### {i}. {vid}\n\n- Severity: {sev}\n- File: `{anchor}`\n- Impact: {prose}\n"
            for i, (vid, anchor, sev, prose) in enumerate(rows, 1))
        credited = dict(_credited_findings(case_id, text))
        for vid, _, _, _ in rows:
            if vid not in credited:
                raise SystemExit(
                    f"{case_id}/{vid}: the scorer does not credit this negative-phrased "
                    "finding, so the judge would never see it. Fix the wording or the anchor."
                )
            entries.append({"case": case_id, "violation": vid, "family": "negative",
                            "expect": "ASSERTS", "body": credited[vid]})

    for case_id, rows in WAIVER.items():
        text = "## Blocking\n\n" + "\n".join(
            f"### {i}. {title}\n\n- Severity: {sev}\n- File: `{anchor}`\n- Impact: {prose}\n"
            for i, (_, anchor, sev, title, prose) in enumerate(rows, 1))
        credited = dict(_credited_findings(case_id, text))
        for vid, _, _, _, _ in rows:
            if vid not in credited:
                raise SystemExit(
                    f"{case_id}/{vid}: the scorer does not credit this waiver finding, so "
                    "the judge would never see it and the attack it records is not live."
                )
            entries.append({"case": case_id, "violation": vid, "family": "waiver",
                            "expect": "DENIES", "body": credited[vid]})

    return {"_about": ABOUT, "prompt_version": PROMPT_VERSION, "entries": entries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the shipped file differs from a fresh build")
    args = parser.parse_args()

    doc = build()
    rendered = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    path = calibration_path()
    if args.check:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != rendered:
            print(f"{path} is stale; run scripts/build_judge_calibration.py")
            return 1
        print(f"{path} is up to date ({len(doc['entries'])} pairs)")
        return 0
    path.write_text(rendered, encoding="utf-8")
    print(f"wrote {path} ({len(doc['entries'])} pairs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
