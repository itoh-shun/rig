---
description: "rig/goal — a goal-driven loop. Give it a high-level goal and it turns that into acceptance criteria, then cycles read the situation, decide the next move, delegate to an existing flow, check against the criteria, until the goal is met. Stops when stuck. The way in when you want to declare an outcome rather than pick a process."
argument-hint: "[the goal you want reached (free text)] [--autonomous] [--plan] [--capture]"
---

# rig/goal — the goal-driven loop

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. It is the same engine every other core flow and installed extension uses, running in goal mode.

Then PARSE the following arguments with `--recipe goal-loop` as the default, and cycle until the goal is reached:

```
$ARGUMENTS
```

With no argument, ask for the goal in one line before starting. Do not invent one.

## What it does

Hands the goal to the `goal-loop` recipe. The procedure — turn the goal into acceptance criteria, read the current situation, decide the smallest single move that narrows the gap, delegate to the right `/rig:*`, check against the acceptance gate, then stop when satisfied, cycle again when not, and stop and escalate after two cycles with no progress — is in `facets/instructions/goal-loop`, driven in the `goal-driver` voice.

- goal is **the driver of the cycle**. Implementation, review, and investigation are run by what it delegates to (`/rig:dev` and its siblings) and by subagents — context-minimal.
- **Stop once the criteria are met**; do not keep building. **Stop and hand back after two cycles with no progress**; no infinite loops.

## Flags

- `--autonomous` — drop the per-cycle gate and run under `patterns/autonomous-loop` (`ScheduleWakeup`). The **capture gate is not lifted**.
- `--plan` — present the acceptance criteria and the intended loop, then stop. A dry run; it does not RUN.
- `--capture` — write what the loop learned (why it got stuck, what was decided) into the knowledge layer without the confirmation dialogue. The proposal and the after-the-fact report are still shown.

## Examples

```
/rig:goal "fix the login bug, with a regression test, through review"
/rig:goal --plan "get this issue into a state where it can be solved"
/rig:goal --autonomous "implement feature X to the point a PR can go out"
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
