"""Permissions, approvals and waivers (v2).

The three questions a governed accept asks — may you, did enough people say yes,
and is the exception you are leaning on still alive.
"""

import datetime
import json

import pytest

from rig_workbench.govern.approval import evaluate, load_approvals, record_decision
from rig_workbench.govern.identity import current_actor, load_org_binding
from rig_workbench.govern.policy import (SCHEMA, EffectivePolicy, PolicyError,
                                         effective_policy)
from rig_workbench.govern.rbac import PermissionDenied, can, explain, require, roles_of
from rig_workbench.govern import waiver


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
