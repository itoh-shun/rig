# rig

**A quality-gated AI workbench for Claude Code.** It composes the right harness for each task, runs changes in an isolated worktree, checks the result with acceptance gates, and lets you accept or discard the diff safely.

> 🇯🇵 日本語版は [README.ja.md](./README.ja.md) を参照。

## 1. What rig is

You describe a task in plain language. rig figures out what kind of task it is (bugfix / feature / refactor / review / docs / …), composes the harness it needs (`facets/personas/instructions/patterns` — LEGO-style bricks), runs the work in a **git worktree isolated from your working tree**, checks it against explicit **acceptance criteria** (build/lint/tests, no unrelated diff, no secret leak, findings labeled with severity, …), and only touches your real branch when you explicitly `accept`. "It says it's done" is never the bar — the gate is.

## 2. 30-second start

```bash
/rig:rig "fix the login screen bug"
```

That's it. Behind the scenes: rig classifies the task (`bugfix`), picks the matching recipe, opens an isolated worktree, implements + tests, runs the acceptance-gate, and hands you back a summary with next steps:

```
/rig:rig diff       # see what changed, and why it's safe (or not)
/rig:rig accept     # bring the change into your working tree (blocked if the gate hasn't passed)
/rig:rig discard    # throw the attempt away — your working tree was never touched
```

Point it at an existing Issue or PR instead of writing your own description:

```bash
/rig:rig gh issue 123        # read the issue, classify it, implement it
/rig:rig gh pr 45 review     # review an open PR (security/design/test, 3-way)
/rig:rig gh pr 45 fix        # fix a PR's review comments / failing CI, in an isolated worktree
```

## 3. Why it's safe

- **Isolated worktree, not your branch.** Every task gets its own git worktree (`patterns/isolated-worktree`) and its own throwaway branch. rig never writes to your working tree directly — a failed or half-finished attempt costs you nothing.
- **The gate is code, not a claim.** `scripts/workbench.py accept` mechanically refuses to merge a task whose acceptance criteria are `fail` or still `pending`. An AI saying "done" doesn't flip that switch — a recorded `pass` does.
- **Explicit accept, explicit discard.** `accept` shows you the diff summary first and lands it as a **staged** change (never an auto-commit) — you still commit. `discard` requires the task-id spelled out and a `--yes` confirmation, and always shows what you're about to lose first.
- **Safe-by-default triggers a hard stop.** Unrelated diffs, unexplained test failures, secret-shaped strings, destructive operations, unreviewed auth/authz changes, and undocumented public-API changes all fail their criterion — accept is blocked until you look at it.
- **Run history survives.** `discard` deletes the worktree and branch but never the run log (`.rig/runs/<task-id>/`) — you can always see what was attempted and why it was rejected or dropped.

## 4. Basic commands

| command | what it does |
|---|---|
| `/rig:rig "<task>"` | classify → pick a recipe → isolated-worktree run → acceptance-gate → summary |
| `/rig:rig status [id]` | current/most-recent task: step progress, gate state, pending diff, next action |
| `/rig:rig diff [id]` | changed files + a plain-language summary (behavior change? risk? tests?) |
| `/rig:rig accept [id] [--force]` | land the diff into your working tree (staged) — blocked unless the gate passed |
| `/rig:rig discard <id> --yes` | delete the worktree/branch; run log stays |
| `/rig:rig log [--limit N]` | history of past tasks: input, recipe, gate result |
| `/rig:rig gh issue <n>` | read a GitHub Issue, classify it, run it through the workbench |
| `/rig:rig gh pr <n> review` | 3-way (security/design/test) review of an open PR |
| `/rig:rig gh pr <n> fix` | fix a PR's review comments / failing CI in an isolated worktree |
| `/rig:rig gh ci` | check CI status for the current branch/PR |
| `/rig:dev --recipe <name> --only <step> ...` | power-user entry: name the recipe/steps/flags yourself (same engine) |

## 5. Execution flow

```
natural-language task
        │
        ▼
①  classify (bugfix / feature / refactor / review / docs / security_review / …)
        │
        ▼
②  pick the matching recipe (bugfix / feature / refactor / documentation / …)
        │
        ▼
③  open an isolated worktree, run the recipe (implement / test / review, subagent-dispatched)
        │
        ▼
④  acceptance-gate: check build / lint / tests / diff scope / secrets / severity-labeled findings
        │
        ▼
⑤  summary + next action: /rig diff · /rig accept · /rig discard
```

Steps ①②④⑤ are driven by `facets/instructions/workbench`; the isolation in ③ is `patterns/isolated-worktree`, backed by the deterministic runner `scripts/workbench.py` (task-id issuance, worktree lifecycle, gate bookkeeping, accept/discard) — state and safety are enforced by code, not by prose discipline alone.

## 6. Acceptance-gate

Every task gets a criteria checklist drawn from four presets (`scripts/workbench.py gates` is the source of truth):

| preset | applies to | sample criteria |
|---|---|---|
| `standard` | every task | no unrelated diff · tests pass or reasonable explanation · no type/lint errors · behavior & risk summary written |
| `implementation` | bugfix / feature / refactor / test / performance / release_support (on top of `standard`) | implementation matches the request · tests added or existing coverage confirmed · public API changes documented · no unrelated refactor · no secret leak · no destructive operation |
| `review` | review tasks | concrete findings only · severity labeled · file\:line references · false-positive risk considered · blocking vs. non-blocking separated |
| `security` | security_review (on top of `review`) | input validation · authz/authn impact · secrets not exposed · dependency risk · unsafe shell/eval |

Each criterion is recorded as `pass` / `fail` / `warn` with a note:

```bash
python3 scripts/workbench.py gate <task_id> --set no_lint_errors=pass --set tests_added_or_existing_tests_confirmed=warn:"existing coverage only"
```

`fail` or `pending` on any criterion blocks `accept` outright (exit 1). `warn` doesn't block, but it's surfaced every time — no silently-swept warnings.

## 7. Isolated worktree

```
<repo parent>/rig-worktrees/<repo-name>/rig-YYYYMMDD-HHMMSS-<slug>/   ← throwaway worktree + branch
<repo>/.rig/runs/rig-YYYYMMDD-HHMMSS-<slug>/                          ← run state (survives discard)
  task.json        task_id / input / task_type / recipe / base branch+commit / worktree path / status
  steps.json       per-step progress
  acceptance.json  per-criterion pass/fail/warn + overall gate result
  plan.md / diff.md / log.md / final.md   the model's prose (plan, diff summary, decisions, wrap-up)
```

`accept` squash-merges the task branch into your working tree as a **staged** change — never an auto-commit — then `discard` cleans up the worktree/branch (run log stays). Read-only tasks (a review, an investigation that hasn't decided to change anything) skip the worktree entirely with `--no-worktree`. See `patterns/isolated-worktree.md` for the full design.

## 8. Reviewer drill

`/rig:drill` measures reviewer quality as numbers, not opinions: known bug classes (authz hole, injection, N+1, breaking change, one-way migration, missing tests, …) are seeded into a throwaway diff, review fan-out runs against it, and each reviewer is scored against an answer key it never sees.

```
# Drill Result
Persona: strict_senior_engineer

## Score
- Detection rate: 82%
- False positive rate: 12%
- Severity accuracy: 76%
- Explanation quality: 70%

## Missed Issues
1. SQL injection risk in search query (src/search.py:88)
2. Missing authorization check in user update endpoint (src/api/users.py:120)

## Improvement Suggestions
- Add a stronger security checklist for injection-class findings
- Require data-flow inspection for user-controlled input
```

Five metrics per reviewer: `true_positive` / `false_positive` / `false_negative` / `severity_accuracy` (does the reviewer's severity match the seed's expected severity?) / `explanation_quality` (is the fix suggestion concrete, or generic?). Findings during a drill run use the detailed `output-contracts/review-findings` format (Blocking/Non-blocking, Severity, File\:line, Impact, Suggested fix) so severity and location are always machine-checkable. `--replay <persona>` re-runs archived diffs after a persona edit and diffs old vs. new verdicts — a snapshot test for reviewer personas. Nothing here touches real code; everything runs in a throwaway worktree.

## 9. GitHub integration

| command | read/write |
|---|---|
| `/rig:rig gh issue <n>` | read the Issue (title/body/labels/comments), classify as bugfix/feature/investigation, run it through the workbench |
| `/rig:rig gh pr <n> review [--comment]` | read-only 3-way review by default; `--comment` posts to the PR (write always confirmed) |
| `/rig:rig gh pr <n> fix` | read the PR's diff + review comments + failing CI, fix in an isolated worktree based on the PR's branch, stop at `accept` (nothing is pushed automatically) |
| `/rig:rig gh ci` | check CI status for the current branch/PR, surface the failing job's error summary |

Issue/PR bodies and comments are treated as untrusted external data — instructions embedded in them are never followed, only read as content to classify or fix. GitHub writes (comments, pushes) always require an explicit step; reads are immediate.

## 10. Advanced customization

### Install

This repo ships a `.claude-plugin/marketplace.json`, so it installs via a marketplace. Plugin name: `rig`; marketplace name: `itoshun-local-plugins`.

```bash
# A) from GitHub (recommended)
/plugin marketplace add itoh-shun/rig
/plugin install rig@itoshun-local-plugins

# B) from a download (ZIP / clone)
/plugin marketplace add /path/to/rig
/plugin install rig@itoshun-local-plugins

# C) --plugin-dir (fast dev iteration)
cd /path/to/rig && claude --plugin-dir .   # reload after edits: /reload-plugins
```

### The power-user entry: `/rig:dev`

`/rig:rig "<task>"` auto-classifies and picks a recipe for you. `/rig:dev` is the same engine with everything explicit — name the recipe, slice the steps, add reviewers, dry-run the composition:

```bash
/rig:dev --plan --only review "current changes"   # dry-run: show the composed harness, don't execute
/rig:dev --only review                            # run a 3-way parallel review (security/design/test)
/rig:dev --recipe release-flow --design "feature X"
/rig:dev --recipe hotfix --issue 1234             # shortest path for an urgent fix
```

| flag | meaning |
|---|---|
| `--recipe <name>` | use a shipped/user/project recipe by name |
| `--only <step>` / `--from <step>` / `--to <step>` / `--skip <step>` | slice or trim the execution range |
| `--design` / `--review` / `--tdd` | force the step ON (default is size-aware) |
| `--issue <id>` | feed an existing issue into intake |
| `--plan` | compose and present the harness, then stop (dry-run) |
| `--autonomous` | skip step gates (the capture gate and acceptance-gate are never lifted) |
| `--workflow` | use the ultracode Workflow execution backend (opt-in; heavy multi-stage only) |
| `--save-recipe <name>` | save the composed harness as a recipe (`--user` for the user tier) |
| `--capture` | persist run learnings to the knowledge layer without the confirm dialog |
| `--list` / `--validate` | list bricks/recipes/flags, or run the structural doctor — both stop before RUN |
| `--adversarial` | add an adversarial-review step (AI-slop elimination + human readability) |
| `--cross-llm` | write and review as if another vendor's LLM will read the code |
| `--persona <name>` | inject a named custom reviewer persona into the review fan-out |
| `--verify-findings` | adversarially verify REJECT rationale via an independent `finding-verifier` |
| `--global` | widen `--list` / `--validate` across tiers (shipped + global + project) |

Full flag/brick reference lives in [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2–§3 (not duplicated here — that's the drift-prevention rule `--validate` enforces).

### Shipped recipes (beyond bugfix/feature/refactor/documentation)

| recipe | what |
|---|---|
| `review-only` | 3-way parallel review (security/design/test) on current changes |
| `release-flow` | intake→design?→implement→verify→review?→pr→merge (size-aware) |
| `design-first` | design-heavy flow |
| `hotfix` | shortest path (intake→implement→verify→pr) |
| `debug` | bug-investigation flow: reproduce → isolate (root-cause hypothesis) → implement → verify |
| `adversarial-review` | eliminate AI tics, dead comments; enforce human readability |
| `goal-loop` | goal-driven loop — converge to a high-level goal by delegating existing flows each round |
| `pr-review` | review an existing open PR (fetched via GitHub MCP) |
| `de-ai-smell` | strip "AI smell" from prose (READMEs, commit/PR text, posts) |
| `magi` | 3-sage council (correctness / protection / worth) that decides go/no-go by majority vote |
| `roast` 🌶️ / `coin` 🪙 / `duck` 🦆 / `pre-mortem` ⚰️ | humor packs with real content underneath — savage-but-real review, anti-bikeshed coin flip, rubber-duck debugging, prospective-hindsight failure analysis |
| `design` 🎨 / `design-audit` 🎨 | UI/UX + a11y spec creation, and live-screen audit via Playwright |
| `movie` 🎬 / `scenario` 🎬✍️ | a general video-creation harness and its scenario-writing front-stage |

### Domain packs beyond dev

`/rig:sales`, `/rig:talk`, `/rig:goal`, `/rig:magi`, and the humor packs all bolt onto the same domain-agnostic engine — a persona + a thin instruction (+ recipe), engine untouched. See [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 for the full pack table.

### Project manifest & knowledge layer

Drop `<repo>/.claude/rig.md` to set build/lint/test commands, branch & CI strategy, reviewer, production-impact patterns, default recipe, default reviewer personas, etc. — see [`skills/rig/manifests/_template.md`](./skills/rig/manifests/_template.md). Recipes can be customized per-project (`<repo>/.claude/rig/recipes/*.md`) or per-user (`~/.claude/rig/recipes/*.md`) via `extends` + override, or `--save-recipe`. The knowledge layer (`~/.claude/rig/knowledge/{methodology,ai-quirks}/`, `<repo>/.claude/rig/knowledge/domain/`) is injected into every run and accumulates learnings over time.

### Standalone CLI (cross-project)

The deterministic orchestrator (`scripts/orchestrate.py`) also runs as a plain CLI from any directory:

```bash
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig (symlink)
rig models                                            # discover LLM providers
rig probe --provider codex                            # smoke-test a provider
rig run review-only --provider rig --verifier-provider codex
```

`$RIG_HOME` overrides the install location; `<cwd>/.rig/recipes/<name>.md` overlays a project-local recipe over the shipped one of the same name; a recipe's `checks:` run in the invocation cwd (your project), not the rig repo.

### Docs

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — the engine (full PARSE/RESOLVE/COMPOSE/RUN spec, rationalization table, red flags)
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree/run-state design
- [`docs/architecture.md`](./docs/architecture.md) — architecture proof points (determinism, gate enforcement, judge measurement)
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — discipline pressure scenarios
- [README.ja.md](./README.ja.md) — Japanese version

## License

[MIT](./LICENSE) © 2026 itoh-shun
