"""Stage-level governance (v2.1): `actor`, `human_gate`, and `stage:<id>` policy rules.

v2 governed the one place changes enter the working tree. These tests cover the
generalisation: a run can be required to stop at a named stage until a person
with the right role signs off, and the org can impose that requirement on a
recipe that never asked for it.
"""

import json

import pytest

from rig_workbench.govern.policy import (SCHEMA, EffectivePolicy, PolicyError,
                                         load_policy_document, stage_key)
from rig_workbench.govern.stage import (StageConfigError, actor_mismatch, describe,
                                        evaluate_stage, parse_human_gate, stage_rule)


def policy(**doc) -> EffectivePolicy:
    eff = EffectivePolicy(active=True, org="acme")
    eff.roles = doc.get("roles", {})
    eff.members = doc.get("members", {})
    eff.approvals = doc.get("approvals", {})
    return eff


def step(**fields) -> dict:
    base = {"id": "architecture_review", "instruction": "verify"}
    base.update(fields)
    return base


def decision(actor, roles=("architect",), verdict="approve", note=""):
    import datetime
    return {"actor": actor, "decision": verdict, "roles": list(roles), "head": None,
            "note": note,
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}


# ── human_gate parsing ───────────────────────────────────────────────────────
def test_absent_or_false_human_gate_means_no_gate():
    assert parse_human_gate(None, where="s") is None
    assert parse_human_gate(False, where="s") is None


def test_true_means_one_approval():
    assert parse_human_gate(True, where="s")["quorum"] == 1


def test_an_object_form_carries_its_fields():
    rule = parse_human_gate({"quorum": 2, "roles": ["architect", "security-owner"],
                             "expires_hours": 48}, where="s")
    assert rule["quorum"] == 2
    assert rule["roles"] == ["architect", "security-owner"]
    assert rule["expires_hours"] == 48


def test_a_quorum_of_zero_is_refused():
    with pytest.raises(StageConfigError, match="a gate that needs nobody is not a gate"):
        parse_human_gate({"quorum": 0}, where="s")


def test_an_unknown_human_gate_key_is_refused():
    with pytest.raises(StageConfigError, match="unknown key"):
        parse_human_gate({"quorum": 1, "approvers": ["x"]}, where="s")


def test_a_non_object_human_gate_is_refused():
    with pytest.raises(StageConfigError, match="must be true or an object"):
        parse_human_gate("architect", where="s")


# ── rule resolution ──────────────────────────────────────────────────────────
def test_a_step_without_a_human_gate_is_not_governed():
    assert stage_rule(policy(), step()) is None


def test_actor_alone_does_not_gate_a_step():
    """Declaring an owner is not the same as requiring one — otherwise every
    annotated step would silently become blocking."""
    assert stage_rule(policy(), step(actor="architect")) is None


def test_actor_seeds_the_approving_role():
    rule = stage_rule(policy(), step(actor="architect", human_gate=True))
    assert rule["roles"] == ["architect"]


def test_explicit_roles_win_over_actor():
    rule = stage_rule(policy(), step(actor="architect",
                                     human_gate={"quorum": 1, "roles": ["security-owner"]}))
    assert rule["roles"] == ["security-owner"]


def test_a_policy_stage_rule_governs_a_recipe_that_asked_for_nothing():
    """The org's half of this: a step becomes gated because the policy names it,
    not because the recipe author remembered to."""
    eff = policy(approvals={stage_key("architecture_review"): {"quorum": 1, "roles": ["architect"],
                                                              "separation_of_duties": True,
                                                              "expires_hours": None}})
    rule = stage_rule(eff, step())
    assert rule["quorum"] == 1 and rule["roles"] == ["architect"]


def test_the_policy_and_the_recipe_merge_to_the_stricter_rule():
    eff = policy(approvals={stage_key("architecture_review"): {
        "quorum": 2, "roles": ["security-owner"], "separation_of_duties": True,
        "expires_hours": 24}})
    rule = stage_rule(eff, step(human_gate={"quorum": 1, "roles": ["architect"],
                                            "expires_hours": 72}))
    assert rule["quorum"] == 2                                   # higher wins
    assert rule["roles"] == ["architect", "security-owner"]       # union
    assert rule["expires_hours"] == 24                            # shorter wins


def test_a_recipe_cannot_relax_the_policys_stage_rule():
    eff = policy(approvals={stage_key("architecture_review"): {
        "quorum": 2, "roles": ["architect"], "separation_of_duties": True,
        "expires_hours": None}})
    rule = stage_rule(eff, step(human_gate={"quorum": 1, "separation_of_duties": False}))
    assert rule["quorum"] == 2 and rule["separation_of_duties"] is True


def test_an_inactive_policy_leaves_the_recipes_own_gate_standing():
    rule = stage_rule(EffectivePolicy(), step(human_gate={"quorum": 1, "roles": ["architect"]}))
    assert rule["quorum"] == 1


def test_the_default_approval_rule_does_not_leak_onto_every_stage():
    """`default` covers accept. Applying it to every step of every recipe would turn
    one approval into a dozen."""
    eff = policy(approvals={"default": {"quorum": 2, "roles": [],
                                        "separation_of_duties": True, "expires_hours": None}})
    assert stage_rule(eff, step()) is None


# ── stage evaluation ─────────────────────────────────────────────────────────
def approving():
    return policy(roles={"architect": ["approve"], "developer": ["accept"]},
                  members={"olivia": ["architect"], "alice": ["developer"]})


def test_a_gated_stage_starts_unsatisfied():
    status = evaluate_stage(approving(), step(actor="architect", human_gate=True), [])
    assert status is not None and not status.satisfied and status.required == 1


def test_a_qualified_approval_satisfies_the_stage():
    status = evaluate_stage(approving(), step(actor="architect", human_gate=True),
                            [decision("olivia")])
    assert status.satisfied


def test_an_unqualified_approval_does_not():
    status = evaluate_stage(approving(), step(actor="architect", human_gate=True),
                            [decision("alice", roles=("developer",))])
    assert not status.satisfied


def test_whoever_ran_the_stage_cannot_sign_it_off():
    status = evaluate_stage(approving(), step(actor="architect", human_gate=True),
                            [decision("olivia")], author="olivia")
    assert not status.satisfied
    assert any("separation of duties" in why for _d, why in status.ignored)


def test_a_denial_blocks_a_met_quorum():
    status = evaluate_stage(approving(), step(actor="architect", human_gate=True),
                            [decision("olivia"), decision("dana", verdict="deny", note="no ADR")])
    assert not status.satisfied and status.denials[0]["note"] == "no ADR"


def test_an_ungated_step_evaluates_to_none():
    assert evaluate_stage(approving(), step(), []) is None


# ── actor ownership (advisory) ───────────────────────────────────────────────
def test_the_owning_role_running_the_stage_raises_nothing():
    assert actor_mismatch(approving(), step(actor="architect"), "olivia") is None


def test_a_different_role_running_the_stage_is_noted_with_who_owns_it():
    note = actor_mismatch(approving(), step(actor="architect"), "alice")
    assert "owned by role `architect`" in note and "olivia" in note


def test_an_undefined_actor_role_is_named():
    note = actor_mismatch(approving(), step(actor="ghost"), "alice")
    assert "which the policy does not define" in note


def test_actor_is_inert_without_governance():
    assert actor_mismatch(EffectivePolicy(), step(actor="architect"), "alice") is None


def test_describe_renders_the_rule_for_plan_and_status():
    text = describe(approving(), step(actor="architect", human_gate={"quorum": 2, "expires_hours": 48}))
    assert "actor architect" in text
    assert "2 approval(s)" in text and "author excluded" in text and "48h" in text


# ── policy documents accept stage keys ───────────────────────────────────────
def test_a_stage_key_is_a_valid_approvals_target(tmp_path):
    p = tmp_path / "org.json"
    p.write_text(json.dumps({"schema": SCHEMA, "id": "acme", "scope": "org", "org": "acme",
                             "approvals": {"stage:architecture_review": {"quorum": 1}}}),
                 encoding="utf-8")
    assert load_policy_document(p)["approvals"]["stage:architecture_review"]["quorum"] == 1


def test_a_malformed_stage_key_is_refused(tmp_path):
    p = tmp_path / "org.json"
    p.write_text(json.dumps({"schema": SCHEMA, "id": "acme", "scope": "org", "org": "acme",
                             "approvals": {"stage:Architecture Review": {"quorum": 1}}}),
                 encoding="utf-8")
    with pytest.raises(PolicyError, match="must be 'default', a task_type slug, or 'stage:"):
        load_policy_document(p)


def test_stage_rules_tighten_downstream_like_every_other_rule(tmp_path):
    """The v2 invariant, applied to the new key: a team layer may raise a stage's
    quorum and never lower it."""
    from rig_workbench.govern.policy import effective_policy

    d = tmp_path / ".rig" / "policy"
    d.mkdir(parents=True)
    (d / "org.json").write_text(json.dumps(
        {"schema": SCHEMA, "id": "acme", "scope": "org", "org": "acme",
         "approvals": {"stage:release": {"quorum": 2}}}), encoding="utf-8")
    (d / "team.json").write_text(json.dumps(
        {"schema": SCHEMA, "id": "team-a", "scope": "team", "org": "acme", "team": "team-a",
         "approvals": {"stage:release": {"quorum": 1}}}), encoding="utf-8")
    binding = {"org": "acme", "policy_layers": [".rig/policy/org.json", ".rig/policy/team.json"]}
    with pytest.raises(PolicyError, match="quorum may only be raised"):
        effective_policy(tmp_path, binding)
