"""New-run preflight and durable selection are wired ahead of execution."""
import json

import pytest

from rig_workbench.orchestrate import commands, config, runstate
from rig_workbench.orchestrate.orchestrators.base import Availability, UnsupportedAgentSpec
from rig_workbench.orchestrate.orchestrators.selection import select_orchestrator


class Backend:
    name = "t3"
    project_id = "project"

    def __init__(self, allowed=None):
        self.allowed = allowed
        self.requirements = []
        self.closed = 0
        self.client = self

    def available(self):
        return Availability(True, "compatible", "compatible")

    def capabilities(self):
        return frozenset({"agent.run", "agent.cancel", "thread.resume", "thread.durable"})

    def validate_agent(self, task, agent):
        self.requirements.append((task, agent))
        if self.allowed is not None and (agent.role, agent.model) not in self.allowed:
            raise UnsupportedAgentSpec("unsupported_agent_spec")

    def close(self):
        self.closed += 1


@pytest.fixture
def prepare(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INVOCATION_CWD", tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("RIG_T3_MCP_URL", "http://localhost/mcp")
    monkeypatch.setenv("RIG_T3_MCP_TOKEN", "secret")
    monkeypatch.setattr(commands, "load_manifest", lambda: {})
    path = tmp_path / "recipe.md"
    path.write_text("---\nname: recipe\nsteps:\n  - id: implement\n    instruction: implement\n    model: generation\n    verifier_model: review\n---\n")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _: path)
    return path, tmp_path / "state.json"


def _inject(monkeypatch, backend):
    monkeypatch.setattr(commands, "select_orchestrator", lambda *a, **kw: select_orchestrator(
        *a, **kw, factory=lambda _: backend))


def test_run_saves_the_selected_identity_and_distinct_role_models_before_execution(prepare, monkeypatch):
    _, path = prepare
    backend = Backend({("generator", "generation"), ("verifier", "review")})
    _inject(monkeypatch, backend)
    seen = []

    def execute(state, target, _gen, _ver, cfg, *_a, **_kw):
        saved = runstate.load_state(target)
        assert saved["orchestrator"]["name"] == "t3"
        assert saved["orchestrator"]["selected_by"] == "cli"
        assert cfg["_orchestrator_bridge"].backend is backend
        assert "orchestrator" not in saved["execution"]
        seen.append(state)
        return "DONE"

    monkeypatch.setattr(commands, "run_loop", execute)
    with pytest.raises(SystemExit) as completed:
        commands.cmd_run(["recipe", "--provider", "claude", "--verifier-provider", "codex",
                          "--orchestrator", "t3", "--out", str(path)])
    assert completed.value.code == 0
    assert len(seen) == 1 and backend.closed == 1
    assert {(agent.role, agent.model) for _, agent in backend.requirements} == {
        ("generator", "generation"), ("verifier", "review")}
    assert "secret" not in path.read_text()


def test_run_validates_actual_auto_route_candidates_without_treating_dictionary_keys_as_models(prepare, monkeypatch):
    recipe, path = prepare
    recipe.write_text("---\nname: recipe\nsteps:\n  - id: implement\n    instruction: implement\n    auto_route:\n      candidates:\n        - model: small\n          cost_tier: low\n          max_size: S\n        - model: large\n          cost_tier: high\n          max_size: XL\n---\n")
    backend = Backend()
    _inject(monkeypatch, backend)
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_kw: "DONE")
    with pytest.raises(SystemExit) as completed:
        commands.cmd_run(["recipe", "--provider", "mock", "--auto-route", "--out", str(path)])
    assert completed.value.code == 0
    assert {agent.model for _, agent in backend.requirements} == {"small", "large"}
    assert {agent.role for _, agent in backend.requirements} == {"generator", "verifier"}


def test_unresolved_external_invocation_preserves_isolation_without_teardown(prepare, monkeypatch):
    _, path = prepare
    backend = Backend()
    _inject(monkeypatch, backend)
    monkeypatch.setattr(commands, "setup_isolation", lambda _: {"dir": str(path.parent), "branch": "test"})
    monkeypatch.setattr(commands, "teardown_isolation", lambda *_a: pytest.fail("unresolved worktree was removed"))

    def execute(state, target, *_a, **_kw):
        state["orchestrator"]["invocations"]["call-1"] = {
            "step_id": "implement", "attempt": 1, "role": "generator", "persona": "implementer",
            "provider": "mock", "model": None, "orchestrator": "t3", "phase": "unknown", "result_applied": False,
        }
        state["orchestrator"]["ref"]["t3"]["threads"]["call-1"] = {"thread_id": "thread-1", "run_id": "run-1"}
        state["stopped"] = {"kind": "BLOCKED", "source": "orchestrator", "reason": "connection_lost"}
        runstate.save_state(state, target)
        return "BLOCKED"

    monkeypatch.setattr(commands, "run_loop", execute)
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(["recipe", "--provider", "mock", "--orchestrator", "t3", "--isolate", "--out", str(path)])
    assert stopped.value.code == 1
    saved = json.loads(path.read_text())
    assert saved["isolation"]["dir"] == str(path.parent)
    assert saved["orchestrator"]["ref"]["t3"]["threads"]["call-1"]["run_id"] == "run-1"


def test_cli_native_cannot_hide_invalid_manifest_settings(prepare, monkeypatch):
    _, path = prepare
    monkeypatch.setattr(commands, "load_manifest", lambda: {"orchestrator": {"t3": {"token": "secret"}}})
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_kw: pytest.fail("invalid manifest executed"))
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(["recipe", "--provider", "mock", "--orchestrator", "native", "--out", str(path)])
    assert stopped.value.code == 2 and not path.exists()


@pytest.mark.parametrize("mode", ["strict", "secure"])
@pytest.mark.parametrize("choice,fallback", [("t3", "native"), ("auto", "none")])
def test_run_rejects_t3_for_strict_and_secure_modes_before_execution_preflight(
    prepare, monkeypatch, mode, choice, fallback,
):
    recipe, path = prepare
    if mode == "secure":
        recipe.write_text("---\nname: secure-provider-execution\nsteps:\n  - id: implement\n    instruction: implement\n    policies: [secure-provider-execution]\n---\n")
    monkeypatch.setattr(commands, "load_manifest", lambda: {"orchestrator": {"fallback": fallback}})
    monkeypatch.setattr(commands, "preflight_secure_runtime", lambda *_a, **_kw: pytest.fail("T3 reached secure launcher preflight"))
    monkeypatch.setattr(commands, "setup_isolation", lambda *_a: pytest.fail("unsupported T3 mode created a worktree"))
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_kw: pytest.fail("unsupported T3 mode executed"))
    args = ["recipe", "--provider", "mock", "--orchestrator", choice, "--out", str(path)]
    if mode == "strict":
        args += ["--deterministic", "--isolate"]
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(args)
    assert stopped.value.code == 2 and not path.exists()


@pytest.mark.parametrize("mode", ["strict", "secure"])
def test_auto_native_fallback_preserves_existing_strict_and_secure_constraints(prepare, monkeypatch, mode, capsys):
    recipe, path = prepare
    choices = []

    def select(*args, **kwargs):
        selection = select_orchestrator(*args, **kwargs, factory=lambda _: pytest.fail("Native-only mode probed T3"))
        choices.append(selection.name)
        return selection

    monkeypatch.setattr(commands, "select_orchestrator", select)
    monkeypatch.setattr(commands, "run_loop", lambda *_a, **_kw: pytest.fail("invalid Native constraints executed"))
    if mode == "secure":
        recipe.write_text("---\nname: secure-provider-execution\nsteps:\n  - id: implement\n    instruction: implement\n    policies: [secure-provider-execution]\n---\n")
        extra = []  # The existing private-goal requirement still refuses this run.
    else:
        extra = ["--deterministic", "--deterministic-task", "missing-task"]
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_run(["recipe", "--provider", "mock", "--orchestrator", "auto", "--out", str(path), *extra])
    assert choices == ["native"]
    assert stopped.value.code == 2
    output = capsys.readouterr().out
    assert "requires a private goal" in output if mode == "secure" else "deterministic" in output
