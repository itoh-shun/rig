import json

import pytest

from rig_workbench import evidence


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_field_summary_keeps_missing_costs_unmeasured():
    rows = [
        {"schema": evidence.FIELD_SCHEMA, "arm": "rig", "outcome": "ok", "defects_caught": 2},
        {"schema": evidence.FIELD_SCHEMA, "arm": "bare", "outcome": "incident", "tokens": 1000},
    ]
    summary = evidence.summarize_field_study(rows)
    assert summary["arms"]["rig"]["n"] == 1
    assert summary["arms"]["rig"]["defects_caught"] == 2
    assert summary["arms"]["rig"]["tokens_mean"] is None
    assert summary["arms"]["bare"]["tokens_mean"] == 1000.0
    assert summary["comparison"]["incident_rate_delta_pp_bare_minus_rig"] == 100.0
    assert summary["claim"] == "observational-not-causal"


def test_field_summary_computes_quality_cost_only_on_joint_measurements():
    rows = [
        {"schema": evidence.FIELD_SCHEMA, "arm": "rig", "outcome": "ok",
         "defects_caught": 2, "tokens": 4000, "minutes": 10},
        {"schema": evidence.FIELD_SCHEMA, "arm": "rig", "outcome": "ok",
         "defects_caught": 0, "minutes": 20},
        {"schema": evidence.FIELD_SCHEMA, "arm": "bare", "outcome": "ok",
         "defects_caught": 1, "tokens": 1000, "minutes": 8},
    ]
    summary = evidence.summarize_field_study(rows)
    rig = summary["arms"]["rig"]
    assert rig["tokens_per_defect_caught"] == 2000.0
    assert rig["economics_joint_n"] == 1
    assert rig["minutes_mean"] == 15.0
    assert summary["comparison"]["tokens_mean_delta_rig_minus_bare"] == 3000.0


def test_matched_case_count_requires_both_arms():
    rows = [
        {"schema": evidence.FIELD_SCHEMA, "arm": "rig", "outcome": "ok", "case": "checkout"},
        {"schema": evidence.FIELD_SCHEMA, "arm": "bare", "outcome": "ok", "case": "checkout"},
        {"schema": evidence.FIELD_SCHEMA, "arm": "rig", "outcome": "ok", "case": "login"},
    ]
    summary = evidence.summarize_field_study(rows)
    assert summary["matched_cases"] == ["checkout"]
    assert summary["matched_case_count"] == 1


def test_record_rig_task_inherits_existing_production_outcome_and_elapsed_time(tmp_path):
    run = tmp_path / ".rig" / "runs" / "rig-1"
    _write_json(run / "task.json", {
        "task_id": "rig-1",
        "status": "accepted",
        "task_type": "bugfix",
        "recipe": "bugfix",
        "created_at": "2026-08-09T09:00:00+09:00",
        "accepted_at": "2026-08-09T09:30:00+09:00",
    })
    _write_json(run / "outcome.json", {"task_id": "rig-1", "status": "incident"})

    rec = evidence.append_observation(tmp_path, arm="rig", outcome=None, task_id="rig-1")
    assert rec["outcome"] == "incident"
    assert rec["minutes"] == 30.0
    assert rec["task_type"] == "bugfix"
    assert rec["recipe"] == "bugfix"

    stored = evidence.field_observations(tmp_path)
    assert len(stored) == 1
    assert stored[0]["task_id"] == "rig-1"


def test_bare_observation_cannot_claim_a_rig_task(tmp_path):
    with pytest.raises(ValueError, match="only be used"):
        evidence.append_observation(tmp_path, arm="bare", outcome="ok", task_id="rig-1")


def test_unmeasured_outcome_is_not_assumed_ok(tmp_path):
    run = tmp_path / ".rig" / "runs" / "rig-1"
    _write_json(run / "task.json", {"task_id": "rig-1", "status": "accepted"})
    with pytest.raises(ValueError, match="outcome must be"):
        evidence.append_observation(tmp_path, arm="rig", outcome=None, task_id="rig-1")


def test_production_outcome_summary_reports_coverage_separately_from_incident_rate(tmp_path):
    for task_id in ("a", "b", "c"):
        _write_json(tmp_path / ".rig" / "runs" / task_id / "task.json",
                    {"task_id": task_id, "status": "accepted"})
    _write_json(tmp_path / ".rig" / "runs" / "a" / "outcome.json", {"status": "ok"})
    _write_json(tmp_path / ".rig" / "runs" / "b" / "outcome.json", {"status": "incident"})

    result = evidence.production_outcomes(tmp_path)
    assert result["accepted_tasks"] == 3
    assert result["outcomes_recorded"] == 2
    assert result["outcome_coverage_pct"] == 66.67
    assert result["incident_rate_pct"] == 50.0


def test_fleet_config_resolves_relative_projects_and_uses_existing_governance_rollup(tmp_path):
    org_policy = {
        "schema": "rig.policy/v2",
        "id": "org",
        "scope": "org",
        "org": "acme",
        "require_criteria": {},
    }
    projects = []
    for name, team in (("api", "platform"), ("web", "product")):
        repo = tmp_path / name
        policy_path = repo / ".rig" / "policy" / "org.json"
        _write_json(policy_path, org_policy)
        _write_json(repo / ".rig" / "org.json", {
            "schema": "rig.org/v2",
            "org": "acme",
            "team": team,
            "policy_layers": [".rig/policy/org.json"],
        })
        projects.append(repo)

    control = tmp_path / "control"
    control.mkdir()
    evidence.save_fleet_config(control, ["../api", "../web"], since_days=30)
    result = evidence.fleet_snapshot(control)
    assert result["configured"] is True
    assert result["projects"] == 2
    assert set(result["teams"]) == {"platform", "product"}
    assert result["since_days"] == 30


def test_invalid_fleet_schema_is_visible_not_treated_as_empty(tmp_path):
    _write_json(tmp_path / ".rig" / "fleet.json", {"schema": "rig.fleet/v0", "projects": []})
    result = evidence.fleet_snapshot(tmp_path)
    assert result["configured"] is True
    assert "unsupported fleet schema" in result["error"]


def test_mission_control_snapshot_has_stable_core_contract(tmp_path):
    snapshot = evidence.mission_control_snapshot(tmp_path)
    assert snapshot["schema"] == "rig.mission-control/v1"
    assert [stage["label"] for stage in snapshot["core"]] == [
        "Task", "Isolate", "Execute", "Verify", "Accept",
    ]
    assert snapshot["field_study"]["claim"] == "observational-not-causal"
    assert snapshot["fleet"]["configured"] is False
