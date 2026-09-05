"""A provider subprocess must be marked, so rig's own blocking hooks stand down in it.

The bug this pins: `hooks/suggest-instincts.sh` is a Stop hook that *blocks* the stop
and spends a round-trip asking the model whether it learned an instinct. Inside a
`codex exec` the orchestrator spawned for a verdict, that reply becomes the last
assistant message — which is what the CLI prints and what `run_provider` captures. The
verdict never comes back. Observed as a step whose verifier answered all thirteen
criteria with `VERDICT: PASS` being recorded as `ok: false, criteria: []`, then
escalating after two identical retries.
"""
import subprocess

from rig_workbench.orchestrate import providers


def _capture_env(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="VERDICT: PASS", stderr="")

    monkeypatch.setattr(providers.subprocess, "run", fake_run)
    return seen


def test_generator_subprocess_is_marked(monkeypatch):
    seen = _capture_env(monkeypatch)
    providers.run_provider("codex", "generator", "do the thing", {})
    assert seen["env"] is not None, "env must be passed explicitly, not inherited"
    assert seen["env"].get("RIG_PROVIDER_SUBPROCESS") == "1"


def test_verifier_subprocess_is_marked(monkeypatch):
    seen = _capture_env(monkeypatch)
    providers.run_provider("codex", "verifier", "judge the thing", {})
    assert seen["env"].get("RIG_PROVIDER_SUBPROCESS") == "1"


def test_marking_does_not_drop_the_inherited_environment(monkeypatch):
    """The marker is added to the environment, not substituted for it — a provider
    still needs PATH, HOME and its own credentials to run at all."""
    monkeypatch.setenv("RIG_TEST_INHERITED_VALUE", "kept")
    seen = _capture_env(monkeypatch)
    providers.run_provider("codex", "generator", "do the thing", {})
    assert seen["env"].get("RIG_TEST_INHERITED_VALUE") == "kept"
    assert "PATH" in seen["env"]
