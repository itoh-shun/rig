---
name: rig
description: Quality-gated AI workbench for Claude Code, ported for Codex CLI (#294). Use this whenever the user asks for a gated dev task (bugfix/feature/refactor/review/security review) that should run in an isolated git worktree with explicit acceptance-gate criteria before landing. Also use it to check status/board/diff/accept/discard of an existing rig task, or to drive the deterministic recipe engine (orchestrate.py).
---

# rig (Codex)

This is a **thin procedural pointer**, not a reimplementation of rig's engine. All state, worktree isolation, and acceptance-gate logic live in `scripts/workbench.py` and `scripts/orchestrate.py` — the same stdlib-only scripts Claude Code's `/rig:rig` and `/rig:orchestrate` shell out to. This skill exists so Codex gets the same "native-layer" integration Claude Code has (per rig issue #294), instead of only reaching rig through a stateless `codex exec` subprocess call from `orchestrate.py`'s `--provider codex`.

Note (#304): Cursor's skill discovery also scans `.agents/skills/` for legacy Claude/Codex compatibility, so this same file — installed once at `.agents/skills/rig/SKILL.md` — is picked up by Cursor too. No separate Cursor-specific skill file is needed. See `scripts/host_adapters.py` for the full host capability matrix (this file is host-agnostic; only hooks and subagent definitions differ per host).

**Do not reimplement gate logic, worktree management, or state files in prose.** Call the scripts; they are the source of truth.

## Task workflow (workbench.py)

1. **Start a task**: `python3 scripts/workbench.py new "<task in plain language>" --type <bugfix|feature|refactor|test|performance|documentation|design|investigation|release_support|review|security_review>`
   This creates an isolated git worktree and prints the task_id, worktree path, and acceptance-gate checklist.
2. **Do the work** inside the printed worktree path (or main tree if `--no-worktree` was used for a read-only review task).
3. **Write `.rig/runs/<task_id>/diff.md`** with `## Summary` / `## Risk` / `## Tests` / `## Unrelated diff` sections before trying to accept — this is a structural precondition (`diff_summary_generated`), not optional.
4. **Record gate criteria**: `python3 scripts/workbench.py gate <task_id> --set <criterion>=<passed|failed|warning|skipped>[:<detail>]`
5. **Check status any time**: `python3 scripts/workbench.py status [<task_id>]` or `python3 scripts/workbench.py board` for all active tasks.
6. **Land the change**: `python3 scripts/workbench.py accept <task_id>` (squash-stages onto the main tree; refuses if `accept_requirements` or the gate itself isn't satisfied — `--force` only overrides judgment-based criteria, never the structural ones).
7. **Abandon a task**: `python3 scripts/workbench.py discard <task_id> --yes`.

## Recipe-DAG workflow (orchestrate.py)

For multi-step recipes with deterministic transitions (retry/escalate/DAG-parallel steps), use `python3 scripts/orchestrate.py run <recipe.md> --provider codex --isolate` (or `--provider mock` for a dry run). See `commands/orchestrate.md` in this repo for the full flag reference (`--auto-route`, `--step-model`, `--ab`, etc.) — it is provider-agnostic and works the same whether invoked from Claude Code or Codex.

## Safety invariants (identical to the Claude Code path)

- Worktree isolation: risky work happens in a disposable git worktree/branch, never directly on the user's checked-out branch.
- Acceptance gate: `accept` mechanically refuses when `worktree_exists` / `base_branch_recorded` / `diff_summary_generated` aren't met — no prose shortcut, no `--force` bypass for these three.
- Nothing lands without an explicit `accept`.

## Run-continuity

If `.codex/hooks.json`'s `PreCompact` hook is installed (see `codex/hooks.json` in this repo), an active rig run's state survives context compaction the same way it does in Claude Code. See `hooks/preserve-rig-state.sh` for what it preserves.
