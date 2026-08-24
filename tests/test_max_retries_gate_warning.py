"""`max_retries` on a non-acceptance-gate step: the validator used to call it a no-op (#495).

The old WARN read "max_retries is set on a step without gate: acceptance-gate (no effect in
this context)". That was false, and following it removed a working safety property:
`skills/engine/recipes/adaptive-bugfix.md`'s `targeted-review` sets `max_retries: 1` on a
**review-gate** step so a failed review escalates instead of retrying, exactly as that
recipe's body declares. Deleting the key would raise K to `config.DEFAULT_K` (2).

K is read at `runstate.compute_next`'s generic verdict path, not inside a gate handler, so
it governs any step whose gate can report a failure. The measured tests below drive
`compute_next` so the reasoning cannot go stale silently: if the retry path ever stops
consulting K, or a gateless step starts being able to fail, they fail here rather than
letting the validator's prose quietly become a lie again.
"""

import pathlib

import pytest

from rig_workbench.orchestrate.config import DEFAULT_K
from rig_workbench.orchestrate.gates import RUNTIME_GATES
from rig_workbench.orchestrate.runstate import compute_next, gate_outcome, new_state
from rig_workbench.validation import state as validation_state
from rig_workbench.validation.recipes import _check_max_retries, check_recipe

CTX = "recipe demo step targeted-review"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_validation_state():
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    yield
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0


def _warns() -> list[str]:
    return [r for r in validation_state.results if r.startswith("[WARN]")]


def _fails() -> list[str]:
    return [r for r in validation_state.results if r.startswith("[FAIL]")]


# ── which steps the WARN must stay silent about ──────────────────────────────


@pytest.mark.parametrize("gate", ["acceptance-gate", "review-gate"])
def test_a_runtime_gate_step_with_max_retries_is_not_warned_about(gate):
    """The regression: K governs the retry count on both runtime gates (measured below)."""
    _check_max_retries({"gate": gate, "checks": [], "max_retries": 1}, CTX)
    assert validation_state.results == []


def test_a_gateless_step_that_declares_checks_is_not_warned_about():
    """`gate_outcome` judges declared checks *before* it looks at the gate, so a gateless
    step with checks can fail — and then K decides how often it retries."""
    _check_max_retries({"gate": None, "checks": ["pytest -q"], "max_retries": 3}, CTX)
    assert validation_state.results == []


def test_an_absent_max_retries_is_not_checked():
    _check_max_retries({"gate": None, "checks": []}, CTX)
    assert validation_state.results == []


# ── positive controls: what the WARN must object to ──────────────────────────


@pytest.mark.parametrize("gate", [None, "", "—", "-", "custom-aggregation"])
def test_a_step_with_no_runtime_gate_and_no_checks_is_warned_about(gate):
    """Absent, empty, placeholder and unresolvable gate values all reach the same verdict:
    nothing in the step can report a failure, so the retry budget is never read."""
    _check_max_retries({"gate": gate, "checks": [], "max_retries": 2}, CTX)
    assert len(_warns()) == 1
    assert "max_retries has no effect on this step" in _warns()[0]
    assert _fails() == []


def test_the_warning_does_not_claim_gate_kind_decides_it():
    """`pitfall_claim_only_what_the_check_holds`: the message must not resurrect the old
    (false) claim that only acceptance-gate makes max_retries meaningful."""
    _check_max_retries({"gate": None, "checks": [], "max_retries": 2}, CTX)
    message = _warns()[0]
    assert "without gate: acceptance-gate" not in message
    assert "review-gate" in message  # it says K applies there, rather than denying it


def test_the_gate_registry_is_the_two_runtime_gates():
    """The check reuses `is_runtime_gate` rather than re-deriving the set
    (`pitfall_derive_each_layer_does_not_converge`). Pinned literally, both directions."""
    assert set(RUNTIME_GATES) == {"acceptance-gate", "review-gate"}
    assert "custom-aggregation" not in RUNTIME_GATES


# ── the value check that shares the helper ───────────────────────────────────


@pytest.mark.parametrize("value", [0, -1, "2", 1.5])
def test_an_unusable_max_retries_value_fails(value):
    _check_max_retries({"gate": "review-gate", "checks": [], "max_retries": value}, CTX)
    assert len(_fails()) == 1
    assert "must be an integer ≥1" in _fails()[0]


def test_a_bad_value_on_a_gateless_step_reports_both_facts():
    """Two independent defects, two lines — the value is unusable *and* the context is inert."""
    _check_max_retries({"gate": None, "checks": [], "max_retries": 0}, CTX)
    assert len(_fails()) == 1
    assert len(_warns()) == 1


# ── the shipped recipe the false WARN was advising against ───────────────────


def test_adaptive_bugfix_targeted_review_keeps_max_retries_and_draws_no_warning():
    recipe = REPO_ROOT / "skills" / "engine" / "recipes" / "adaptive-bugfix.md"
    assert recipe.is_file(), f"{recipe} moved; this test checks nothing until it is repointed"
    check_recipe(recipe)
    assert [r for r in validation_state.results if "max_retries" in r] == []
    text = recipe.read_text(encoding="utf-8")
    assert "targeted-review" in text and "max_retries: 1" in text


# ── measured: what K actually does (drives the state machine) ────────────────


def _drive_to_escalation(step, *, failing_check=False, limit=16):
    """Run one step, failing its gate on every attempt, and return the action sequence."""
    state = new_state("t", [step], None)
    actions = []
    for _ in range(limit):
        action, _msg = compute_next(state)
        actions.append(action)
        if action in ("ESCALATE", "DONE", "BLOCKED", "STOPPED"):
            break
        if action == "AWAIT":
            st = state["step_state"][step["id"]]
            if failing_check:
                st["checks"] = [{"cmd": c, "ok": False} for c in step["checks"]]
            else:
                st["verdicts"] = [{"by": "claude:reviewer", "ok": False, "note": "nope"}]
    return actions, state["step_state"][step["id"]]["retries"]


def test_k_governs_the_retry_count_on_a_review_gate_step(step_factory):
    """K=1 escalates on the first failure; K=3 retries twice first. Written out literally
    so shrinking the behaviour cannot shrink the test with it."""
    actions, retries = _drive_to_escalation(
        step_factory(id="review", gate="review-gate", max_retries=1))
    assert actions == ["START", "AWAIT", "ESCALATE"]
    assert actions.count("RETRY") == 0
    assert retries == 1

    actions, retries = _drive_to_escalation(
        step_factory(id="review", gate="review-gate", max_retries=3))
    assert actions == ["START", "AWAIT", "RETRY", "START", "AWAIT", "RETRY",
                       "START", "AWAIT", "ESCALATE"]
    assert actions.count("RETRY") == 2
    assert retries == 3


def test_the_same_k_produces_the_same_shape_on_an_acceptance_gate_step(step_factory):
    """The two runtime gates are indistinguishable from K's point of view — which is why
    the old 'acceptance-gate only' condition had nothing to stand on."""
    for gate in ("acceptance-gate", "review-gate"):
        assert _drive_to_escalation(step_factory(id="s", gate=gate, max_retries=2)) == (
            ["START", "AWAIT", "RETRY", "START", "AWAIT", "ESCALATE"], 2)


def test_a_gateless_step_with_no_checks_never_retries_whatever_k_says(step_factory):
    """The claim the WARN now makes. A recorded failing verdict does not even count:
    `gate_outcome` returns "pass" for a step with no gate and no checks."""
    for k in (1, 2, 3):
        step = step_factory(id="plain", gate=None, max_retries=k)
        actions, retries = _drive_to_escalation(step)
        assert actions == ["START", "DONE"]
        assert retries == 0
    st = {"status": "running", "retries": 0, "checks": [], "approvals": [],
          "verdicts": [{"by": "claude:reviewer", "ok": False, "note": "nope"}]}
    assert gate_outcome(step_factory(id="plain", gate=None), st) == "pass"


def test_a_gateless_step_with_checks_does_retry_k_times(step_factory):
    """Why the condition is not simply `gate is None`: declared checks are judged before
    the gate is, so K is live on a gateless step that has them."""
    step = step_factory(id="verify", gate=None, checks=["false"], max_retries=3)
    actions, retries = _drive_to_escalation(step, failing_check=True)
    assert actions == ["START", "AWAIT", "RETRY", "START", "AWAIT", "RETRY",
                       "START", "AWAIT", "ESCALATE"]
    assert retries == 3


def test_default_k_is_what_deleting_the_key_would_have_bought(step_factory):
    """Concretely: obeying the old WARN on `targeted-review` would have turned its
    `max_retries: 1` (escalate at once) into DEFAULT_K retries."""
    assert DEFAULT_K == 2
    actions, _ = _drive_to_escalation(step_factory(id="review", gate="review-gate"))
    assert actions.count("RETRY") == 1  # DEFAULT_K applies when the step omits the key
