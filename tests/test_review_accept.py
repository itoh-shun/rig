"""A recorded rejection must reach the accept decision, even after a passing gate.

These CLI tests use real commits and squash merges so a refusal cannot be a
message printed after rejected changes have already reached the main tree.
"""

import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def ready(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "review-test")
    git(repo, "config", "user.email", "review-test@example.com")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-qm", "base")
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(tmp_path / "worktrees"), RIG_ACTOR="owner")

    def cli(*args, actor=None):
        process_env = dict(env, RIG_ACTOR=actor or "owner")
        return subprocess.run([sys.executable, str(WORKBENCH), *args], cwd=repo,
                              env=process_env, capture_output=True, text=True, timeout=60)

    made = cli("new", "review a change", "--type", "documentation", "--slug", "veto")
    assert made.returncode == 0, made.stdout + made.stderr
    task_id = re.search(r"task_id: (\S+)", made.stdout).group(1)
    wt = tmp_path / "worktrees" / task_id
    (wt / "app.py").write_text("value = 2\n", encoding="utf-8")
    git(wt, "add", "app.py")
    git(wt, "commit", "-qm", "the change under review")
    run = repo / ".rig" / "runs" / task_id
    (run / "diff.md").write_text(
        "## Summary\nUpdate the value.\n## Risk\nLocal fixture only.\n"
        "## Tests\nChecked the value.\n## Unrelated diff\nNone.\n", encoding="utf-8")
    criteria = [c["name"] for c in read(run / "acceptance.json")["checks"]]
    gate_args = [arg for name in criteria for arg in ("--set", f"{name}=passed")]

    def gate():
        result = cli("gate", task_id, *gate_args)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    def review(*verdicts):
        result = cli("review", task_id, *[arg for v in verdicts for arg in ("--set", v)])
        assert result.returncode == 0, result.stdout + result.stderr

    gate()
    return SimpleNamespace(repo=repo, wt=wt, run=run, task_id=task_id,
                           cli=cli, review=review, gate=gate, env=env)


def assert_unapplied(task):
    assert git(task.repo, "diff", "--cached", "--name-only") == ""
    assert (task.repo / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert read(task.run / "task.json")["status"] != "accepted"
    assert not (task.run / "provenance.json").exists()


def test_rejection_after_a_passing_gate_blocks_accept_until_that_reviewer_approves(ready):
    ready.review("security=REJECT", "design=APPROVE")
    rejected = ready.cli("accept", ready.task_id)
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert "no_rejected_reviews" in rejected.stdout
    assert "security=REJECT" in rejected.stderr
    assert_unapplied(ready)

    # Re-running the completion gate cannot erase a reviewer's rejection.
    ready.gate()
    assert ready.cli("accept", ready.task_id).returncode == 1
    assert_unapplied(ready)

    ready.review("security=APPROVE")
    accepted = ready.cli("accept", ready.task_id)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert git(ready.repo, "diff", "--cached", "--name-only") == "app.py"
    assert not read(ready.run / "provenance.json")["record"]["forced"]


@pytest.mark.parametrize("verdict", [None, "APPROVE", "APPROVE_WITH_CONDITIONS"])
def test_optional_and_non_rejecting_reviews_preserve_acceptance(ready, verdict):
    if verdict:
        ready.review(f"design={verdict}")
    result = ready.cli("accept", ready.task_id, "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not read(ready.run / "provenance.json")["record"]["forced"]
    assert not (ready.repo / ".rig" / "audit.jsonl").exists()


def test_force_keeps_the_rejected_review_in_audit_and_signed_provenance(ready):
    ready.review("security=REJECT", "tests=REJECT", "design=APPROVE")
    expected = [v for v in read(ready.run / "review.json")["verdicts"] if v["verdict"] == "REJECT"]
    result = ready.cli("accept", ready.task_id, "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    record = read(ready.run / "provenance.json")["record"]
    assert record["forced"] is True
    assert record["gate_status"] == "passed"
    assert record["rejected_reviews"] == expected
    events = [json.loads(line) for line in (ready.repo / ".rig" / "audit.jsonl")
              .read_text(encoding="utf-8").splitlines()]
    forced = next(e for e in events if e["action"] == "accept_force")
    assert forced["bypassed"] == ["no_rejected_reviews"]
    assert forced["rejected_reviews"] == expected
    assert ready.cli("verify-provenance", ready.task_id).returncode == 0
    changed = read(ready.run / "provenance.json")
    changed["record"]["rejected_reviews"] = []
    (ready.run / "provenance.json").write_text(json.dumps(changed), encoding="utf-8")
    assert ready.cli("verify-provenance", ready.task_id).returncode == 1


def test_review_only_completion_gate_is_independent_of_target_approval(ready):
    made = ready.cli("new", "review the target", "--type", "review", "--slug", "review-only")
    assert made.returncode == 0, made.stdout + made.stderr
    task_id = re.search(r"task_id: (\S+)", made.stdout).group(1)
    run = ready.repo / ".rig" / "runs" / task_id
    reviewed = ready.cli("review", task_id, "--set", "behavior=REJECT")
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    criteria = [c["name"] for c in read(run / "acceptance.json")["checks"]]
    result = ready.cli("gate", task_id,
                       *[arg for name in criteria for arg in ("--set", f"{name}=passed")])
    assert result.returncode == 0, result.stdout + result.stderr
    checks = read(run / "acceptance.json")["checks"]
    assert checks and all(c["status"] == "passed" for c in checks)
    assert read(run / "review.json")["verdicts"][0]["verdict"] == "REJECT"


@pytest.mark.parametrize("corrupt", [
    "{", "null", "{}", '{"verdicts": null}',
    '{"verdicts": [{"persona": "security", "verdict": "REJCT"}]}',
    '{"verdicts": [{"persona": "security", "verdict": "REJECT"}, '
    '{"persona": "security", "verdict": "APPROVE"}]}',
])
def test_unreadable_or_ambiguous_reviews_fail_closed_even_with_force(ready, corrupt):
    # Supply task_id when the JSON object is otherwise parseable, so each case
    # tests the invalid shape rather than just an unrelated task identifier.
    try:
        data = json.loads(corrupt)
        if isinstance(data, dict):
            data["task_id"] = ready.task_id
            corrupt = json.dumps(data)
    except ValueError:
        pass
    (ready.run / "review.json").write_text(corrupt, encoding="utf-8")
    result = ready.cli("accept", ready.task_id, "--force")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "review.json" in result.stderr
    assert "Traceback" not in result.stderr
    assert_unapplied(ready)


def test_rejection_override_requires_permission_and_a_live_governance_waiver(ready):
    ready.review("security=REJECT")
    config = ready.repo / ".rig"
    (config / "policy").mkdir()
    (config / "org.json").write_text(json.dumps({
        "schema": "rig.org/v2", "org": "acme", "team": "team-a",
        "policy_layers": [".rig/policy/org.json"],
    }), encoding="utf-8")
    (config / "policy" / "org.json").write_text(json.dumps({
        "schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
        "roles": {"dev": ["accept"], "owner": ["accept", "accept.force", "waiver.grant"]},
        "members": {"dev": ["dev"], "owner": ["owner"]},
        "waivers": {"required_for_force": True, "max_days": 14, "grant_roles": ["owner"]},
    }), encoding="utf-8")
    denied = ready.cli("accept", ready.task_id, "--force", actor="dev")
    assert denied.returncode == 1, denied.stdout + denied.stderr
    assert "not permitted to use --force" in denied.stderr
    assert_unapplied(ready)
    denied = ready.cli("accept", ready.task_id, "--force")
    assert denied.returncode == 1, denied.stdout + denied.stderr
    assert "requires a live waiver for no_rejected_reviews" in denied.stderr
    assert_unapplied(ready)

    expires = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    env = dict(ready.env, PYTHONPATH=str(REPO_ROOT))
    granted = subprocess.run([
        sys.executable, "-m", "rig_workbench.cli", "govern", "waiver", "grant", "review-exception",
        "--criterion", "no_rejected_reviews", "--reason", "Controlled test exception",
        "--expires", expires,
    ], cwd=ready.repo, env=env, capture_output=True, text=True, timeout=60)
    assert granted.returncode == 0, granted.stdout + granted.stderr
    accepted = ready.cli("accept", ready.task_id, "--force")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "waiver review-exception covers no_rejected_reviews" in accepted.stdout


@pytest.mark.parametrize("verdict_line", ["VERDICT: REJECT", "判定: REJECT"])
def test_recipe_review_gate_also_rejects_a_reviewer_veto(step_factory, verdict_line):
    from rig_workbench.orchestrate.providers import _judge_output
    from rig_workbench.orchestrate.runstate import gate_outcome

    ok, criteria = _judge_output("CRITERION 1: PASS — app.py:1\n" + verdict_line)
    step = step_factory(id="review", gate="review-gate", acceptance=["behavior is correct"])
    state = {"checks": [], "verdicts": [{"by": "behavior", "ok": ok, "criteria": criteria}]}
    assert gate_outcome(step, state) == "fail"
