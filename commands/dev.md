---
description: "rig — a LEGO-style dev-flow orchestrator. Composes facet, pattern, step, and recipe bricks at invocation time into a harness built for the task at hand: review, implement, PR, and so on."
argument-hint: "[--recipe review-only|release-flow|design-first|hotfix] [--only <step>] [--from <step>] [--issue <id>] [--design] [--review] [--tdd] [--visual] [--autonomous] [--workflow] [--plan] [--save-recipe <name>] [--capture] [--list] [--validate] [--adversarial] [--cross-llm] [--persona <name>] [free text]"
---

# rig — the dev-flow orchestrator

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md strictly** — every rule of PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, the recipe schema, knowledge-layer injection. This command is only the entry point; the engine lives in the skill and is not repeated here.

Then PARSE the following arguments, compose the harness, and run it:

```
$ARGUMENTS
```

## Quick reference (the detail is in skill §3-§6)

**Shipped recipes** (`--recipe <name>`):
- `review-only` — nothing but a 3-way parallel review (security, design, test) of the current changes
- `release-flow` — intake → design? → implement → verify → review? → pr → merge, where `?` is size-aware and conditional
- `design-first` — a heavier design phase
- `hotfix` — the shortest path: intake → implement → verify → pr

**The flags you will reach for**:
- `--plan` — compose the harness, present it, and stop. A dry run; it does not execute.
- `--only <step>` / `--from <step>` — slice the range (`--only review`)
- `--design` / `--review` / `--tdd` — force that step on, regardless of the size-aware default
- `--issue <id>` — feed an existing issue into intake
- `--autonomous` — run through without the per-step gate (the capture gate is not lifted)
- `--workflow` — switch the backend to the ultracode Workflow tool. Opt-in, and only worth it for heavy multi-stage or exhaustive work.
- `--save-recipe <name>` — save this composition as a recipe (`--user` for the user layer)
- `--capture` — write what the run taught into the knowledge layer without the confirmation dialogue (the proposal and the after-the-fact report are still shown)
- `--list` — list the available bricks, recipes, and flags, then stop
- `--adversarial` — add the adversarial review step (AI tells, human readability, comments that earn nothing)
- `--cross-llm` — assume review by another vendor's model: inject the `cross-llm-legibility` discipline into implement ("a Codex, Copilot, or GPT reader should get it first time") and add the `cross-llm-reviewer` lens to review

## Examples

```
/rig:dev --plan --only review "the current changes"   # dry-run the review composition
/rig:dev --only review                                # run the 3-way parallel review
/rig:dev --recipe release-flow --design "feature X"   # the full flow, design included
/rig:dev --recipe hotfix --issue 1234                 # an emergency fix by the shortest path
```

## Rules (summarised from the skill, which is the source of truth)

- **Empty or ambiguous arguments** → compose in conversation: ask what they want, propose bricks, let them choose, present the harness, confirm.
- **`--plan`** → stop at COMPOSE and present the composed harness in human-readable form. Do not RUN.
- **context-minimal (a hard rule)** → the real work is always dispatched to a subagent. The parent only dispatches, aggregates, and judges gates. Long output never reaches the parent's context.
- **Size-aware defaults** → S and M turn design, review, and tdd off automatically; an explicit flag turns them on.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
