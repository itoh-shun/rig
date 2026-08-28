---
description: "rig/brainstorm — think a rough idea through: questions, alternatives, and agreement section by section. It comes before implementation and task-splitting, settling what to build, why, and in what order, so nothing goes into implementation still vague. Converges on a design brief and hands off to /rig:tasks or /rig:dev."
argument-hint: "[\"<a half-formed want>\"] [--plan]"
---

# rig/brainstorm — thinking a design through 💭

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal). This command is only the entry point; the engine lives in the skill and is not repeated here.

Then PARSE the following arguments with `--recipe brainstorm` as the default:

```
$ARGUMENTS
```

## What it does

Hands the want to the `brainstorm` recipe. The procedure — diverge, propose alternatives, agree section by section, converge, connect — is in `facets/instructions/brainstorm`, and how to go about it is in `facets/personas/brainstormer`.

- **Do not decide first; ask first**: clear the unknowns, assumptions, constraints, and success conditions with questions. Do not design on guesswork.
- **Diverge, then converge**: put up two or three alternatives with their trade-offs, and converge on one recommendation with grounds. One option is not a choice.
- **Agree section by section**: split the design into sections (data, UI, failure, migration) and take approval or correction on each. Do not settle it all at once.
- **Do not hide what is unresolved**: what cannot be settled goes under "open questions" rather than being filled in with invention.
- **Do not step into implementation**: what, why, in what order — then hand to `/rig:tasks` or `/rig:dev`.
- **Recommend one next step at the end**: from what was settled, propose the right next stage with a reason (large → `/rig:tasks`; small and clear → `/rig:dev`; heavy unknowns → investigation), and ask "shall we go with this?" before handing over. Never auto-chain without asking.

## Where it sits

`/rig:brainstorm` (what and why) → `/rig:tasks` (how to split it) → `/rig:dev` (how to build it). It also connects to `/rig:goal`, which converges on an outcome.

## Flags

- `--plan` — present the draft design brief and stop. A dry run before entering the agreement process.

## Examples

```
/rig:brainstorm "I want to build notifications"
/rig:brainstorm --plan "redesigning billing"
/rig:brainstorm "search is slow and I want to talk through how to fix it"
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
