"""Policy layering (v2): documents validate, and a downstream layer can only tighten.

The monotonic-tightening invariant is the entire load-bearing claim of the
"common policy" concept — if a team repository can quietly drop a criterion the
org requires, the org policy is a suggestion. These tests are the enforcement.
"""

import json

import pytest

from rig_workbench.govern.policy import (PERMISSIONS, SCHEMA, PolicyError,
                                         effective_policy, load_policy_document,
                                         resolve_layer_paths)


def write(tmp_path, name, **doc):
    base = {"schema": SCHEMA, "id": name, "scope": "org", "org": "acme"}
    base.update(doc)
    d = tmp_path / ".rig" / "policy"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def bind(tmp_path, *layers, team=None):
    binding = {"schema": "rig.org/v2", "org": "acme",
               "policy_layers": [f".rig/policy/{n}.json" for n in layers]}
    if team:
        binding["team"] = team
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(binding), encoding="utf-8")
    return binding


# ── single-document validation ───────────────────────────────────────────────
def test_valid_document_loads(tmp_path):
    p = write(tmp_path, "base", version="1.0.0",
              require_criteria={"standard": ["threat_model_reviewed"]},
              roles={"developer": ["accept"]}, members={"alice": ["developer"]})
    doc = load_policy_document(p)
    assert doc["id"] == "base"


def test_wrong_schema_is_rejected(tmp_path):
    p = write(tmp_path, "base")
    p.write_text(json.dumps({"schema": "rig.policy/v1", "id": "b", "scope": "org", "org": "acme"}),
                 encoding="utf-8")
    with pytest.raises(PolicyError, match="schema must be"):
        load_policy_document(p)


def test_unknown_top_level_key_is_rejected(tmp_path):
    p = write(tmp_path, "base", nonsense=1)
    with pytest.raises(PolicyError, match="unknown key 'nonsense'"):
        load_policy_document(p)


def test_unknown_permission_is_rejected(tmp_path):
    p = write(tmp_path, "base", roles={"dev": ["accept", "deploy.prod"]})
    with pytest.raises(PolicyError, match="unknown permission 'deploy.prod'"):
        load_policy_document(p)


def test_every_documented_permission_is_usable(tmp_path):
    p = write(tmp_path, "base", roles={"root": list(PERMISSIONS)})
    assert load_policy_document(p)["roles"]["root"] == list(PERMISSIONS)


def test_team_scope_requires_a_team_identifier(tmp_path):
    p = write(tmp_path, "t", scope="team")
    with pytest.raises(PolicyError, match="requires a 'team' identifier"):
        load_policy_document(p)


def test_delegatable_permissions_is_org_only(tmp_path):
    p = write(tmp_path, "t", scope="team", team="team-a",
              delegatable_permissions=["accept.force"])
    with pytest.raises(PolicyError, match="may only be set on the org layer"):
        load_policy_document(p)


def test_malformed_json_names_the_file(tmp_path):
    d = tmp_path / ".rig" / "policy"
    d.mkdir(parents=True)
    p = d / "broken.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(PolicyError, match="not valid JSON"):
        load_policy_document(p)


# ── layering: additive parts ─────────────────────────────────────────────────
def test_criteria_from_every_layer_accumulate(tmp_path):
    write(tmp_path, "org", require_criteria={"standard": ["a_check"]})
    write(tmp_path, "team", scope="team", team="team-a",
          require_criteria={"standard": ["b_check"], "feature": ["c_check"]})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team", team="team-a"))
    assert eff.require_criteria["standard"] == ["a_check", "b_check"]
    assert eff.required_criteria_for("feature", ["standard", "feature"]) == \
        ["a_check", "b_check", "c_check"]


def test_a_child_cannot_drop_a_parent_criterion_by_omission(tmp_path):
    """There is no 'remove' key, and omission inherits — the two ways a child could
    try to lose a criterion both fail closed."""
    write(tmp_path, "org", require_criteria={"standard": ["a_check"]})
    write(tmp_path, "proj", scope="project", require_criteria={"standard": []})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "proj"))
    assert eff.require_criteria["standard"] == ["a_check"]


def test_team_identifier_flows_into_the_effective_policy(tmp_path):
    write(tmp_path, "org")
    write(tmp_path, "team", scope="team", team="team-b")
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert (eff.org, eff.team) == ("acme", "team-b")


def test_layers_from_two_orgs_cannot_be_stacked(tmp_path):
    write(tmp_path, "org")
    write(tmp_path, "other", org="globex")
    with pytest.raises(PolicyError, match="two different orgs"):
        effective_policy(tmp_path, bind(tmp_path, "org", "other"))


# ── layering: the tightening invariant ───────────────────────────────────────
def test_a_child_may_narrow_a_role(tmp_path):
    write(tmp_path, "org", roles={"dev": ["accept", "discard", "gate.set"]})
    write(tmp_path, "team", scope="team", team="team-a", roles={"dev": ["accept"]})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert eff.roles["dev"] == ["accept"]


def test_a_child_may_not_widen_a_role(tmp_path):
    write(tmp_path, "org", roles={"dev": ["accept"]})
    write(tmp_path, "team", scope="team", team="team-a", roles={"dev": ["accept", "accept.force"]})
    with pytest.raises(PolicyError, match="role 'dev' adds permission"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_not_invent_a_role_with_undelegated_power(tmp_path):
    write(tmp_path, "org", roles={"dev": ["accept"]})
    write(tmp_path, "team", scope="team", team="team-a", roles={"cowboy": ["accept.force"]})
    with pytest.raises(PolicyError, match="cannot invent power the org never handed out"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_invent_a_role_within_the_delegated_set(tmp_path):
    write(tmp_path, "org", roles={"dev": ["accept"]},
          delegatable_permissions=["approve", "gate.set"])
    write(tmp_path, "team", scope="team", team="team-a", roles={"buddy": ["approve"]})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert eff.roles["buddy"] == ["approve"]


def test_a_child_may_raise_a_quorum(tmp_path):
    write(tmp_path, "org", approvals={"feature": {"quorum": 1}})
    write(tmp_path, "team", scope="team", team="team-a", approvals={"feature": {"quorum": 3}})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert eff.approval_rule("feature")["quorum"] == 3


def test_a_child_may_not_lower_a_quorum(tmp_path):
    write(tmp_path, "org", approvals={"feature": {"quorum": 2}})
    write(tmp_path, "team", scope="team", team="team-a", approvals={"feature": {"quorum": 1}})
    with pytest.raises(PolicyError, match="quorum may only be raised"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_not_switch_off_separation_of_duties(tmp_path):
    write(tmp_path, "org", approvals={"feature": {"quorum": 1, "separation_of_duties": True}})
    write(tmp_path, "team", scope="team", team="team-a",
          approvals={"feature": {"quorum": 1, "separation_of_duties": False}})
    with pytest.raises(PolicyError, match="separation_of_duties cannot be turned off"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_not_extend_an_approval_expiry(tmp_path):
    write(tmp_path, "org", approvals={"feature": {"quorum": 1, "expires_hours": 24}})
    write(tmp_path, "team", scope="team", team="team-a",
          approvals={"feature": {"quorum": 1, "expires_hours": 240}})
    with pytest.raises(PolicyError, match="expires_hours may only be shortened"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_approval_roles_accumulate_rather_than_replace(tmp_path):
    write(tmp_path, "org", approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    write(tmp_path, "team", scope="team", team="team-a",
          approvals={"feature": {"quorum": 1, "roles": ["security-owner"]}})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert eff.approval_rule("feature")["roles"] == ["reviewer", "security-owner"]


def test_a_child_may_not_lengthen_a_waiver(tmp_path):
    write(tmp_path, "org", waivers={"max_days": 7})
    write(tmp_path, "team", scope="team", team="team-a", waivers={"max_days": 30})
    with pytest.raises(PolicyError, match="max_days may only be shortened"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_shorten_a_waiver_and_add_non_waivable_criteria(tmp_path):
    write(tmp_path, "org", waivers={"max_days": 30, "non_waivable": ["no_secret_leak"]})
    write(tmp_path, "team", scope="team", team="team-a",
          waivers={"max_days": 3, "non_waivable": ["tests_pass_or_explained"]})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "team"))
    assert eff.waivers["max_days"] == 3
    assert eff.waivers["non_waivable"] == ["no_secret_leak", "tests_pass_or_explained"]


def test_a_child_may_not_switch_off_required_for_force(tmp_path):
    write(tmp_path, "org", waivers={"required_for_force": True})
    write(tmp_path, "team", scope="team", team="team-a", waivers={"required_for_force": False})
    with pytest.raises(PolicyError, match="required_for_force cannot be turned off"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_not_widen_who_grants_waivers(tmp_path):
    write(tmp_path, "org", waivers={"grant_roles": ["quality-owner"]})
    write(tmp_path, "team", scope="team", team="team-a",
          waivers={"grant_roles": ["quality-owner", "developer"]})
    with pytest.raises(PolicyError, match="does not allow to grant waivers"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_child_may_not_switch_off_the_audit_chain(tmp_path):
    write(tmp_path, "org", audit={"chain_required": True})
    write(tmp_path, "team", scope="team", team="team-a", audit={"chain_required": False})
    with pytest.raises(PolicyError, match="chain_required cannot be turned off"):
        effective_policy(tmp_path, bind(tmp_path, "org", "team"))


def test_a_project_cannot_write_itself_into_a_sealed_role(tmp_path):
    write(tmp_path, "org", roles={"quality-owner": ["accept.force"]},
          sealed_roles=["quality-owner"], members={"alice": ["quality-owner"]})
    write(tmp_path, "proj", scope="project", members={"mallory": ["quality-owner"]})
    with pytest.raises(PolicyError, match="cannot assign sealed role"):
        effective_policy(tmp_path, bind(tmp_path, "org", "proj"))


def test_an_unsealed_role_may_be_assigned_downstream(tmp_path):
    write(tmp_path, "org", roles={"dev": ["accept"]}, sealed_roles=["quality-owner"])
    write(tmp_path, "proj", scope="project", members={"bob": ["dev"]})
    eff = effective_policy(tmp_path, bind(tmp_path, "org", "proj"))
    assert eff.members["bob"] == ["dev"]


# ── discovery ────────────────────────────────────────────────────────────────
def test_layers_without_an_explicit_list_are_ordered_org_team_project(tmp_path):
    write(tmp_path, "zzz-org", scope="org")
    write(tmp_path, "aaa-project", scope="project")
    write(tmp_path, "mmm-team", scope="team", team="team-a")
    order = [p.stem for p in resolve_layer_paths(tmp_path, {})]
    assert order == ["zzz-org", "mmm-team", "aaa-project"]


def test_relative_layers_resolve_against_the_shared_policy_home(tmp_path, monkeypatch):
    """The mechanism that lets team A, B and C reference one org document: each repo
    lists the same relative path, and $RIG_POLICY_HOME says where the checkout is."""
    shared = tmp_path / "shared"
    (shared / "policy").mkdir(parents=True)
    (shared / "policy" / "acme.json").write_text(
        json.dumps({"schema": SCHEMA, "id": "acme", "scope": "org", "org": "acme",
                    "require_criteria": {"standard": ["org_check"]}}), encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".rig").mkdir(parents=True)
    monkeypatch.setenv("RIG_POLICY_HOME", str(shared))
    eff = effective_policy(repo, {"org": "acme", "policy_layers": ["policy/acme.json"]})
    assert eff.require_criteria == {"standard": ["org_check"]}


def test_no_policy_anywhere_is_simply_inactive(tmp_path):
    eff = effective_policy(tmp_path, {})
    assert eff.active is False and eff.roles == {}
