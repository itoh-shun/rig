# Upstream lifecycle modes

Status: implementation specification for the previously agreed deterministic-flow stage 3.
Base: released v3.2.0 (`bb99a0ee1353f3a40593985c71250df1eac6b81c`).

## Outcome

Make requirements, design, acceptance criteria and declared checks prerequisites for
implementation. Waterfall requires the entire release baseline to be ready. Iterative
execution requires the shared contract and current feature to be ready, with completed
dependencies, and permits independent later features to remain drafts. Both modes require
final integration checks; feature completion is not release completion.

AI can draft requirements, designs and diagnoses. The controller computes readiness,
invalidation and transitions. Approval is an explicit trusted-caller action bound to the
displayed content digest, not a boolean supplied in a proposed plan. Actor labels record
attribution, not authenticated identity. Structural checks do not establish semantic quality.

## Scope and choices

Use the existing strict runner, isolation, task binding and acceptance boundary. Add a
pure lifecycle policy and explicit lifecycle CLI actions. A separate child-run coordinator
would duplicate locking and permit retry-budget resets; prose-only recipes would not enforce
the upstream gate. Neither alternative is adopted.

This is a sequential feature lifecycle, not a Scrum role/event/sprint management system.
The initial feature IDs and order are fixed for a run. Dependencies must name earlier
features. New releases or a different feature inventory need a new run. Explicit revisions
may change requirements, design, checks, common contracts and mode. Existing legacy and
strict schema-1 runs keep their semantics. No automatic hook is introduced.

## Plan contract

Plan schema 1 has exactly `schema_version`, `mode`, `objective`, `shared`, `units`,
and `integration_checks`. Mode is explicitly `waterfall` or `iterative`.

- Shared: `constraints`, `interfaces`, `open_questions`, each a list of strings.
- Ordered unit: `id`, `depends_on`, `requirements`, `design`, `checks`, `open_questions`.
- Requirement: `id`, `text`, `acceptance`; acceptance item: `id`, `text`, `check_ids`.
- Design: `summary`, `requirement_ids`.
- Check: `id`, `command`; integration checks use a separate namespace.

Drafts may have empty requirements, design, checks and integration definitions. Unknown
fields, invalid types, duplicate IDs, dangling nonempty references and invalid dependencies
are refused. Draft incompleteness produces readiness reasons, not permission to execute.
Start requires a nonempty objective, no blocking shared/current questions, nonempty
requirements and acceptance criteria, complete design coverage, complete check coverage,
and nonempty final integration checks. Waterfall applies readiness to every feature.

## Approval and rejection

Scopes are `shared` and a feature ID; stages are `shared`, `requirements`, `design`.
Records contain unique ID, scope, stage, digest, actor, decision and nonempty reason.
The latest decision for a scope/stage is authoritative. A rejection cannot reveal an older
approval. Design approval must follow the current requirements approval. Digests bind the
appropriate shared/requirements/design content so refining an unrelated later draft does
not invalidate an approved earlier feature unnecessarily.

CLI decisions require expected revision and digest. Decisions and revisions are applied
under task-then-state locks after rereading authoritative state. Untrusted provider processes
cannot write these records. Rejection is retained with reason and affected stage, and blocks
execution until a current decision permits it. It does not reset implementation failures.

## Runtime and revisions

Lifecycle runs use strict runtime schema 2. The frozen identity includes run, workspace,
state path, provider configuration, feature order and recovery limits. Versioned definitions
add the current plan and compiled steps. An `UPSTREAM` waiting phase is resumable and
retains the phase it blocked; it is not an implementation failure or fabricated completion.
The gate runs before effects and at final acceptance. Schema 1 remains unchanged.

Revision history retains prior plans/digests, reason, actor and invalidated IDs. Compute
affected features from old/new content and dependencies, never from a caller-supplied
affected list. Shared/mode/dependency changes conservatively invalidate all features;
local changes invalidate the feature and transitive dependents. Every revision invalidates
final integration evidence. Unaffected implementation completion may remain, but final
checks rerun against the current full subject and contract.

Preserve failure events, replan events, plan history and cumulative limits. A revision must
not escape an inflight operation or an exhausted recovery budget. A revision used to resolve
a mandatory replan must consume a valid replan event rather than bypass it. Retain old
evidence for audit in revision history while removing it from current acceptance eligibility.

Final verification runs all feature checks and the dedicated integration checks without a
new integration generator. Acceptance, including force acceptance, requires current full
evidence and current upstream readiness. A rejected integration check cannot produce DONE.

## User interface

Canonical entry is `python3 scripts/orchestrate.py lifecycle` (or the actual orchestrate
module). Actions: template, init, decide, revise, status. Init snapshots a proposed plan into
an isolated strict run and stops at UPSTREAM without calling a provider. Existing resume
executes it once approvals are ready; existing progress displays waiting/terminal state.
Status exposes revision, relevant digests, unmet conditions and next actions, avoiding raw
provider payloads. Conversation guidance helps draft the plan and present decisions but
must not infer approval from provider PASS text. Existing plan/approve commands retain
their meanings. Installed plugin/cache changes and publishing are separate from this task.

## Acceptance evidence

Test waterfall refusal before any provider call; iterative early-feature execution with a
later draft; dependency rejection; current-digest approval; stale/duplicate/reordered
decisions; rejection superseding approval; requirement/design/check coverage gaps;
revision invalidation; mandatory integration failure; schema corruption; stopped/inflight
revision refusal; unchanged retry budgets; restart and bound-task acceptance.

Run pure tests, real CLI subprocess/isolated cmd-provider workflows, schema-1 regressions,
architecture/layering, docs registry, lint and structural checks. Live model quality and
Claude Code UI behavior remain separate from these deterministic fixture results.
