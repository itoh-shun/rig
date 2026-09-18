# Upstream Lifecycle Implementation Plan

> **For agentic workers:** Use subagent-driven development and independent review for each component. Steps use checkbox syntax for tracking.

**Goal:** Enforce requirements/design readiness and versioned change handling in waterfall and iterative strict runs.

**Architecture:** Pure lifecycle policy computes readiness and invalidation. Schema-2 strict runtime owns protected state, effects, revision history and integration verification. Thin explicit CLI actions preserve existing command semantics.

**Tech Stack:** Python 3.10+, standard library policy, existing ports/StrictIO, pytest, WSL/Linux bubblewrap.

**Spec:** `docs/superpowers/specs/2026-09-18-upstream-lifecycle.md`

## Global constraints

- No implicit approval, legacy fallback, hook injection, skipped integration gate or budget reset.
- Preserve legacy/schema-1 behavior, frozen architecture boundaries and bound-task force-accept restrictions.
- Explicit modes; fixed ordered feature inventory per run; earlier-only dependencies.
- Validate actual CLI commands and source artifacts; do not claim live provider/UI verification from fixtures.

## Task 1: Pure upstream policy

Files: new `rig_workbench/orchestrate/lifecycle_policy.py`, `tests/test_lifecycle_policy.py`.

Interfaces: `validate_plan`, `plan_digest`, `decision_digest`, `readiness`, `affected_units`, `compile_steps`.

- [x] Write failing tests for draft/ready, trace coverage, both modes, dependency and approval freshness.
- [x] Implement exact schema checks, stage digests and deterministic readiness reasons.
- [x] Implement change-derived transitive invalidation and step compilation.
- [x] Verify rejected/stale decisions cannot resurrect an old approval, and unknown input fails closed.

## Task 2: Protected lifecycle runtime

Files: `deterministic_runtime.py`, focused lifecycle runtime helper(s), runtime tests.

Interfaces: optional `initialize(..., lifecycle_plan=None)`, `lifecycle_decide`, `lifecycle_revise`; existing resume/accept remain entry points.

- [x] Write failing tests before adding schema 2 and UPSTREAM gating.
- [x] Freeze runtime identity separately from versioned plan/steps.
- [x] Add locked decision and revision operations, optimistic revision/digest checks and retained history.
- [x] Preserve recovery counters and replan obligations across changes.
- [x] Add dedicated integration checks to final evidence and acceptance validation.
- [x] Verify real isolation, restart, tampering, inflight interruption and schema-1 compatibility.

## Task 3: CLI and usable guidance

Files: new lifecycle CLI module, orchestrate `cli.py`/`commands.py` as needed, CLI tests,
user docs and README indexes, command/computational-orchestration guidance as needed.

- [x] Add template/init/decide/revise/status under explicit lifecycle namespace.
- [x] Reuse isolation and bound-task lock ordering; init calls no provider.
- [x] Render revision/digests, unmet conditions and actionable next commands.
- [x] Exercise complete waterfall and iterative subprocess workflows and rejected integration.
- [x] Document natural-language drafting, explicit decisions, rejection/revision and unsupported Scrum features.

## Task 4: Review and delivery

- [x] Cross-review components with adversarial bypass and counter-preservation cases.
- [x] Run integrated new and relevant existing tests on a native WSL checkout matching final sources.
- [x] Run lint, structure/selftest, architecture/layering, documentation and applicable prompt checks.
- [x] Commit reviewed changes locally, export a base-checked patch and concise user report.
- [x] Distinguish implemented behavior from live Claude Code/model checks and release status.
