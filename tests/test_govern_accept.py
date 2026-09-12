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
    # The head the gate is claimed to have judged: `accept`'s `gate_judged_this_head`
    # compares it with the worktree's HEAD, and an acceptance.json naming none is refused
    # as unknown — which would block this fixture on the gate instead of on governance.
    acc["evaluated_head"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True).stdout.strip()
    (d / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    (d / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")
    task = json.loads((d / "task.json").read_text(encoding="utf-8"))
    task["worktree_path"] = str(repo)
    (d / "task.json").write_text(json.dumps(task), encoding="utf-8")


def out(result):
    return result.stdout + result.stderr


def audit(repo):
    """`.rig/audit.jsonl` as a list — absent file included, as an empty one."""
    path = repo / ".rig" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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
    # What it does leave is the attempt. A force refused by the quorum used to write
    # nothing at all, here or in the chained ledger, so somebody testing the boundary
    # once a day looked exactly like somebody who never tried.
    entries = audit(repo)
    assert [e["action"] for e in entries] == ["accept_refused"]
    assert entries[0]["reason"] == "governance"
    assert "approval requirement not met (0/1)" in entries[0]["detail"]
    assert entries[0]["task_id"] == task_id and entries[0]["forced"] is True
    assert entries[0]["actor"]


def test_the_flag_alone_is_not_a_force_and_is_not_recorded_as_one(repo):
    """`--force` on a run with nothing to force changes nothing, including the record.

    Measured before this was guarded on `soft_fail`: a fully judged gate, `--force`, and an
    unmet approval quorum wrote `accept_refused` with `forced: true` — an ordinary
    governance refusal filed as an override attempt, because a word was on the command
    line. `check_accept` is asked with `force=bool(soft_fail)` for the same reason, so the
    refusal now follows the same predicate the decision did.
    """
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}},
           members={"alice": ["dev"], "olivia": ["owner"], "bob": ["reviewer"]})
    task_id = new_task(repo)
    make_acceptable(repo, task_id)            # every criterion judged: soft_fail is empty
    result = run_cli(["accept", task_id, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert result.returncode != 0
    assert "approval requirement not met (0/1)" in out(result)
    assert [e for e in audit(repo) if e.get("forced")] == []

    # ...and the same refusal with one criterion unmet is a force, and is recorded as one.
    forced_task = new_task(repo)
    make_acceptable(repo, forced_task, leave_failing="tests_pass_or_explained")
    second = run_cli(["accept", forced_task, "--force"], repo, env={"RIG_ACTOR": "olivia"})
    assert second.returncode != 0
    entries = [e for e in audit(repo) if e["task_id"] == forced_task]
    assert [e["action"] for e in entries] == ["accept_refused"]
    assert entries[0]["forced"] is True and entries[0]["reason"] == "governance"


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


# ── an approval is bound to the commit accept will squash ────────────────────
#
# The gate's half of this is `tests/test_gate_sensor_authority.py`
# (`…detached_worktree…`, `…branch_moved…`). The approval's half is here, and it is the
# half that still mattered after the gate's was closed: `--force` is the one door past
# the gate, and behind it the approval quorum is the *only* human check left. An approval
# bound to the worktree's HEAD is not bound to what `accept` squashes.
def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def worktree_root(repo):
    """Task worktrees live OUTSIDE the repository. `repo` is `tmp_path` itself, so a
    worktree root under it would be an untracked directory and `accept` — which requires a
    clean main tree it can roll a failed squash back in — would refuse before governance ran."""
    return repo.parent / f"{repo.name}-worktrees"


def _worktree_task(repo, wt_root, slug="bind"):
    """A task with a real worktree, one commit of its own, and nothing but governance
    left to decide. `--force` covers the gate, so the approval is what is being tested."""
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    made = run_cli(["new", "add a thing", "--type", "feature", "--slug", slug], repo,
                   env={"RIG_WORKTREE_ROOT": str(wt_root)})
    assert made.returncode == 0, out(made)
    task_id = sorted(p.name for p in (repo / ".rig" / "runs").iterdir())[-1]
    wt = wt_root / task_id
    (wt / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "the task's own work")
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text("## Summary\nx\n", encoding="utf-8")
    return task_id, wt


def test_an_approval_cannot_be_spent_on_a_branch_tip_the_approver_never_saw(repo, worktree_root):
    """The reproduction. Approve at A, park the worktree back on A, leave the branch at B.

    Measured before the fix: `govern approve grant` recorded the WORKTREE's HEAD and
    `check_accept` compared it against the worktree's HEAD again, which goes vacuous exactly
    here — the worktree is detached at the approved commit. `accept --force` reported
    `approvals: 1/1  ✓ satisfied`, exited 0, and squash-merged B, a commit bob never
    approved, into the main tree.
    """
    wt_root = worktree_root
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id, wt = _worktree_task(repo, wt_root)
    approved = _git(wt, "rev-parse", "HEAD")

    granted = run_govern(["approve", "grant", task_id, "--note", "read every line"], repo,
                         env={"RIG_ACTOR": "bob"})
    assert granted.returncode == 0, out(granted)

    (wt / "evil.py").write_text("# never approved by anyone\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "a commit the approver never saw")
    evil = _git(wt, "rev-parse", "HEAD")
    _git(wt, "checkout", "--detach", approved)
    _git(repo, "branch", "-f", f"rig/{task_id}", evil)
    assert _git(wt, "rev-parse", "HEAD") == approved          # what the old check compared
    assert _git(repo, "rev-parse", f"rig/{task_id}") == evil  # what accept squashes

    result = run_cli(["accept", task_id, "--force"], repo,
                     env={"RIG_ACTOR": "olivia", "RIG_WORKTREE_ROOT": str(wt_root)})
    assert result.returncode != 0, out(result)
    assert (f"approved {approved[:12]}, the branch is now at {evil[:12]} "
            f"(the branch moved after this approval); re-approve at {evil[:12]}") in out(result)
    assert "approval requirement not met (0/1)" in out(result)
    # Nothing reached the main tree, and no forced accept was recorded — the attempt is,
    # as a refusal, which is the difference between the record and the outcome.
    assert not (repo / "evil.py").exists()
    assert _git(repo, "status", "--porcelain") == ""
    assert [e["action"] for e in audit(repo)] == ["accept_refused"]


def test_an_approval_granted_on_the_branch_is_still_spent_on_it(repo, worktree_root):
    """The other half, and the one that has to be boring: a worktree sitting on its own
    branch approves and accepts exactly as it did. `head` and `branch_tip` are the same
    commit there, which is every ordinary run."""
    wt_root = worktree_root
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id, wt = _worktree_task(repo, wt_root, slug="ok")
    approved = _git(wt, "rev-parse", "HEAD")

    granted = run_govern(["approve", "grant", task_id, "--note", "read every line"], repo,
                         env={"RIG_ACTOR": "bob"})
    assert granted.returncode == 0, out(granted)
    # Both shas land in the record, and here they agree.
    stored = json.loads((repo / ".rig" / "runs" / task_id / "approvals.json")
                        .read_text(encoding="utf-8"))["decisions"][0]
    assert stored["head"] == stored["branch_tip"] == approved

    result = run_cli(["accept", task_id, "--force"], repo,
                     env={"RIG_ACTOR": "olivia", "RIG_WORKTREE_ROOT": str(wt_root)})
    assert result.returncode == 0, out(result)
    assert "approvals: 1/1  ✓ satisfied" in result.stdout
    assert (repo / "app.py").is_file()


def test_approve_status_reports_a_stale_approval_the_same_way_accept_does(repo, worktree_root):
    """The preview and the gate read one sha. An approver who runs `approve status` after
    the branch moved must not be told the quorum is met by an approval accept will ignore."""
    wt_root = worktree_root
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id, wt = _worktree_task(repo, wt_root, slug="status")
    approved = _git(wt, "rev-parse", "HEAD")
    run_govern(["approve", "grant", task_id], repo, env={"RIG_ACTOR": "bob"})

    (wt / "later.py").write_text("y = 1\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "a later commit")
    later = _git(wt, "rev-parse", "HEAD")
    _git(wt, "checkout", "--detach", approved)

    shown = run_govern(["approve", "status", task_id], repo)
    assert "approvals: 0/1" in shown.stdout
    assert f"approved {approved[:12]}, the branch is now at {later[:12]}" in shown.stdout


def test_a_task_branch_that_no_longer_resolves_is_refused_before_governance(repo, worktree_root):
    """The behavioural review's measurement. Delete the task branch and every check that
    reads it gets `None`, which each of them read as "nothing to compare": `--force` covered
    the gate, governance was handed no head and counted every approval, `approvals: 1/1  ✓
    satisfied` printed, an `accept_force` line was appended to `.rig/audit.jsonl`, and only
    then did a raw `git rev-list` failure end the run — a weakened check and a false ledger
    entry for an accept that never reached the squash.
    """
    wt_root = worktree_root
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id, wt = _worktree_task(repo, wt_root, slug="gone")
    approved = _git(wt, "rev-parse", "HEAD")
    run_govern(["approve", "grant", task_id], repo, env={"RIG_ACTOR": "bob"})

    (wt / "later.py").write_text("y = 1\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "a later commit")
    _git(wt, "checkout", "--detach", "HEAD")
    _git(repo, "branch", "-D", f"rig/{task_id}")

    result = run_cli(["accept", task_id, "--force"], repo,
                     env={"RIG_ACTOR": "olivia", "RIG_WORKTREE_ROOT": str(wt_root)})
    assert result.returncode != 0, out(result)
    assert f"branch 'rig/{task_id}' does not resolve" in out(result)
    # Refused before governance ran, so nothing claimed the approval was satisfied...
    assert "approvals: 1/1" not in out(result)
    assert "✓ satisfied" not in out(result)
    # ...and nothing was applied. The one thing written is the attempt itself: one
    # `accept_refused` line naming the missing ref, never an `accept_force` for a squash
    # that did not run.
    entries = audit(repo)
    assert [e["action"] for e in entries] == ["accept_refused"]
    assert entries[0]["reason"] == "branch_unresolvable"
    assert f"rig/{task_id}" in entries[0]["detail"]
    assert _git(repo, "status", "--porcelain") == ""
    assert "forced" not in json.loads(
        (repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))

    # The preview reads the missing branch the same way the gate does.
    shown = run_govern(["approve", "status", task_id], repo)
    assert "approvals: 0/1" in shown.stdout
    assert "could not be resolved" in shown.stdout
    assert approved  # the approval is on file; it is the branch that is gone


def test_the_ledger_records_which_commit_an_approval_was_for(repo, worktree_root):
    """`approvals.json` is unsigned and writable; the ledger is neither. An entry that said
    only "alice approved something" could not contradict a hand-written decision naming a
    commit nobody read, so the chain now attests the verdict and both shas."""
    wt_root = worktree_root
    govern(repo, approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    task_id, wt = _worktree_task(repo, wt_root, slug="chain")
    approved = _git(wt, "rev-parse", "HEAD")
    run_govern(["approve", "grant", task_id, "--note", "read every line"], repo,
               env={"RIG_ACTOR": "bob"})

    entry = next(json.loads(line) for line
                 in (repo / ".rig" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["action"] == "approval.grant")
    assert entry["actor"] == "bob" and entry["subject"] == task_id
    assert entry["data"]["decision"] == "approve"
    assert entry["data"]["head"] == entry["data"]["branch_tip"] == approved
    # And the chain still verifies with the wider record in it.
    assert run_govern(["audit", "verify"], repo).returncode == 0
