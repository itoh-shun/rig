# rig

**A LEGO-style harness composer for Claude Code.** Compose bricks — *facets, patterns, steps, recipes* — at invocation time into a task-specific agent harness for dev-flow orchestration (review, implement, PR, and more). Claude Code native (command + skill + agents); no heavy DSL engine.

> 🇯🇵 日本語版は [README.ja.md](./README.ja.md) を参照。

## Why rig

- **LEGO composition** — don't run one fixed workflow. Assemble the bricks you need per task: `PARSE → RESOLVE → COMPOSE → RUN`. The classic intake→design→implement→verify→review→pr→merge flow is just *one recipe*.
- **determinism-by-gate** — agent execution is non-deterministic (same input, varying output/quality). rig wraps quality-critical steps in **explicit acceptance gates** (`acceptance-gate`): the *path* varies, but the *output quality converges to the same bar every run*. Generation is non-deterministic; the result is deterministic in quality.
- **context-minimal** — the orchestrator dispatches all real work to subagents and only aggregates structured reports + makes gate decisions. The parent context stays clean.
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

- **Command**: `/rig:dev` — the entry point you type, with args. e.g. `/rig:dev --plan --only review "current changes"`
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
| `--adversarial` | add an adversarial-review step (AI-slop elimination + human readability) |

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
