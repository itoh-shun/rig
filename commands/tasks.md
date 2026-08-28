---
description: "rig/tasks — split a request into small, verifiable tasks before implementing it. plan (each task with its own verification) → implement (in order, --tdd available) → verify → review. Instead of building something large and vague: split small, check as you go, clear them in order. Runs only after approval."
argument-hint: "[\"<what you want done>\"] [--plan] [--tdd] [--orchestrate]"
---

# rig/tasks — fine-grained planning 🧩

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, acceptance-gate). This command is only the entry point; the engine lives in the skill and is not repeated here.

Then PARSE the following arguments with `--recipe task-plan` as the default:

```
$ARGUMENTS
```

## What it does

Hands the request to the `task-plan` recipe. The procedure — decompose, execute, review, converge — is in `facets/instructions/task-plan`, and how to decompose is in `facets/personas/planner`.

- **Split small**: one task is minutes and a few files. Do not create a large vague task.
- **Every task carries its verification**: what "done" looks like, as a command, a test, a grep, or an observation. A task with no verification is not a task.
- **Surface the unknowns first**: a hole in the spec or an unstated assumption does not become an invented task — it becomes "needs investigation" and gets cleared first.
- **Approve, then execute**: present the plan and move to implementation once it is agreed, so nothing large is built before the direction changes.
- **Clear them from the top**: implement in dependency order, verify each, move on. Independent tasks can run in parallel with `--orchestrate`.

## How it differs from goal

- `/rig:tasks` — **see every task up front and plan** (arrange, then clear).
- `/rig:goal` — **decide the next move reactively** (converge until achieved).

## Flags

- `--plan` — present the plan (the task table plus the unknowns) and stop without executing.
- `--tdd` — implement each task red-green-refactor.
- `--orchestrate` — run independent tasks in parallel processes (a DAG).

## Examples

```
/rig:tasks "add JWT refresh"                     # split small, through to implementation
/rig:tasks --plan "refactor the payment screen"  # see the plan first
/rig:tasks --tdd "make validation strict"        # each task under TDD
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
