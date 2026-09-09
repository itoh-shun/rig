"""What the ja-textlint sensor actually catches, measured against a spec-derived corpus.

`tests/fixtures/ja-textlint/` was written from the NG/OK examples in the textlint-ja
rule READMEs before the sensor existed, so `answer_key.json` is an expectation derived
from the upstream rules, not a transcript of what this implementation happens to do.
The corpus author and the sensor author are the same person; the separation is in
time and in source, not in people. `README.md` there says so.

The numbers below are pinned deliberately. If a change moves one, that is not a
broken test — it is the shipped ratio going stale. Re-measure, update the number
here **and** in `policies/japanese-textlint-rules`, and say which way it moved.
"""

import json
import pathlib

import pytest

from rig_workbench import ja_textlint as jt

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "ja-textlint"
POLICY = (
    pathlib.Path(__file__).resolve().parent.parent
    / "skills" / "engine" / "facets" / "policies" / "japanese-textlint-rules.md"
)

# 2026-09-09 の実測（本家 textlint-ja との突き合わせ後）。種 69/69、種の file 内での同一 rule の
# 偽陽性 0。clean 3 本では error 0、warning は release-notes の no-doubled-joshi 2 件だけで、
# 本家（kuromoji）が同じ file に報告する 2 行と一致する。
EXPECTED_HITS = 69
EXPECTED_TOTAL = 69
EXPECTED_CLEAN_WARNINGS = {"clean/release-notes.md": 2, "clean/incident-report.md": 0, "clean/howto.md": 0}


@pytest.fixture(scope="module")
def key() -> dict:
    return json.loads((FIXTURES / "answer_key.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def settings() -> jt.Settings:
    data, _ = jt.load_config(str(FIXTURES / "config.json"), True)
    return jt.Settings(data)


def _findings(path: str, settings: jt.Settings) -> list[dict]:
    return jt.lint_text(jt.read_source(str(FIXTURES / path)), settings, path)


def test_every_rule_in_the_key_is_a_shipped_rule(key):
    assert {c["rule"] for c in key["cases"]} <= set(jt.RULES)


def test_every_shipped_rule_has_a_seed(key):
    """A rule with no seed has no measured detection rate — it is a claim, not a number."""
    assert set(jt.RULES) - {c["rule"] for c in key["cases"]} == set()


def test_seeds_are_caught_line_exact_and_nothing_else_fires_for_that_rule(key, settings):
    hits = total = 0
    false_positives: list[tuple[str, str, int]] = []
    misses: list[tuple[str, str, int]] = []
    for case in key["cases"]:
        got = {f["line"] for f in _findings(case["path"], settings) if f["rule"] == case["rule"]}
        expected = set(case["expect"])
        total += len(expected)
        hits += len(expected & got)
        misses += [(case["path"], case["rule"], ln) for ln in sorted(expected - got)]
        false_positives += [(case["path"], case["rule"], ln) for ln in sorted(got - expected)]
    assert false_positives == [], f"the seed files report the target rule where the key says clean: {false_positives}"
    assert misses == [], f"seeds no longer caught: {misses}"
    assert (hits, total) == (EXPECTED_HITS, EXPECTED_TOTAL)


def test_clean_documents_have_zero_errors_and_the_pinned_warning_count(key, settings):
    for path in key["clean"]:
        found = _findings(path, settings)
        errors = [f for f in found if f["severity"] == "error"]
        assert errors == [], f"{path}: {[(f['line'], f['rule'], f['text']) for f in errors]}"
        warnings = [f for f in found if f["severity"] == "warning"]
        assert len(warnings) == EXPECTED_CLEAN_WARNINGS[path], (
            f"{path}: {[(f['line'], f['rule'], f['text']) for f in warnings]}"
        )


def test_clean_warnings_are_all_the_approximate_particle_rule(key, settings):
    """If a different rule starts firing on honest prose, that is a new false-positive class."""
    for path in key["clean"]:
        rules = {f["rule"] for f in _findings(path, settings) if f["severity"] == "warning"}
        assert rules <= {"no-doubled-joshi"}, (path, rules)


def test_the_policy_quotes_the_same_measurement():
    text = POLICY.read_text(encoding="utf-8")
    assert f"{EXPECTED_HITS}/{EXPECTED_TOTAL}" in text, "policy と test の実測値がずれている"
