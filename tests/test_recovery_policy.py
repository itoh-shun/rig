"""Recovery transitions are determined by recorded failures, never AI counters."""
from dataclasses import FrozenInstanceError, replace

import pytest

from rig_workbench.orchestrate.recovery_policy import (
    FailureEvent, ReplanEvent, RecoveryLimits, decide_recovery, stable_failure_id,
)


def failure(sequence=1, classification="IMPLEMENTATION", check="test"):
    return FailureEvent(sequence, check, "assertion", "module", classification)


def fid(check="test"):
    return stable_failure_id(check, "assertion", "module")


@pytest.mark.parametrize("classification, action", [
    ("IMPLEMENTATION", "REPAIR"), ("DESIGN", "DESIGN"),
    ("REQUIREMENT", "REQUIREMENTS"), ("ENVIRONMENT", "BLOCKED"),
    ("CHECK_ERROR", "BLOCKED"), ("UNKNOWN", "AWAIT_DECISION"),
    ("invented", "AWAIT_DECISION"), (None, "AWAIT_DECISION"),
])
def test_classification_routes(classification, action):
    assert decide_recovery((failure(classification=classification),)).action == action


def test_repeated_failure_replans_and_is_deterministic():
    history = (failure(), failure(2))
    decision = decide_recovery(history)
    assert decision.action == "REPLAN"
    assert decision.repeated_failures == 2
    assert decision == decide_recovery(history)
    with pytest.raises(FrozenInstanceError):
        decision.action = "REPAIR"


def test_two_replans_escalate_only_after_a_new_matching_failure():
    history = (failure(), failure(2), ReplanEvent(3, fid()),
               failure(4), failure(5), ReplanEvent(6, fid()), failure(7))
    decision = decide_recovery(history)
    assert (decision.action, decision.total_failures, decision.replans) == ("ESCALATE", 5, 2)
    assert decide_recovery(history[:4]).action == "REPAIR"


def test_any_new_failure_after_two_replans_escalates():
    history = (failure(), failure(2), ReplanEvent(3, fid()),
               failure(4), failure(5), ReplanEvent(6, fid()), failure(7, check="other"))
    assert decide_recovery(history).action == "ESCALATE"


def test_cumulative_failure_limit_survives_replans():
    history = (failure(), failure(2), ReplanEvent(3, fid()),
               failure(4, check="b"), failure(5, check="c"),
               failure(6, check="d"), failure(7, check="e"))
    assert decide_recovery(history[:-1]).action == "REPAIR"
    assert decide_recovery(history).action == "ESCALATE"


@pytest.mark.parametrize("history", [
    (), [], (failure(True),), (failure(0),), (failure(2),),
    (failure(), failure(1)), (failure(), {"sequence": 2}),
    (ReplanEvent(1, fid()), failure(2)),
    (failure(), ReplanEvent(2, "made-up"), failure(3)),
    (failure(), failure(2), ReplanEvent(3, fid()), ReplanEvent(4, fid()), failure(5)),
    (failure(), failure(2), ReplanEvent(3, fid())),
    (replace(failure(), check_id=""),),
    (replace(failure(), error_class=[]),),
    (failure(classification="bad"), failure(2)),
])
def test_malformed_history_fails_closed(history):
    assert decide_recovery(history).action == "AWAIT_DECISION"


@pytest.mark.parametrize("field", ["repeat_threshold", "max_total_failures", "max_replans"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "2", None])
def test_invalid_limits_raise(field, value):
    with pytest.raises(ValueError):
        decide_recovery((failure(),), replace(RecoveryLimits(), **{field: value}))


def test_custom_thresholds_are_exact():
    limits = RecoveryLimits(repeat_threshold=3, max_total_failures=4, max_replans=1)
    assert decide_recovery((failure(), failure(2)), limits).action == "REPAIR"
    assert decide_recovery((failure(), failure(2), failure(3)), limits).action == "REPLAN"


def test_failure_identifier_has_unambiguous_component_encoding():
    assert stable_failure_id("a:b", "c", "d") != stable_failure_id("a", "b:c", "d")
    assert fid() == fid()
    assert fid() != fid("different")
    for values in [("", "b", "c"), ("a", None, "c"), ("a", "b", True)]:
        with pytest.raises(ValueError):
            stable_failure_id(*values)


@pytest.mark.parametrize("classification, action", [
    ("DESIGN", "DESIGN"), ("REQUIREMENT", "REQUIREMENTS"),
    ("ENVIRONMENT", "BLOCKED"), ("CHECK_ERROR", "BLOCKED"),
    ("UNKNOWN", "AWAIT_DECISION"),
])
def test_repetition_does_not_override_nonimplementation_routes(classification, action):
    assert decide_recovery((failure(),
                            failure(2, classification=classification))).action == action


def test_replan_cannot_be_invented_after_first_failure():
    history = (failure(), ReplanEvent(2, fid()), failure(3))
    assert decide_recovery(history).action == "AWAIT_DECISION"


def test_replan_cannot_reference_an_older_failure():
    history = (failure(), failure(2), ReplanEvent(3, fid()),
               failure(4, check="b"), failure(5, check="b"),
               ReplanEvent(6, fid()), failure(7))
    assert decide_recovery(history).action == "AWAIT_DECISION"


def test_cumulative_repetition_survives_replan_but_cadence_restarts():
    history = (failure(), failure(2), ReplanEvent(3, fid()), failure(4))
    result = decide_recovery(history)
    assert (result.action, result.repeated_failures, result.total_failures) == ("REPAIR", 3, 3)
    assert decide_recovery(history + (failure(5),)).action == "REPLAN"


def test_dataclasses_are_frozen_and_wrong_limit_object_rejected():
    for value, attribute in [(failure(), "sequence"), (RecoveryLimits(), "max_replans"),
                             (ReplanEvent(3, fid()), "sequence")]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, 42)
    with pytest.raises(ValueError):
        decide_recovery((failure(),), {})

@pytest.mark.parametrize("classification", ["ENVIRONMENT", "CHECK_ERROR", "UNKNOWN", "DESIGN", "REQUIREMENT"])
def test_history_cannot_continue_after_stopping_or_upstream_decision(classification):
    history = (failure(classification=classification), failure(2, check="other"))
    assert decide_recovery(history).action == "AWAIT_DECISION"
    assert decide_recovery(history).reason == "invalid_history"


def test_history_cannot_continue_after_escalation():
    history = (failure(), failure(2, check="other"))
    result = decide_recovery(history, RecoveryLimits(max_total_failures=1))
    assert (result.action, result.reason) == ("AWAIT_DECISION", "invalid_history")


def test_replan_decision_requires_explicit_replan_event_before_more_failures():
    assert decide_recovery((failure(), failure(2), failure(3))).reason == "invalid_history"
