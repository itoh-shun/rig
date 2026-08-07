"""The human gate, end to end through the deterministic orchestrator (v2.1).

The orchestrator could already stop a run (K gate failures → escalate). What it
could not do was *park* one: halt at a named stage, stay halted across processes,
and resume when a qualified person signs off. These tests pin that down, plus the
promise that a recipe declaring none of it behaves exactly as it did before.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ORCHESTRATE = REPO_ROOT / "scripts" / "orchestrate.py"

RECIPE = """---
name: staged
description: two stages, the second gated on a person
scope: project
autonomy: interactive
steps:
  - id: implement
    instruction: implement
    gate: acceptance-gate
    acceptance: ["it builds"]
    checks: ["true"]
  - id: architecture_review
    instruction: verify
    actor: architect
    human_gate: true
    gate: acceptance-gate
    acceptance: ["ADR updated"]
    checks: ["true"]
---
"""

UNGATED = RECIPE.replace("    actor: architect\n    human_gate: true\n", "")

POLICY = {
    "schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
    "roles": {"developer": ["task.new", "gate.set", "accept", "discard"],
              "architect": ["approve", "accept"]},
    "members": {"alice": ["developer"], "olivia": ["architect"], "dana": ["architect"]},
}


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "alice"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".rig" / "recipes").mkdir(parents=True)
    (tmp_path / ".rig" / "recipes" / "staged.md").write_text(RECIPE, encoding="utf-8")
    return tmp_path


def govern(repo, policy=None):
    (repo / ".rig" / "policy").mkdir(parents=True, exist_ok=True)
    (repo / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    (repo / ".rig" / "policy" / "org.json").write_text(
        json.dumps(policy or POLICY), encoding="utf-8")


def run(repo, *args, actor="alice"):
    env = dict(os.environ)
    env.update({"RIG_ACTOR": actor, "RIG_HOME": str(REPO_ROOT),
                "RIG_ALLOW_PROJECT_RECIPES": "1", "RIG_SKIP_GH_CHECK": "1",
                "PYTHONPATH": str(REPO_ROOT)})
    return subprocess.run([sys.executable, str(ORCHESTRATE), *args],
                          capture_output=True, text=True, cwd=repo, timeout=120, env=env)


def out(result):
    return result.stdout + result.stderr


def drive_to_gate(repo, actor="alice"):
    """init → clear step 1 → start step 2 → clear its machine checks."""
    assert run(repo, "init", ".rig/recipes/staged.md", actor=actor).returncode == 0
    run(repo, "check", actor=actor)
    run(repo, "next", actor=actor)          # implement passes
    run(repo, "next", actor=actor)          # architecture_review starts
    run(repo, "check", actor=actor)
    return run(repo, "next", actor=actor)   # → parks


# ── the gate parks the run ───────────────────────────────────────────────────
def test_a_gated_stage_parks_the_run_instead_of_advancing(repo):
    govern(repo)
    result = drive_to_gate(repo)
    assert result.returncode == 3, out(result)
    assert "AWAIT_APPROVAL" in result.stdout
    assert "awaits human sign-off (0/1, from architect)" in result.stdout


def test_the_parked_state_survives_the_process(repo):
    govern(repo)
    drive_to_gate(repo)
    state = json.loads((repo / "run-state.json").read_text(encoding="utf-8"))
    assert state["step_state"]["architecture_review"]["status"] == "awaiting_approval"
    assert state["cursor"] == 1                      # never advanced
    assert state["done"] is False
    status = run(repo, "status")
    assert "awaiting_approval" in status.stdout
    assert "roles: architect" in status.stdout


def test_next_keeps_parking_until_somebody_signs(repo):
    govern(repo)
    drive_to_gate(repo)
    again = run(repo, "next")
    assert again.returncode == 3
    assert "AWAIT_APPROVAL" in again.stdout


def test_a_qualified_approval_releases_the_run(repo):
    govern(repo)
    drive_to_gate(repo)
    approved = run(repo, "approve", "architecture_review", "--note", "boundaries ok", actor="olivia")
    assert approved.returncode == 0, out(approved)
    assert "DONE" in approved.stdout
    state = json.loads((repo / "run-state.json").read_text(encoding="utf-8"))
    assert state["done"] is True
    assert state["step_state"]["architecture_review"]["status"] == "passed"


def test_an_actor_without_approve_cannot_release_it(repo):
    govern(repo)
    drive_to_gate(repo)
    denied = run(repo, "approve", "architecture_review", actor="alice")
    assert denied.returncode == 1
    assert "not permitted to approve" in out(denied)
    assert "held by architect" in out(denied)


def test_whoever_ran_the_stage_cannot_sign_it_off(repo):
    govern(repo)
    drive_to_gate(repo, actor="olivia")            # olivia did the work
    result = run(repo, "approve", "architecture_review", actor="olivia")
    assert result.returncode == 3                  # still parked
    assert "separation of duties" in out(result)
    dana = run(repo, "approve", "architecture_review", actor="dana")
    assert dana.returncode == 0 and "DONE" in dana.stdout


def test_a_denial_keeps_the_run_parked_and_names_the_denier(repo):
    govern(repo)
    drive_to_gate(repo)
    result = run(repo, "approve", "architecture_review", "--deny",
                 "--note", "no ADR recorded", actor="olivia")
    assert result.returncode == 3
    assert "rejected by olivia" in out(result) and "no ADR recorded" in out(result)


def test_a_later_approval_supersedes_an_earlier_denial(repo):
    govern(repo)
    drive_to_gate(repo)
    run(repo, "approve", "architecture_review", "--deny", "--note", "no ADR", actor="olivia")
    fixed = run(repo, "approve", "architecture_review", "--note", "ADR added", actor="olivia")
    assert fixed.returncode == 0 and "DONE" in fixed.stdout


def test_approving_a_step_with_no_human_gate_is_refused(repo):
    govern(repo)
    drive_to_gate(repo)
    result = run(repo, "approve", "implement", actor="olivia")
    assert result.returncode == 1
    assert "declares no human gate" in out(result)


def test_approving_an_unknown_step_lists_the_real_ones(repo):
    govern(repo)
    drive_to_gate(repo)
    result = run(repo, "approve", "nope", actor="olivia")
    assert result.returncode == 1
    assert "no step `nope`" in out(result) and "architecture_review" in out(result)


# ── ownership is advisory, not a block ───────────────────────────────────────
def test_running_a_stage_outside_its_owning_role_warns_but_proceeds(repo):
    """Blocking here would break every CI-driven run for no safety gain: rig cannot
    verify that a human architect typed anything, only that one signed."""
    govern(repo)
    run(repo, "init", ".rig/recipes/staged.md")
    run(repo, "check")
    run(repo, "next")
    started = run(repo, "next")                    # alice starts the architect's stage
    assert started.returncode == 0
    assert "[WARN]" in started.stdout
    assert "owned by role `architect`" in started.stdout
    state = json.loads((repo / "run-state.json").read_text(encoding="utf-8"))
    assert state["step_state"]["architecture_review"]["ran_as"] == "alice"


# ── the org can gate a stage the recipe never asked about ────────────────────
def test_a_policy_stage_rule_gates_an_ungated_recipe(repo):
    (repo / ".rig" / "recipes" / "plain.md").write_text(UNGATED.replace("name: staged",
                                                                       "name: plain"),
                                                        encoding="utf-8")
    govern(repo, {**POLICY, "approvals": {"stage:architecture_review": {
        "quorum": 1, "roles": ["architect"]}}})
    assert run(repo, "init", ".rig/recipes/plain.md").returncode == 0
    run(repo, "check")
    run(repo, "next")
    run(repo, "next")
    run(repo, "check")
    parked = run(repo, "next")
    assert parked.returncode == 3
    assert "awaits human sign-off" in parked.stdout


# ── compatibility ────────────────────────────────────────────────────────────
def test_a_recipe_without_a_human_gate_runs_straight_through(repo):
    govern(repo)
    (repo / ".rig" / "recipes" / "plain.md").write_text(UNGATED.replace("name: staged",
                                                                       "name: plain"),
                                                        encoding="utf-8")
    run(repo, "init", ".rig/recipes/plain.md")
    run(repo, "check")
    run(repo, "next")
    run(repo, "next")
    run(repo, "check")
    done = run(repo, "next")
    assert done.returncode == 0
    assert "DONE" in done.stdout
    assert "AWAIT_APPROVAL" not in done.stdout


def test_an_ungoverned_repo_ignores_a_policy_stage_key_entirely(repo):
    """No .rig/org.json → the recipe's own human_gate still applies (it is the
    recipe's own request), but nothing about org policy is consulted."""
    result = drive_to_gate(repo)
    assert result.returncode == 3
    assert "awaits human sign-off" in result.stdout
    # ...and anyone may clear it, because no policy restricts `approve`
    cleared = run(repo, "approve", "architecture_review", actor="anybody")
    assert cleared.returncode == 0 and "DONE" in cleared.stdout


def test_a_broken_policy_refuses_to_open_a_gated_stage(repo):
    """The fail-closed rule from v2, applied here: a policy that will not parse must
    never read as 'no gate'."""
    govern(repo)
    drive_to_gate(repo)
    (repo / ".rig" / "policy" / "org.json").write_text("{ broken", encoding="utf-8")
    result = run(repo, "next")
    assert result.returncode != 0
    assert "BLOCKED" in out(result) or "cannot be evaluated" in out(result)


# ── the decision reaches the tamper-evident ledger ───────────────────────────
def test_stage_decisions_are_recorded_in_the_ledger(repo):
    govern(repo)
    drive_to_gate(repo)
    run(repo, "approve", "architecture_review", "--note", "ok", actor="olivia")
    entries = [json.loads(line) for line in
               (repo / ".rig" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    stage = [e for e in entries if e["action"] == "stage.approve"]
    assert stage and stage[-1]["actor"] == "olivia"
    assert stage[-1]["subject"] == "staged:architecture_review"
    assert stage[-1]["data"]["step"] == "architecture_review"
    assert stage[-1]["org"] == "acme" and stage[-1]["team"] == "team-a"


def test_a_denial_is_recorded_too(repo):
    govern(repo)
    drive_to_gate(repo)
    run(repo, "approve", "architecture_review", "--deny", "--note", "no", actor="olivia")
    entries = [json.loads(line) for line in
               (repo / ".rig" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["action"] == "stage.deny" for e in entries)


# ── plan surfaces the gate before anything runs ──────────────────────────────
def test_plan_shows_the_owner_and_the_gate(repo):
    result = run(repo, "plan", ".rig/recipes/staged.md")
    assert "actor=architect" in result.stdout
    assert "human gate (quorum 1)" in result.stdout


# ── the DAG runner parks too, and says why ───────────────────────────────────
def _dag_steps(step_factory):
    return [
        step_factory(id="a", gate="acceptance-gate", checks=["true"]),
        step_factory(id="b", needs=["a"], gate="acceptance-gate", checks=["true"],
                     human_gate=True),
    ]


def test_the_dag_runner_parks_on_a_human_gate(step_factory, tmp_path, monkeypatch):
    """`needs:` switches the runner to DAG mode, which evaluates gates on its own
    path — a parked step must not be reported there as a dependency failure."""
    from rig_workbench.orchestrate.providers import run_loop
    from rig_workbench.orchestrate.runstate import new_state

    monkeypatch.chdir(tmp_path)
    state = new_state("dag", _dag_steps(step_factory), None)
    final = run_loop(state, None, "mock", "mock", {"cwd": str(tmp_path)}, 20, quiet=True)
    assert final == "AWAIT_APPROVAL"
    assert state["step_state"]["b"]["status"] == "awaiting_approval"
    assert state["step_state"]["a"]["status"] == "passed"
    assert state["stopped"] is None          # parked, not stopped


def test_the_dag_runner_finishes_once_the_gate_is_signed(step_factory, tmp_path, monkeypatch):
    from rig_workbench.govern.approval import make_decision
    from rig_workbench.orchestrate.providers import run_loop
    from rig_workbench.orchestrate.runstate import new_state

    monkeypatch.chdir(tmp_path)
    state = new_state("dag", _dag_steps(step_factory), None)
    run_loop(state, None, "mock", "mock", {"cwd": str(tmp_path)}, 20, quiet=True)
    st = state["step_state"]["b"]
    st["approvals"] = [make_decision(actor="olivia", decision="approve", roles=[])]
    st["status"] = "pending"                 # the runner re-evaluates it on the next wave
    st["checks"] = []
    final = run_loop(state, None, "mock", "mock", {"cwd": str(tmp_path)}, 20, quiet=True)
    assert final == "DONE"
    assert state["step_state"]["b"]["status"] == "passed"


def test_an_ungated_dag_is_untouched(step_factory, tmp_path, monkeypatch):
    from rig_workbench.orchestrate.providers import run_loop
    from rig_workbench.orchestrate.runstate import new_state

    monkeypatch.chdir(tmp_path)
    steps = [step_factory(id="a", gate="acceptance-gate", checks=["true"]),
             step_factory(id="b", needs=["a"], gate="acceptance-gate", checks=["true"])]
    state = new_state("dag", steps, None)
    assert run_loop(state, None, "mock", "mock", {"cwd": str(tmp_path)}, 20, quiet=True) == "DONE"
