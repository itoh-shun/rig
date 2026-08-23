from rig_workbench import mission_control


def _snapshot():
    return {
        "schema": "rig.mission-control/v1",
        "generated_at": "2026-08-09T10:00:00+09:00",
        "repo": "/tmp/demo",
        "core": [
            {"id": "task", "label": "Task", "meaning": "intent"},
            {"id": "isolate", "label": "Isolate", "meaning": "worktree"},
            {"id": "execute", "label": "Execute", "meaning": "recipe"},
            {"id": "verify", "label": "Verify", "meaning": "gates"},
            {"id": "accept", "label": "Accept", "meaning": "explicit apply"},
        ],
        "production": {
            "accepted_tasks": 10,
            "outcomes_recorded": 8,
            "outcome_coverage_pct": 80.0,
            "ok": 7,
            "incidents": 1,
            "incident_rate_pct": 12.5,
        },
        "field_study": {
            "arms": {
                "rig": {"n": 8, "incident_rate_pct": 12.5, "defects_caught": 9,
                        "defects_measured_n": 8, "tokens_mean": 4000.0,
                        "minutes_mean": 18.0, "tokens_per_defect_caught": 3555.6},
                "bare": {"n": 8, "incident_rate_pct": 25.0, "defects_caught": 3,
                         "defects_measured_n": 8, "tokens_mean": 2500.0,
                         "minutes_mean": 12.0, "tokens_per_defect_caught": 6666.7},
            },
            "comparison": {"available": True, "incident_rate_delta_pp_bare_minus_rig": 12.5},
            "matched_case_count": 8,
            "note": "observational evidence only",
        },
        "assurance": {
            "counts": {"assurance-complete": 1, "assurance-incomplete": 0,
                       "assurance-unobservable": 0, "absent": 3, "unreadable": 0,
                       "invalid": 0},
            "tasks": [{"task_id": "rig-20260101-000000-example",
                       "status": "assurance-complete", "met": 1, "unmet": 0,
                       "unobservable": 0,
                       "axes": {"gate": {"outcome": "met", "required": "passed",
                                         "achieved": "passed"}}}],
            "unreadable_tasks": [],
        },
        "fleet": {
            "configured": True,
            "projects": 2,
            "score": 0.9,
            "since_days": 90,
            "teams": {
                "platform": {"projects": 2, "score": 0.9, "failing": ["api"],
                             "findings": ["force_rate"]},
            },
            "reports": [],
        },
        "operations": {
            "tasks_total": 20,
            "tasks_active": 2,
            "gate_counts": {"failed": 1},
            "reviewer_confidence": {
                "security-reviewer": {"seeded": 10, "detected": 9,
                                      "false_positives": 1, "detection_rate_pct": 90.0},
            },
            "token_usage": {"prompt_tokens": 1000, "completion_tokens": 500, "calls": 3},
            "force_bypass_count": 1,
        },
    }


def test_html_makes_the_five_stage_contract_obvious():
    html = mission_control.render_html(_snapshot())
    for label in ("Task", "Isolate", "Execute", "Verify", "Accept"):
        assert label in html


def test_html_labels_field_evidence_as_observational_and_ui_as_read_only():
    html = mission_control.render_html(_snapshot())
    assert "observational evidence only" in html
    assert "READ ONLY" in html
    assert "Accept / discard / approve / waiver are intentionally not buttons" in html


def test_html_surfaces_quality_cost_and_fleet_measurements():
    html = mission_control.render_html(_snapshot())
    assert "tokens / caught defect" in html
    assert "platform" in html
    assert "90%" in html
    assert "force_rate" in html


def test_snapshot_schema_is_presentation_neutral(tmp_path):
    # No .rig data is a valid cold-start state: the UI should still have a core
    # contract and explicit "unmeasured" sections rather than inventing healthy zeros.
    snapshot = mission_control.build_snapshot(tmp_path)
    assert snapshot["schema"] == "rig.mission-control/v1"
    assert snapshot["operations"]["tasks_total"] == 0
    # A cold start has nothing to compare, and says so with zeros in named states rather
    # than by leaving the section out — an absent section reads as a page that never looked.
    assert snapshot["assurance"]["counts"]["assurance-complete"] == 0
    assert snapshot["assurance"]["counts"]["absent"] == 0
    assert snapshot["assurance"]["tasks"] == []
    assert snapshot["field_study"]["arms"]["rig"]["n"] == 0
