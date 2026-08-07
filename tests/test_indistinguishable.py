"""Equivalence gate for "indistinguishable from human" on the jp-natural-writing bench.

The behaviour worth protecting is the three-way verdict. A difference test collapses
EQUIVALENT and UNDERPOWERED into one "no significant difference", and reading that as
"indistinguishable from human" is the most expensive available mistake here — the harness
is underpowered by its own measurement (MDE 19.4-29.0 points), so a difference test fails
to reject almost by construction.
"""

import json
import pathlib
import subprocess
import sys
from importlib import util as _importlib_util

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing"
MODULE = BENCH / "indistinguishable.py"
POSITIVE_CONTROL = BENCH / "results" / "2026-07-31-mde-positive-control.json"

_spec = _importlib_util.spec_from_file_location("indistinguishable", MODULE)
indist = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(indist)


def _counts(rates, trials=8):
    """topic -> (correct, trials) from a list of per-topic rates."""
    return {f"T{i}": (round(r * trials), trials) for i, r in enumerate(rates)}


def _assess(arm_rates, floor_rates, margin=0.10, seed="t"):
    return indist.assess(_counts(arm_rates), _counts(floor_rates), 0.694,
                         margin, 0.05, 2000, seed)


# ---- the three verdicts ------------------------------------------------------

def test_identical_populations_are_equivalent():
    rates = [0.75, 0.625, 0.875, 0.75, 0.5, 0.75, 0.625, 0.875]
    assert _assess(rates, rates)["verdict"] == "EQUIVALENT"


def test_large_consistent_gap_is_different():
    arm = [1.0] * 8
    floor = [0.625] * 8
    assert _assess(arm, floor)["verdict"] == "DIFFERENT"


def test_small_gap_with_wide_spread_is_underpowered_not_equivalent():
    """The case a difference test would call 'no significant difference'. It is not
    evidence of sameness, and the gate must not let it pass."""
    arm = [1.0, 0.25, 1.0, 0.25, 1.0, 0.25, 1.0, 0.25]
    floor = [0.25, 1.0, 0.25, 1.0, 0.25, 1.0, 0.25, 1.0]
    assert _assess(arm, floor)["verdict"] == "UNDERPOWERED"


def test_underpowered_is_not_a_pass_at_the_cli(tmp_path):
    """Exit code is the gate. UNDERPOWERED must be non-zero."""
    proc = _cli("--arm", "pc_human_0750", "--floor-arm", "pc_human_1000",
                "--margin", "0.10")
    assert "UNDERPOWERED" in proc.stdout
    assert proc.returncode != 0


# ---- clustering --------------------------------------------------------------

def test_bootstrap_resamples_topics_not_trials():
    """62 trials treated as independent would roughly halve the interval and manufacture
    significance; the results file itself declares topic as the primary unit."""
    rates = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    result = _assess(rates, [0.5] * 8)
    assert result["clusters"] == 8
    assert result["half_width"] > 0.05


def test_paired_flag_reflects_whether_a_floor_arm_was_given():
    paired = indist.assess(_counts([0.7] * 8), _counts([0.7] * 8), 0.694,
                           0.10, 0.05, 500, "s")
    unpaired = indist.assess(_counts([0.7] * 8), None, 0.694, 0.10, 0.05, 500, "s")
    assert paired["paired"] and not unpaired["paired"]


def test_unpaired_mode_uses_the_measured_floor_constant():
    # trials=1000 so the floor is representable exactly; at 8 trials per topic the
    # helper rounds 0.694 to 6/8 and the "gap" is the rounding.
    out = indist.assess(_counts([indist.MEASURED_HUMAN_FLOOR] * 8, trials=1000), None,
                        indist.MEASURED_HUMAN_FLOOR, 0.10, 0.05, 500, "s")
    assert abs(out["observed_gap"]) < 0.01
    assert out["verdict"] == "EQUIVALENT"


# ---- determinism -------------------------------------------------------------

def test_same_seed_same_verdict():
    rates = [0.9, 0.6, 0.8, 0.7, 0.75, 0.65, 0.85, 0.7]
    a = _assess(rates, [0.7] * 8, seed="fixed")
    b = _assess(rates, [0.7] * 8, seed="fixed")
    assert a == b


# ---- required clusters -------------------------------------------------------

def test_point_estimate_outside_the_margin_is_unreachable_at_any_n():
    """More topics cannot fix a gap larger than the margin. Saying otherwise would send
    someone off to collect data against a fixed difference."""
    result = _assess([1.0] * 8, [0.5] * 8, margin=0.10)
    assert result["required_clusters"] is None


def test_required_clusters_exceeds_current_when_underpowered():
    arm = [1.0, 0.25, 1.0, 0.25, 1.0, 0.25, 1.0, 0.25]
    floor = [0.25, 1.0, 0.25, 1.0, 0.25, 1.0, 0.25, 1.0]
    result = _assess(arm, floor)
    assert result["required_clusters"] > result["clusters"]


def test_equivalent_requires_no_extra_clusters():
    rates = [0.7] * 8
    assert _assess(rates, rates)["required_clusters"] == 8


# ---- floor constant ----------------------------------------------------------

def test_measured_floor_is_the_positive_control_endpoint_not_one_half():
    """50% is not reachable on this instrument: at level 1.00 the candidate IS a human
    article and the judge still picks it 43/62."""
    assert indist.MEASURED_HUMAN_FLOOR == pytest.approx(43 / 62)
    assert indist.MEASURED_HUMAN_FLOOR > 0.65


# ---- loading -----------------------------------------------------------------

def test_load_pairs_accepts_the_nested_positive_control_record():
    assert len(indist.load_pairs(POSITIVE_CONTROL)) > 0


def test_load_pairs_accepts_a_flat_discriminate_output(tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({"pairs": [{"arm": "a", "topic": "T", "trials": []}]}),
                 encoding="utf-8")
    assert indist.load_pairs(p) == [{"arm": "a", "topic": "T", "trials": []}]


def test_load_pairs_rejects_a_record_without_pairs(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"arms": {}}), encoding="utf-8")
    with pytest.raises(SystemExit):
        indist.load_pairs(p)


def test_missing_arm_is_an_error_not_an_empty_result():
    with pytest.raises(SystemExit):
        indist.by_topic(indist.load_pairs(POSITIVE_CONTROL), "no_such_arm")


def test_by_topic_counts_both_orders_per_pair():
    counts = indist.by_topic(indist.load_pairs(POSITIVE_CONTROL), "writer_agent")
    assert len(counts) == 8
    assert all(n >= 2 for _, n in counts.values())


# ---- CLI against the real measurement ----------------------------------------

def _cli(*args):
    return subprocess.run(
        [sys.executable, str(MODULE), "--run", str(POSITIVE_CONTROL), *args],
        capture_output=True, text=True, timeout=180, check=False)


def test_human_endpoint_is_equivalent_to_itself():
    """Sanity: the gate must pass text that IS human."""
    proc = _cli("--arm", "pc_human_1000", "--floor-arm", "pc_human_1000")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EQUIVALENT" in proc.stdout


def test_best_arm_is_still_demonstrably_different_from_human():
    proc = _cli("--arm", "writer_agent", "--floor-arm", "pc_human_1000")
    assert proc.returncode == 1
    assert "DIFFERENT" in proc.stdout


def test_json_mode_is_machine_readable():
    proc = _cli("--arm", "writer_agent", "--floor-arm", "pc_human_1000", "--json")
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "DIFFERENT"
    assert payload["clusters"] == 8
