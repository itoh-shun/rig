# Adaptive Bugfix and Benchmark Redesign

Date: 2026-07-20
Status: Approved design

## Objective

Improve rig so that it demonstrates a practical quality advantage over a bare
one-shot coding agent without imposing the current fixed review cost on every
task.

The primary target is quality. Cost and benchmark credibility are co-equal
constraints:

- Run at least 10 repository-shaped tasks at least 3 times each.
- Reduce rig's silent-defect rate by at least 50% relative to bare mode.
- Keep rig's safe-stop rate at or below 20%.
- Keep rig's average LLM invocation count at or below 2.5 times bare mode.
- Produce no unrelated diffs or workspace leaks.

The workflow and scoring rules must be provider-independent. Results are
reported separately for each provider and model; results from different
providers are never pooled to hide a weak result.

## Scope

This change introduces an opt-in `adaptive-bugfix` recipe and replaces the
current benchmark's built-in, single-file task model with an external,
repository-shaped corpus.

The existing `bugfix` and `fast-bugfix` recipes remain unchanged. Making
`adaptive-bugfix` the default is explicitly deferred until it passes the
benchmark. The default switch will be a separate change.

The initial corpus contains 10 to 15 small repositories, primarily Python and
TypeScript. It does not include large third-party OSS snapshots.

## Architecture

### Adaptive Recipe

`adaptive-bugfix` has four logical stages:

1. `implement`: one generator edits the isolated task repository.
2. `assess`: deterministic checks and diff-risk analysis select the relevant
   review lens without an LLM call.
3. `targeted-review`: one independent reviewer checks the change using the
   selected lens.
4. `acceptance`: mechanical checks and the review result decide whether the
   run may complete.

Additional review or repair is conditional:

- A concrete blocking finding permits one informed repair attempt only when
  the finding includes a reproducible, mechanically checkable failure
  condition.
- A change spanning multiple high-risk domains permits a second reviewer.
- A failed post-repair mechanical check stops the run without another
  generation.
- Exhausting the invocation budget stops the run and cannot be reported as
  complete.

The recipe records the selected reviewer, matched risk signals, budget
decisions, findings, repair feedback, and final outcome in run state.

### Components

The implementation is divided into bounded components:

- Risk analyzer: consumes changed paths and diff metadata and returns ranked
  risk domains with evidence.
- Reviewer selector: maps ranked risk domains to a shipped reviewer persona.
- Invocation budget: owns the normal and high-risk call limits and decides
  whether review, repair, or escalation is allowed.
- Adaptive run controller: advances the recipe using only structured outputs
  from the preceding components.
- Benchmark task loader: validates and loads external task definitions.
- Provider adapter: gives bare and rig modes equivalent editing capability.
- Benchmark scorer: evaluates hidden checks and classifies outcomes.
- Benchmark reporter: aggregates per-provider metrics into JSON and HTML.

These components communicate through structured dictionaries or typed data
objects. Benchmark task data and hidden checks do not depend on recipe
internals.

## Risk Analysis and Review Selection

Risk analysis is deterministic and provider-independent. Initial signals
include:

- Authentication, authorization, ownership, SQL, secrets, unsafe execution,
  and trust-boundary changes select `security-reviewer`.
- Public API, schema, dependency, configuration, and structural changes select
  `design-reviewer`.
- Missing regression coverage, boundary behavior, state transitions, error
  paths, and test modifications select `test-reviewer`.

When signals overlap, the selector ranks them by severity and affected change
scope. The highest-ranked domain supplies the first reviewer. A second
reviewer is allowed only when two independently high-risk domains remain after
ranking.

Unknown file types and unrecognized risk signals do not silently bypass
review. They fall back to `test-reviewer`, and the fallback reason is recorded.

## Invocation Budget

Bare mode has one LLM invocation.

The normal adaptive path has two invocations:

1. implementer
2. targeted reviewer

The default task limit is three invocations, allowing one informed repair.
Tasks with multiple high-risk domains may use four invocations to add a second
independent reviewer or one informed repair. A blocking finding that cannot be
verified mechanically produces a safe stop instead of an unverified repair.
The benchmark enforces the aggregate limit of 2.5 times bare mode, so
expensive high-risk runs must be offset by normal two-call runs.

Provider attempts count toward the cost metric whether they succeed or fail.
Mechanical checks, risk analysis, hidden checks, and reporting do not count as
LLM invocations.

## Benchmark Corpus

Tasks live under `benchmarks/tasks/<task-id>/`. Each task contains:

- A visible repository snapshot.
- A provider-neutral goal.
- Public tests available to the agent.
- Hidden checks stored outside the copied working repository.
- Metadata describing language, difficulty, risk domain, and expected files.
- A canonical fix used only to validate corpus consistency.
- A deliberately narrow fix when the task is intended to measure a
  public-test/hidden-spec gap.

The first corpus contains 10 to 15 tasks across Python and TypeScript. It
includes authorization siblings, state consistency, API compatibility,
boundary behavior, error handling, unsafe query construction, regression
coverage, and unrelated-change traps.

Every task must satisfy these corpus invariants:

- The original snapshot fails its intended hidden requirement.
- The narrow fix passes public tests but fails the hidden check when a hidden
  gap is part of the scenario.
- The canonical fix passes public tests and hidden checks.
- Hidden checks are not copied into the agent's work directory.

## Fair A/B Execution

Bare and rig modes receive:

- The same provider and model.
- The same provider-neutral goal.
- Independent copies of the same starting repository.
- Equivalent file-editing permissions inside their scratch repositories.
- The same public tests and execution limits.

Bare mode is a single editing-agent invocation with no rig orchestration.
It no longer relies on returning one target file in a fenced code block.
Rig mode runs `adaptive-bugfix`.

The harness records provider name, model name, provider CLI or endpoint
version, rig version, recipe version, corpus version, timestamps, elapsed
time, invocation count, changed files, test outcomes, hidden-check outcomes,
and runner state.

## Scoring

Each valid run is classified as:

- `clean_pass`: completed and hidden checks pass.
- `silent_defect`: completed while hidden checks fail.
- `safe_stop`: did not complete, but the resulting code passes hidden checks.
- `stopped_wrong`: did not complete and hidden checks fail.
- `infra_error`: the provider or harness could not execute the arm.

The acceptance calculation is performed separately for every provider and
model:

- At least 10 tasks have at least 3 valid paired runs.
- Rig's silent-defect rate is at least 50% lower than bare mode.
- Rig's safe-stop rate is no more than 20% of valid rig runs.
- Rig's average invocation count is no more than 2.5 times bare mode.
- Both arms have zero unrelated diffs and workspace leaks.

If bare mode has zero silent defects, the relative quality result is
`inconclusive`, not a pass. The report still shows the absolute rates and the
cost and safety metrics, but it does not claim a demonstrated quality
advantage.

An `infra_error` is never silently excluded. All attempts remain in the
report. A provider result is invalid when more than 10% of planned arm runs
end in infrastructure errors. Replacement paired runs may be executed, but
the original errors remain visible.

## Failure Handling

Provider launch failures, authentication failures, endpoint failures, and
timeouts are `infra_error`.

Malformed reviewer output, unsupported reviewer claims, blocking findings
without a reproducible check, exhausted invocation budgets, and inability to
resolve a blocking finding are product behavior. They produce a safe stop or
stopped-wrong result according to hidden checks, not an infrastructure
exclusion.

A hidden-check crash means the task definition is invalid and fails the
benchmark suite. A risk-analyzer failure falls back to `test-reviewer`, records
the analyzer error, and prevents automatic acceptance without review.

No path converts an unknown or failed state into `clean_pass`.

## Test Strategy

Unit tests cover:

- Risk signal extraction and priority ordering.
- Reviewer selection and fallback behavior.
- Normal and high-risk invocation budgets.
- Outcome and acceptance-metric calculations.
- Task schema validation and hidden-check isolation.

Integration tests cover:

- Mock clean pass.
- Blocking finding followed by successful informed repair.
- Multiple high-risk domains and second-reviewer dispatch.
- Budget exhaustion and safe stopping.
- Provider timeout and infrastructure-error accounting.
- Equivalent editable workspaces for bare and rig modes.

Corpus contract tests run the original, narrow, and canonical variants for
every task. Provider adapter contract tests cover Claude, Codex, and
OpenAI-compatible local endpoints without requiring paid calls in CI.

Golden tests cover JSON and HTML reporting. Existing `bugfix`,
`fast-bugfix`, and old benchmark JSON rendering remain regression-tested.

## Operation and Rollout

CI runs deterministic schema, mock, corpus-contract, adapter-contract, and
reporting tests. It does not make paid LLM calls.

Full real-provider benchmarks are explicit manual commands. Their JSON and
HTML outputs can be archived and compared, but they are not committed as
unqualified universal claims.

Rollout occurs in two phases:

1. Ship `adaptive-bugfix` as an opt-in recipe with the redesigned benchmark.
2. After the agreed thresholds pass on representative provider runs, propose
   a separate change that makes the adaptive recipe the default.

## Non-Goals

- Model-specific prompt tuning.
- Pooling providers into a single favorable score.
- Automatically running paid benchmarks in CI.
- Importing large OSS repositories into the initial corpus.
- Changing existing default recipes in this change.
- Claiming universal quality improvement from mock-provider results.
