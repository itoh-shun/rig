import pytest

from rig_workbench.orchestrate.adaptive import (
    RiskAssessment,
    RiskSignal,
    analyze_diff,
    invocation_limit,
)


def test_authorization_change_selects_security():
    result = analyze_diff(
        "+ if current_user_id != requested_user_id:\n+"
        "+     return None\n",
        ["profile_service.py"],
    )
    assert result.primary == "security-reviewer"
    assert any(
        s.domain == "security" and "requested_user_id" in s.evidence
        for s in result.signals
    )
    assert invocation_limit(result) == 3


@pytest.mark.parametrize(
    "diff",
    [
        "+    if not is_owner(user, doc):\n+        raise Forbidden('not the owner')\n",
        "+    if record['tenant_id'] != tenant_id:\n+        return None\n",
        "+    if not can_access(user, resource):\n+        return 403\n",
        "+    def _sanitize(value):\n",
        "+    name = validate_username(name)\n",
    ],
)
def test_authz_tenant_validation_changes_route_to_security(diff):
    # Regression for the model-invariance panel finding: an ownership/permission,
    # multi-tenant, or input-validation fix must reach security-reviewer (so its
    # null-match-bypass / shared-sink lens can fire), even when the diff never
    # says the word "authorization".
    result = analyze_diff(diff, ["handlers.py"])
    assert result.primary == "security-reviewer", (diff, result.signals)


def test_api_and_test_change_selects_two_high_risk_lenses():
    result = analyze_diff(
        "+ app.get('/v2/users', handler)\n+"
        "+ describe('compat', () => {})\n",
        ["src/api.ts", "tests/api.test.ts"],
    )
    assert result.primary == "design-reviewer"
    assert result.secondary == "test-reviewer"
    assert invocation_limit(result) == 4


def test_unknown_change_falls_back_closed():
    result = analyze_diff("+opaque\n", ["data.unknown"])
    assert result.primary == "test-reviewer"
    assert result.secondary is None
    assert result.fallback_reason == "no recognized risk signals"


def test_risk_values_are_frozen_and_serialized_stably():
    signal = RiskSignal("security", 3, "authorization boundary")
    assessment = RiskAssessment("security-reviewer", None, (signal,), None)

    assert signal.to_dict() == {
        "domain": "security",
        "severity": 3,
        "evidence": "authorization boundary",
    }
    assert assessment.to_dict() == {
        "primary": "security-reviewer",
        "secondary": None,
        "signals": [signal.to_dict()],
        "fallback_reason": None,
    }

    try:
        signal.domain = "design"
    except AttributeError:
        pass
    else:
        raise AssertionError("RiskSignal must be frozen")

    try:
        assessment.primary = "test-reviewer"
    except AttributeError:
        pass
    else:
        raise AssertionError("RiskAssessment must be frozen")


def test_signals_rank_by_severity_domain_then_evidence():
    result = analyze_diff(
        "+ assert response.status == 200\n"
        "+ SELECT * FROM users WHERE id = 1\n"
        "+ app.get('/v2/users', handler)\n",
        ["tests/api_test.py", "service.py"],
    )

    assert [
        (signal.domain, signal.severity, signal.evidence)
        for signal in result.signals
    ] == [
        ("design", 3, "app.get('/v2/users', handler)"),
        ("security", 3, "SELECT * FROM users WHERE id = 1"),
        ("test", 2, "assert response.status == 200"),
        ("test", 2, "tests/api_test.py"),
    ]
    assert [result.primary, result.secondary] == [
        "design-reviewer",
        "security-reviewer",
    ]


def test_representative_risk_families_map_to_review_domains():
    cases = {
        "password = os.environ['API_SECRET']": "security-reviewer",
        "subprocess.run(command, shell=True)": "security-reviewer",
        "ALTER TABLE users ADD COLUMN role TEXT": "design-reviewer",
        "requirements = ['requests==1.0']": "design-reviewer",
        "state = 'transitioned'": "test-reviewer",
        "raise ValueError('invalid boundary')": "test-reviewer",
    }

    for line, reviewer in cases.items():
        assert analyze_diff("+ " + line, ["change.py"]).primary == reviewer


def test_design_file_changes_are_reviewed_without_matching_diff_text():
    result = analyze_diff("+ changed\n", ["src/configuration.py"])

    assert result.primary == "design-reviewer"
    assert result.signals[0].evidence == "src/configuration.py"


@pytest.mark.parametrize(
    ("line", "evidence"),
    [
        ("SELECT * FROM users", "SELECT * FROM users"),
        ("resource.owner_id != current_user_id", "resource.owner_id"),
        ("untrusted_payload", "untrusted_payload"),
        ("authenticate(request)", "authenticate(request)"),
        ("os.system(command)", "os.system(command)"),
        ("eval(user_input)", "eval(user_input)"),
    ],
)
def test_common_security_forms_select_security(line, evidence):
    result = analyze_diff("+ " + line, ["service.py"])

    assert result.primary == "security-reviewer"
    assert any(
        signal.domain == "security" and evidence in signal.evidence
        for signal in result.signals
    )
