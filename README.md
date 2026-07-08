# rig

**A quality-gated AI workbench for Claude Code.** It composes the right harness for each task, runs changes in an isolated worktree, checks the result with acceptance gates, and lets you accept or discard the diff safely.

> 🇯🇵 日本語版は [README.ja.md](./README.ja.md) を参照。

## 1. What is rig?

You describe a task in plain language. rig figures out what kind of task it is (bugfix / feature / refactor / review / docs / …), composes the harness it needs (`facets/personas/instructions/patterns` — LEGO-style bricks), runs the work in a **git worktree isolated from your working tree**, checks it against explicit **acceptance criteria** (build/lint/tests, no unrelated diff, no secret leak, findings labeled with severity, …), and only touches your real branch when you explicitly `accept`. "It says it's done" is never the bar — the gate is.

rig's value isn't running AI. It's structurally removing the dangerous parts of letting AI work unsupervised: isolation, verification, measurement, recording, and controlled hand-off.

Three properties keep the safety flow real (not just documented):

- **Force-proof accept requirements.** `accept` blocks landing when structural prerequisites are missing (worktree, base branch, diff summary). `--force` overrides *soft* gate failures (recorded to `.rig/audit.jsonl`), but cannot bypass the *hard* prerequisites — the checkpoints live where a flag can't remove them.
- **Cross-provider by design.** The generator and the verifier are separate roles run as separate processes, and each role can pick its own LLM: `claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock` / a nested `rig` harness. The default flow can implement with Claude and verify with Codex (or vice versa) — one class of model does not review its own artifacts. `orchestrate.py probe` proves the read-only sandbox is actually applied per provider, not just wired in the config (§5 & §12).
- **Runs as a Claude Code plugin, not an outside CLI.** `/rig:rig` lives in the same session as your regular work; the isolation, the gate, and the accept step are all a keystroke away rather than a context switch to a separate tool.

**Where rig stands today:** the core safety flow — routing, isolation, the acceptance-gate, and explicit accept/discard — is implemented and exercised by this repo's own test suite (§15). A layer of quality/observability tooling (drill, board, stats, GitHub integration) sits on top of that and is actively evolving. A separate set of playful/creative commands (MAGI council, roast, movie, …) shares the same gates but is explicitly marked experimental. §7 breaks all of this down by name.

### Positioning (#267)

rig is deliberately **not** a heavyweight external engine with its own standalone CLI, session model, and subagent runtime. When the host is Claude Code, rig reuses Claude Code's own native primitives — Skill invocation, subagent dispatch, hooks (`PreCompact` for run-continuity) — instead of reimplementing them; the plugin adds only the layer that's actually missing: recipe DAGs, the acceptance-gate, isolated worktrees, and telemetry. That's what "Claude Code native; no heavy DSL engine" means in `plugin.json`.

This delegation is currently asymmetric across hosts. For `--provider codex` (and other non-Claude-Code providers), rig does **not** get to reuse an equivalent native layer — `orchestrate.py` calls the provider as a stateless one-shot subprocess and owns the entire control loop itself (state machine, retries, gate) rather than delegating any of it. Deepening that integration for Codex/Copilot/other hosts is tracked in #294/#304 and not yet done; until then, rig is "native" specifically on Claude Code, and "a self-contained harness calling out to a model" everywhere else.

What doesn't vary by host: rig doesn't just claim its gate works, it measures whether it does. `/rig:drill` seeds known bug classes into a synthetic diff and scores each reviewer persona's actual detection rate; `/rig:rig stats` flags reviewers with zero rejects as possible rubber stamps. Most review-gate tooling stops at "a reviewer exists" — rig's differentiator is knowing, with numbers, whether that reviewer is actually catching anything.

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

## 3. Main entrypoint

The main command is:

```bash
/rig:rig "fix the login bug"
```

Claude Code exposes plugin commands as `/<plugin-namespace>:<command>`, and this plugin's namespace happens to also be named `rig` — so the full form repeats the word. That's a namespacing artifact, not a design choice: **`/rig:rig` is the single main entrypoint**, the one worth memorizing before anything else in this doc.

If the repeated name still bothers you, `/rig:talk` is a more conversational front door onto the same engine — useful when you'd rather describe the situation and let rig ask follow-ups than state a single task up front:

```bash
/rig:talk "the login bug is back, not sure why this time"
```

Use `/rig:rig` for the full gated workbench flow. Use `/rig:talk` when you want a conversational entrypoint into the same underlying engine.

## 4. Core safety flow

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
⑤  structured diff summary + next action
        │
        ▼
user decision
   ├─ accept  → land the staged diff into your working tree
   └─ discard → delete the worktree; the run log stays
```

Every `new` task starts with a **routing banner** so you never wonder why rig picked what it picked:

```
▸ rig
task: fix the login bug
detected: bugfix
recipe: bugfix — matched "bug"/"fix"
mode: isolated worktree
gate: standard + bugfix
```

See §8 for how the recipe behind step ② actually gets composed, and §5 for what backs steps ③–⑤.

## 5. Why it is safe

### Isolated worktree

Every task gets its own git worktree (`patterns/isolated-worktree`) and its own throwaway branch. rig never writes to your working tree directly — a failed or half-finished attempt costs you nothing.

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

**Running several tasks at once, without losing track.** Because isolation is per-task, running multiple tasks concurrently is safe by construction — each gets its own worktree and branch, so they can't step on each other. To actually run them in parallel (instead of typing `/rig:rig "<task>"` one at a time), queue them and go:

```bash
/rig:queue add "fix the login bug"
/rig:queue add "add search to the inventory list"
/rig:queue add "make the README clearer"
/rig:queue go --provider rig --max-parallel 3   # dispatches 3 independent headless processes
```

`--provider rig` routes each queued item through `/rig:rig "<task>"`, so each one is isolated the same way a task you typed directly would be — no risk of the parallel processes fighting over the same files. Queue's own verifier only confirms the gate resolved and the task stayed isolated; it never accepts on your behalf. Once they're done, `/rig:rig board` (§10) is the single place to check every task regardless of how many terminals or queue items are behind them.

**Visual verification screenshots.** `visual-verify` (UI diff checks) and `design-audit` (Playwright screen capture) both produce screenshots. These are disposable evidence, not the deliverable — the conclusion lives in prose (`diff.md`), not the pixels:

```
<repo>/.rig/runs/<task-id>/visual/            ← task-scoped (ran via /rig:rig)
<repo>/.rig/visual/adhoc/<ts>-<slug>/         ← ad-hoc (e.g. a standalone /rig:design <url> audit)
```

`discard` deletes a task's `visual/` immediately (the run log's JSON/MD stays). Everything else — including screenshots from accepted tasks — is pruned by age (`python3 scripts/workbench.py gc --dry-run` to preview, `gc` to delete what's 14+ days old). See [`patterns/visual-artifacts.md`](./skills/rig/patterns/visual-artifacts.md) for the full rules.

### Acceptance gate

Acceptance gates decide whether a run is safe to hand off. The model cannot mark work as done by itself — a run must pass mechanical checks such as unrelated-diff detection, test/type/lint status, risk summary, and task-specific requirements. Failed or pending gates block `accept` outright.

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

The gate as a whole resolves to `passed` / `passed_with_warnings` / `failed` / `pending` / `skipped`:

```
Gate:
✓ task_intent_satisfied
✓ no_unrelated_diff
✓ diff_summary_written
✓ risk_summary_written
⚠ tests_pass_or_explained
✓ no_secret_leak

Overall:
passed_with_warnings

Next:
Review /rig:rig diff, then choose accept or discard.
```

`failed` or `pending` on any criterion blocks `accept` outright (exit 1). `warning` doesn't block, but it's surfaced every time — no silently-swept warnings.

### Read-only verifier

rig separates the AI that implements from the AI that verifies, and the verifier is forced into read-only mode at the process level — not by asking nicely.

Verifier/reviewer subagents run with restricted tool access (`claude --allowedTools Read,Grep,Glob`, `codex --sandbox read-only`). They can inspect files, grep context, read diffs, and report findings. They cannot edit files, run formatters that mutate files, commit changes, or modify the worktree. This prevents the reviewer from silently fixing or altering the artifact it is supposed to judge — a real risk when the same model class implements and reviews. `scripts/orchestrate.py probe`/`selftest` prove the restriction is actually applied per provider, not just documented.

### Explicit accept / discard

`accept` first prints an `accept_requirements` checklist — `worktree_exists`, `base_branch_recorded`, and `diff_summary_generated` are **structural prerequisites that even `--force` cannot bypass**. It then lands the change as a **staged** diff (never an auto-commit) — you still commit. `discard` requires the task-id spelled out and a `--yes` confirmation, and always shows what you're about to lose first. Full walkthrough with example output in §9.

### Run history

`discard` deletes the worktree and branch but never the run log (`.rig/runs/<task-id>/`) — you can always see what was attempted and why it was rejected or dropped.

This survives more than `discard`: a mid-flow interruption (a side question, a tool call, a long pause) doesn't quietly drop you out of the harness either. Every RUN turn re-prints a one-line status header:

```
▸ rig | task: rig-20260704-153012-login-fix | recipe: bugfix | step: test (4/7) | gate: pending | mode: isolated worktree
```

The next turn re-anchors on this header rather than sliding into direct, un-gated work. It even survives **context compaction**: a shipped `PreCompact` hook injects instructions to preserve the run-state, and `/rig:init` can mirror them into your CLAUDE.md "Compact Instructions."

### Beyond rig-driven changes

Everything above only protects changes that went through `/rig:rig`. A plain `git commit`/`git push` — made by a human, or by an AI working outside rig entirely — gets none of it. `build`/`lint`/`test` are project-specific and a plain git hook has no way to know them, but one piece of the gate *is* checkable mechanically without any project config: secret-pattern scanning (`no_secret_leak`). `/rig:rig install-git-hook` installs that one sensor as a real `pre-commit`/`pre-push` hook, opt-in and never silently overwriting a hook it didn't install itself (`--force` to override deliberately):

```bash
python3 scripts/workbench.py install-git-hook               # both pre-commit and pre-push
python3 scripts/workbench.py install-git-hook --which pre-push
```

It's a mechanical tripwire, not a replacement for the AI-judged `no_secret_leak` criterion — false positives exist, and `git commit/push --no-verify` bypasses it per-call same as any other hook.

## 6. Core commands

Core commands are the default safety workflow: route task, isolate work, verify, inspect diff, accept or discard.

| command | what it does |
|---|---|
| `/rig:rig "<task>"` | classify → pick a recipe → isolated-worktree run → acceptance-gate → summary |
| `/rig:talk "<task>"` | same engine, conversational entrypoint (§3) |
| `/rig:dev ...` | same engine, everything explicit (recipe/steps/flags) — power-user entry, §13 |
| `/rig:orchestrate` | same engine, step-level computational orchestration — §13 |
| `/rig:rig status [id]` | current/most-recent task: step checklist, gate checklist, pending diff, next action |
| `/rig:rig diff [id]` | changed files + Summary/Risk/Tests/Unrelated-diff/Recommended (§9) |
| `/rig:rig accept [id] [--force]` | land the diff into your working tree (staged) — blocked unless the gate passed (§9) |
| `/rig:rig discard <id> --yes` | delete the worktree/branch; run log stays (§9) |
| `/rig:rig log [--limit N]` | history of past tasks: input, recipe, gate result |

## 7. Feature status

| Area | Status | Notes |
|---|---:|---|
| Natural task routing | Stable | `/rig:rig "<task>"` routes task to recipe (§4, §8) |
| Isolated worktree | Stable | risky changes are isolated by default (§5) |
| Acceptance gate | Stable | `failed`/`pending` gates block accept (§5) |
| Diff / accept / discard | Stable | explicit, staged hand-off flow (§9) |
| Read-only verifier | Stable | reviewers cannot mutate artifacts (§5), enforced per-provider |
| Run history / run-continuity | Stable | run logs persist; state survives interruption and context compaction (§5) |
| Validation (`--validate`) | Stable | structural doctor for the brick catalog itself, CI-enforced |
| Board / stats | Beta | useful for observing multiple runs; output format still evolving (§10) |
| Reviewer drill | Beta | measures reviewer quality with injected issues (§11) |
| GitHub integration | Beta | Issue/PR/CI flow may evolve (§12) |
| Queue (parallel dispatch) | Beta | safe by construction (isolation), UX still evolving (§5) |
| Knowledge import/export/persona/catalog/forge | Beta | useful but not on the core safety path (§13) |
| Planning commands (goal/design/brainstorm/tasks/loop/harness/qa) | Beta | real, gated flows; less battle-tested than Core (§13) |
| Creative / party commands (MAGI, roast, movie, …) | Experimental | real gates underneath, playful delivery, kept out of the default path (§14) |

Nothing in this table is aspirational — there's no "Planned" row because we don't document unshipped features here; proposals live as GitHub issues. If a command isn't listed, it isn't shipped yet.

## 8. Task routing and recipes

The engine (`skills/rig/SKILL.md`) composes four brick kinds at invocation time: **persona** (who's judging), **instruction** (what to do), **pattern** (how it's dispatched/gated), **recipe** (a named bundle of steps). Task-type auto-routing (step ① in §4) uses four shipped recipes plus native delegation to the rest. This table is illustrative, not exhaustive — see `/rig:dev --list` or `/rig:catalog` for the full current set:

| recipe | what |
|---|---|
| `bugfix` / `feature` / `refactor` / `documentation` | the four workbench defaults — inspect → … → acceptance |
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

## 9. Diff / accept / discard

**`/rig:rig diff`** parses `diff.md`'s `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` headings and prints them structured, plus a `Recommended:` line the *code* computes from gate state (not something the model writes, so it can't be wishful). Modified `*.py` files also get an automatic semantic-diff line (AST-based signature/body-change/no-semantic-change distinction, #280):

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

## 10. Run board and stats

### Run board

When multiple AI tasks are running or completed, `/rig:rig board` is a management tower: one table showing every task's state, no matter how many terminals or `/rig:queue` items dispatched them.

```
[running    ] rig-20260705-091200-search-feature
    add search to the inventory list
    type=feature      recipe=feature      mode=isolated   step=implement(running)      gate=-
[gate_passed] rig-20260705-090800-login-fix
    fix the login bug
    type=bugfix       recipe=bugfix       mode=isolated   step=acceptance(passed)      gate=passed
[gate_failed] rig-20260705-091500-readme-clarity
    make the README clearer
    type=documentation recipe=documentation mode=isolated step=verify-commands(failed) gate=failed
```

It tells you: which task is still running, which passed or failed its gate, which worktree holds changes, which run is ready for `diff` review, and which should be `discard`ed. `/rig:rig board --all` widens this to every task ever recorded, not just active ones.

### Stats

`/rig:rig stats` summarizes past runs — an observation layer over the whole workbench, not just a single run's outcome:

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

It can reveal frequently-failing recipes, reviewers that never reject, gate types that often block accept, and the accept-vs-discard ratio. Reviewer verdicts feed this from `/rig:rig review <task_id> --set <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS>` — record them as review tasks resolve, and rig will flag a reviewer that never says no. This is separate from `.rig/runs.jsonl` (the engine-wide execution telemetry `scripts/orchestrate.py runs` reads) — `workbench.py stats` is specifically the workbench task lifecycle (accepted/discarded/gate outcomes).

### Cockpit

`/rig:rig cockpit` puts board, gate radar, drill-measured reviewer confidence, a cost meter, and a force-bypass safety strip on one read-only screen — the single place to check "is everything actually safe right now," not just "what ran":

```bash
python3 scripts/workbench.py cockpit
```

```
┌─ Run timeline（アクティブ 2 / 全 2 件）
│ [gate_passed] rig-test-001                 gate=passed_with_warnings fix the login bug...
│ [gate_failed] rig-test-002                 gate=failed               add search to inventory...
├─ Gate radar
│ passed_with_warnings: 1
│ failed: 1
├─ Reviewer confidence（drill 実測）
│ design-reviewer: 検出率 100%（2/2）
│ security-reviewer: 検出率 80%（4/5・誤検出 1）
├─ Cost meter
│ 未計測（recipe/model 単位のコスト計測は今後追加予定）
├─ Safety strip
│ force-bypass: 1 件（詳細: `workbench.py audit`）
└─ Next action rail
  diff/accept 待ち 1 件: rig-test-001 → `workbench.py diff <id>` / `accept <id>`
  gate 未達 1 件: rig-test-002 → 修正して再判定、または `discard <id> --yes`
```

It's v1 and read-only by design: `accept`/`discard` aren't triggered from here, only recommended — the next-action rail always points back to the existing commands. Missing data (no drill run yet, cost metering not implemented) is shown as "未計測" (not measured), never as a blank that could be misread as a clean bill of health. It reuses `board`/`stats`/`audit`'s own aggregation functions rather than re-implementing them.

## 11. Reviewer drill

Reviewer personas are not just prompts. rig can test them.

`/rig:drill` injects known bug classes (authz hole, injection, N+1, breaking change, one-way migration, missing tests, …) into a throwaway diff, runs the review fan-out against it, and scores each reviewer against an answer key it never sees:

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

rig does not just run reviewers. It measures them.

### Dogfooding (#284)

The same measurement applies to rig's own development. Anyone maintaining a fork or a heavily-customized instance can generate the current numbers with the commands already covered above — no separate tooling needed:

```bash
python3 scripts/workbench.py digest --since 30d   # §10 — failing gates, drill detection rate, rubber-stamp warnings
python3 scripts/workbench.py stats                 # §10 — the same aggregation, unscoped by time
/rig:drill --replay                                # §11 — regression-test the reviewer personas themselves
```

**Honest scope note:** this repo does not currently auto-publish those numbers (e.g. a CI job that regenerates a badge or a docs page on every merge) — that's tracked as follow-up work, not implemented here. Today, "dogfooding" means the maintainer can run the above locally and paste the output into a PR description or release notes; it is not yet a live, continuously-updated public score.

## 12. GitHub integration

| command | read/write |
|---|---|
| `/rig:rig gh issue <n>` | read the Issue (title/body/labels/comments), classify as bugfix/feature/investigation, run it through the workbench |
| `/rig:rig gh pr <n> review [--comment]` | read-only 3-way review by default; `--comment` posts to the PR (write always confirmed) |
| `/rig:rig gh pr <n> fix` | read the PR's diff + review comments + failing CI, fix in an isolated worktree based on the PR's branch, stop at `accept` (nothing is pushed automatically); CI status feeds the `tests_pass_or_explained` gate criterion |
| `/rig:rig gh ci` | check CI status for the current branch/PR, surface the failing job's error summary |

Issue/PR bodies and comments are treated as untrusted external data — instructions embedded in them are never followed, only read as content to classify or fix. GitHub writes (comments, pushes) always require an explicit step; reads are immediate.

### GitHub Action (#265)

`action.yml` packages headless CI usage of `orchestrate.py run --isolate` for workflows that don't have a live Claude Code session:

```yaml
- uses: itoh-shun/rig@master
  with:
    task: "Fix the flaky test in ci.yml"
    recipe: recipes/bugfix.md
    provider: claude
    anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
    auto_pr: true
```

It never invents its own execution logic — `scripts/rig-action-entrypoint.sh` shells out to the same `orchestrate.py` used everywhere else, derives the final status (`DONE`/`ESCALATE`/`BLOCKED`/`STOPPED`) from the run-state JSON, and only pushes a branch + opens a PR (via `gh pr create`) when the gate resolved `DONE`. A failing or pending gate fails the job and creates nothing.

**Honest verification note:** the `run` step (task execution, gate evaluation, worktree isolation/cleanup) was verified end-to-end locally with `--provider mock`. The `open-pr` step (branch push + `gh pr create`) could not be exercised against a real GitHub Actions runner from this environment — it's implemented against `gh`'s documented CLI interface (pre-installed on GitHub-hosted runners) but hasn't been run live. Treat it as reviewed-but-not-live-tested until it's exercised in an actual workflow run.

### MCP server (#263)

To drive rig from outside a Claude Code session (another agent, CI, a separate process), start `scripts/mcp_server.py`:

```bash
python3 scripts/mcp_server.py
```

It listens for Model Context Protocol (JSON-RPC 2.0, line-delimited) on stdio. It doesn't depend on the official `mcp` SDK — to match `workbench.py`/`orchestrate.py`'s stdlib-only stance and avoid a heavy third-party dependency, it implements a minimal stdio transport with the standard library alone. No new execution engine: every tool is a thin adapter that shells out to `workbench.py`/`orchestrate.py`, so accept/discard's force-proof requirements (`worktree_exists`/`base_branch_recorded`/`diff_summary_generated`, etc.) go through the exact same code path and can't be bypassed via MCP either.

Tools provided:

| Tool | Equivalent CLI |
|---|---|
| `rig_task_new` / `rig_task_status` / `rig_task_board` / `rig_task_diff` / `rig_task_gate` / `rig_task_accept` / `rig_task_discard` / `rig_task_log` | `workbench.py new/status/board/diff/gate/accept/discard/log` |
| `rig_orchestrate_init` / `rig_orchestrate_next` / `rig_orchestrate_check` / `rig_orchestrate_status` / `rig_orchestrate_run` / `rig_orchestrate_runs` | `orchestrate.py init/next/check/status/run/runs` |

Opt-in: nothing changes unless you start this server; existing CLI/skill usage is unaffected. To wire it into an MCP client (e.g. Claude Desktop), register `command: python3`, `args: ["<repo>/scripts/mcp_server.py"]` in its MCP config.

**Verification:** launched the server as a subprocess against a disposable repo and drove a full happy path purely over JSON-RPC — `initialize` → `tools/list` → `rig_task_new` (enqueue) → `rig_task_board` (recover task_id) → `rig_task_accept` (confirmed `isError: true` rejection while the gate is unmet) → `rig_task_gate` to mark every criterion `passed` → `rig_task_accept` (succeeds) — and confirmed the rejection text and the squash-accept result match direct CLI invocation exactly.

### VS Code extension (`vscode-extension/`, #286)

A sidebar view (Explorer panel "rig board") that shows `.rig/runs/` task/gate state without leaving the editor. **Read-only** — no accept/discard or any other write command is registered. It just reads the same JSON `workbench.py` already writes (`task.json`/`acceptance.json`/`steps.json`); no new state-management engine.

```bash
cd vscode-extension
npm install
npm run compile
```

Open this folder in VS Code and press `F5` (launches an Extension Development Host), or package it with `npx vsce package` and `code --install-extension` the resulting `.vsix`. See `vscode-extension/README.md` for details.

**Honest verification note:** the state-parsing logic (`src/rigState.ts`) has no dependency on the `vscode` module, so it's unit-tested with plain Node (`npm run test:unit`) — confirming gate-status priority matches `workbench.py`'s `gate_status()` exactly, correct parsing of `task.json`/`acceptance.json`/`steps.json`, and the active-only filter matching `board`'s default. `tsc` compiles cleanly against `@types/vscode`. **This sandbox has no VS Code GUI, so actually loading the extension in a real Extension Host and confirming the Tree View renders and the file watcher fires has not been verified** — treat it as reviewed-but-not-live-tested.

### Codex native-layer integration (#294)

As of 2026, the Codex CLI has extension mechanisms (Skills, Hooks, Subagent TOML) that closely mirror Claude Code's. `orchestrate.py --provider codex` previously only ever treated Codex as a stateless one-shot `codex exec` subprocess call; the following wires rig into Codex's native layer too:

| Mechanism | File added | What it does |
|---|---|---|
| Skills | `codex/skills/rig/SKILL.md` | A thin skill following Codex's `.agents/skills/<name>/SKILL.md` convention (`name`/`description` frontmatter). No new engine — it's a procedural pointer to the existing `workbench.py`/`orchestrate.py` |
| Hooks | `codex/hooks.json` | Wires run-continuity into Codex's `PreCompact` event by reusing the exact same `hooks/preserve-rig-state.sh` (it contains nothing Claude-Code-specific, so there's nothing to duplicate) |
| Subagents | `.codex/agents/security-reviewer.toml` | A Codex-native subagent definition with the same review axes and output contract as `agents/security-reviewer.md`. `sandbox_mode = "read-only"` asks Codex's own sandbox to enforce read-only, layered on top of — not replacing — rig's existing argv-level enforcement (`--sandbox read-only` in `orchestrate.py`'s `build_argv`); defense in depth per #294's requirement |
| MCP | (docs only) | Register `scripts/mcp_server.py` (#263) under `[mcp_servers.rig]` in `~/.codex/config.toml` or `.codex/config.toml`: `command = "python3"`, `args = ["<repo>/scripts/mcp_server.py"]` |

Install by copying/symlinking `codex/skills/rig/` to `~/.agents/skills/rig/` (or `.agents/skills/rig/` at the repo root), copying `codex/hooks.json` to `.codex/hooks.json` (or merging its `PreCompact` entry into `~/.codex/hooks.json`), and leaving `.codex/agents/security-reviewer.toml` where it is — Codex picks up project-scoped agents from `.codex/agents/` automatically.

**Honest verification note:** there is no `codex` CLI in this environment, so none of this has been exercised against a real Codex session. What was verified: `codex/hooks.json` is valid JSON; `.codex/agents/security-reviewer.toml` parses with Python's `tomllib` and only uses fields documented on [Codex's official Subagents page](https://developers.openai.com/codex/subagents) (`name`/`description`/`sandbox_mode`/`developer_instructions`); the existing stateless `--provider codex` path (`build_argv`'s `codex` branch, including the `--sandbox read-only` verifier enforcement) was left completely untouched by this batch, and `orchestrate.py selftest`'s existing coverage for it (e.g. `N probe: codex verifier は read-only サンドボックスを強制`) still passes, confirming backward compatibility. Actually loading the skill, firing the hook, having Codex enforce `sandbox_mode` on the subagent, and connecting to the MCP server all require a live Codex CLI and remain **unverified** — the paths/schemas here are sourced from Codex's official docs (Subagents/Hooks/Skills pages) but haven't been run live.

### Fable 5 refusal-classifier → fallback handling (`--provider anthropic`, #297)

Fable 5's safety filter auto-blocks requests in three categories (cyber/bio/reasoning_extraction) and can transparently fall back to Opus 4.8. `orchestrate.py run --provider anthropic` calls the Anthropic Messages API directly over HTTP to detect and handle this (the `claude`/`rig` CLI providers don't expose a structured `stop_reason`, so they're out of scope):

- Set `fallback_model` (e.g. `claude-opus-4-8`) to request `anthropic-beta: server-side-fallback-2026-06-01`; on a successful fallback, `FABLE_FALLBACK` is recorded in `state["history"]` and **the gate is not blocked** — the step continues with the fallback's output as a normal result.
- A direct refusal (no fallback configured, or exhausted) records `FABLE_REFUSAL` (category/explanation) instead of failing silently.
- `runs --cost` shows token usage (including `cache_read_input_tokens`) and a fallback/refusal occurrence count.
- If you assign Fable 5 to a persona whose job is discussing attack techniques (e.g. `security-reviewer`) via `--step-model` (#293), always set `fallback_model` — see `agents/security-reviewer.md`.

**Honest verification note:** verified against a mock HTTP server reproducing the Anthropic Messages API's response shape, across three cases — direct refusal, successful server-side fallback, and a normal response with neither. **Not connected to the real Anthropic API** (that would require live traffic and carries real billing risk). The schema used here is sourced from `anthropics/claude-cookbooks`' `fable_5_fallback_billing/guide.ipynb`, but behavior against the real model is unverified.

### Managed Agents API delegation (experimental, opt-in, #295)

An experimental backend that delegates review-gate parallel fan-out to Anthropic's Managed Agents API (coordinator/worker, beta) instead of the existing subprocess + ThreadPoolExecutor path. Enable with `cfg["parallel_backend"] = "managed-agents"` plus `cfg["environment_id"]` (required) — **the default stays the existing mechanism**; this is fully opt-in. See `commands/orchestrate.md` §⑨ for details and honest limitations (REST paths are inferred from the documented SDK method names, it has not been connected to the real API, and event-stream integration into the run-continuity header is not implemented).

## 13. Advanced commands

### Command map

| tier | commands |
|---|---|
| **Quality** | `/rig:drill`, `/rig:rig stats\|review`, `/rig:pr` (review-only entry), `/rig:harness` (audit your project's own dev harness), `/rig:qa` (spec-based test-case design) |
| **Knowledge** | `/rig:import`, `/rig:export`, `/rig:catalog`, `/rig:knowledge`, `/rig:persona`, `/rig:forge` (self-extension: author new bricks/packs from a description) |
| **Planning** | `/rig:goal`, `/rig:design`, `/rig:brainstorm`, `/rig:tasks`, `/rig:loop` (recurring driver — polling/watch, the opposite of goal) |

These are useful after you understand the core safety flow (§4–§6) — see [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) §2 for the full brick catalog. (`/rig:queue` is covered in §5, `/rig:init` in the FAQ, `/rig:sales` in §8, and Experimental commands have their own section — §14.)

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

### Codex skill install

Codex can use rig directly as a skill by exposing this repo's `skills/rig` folder under `~/.codex/skills`:

```bash
mkdir -p ~/.codex/skills
ln -sfn /path/to/rig/skills/rig ~/.codex/skills/rig
```

After restarting Codex, invoke it as `$rig`. In Codex, `$rig "fix the login bug"` is the equivalent of the Claude Code `/rig:rig "fix the login bug"` entrypoint. For cross-provider orchestration, `scripts/orchestrate.py` already knows how to call `codex exec` and enforces read-only mode for verifier roles.

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

## 14. Experimental commands

Experimental commands explore alternative collaboration, creativity, and playful workflows. They run on the same gates as everything else — a `magi` verdict or a `roast` review is real content, not a toy — but they're kept out of the default day-to-day path and out of the Core/Quality/Advanced tiers above so they don't crowd a first-time read of this README.

| commands | what |
|---|---|
| `/rig:magi`, `/rig:sage` | decision/wisdom modes — MAGI 3-council go/no-go vote, sage-style guidance |
| `/rig:roast`, `/rig:coin`, `/rig:duck`, `/rig:pre-mortem` | humor packs with real content underneath (§8) |
| `/rig:party` | party/status-rendering novelty on top of real run data |
| `/rig:movie`, `/rig:scenario` | a general video-creation harness and its scenario-writing front-stage |

They are not required for the core AI workbench experience described in §4–§9.

## 15. Implementation notes

What backs the claims above, concretely — this table exists so "documented" and "verified" don't quietly drift apart:

| Feature | Evidence |
|---|---|
| Recipe resolution, RESOLVE flags, size-aware routing | `scripts/orchestrate.py selftest` (resolve/RESOLVE sections) |
| Isolated worktree lifecycle (create / merge / preserve-on-dirty / preserve-on-escalate) | `scripts/orchestrate.py selftest` (isolate section) |
| Read-only verifier sandboxing (per-provider CLI flags) | `scripts/orchestrate.py probe` / `selftest` (probe section) |
| Queue dispatch and state transitions | `scripts/orchestrate.py selftest` (queue section) |
| Recipe/persona/command schema, brick-catalog drift | `scripts/validate.py` + `scripts/validate.py selftest` (CI-enforced on every PR) |
| Acceptance-gate criteria, accept/discard mechanics | `scripts/workbench.py` — exercised against scratch git repos each release (see `CHANGELOG.md` entries for the verification notes) |
| Run telemetry | `.rig/runs.jsonl` (`scripts/orchestrate.py runs`) and `.rig/runs/<task-id>/*.json` (workbench run state) |

## 16. FAQ

**Does `/rig:rig` replace `/rig:dev`?** No — `/rig:rig` auto-classifies and is the recommended default; `/rig:dev` is the same engine with recipe/step/flags spelled out explicitly, for when you want that control.

**What happens to my working tree while rig works?** Nothing. All work happens in an isolated worktree/branch. Your working tree is only ever touched by `accept`, and only as a staged (uncommitted) diff.

**Can I skip the gate if I know better?** `--force` on `accept` overrides judgment-call criteria (`acceptance_gate_not_failed`, `no_unrelated_diff`) and records `forced: true` — it's visible, not silent. Structural prerequisites (`worktree_exists`, `base_branch_recorded`, `diff_summary_generated`) can't be forced; there's nothing to override, they're just true or not.

**Can a reviewer/verifier subagent modify my code?** No. Verifiers run with read-only tool restrictions (`Read,Grep,Glob` / sandboxed shell) enforced at the process level — see `scripts/orchestrate.py probe`.

**Where does rig keep its state?** `<repo>/.rig/runs/<task-id>/` (add `.rig/` to your `.gitignore` — `/rig:init` will offer to do this for you) and, for isolated tasks, a sibling `../rig-worktrees/<repo>/<task-id>/` directory outside your repo.

**How do I know if a reviewer persona is any good?** `/rig:drill` scores detection/false-positive/severity/blocking/explanation quality against known bug seeds. `/rig:rig stats` flags reviewers with zero rejects across 5+ runs as possible rubber stamps.

**What if two tasks run at once?** Each gets its own worktree and branch (`rig/<task-id>`) — they don't collide. `accept` operates on your main working tree, so accept one task's diff, commit it, and only then accept the next (accept refuses if your working tree isn't clean, precisely to keep this safe).

**Can I work on several tasks in one session instead of juggling terminals?** Yes — see §5 "Isolated worktree → Running several tasks at once." Queue them with `/rig:queue add` + `/rig:queue go --provider rig --max-parallel N` (each dispatched task is isolated automatically), then check `/rig:rig board` (§10) for a single combined view instead of tracking N terminal windows in your head.

## Docs

- [`skills/rig/SKILL.md`](./skills/rig/SKILL.md) — the engine (full PARSE/RESOLVE/COMPOSE/RUN spec, rationalization table, red flags)
- [`skills/rig/patterns/isolated-worktree.md`](./skills/rig/patterns/isolated-worktree.md) — worktree/run-state design
- [`docs/architecture.md`](./docs/architecture.md) — architecture proof points (determinism, gate enforcement, judge measurement)
- [`docs/testing-scenarios.md`](./docs/testing-scenarios.md) — discipline pressure scenarios
- [README.ja.md](./README.ja.md) — Japanese version

## License

[MIT](./LICENSE) © 2026 itoh-shun
