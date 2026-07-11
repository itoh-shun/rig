"""workbench config: gate presets / task types / status vocabularies (split from scripts/workbench.py)."""

# ── acceptance-gate presets (source of truth; the instruction references this) ──
GATE_PRESETS: dict[str, list[str]] = {
    # Standard gate shared by all task_types
    "standard": [
        "task_intent_satisfied",
        "no_unrelated_diff",
        "diff_summary_written",
        "risk_summary_written",
        "tests_pass_or_explained",
        "no_type_errors_or_explained",
        "no_secret_leak",
        "no_gate_tampering",     # anti-tamper sensor (hardening.py) — covers bugfix/feature too (they layer on standard)
        "no_injection_markers",  # injection-marker sensor (injection.py)
        "no_destructive_operation",
    ],
    # bugfix-specific (layered on top of standard)
    "bugfix": [
        "bug_cause_identified",
        "fix_is_minimal",
        "regression_test_added_or_explained",
        "existing_behavior_preserved",
        "no_unrelated_refactor",
    ],
    # feature-specific (layered on top of standard)
    "feature": [
        "requirement_summary_written",
        "implementation_matches_requirement",
        "tests_added_or_explained",
        "public_api_changes_documented",
        "migration_or_backward_compatibility_considered",
    ],
    # refactor-specific (layered on top of standard)
    "refactor": [
        "behavior_boundaries_identified",
        "no_unintended_behavior_change",
        "tests_confirm_behavior_preserved",
        "no_unrelated_refactor",
        "public_api_changes_documented_if_any",
    ],
    # For review tasks (produces no diff, so standard is not included)
    "review": [
        "findings_are_concrete",
        "severity_labeled",
        "file_references_included",
        "blocking_and_non_blocking_separated",
        "false_positive_risk_considered",
    ],
    # For security checks (layered on top of review)
    "security": [
        "authn_authz_impact_checked",
        "user_input_flow_checked",
        "secret_exposure_checked",
        "unsafe_eval_or_shell_checked",
        "dependency_risk_checked",
    ],
}

# task_type → applied gate presets (listed in composition order: first is base, rest are layered on)
TASK_TYPES: dict[str, list[str]] = {
    "bugfix": ["standard", "bugfix"],
    "feature": ["standard", "feature"],
    "refactor": ["standard", "refactor"],
    "test": ["standard", "feature"],
    "performance": ["standard", "bugfix"],
    "documentation": ["standard"],
    "design": ["standard"],
    "investigation": ["standard"],
    "release_support": ["standard"],
    "review": ["review"],
    "security_review": ["review", "security"],
}

VALID_STEP_STATUS = ("pending", "running", "passed", "failed", "skipped")
VALID_CRITERION_STATUS = ("pending", "passed", "failed", "warning", "skipped")
VALID_VERDICT = ("APPROVE", "REJECT", "APPROVE_WITH_CONDITIONS")

STEP_ICON = {"passed": "✓", "failed": "✗", "running": "▸", "pending": "…", "skipped": "-"}
CHECK_ICON = {"passed": "✓", "failed": "✗", "warning": "⚠", "pending": "…", "skipped": "-"}

NEXT_ACTIONS = {
    "running": "Running. Evaluate the gate after completion (workbench.py gate <id> --set …)",
    "gate_passed": "Review the diff with /rig diff → apply with /rig accept (or drop with /rig discard)",
    "gate_failed": "Fix the unmet criteria and re-evaluate the gate (if still failed, /rig discard)",
    "accepted": "Review git diff --staged and commit → clean up the worktree with /rig discard <id>",
    "discarded": "Finished (only the run log is kept)",
}

RECOMMENDATION = {
    "failed": "Fix the failed acceptance-gate criteria before accept (check with `workbench.py gate`).",
    "pending": "Evaluate the remaining acceptance criteria before accepting.",
    "passed_with_warnings": "Review the warnings, then accept if they are acceptable.",
    "passed": "Safe to accept.",
    "skipped": "This task has no gate criteria configured — verify manually before accepting.",
}

ACTIVE_STATUSES = ("running", "gate_passed", "gate_failed")
