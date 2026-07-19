"""RETRY gate-failure feedback (#333): a gate-failure RETRY used to reset st["checks"] /
st["verdicts"] before the retry generator ever saw the reviewer's findings — the retry was
blind. compute_next now distills failed checks and dissenting verdicts into a short string
(_distill_failures), records it on the FAIL history entry, and stashes it in st["last_failure"]
so _build_step_contract's existing `previous_failure:` wiring surfaces it to the next attempt."""

from rig_workbench.orchestrate.providers import _build_step_contract
from rig_workbench.orchestrate.runstate import _distill_failures, compute_next, new_state


def _run_and_fail(state, step, verdicts=None, checks_ok=None):
    """Drive one step from pending through to a failed gate, recording checks/verdicts first."""
    action, _ = compute_next(state)  # START
    assert action == "START"
    st = state["step_state"][step["id"]]
    if checks_ok is not None:
        st["checks"] = [{"cmd": c, "ok": ok} for c, ok in zip(step["checks"], checks_ok)]
    if verdicts is not None:
        st["verdicts"] = list(verdicts)
    return compute_next(state)  # RETRY or ESCALATE


def test_retry_carries_dissenting_verdicts_not_passing_ones(step_factory):
    step = step_factory(id="review", gate="review-gate", max_retries=3)
    state = new_state("t", [step], None)
    verdicts = [
        {"by": "claude:security-reviewer", "ok": False, "note": "SQL injection in login handler"},
        {"by": "claude:perf-reviewer", "ok": False, "note": "N+1 query in list endpoint"},
        {"by": "claude:style-reviewer", "ok": True, "note": "looks clean"},
    ]
    action, _ = _run_and_fail(state, step, verdicts=verdicts)
    assert action == "RETRY"
    st = state["step_state"]["review"]
    assert st["verdicts"] == []  # records reset as before
    lf = st["last_failure"]
    assert "claude:security-reviewer" in lf and "SQL injection in login handler" in lf
    assert "claude:perf-reviewer" in lf and "N+1 query in list endpoint" in lf
    assert "claude:style-reviewer" not in lf  # the passing verdict is not a finding
    assert "looks clean" not in lf


def test_fail_history_entry_carries_findings(step_factory):
    step = step_factory(id="review", gate="review-gate", max_retries=3)
    state = new_state("t", [step], None)
    verdicts = [{"by": "claude:security-reviewer", "ok": False, "note": "hardcoded secret"}]
    _run_and_fail(state, step, verdicts=verdicts)
    fail_entries = [h for h in state["history"] if h["action"] == "FAIL"]
    assert len(fail_entries) == 1
    assert "claude:security-reviewer" in fail_entries[0]["findings"]
    assert "hardcoded secret" in fail_entries[0]["findings"]


def test_build_step_contract_surfaces_findings_after_retry(step_factory):
    step = step_factory(id="review", gate="review-gate", max_retries=3)
    state = new_state("t", [step], None)
    verdicts = [{"by": "claude:security-reviewer", "ok": False, "note": "missing input validation"}]
    action, _ = _run_and_fail(state, step, verdicts=verdicts)
    assert action == "RETRY"
    st = state["step_state"]["review"]
    contract = _build_step_contract(state, step, st)
    assert "previous_failure:" in contract
    assert "claude:security-reviewer" in contract
    assert "missing input validation" in contract


def test_k_exhausted_final_fail_entry_carries_findings(step_factory):
    step = step_factory(id="review", gate="review-gate", max_retries=2)
    state = new_state("t", [step], None)
    v1 = [{"by": "claude:security-reviewer", "ok": False, "note": "attempt 1 problem"}]
    action, _ = _run_and_fail(state, step, verdicts=v1)
    assert action == "RETRY"
    v2 = [{"by": "claude:security-reviewer", "ok": False, "note": "attempt 2 still broken"}]
    action, _ = _run_and_fail(state, step, verdicts=v2)
    assert action == "ESCALATE"
    fail_entries = [h for h in state["history"] if h["action"] == "FAIL"]
    assert len(fail_entries) == 2
    assert "attempt 1 problem" in fail_entries[0]["findings"]
    assert "attempt 2 still broken" in fail_entries[1]["findings"]


def test_distill_bounded_for_huge_notes():
    # a single 5000-char note is clipped to 240 chars per-verdict before joining, so use
    # several dissenting verdicts to push the *joined* total over the 800-char cap too.
    st = {"checks": [], "verdicts": [
        {"by": f"claude:reviewer-{i}", "ok": False, "note": "X" * 5000} for i in range(6)
    ]}
    findings = _distill_failures(st)
    assert findings is not None
    assert len(findings) <= 810  # 800-char clip + "…" (a little slack for the ellipsis char)
    assert findings.endswith("…")


def test_distill_none_when_nothing_failed():
    st = {"checks": [{"cmd": "true", "ok": True}], "verdicts": [{"by": "r", "ok": True, "note": "fine"}]}
    assert _distill_failures(st) is None


def test_machine_check_failure_unchanged_and_combines_with_verdicts(step_factory):
    step = step_factory(id="verify", gate="acceptance-gate", checks=["true", "false"], max_retries=3)
    state = new_state("t", [step], None)
    action, _ = _run_and_fail(state, step, checks_ok=[True, False])
    assert action == "RETRY"
    st = state["step_state"]["verify"]
    assert "check failed: false" in st["last_failure"]

    # both checks and verdicts failing → both kinds of findings appear
    step2 = step_factory(id="verify2", gate="acceptance-gate", checks=["false"], max_retries=3)
    state2 = new_state("t", [step2], None)
    action2, _ = compute_next(state2)  # START
    assert action2 == "START"
    st2 = state2["step_state"]["verify2"]
    st2["checks"] = [{"cmd": "false", "ok": False}]
    st2["verdicts"] = [{"by": "claude:reviewer", "ok": False, "note": "also broken semantically"}]
    action2, _ = compute_next(state2)
    assert action2 == "RETRY"
    assert "check failed: false" in st2["last_failure"]
    assert "claude:reviewer" in st2["last_failure"] and "also broken semantically" in st2["last_failure"]
