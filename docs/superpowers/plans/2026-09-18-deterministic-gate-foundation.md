# Deterministic Gate Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Rig's engine delegates the two independent pure modules; one independent reviewer examines both.

**Goal:** Complete stage 1 of the approved design: independently testable evidence evaluation and recovery routing, with no claim that existing runtime or accept paths enforce the new rules yet.

**Architecture:** Two standard-library-only modules in the orchestrate judgement layer. The evidence evaluator consumes immutable typed records; the recovery policy consumes a runner-owned ordered event history. Neither performs I/O, calls a model, authenticates a caller, nor silently substitutes for the existing runtime gate.

**Tech Stack:** Python >=3.10, frozen dataclasses, hashlib, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-18-deterministic-flow-design.md`.

## Global Constraints

- Python >=3.10; no new dependencies.
- Preserve existing recipe and runtime behavior in this stage.
- Never convert unknown, incomplete or malformed evidence into a pass.
- Counters come from ordered failure/replan events, not an AI-provided retry count.
- Pure code cannot authenticate history or artifacts: document the caller's responsibility.
- Replanning does not erase total failures or previous replans.
- No CLI, mode-switching, snapshot collector or provenance enforcement is claimed by stage 1.

## Task 1: Immutable evidence and exact required-check matching

**Files:** Create `rig_workbench/orchestrate/gate_evidence.py` and `tests/test_gate_evidence.py`.

**Interfaces:** `EvidenceContext(run_id, unit_id, attempt_id, subject_digest, baseline_digest, environment_digest)`; `RequiredCheck(check_id, definition_digest)`; `CheckEvidence(context, check_id, definition_digest, status, exit_code, artifact_digest, schema_version=1)`; `evaluate_gate(context, required_checks, evidence) -> GateDecision` with `passed` and canonical `issues`.

- [x] Write a test for the false-pass boundary before implementation:

```python
def test_missing_required_check_never_passes():
    from rig_workbench.orchestrate.gate_evidence import (
        EvidenceContext, RequiredCheck, evaluate_gate,
    )
    ctx = EvidenceContext("run", "unit", 1, "a" * 64, "b" * 64, "c" * 64)
    decision = evaluate_gate(ctx, (RequiredCheck("test", "d" * 64),), ())
    assert decision.passed is False
```

- [x] Run `python3 -m pytest tests/test_gate_evidence.py -q`; establish the new API does not yet exist.
- [x] Implement frozen records and strict constructor validation. Reject booleans where integers are required; allow only PASS/FAIL/UNKNOWN; require a zero exit code for PASS. Use exact digest and context identity comparisons.
- [x] Extend tests for complete pass, missing and duplicate IDs, unknown IDs, stale attempt, different run/unit/subject/baseline/environment, changed check definition, FAIL/UNKNOWN, malformed records and evidence ordering. Required check lists must not be empty or duplicate.
- [x] Run the module tests and lint. Keep validation errors explicit rather than turning them into successful empty evidence.

## Task 2: Deterministic recovery and bounded replanning

**Files:** Create `rig_workbench/orchestrate/recovery_policy.py` and `tests/test_recovery_policy.py`.

**Interfaces:** `FailureEvent(sequence, check_id, error_class, target_id, classification)`; `ReplanEvent(sequence, failure_id)`; `RecoveryLimits(repeat_threshold=2, max_total_failures=6, max_replans=2)`; `stable_failure_id(check_id, error_class, target_id)`; `decide_recovery(history, limits) -> RecoveryDecision`.

- [x] Write the initial implementation-failure test before implementation:

```python
def test_first_implementation_failure_requires_repair():
    from rig_workbench.orchestrate.recovery_policy import FailureEvent, decide_recovery
    event = FailureEvent(1, "test", "assertion", "login", "IMPLEMENTATION")
    assert decide_recovery((event,)).action == "REPAIR"
```

- [x] Run `python3 -m pytest tests/test_recovery_policy.py -q`; establish the missing behavior.
- [x] Derive stable IDs from an unambiguous serialized triple and digest. Validate ordered events, known classifications, eligible replan events, and strict integer limits.
- [x] Implement the declared classification-to-action mapping. Count repeated failures since the relevant replan for routing, and lifetime failures/replans for hard limits. Never accept a history that continues past an already-terminal decision.
- [x] Add cases for threshold boundaries, repair after replan, cumulative failure cap, two replans followed by failure, mismatched or fabricated replan events, malformed input, unknown classification, colliding-looking identity triples, and deterministic decisions.
- [x] Run the module tests and lint.

## Task 3: Independent review, regression checks and handoff

**Files:** Add `docs/deterministic-gate-foundation.md`; preserve a copy of the approved design and this plan. Do not modify an existing runtime module merely to make unused code look wired.

- [x] Have an independent reviewer attempt false-pass and unbounded-loop counterexamples against both modules. Any actionable rejection receives a regression test and a fix before completion.
- [x] Run `python3 -m pytest tests/test_gate_evidence.py tests/test_recovery_policy.py tests/test_retry_feedback.py tests/test_development_loop.py tests/test_layering_contract.py -q` under Linux/WSL. The existing conftest imports POSIX `fcntl`; do not replace it with a mock to report Windows compatibility.
- [x] Run ruff against changed Python files using the repository's CI version, 0.15.8.
- [x] Run repository structural validation and inspect actual exit codes; report pre-existing failures separately without changing unrelated checks.
- [x] Document the actual exported API, examples, passed checks and the unimplemented runtime boundary.
- [x] Review the final diff, create a local commit and export a patch as a user-facing deliverable. Do not push or publish automatically.

## Next implementation boundary

Stage 2 must collect authenticated runner-owned evidence, bind snapshots before/after checks, persist transitions atomically, execute the repair/replan path, retain counters on resume, and enforce the new outcome in accept. Stage 3 adds the two upstream mode policies and integration gate; stage 4 measures behavior on fixed and held-out tasks. Passing stage 1 tests does not establish those later properties.
