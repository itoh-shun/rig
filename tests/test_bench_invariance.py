"""Unit tests for the pure model-invariance scorer (rig_workbench.bench_invariance).

These exercise the scoring math on synthetic per-model bench summaries — no
provider, no cost — so the metric that backs the "rig is model-invariant" claim
is itself verified rather than asserted.
"""

from __future__ import annotations

from rig_workbench.bench_invariance import classify_arm_dict, score_invariance


def _arm(public: bool, hidden: bool, completed: bool = True, infra: bool = False) -> dict:
    return {
        "completed": completed,
        "invocation_count": 1,
        "attempts": [{"infra_error": "boom" if infra else None}],
        "public_test": {"passed": public, "infra_error": None},
        "hidden_check": {"passed": hidden, "infra_error": None},
    }


def _summary(model: str, task_arms: dict[str, tuple[dict, dict]]) -> dict:
    """One model's bench summary: {task_id: (bare_arm, rig_arm)}."""
    return {
        "model": model,
        "bare_model": model,
        "rig_model": model,
        "tasks": [
            {"task_id": tid, "runs": [{"arms": {"bare": bare, "rig": rig}}]}
            for tid, (bare, rig) in task_arms.items()
        ],
    }


def test_classify_arm_dict_matches_taxonomy():
    assert classify_arm_dict(_arm(True, True)) == "clean_pass"
    assert classify_arm_dict(_arm(True, False)) == "silent_defect"
    assert classify_arm_dict(_arm(False, False)) == "stopped_wrong"
    assert classify_arm_dict(_arm(False, True)) == "safe_stop"
    assert classify_arm_dict(_arm(True, True, infra=True)) == "infra_error"
    assert classify_arm_dict(None) == "invalid"
    assert classify_arm_dict({"completed": "nope"}) == "invalid"


def test_full_agreement_scores_invariant():
    # Every model in the panel lands on clean_pass for the rig arm.
    panel = [
        _summary(m, {"t1": (_arm(True, True), _arm(True, True))})
        for m in ("weak", "mid", "strong")
    ]
    report = score_invariance(panel)
    assert report["model_invariance_score"] == 1.0
    assert report["verdict"] == "model_invariant"
    assert report["arms"]["rig"]["panel_silent_defect_rate"] == 0.0
    assert report["panel_size"] == 3


def test_rig_neutralizes_model_where_bare_diverges():
    # Bare arm: the weak model ships a silent defect, others clean -> low agreement,
    # nonzero silent-defect. Rig arm: all safe (weak safe-stops, others clean) ->
    # zero silent defects, but outcomes split clean/safe_stop so agreement < 1.
    panel = [
        _summary("weak", {"t1": (_arm(True, False), _arm(False, False))}),
        _summary("mid", {"t1": (_arm(True, True), _arm(True, True))}),
        _summary("strong", {"t1": (_arm(True, True), _arm(True, True))}),
    ]
    report = score_invariance(panel)
    bare = report["arms"]["bare"]
    rig = report["arms"]["rig"]
    # bare shipped a silent defect on 1/3 samples; rig shipped none.
    assert bare["panel_silent_defect_rate"] > 0
    assert rig["panel_silent_defect_rate"] == 0.0
    # rig is safer, so even with split outcomes it is not "unsafe".
    assert report["verdict"] in {"model_invariant", "model_sensitive"}


def test_any_rig_silent_defect_makes_verdict_unsafe():
    panel = [
        _summary("weak", {"t1": (_arm(True, False), _arm(True, False))}),
        _summary("strong", {"t1": (_arm(True, True), _arm(True, True))}),
    ]
    report = score_invariance(panel)
    assert report["arms"]["rig"]["panel_silent_defect_rate"] > 0
    assert report["verdict"] == "unsafe"


def test_infra_noise_excluded_from_agreement():
    # One model's rig arm is an infra error; it must not count as disagreement.
    panel = [
        _summary("a", {"t1": (_arm(True, True), _arm(True, True))}),
        _summary("b", {"t1": (_arm(True, True), _arm(True, True, infra=True))}),
    ]
    report = score_invariance(panel)
    rig_task = report["arms"]["rig"]["per_task"][0]
    assert rig_task["valid"] == 1  # the infra sample is excluded
    assert rig_task["noise"] == 1
    assert rig_task["agreement"] == 1.0


def test_split_outcomes_lower_agreement_below_threshold():
    # Rig outcomes split evenly clean/safe_stop across 4 models -> agreement 0.5.
    panel = [
        _summary("a", {"t1": (_arm(True, True), _arm(True, True))}),
        _summary("b", {"t1": (_arm(True, True), _arm(True, True))}),
        _summary("c", {"t1": (_arm(True, True), _arm(False, True))}),
        _summary("d", {"t1": (_arm(True, True), _arm(False, True))}),
    ]
    report = score_invariance(panel, agreement_threshold=0.8)
    assert report["arms"]["rig"]["per_task"][0]["agreement"] == 0.5
    assert report["model_invariance_score"] == 0.5
    assert report["verdict"] == "model_sensitive"
