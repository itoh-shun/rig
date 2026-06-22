# rig

**A LEGO-style harness composer for Claude Code.** Compose bricks — *facets, patterns, steps, recipes* — at invocation time into a task-specific agent harness for dev-flow orchestration (review, implement, PR, and more). Claude Code native (command + skill + agents); no heavy DSL engine.

> 🇯🇵 日本語版は [README.ja.md](./README.ja.md) を参照。

## Why rig

- **LEGO composition** — don't run one fixed workflow. Assemble the bricks you need per task: `PARSE → RESOLVE → COMPOSE → RUN`. The classic intake→design→implement→verify→review→pr→merge flow is just *one recipe*.
- **determinism-by-gate** — agent execution is non-deterministic (same input, varying output/quality). rig wraps quality-critical steps in **explicit acceptance gates** (`acceptance-gate`): the *path* varies, but the *output quality converges to the same bar every run*. Generation is non-deterministic; the result is deterministic in quality.
- **context-minimal** — the orchestrator dispatches all real work to subagents and only aggregates structured reports + makes gate decisions. The parent context stays clean.
- **run-continuity** — a mid-flow question won't quietly drop you out of the harness. Every RUN turn re-prints a one-line status header (`▸ rig | recipe … | step … | gate …`), re-anchors after any interruption, and marks step boundaries — so you can always *see* rig is still driving, and the orchestrator can't silently slide into direct, un-gated work. This even survives **context compaction**: a shipped `PreCompact` hook injects instructions to preserve the run-state, and `/rig:init` can mirror them into your CLAUDE.md "Compact Instructions".
- **native-first** — instruction facets are thin and delegate to existing skills/commands/agents. The engine routes and gates; it does not reimplement.
- **lightness by default** — interactive, size-aware, manual backend by default. Heavy machinery (autonomous loops, the Workflow backend, multi-stage fan-out) is opt-in.
- **grows with you** — a two-tier knowledge layer (methodology + AI quirks) is injected into every run and accumulates learnings, so the system gets better over time.

## Install

This repo ships a `.claude-plugin/marketplace.json`, so it installs via a marketplace. Plugin name: `rig`; marketplace name: `itoshun-local-plugins`.

### A) From GitHub (recommended)

```bash
/plugin marketplace add itoh-shun/rig
/plugin install rig@itoshun-local-plugins
```

### B) From a download (ZIP / clone)

```bash
# After extracting the ZIP or cloning, point at the folder:
/plugin marketplace add /path/to/rig
/plugin install rig@itoshun-local-plugins
```

### C) --plugin-dir (fast dev iteration)

```bash
cd /path/to/rig
claude --plugin-dir .
# reload after edits: /reload-plugins
```

### Invocation (namespaced)

- **Command**: `/rig:dev` — the dev-flow entry point you type, with args. e.g. `/rig:dev --plan --only review "current changes"`
- **Command**: `/rig:sales` — the sales-domain entry point: review a deal record across 5 perspectives. e.g. `/rig:sales ./deals/acme.md`
- **Command**: `/rig:talk` — a JARVIS-style conversational mode: speak naturally, it routes your intent to the right rig flow (dev/sales) and runs it. e.g. `/rig:talk just review my current changes`
- **Command**: `/rig:goal` — a goal-driven loop: state a high-level goal and it converts it into acceptance criteria, then loops (assess → next step → delegate to an existing flow → check) until the goal is met. e.g. `/rig:goal "fix the login bug with regression coverage, through review"`
- **Command**: `/rig:pr` — review an existing open PR: fetch it via GitHub MCP and run the 3-way (security/design/test) review to a structured verdict. e.g. `/rig:pr 1234 --adversarial`
- **Command**: `/rig:init` — scaffold a repo for rig: a manifest (`.claude/rig.md`), knowledge dirs, and a CLAUDE.md "Compact Instructions" section (so a rig run survives context compaction). Writes are always confirmed; idempotent.
- **Command**: `/rig:persona` — generate a reviewer persona from a description and save it per-product (project tier) or globally (`--user`), then inject it into a review with `--persona <name>`. e.g. `/rig:persona "a reviewer who understands 80s music"`
- **Command**: `/rig:knowledge` — generate domain knowledge as **LLM-wiki pages** (one canonical, cross-linked `[[page]]` per concept) from a description or `--auto` (repo scan), saved globally (default, shared across products) or as a project overlay. Personas reference pages via `inject: [[slug]]` instead of embedding facts — so knowledge is shared, not siloed as each agent's tacit knowledge. e.g. `/rig:knowledge --auto`
- **Skill**: `/rig:rig` — the engine; also **auto-invoked** when you say things like "implement…", "review my changes", "finish the PR".

## Quick start

```bash
/rig:dev --plan --only review "current changes"  # dry-run: show the composed harness, don't execute
/rig:dev --only review                           # run a 3-way parallel review (security/design/test)
/rig:dev --recipe release-flow --design "feature X"
/rig:dev --recipe hotfix --issue 1234            # shortest path for an urgent fix
```

## Recipes (shipped)

| recipe | what |
|---|---|
| `review-only` | 3-way parallel review (security/design/test) on current changes |
| `release-flow` | intake→design?→implement→verify→review?→pr→merge (size-aware; `?` steps are conditional) |
| `design-first` | design-heavy flow |
| `hotfix` | shortest path (intake→implement→verify→pr) |
| `adversarial-review` | adversarial review — eliminate AI tics, dead comments; enforce human readability (lazy-senior / cognitive-economist) |
| `goal-loop` | goal-driven loop — turn a high-level goal into acceptance criteria, then converge to it by delegating existing flows each round (acceptance-gate + autonomous-loop) |
| `pr-review` | review an existing open PR (fetched via GitHub MCP) with the 3-way (security/design/test) review + optional adversarial pass |

## Domain packs (beyond dev)

The engine ([`SKILL.md`](./skills/rig/SKILL.md)) is domain-agnostic. The same `PARSE → RESOLVE → COMPOSE → RUN` / context-minimal / acceptance-gate machinery runs non-dev domains by adding a *pack* (entry command + recipe + persona/instruction/output-contract facets) without touching the engine.

- **sales** — `/rig:sales <deal record>` runs the `deal-review` recipe: 5 perspectives (hearing / needs / proposal / closing / next-action) evaluated in parallel, converged via acceptance-gate, into an overall grade (S/A/B/C) + per-perspective verdict + concrete next actions + info gaps. Company-specifics (product strengths, ICP, pricing, competitors, winning patterns) live in [`facets/knowledge/sales-domain/`](./skills/rig/facets/knowledge/sales-domain/) — swap them and the pack transfers to another company. Input template: [`templates/deal-record.md`](./skills/rig/templates/deal-record.md).
- **talk** — `/rig:talk` is a conversational front-end (text in v1): speak in natural language and it normalizes intent, dynamically routes to the best `/rig:*` command, confirms before consequential actions, and replies in short spoken-style sentences. The engine is untouched — talk is just a thin natural-language layer in front of `PARSE`. Voice I/O (TTS/STT, user-selectable engines) is a future layer.
- **goal** — `/rig:goal "<goal>"` runs the `goal-loop` recipe: it converts the goal into a machine/criteria-checkable **acceptance contract**, then drives a closed loop — *assess gap → pick the smallest next step → delegate it to an existing flow (`/rig:dev`, …) → check against the contract* — converging until the goal is met (and stopping there — no over-build) or escalating after two no-progress rounds. It's the marriage of two existing patterns: `acceptance-gate` (the goal **is** the contract) + `autonomous-loop` (hands-free continuation under `--autonomous`). The engine is untouched — goal is a thin loop driver around `RUN`. Unlike `talk` (a one-shot natural-language router), `goal` keeps looping until the goal converges. GitHub-checkable criteria (PR open / CI green / issue closeable) are verified via the GitHub MCP, so "just declare the goal, get to a mergeable PR" runs as one flow.
- **pr-review** — `/rig:pr <number>` runs the `pr-review` recipe: it fetches an existing open PR via the GitHub MCP and runs the same 3-way (security/design/test) review (`+ --adversarial`) the dev flow uses, converged via acceptance-gate into a structured verdict — optionally posted back to the PR with `--comment` (write is always confirmed). Where `/rig:dev --only review` reviews *your working tree*, `/rig:pr` reviews *an existing PR*. The engine and reviewer bricks are shared, unchanged.

## Flags

| flag | meaning |
|---|---|
| `--recipe <name>` | use a shipped/user/project recipe by name |
| `--only <step>` / `--from <step>` | slice the execution range |
| `--design` / `--review` / `--tdd` | force the step ON (default is size-aware) |
| `--issue <id>` | feed an existing issue into intake |
| `--plan` | compose and present the harness, then stop (dry-run) |
| `--autonomous` | skip step gates (the capture gate is never lifted) |
| `--workflow` | use the ultracode Workflow execution backend (opt-in; heavy multi-stage only) |
| `--save-recipe <name>` | save the composed harness as a recipe (`--user` for the user tier) |
| `--capture` | persist run learnings to the knowledge layer without the confirm dialog (proposal + report are never skipped) |
| `--list` | list available bricks/recipes/flags and stop (no run) |
| `--validate` | doctor: check recipe→facet references, frontmatter schema, and §2 inventory drift; report and stop (no run) |
| `--adversarial` | add an adversarial-review step (AI-slop elimination + human readability) |
| `--persona <name>` | inject a named custom reviewer persona into the review fan-out (resolves project→user→shipped; pairs with `/rig:persona`) |

## How it works

The engine (`skills/rig/SKILL.md`) runs four phases:

1. **PARSE** — split the invocation into flags + free text; empty/ambiguous → interactive composition.
2. **RESOLVE** — load the project manifest (or generic defaults) + recipe + flag overrides; apply size-aware defaults.
3. **COMPOSE** — assemble each step's subagent prompt from facets in a fixed order (System=Persona / Knowledge head → Instruction → Output-Contract → Policy tail), inject the knowledge layer, bind native delegations. `--plan` stops here.
4. **RUN** — execute via Claude Code primitives. Real work is dispatched to subagents; the parent only aggregates + gates. `acceptance-gate` converges quality; the "stuck twice" guard escalates to you.

The full brick catalog (personas, policies, instructions, knowledge, output-contracts, patterns, recipes) lives in [`skills/rig/SKILL.md`](./skills/rig/SKILL.md).

## Customization (no fork needed)

- **Project manifest** — drop `<repo>/.claude/rig.md` to set build/lint/test commands, branch & CI strategy, reviewer, production-impact patterns, default recipe, etc. See [`skills/rig/manifests/_template.md`](./skills/rig/manifests/_template.md).
- **Recipes** — add `<repo>/.claude/rig/recipes/*.md` (project) or `~/.claude/rig/recipes/*.md` (user); `extends` a shipped recipe and override just the diff. Or `--save-recipe`.
- **Knowledge layer** — grow `~/.claude/rig/knowledge/{methodology,ai-quirks}/` (cross-project) and `<repo>/.claude/rig/knowledge/domain/` (per-project). Injected into every run.

## Docs

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — the engine (full PARSE/RESOLVE/COMPOSE/RUN spec, rationalization table, red flags)
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — discipline pressure scenarios
- [README.ja.md](./README.ja.md) — Japanese version

## License

[MIT](./LICENSE) © 2026 itoh-shun
