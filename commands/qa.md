---
description: "rig/qa — designing test cases. Sweeps seven fixed lenses (first-time user, veteran, malicious actor, data-integrity auditor, migration, regression, spec sceptic) without dropping any, requires a Test Basis for each case, marks the unverified as \"needs checking\", and makes spec gaps visible through requirement coverage. --migration switches to the migration track, --review critiques existing cases. The AI is a test designer; running, judging, and fixing stay with people."
argument-hint: "[target (an issue, a feature, a diff, a spec)] [--migration] [--review <path>] [--plan]"
---

# rig/qa — test design 🧪

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. It is the same engine the other recipes use, pointed at designing test coverage.

Then PARSE the following as the target, with `--recipe test-design` as the default:

```
$ARGUMENTS
```

With no argument, the target is the current working tree's changes (`git diff`).

## What it does

Hands the target — an issue, a feature, a diff, a spec — to the `test-design` recipe. The procedure (fix the target and the track, inject `qa-test-lenses`, sweep the seven lenses, hold to grounds and honesty, structure the result as `test-cases`) is in `facets/instructions/test-design`.

- **Drop none of the seven lenses**: first-time user, veteran on the floor, malicious actor, data-integrity auditor, migration operator, regression watchdog, spec sceptic. **At least one case per lens** — and where there is none, say "not applicable" with the reason. Do not let it drift towards the happy path.
- **Grounds and honesty**: tie every case to a **Test Basis** — a primary source. Where the code is unread or the spec unsettled, write `needs checking (not done)` rather than asserting. Sort cases into testable, on hold, and untestable, and **send spec gaps back** rather than filling them with invention.
- **Two tracks**: the default is a new feature (does it meet the requirement). `--migration` is migration (does it still behave as it did) plus a requirement-coverage table.
- **The AI is a test designer, not a tester**: it does not run tests, judge pass or fail, or edit existing cases (`--review` **only critiques**). Confidential material — real data — does not pass through unexamined.
- The actual work, reading and designing, is done by subagents (context-minimal). Implementing or automating the cases goes to `/rig:dev`.

## Flags

- `--migration` — the migration track: the spec's starting point becomes the current help text and current behaviour, and a requirement-coverage table is added.
- `--review <path>` — review existing test cases (**critique only, no edits**), sweeping the seven lenses for what is missing.
- `--plan` — present the composition (track, lenses, target) and stop. A dry run.

## Examples

```
/rig:qa                                  # design cases for the current changes
/rig:qa the login feature in issue #123  # cases for one feature, new-feature track
/rig:qa --migration help section: billing  # migration track: does it still behave as before
/rig:qa --review ./tests/cases.csv       # review existing cases for missing lenses
/rig:qa --plan                           # see the composition first
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
