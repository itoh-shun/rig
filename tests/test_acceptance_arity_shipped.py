"""The arity rule against the shipped catalogue rather than a hand-built step (#503).

The unit tests pin `gate_outcome`; these pin that a real recipe's acceptance step is judged
by it, loaded through the same `resolve_recipe` → `load_steps` path `rig-wb run` uses. Without
this, a recipe whose declared criteria stopped reaching the run state would still show green
in the unit tests while `rig-wb run bugfix --provider mock` passed on one answer.
"""

import pytest

from rig_workbench.orchestrate import providers
from rig_workbench.orchestrate.providers import run_loop
from rig_workbench.orchestrate.recipes import (load_steps, parse_frontmatter,
                                               resolve_recipe)
from rig_workbench.orchestrate.gates import is_runtime_gate
from rig_workbench.orchestrate.runstate import new_state

#: Recipes whose gated step declares criteria, with the count the shipped file declares. A
#: recipe that changes its declaration fails here rather than silently loosening the pin. The
#: two gate kinds are both represented on purpose: arity is a property of a runtime gate that
#: was handed declared criteria, not of the word `acceptance-gate`.
SHIPPED = [("bugfix", "acceptance", 13), ("adaptive-bugfix", "targeted-review", 4)]


def _shipped_steps(recipe):
    return load_steps(parse_frontmatter(resolve_recipe(recipe)))


def _run(recipe, steps, answers, tmp_path, monkeypatch):
    def fake_run_provider(provider, role, prompt, cfg, persona="", state=None,
                          step_id=None):
        if role == "verifier":
            return 0, answers + "No blocking defect.\nVERDICT: PASS"
        return 0, "STATUS: done"

    monkeypatch.setattr(providers, "run_provider", fake_run_provider)
    state = new_state(recipe, steps, "fix")
    return state, run_loop(state, None, "mock", "mock", {"cwd": str(tmp_path)}, 40,
                           quiet=True)


def _answers(count):
    return "".join(f"CRITERION {n}: PASS — f.py:1\n" for n in range(1, count + 1))


@pytest.fixture(autouse=True)
def _stub_checks(monkeypatch):
    """Machine checks are not what these tests are about, and the shipped recipes run real
    commands. Pass them so the verdict is the only thing deciding the step."""
    def pass_checks(step, st, cfg):
        st["checks"] = [{"cmd": c, "ok": True} for c in step["checks"]]
        st["last_failure"] = None
    monkeypatch.setattr(providers, "_run_step_checks", pass_checks)


@pytest.mark.parametrize("recipe,step_id,declared", SHIPPED)
def test_shipped_acceptance_step_still_declares_what_the_gate_judges(recipe, step_id,
                                                                     declared):
    """Positive control for the two tests below: they only mean something while the shipped
    recipe declares these criteria and the loader carries them into the step."""
    step = next(s for s in _shipped_steps(recipe) if s["id"] == step_id)
    assert len(step["acceptance"]) == declared
    assert is_runtime_gate(step["gate"])


@pytest.mark.parametrize("recipe,step_id,declared", SHIPPED)
def test_a_shipped_run_is_not_done_until_every_declared_criterion_is_answered(
    recipe, step_id, declared, tmp_path, monkeypatch
):
    """One short of the declaration does not finish the run — the case a floor of one let
    through, where a 13-criterion step passed on a single answer."""
    steps = _shipped_steps(recipe)
    state, final = _run(recipe, steps, _answers(declared - 1), tmp_path, monkeypatch)

    assert final != "DONE"
    failures = [h for h in state["history"]
                if h.get("action") == "FAIL" and h.get("step") == step_id]
    assert failures and failures[0]["outcome"] == "unanswered"
    assert f"answering {declared - 1} of its {declared} declared criteria" \
        in failures[0]["findings"]
    assert f"still unanswered: {declared}" in failures[0]["findings"]


@pytest.mark.parametrize("recipe,step_id,declared", SHIPPED)
def test_a_shipped_run_answering_every_criterion_finishes(recipe, step_id, declared,
                                                          tmp_path, monkeypatch):
    """The other half of the control: arity is a rule the shipped catalogue can satisfy, not
    a re-baselining of it. The mock provider emits one line per declared criterion (#519), so
    a real `--provider mock` run of these recipes still reaches DONE."""
    steps = _shipped_steps(recipe)
    state, final = _run(recipe, steps, _answers(declared), tmp_path, monkeypatch)

    assert final == "DONE"
    verdict = state["step_state"][step_id]["verdicts"][0]
    assert verdict["answered"] == verdict["declared"] == declared
    # Each answer resolves back to the criterion it judged: `CRITERION 3` alone is
    # unresolvable from a record that pins no recipe version.
    assert [c["criterion"] for c in verdict["criteria"]] \
        == list(steps[[s["id"] for s in steps].index(step_id)]["acceptance"])
