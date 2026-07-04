# rig

**A quality-gated AI workbench for Claude Code.** It composes the right harness for each task, runs changes in an isolated worktree, checks the result with acceptance gates, and lets you accept or discard the diff safely.

> 🇯🇵 日本語版は [README.ja.md](./README.ja.md) を参照。

## 1. What rig is

You describe a task in plain language. rig figures out what kind of task it is (bugfix / feature / refactor / review / docs / …), composes the harness it needs (`facets/personas/instructions/patterns` — LEGO-style bricks), runs the work in a **git worktree isolated from your working tree**, checks it against explicit **acceptance criteria** (build/lint/tests, no unrelated diff, no secret leak, findings labeled with severity, …), and only touches your real branch when you explicitly `accept`. "It says it's done" is never the bar — the gate is.

rig's value isn't running AI. It's structurally removing the dangerous parts of letting AI work unsupervised: isolation, verification, measurement, recording, and controlled hand-off.

## 2. 30-second start

```bash
/rig:rig "fix the login bug"
/rig:rig "review this PR strictly"
/rig:rig "check my current changes are safe"
```

That's the whole surface for a first run. Behind the scenes: rig classifies the task, picks the matching recipe, opens an isolated worktree (skipped for read-only tasks like reviews), implements + tests, runs the acceptance-gate, and hands you back a summary with next steps:

```
/rig:rig diff       # see what changed, and why it's safe (or not)
/rig:rig accept     # bring the change into your working tree (blocked if the gate hasn't passed)
/rig:rig discard    # throw the attempt away — your working tree was never touched
```

## 3. Why it's safe

- **Isolated worktree, not your branch.** Every task gets its own git worktree (`patterns/isolated-worktree`) and its own throwaway branch. rig never writes to your working tree directly — a failed or half-finished attempt costs you nothing.
- **The gate is code, not a claim.** `scripts/workbench.py accept` mechanically refuses to land a task whose acceptance criteria are `failed`/`pending`. An AI saying "done" doesn't flip that switch — a recorded `passed` does.
- **Verifiers are read-only, structurally.** Reviewer/verifier subagents run with restricted tool access (`claude --allowedTools Read,Grep,Glob`, `codex --sandbox read-only`) — they inspect and report, they cannot write files, commit, or run destructive commands. This is enforced at the process level, not by asking nicely (`scripts/orchestrate.py probe`/`selftest` verify it).
- **Explicit accept, explicit discard.** `accept` first prints an `accept_requirements` checklist — `worktree_exists`, `base_branch_recorded`, and `diff_summary_generated` are **structural prerequisites that even `--force` cannot bypass**. It then lands the change as a **staged** diff (never an auto-commit) — you still commit. `discard` requires the task-id spelled out and a `--yes` confirmation, and always shows what you're about to lose first.
- **Safe-by-default triggers a hard stop.** Unrelated diffs, unexplained test failures, secret-shaped strings, destructive operations, unreviewed auth/authz changes, and undocumented public-API changes all fail their criterion — accept is blocked until you look at it.
- **Run history survives.** `discard` deletes the worktree and branch but never the run log (`.rig/runs/<task-id>/`) — you can always see what was attempted and why it was rejected or dropped.

## 4. Basic flow

```
natural-language task
        │
        ▼
①  classify (bugfix / feature / refactor / review / docs / security_review / …)
        │
        ▼
②  pick the matching recipe + show why (a one-line routing banner, not a guess)
        │
        ▼
③  open an isolated worktree, run the recipe (implement / test / review, subagent-dispatched)
        │
        ▼
④  acceptance-gate: check intent / diff scope / risk / tests / secrets / severity-labeled findings
        │
        ▼
⑤  summary + next action: /rig diff · /rig accept · /rig discard
```

| command | what it does |
|---|---|
| `/rig:rig "<task>"` | classify → pick a recipe → isolated-worktree run → acceptance-gate → summary |
| `/rig:rig status [id]` | current/most-recent task: step checklist, gate checklist, pending diff, next action |
| `/rig:rig diff [id]` | changed files + Summary/Risk/Tests/Unrelated-diff/Recommended |
| `/rig:rig accept [id] [--force]` | land the diff into your working tree (staged) — blocked unless the gate passed |
| `/rig:rig discard <id> --yes` | delete the worktree/branch; run log stays |
| `/rig:rig log [--limit N]` | history of past tasks: input, recipe, gate result |
| `/rig:rig board [--all]` | **single dashboard of every active task** — running multiple tasks in parallel? this is the one place to check, no matter how many terminals or `/rig:queue` items are behind them |
| `/rig:rig stats` | aggregate past runs: acceptance rate, gate outcomes, reviewer rubber-stamp detection |
| `/rig:rig gh issue/pr/ci …` | GitHub Issue/PR/CI as input — see §13 |
| `/rig:dev --recipe <name> --only <step> ...` | power-user entry: name the recipe/steps/flags yourself (same engine) — see §11 |

Every `new` task starts with a **routing banner** so you never wonder why rig picked what it picked:

```
▸ rig
task: fix the login bug
detected: bugfix
recipe: bugfix — matched "bug"/"fix"
mode: isolated worktree
gate: standard + bugfix
```

## 5. Isolated worktree

```
<repo parent>/rig-worktrees/<repo-name>/rig-YYYYMMDD-HHMMSS-<slug>/   ← throwaway worktree + branch
<repo>/.rig/runs/rig-YYYYMMDD-HHMMSS-<slug>/                          ← run state (survives discard)
  task.json        task_id / input / task_type / recipe / base branch+commit / worktree path / status
  steps.json       per-step progress
  acceptance.json  {task_id, task_type, presets, status, checks: [{name, status, detail}]}
  review.json      per-reviewer-persona verdicts for review tasks (feeds /rig:rig stats)
  plan.md / diff.md / log.md / final.md   the model's prose (plan, diff summary, decisions, wrap-up)
```

Read-only tasks (a review, an investigation that hasn't decided to change anything) skip the worktree entirely with `--no-worktree`. See [`patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) for the full design.

### Running several tasks at once, without losing track

Because isolation is per-task, running multiple tasks concurrently is safe by construction — each gets its own worktree and branch, so they can't step on each other. To actually run them in parallel (instead of typing `/rig:rig "<task>"` one at a time), queue them and go:

```bash
/rig:queue add "fix the login bug"
/rig:queue add "add search to the inventory list"
/rig:queue add "make the README clearer"
/rig:queue go --provider rig --max-parallel 3   # dispatches 3 independent headless processes
```

`--provider rig` routes each queued item through `/rig:rig "<task>"`, so each one is isolated the same way a task you typed directly would be — no risk of the parallel processes fighting over the same files. Queue's own verifier only confirms the gate resolved and the task stayed isolated; it never accepts on your behalf. Once they're done:

```bash
/rig:rig board       # one table: every task, its type/recipe/step/gate — no matter which terminal or process ran it
/rig:rig diff <id>   # then diff/accept/discard each individually, whenever you're ready
```

This is the direct fix for "I opened five terminals and forgot what each one was doing" — `board` is a single source of truth regardless of how the work was dispatched.

### Visual verification screenshots

`visual-verify` (UI diff checks) and `design-audit` (Playwright screen capture) both produce screenshots. These are disposable evidence, not the deliverable — the conclusion lives in prose (`diff.md`), not the pixels:

```
<repo>/.rig/runs/<task-id>/visual/            ← task-scoped (ran via /rig:rig)
<repo>/.rig/visual/adhoc/<ts>-<slug>/         ← ad-hoc (e.g. a standalone /rig:design <url> audit)
```

`discard` deletes a task's `visual/` immediately (the run log's JSON/MD stays). Everything else — including screenshots from accepted tasks — is pruned by age:

```bash
python3 scripts/workbench.py gc --dry-run     # preview what's 14+ days old
python3 scripts/workbench.py gc               # delete it
```

See [`patterns/visual-artifacts.md`](./skills/rig/patterns/visual-artifacts.md) for the full rules.

## 6. Acceptance-gate

Every task gets a criteria checklist drawn from `standard` (applies to every task) plus a task-type-specific preset on top (`scripts/workbench.py gates` is the source of truth):

| preset | applies on top of `standard` for | sample criteria |
|---|---|---|
| `standard` | every task | `task_intent_satisfied` · `no_unrelated_diff` · `diff_summary_written` · `risk_summary_written` · `tests_pass_or_explained` · `no_type_errors_or_explained` · `no_secret_leak` · `no_destructive_operation` |
| `bugfix` | bugfix, performance | `bug_cause_identified` · `fix_is_minimal` · `regression_test_added_or_explained` · `existing_behavior_preserved` · `no_unrelated_refactor` |
| `feature` | feature, test | `requirement_summary_written` · `implementation_matches_requirement` · `tests_added_or_explained` · `public_api_changes_documented` · `migration_or_backward_compatibility_considered` |
| `refactor` | refactor | `behavior_boundaries_identified` · `no_unintended_behavior_change` · `tests_confirm_behavior_preserved` · `no_unrelated_refactor` · `public_api_changes_documented_if_any` |
| `review` | review | `findings_are_concrete` · `severity_labeled` · `file_references_included` · `blocking_and_non_blocking_separated` · `false_positive_risk_considered` |
| `security` | security_review (on top of `review`) | `authn_authz_impact_checked` · `user_input_flow_checked` · `secret_exposure_checked` · `unsafe_eval_or_shell_checked` · `dependency_risk_checked` |

Each criterion is recorded as `passed` / `failed` / `warning` / `skipped` with a detail:

```bash
python3 scripts/workbench.py gate <task_id> --set no_type_errors_or_explained=passed --set tests_added_or_explained=warning:"existing coverage only"
```

The gate as a whole resolves to `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped`. `failed` or `pending` on any criterion blocks `accept` outright (exit 1). `warning` doesn't block, but it's surfaced every time — no silently-swept warnings.

## 7. diff / accept / discard

**`/rig:rig diff`** parses `diff.md`'s `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` headings and prints them structured, plus a `Recommended:` line the *code* computes from gate state (not something the model writes, so it can't be wishful):

```
## rig diff: rig-20260704-153012-login-fix
Changed files:
  M  src/auth/login.ts
  M  src/auth/login.test.ts

Summary:
  Fixed login failure when email includes uppercase characters.
Risk:
  Low. Change is limited to email normalization before lookup.
Tests:
  Added regression test for case-insensitive email login.
Unrelated diff:
  None detected.

Recommended:
  Accept when ready.
```

**`/rig:rig accept`** prints an `accept_requirements` checklist before touching anything:

```
## rig accept: rig-20260704-153012-login-fix — accept_requirements
  ✓ worktree_exists
  ✓ base_branch_recorded
  ✓ diff_summary_generated
  ✓ acceptance_gate_not_failed
  ✓ no_unrelated_diff
```

`worktree_exists`, `base_branch_recorded`, and `diff_summary_generated` are **structural** — no `diff.md`, no accept, full stop, `--force` included. `acceptance_gate_not_failed` and `no_unrelated_diff` are judgment calls the gate makes, and `--force` can override them (recorded as `forced: true` — it doesn't disappear). Once past the checklist, `accept` squash-merges the task branch into your working tree as a **staged** change — never an auto-commit.

**`/rig:rig discard <id> --yes`** always shows the changed-files list first; without `--yes` it's a dry-run preview. It deletes the worktree/branch — the run log (`.rig/runs/<task-id>/`) stays.

## 8. Run-continuity

A mid-flow question won't quietly drop you out of the harness. Every RUN turn re-prints a one-line status header:

```
▸ rig | task: rig-20260704-153012-login-fix | recipe: bugfix | step: test (4/7) | gate: pending | mode: isolated worktree
```

Interruptions (a side question, a tool call, a long pause) don't reset this — the next turn re-anchors: re-print the header, re-state which recipe/step is active, and resume from there rather than quietly sliding into direct, un-gated work. This even survives **context compaction**: a shipped `PreCompact` hook injects instructions to preserve the run-state, and `/rig:init` can mirror them into your CLAUDE.md "Compact Instructions".

## 9. Reviewer drill

`/rig:drill` measures reviewer quality as numbers, not opinions: known bug classes (authz hole, injection, N+1, breaking change, one-way migration, missing tests, …) are seeded into a throwaway diff, review fan-out runs against it, and each reviewer is scored against an answer key it never sees.

```
# Drill Result
Persona: strict_senior_engineer

## Score
- Detection rate: 82%
- False positive rate: 12%
- Severity accuracy: 76%
- Blocking accuracy: 81%
- Explanation quality: 70%

## Missed Issues
1. SQL injection risk in search query (src/search.py:88)
2. Missing authorization check in user update endpoint (src/api/users.py:120)

## Recommended Persona Updates
- [strengthen_security_focus] 2+ security-class misses — raise the priority of the security lens
- [adjust_severity_rule] severity accuracy 76% (< 80%) — clarify the Critical/High/Medium/Low boundary
```

Six metrics per reviewer: `true_positive` / `false_positive` / `false_negative` / `severity_accuracy` (does the reviewer's severity match the seed's?) / `blocking_accuracy` (Blocking vs. Non-blocking placement) / `explanation_quality` (concrete fix, or generic advice?). `Recommended Persona Updates` picks only from four fixed categories (`add_checklist_item` / `adjust_severity_rule` / `add_false_positive_guard` / `strengthen_security_focus`) — no vague prose, so results roll up across runs. `--replay <persona>` re-runs archived diffs after a persona edit and diffs old vs. new verdicts — a snapshot test for reviewer personas. Nothing here touches real code; everything runs in a throwaway worktree.

## 10. Telemetry

```bash
python3 scripts/workbench.py stats                          # everything
python3 scripts/workbench.py stats --recipe bugfix           # one recipe
python3 scripts/workbench.py stats --verifier security-reviewer --last 30d
```

```
## rig stats
Runs: 42
Accepted: 27
Discarded: 8
Failed gate: 7

Most used recipes:
- bugfix: 18
- review: 11
- feature: 8

Gate results:
- passed: 24
- passed_with_warnings: 11
- failed: 7

Verifier behavior:
- strict_senior_engineer: 14 runs, 6 rejects
- product_reviewer: 6 runs, 0 rejects

Warning:
product_reviewer has 0 rejects across 6 runs. Possible rubber-stamp behavior.
```

Reviewer verdicts feed this from `/rig:rig review <task_id> --set <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>` — record them as review tasks resolve, and rig will flag a reviewer that never says no. This is separate from `.rig/runs.jsonl` (the engine-wide execution telemetry `scripts/orchestrate.py runs` reads) — `workbench.py stats` is specifically the workbench task lifecycle (accepted/discarded/gate outcomes).

## 11. Advanced commands

### Command map

| tier | commands |
|---|---|
| **Core** | `/rig:rig`, `/rig:talk`, `/rig:dev`, `/rig:rig status\|diff\|accept\|discard` |
| **Quality** | `/rig:drill`, `/rig:rig stats\|review`, `/rig:pr` (review-only entry) |
| **Knowledge** | `/rig:import`, `/rig:export`, `/rig:catalog`, `/rig:knowledge`, `/rig:persona` |
| **Planning** | `/rig:goal`, `/rig:design`, `/rig:brainstorm`, `/rig:tasks` |
| **Experimental** (real gates, playful delivery) | `/rig:magi`, `/rig:roast`, `/rig:sage`, `/rig:party`, `/rig:movie`, `/rig:coin`, `/rig:duck`, `/rig:pre-mortem` |

Core and Quality are what you need day-to-day. Everything else is opt-in power — see [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 for the full brick catalog.

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

### Project manifest & knowledge layer

Drop `<repo>/.claude/rig.md` to set build/lint/test commands, branch & CI strategy, reviewer, production-impact patterns, default recipe, default reviewer personas, etc. — see [`skills/rig/manifests/_template.md`](./skills/rig/manifests/_template.md). The knowledge layer (`~/.claude/rig/knowledge/{methodology,ai-quirks}/`, `<repo>/.claude/rig/knowledge/domain/`) is injected into every run and accumulates learnings over time.

### Standalone CLI (cross-project)

The deterministic orchestrator (`scripts/orchestrate.py`) also runs as a plain CLI from any directory:

```bash
python3 scripts/orchestrate.py install-shim          # → ~/.local/bin/rig (symlink)
rig models                                            # discover LLM providers
rig probe --provider codex                            # smoke-test a provider (also proves the read-only sandbox)
rig run review-only --provider rig --verifier-provider codex
```

`$RIG_HOME` overrides the install location; `<cwd>/.rig/recipes/<name>.md` overlays a project-local recipe over the shipped one of the same name; a recipe's `checks:` run in the invocation cwd (your project), not the rig repo.

## 12. Recipes / facets / steps

The engine (`skills/rig/SKILL.md`) composes four brick kinds at invocation time: **persona** (who's judging), **instruction** (what to do), **pattern** (how it's dispatched/gated), **recipe** (a named bundle of steps). Task-type auto-routing uses four shipped recipes plus native delegation to the rest:

| recipe | what |
|---|---|
| `bugfix` / `feature` / `refactor` / `documentation` | the four workbench defaults (§4) — inspect → … → acceptance |
| `review-only` | 3-way parallel review (security/design/test) on current changes |
| `pr-review` | review an existing open PR (fetched via GitHub MCP) |
| `debug` | bug-investigation flow: reproduce → isolate (root-cause hypothesis) → implement → verify |
| `release-flow` | intake→design?→implement→verify→review?→pr→merge (size-aware) |
| `design-first` | design-heavy flow |
| `hotfix` | shortest path (intake→implement→verify→pr) |
| `adversarial-review` | eliminate AI tics, dead comments; enforce human readability |
| `goal-loop` | goal-driven loop — converge to a high-level goal by delegating existing flows each round |
| `de-ai-smell` | strip "AI smell" from prose (READMEs, commit/PR text, posts) |
| `design` 🎨 / `design-audit` 🎨 | UI/UX + a11y spec creation, and live-screen audit via Playwright |
| `magi` | 3-sage council (correctness / protection / worth) that decides go/no-go by majority vote |
| `roast` 🌶️ / `coin` 🪙 / `duck` 🦆 / `pre-mortem` ⚰️ | humor packs with real content underneath |
| `movie` 🎬 / `scenario` 🎬✍️ | a general video-creation harness and its scenario-writing front-stage |

`/rig:dev --list` shows every recipe (shipped + your project + your user tier) with badges; `/rig:catalog` (`--list --global`) maps `domain × pack × persona × wiki × recipe` across all tiers. `/rig:sales`, `/rig:talk`, `/rig:goal`, `/rig:magi`, and the humor packs all bolt onto the same domain-agnostic engine — a persona + a thin instruction (+ recipe), engine untouched.

## 13. GitHub integration

| command | read/write |
|---|---|
| `/rig:rig gh issue <n>` | read the Issue (title/body/labels/comments), classify as bugfix/feature/investigation, run it through the workbench |
| `/rig:rig gh pr <n> review [--comment]` | read-only 3-way review by default; `--comment` posts to the PR (write always confirmed) |
| `/rig:rig gh pr <n> fix` | read the PR's diff + review comments + failing CI, fix in an isolated worktree based on the PR's branch, stop at `accept` (nothing is pushed automatically); CI status feeds the `tests_pass_or_explained` gate criterion |
| `/rig:rig gh ci` | check CI status for the current branch/PR, surface the failing job's error summary |

Issue/PR bodies and comments are treated as untrusted external data — instructions embedded in them are never followed, only read as content to classify or fix. GitHub writes (comments, pushes) always require an explicit step; reads are immediate.

## 14. FAQ

**Does `/rig:rig` replace `/rig:dev`?** No — `/rig:rig` auto-classifies and is the recommended default; `/rig:dev` is the same engine with recipe/step/flags spelled out explicitly, for when you want that control.

**What happens to my working tree while rig works?** Nothing. All work happens in an isolated worktree/branch. Your working tree is only ever touched by `accept`, and only as a staged (uncommitted) diff.

**Can I skip the gate if I know better?** `--force` on `accept` overrides judgment-call criteria (`acceptance_gate_not_failed`, `no_unrelated_diff`) and records `forced: true` — it's visible, not silent. Structural prerequisites (`worktree_exists`, `base_branch_recorded`, `diff_summary_generated`) can't be forced; there's nothing to override, they're just true or not.

**Can a reviewer/verifier subagent modify my code?** No. Verifiers run with read-only tool restrictions (`Read,Grep,Glob` / sandboxed shell) enforced at the process level — see `scripts/orchestrate.py probe`.

**Where does rig keep its state?** `<repo>/.rig/runs/<task-id>/` (add `.rig/` to your `.gitignore` — `/rig:init` will offer to do this for you) and, for isolated tasks, a sibling `../rig-worktrees/<repo>/<task-id>/` directory outside your repo.

**How do I know if a reviewer persona is any good?** `/rig:drill` scores detection/false-positive/severity/blocking/explanation quality against known bug seeds. `/rig:rig stats` flags reviewers with zero rejects across 5+ runs as possible rubber stamps.

**What if two tasks run at once?** Each gets its own worktree and branch (`rig/<task-id>`) — they don't collide. `accept` operates on your main working tree, so accept one task's diff, commit it, and only then accept the next (accept refuses if your working tree isn't clean, precisely to keep this safe).

**Can I work on several tasks in one session instead of juggling terminals?** Yes — see §5 "Running several tasks at once." Queue them with `/rig:queue add` + `/rig:queue go --provider rig --max-parallel N` (each dispatched task is isolated automatically), then check `/rig:rig board` for a single combined view instead of tracking N terminal windows in your head.

## Docs

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — the engine (full PARSE/RESOLVE/COMPOSE/RUN spec, rationalization table, red flags)
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree/run-state design
- [`docs/architecture.md`](./docs/architecture.md) — architecture proof points (determinism, gate enforcement, judge measurement)
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — discipline pressure scenarios
- [README.ja.md](./README.ja.md) — Japanese version

## License

[MIT](./LICENSE) © 2026 itoh-shun
