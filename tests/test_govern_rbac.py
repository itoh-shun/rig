"""Permissions, approvals and waivers (v2).

The three questions a governed accept asks — may you, did enough people say yes,
and is the exception you are leaning on still alive.
"""

import ast
import datetime
import json
import pathlib

import pytest

from rig_workbench.govern.approval import (UNKNOWN_HEAD, Attestations, actor_label,
                                           evaluate, ledger_attestations, load_approvals,
                                           make_decision, record_decision)
from rig_workbench.govern.identity import (SELF_ASSERTED, current_actor, load_org_binding,
                                           resolve_actor)
from rig_workbench.govern.policy import (SCHEMA, EffectivePolicy, PolicyError,
                                         effective_policy)
from rig_workbench.govern.rbac import PermissionDenied, can, explain, require, roles_of
from rig_workbench.govern import waiver


def _scoped_nodes(tree):
    """Every node in a module, paired with the name of the function it sits in.

    `<module>` covers module level and class bodies; `AsyncFunctionDef` opens a scope like
    `FunctionDef` does. Both matter: a scan that walks `ast.FunctionDef` alone cannot see a
    reference written anywhere else, which is how the first version of the scan below could
    be slipped past.
    """
    stack = [("<module>", tree)]
    while stack:
        scope, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            yield scope, child
            inner = (child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                     else scope)
            stack.append((inner, child))


def policy(**doc) -> EffectivePolicy:
    eff = EffectivePolicy(active=True, org="acme")
    eff.roles = doc.get("roles", {})
    eff.members = doc.get("members", {})
    eff.approvals = doc.get("approvals", {})
    eff.waivers = doc.get("waivers", {})
    return eff


# ── rbac ─────────────────────────────────────────────────────────────────────
def test_permission_granted_through_a_role():
    eff = policy(roles={"dev": ["accept"]}, members={"alice": ["dev"]})
    assert can(eff, "alice", "accept")
    assert not can(eff, "alice", "accept.force")


def test_the_wildcard_member_grants_a_baseline_role_to_everyone():
    eff = policy(roles={"dev": ["accept"]}, members={"*": ["dev"]})
    assert can(eff, "somebody-nobody-listed", "accept")
    assert roles_of(eff, "carol") == ["dev"]


def test_wildcard_and_explicit_roles_combine():
    eff = policy(roles={"dev": ["accept"], "owner": ["accept.force"]},
                 members={"*": ["dev"], "alice": ["owner"]})
    assert sorted(roles_of(eff, "alice")) == ["dev", "owner"]
    assert can(eff, "alice", "accept.force")


def test_a_denial_says_who_does_hold_the_permission():
    eff = policy(roles={"dev": ["accept"], "quality-owner": ["accept.force"]},
                 members={"bob": ["dev"], "alice": ["quality-owner"]})
    decision = can(eff, "bob", "accept.force")
    assert not decision.allowed
    assert "quality-owner" in decision.reason and "bob" in decision.reason


def test_inactive_policy_permits_everything():
    assert can(EffectivePolicy(), "anyone", "accept.force").allowed


def test_a_policy_with_no_roles_permits_everything():
    eff = policy(members={"alice": ["dev"]})
    assert can(eff, "eve", "accept.force").allowed


def test_require_raises_on_denial():
    eff = policy(roles={"dev": ["accept"]}, members={"bob": ["dev"]})
    with pytest.raises(PermissionDenied):
        require(eff, "bob", "waiver.grant")


def test_unknown_permission_is_a_programming_error_not_a_denial():
    with pytest.raises(ValueError, match="unknown permission"):
        can(policy(), "alice", "deploy.prod")


def test_explain_lists_every_permission_with_its_holders():
    eff = policy(roles={"dev": ["accept"], "owner": ["accept.force"]},
                 members={"bob": ["dev"], "alice": ["owner"]})
    text = "\n".join(explain(eff, "bob"))
    assert "✓ accept" in text
    assert "accept.force" in text and "held by owner" in text


# ── identity ─────────────────────────────────────────────────────────────────
def test_rig_actor_wins_over_rig_user(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_ACTOR", "alice")
    monkeypatch.setenv("RIG_USER", "bob")
    assert current_actor(tmp_path) == "alice"


def test_the_v1_rig_user_variable_still_resolves(monkeypatch, tmp_path):
    monkeypatch.delenv("RIG_ACTOR", raising=False)
    monkeypatch.setenv("RIG_USER", "bob")
    assert current_actor(tmp_path) == "bob"


def test_a_malformed_binding_is_reported_not_raised(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "org.json").write_text("{ nope", encoding="utf-8")
    binding = load_org_binding(tmp_path)
    assert binding.bound is False and "not valid JSON" in binding.error


# ── approvals ────────────────────────────────────────────────────────────────
def approving_policy(**overrides):
    rule = {"quorum": 2, "roles": ["reviewer"], "separation_of_duties": True,
            "expires_hours": 168}
    rule.update(overrides)
    return policy(roles={"reviewer": ["approve"], "dev": ["accept"]},
                  members={"alice": ["reviewer"], "bob": ["reviewer"],
                           "carol": ["dev"], "author": ["dev"]},
                  approvals={"feature": rule})


TASK = {"task_id": "t1", "task_type": "feature", "actor": "author"}


def approvals(*decisions):
    return {"task_id": "t1", "decisions": list(decisions)}


def decision(actor, roles=("reviewer",), head=None, ts=None, verdict="approve", note=""):
    return {"actor": actor, "decision": verdict, "roles": list(roles), "head": head,
            "note": note, "ts": ts or datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


def test_quorum_is_met_by_distinct_qualified_approvers():
    status = evaluate(approving_policy(), TASK, approvals(decision("alice"), decision("bob")))
    assert status.satisfied and status.counted == 2


def test_quorum_is_not_met_by_one_approver():
    status = evaluate(approving_policy(), TASK, approvals(decision("alice")))
    assert not status.satisfied and status.counted == 1


def test_the_authors_own_approval_never_counts():
    status = evaluate(approving_policy(), TASK,
                      approvals(decision("author"), decision("alice"), decision("bob")))
    assert status.counted == 2
    assert any("separation of duties" in why for _d, why in status.ignored)


def test_separation_of_duties_can_be_off_for_a_task_type():
    eff = approving_policy(separation_of_duties=False, quorum=1)
    status = evaluate(eff, TASK, approvals(decision("author")))
    assert status.satisfied


def test_an_unqualified_role_does_not_count_toward_the_quorum():
    status = evaluate(approving_policy(), TASK, approvals(decision("carol", roles=("dev",)),
                                                          decision("alice")))
    assert status.counted == 1
    assert any("do not include" in why for _d, why in status.ignored)


def test_an_approval_stops_counting_when_the_branch_moves():
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice", head="a" * 40)), head="b" * 40)
    assert not status.satisfied
    assert any("branch moved" in why for _d, why in status.ignored)


def test_an_approval_still_counts_for_the_commit_it_approved():
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice", head="a" * 40)), head="a" * 40)
    assert status.satisfied


# ── which commit an approval is bound to ─────────────────────────────────────
#
# `evaluate`'s `head` is the commit the caller is about to apply — for `accept`, the tip of
# the task branch resolved in the main tree. The two tests above use the OLD record shape
# (a `head` and no `branch_tip`), which is what every ledger written before this field
# existed holds, and they are the proof that such a record is still read: it is compared
# against the branch tip, which closes the detached-worktree hole for old ledgers too.
def test_an_old_format_decision_is_held_to_the_branch_tip():
    """A record with only a worktree `head`. Equal to the tip, it counts; different, it does
    not — and "different" now includes a worktree that was detached at the approved commit
    while the branch had moved on, which is the case the old comparison could not see."""
    old_shape = decision("alice", head="a" * 40)
    assert "branch_tip" not in old_shape
    on_the_tip = evaluate(approving_policy(quorum=1), TASK, approvals(old_shape), head="a" * 40)
    moved_on = evaluate(approving_policy(quorum=1), TASK, approvals(old_shape), head="b" * 40)
    assert on_the_tip.satisfied
    assert not moved_on.satisfied
    assert any(f"approved {'a' * 12}, the branch is now at {'b' * 12}" in why
               for _d, why in moved_on.ignored)


def test_a_decision_records_both_the_tree_read_and_the_tip_approved():
    d = make_decision(actor="alice", decision="approve", roles=["reviewer"],
                      head="a" * 40, branch_tip="b" * 40)
    assert (d["head"], d["branch_tip"]) == ("a" * 40, "b" * 40)


def test_a_new_format_decision_is_bound_to_the_tip_not_the_tree_the_approver_stood_in():
    """Both shas recorded and disagreeing: the worktree was detached at A, the branch was at
    B. The approval is for B — what `accept` squashes — so B counts and A does not."""
    detached = decision("alice", head="a" * 40)
    detached["branch_tip"] = "b" * 40
    assert evaluate(approving_policy(quorum=1), TASK, approvals(detached), head="b" * 40).satisfied
    spent_on_a = evaluate(approving_policy(quorum=1), TASK, approvals(detached), head="a" * 40)
    assert not spent_on_a.satisfied
    assert any(f"approved {'b' * 12}" in why for _d, why in spent_on_a.ignored)


def test_a_decision_with_no_recorded_commit_is_not_held_to_one():
    """An orchestrator stage gate records no branch, and a rule that was never recorded
    cannot be applied retroactively. This is not new: a decision with no sha has counted
    since the freshness rule existed, and `branch_tip` did not change it."""
    assert evaluate(approving_policy(quorum=1), TASK,
                    approvals(decision("alice")), head="b" * 40).satisfied


def test_a_head_that_could_not_be_resolved_counts_no_approval_at_all():
    """`UNKNOWN_HEAD`, which is the one thing `None` must not be allowed to mean. A caller
    whose task branch has been deleted knows there is a commit and cannot name it; reading
    that as "nothing to compare" is how a missing ref became permission."""
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice", head="a" * 40)), head=UNKNOWN_HEAD)
    assert not status.satisfied and status.counted == 0
    assert any("could not be resolved" in why for _d, why in status.ignored)


def test_an_unknown_head_is_not_silently_equal_to_a_missing_one():
    """The two answers have to stay distinguishable at the call site, or the sentinel is
    decoration: same decisions, same rule, opposite verdicts."""
    same = approvals(decision("alice", head="a" * 40))
    assert evaluate(approving_policy(quorum=1), TASK, same, head=None).satisfied
    assert not evaluate(approving_policy(quorum=1), TASK, same, head=UNKNOWN_HEAD).satisfied


def test_an_expired_approval_stops_counting():
    old = (datetime.datetime.now().astimezone() - datetime.timedelta(hours=200)).isoformat(timespec="seconds")
    status = evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice", ts=old)))
    assert not status.satisfied
    assert any("expired" in why for _d, why in status.ignored)


def test_a_denial_blocks_even_with_a_met_quorum():
    status = evaluate(approving_policy(), TASK,
                      approvals(decision("alice"), decision("bob"),
                                decision("carol", verdict="deny", note="race condition")))
    assert status.counted == 2 and not status.satisfied
    assert status.denials[0]["note"] == "race condition"


def test_no_quorum_configured_is_satisfied_by_default():
    status = evaluate(policy(), TASK, approvals())
    assert status.satisfied and status.required == 0


def test_a_second_decision_replaces_the_first(tmp_path):
    record_decision(tmp_path, "t1", actor="alice", decision="deny", roles=["reviewer"])
    record_decision(tmp_path, "t1", actor="alice", decision="approve", roles=["reviewer"])
    stored = load_approvals(tmp_path, "t1")
    assert len(stored["decisions"]) == 1
    assert stored["decisions"][0]["decision"] == "approve"


# ── waivers ──────────────────────────────────────────────────────────────────
def future(days=3):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def test_a_waiver_covers_the_criteria_it_names(tmp_path):
    eff = policy(waivers={"max_days": 14})
    waiver.grant(tmp_path, eff, waiver_id="w1", actor="alice",
                 criteria=["tests_pass_or_explained"], reason="flaky CI runner", expires=future())
    cover = waiver.coverage(tmp_path, ["tests_pass_or_explained", "no_type_errors_or_explained"])
    assert cover.covered == ["tests_pass_or_explained"]
    assert cover.uncovered == ["no_type_errors_or_explained"]
    assert not cover.complete


def test_a_lapsed_waiver_covers_nothing(tmp_path):
    eff = policy(waivers={})
    waiver.grant(tmp_path, eff, waiver_id="w1", actor="alice", criteria=["tests_pass_or_explained"],
                 reason="temporary", expires=future(1))
    stored = waiver.load_waivers(tmp_path)
    stored[0]["expires"] = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    waiver.save_waivers(tmp_path, stored)
    cover = waiver.coverage(tmp_path, ["tests_pass_or_explained"])
    assert cover.covered == [] and len(cover.expired) == 1


def test_a_non_waivable_criterion_cannot_be_waived(tmp_path):
    eff = policy(waivers={"non_waivable": ["no_secret_leak"]})
    with pytest.raises(waiver.WaiverError, match="non-waivable"):
        waiver.grant(tmp_path, eff, waiver_id="w1", actor="alice", criteria=["no_secret_leak"],
                     reason="just this once", expires=future())


def test_a_waiver_cannot_outlive_the_policy_limit(tmp_path):
    eff = policy(waivers={"max_days": 3})
    with pytest.raises(waiver.WaiverError, match="exceeds the policy limit"):
        waiver.grant(tmp_path, eff, waiver_id="w1", actor="alice", criteria=["tests_pass_or_explained"],
                     reason="long migration", expires=future(30))


def test_a_waiver_needs_a_reason(tmp_path):
    with pytest.raises(waiver.WaiverError, match="needs a reason"):
        waiver.grant(tmp_path, policy(), waiver_id="w1", actor="alice",
                     criteria=["tests_pass_or_explained"], reason="   ", expires=future())


def test_a_waiver_expiring_in_the_past_is_refused(tmp_path):
    with pytest.raises(waiver.WaiverError, match="not in the future"):
        waiver.grant(tmp_path, policy(), waiver_id="w1", actor="alice",
                     criteria=["tests_pass_or_explained"], reason="backdated",
                     expires=(datetime.date.today() - datetime.timedelta(days=1)).isoformat())


def test_scope_pins_a_waiver_to_a_task_type(tmp_path):
    waiver.grant(tmp_path, policy(), waiver_id="w1", actor="alice",
                 criteria=["tests_pass_or_explained"], reason="docs only",
                 expires=future(), scope="documentation")
    assert waiver.coverage(tmp_path, ["tests_pass_or_explained"],
                           task_type="documentation").complete
    assert not waiver.coverage(tmp_path, ["tests_pass_or_explained"],
                               task_type="feature").complete


def test_a_revoked_waiver_covers_nothing(tmp_path):
    waiver.grant(tmp_path, policy(), waiver_id="w1", actor="alice",
                 criteria=["tests_pass_or_explained"], reason="temporary", expires=future())
    waiver.revoke(tmp_path, "w1", actor="alice", reason="fixed")
    assert waiver.coverage(tmp_path, ["tests_pass_or_explained"]).covered == []


def test_revoking_an_unknown_waiver_is_an_error(tmp_path):
    with pytest.raises(waiver.WaiverError, match="no waiver with id"):
        waiver.revoke(tmp_path, "nope", actor="alice")


# ── the effective policy reaches the gate ────────────────────────────────────
def test_policy_required_criteria_land_in_a_new_gate(tmp_path, monkeypatch):
    from rig_workbench.workbench.state import build_acceptance

    (tmp_path / ".rig" / "policy").mkdir(parents=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "policy_layers": [".rig/policy/org.json"]}),
        encoding="utf-8")
    (tmp_path / ".rig" / "policy" / "org.json").write_text(json.dumps(
        {"schema": SCHEMA, "id": "acme", "scope": "org", "org": "acme",
         "require_criteria": {"feature": ["threat_model_reviewed"]},
         "descriptions": {"threat_model_reviewed": "STRIDE pass recorded"}}), encoding="utf-8")

    acc = build_acceptance("t1", "feature", tmp_path)
    policy_checks = [c for c in acc["checks"] if c.get("origin") == "policy"]
    assert [c["name"] for c in policy_checks] == ["threat_model_reviewed"]
    assert policy_checks[0]["description"] == "STRIDE pass recorded"
    assert policy_checks[0]["status"] == "pending"
    # the built-in preset criteria are untouched
    assert any(c["name"] == "no_secret_leak" and "origin" not in c for c in acc["checks"])


def test_no_policy_leaves_the_gate_exactly_as_v1_built_it(tmp_path):
    from rig_workbench.workbench.state import build_acceptance

    acc = build_acceptance("t1", "feature", tmp_path)
    assert all("origin" not in c for c in acc["checks"])


def test_a_broken_policy_never_strands_a_new_task(tmp_path):
    """Gate construction runs at `new`; refusing there would block work before it
    starts. `accept` is where a broken policy is reported and blocks."""
    from rig_workbench.workbench.state import build_acceptance

    (tmp_path / ".rig" / "policy").mkdir(parents=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "policy_layers": [".rig/policy/org.json"]}),
        encoding="utf-8")
    (tmp_path / ".rig" / "policy" / "org.json").write_text("{ broken", encoding="utf-8")
    acc = build_acceptance("t1", "feature", tmp_path)
    assert acc["checks"]
    # ...and the same document is a hard error everywhere it matters, so it cannot
    # sit there quietly costing the org its rules.
    with pytest.raises(PolicyError, match="not valid JSON"):
        effective_policy(tmp_path, {})


# ── reconciliation: a decision the chain does not attest ─────────────────────
def attestations(*entries, enforced=True):
    return Attestations(entries=tuple(entries), enforced=enforced)


def grant_entry(actor="alice", task="t1", verdict="approve", sig=None, **data):
    entry = {"action": "approval.grant" if verdict == "approve" else "approval.deny",
             "actor": actor, "subject": task,
             "data": {"task_type": "feature", "note": "", "decision": verdict, **data}}
    if sig:
        entry["sig"] = sig
    return entry


def test_a_decision_the_chain_does_not_attest_is_not_counted():
    status = evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                      attested=attestations())
    assert not status.satisfied and status.counted == 0
    assert [why for _, why in status.ignored] == ["no ledger entry attests this decision"]


def test_a_decision_the_chain_attests_counts():
    status = evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                      attested=attestations(grant_entry(head=None, branch_tip=None)))
    assert status.satisfied and status.ignored == []


def test_a_ledger_entry_for_another_commit_does_not_attest_this_decision():
    """The whole point of G2's shas: an entry that says "alice approved something" cannot
    stand in for an approval of the commit accept is about to squash."""
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice", head="a" * 40)), head="a" * 40,
                      attested=attestations(grant_entry(head="b" * 40, branch_tip="b" * 40)))
    assert not status.satisfied
    assert status.ignored[0][1] == "no ledger entry attests this decision"


def test_a_pre_g2_ledger_entry_still_attests_its_decision():
    """The upgrade path. Entries written before b5016ed carry the task type and the note
    and no shas at all, so they are matched on task_id, actor and decision alone — holding
    them to a commit they never recorded would lock a team out of accepting work its own
    ledger already attests."""
    old = {"action": "approval.grant", "actor": "alice", "subject": "t1",
           "data": {"task_type": "feature", "note": "looks right"}}
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice", head="a" * 40)), head="a" * 40,
                      attested=attestations(old))
    assert status.satisfied and status.ignored == []


def test_an_unsigned_entry_still_attests_once_the_repository_has_a_key(tmp_path):
    """The key is created lazily by the first successful accept, so honest grants made
    before it exists are unsigned for good. Requiring a signature on the attesting entry
    refused them from that moment on — measured, bob granted two tasks, accepting the first
    created the key, and the second was then refused with "no ledger entry attests this
    decision" while the ledger plainly attested it. It bought nothing either: the chain
    needs no secret, so a forger can append an unsigned entry too."""
    from rig_workbench.govern import ledger

    ledger.append(tmp_path, "approval.grant", actor="alice", subject="t1",
                  data={"task_type": "feature", "note": "", "decision": "approve",
                        "head": None, "branch_tip": None})
    assert "sig" not in ledger.read_ledger(tmp_path)[0]
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"k" * 32)
    attested = ledger_attestations(tmp_path)
    assert attested.enforced
    assert evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                    attested=attested).satisfied


def test_an_entry_from_another_org_does_not_attest_this_task():
    other = grant_entry(head=None, branch_tip=None)
    other["org"], other["team"] = "other-corp", "team-z"
    status = evaluate(approving_policy(quorum=1),
                      {**TASK, "org": "acme", "team": "team-a"},
                      approvals(decision("alice")), attested=attestations(other))
    assert not status.satisfied
    assert status.ignored[0][1] == "no ledger entry attests this decision"


def test_one_identity_typed_two_ways_is_one_person():
    entry = grant_entry(actor="Alice ", head=None, branch_tip=None)
    assert evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                    attested=attestations(entry)).satisfied


def test_reconciliation_that_is_not_enforced_leaves_the_arithmetic_alone():
    assert evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                    attested=attestations(enforced=False)).satisfied
    assert evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                    attested=None).satisfied


def test_a_denial_is_never_reconciled_away():
    """Reconciliation removes reasons to proceed, never reasons to stop."""
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("bob", verdict="deny", note="race condition")),
                      attested=attestations())
    assert not status.satisfied and len(status.denials) == 1


def test_an_empty_chain_in_a_signing_repository_attests_nothing(tmp_path):
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"k" * 32)
    attested = ledger_attestations(tmp_path)
    assert attested.enforced and attested.entries == ()
    assert not evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                        attested=attested).satisfied


def test_an_absent_chain_without_a_key_degrades_unless_the_policy_requires_it(tmp_path):
    assert not ledger_attestations(tmp_path).enforced
    assert ledger_attestations(tmp_path, chain_required=True).enforced


def test_a_grant_for_another_task_does_not_attest_this_one():
    """Dropping the `subject` comparison in `_matches` changed nothing that any other test
    noticed: every fixture granted and evaluated the same task, so one approval anywhere in
    an org's chain would have attested every task in it."""
    elsewhere = grant_entry(task="t2", head=None, branch_tip=None)
    status = evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                      attested=attestations(elsewhere))
    assert not status.satisfied and status.counted == 0
    assert status.ignored[0][1] == "no ledger entry attests this decision"
    # ...and the same entry against its own task still counts, so this is the subject and
    # not some other field refusing it.
    assert evaluate(approving_policy(quorum=1), {**TASK, "task_id": "t2"},
                    {"task_id": "t2", "decisions": [decision("alice")]},
                    attested=attestations(elsewhere)).satisfied


def test_a_denial_entry_does_not_attest_an_approval():
    """Dropping the decision-word comparison changed nothing either: `approval.deny` and
    `approval.grant` both name the right task and actor, so a recorded objection would have
    stood in as the approval that overrode it."""
    denial = grant_entry(verdict="deny", head=None, branch_tip=None)
    assert denial["action"] == "approval.deny"
    status = evaluate(approving_policy(quorum=1), TASK, approvals(decision("alice")),
                      attested=attestations(denial))
    assert not status.satisfied and status.counted == 0
    assert status.ignored[0][1] == "no ledger entry attests this decision"


# ── whose name is on a decision, and what that name is worth ────────────────
#
# There is no identity provider in this repository. `--actor` is a flag, `RIG_ACTOR` and
# `RIG_USER` are environment variables, and `git config user.name` is a file the same person
# writes — four ways of typing a name and no way of checking one. Before this, the record
# said only the name, so `--actor "Chief Security Officer"` read back out of
# `approvals.json`, the chain and every listing exactly like a name somebody had proved.
# These pin the record saying what it knows, and — the half that must not break — pin that
# saying it changed nothing about which decisions the chain attests.
class _FakeProc:
    def __init__(self, stdout=""):
        self.stdout = stdout


class _FakeRunner:
    def __init__(self, stdout=""):
        self.stdout = stdout

    def run(self, argv, cwd=None, **kw):
        return _FakeProc(self.stdout)


def test_every_way_of_naming_an_actor_is_a_claim(monkeypatch, tmp_path):
    """All four resolutions, and not one of them is authenticated. The `source` is kept
    because "the name came from a flag on this invocation" is a fact worth recording; it is
    not a ranking, and nothing reads it as one."""
    monkeypatch.delenv("RIG_ACTOR", raising=False)
    monkeypatch.delenv("RIG_USER", raising=False)
    from_git = resolve_actor(tmp_path, runner=_FakeRunner("carol\n"))
    monkeypatch.setenv("RIG_USER", "bob")
    from_v1_env = resolve_actor(tmp_path, runner=_FakeRunner("carol\n"))
    monkeypatch.setenv("RIG_ACTOR", "alice")
    from_env = resolve_actor(tmp_path, runner=_FakeRunner("carol\n"))
    from_flag = resolve_actor(tmp_path, "Chief Security Officer", runner=_FakeRunner("carol\n"))
    nobody = resolve_actor(tmp_path, runner=_FakeRunner(""))

    assert [c.name for c in (from_flag, from_env, from_v1_env, from_git)] == [
        "Chief Security Officer", "alice", "bob", "carol"]
    assert [c.source for c in (from_flag, from_env, from_v1_env, from_git)] == [
        "--actor", "$RIG_ACTOR", "$RIG_USER", "git config user.name"]
    for claim in (from_flag, from_env, from_v1_env, from_git, nobody):
        assert claim.authenticated is False
        assert claim.assertion == SELF_ASSERTED == "self-asserted"


def test_resolving_an_actor_returns_the_name_current_actor_always_returned(monkeypatch, tmp_path):
    """The name is the compatibility surface: it is stored, compared against the task's
    author, de-duplicated on and matched against the chain. Only what is recorded *beside*
    it is new."""
    monkeypatch.setenv("RIG_ACTOR", "alice")
    assert resolve_actor(tmp_path).name == current_actor(tmp_path) == "alice"


def test_what_a_decision_records_about_its_actor_decides_nothing():
    """The two written fields, pinned by what they are *for* rather than by their values.

    They exist so the record states what rig knew, and the copy that carries weight is the
    one in the `approval.grant` entry, where the hash chain covers it — that copy is pinned
    end to end in `test_govern_accept.py`, on the entry rather than on the file. Here the
    contract is the other half: whatever a caller puts in them, including a caller claiming
    the name was authenticated, changes nothing about how the decision reads. A test that
    only asserted `d["actor_assertion"] == "self-asserted"` pinned data and would have gone
    on passing through the whole defect this closes.
    """
    honest = make_decision(actor="alice", decision="approve", roles=["reviewer"],
                           actor_source="--actor")
    assert honest["actor"] == "alice"                 # unchanged, and load-bearing
    assert (honest["actor_assertion"], honest["actor_source"]) == (SELF_ASSERTED, "--actor")

    lying = make_decision(actor="alice", decision="approve", roles=["reviewer"],
                          assertion="authenticated", actor_source="corporate sso")
    assert actor_label(lying) == actor_label(honest) == "alice [self-asserted]"
    assert (evaluate(approving_policy(quorum=1), TASK, approvals(lying)).satisfied
            is evaluate(approving_policy(quorum=1), TASK, approvals(honest)).satisfied)


def test_a_decision_that_says_nothing_about_its_actor_is_read_as_a_claim():
    """Every decision on disk today. Absence is not evidence of an identity — nothing has
    ever authenticated an approver here — so the name is marked all the same."""
    old_shape = decision("alice")
    assert "actor_assertion" not in old_shape
    assert actor_label(old_shape) == "alice [self-asserted]"


def test_the_approvals_file_cannot_talk_the_mark_off_a_name():
    """The reproduction the security lane made, pinned at the function it lives in.

    The first shape of the mark read `actor_assertion` back out of the decision, so the one
    person who can write `approvals.json` — the task's own author — could edit it to
    `"authenticated"` and the name printed clean, with the explanatory note gone too. That
    is worse than no mark at all: an unmarked name among marked ones reads as a checked one.
    Nothing in the record decides this now.
    """
    # "SELF-ASSERTED" and "self-asserted " are the behavioural lane's two: a predicate that
    # compared the stored string exactly dropped the mark for both, while `_same_actor`
    # case-folds and strips — two readings of one file's strings, in one module. Nothing
    # compares this string now, so the whole class of near-miss is gone rather than widened.
    for claimed in ("authenticated", "sso", "SELF-ASSERTED", "self-asserted ", "", None, 42,
                    {"trust": "total"}):
        forged = {**decision("alice"), "actor_assertion": claimed}
        assert actor_label(forged) == "alice [self-asserted]"

    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals({**decision("alice"), "actor_assertion": "authenticated"}))
    text = "\n".join(status.lines())
    assert "✓ alice [self-asserted] (reviewer)" in text
    assert "not authenticated by anything" in text


def test_the_approval_report_marks_the_claims_and_says_what_the_mark_means():
    status = evaluate(approving_policy(quorum=1), TASK,
                      approvals(decision("alice"), decision("carol", roles=("dev",)),
                                decision("bob", verdict="deny")))
    text = "\n".join(status.lines())
    assert "✓ alice [self-asserted] (reviewer)" in text
    assert "· carol [self-asserted] — not counted:" in text
    assert "✗ bob [self-asserted] denied:" in text
    assert "not authenticated by anything" in text


def test_marking_a_name_as_a_claim_changes_nothing_the_chain_attests():
    """The lock-out this fix must not cause, pinned from both sides.

    `Attestations` matches a decision to a ledger entry on the actor's NAME. A record
    written before `actor_assertion` existed — which is every record on every checkout that
    upgrades into this commit — still attests, and so does a record written after it against
    an entry written before it. If this ever fails, an upgrade has locked teams out of
    approvals their own ledger holds.
    """
    old_entry = grant_entry(head=None, branch_tip=None)
    assert "actor_assertion" not in old_entry["data"]

    old_decision = decision("alice")
    assert "actor_assertion" not in old_decision
    assert evaluate(approving_policy(quorum=1), TASK, approvals(old_decision),
                    attested=attestations(old_entry)).satisfied

    new_decision = make_decision(actor="alice", decision="approve", roles=["reviewer"],
                                 actor_source="$RIG_ACTOR")
    assert evaluate(approving_policy(quorum=1), TASK, approvals(new_decision),
                    attested=attestations(old_entry)).satisfied


def test_nothing_reads_the_assertion_fields_back_out_of_a_record():
    """`actor_assertion` and `actor_source` are written and never consulted, and this is the
    guard that keeps it that way.

    The first shape of the mark read `actor_assertion` back, which put the caveat in the
    gift of the author of the file it caveats. The fields stay because the record should say
    what rig knew when it wrote it — and because the copy in the `approval.grant` entry is
    inside the hash chain — but a reader appearing later would be the same defect again.

    **Every mention of the name, not every shape of read.** The first version of this looked
    for `d["actor_assertion"]` and `d.get("actor_assertion")` and would have missed
    `d.pop(...)`, `"actor_assertion" in d`, a key held in a variable, a module-level
    expression and an `async def`. So the rule is about the string rather than the shape of
    the read: it may appear in exactly the two places that *write* it, counted, and nowhere
    else in the package. A future authenticator that wants to read it has to come here and
    say so.

    **What it does not reach, said here rather than left implied.** A name that is never
    written out whole is invisible to it: `d["actor_" + "assertion"]`, an f-string, a
    `startswith("actor_")` prefix match over a record's keys. No static scan resolves those,
    and the same limit is written on the caller scan in `test_provenance.py` for the same
    reason — a guard whose docstring overstates it is worse than no guard. What stops the
    defect itself is not this scan but `actor_label`, which takes no argument from the
    record at all, and the tests that drive a forged `"authenticated"` through the label,
    the report and the conformance clause.
    """
    package = pathlib.Path(__file__).resolve().parent.parent / "rig_workbench"
    watched = {"actor_assertion", "actor_source"}
    mentions: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for scope, node in _scoped_nodes(tree):
            if isinstance(node, ast.Constant) and node.value in watched:
                key = f"{path.name}:{scope}:{node.value}"
                mentions[key] = mentions.get(key, 0) + 1
    # COUNTED, and one apiece. Keyed by scope alone, a read added inside `make_decision` —
    # the scope that legitimately writes the field — was invisible to this scan; review
    # demonstrated exactly that. The count makes a second mention in a listed scope fail
    # like a mention in any other.
    assert mentions == {
        # the decision record…
        "approval.py:make_decision:actor_assertion": 1,
        "approval.py:make_decision:actor_source": 1,
        # …and the `approval.grant` / `approval.deny` entry the chain covers.
        "cli.py:cmd_approve:actor_assertion": 1,
        "cli.py:cmd_approve:actor_source": 1,
    }
