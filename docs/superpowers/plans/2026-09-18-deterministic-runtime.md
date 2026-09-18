# Deterministic Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Rig delegates the independent IO boundary, runner, and CLI binding tasks; implementations are reviewed by a different worker.

**Goal:** Make the stage-1 evidence and recovery rules executable through an opt-in CLI path and enforce the result at the linked workbench accept boundary.

**Architecture:** Linux bubblewrap restricts generator writes to the isolated worktree; verification and diagnosis see it read-only. State and evidence live outside that worktree, with atomic writes and a per-run lock. A separate strict runner consumes the stage-1 pure APIs and never falls back to the legacy retry runner.

**Tech Stack:** Python >=3.10, standard library, existing ports/secure_fs, Linux bubblewrap, pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-deterministic-flow-design.md`, stage 2. The initial strict lane is sequential; DAG/adaptive workflows are rejected explicitly until supported.

## Global constraints

- Legacy runs retain their behavior unless strict mode is requested.
- Missing isolation, malformed state, unknown schema, missing checks and unsupported options block before provider execution.
- Generator output cannot change check definitions, budgets, stored evidence or run history.
- All required checks must pass against the same current subject; final checks rerun after later implementation steps.
- An external operation is marked inflight durably before invocation. An interrupted inflight operation is blocked on resume rather than invoked a second time.
- No real paid provider call is required for tests; use actual sandboxed fixture processes and actual check commands.
- New guarantees are limited to tested filesystem/process boundaries. Reading the host filesystem is not confidentiality isolation.
- CLI mode support for waterfall/iterative remains stage 3, not an implicit property of this lane.

## Task 1: OS boundary and evidence storage

Create `orchestrate/deterministic_io.py` and `tests/test_deterministic_io.py`.

Interface: `StrictIO(workspace, state_path)` with `preflight()`, `snapshot()`, `run(argv, input=None, timeout=600, writable=True, network=False)`, `save(state)`, `load()` and `locked()`.

1. Test an actual child trying to write outside the worktree before implementation.
2. Build the Linux-only adapter: read-only host bind, explicit workspace bind, private PID/proc and temporary area, read-only git metadata; refuse unsupported hosts.
3. Test generator write success in scope, protected state write refusal, check/diagnosis write refusal, symlink/hardlink hazards and subprocess failure.
4. Test snapshot changes for tracked edits/deletions and nonignored untracked files; explicitly reject unsupported file types/submodules.
5. Persist strict JSON with secure atomic writes and an exclusive lock. Test malformed JSON, unknown shape, duplicate keys and concurrent lock refusal.

## Task 2: Strict execution and recovery

Create `orchestrate/deterministic_runtime.py` and `tests/test_deterministic_runtime.py`.

Interface: `initialize(state, workspace, state_path, provider_config)`, `validate_state(state)`, `run_strict(state, state_path, max_steps=40)`, `resume_strict(state_path, max_steps=40)`, `validate_acceptance(state_path, workspace)`.

1. Establish a failing real-process test for generate → machine FAIL → structured diagnosis → repair → machine PASS.
2. Bind immutable definition, provider configuration and limits; validate on every continuation.
3. Record snapshots and captured check outputs as typed evidence, then call the existing pure evaluator.
4. Generate failure identities from the machine observation. Ask the provider for a bounded structured diagnosis, not a replacement verdict. Reject missing/wrong check references and undeclared fields.
5. Use the pure recovery policy for REPAIR/REPLAN/STOP. Replanning requires a changed plan; preserve cumulative history and bounds.
6. Save write-ahead operation markers and results under one run lock. Test safe completed-boundary resumes and blocked inflight resumes.
7. Rerun every declared check on the final current snapshot before DONE. Validate stored output digests and target correspondence during acceptance.

## Task 3: CLI and non-overridable accept binding

Modify `orchestrate/commands.py`, `providers.py`, `runstate.py`, `workbench/accept.py`; add `orchestrate/deterministic_binding.py`, `tests/test_deterministic_cli.py`, and `docs/deterministic-runtime.md`.

1. Add explicit `--deterministic` and optional `--deterministic-task ID`. Strict standalone execution requires isolation; an existing task uses its own isolated worktree.
2. Apply strict `--check` to the final step; reject it if the strict contract cannot be satisfied instead of silently dropping it.
3. Route strict runs and resumes to the new driver; refuse legacy manual mutations of strict state.
4. Preserve strict isolated worktrees on completion for review, without legacy automatic fast-forward merging.
5. Bind workbench task, run ID, workspace and state path in two reciprocal records outside the writable tree. Missing/mismatched records refuse acceptance, even with `--force`.
6. Test real CLI success, failure, resume, missing checks, unknown options, strict binding tampering and stale evidence.

## Task 4: Independent integration verification

1. Cross-review the IO boundary, transition machine and binding from different workers; reproduce and fix actionable findings.
2. Run new tests together with gate-evidence, recovery, retry, development-loop, runtime-security, accept and CLI regression tests.
3. Run architecture/layering checks and the repository's pinned Ruff. New IO adapters must be explicitly described; do not weaken pure judgement rules or relax frozen budgets to hide accidental effects.
4. Run structural validation and document warnings separately.
5. Export the exact reviewed diff and a report with supported hosts/providers, actual test evidence and stage-3/4 exclusions.

## Test assertions that define completion

```python
# Real command output and exit status determine the gate, never a provider PASS.
assert machine_failure_decision.passed is False
# Interrupted work is not automatically issued twice.
assert resumed_inflight_status == "BLOCKED"
# Mode intent survives even if one task marker is removed.
assert accept_with_missing_binding_is_refused
# Replanning cannot erase the previous attempts.
assert failures_after_replan >= failures_before_replan
```

These are requirements for the integration tests, not a claim that a pure-function test proves the runtime boundary.
