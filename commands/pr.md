---
description: "rig/pr — review an open pull request. Give it a number or URL and it fetches the PR through the GitHub MCP server, reviews it in parallel from three angles (security, design, test, plus adversarial on request), and returns a structured verdict. The way in for a PR that is already open, rather than your own working tree."
argument-hint: "[PR number or URL] [--adversarial] [--comment] [--plan]"
---

# rig/pr — reviewing an open pull request

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. It is the same engine every other core flow and installed extension uses, pointed at a PR.

Then PARSE the following arguments with `--recipe pr-review` as the default and review the PR from three angles in parallel:

```
$ARGUMENTS
```

If the arguments carry no PR number or URL, ask once. Do not invent one.

## What it does

Hands the PR number or URL to the `pr-review` recipe. The procedure — fetch the PR through the GitHub MCP server, review security, design, and test in parallel under `parallel-fanout`, aggregate through `acceptance-gate` and `review-gate`, then present the combined verdict and optionally comment on the PR — is in `facets/instructions/pr-review`.

- **How it differs from `/rig:dev --only review`**: dev reviews **the diff in your own working tree**; pr reviews **a pull request that is already open**, fetched over MCP.
- The actual work — reading and judging — is done by reviewer subagents (context-minimal). A long diff never reaches the parent.

## Flags

- `--adversarial` — add the adversarial review step (AI tells, human readability, comments that earn nothing).
- `--comment` — post the result to the PR as a comment or review. **Writing, so confirmation is required**, and `--autonomous` does not lift it; the default is to present only. What gets posted follows `facets/policies/comment-policy`: Critical and High always posted, Medium and Low capped at five nits plus a "+N similar" rollup, `Pre-existing:` notes, and a re-review that posts only what is important while marking what was fixed as resolved.
- `--plan` — present the composition and stop. A dry run.

## Examples

```
/rig:pr 1234                 # review PR #1234 from three angles
/rig:pr 1234 --adversarial   # with the adversarial review as well
/rig:pr 1234 --comment       # post the review to the PR, once confirmed
/rig:pr --plan 1234          # dry-run the review composition
```

## Watching a PR (babysitting, composed with loop)

To watch a PR and re-review on every push, compose this with `/rig:loop`. No new machinery is needed; it is existing bricks put together:

```
/rig:loop --until "PR #123 is MERGED or CLOSED" "/rig:pr 123"
```

Each tick checks the PR for new pushes and CI state, re-runs the three-way review when something changed, and otherwise skips and schedules the next tick. A stopping condition is required (`--until` or `--times`), per the loop pack's safety rules.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
