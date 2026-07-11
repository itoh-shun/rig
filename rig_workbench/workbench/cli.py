"""
rig workbench — deterministic runner for a quality-assured AI work environment

Behind the unified `/rig "<task>"` entry point (facets/instructions/workbench.md),
this code enforces **state management, isolated worktrees, acceptance-gate
verdicts, and accept/discard safety**. Task classification, recipe selection,
implementation, and review are the model's job; state and safety are this
script's job (the workbench version of the "code holds the helm" philosophy
from patterns/computational-orchestration).

State is persisted under `<repo>/.rig/runs/<task-id>/`:
  task.json        canonical task metadata (input, classification, base branch, worktree path, status)
  steps.json       progress state of executed steps
  acceptance.json  acceptance-gate criteria and verdicts ({task_id, status, checks[]})
  review.json      per-persona verdicts for review tasks (used by stats for rubber-stamp detection; optional)
  plan.md / diff.md / log.md / final.md   prose artifacts written by the model (this script doesn't touch them.
                                          If diff.md has `## Summary` / `## Risk` / `## Tests` /
                                          `## Unrelated diff` headings, `diff` renders them structured)

Exit codes: 0=success / 1=error (includes accept gate failures and worktree inconsistencies)
Dependencies: standard library only (no PyYAML needed)
"""

import argparse

from .accept import cmd_accept, cmd_diff, cmd_discard, cmd_gc
from .config import (TASK_TYPES, VALID_CRITERION_STATUS, VALID_STEP_STATUS,
                     VALID_VERDICT)
from .digest import cmd_digest
from .injection import cmd_scan_injection
from .lifecycle import cmd_gate, cmd_new, cmd_review, cmd_step
from .reporting import (cmd_audit, cmd_board, cmd_gates, cmd_log, cmd_stats,
                        cmd_status)
from .secrets import cmd_scan_secrets


def main() -> None:
    parser = argparse.ArgumentParser(description="rig workbench — run-state / worktree / acceptance-gate manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="register a task and create an isolated worktree")
    p.add_argument("input", help="the user's natural-language task")
    p.add_argument("--type", required=True, help=f"task_type ({', '.join(TASK_TYPES)})")
    p.add_argument("--slug", help="short English slug for the task-id (derived from input if omitted)")
    p.add_argument("--base", help="explicit base branch name (defaults to the current branch)")
    p.add_argument("--recipe", help="name of the selected recipe")
    p.add_argument("--reason", help="reason for the recipe choice (for the banner and log)")
    p.add_argument("--no-worktree", action="store_true", help="skip worktree creation (read-only runs such as review)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("step", help="record step progress")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", required=True, metavar="STEP=STATUS",
                   help=f"status: {', '.join(VALID_STEP_STATUS)} (repeatable)")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("gate", help="record and evaluate acceptance-gate criteria")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", metavar="CRITERION=STATUS[:DETAIL]",
                   help=f"status: {', '.join(VALID_CRITERION_STATUS)} (append DETAIL after a colon)")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("diff", help="show the diff against base in a structured format")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("accept", help="check accept_requirements and the gate, then squash-apply into the main working tree")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--force", action="store_true", help="apply despite an unmet gate (recorded; missing structural preconditions cannot be overridden)")
    p.set_defaults(func=cmd_accept)

    p = sub.add_parser("discard", help="discard the worktree and branch (keeps the run log)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--yes", action="store_true", help="final confirmation for discarding")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("status", help="show the run state of the current (or given) task")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("board", help="dashboard listing all tasks (active only by default)")
    p.add_argument("--all", action="store_true", help="show all tasks including accepted/discarded")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("log", help="list past run logs")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("gates", help="show the acceptance-gate preset definitions")
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("gc", help="age-based disposal of temporary visual-verification images (visual/) (patterns/visual-artifacts)")
    p.add_argument("--older-than", help="remove items older than this many days (e.g. 14d; default 14d)")
    p.add_argument("--dry-run", action="store_true", help="only show candidates, without deleting")
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("review", help="record per-persona verdicts for review tasks (for stats)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--set", action="append", required=True, metavar="PERSONA=VERDICT",
                   help=f"verdict: {', '.join(VALID_VERDICT)} (repeatable)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("scan-secrets", help="deterministic secret scan (machine backing for no_secret_leak; findings are always masked)")
    p.add_argument("paths", nargs="*", help="files/directories to scan (default: current directory)")
    p.add_argument("--diff", metavar="TASK_ID", help="scan only the task worktree's diff vs its base commit")
    p.set_defaults(func=cmd_scan_secrets)

    p = sub.add_parser("scan-injection", help="deterministic prompt-injection-marker scan "
                       "(machine backing for no_injection_markers; invisible Unicode is fail-grade, "
                       "override phrases warning-grade)")
    p.add_argument("paths", nargs="*", help="files/directories to scan "
                   "(default: the repo's prose surfaces — .claude/rig.md, .claude/rig/knowledge, "
                   ".claude/rig/personas, .rig/recipes/*.md)")
    p.add_argument("--diff", metavar="TASK_ID",
                   help="scan the task worktree's diff vs base + its prose surfaces (what the gate sensor sees)")
    p.set_defaults(func=cmd_scan_injection)

    p = sub.add_parser("digest", help="periodic telemetry digest in Markdown (runs / gates / force-accepts / rubber-stamps / drills)")
    p.add_argument("--period", choices=("week", "month"), default="week",
                   help="rolling window: week = last 7 days (default), month = last 30 days")
    p.add_argument("--out", metavar="PATH", help="write the Markdown to this file instead of stdout")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("stats", help="aggregate past runs (by recipe, by gate, verifier rubber-stamp detection)")
    p.add_argument("--recipe", help="filter by recipe name")
    p.add_argument("--verifier", help="filter by persona name (only runs recorded in review.json)")
    p.add_argument("--last", help="restrict to the last N days (e.g. 30d)")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("audit", help="list the audit log of `accept --force` etc. (`.rig/audit.jsonl`)")
    p.add_argument("--limit", type=int, help="show only the latest N entries")
    p.add_argument("--action", help="filter by action name (e.g. accept_force)")
    p.add_argument("--since", help="show only entries since YYYY-MM-DD")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
