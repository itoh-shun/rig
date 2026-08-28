---
description: "rig/design — a design harness with UI/UX and accessibility built in. From a description it produces a specification, component specs, wireframes, and an accessibility plan, then vets them against UI/UX heuristics and WCAG. Given a URL it fetches the running screen with Playwright and audits it. --ppt and --claudedesign add output formats."
argument-hint: "[a description, or a screen URL] [--url <url>] [--a11y-level A|AA|AAA] [--ppt] [--claudedesign] [--plan] [--persona <name>]"
---

# rig/design — designing and auditing 🎨

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, knowledge-layer injection). This command is only the entry point; the engine lives in the skill and is not repeated here. It is the same engine dev uses, pointed at the design domain.

## Two modes, chosen by whether there is a URL

| What you pass | recipe | What happens |
|---|---|---|
| a description (the default) | `design` | produce the design artefacts, then vet them for UI/UX and accessibility in parallel |
| a URL, or `--url <url>` | `design-audit` | fetch the running screen with Playwright, then audit it for UI/UX and accessibility |

A `http(s)://…` in the arguments, or `--url`, means **audit mode** (`design-audit`); anything else means **create mode** (`design`).

Then PARSE the following arguments:

```
$ARGUMENTS
```

With no arguments, ask in one line what is being designed — the feature, who it is for, which artefacts are needed — or which screen URL to audit. Do not invent it.

## What it does

- **Create (the default)**: hands the description, audience, and artefact types to the `design` recipe. The procedure — settle the requirements, produce the artefacts, choose the output backend, vet in parallel — is in `facets/instructions/design-draft` and `design-vet`. The artefacts are a design specification, component specs, wireframes or mockups, and an accessibility plan. Accessibility is built in at design time rather than added later, vetted by `ux-reviewer` (usability) and `a11y-reviewer` (WCAG 2.2), and converged at the acceptance gate. **Ground everything in what exists, claim nothing extra, and mark what is unknown `[to be filled in]`.**
- **Audit (a URL)**: hands the URL to the `design-audit` recipe. The procedure — screenshot, DOM, and axe results through Playwright, then a parallel review — is in `facets/instructions/design-audit` and `design-vet`. Read-only, no side effects.

## Output backends (create mode; they combine)

- Default: a Markdown design document.
- `--ppt`: slides through the `powerpoint-server` MCP server, in addition.
- `--claudedesign`: generated through claude.ai's design feature (the `claude_design` MCP server), in addition. When that server is not connected, say so and continue with Markdown alone.

## Flags

- `--url <url>` — audit mode explicitly (a bare URL argument is detected anyway).
- `--a11y-level A|AA|AAA` — the WCAG level to aim at. AA by default.
- `--ppt` / `--claudedesign` — extra output backends, in create mode.
- `--persona <name>` — add a custom reviewer to the vetting fan-out (as everywhere in the engine).
- `--plan` — present the composed harness and stop (as everywhere in the engine). A dry run.

## Examples

```
/rig:design a login screen for general users, with a spec and component specs
/rig:design wireframes and an accessibility plan for the settings page --a11y-level AA --ppt
/rig:design https://example.com/login                                      # audit a URL
/rig:design --url https://staging.example.com/signup --a11y-level AAA
/rig:design the dashboard --plan                                           # dry run
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
