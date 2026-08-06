"""
rig workbench — deterministic runner for a quality-assured AI work environment

Behind the unified `/rig "<task>"` entry point (facets/instructions/workbench.md),
this code enforces **state management, isolated worktrees, acceptance-gate
verdicts, and accept/discard safety**. Task classification remains the model's
job; recipe/capability routing is resolved by workbench.capabilities, while
implementation and review stay with the execution harness. State, routing, and
safety are code-owned (the workbench version of the "code holds the helm"
philosophy from patterns/computational-orchestration).

State is persisted under `<repo>/.rig/runs/<task-id>/`:
  task.json        canonical task metadata (input, classification, base branch, worktree path, status)
  steps.json       progress state of executed steps
  acceptance.json  acceptance-gate criteria and verdicts ({task_id, status, checks[]})
  review.json      per-persona verdicts for review tasks (used by stats for rubber-stamp detection; optional)
  plan.md / diff.md / log.md / final.md   prose artifacts written by the model (this script doesn't touch them.
                                          If diff.md has `## Summary` / `## Risk` / `## Tests` /
                                          `## Unrelated diff` headings, `diff` renders them structured)

Exit codes: 0=success / 1=error (includes accept gate failures and worktree inconsistencies)
Dependencies: the installed rig-workbench runtime package
"""

import argparse

from ..gh_requirement import advise_gh
from .accept import cmd_accept, cmd_diff, cmd_discard, cmd_gc, cmd_verify_provenance
from .cockpit import cmd_cockpit
from .config import (TASK_TYPES, VALID_CRITERION_STATUS, VALID_STEP_STATUS,
                     VALID_VERDICT)
from .confidence import cmd_confidence
from .destructive import cmd_scan_destructive
from .detection_corpus import cmd_drill_corpus
from .digest import cmd_digest
from .feedback import cmd_record_commit, cmd_record_outcome, cmd_trace_commit
from .injection import cmd_scan_injection
from .instincts import _INSTINCT_DECAY_DAYS, cmd_instincts
from .lifecycle import cmd_gate, cmd_new, cmd_review, cmd_step
from .reporting import (cmd_audit, cmd_board, cmd_gates, cmd_log, cmd_stats,
                        cmd_status)
from .secrets import cmd_scan_secrets
from .stale_refs import cmd_stale_refs
from .streaming import cmd_stream_checks
from .route_cli import add_context_arguments as _add_route_context_arguments
from .route_cli import cmd_route


# Sub-commands that mention a missing `gh` / github/gh-stack. It is one stderr
# line and never a refusal — the tools are optional (see
# rig_workbench/gh_requirement.py). `new` is the front door of every rig task and
# the point where someone would still choose a stacked-PR flow, so that is where
# the note is worth reading; on status / board / log / diff / gate / stats /
# audit / the scanners it would be noise, so those say nothing.
_GH_ADVISORY_COMMANDS = {"new"}


def main() -> None:
    parser = argparse.ArgumentParser(description="rig workbench — run-state / worktree / acceptance-gate manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="register a task and create an isolated worktree")
    p.add_argument("input", help="the user's natural-language task")
    p.add_argument("--type", required=True, help=f"task_type ({', '.join(TASK_TYPES)})")
    p.add_argument("--slug", help="short English slug for the task-id (derived from input if omitted)")
    p.add_argument("--base", help="explicit base branch name (defaults to the current branch)")
    _add_route_context_arguments(p)
    p.add_argument("--reason", help="reason for the recipe choice (for the banner and log)")
    p.add_argument("--no-worktree", action="store_true", help="skip worktree creation (read-only runs such as review)")
    p.add_argument("--budget-minutes", type=float,
                   help="estimated time in minutes; going over is flagged in status/board (#281, advisory only)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("route", help="resolve a task capability without installing or writing")
    p.add_argument("--type", required=True, help=f"task_type ({', '.join(TASK_TYPES)})")
    _add_route_context_arguments(p)
    p.add_argument("--json", action="store_true", help="emit the exact route record")
    p.set_defaults(func=cmd_route)

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

    p = sub.add_parser("confidence", help="show reviewer confidence from drill-measured detection rate (#301)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--persona", help="(reserved for future single-persona lookup; unused)")
    p.set_defaults(func=cmd_confidence)

    p = sub.add_parser("drill-corpus", help="/rig:drill fixture corpus: list the pre-built cases, "
                       "materialize one into a throwaway git repo, or score reviews against the answer key")
    p.add_argument("action", choices=("list", "materialize", "score"))
    p.add_argument("case", nargs="?", help="with materialize: which case id")
    p.add_argument("--cases", nargs="+", help="restrict to these case ids (default: all)")
    p.add_argument("--into", help="with materialize: target directory (default: a fresh temp dir)")
    p.add_argument("--reviews", metavar="PATH",
                   help="with score: JSON of {case-id: {persona: review text or @path}}")
    p.add_argument("--append", metavar="PATH",
                   help="with score: append the scored row to this jsonl (e.g. .rig/drill-results.jsonl)")
    p.add_argument("--json", action="store_true", help="with list: machine-readable output")
    p.set_defaults(func=cmd_drill_corpus)

    p = sub.add_parser("record-commit", help="link the final commit SHA of an accepted change to its task (#289, #300)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("sha", nargs="?", help="defaults to the current HEAD")
    p.set_defaults(func=cmd_record_commit)

    p = sub.add_parser("record-outcome", help="record a production outcome for a task (#289, #300)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--status", required=True, choices=("ok", "incident"), help="what actually happened")
    p.add_argument("--note", help="free-text detail")
    p.set_defaults(func=cmd_record_outcome)

    p = sub.add_parser("trace-commit", help="reverse-look-up a commit SHA to its task, gate prediction, and recorded outcome (#289, #300)")
    p.add_argument("sha", help="the commit SHA to look up")
    p.set_defaults(func=cmd_trace_commit)

    p = sub.add_parser("verify-provenance", help="verify an accepted task's signed provenance record (#299)")
    p.add_argument("task_id", nargs="?")
    p.set_defaults(func=cmd_verify_provenance)

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

    p = sub.add_parser("cockpit", help="read-only Mission Control aggregating board/gate/drill/cost/audit onto one screen (#307)")
    p.set_defaults(func=cmd_cockpit)

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
    p.add_argument("--deps", action="store_true",
                   help="scan dependency trees' prose files (node_modules/vendor/third_party "
                        "*.md/*.rst/*.txt) for agent-directed hidden instructions — explicit "
                        "opt-in, never part of the default surfaces (#320)")
    p.set_defaults(func=cmd_scan_injection)

    p = sub.add_parser("stream-checks", help="mid-implementation lightweight checks — fast "
                       "secret/injection/destructive sensors as hints; never blocks the gate (#302)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--watch", action="store_true", help="poll and re-scan when the diff changes")
    p.add_argument("--interval", type=float, default=5.0, help="poll interval seconds (with --watch; default 5)")
    p.add_argument("--max-passes", type=int, default=None, help="stop after N passes (with --watch; default unbounded)")
    p.set_defaults(func=cmd_stream_checks)

    p = sub.add_parser("stale-refs", help="stale path-reference check over the manifest and "
                       "project knowledge (WARN-only, exit 0; backtick-quoted relative paths only) (#316)")
    p.add_argument("paths", nargs="*", help="files/directories to scan "
                   "(default: .claude/rig.md + .claude/rig/knowledge/**/*.md)")
    p.set_defaults(func=cmd_stale_refs)

    p = sub.add_parser("scan-destructive", help="deterministic destructive-command scan "
                       "(machine backing for no_destructive_operation; rm -rf / mkfs / dd / DROP DATABASE "
                       "are fail-grade, context-dependent patterns warning-grade) (#315)")
    p.add_argument("paths", nargs="*", help="files/directories to scan (default: current directory)")
    p.add_argument("--diff", metavar="TASK_ID", help="scan only the task worktree's diff vs its base commit")
    p.set_defaults(func=cmd_scan_destructive)

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

    p = sub.add_parser("instincts", help="list/manage the continuous cross-session instinct-learning layer (#306)")
    p.add_argument("--add", metavar="TEXT", help="record an instinct candidate "
                   "(300 chars max; rejected if it contains a secret/local path)")
    p.add_argument("--evidence", help="with --add: a short explanation of why this is believed")
    p.add_argument("--task-id", help="with --add: the task_id this candidate came from")
    p.add_argument("--confidence", type=float, default=0.5, help="with --add: initial confidence (default 0.5)")
    p.add_argument("--supersedes", help="with --add: explicitly mute this id, replacing it with the new one")
    p.add_argument("--mute", metavar="ID", help="mute the given id (stops being injected)")
    p.add_argument("--expire", metavar="ID", help="set the given id to expired")
    p.add_argument("--decay", action="store_true",
                   help=f"decay active instincts whose last_seen hasn't refreshed in {_INSTINCT_DECAY_DAYS}+ days")
    p.add_argument("--inject-preview", action="store_true",
                   help="preview what would actually be injected at the next session start")
    p.add_argument("--json", action="store_true",
                   help="with --inject-preview: machine-readable JSON (for hooks/inject-instincts.sh)")
    p.set_defaults(func=cmd_instincts)

    p = sub.add_parser("audit", help="list the audit log of `accept --force` etc. (`.rig/audit.jsonl`)")
    p.add_argument("--limit", type=int, help="show only the latest N entries")
    p.add_argument("--action", help="filter by action name (e.g. accept_force)")
    p.add_argument("--since", help="show only entries since YYYY-MM-DD")
    p.set_defaults(func=cmd_audit)

    args = parser.parse_args()
    if args.cmd in _GH_ADVISORY_COMMANDS:
        advise_gh(f"workbench {args.cmd}")
    args.func(args)


if __name__ == "__main__":
    main()
