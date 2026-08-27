"""Route-level evidence ownership contract (#508)."""

import pytest
from copy import deepcopy

from rig_workbench.validation import state


def _run_entry(monkeypatch, declarations):
    """Drive the same validation entry used by ``scripts/validate.py``."""
    from rig_workbench.validation import cli, routes

    from rig_workbench.workbench.capabilities import ROUTE_PRODUCERS

    replacements = {
        (item["task_type"], item["capability"], item["recipe"]): item
        for item in declarations
    }
    complete = tuple(
        replacements.pop((item["task_type"], item["capability"], item["recipe"]), item)
        for item in ROUTE_PRODUCERS
    ) + tuple(replacements.values())
    monkeypatch.setattr(routes, "ROUTE_PRODUCERS", complete)
    state.results.clear()
    state._pass = state._warn = state._fail = 0
    try:
        cli.main()
    except SystemExit as stopped:
        code = stopped.code
    else:
        code = 0
    return code, "\n".join(state.results)


def _review_route():
    from rig_workbench.workbench.capabilities import ROUTE_PRODUCERS

    return deepcopy(next(
        item for item in ROUTE_PRODUCERS
        if item["task_type"] == "review" and item["capability"] == "review"
    ))


def test_validate_entry_rejects_a_real_route_with_an_unowned_binding_criterion(monkeypatch):
    """Positive control: construct a route, omit one actual gate member, and enter via CLI."""
    route = _review_route()
    del route["producers"]["false_positive_risk_considered"]
    code, report = _run_entry(monkeypatch, (route,))
    assert code == 1
    assert "false_positive_risk_considered" in report
    route["producers"]["false_positive_risk_considered"] = {
        "kind": "manual", "name": "operator",
    }
    code, report = _run_entry(monkeypatch, (route,))
    assert code == 0
    assert "route review/review → review-only: producer coverage OK" in report


@pytest.mark.parametrize(
    ("producer", "message"),
    [
        (None, "must be a mapping"),
        ({}, "must contain exactly"),
        ({"kind": "step", "name": "no-such-step"}, "does not resolve"),
        ({"kind": "manual", "name": "reviewer"}, "does not resolve"),
        ({"kind": "manual", "name": "operator", "verdict": "passed"}, "unknown keys"),
    ],
)
def test_validate_entry_rejects_bad_producers_and_accepts_the_control(
    monkeypatch, producer, message,
):
    good = {
        criterion: {"kind": "manual", "name": "operator"}
        for criterion in (
            "findings_are_concrete", "severity_labeled", "file_references_included",
            "blocking_and_non_blocking_separated", "false_positive_risk_considered",
        )
    }
    route = _review_route()
    route["producers"] = dict(good)
    route["producers"]["findings_are_concrete"] = producer
    code, report = _run_entry(monkeypatch, (route,))
    assert code == 1
    assert message in report
    route["producers"] = good
    assert _run_entry(monkeypatch, (route,))[0] == 0


def test_shipped_security_review_is_ten_explicit_manual_producers():
    from rig_workbench.workbench.capabilities import ROUTE_PRODUCERS

    route = next(record for record in ROUTE_PRODUCERS
                 if record["task_type"] == "security_review"
                 and record["recipe"] == "review-only")
    assert len(route["producers"]) == 10
    assert {tuple(owner.values()) for owner in route["producers"].values()} == {
        ("manual", "operator")
    }


def test_each_route_declaration_reproduces_its_selector_branch():
    from rig_workbench.validation.routes import check_route_producers

    state.results.clear()
    state._pass = state._warn = state._fail = 0
    check_route_producers()
    assert state._fail == 0, "\n".join(state.results)


def test_standard_gate_uses_the_four_registered_sensors():
    from rig_workbench.workbench.capabilities import ROUTE_PRODUCERS

    route = next(item for item in ROUTE_PRODUCERS if item["task_type"] == "documentation")
    assert {criterion: route["producers"][criterion]["name"] for criterion in (
        "no_secret_leak", "no_gate_tampering", "no_injection_markers",
        "no_destructive_operation",
    )} == {
        "no_secret_leak": "scan-secrets",
        "no_gate_tampering": "anti-tamper",
        "no_injection_markers": "scan-injection",
        "no_destructive_operation": "scan-destructive",
    }


def test_id_form_recipe_work_is_owned_by_its_acceptance_step():
    from rig_workbench.workbench.capabilities import ROUTE_PRODUCERS

    route = next(item for item in ROUTE_PRODUCERS if item["task_type"] == "bugfix")
    assert route["producers"]["bug_cause_identified"] == {
        "kind": "step", "name": "acceptance",
    }
    # Sensor ownership remains more specific than the step's broad work list.
    assert route["producers"]["no_secret_leak"] == {
        "kind": "sensor", "name": "scan-secrets",
    }
