from dataclasses import FrozenInstanceError, replace
from itertools import permutations

import pytest

from rig_workbench.orchestrate.gate_evidence import (
    CheckEvidence, EvidenceContext, RequiredCheck, evaluate_gate,
)

D = "a" * 64
OTHER = "b" * 64


def context():
    return EvidenceContext("run", "feature", 1, D, D, D)


def evidence(check_id="test", **changes):
    return replace(CheckEvidence(context(), check_id, D, "PASS", 0, D), **changes)


def decide(records, required=None):
    return evaluate_gate(context(), (RequiredCheck("test", D),) if required is None else required, records)


def codes(result):
    return {issue.code for issue in result.issues}


def test_pass_requires_exact_complete_evidence():
    result = decide((evidence(),))
    assert result.passed
    assert result.issues == ()


@pytest.mark.parametrize("status,exit_code", [("PASS", 1), ("PASS", None), ("FAIL", 0), ("FAIL", 1), ("UNKNOWN", 0), ("UNKNOWN", None)])
def test_non_success_cannot_pass(status, exit_code):
    result = decide((evidence(status=status, exit_code=exit_code),))
    assert not result.passed
    assert "check_not_passed" in codes(result)


def test_missing_duplicate_and_unregistered_checks_fail_closed():
    assert codes(decide(())) == {"missing_check"}
    assert "duplicate_check" in codes(decide((evidence(), evidence())))
    assert "unknown_check" in codes(decide((evidence(), evidence("AI_PASS"))))


@pytest.mark.parametrize("field,value", [("run_id", "other"), ("unit_id", "other"), ("attempt_id", 2), ("subject_digest", OTHER), ("baseline_digest", OTHER), ("environment_digest", OTHER)])
def test_all_context_dimensions_bind_evidence(field, value):
    record = evidence(context=replace(context(), **{field: value}))
    result = decide((record,))
    assert not result.passed
    assert "context_mismatch" in codes(result)


def test_changed_check_definition_invalidates_pass():
    assert "definition_mismatch" in codes(decide((evidence(definition_digest=OTHER),)))


def test_order_does_not_affect_decision():
    records = (evidence(), evidence(status="FAIL", exit_code=1), evidence("extra"))
    results = [decide(tuple(order)) for order in permutations(records)]
    assert all(result == results[0] for result in results)
    required = (RequiredCheck("test", D), RequiredCheck("other", D))
    assert decide((), required) == decide((), tuple(reversed(required)))


@pytest.mark.parametrize("required", [(), (RequiredCheck("test", D), RequiredCheck("test", D)), ("test",), []])
def test_invalid_required_contract_is_rejected(required):
    with pytest.raises(ValueError):
        decide((evidence(),), required)


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", None])
def test_attempt_is_strict_positive_integer(value):
    with pytest.raises(ValueError):
        replace(context(), attempt_id=value)


@pytest.mark.parametrize("value", [True, False, 0.0, "0"])
def test_exit_code_is_strict_integer_or_none(value):
    with pytest.raises(ValueError):
        evidence(exit_code=value)


@pytest.mark.parametrize("value", [True, 2, "1", 1.0])
def test_schema_is_exact_supported_integer(value):
    with pytest.raises(ValueError):
        evidence(schema_version=value)


@pytest.mark.parametrize("value", ["", "x", "A" * 64, "a" * 63, "g" * 64, None])
def test_digest_validation(value):
    with pytest.raises(ValueError):
        evidence(artifact_digest=value)
    with pytest.raises(ValueError):
        RequiredCheck("test", value)


@pytest.mark.parametrize("value", ["", " test", "test ", "test\n", None, 1])
def test_ids_are_exact_nonempty_strings(value):
    with pytest.raises(ValueError):
        RequiredCheck(value, D)


@pytest.mark.parametrize("value", ["pass", "SKIP", True, None])
def test_unknown_status_is_rejected(value):
    with pytest.raises(ValueError):
        evidence(status=value)


def test_untyped_records_are_rejected_without_attribute_errors():
    result = decide(({"status": "PASS", "exit_code": 0}, evidence()))
    assert not result.passed
    assert "malformed_evidence" in codes(result)
    assert not decide(None).passed
    assert not decide([evidence()]).passed


def test_values_are_immutable():
    with pytest.raises(FrozenInstanceError):
        context().run_id = "other"
    with pytest.raises(FrozenInstanceError):
        evidence().status = "PASS"


def test_invalid_context_and_check_types_rejected():
    with pytest.raises(ValueError):
        evidence(context={})
    with pytest.raises(ValueError):
        evaluate_gate({}, (RequiredCheck("test", D),), ())


def test_machine_failure_has_no_ai_override_channel():
    with pytest.raises(TypeError):
        evaluate_gate(context(), (RequiredCheck("test", D),), (evidence(status="FAIL", exit_code=1),), ai_verdict="PASS")
