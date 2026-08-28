---
description: "rig/loop — a recurring driver that repeats something (a command, a rig flow, a task) on an interval or at its own pace, with a stopping condition (--until, --times, or an explicit stop) and a safety ceiling. The opposite of goal, which converges on an outcome: this watches, polls, and runs on a schedule."
argument-hint: "[\"what to repeat (a command or a task)\"] [--every <dur>] [--until \"<check>\"] [--times N] [--plan]"
---

# rig/loop — repeating and watching 🔁

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal). This command is only the entry point; the engine lives in the skill and is not repeated here. Scheduling reuses the existing `patterns/autonomous-loop` (`ScheduleWakeup`).

Then PARSE the following arguments with `--recipe loop` as the default:

```
$ARGUMENTS
```

## What it does

Hands the target to the `loop` recipe. The procedure — fix the target, the interval, and the stopping condition; run one tick; decide whether to stop; schedule the next tick or finish — is in `facets/instructions/loop-driver`.

- **What it repeats**: a rig flow such as `/rig:dev` or `/rig:pr`, or any command (checking CI, aggregating something).
- **When it goes again**: `--every <dur>` drives it by time (`10m`); without it, it paces itself, moving on a signal or an event.
- **Where it stops (required)**: `--until "<check>"` is a mechanically verified condition; `--times N` is a count; an explicit stop needs the safety ceiling confirmed. Never enter an unbounded watch with neither a condition nor a ceiling.
- Every tick is **reported**. Where a tick writes, pushes, or merges, the step gate of whatever it delegates to confirms it, each time.

## How it differs from goal (they compose)

- `/rig:goal` — **converge until achieved**; the end is the acceptance criteria. Work that finishes.
- `/rig:loop` — **repeat and watch**; the end is a stopping condition or a count. Work that does not.
- `/rig:loop --every 1h /rig:goal "…"` kicks a goal on a schedule: loop is the outer scheduler, goal is the convergence inside it.

## Flags

- `--every <dur>` — the time-driven interval (`5m`, `1h`), following the `ScheduleWakeup` conventions (270 and 1200; **never 300**). Without it, self-paced.
- `--until "<check>"` — the stopping condition, decided mechanically: a shell exit code of 0, a GitHub MCP status, a grep.
- `--times <N>` — finish after N runs.
- `--plan` — present the target, interval, and stopping condition, then stop. A dry run.

## Examples

```
/rig:loop --every 10m --until "CI is green" /rig:pr 1234    # watch a PR's CI until it goes green
/rig:loop --times 3 /rig:dev --only review                  # run the review three times
/rig:loop --every 1h /rig:goal "get the issue through review"
/rig:loop "aggregate the report each morning"               # a self-paced recurring chore
/rig:loop --plan --every 10m --until "the deploy succeeded" ...
```

## Two compositions worth knowing

```
/rig:loop --until "PR #123 is MERGED or CLOSED" "/rig:pr 123"   # babysitting a PR
/rig:loop --every 7d "/rig:import --check-updates"              # a dependabot for skills
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
