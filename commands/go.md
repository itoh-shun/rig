---
description: "rig — the unified entry point. Give it a task in plain language and it drives the whole thing: classify, choose a recipe, implement and review in an isolated worktree, judge at the acceptance gate, and report. The status, diff, accept, discard, log, board, cockpit, stats, confidence, review, gc, audit, scan-secrets, scan-injection, digest, context, stream-checks, stale-refs, scan-destructive, scan-anchors, instincts, and gh subcommands operate on that state. Run several tasks at once and `board` or `cockpit` still holds the whole picture on one screen."
argument-hint: "\"<task in plain language>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | board [--all] | cockpit | stats [--recipe R] [--verifier P] [--last Nd] | confidence [id] | review <id> --set p=v [--body p=@path] | gc [--older-than Nd] [--dry-run] | audit [--limit N] [--action A] [--since YYYY-MM-DD] | scan-secrets [paths…|--diff id] | scan-injection [paths…|--diff id] | digest [--period week|month] [--out PATH] | context [--since-days N] | stream-checks [id] [--watch --interval N --max-passes M] | stale-refs [paths…] | scan-destructive [paths…|--diff id] | scan-anchors [paths…|--diff id] | instincts [--add TEXT --evidence E --confidence C] [--mute ID|--expire ID|--decay|--inject-preview] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# rig — the unified entry point (the workbench)

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md strictly** — PARSE → RESOLVE → COMPOSE → RUN, context-minimal, facet ordering, the recipe schema, knowledge-layer injection. On top of that, this command branches two ways on the first word of `$ARGUMENTS`.

> **The first time only**: without the `rig-wb` CLI, a provider outside Claude Code (Codex, Cursor, a plain terminal) cannot reach the same workbench. **`/rig:setup`** installs it through pipx, uv, or pip (`--yes` skips the prompt, `--check` only detects). If everything you do happens inside Claude Code you do not need it — `scripts/*.py` are called directly and it works without `rig-wb`.

```
$ARGUMENTS
```

## The two branches

A plain-language task accepts `--runtime auto|native|orca`. `auto` uses the Orca backend only when it observes both an active Orca session and a structured CLI that answers; otherwise it says why and falls back to native. An explicit `orca` never downgrades silently. The choice is independent of the provider, and `import` takes the same flag.

### 1. A subcommand (when the first word matches)

| First word | Delegates to |
|---|---|
| `compose-options --type <task_type> [--diff <n>] [--json]` | `facets/instructions/workbench-ops` — the candidates, the recommendation, and the grounds for each, fetched deterministically for a conversation |
| `status [<task_id>]` | `facets/instructions/workbench-ops` — show run state |
| `diff [<task_id>]` | `facets/instructions/workbench-ops` — show the diff |
| `accept [<task_id>] [--force]` | `facets/instructions/workbench-ops` — land it in the main working tree |
| `discard <task_id> [--yes]` | `facets/instructions/workbench-ops` — destroy the worktree and branch |
| `log [--limit N] [--json]` | `facets/instructions/workbench-ops` — list past runs |
| `board [--all]` | `facets/instructions/workbench-ops` — **the dashboard of every task**, the one place to look when several are in flight |
| `cockpit` | `facets/instructions/workbench-ops` — **Mission Control: board, gate, drill, cost, and audit on one screen.** Read-only; it points at the next action and never accepts or discards anything itself |
| `stats [--recipe R] [--verifier P] [--last Nd]` | `facets/instructions/workbench-ops` — aggregate past runs, and detect a reviewer who is rubber-stamping |
| `confidence [<task_id>]` | `facets/instructions/workbench-ops` — present drill's measured detection rates per reviewer as **supporting information**. With a `<task_id>` it records them in `acceptance.json` under `reviewer_confidence`. Below the threshold it only proposes adding a reviewer, never adds one, and a persona with no drill run stays "not measured" rather than having a confidence invented for it |
| `review <task_id> --set <persona>=<verdict> [--body <persona>=@<path>]` | `facets/instructions/workbench-ops` — record a per-persona verdict for a review task. `--body` is optional and persists that reviewer's text to `.rig/runs/<task_id>/reviews/<persona>.md`, keeping the `file:line` evidence anchors a verdict label throws away |
| `gc [--older-than <N>d] [--dry-run]` | `facets/instructions/workbench-ops` — age out visual-verification artefacts (`.rig/runs/*/visual/`, `.rig/visual/adhoc/*`). 14 days by default; `--dry-run` lists candidates only |
| `audit [--limit N] [--action A] [--since YYYY-MM-DD]` | `facets/instructions/workbench-ops` — list and filter the permanent audit log `.rig/audit.jsonl`, where `accept --force` and its kin are recorded |
| `scan-secrets [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops` — the deterministic secret scan, the same implementation as the machine sensor behind the `no_secret_leak` criterion. `--diff` scans only a task worktree's diff, and excerpts are always masked |
| `scan-injection [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops` — the deterministic prompt-injection marker scan, the same implementation as the sensor behind `no_injection_markers`. Invisible and bidi Unicode are fail-grade; instruction-override phrasing is warning-grade. With no arguments it scans the repository's prose surface (`.claude/rig.md`, knowledge, personas, `.rig/recipes/*.md`); `--diff` scans a task worktree's diff plus that prose surface |
| `digest [--period week\|month] [--out PATH]` | `facets/instructions/workbench-ops` — a rolling digest of `.rig/` telemetry (runs, gate outcomes, force-accepts, suspected rubber stamps, drill detection rates) as Markdown. `week` is the default, meaning the last seven days |
| `context [--since-days N]` | `facets/instructions/workbench-ops` — measure context-minimal. Every byte rig printed to the parent session is recorded per invocation in `.rig/context.jsonl` and aggregated by command: **the only context consumption rig can observe.** The whole session's context, the conversation, and files the parent read itself are outside it, and the report says so. All time by default; `--since-days N` narrows it |
| `stream-checks [<task_id>] [--watch --interval N --max-passes M]` | `facets/instructions/workbench-ops` — light streaming checks during implementation. Runs the secret, injection, and destructive sensors against the task worktree there and then and shows the findings as hints. Never blocks the gate; always exits 0 |
| `stale-refs [paths…]` | `facets/instructions/workbench-ops` — detect rot in the manifest and knowledge layer. WARNs on backtick-quoted relative paths that no longer exist. Exits 0 |
| `scan-destructive [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops` — the deterministic destructive-command scan, the same implementation as the sensor behind `no_destructive_operation`, in two grades: fail and warning |
| `scan-anchors [paths…] [--diff <task_id>]` | `facets/instructions/workbench-ops` — the deterministic evidence-anchor check: does each `file:line` in a reviewer's text point at a line that exists? The same implementation as the sensor behind the **opt-in** `evidence_anchors_resolve` criterion, which is **not** in the default presets. `--diff` resolves a task's `reviews/*.md` against the worktree first and then the base commit |
| `instincts [--add TEXT --evidence E --confidence C] [--mute ID\|--expire ID\|--decay\|--inject-preview]` | `facets/instructions/workbench-ops` — the cross-session instinct layer: record an unverified pattern with a confidence, decay it, and preview what would be injected next session |
| `gates` | `facets/instructions/workbench-ops` — print the canonical acceptance-criteria presets. A project's own criteria go in `.rig/gates.json` and are **additive only** |
| `receipt <task_id> [--verify] [--markdown]` | `facets/instructions/workbench-ops` — the **assurance receipt**: a projection of what this task achieved. It judges nothing; it copies from the records of what was judged. `--verify` checks whether the receipt is still current |
| `import <url\|path> [--head <ref>]` | `facets/instructions/workbench-ops` — put a change made elsewhere through rig's gate, without mixing what the producer claimed with what rig verified |
| `contract <task_id>` | `facets/instructions/workbench-ops` — the BYOO contract: names the head rig verified and the receipt that answered for it. The exit code carries pending or acceptable |
| `intent-derive <contract> --against <json> --floor\|--target [--json]` | `facets/instructions/workbench-ops` — derive a workflow floor or an assurance target from an intent contract's **declared** requirements. An inferred requirement never creates a floor |
| `assurance-target <task_id> <target> [--json]` | `facets/instructions/workbench-ops` — check an assurance target against the receipt. `unobservable` is never folded into `unmet`: "not measured" and "measured and short" are different answers |
| `knowledge-candidate <candidate> [--json]` | `facets/instructions/workbench-ops` — judge only whether a submitted knowledge candidate is explicitly supported by cited records. Evidence that cannot be read is `unobservable`; evidence that reads and does not support is `unsupported` |
| `change-graph <graph> [--json]` | `facets/instructions/workbench-ops` — judge only whether a cross-repository change graph the caller wrote admits an execution order satisfying its declared dependencies and compatibility constraints. It does not discover, generate, or execute the graph |
| `anomaly-trigger <event> [--json]` | `facets/instructions/workbench-ops` — judge only whether an anomaly event submitted by an external source declares grounds to start an investigation and whether cited records explicitly support them. It does not detect or confirm the anomaly itself |
| `assurance-derive <target> --requires <map> --against <json> [--json]` | `facets/instructions/workbench-ops` — derive the workflow floor a target requires, from a declared axis-to-step mapping. An axis-value the mapping does not cover is refused |
| `synthesise <workflow> --against <json> [--floor <json>] [--json]` | `facets/instructions/workbench-ops` — restore the floor into a proposed workflow and report what was restored. The floor is assembled by the caller and never read from the thing under inspection |
| `dev-loop <cycles> [--limits <json>] [--receipt <json>] [--json]` | `facets/instructions/workbench-ops` — the stopping decision and handoff for a development loop. It names the reason to stop, and never reads an absence of progress as progress |
| `route-team <evidence> --constraints <json> [--json]` | `facets/instructions/workbench-ops` — decide who takes it, from evidence. The constraints are assembled by the caller, and saying nothing is never turned into requiring nothing |
| `budget-plan <options> --budget <json> [--json]` | `facets/instructions/workbench-ops` — make assurance cheaper to produce, never make the assurance cheaper. When the budget runs out it refuses rather than offering an option |
| `provenance <graph> <node> [--direction both\|back\|forward] [--json]` | `facets/instructions/workbench-ops` — walk a node's chain in both directions, never mixing the confirmed with the inferred, and answering "not queried" for a kind it cannot query |
| `expected-outcome <expected> --observed <file> --as-of <ts> [--task <id>] [--json]` | `facets/instructions/workbench-ops` — check a declared expected outcome against what production observed. The observing side cannot declare the criteria, `unmeasured` and `inconclusive` are never successes, and nothing is final until the window closes. An objective declares `baseline`, `target`, and a `direction` of improvement; a guardrail declares exactly one boundary named for the side it protects — `at_most` (a ceiling) or `at_least` (a floor) — and therefore has no `direction` |
| `effectiveness --query <json> [--json]` | `facets/instructions/workbench-ops` — derive workflow measures from run records that exist, plus only the failure patterns whose thresholds and late steps the caller defined. It never turns "not measured" into zero, and it does not generate, evaluate, or promote candidates |
| `gh issue <n>` | `facets/instructions/gh-flow` — read an issue, classify it, and take it to the workbench |
| `gh pr <n> review [--adversarial] [--comment]` | `facets/instructions/gh-flow` — the equivalent of `/rig:pr`; delegates to the existing `recipes/pr-review` |
| `gh pr <n> fix` | `facets/instructions/gh-flow` — fix a PR's review comments in an isolated worktree |
| `gh ci` | `facets/instructions/gh-flow` — check CI state |

### 2. A task in plain language (when nothing above matches)

Follow `facets/instructions/workbench`: (0) check the host's prerequisites (`rig-wb hostcheck`), (1) classify the task type, (2) resolve a recipe through the capability authority of `rig-wb wb route --type <type> --json`, (3) RUN in an isolated worktree per `patterns/isolated-worktree`, (4) judge at the acceptance gate through `scripts/workbench.py gate`, and (5) report. Route is read-only: it never installs, reaches the network, or approves trust. The user does not have to name a recipe or a step — when they want to, that is what `/rig:dev --recipe <name> ...` is for.

**Step 0 does not block.** Do not pass `--strict`; even at exit 3, the task continues. Report the missing prerequisites one line each and move to step 1 (`facets/instructions/workbench` §0 is the source of truth). It includes `gh auth status`, so it takes a few seconds every time. It runs **every time, not once per session** — rig holds no state that would guarantee "once", and writing that it does would be a promise with no implementation behind it.

## When to use this and when to use `/rig:dev`

- **`/rig:go "<task>"`** (this command) — when you want plain language to be enough. Classification, recipe selection, worktree isolation, and the gate all happen for you.
- **`/rig:dev --recipe <name> --only <step> ...`** — when you want to combine recipe, step, and flags yourself. Every PARSE flag is available.

The engine is the same, one SKILL.md. The only difference is that this command defaults to the workbench route: an isolated worktree, persisted state, and a machine gate.

## Running several tasks at once, without opening more terminals

Instead of typing `/rig:go "<task>"` one at a time, **stack them and run them in parallel** with `/rig:queue` — no extra terminal, a parallel engine of headless processes:

```
/rig:queue add "fix the bug on the login screen"
/rig:queue add "add search to the inventory list"
/rig:queue add "make the README clearer"
/rig:queue go --provider rig --max-parallel 3
```

`go --provider rig` dispatches each item through `/rig:go "<task>"`, so **every task is isolated in its own worktree automatically** and parallel processes never fight over files. The side that stacked them does not accept (the queue's verifier only judges whether the gate settled). Afterwards:

```
/rig:go board          # where every task has got to, in one command
/rig:go diff <id>      # check one diff
/rig:go accept <id>    # land one, without colliding with the others
```

The problem of opening several terminals and forgetting which was doing what goes away because `board` is the single source of truth — wherever execution happened, in a headless process or in this session, the state always collects in `.rig/runs/`.

## Examples

```
/rig:go "fix the bug on the login screen"
/rig:go "read this issue and implement it"      # if it is ambiguous, confirm the issue number once with gh issue <n>
/rig:go "review this PR, strictly"              # confirm the PR number, then the equivalent of gh pr <n> review --adversarial
/rig:go status
/rig:go diff
/rig:go accept
/rig:go discard rig-20260704-153012-login-fix --yes
/rig:go log --limit 5
/rig:go gc --dry-run
/rig:go audit --limit 10
/rig:go scan-secrets --diff rig-20260704-153012-login-fix
/rig:go scan-injection --diff rig-20260704-153012-login-fix
/rig:go digest --period week
/rig:go confidence rig-20260704-153012-login-fix
/rig:go context --since-days 7
/rig:go gh issue 123
/rig:go gh pr 45 review
/rig:go gh pr 45 fix
/rig:go gh ci
```

## Safety, by default

- The AI's changes **never touch the main working tree until they are accepted** (`patterns/isolated-worktree`).
- **While the acceptance gate is failed or pending, the code refuses to accept** (`scripts/workbench.py accept`). Saying "it is done" is not treated as done.
- **discard needs three things**: an explicit task id, the list of changed files, and `--yes`. The run log survives it.
- Writing to GitHub — opening a PR, posting a comment, pushing — always goes through an explicit action. Reads answer immediately.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | task: <task_id> | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending [(try N/K)]|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
