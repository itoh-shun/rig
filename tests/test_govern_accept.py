"""The governed accept, end to end through `workbench.py`.

accept was already the choke point; v2 makes it the governed one. What these
tests pin down is that it is governed *and* that turning governance off leaves
v1 behaviour byte-for-byte intact — the compatibility promise is the reason a
team can adopt this without a migration.
"""

import datetime
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd, env=None):
    full_env = dict(os.environ)
    full_env.setdefault("RIG_ACTOR", "alice")
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=full_env)


def run_govern(args, cwd, env=None):
    full_env = dict(os.environ)
    full_env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + full_env.get("PYTHONPATH", "")
    full_env.setdefault("RIG_ACTOR", "alice")
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", "govern", *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=full_env)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "alice"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def govern(repo, **overrides):
    """Write an org binding plus a one-layer org policy."""
    (repo / ".rig" / "policy").mkdir(parents=True, exist_ok=True)
    (repo / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    doc = {"schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
           "roles": {"dev": ["task.new", "gate.set", "accept", "discard"],
                     "reviewer": ["accept", "approve"],
                     "owner": ["accept", "accept.force", "approve", "waiver.grant"]},
           "members": {"alice": ["dev"], "bob": ["reviewer"], "olivia": ["owner"]}}
    doc.update(overrides)
    (repo / ".rig" / "policy" / "org.json").write_text(json.dumps(doc), encoding="utf-8")


def new_task(repo, task_type="feature"):
    run_cli(["new", "add a thing", "--type", task_type, "--no-worktree"], repo)
    return sorted(p.name for p in (repo / ".rig" / "runs").iterdir())[-1]


def make_acceptable(repo, task_id, leave_failing=None):
    """Everything except governance is satisfied, so governance is the only thing
    that can block. `leave_failing` marks one criterion failed to exercise --force."""
    d = repo / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    for c in acc["checks"]:
        if c["name"] == leave_failing:
            c["status"] = "failed"
        else:
            c["status"] = "passed" if c["name"] == "no_unrelated_diff" else "skipped"
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")
    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    task["worktree_path"] = str(repo)
    (d / "task.json").write_text(json.dumps(task), encoding="utf-8")


def out(result):
    return result.stdout + result.stderr


# ── backward compatibility ───────────────────────────────────────────────────
def test_an_ungoverned_repo_never_mentions_governance(repo):
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo)
    assert "governance" not in out(result)
    assert "✓ acceptance_gate_not_failed" in result.stdout


def test_a_governed_repo_stamps_the_task_with_its_author_and_team(repo):
    govern(repo)
    task_id = new_task(repo)
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["actor"] == "alice"
    assert (task["org"], task["team"]) == ("acme", "team-a")


def test_an_ungoverned_task_records_the_actor_but_no_team(repo):
    task_id = new_task(repo)
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["actor"] == "alice"
    assert "team" not in task


# ── permissions ──────────────────────────────────────────────────────────────
def test_an_actor_without_accept_is_refused(repo):
    govern(repo, members={"alice": ["dev"], "eve": []})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo, env={"RIG_ACTOR": "eve"})
    assert result.returncode != 0
    assert "not permitted to accept" in out(result)


def test_an_actor_with_accept_passes_the_permission_check(repo):
    govern(repo)
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo)
    assert "not permitted" not in out(result)
    assert "governance: acme/team-a" in result.stdout


def test_a_policy_that_does_not_parse_blocks_accept(repo):
    """Unlike the v1 allowlist, whose malformed-file fallback is 'unrestricted'. A
    policy that silently evaporates is the one failure this layer cannot have."""
    govern(repo)
    (repo / ".rig" / "policy" / "org.json").write_text("{ broken", encoding="utf-8")
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "policy layer does not load" in out(result)


def test_the_v1_allowlist_still_applies_alongside_a_policy(repo):
    govern(repo)
    (repo / ".rig" / "access.json").write_text(json.dumps({"default": ["olivia"]}), encoding="utf-8")
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "is not permitted to accept task_type" in out(result)


# ── approvals ────────────────────────────────────────────────────────────────
def test_an_unmet_quorum_blocks_accept(repo):
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "approval requirement not met (0/1)" in out(result)


def test_an_approval_from_a_qualified_reviewer_unblocks_accept(repo):
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    granted = run_govern(["approve", "grant", task_id, "--note", "looks right"], repo,
                         env={"RIG_ACTOR": "bob"})
    assert granted.returncode == 0, out(granted)
    result = run_cli(["accept", task_id], repo)
    assert "approval requirement not met" not in out(result)
    assert "approvals: 1/1" in result.stdout


def test_the_authors_own_approval_does_not_unblock_accept(repo):
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer", "dev"]}})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    run_govern(["approve", "grant", task_id], repo)          # alice authored it
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "approval requirement not met (0/1)" in out(result)


def test_a_denial_blocks_accept_and_names_the_denier(repo):
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)
    run_govern(["approve", "deny", task_id, "--note", "race condition in the retry"], repo,
               env={"RIG_ACTOR": "bob"})
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "approval denied by bob" in out(result)


def test_an_unmet_gate_is_reported_before_a_missing_approval(repo):
    """Both block, but only one is the user's next move. Someone whose gate is
    simply unmet should not be sent chasing an approval they do not yet need."""
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="tests_pass_or_explained")
    result = run_cli(["accept", task_id], repo)
    assert result.returncode != 0
    assert "acceptance-gate is failed" in out(result)
    assert "approval requirement not met" not in out(result)


def test_forcing_past_the_gate_still_needs_the_approval(repo):
    """--force overrides the gate, never the approval. Otherwise the flag would be a
    way around the whole layer."""
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}},
           members={"alice": ["dev"], "olivia": ["owner"], "bob": ["reviewer"]})
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="tests_pass_or_explained")
    result = run_cli(["accept", task_id, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert result.returncode != 0
    assert "approval requirement not met (0/1)" in out(result)
    # ...and the refusal left no forced-accept record behind
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert "forced" not in task
    assert not (repo / ".rig" / "audit.jsonl").exists()


def test_an_actor_without_approve_cannot_approve(repo):
    govern(repo, approvals={"feature": {"quorum": 1}})
    task_id = new_task(repo)
    result = run_govern(["approve", "grant", task_id], repo, env={"RIG_ACTOR": "nobody"})
    assert result.returncode != 0
    assert "not permitted to approve" in out(result)


# ── force, waivers ───────────────────────────────────────────────────────────
def test_force_is_refused_without_the_force_permission(repo):
    govern(repo)
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="tests_pass_or_explained")
    result = run_cli(["accept", task_id, "--force"], repo)   # alice is only a dev
    assert result.returncode != 0
    assert "not permitted to use --force" in out(result)


def test_force_without_a_required_waiver_is_refused(repo):
    govern(repo, waivers={"required_for_force": True, "max_days": 14, "grant_roles": ["owner"]})
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="tests_pass_or_explained")
    result = run_cli(["accept", task_id, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert result.returncode != 0
    assert "requires a live waiver for tests_pass_or_explained" in out(result)


def test_force_with_a_live_waiver_is_allowed(repo):
    govern(repo, waivers={"required_for_force": True, "max_days": 14, "grant_roles": ["owner"]})
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="tests_pass_or_explained")
    expires = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    granted = run_govern(["waiver", "grant", "w-ci", "--criterion", "tests_pass_or_explained",
                          "--reason", "CI runner is down, tracked in OPS-12", "--expires", expires],
                         repo, env={"RIG_ACTOR": "olivia"})
    assert granted.returncode == 0, out(granted)
    result = run_cli(["accept", task_id, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert "requires a live waiver" not in out(result)
    assert "waiver w-ci covers tests_pass_or_explained" in result.stdout


def test_a_non_waivable_criterion_cannot_be_forced_past(repo):
    govern(repo, waivers={"non_waivable": ["no_secret_leak"], "required_for_force": True})
    task_id = new_task(repo)
    make_acceptable(repo, task_id, leave_failing="no_secret_leak")
    result = run_cli(["accept", task_id, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert result.returncode != 0
    assert "non-waivable" in out(result)


def test_only_a_permitted_role_may_grant_a_waiver(repo):
    govern(repo, waivers={"max_days": 14, "grant_roles": ["owner"]})
    result = run_govern(["waiver", "grant", "w1", "--criterion", "tests_pass_or_explained",
                         "--reason", "because"], repo, env={"RIG_ACTOR": "bob"})
    assert result.returncode != 0
    assert "not permitted to grant waivers" in out(result)


def test_a_waiver_cannot_outlive_the_policy_limit_from_the_cli(repo):
    govern(repo, waivers={"max_days": 3, "grant_roles": ["owner"]})
    far = (datetime.date.today() + datetime.timedelta(days=90)).isoformat()
    result = run_govern(["waiver", "grant", "w1", "--criterion", "tests_pass_or_explained",
                         "--reason", "long migration", "--expires", far], repo,
                        env={"RIG_ACTOR": "olivia"})
    assert result.returncode != 0
    assert "exceeds the policy limit" in out(result)


# ── the ledger picks it all up ───────────────────────────────────────────────
def test_governance_decisions_land_in_a_verifiable_ledger(repo):
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}},
           waivers={"max_days": 14, "grant_roles": ["owner"]})
    task_id = new_task(repo)
    run_govern(["approve", "grant", task_id, "--note", "ok"], repo, env={"RIG_ACTOR": "bob"})
    run_govern(["waiver", "grant", "w1", "--criterion", "tests_pass_or_explained",
                "--reason", "flaky runner"], repo, env={"RIG_ACTOR": "olivia"})
    entries = [json.loads(line) for line in
               (repo / ".rig" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    actions = [e["action"] for e in entries]
    assert "approval.grant" in actions and "waiver.grant" in actions
    assert all(e["org"] == "acme" and e["team"] == "team-a" for e in entries)
    verified = run_govern(["audit", "verify"], repo)
    assert verified.returncode == 0 and "ledger intact" in verified.stdout


def test_a_tampered_ledger_fails_verification_from_the_cli(repo):
    govern(repo)
    run_govern(["waiver", "grant", "w1", "--criterion", "tests_pass_or_explained",
                "--reason", "flaky runner"], repo, env={"RIG_ACTOR": "olivia"})
    p = repo / ".rig" / "ledger.jsonl"
    entries = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    entries[0]["actor"] = "somebody-else"
    p.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n", encoding="utf-8")
    result = run_govern(["audit", "verify"], repo)
    assert result.returncode == 3
    assert "edited after the fact" in result.stdout


# ── the operator surface ─────────────────────────────────────────────────────
def test_govern_init_scaffolds_a_working_policy(repo):
    result = run_govern(["init", "--org", "acme", "--team", "team-a"], repo)
    assert result.returncode == 0
    assert (repo / ".rig" / "org.json").is_file()
    shown = run_govern(["policy", "show"], repo)
    assert "## rig govern policy: acme/team-a" in shown.stdout
    linted = run_govern(["policy", "lint"], repo)
    assert linted.returncode == 0 and "stack without loosening" in linted.stdout


def test_govern_lint_reports_a_loosening_layer(repo):
    govern(repo, approvals={"feature": {"quorum": 2}})
    (repo / ".rig" / "policy" / "team.json").write_text(json.dumps(
        {"schema": "rig.policy/v2", "id": "team-a", "scope": "team", "org": "acme",
         "team": "team-a", "approvals": {"feature": {"quorum": 1}}}), encoding="utf-8")
    binding = json.loads((repo / ".rig" / "org.json").read_text(encoding="utf-8"))
    binding["policy_layers"].append(".rig/policy/team.json")
    (repo / ".rig" / "org.json").write_text(json.dumps(binding), encoding="utf-8")
    result = run_govern(["policy", "lint"], repo)
    assert result.returncode == 3
    assert "quorum may only be raised" in result.stdout


def test_govern_can_exits_nonzero_on_a_denial(repo):
    govern(repo)
    allowed = run_govern(["can", "accept"], repo)
    denied = run_govern(["can", "accept.force"], repo)
    assert allowed.returncode == 0 and "✓ allowed" in allowed.stdout
    assert denied.returncode == 3 and "✗ denied" in denied.stdout


def test_govern_conformance_reports_and_exits_nonzero_when_failing(repo):
    result = run_govern(["conformance"], repo)          # unbound repository
    assert result.returncode == 3
    assert "org_binding" in result.stdout


def test_govern_migrate_folds_v1_files_into_a_policy_layer(repo):
    (repo / ".rig").mkdir(exist_ok=True)
    (repo / ".rig" / "access.json").write_text(json.dumps({"feature": ["alice", "bob"]}),
                                               encoding="utf-8")
    (repo / ".rig" / "gates.json").write_text(json.dumps(
        {"extra_criteria": {"feature": ["load_tested"]},
         "descriptions": {"load_tested": "k6 run attached"}}), encoding="utf-8")
    result = run_govern(["migrate", "--org", "acme", "--id", "migrated"], repo)
    assert result.returncode == 0, out(result)
    doc = json.loads((repo / ".rig" / "policy" / "migrated.json").read_text(encoding="utf-8"))
    assert doc["require_criteria"]["feature"] == ["load_tested"]
    assert doc["members"]["alice"] == ["accepter"]
    assert doc["descriptions"]["load_tested"] == "k6 run attached"
    # the originals are untouched — migration is additive, like everything else here
    assert (repo / ".rig" / "access.json").is_file()


def test_govern_rollup_renders_the_team_view(tmp_path, repo):
    govern(repo)
    other = tmp_path / "svc-2"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    govern(other)
    binding = json.loads((other / ".rig" / "org.json").read_text(encoding="utf-8"))
    binding["team"] = "team-b"
    (other / ".rig" / "org.json").write_text(json.dumps(binding), encoding="utf-8")
    result = run_govern(["rollup", str(repo), str(other)], repo)
    assert "## rig govern rollup: acme" in result.stdout
    assert "| team-a |" in result.stdout and "| team-b |" in result.stdout
