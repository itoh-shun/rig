"""Single-article judging against the human score distribution (jp-natural-writing).

The paired discriminator is being retired because the opponent decides the outcome — 39
of 47 winning verdicts cited the OPPONENT looking templated, and rebuilding the all-human
endpoint on a fresh pool moved it from 69.4% to 30.2%. Dropping the opponent removes that,
but it walks back into the reason absolute scoring was retired in the first place: the
judge is bimodal on gated-arm text, returning 12 or 76 for the same article
(results/2026-07-29-judge-variance.json).

So the behaviour worth pinning is the bimodality handling. A mean of [12, 76, 12, 72, 74]
is 49, a number the judge never returned and that describes neither mode. These tests
exist to keep that from ever being reported as a score.

Only the pure functions are covered — judging calls a model.
"""

import json
import pathlib
from importlib import util as _importlib_util

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing"
MODULE = BENCH / "solo_judge.py"

_spec = _importlib_util.spec_from_file_location("solo_judge", MODULE)
solo = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(solo)

# The shipped calibration, verbatim: 15 of 16 human articles land in 3-8, one misread at 88.
HUMAN = [3, 4, 4, 4, 4, 5, 6, 6, 6, 7, 8, 8, 8, 8, 8, 88]


@pytest.fixture
def reference(tmp_path):
    p = tmp_path / "ref.json"
    p.write_text(json.dumps({"samples": [{"score": s} for s in HUMAN]}), encoding="utf-8")
    return solo.load_reference(p)


# ---- the reference band ------------------------------------------------------

def test_band_excludes_the_single_misread(reference):
    """The 88 is one article the judge got wrong. Letting it stretch the band to [3, 88]
    would make every arm 'inside the human distribution'."""
    assert reference["p90"] < 50
    assert reference["median"] == 6.0


def test_reference_accepts_a_calibrate_output_shape(tmp_path):
    p = tmp_path / "ref2.json"
    p.write_text(json.dumps({"scores": [4, 5, 6, 7, 8]}), encoding="utf-8")
    assert solo.load_reference(p)["n"] == 5


def test_reference_without_scores_is_an_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"judge_model": "x"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        solo.load_reference(p)


# ---- the three verdicts ------------------------------------------------------

def test_scores_in_the_human_band_are_inside(reference):
    """The diary written this session scored 4, 6, 6."""
    r = solo.assess([4, 6, 6, 5, 4, 7, 6], reference)
    assert r["verdict"] == "INSIDE_HUMAN"
    assert not r["bimodal"]


def test_consistently_high_scores_are_outside(reference):
    r = solo.assess([72, 78, 82, 87, 78, 75, 80], reference)
    assert r["verdict"] == "OUTSIDE_HUMAN"
    assert not r["bimodal"]


def test_two_modes_are_unstable_not_averaged(reference):
    """writer:機械学習 returned 12, 76, 12, 72, 74. Its mean is 49 — a value the judge
    never produced, sitting in the gap where almost nothing lands."""
    r = solo.assess([12, 76, 12, 72, 74], reference)
    assert r["verdict"] == "UNSTABLE"
    assert r["bimodal"]
    assert r["low_mode"] == 2 and r["high_mode"] == 3


def test_unstable_wins_over_a_median_inside_the_band(reference):
    """A text landing in the human mode more often than not is still two answers, not a
    human one. Reporting it as INSIDE_HUMAN is exactly the overclaim to avoid."""
    r = solo.assess([6, 4, 5, 74, 72], reference)
    assert r["median"] <= reference["p90"]
    assert r["verdict"] == "UNSTABLE"


def test_a_single_excursion_still_counts_as_two_modes(reference):
    """freewrite:Python was 8, 74, 68, 74, 70 — one excursion into the human mode."""
    r = solo.assess([8, 74, 68, 74, 70], reference)
    assert r["verdict"] == "UNSTABLE"


def test_scores_in_the_gap_are_neither_mode(reference):
    """40-50 is the sparse band between the modes; that is not bimodality."""
    r = solo.assess([44, 46, 45, 47, 43], reference)
    assert not r["bimodal"]
    assert r["verdict"] == "OUTSIDE_HUMAN"


# ---- summary statistics ------------------------------------------------------

def test_median_is_reported_and_the_mean_is_not_the_verdict_basis(reference):
    r = solo.assess([12, 76, 12, 72, 74], reference)
    assert r["median"] == 72
    assert r["mean"] == pytest.approx(49.2, abs=0.5)
    assert r["verdict"] == "UNSTABLE"      # driven by the modes, not by either statistic


def test_empty_scores_do_not_crash(reference):
    assert solo.assess([], reference)["verdict"] == "NO_SCORES"


def test_single_score_reports_zero_sd(reference):
    assert solo.assess([6], reference)["sd"] == 0.0


def test_mode_boundaries_leave_a_real_gap():
    """If these ever met, every mixed result would read as bimodal."""
    assert solo.LOW_MODE_MAX < solo.HIGH_MODE_MIN


def test_default_repeats_can_show_a_second_mode():
    """Three judgments cannot distinguish a rare second mode from noise."""
    assert solo.DEFAULT_REPEATS >= 5


# ---- rank against the reference ----------------------------------------------

def test_rank_counts_human_articles_at_or_above_the_candidate(reference):
    """At n=24 the band moves a whole step when one article does, so the rank is the
    statistic that survives. The session's diary at 6.0 was beaten by 23 of 24."""
    r = solo.assess([6, 6, 6, 6, 6, 6, 6], reference)
    assert r["human_at_or_above"] == sum(1 for s in HUMAN if s >= 6)
    assert r["human_rank_share"] == pytest.approx(10 / 16)


def test_rank_is_zero_when_no_human_article_scored_that_badly(reference):
    r = solo.assess([95, 95, 95, 95, 95, 95, 95], reference)
    assert r["human_at_or_above"] == 0
    # the fixture's lone misread at 88 is still counted when the candidate reaches it
    assert solo.assess([88] * 7, reference)["human_at_or_above"] == 1


def test_rank_is_high_for_a_text_at_the_human_median(reference):
    r = solo.assess([4, 4, 4, 4, 4, 4, 4], reference)
    assert r["human_rank_share"] > 0.5
    assert r["verdict"] == "INSIDE_HUMAN"


def test_shipped_reference_ships_with_repeats_recorded():
    """The default reference must be built on the same protocol as the candidate.
    Comparing a median-of-7 against single judgments is what produced the [3, 88] band."""
    ref = solo.load_reference(solo.DEFAULT_REFERENCE)
    assert ref["corpus_note"], "reference must record how it was built"
    assert ref["p90"] <= 8
    assert ref["n"] >= 20
