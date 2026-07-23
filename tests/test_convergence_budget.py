"""Tests for the opt-in convergence budget (RIG_CONVERGENCE_K).

The convergence budget is a model-invariance lever: raising the per-step retry
cap lets a run keep feeding the distilled previous_failure (#333) back to the
generator for more attempts, so a weaker model gets more chances to converge on
a gate-passing result instead of escalating. It must only ever *raise* a step's
K, never lower an explicit recipe value, and must be a no-op when unset.
"""

from __future__ import annotations

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.recipes import load_steps


def test_env_int_parsing(monkeypatch):
    monkeypatch.setenv("RIG_TEST_K", "5")
    assert config._env_int("RIG_TEST_K", 0) == 5
    monkeypatch.setenv("RIG_TEST_K", "")
    assert config._env_int("RIG_TEST_K", 3) == 3
    monkeypatch.setenv("RIG_TEST_K", "not-an-int")
    assert config._env_int("RIG_TEST_K", 3) == 3
    monkeypatch.setenv("RIG_TEST_K", "-4")  # negative rejected
    assert config._env_int("RIG_TEST_K", 3) == 3
    monkeypatch.delenv("RIG_TEST_K", raising=False)
    assert config._env_int("RIG_TEST_K", 9) == 9


def test_effective_k_unset_preserves_defaults(monkeypatch):
    monkeypatch.setattr(config, "CONVERGENCE_K", 0)
    assert config.effective_k(None) == config.DEFAULT_K
    assert config.effective_k(4) == 4  # explicit recipe value preserved


def test_effective_k_raises_but_never_lowers(monkeypatch):
    monkeypatch.setattr(config, "CONVERGENCE_K", 6)
    assert config.effective_k(None) == 6      # default 2 raised to 6
    assert config.effective_k(2) == 6          # low recipe value raised
    assert config.effective_k(10) == 10        # higher recipe value never lowered


def _fm_with_step(max_retries):
    step = {"id": "acceptance", "instruction": "acceptance-check", "gate": "acceptance-gate"}
    if max_retries is not None:
        step["max_retries"] = max_retries
    return {"steps": [step]}


def test_load_steps_applies_convergence_budget(monkeypatch):
    monkeypatch.setattr(config, "CONVERGENCE_K", 5)
    steps = load_steps(_fm_with_step(max_retries=2))
    assert steps[0]["max_retries"] == 5


def test_load_steps_unset_uses_recipe_or_default(monkeypatch):
    monkeypatch.setattr(config, "CONVERGENCE_K", 0)
    assert load_steps(_fm_with_step(max_retries=None))[0]["max_retries"] == config.DEFAULT_K
    assert load_steps(_fm_with_step(max_retries=3))[0]["max_retries"] == 3
