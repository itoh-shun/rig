"""workbench streaming: mid-implementation lightweight checks (issue #302).

The acceptance/review gates run at step boundaries; on a long implement step
that means feedback arrives in one pile at the end. `stream-checks` runs the
FAST machine sensors (secret / injection / destructive — diff-scoped, no LLM,
tens of milliseconds) against the task worktree on demand, printing findings
as HINTS for the implementer.

Structural guarantees (the issue's core requirements, enforced by shape, not
promise):
  - Never blocks the final gate: this command never reads or writes
    acceptance.json and always exits 0. The same detectors run again inside
    `cmd_gate`, where pass/fail is actually decided — streaming findings are
    a preview of that verdict, not a verdict.
  - Opt-in: nothing calls this automatically; the implement instruction
    suggests invoking it at natural checkpoints (after a commit-sized chunk).
  - Diff-scoped only: cost is bounded by the change, not the repo.

`--watch --interval N` polls, re-scanning only when the diff actually changed
(hash comparison — quiet loop on an idle worktree). `--max-passes M` bounds
the loop (mainly for tests/CI; default unbounded until the worktree
disappears or Ctrl-C).
"""

import argparse
import hashlib
import pathlib
import time

from .destructive import MASS_DELETE_THRESHOLD
from .destructive import format_findings as destructive_format
from .destructive import scan_task_diff as destructive_scan
from .injection import format_findings as injection_format
from .injection import scan_file as injection_scan_file
from .injection import scan_line as injection_scan_line
from .secrets import iter_added_lines, untracked_files, worktree_diff_text
from .secrets import scan_file as secret_scan_file
from .secrets import scan_line as secret_scan_line
from .state import die, load_task, repo_root, resolve_task_id


def _scan_once(wt: pathlib.Path, base: str) -> dict:
    """One pass of the fast sensors over the worktree diff. Pure read."""
    diff_text = worktree_diff_text(wt, base)
    untracked = untracked_files(wt)

    secrets: list[dict] = []
    injections: list[dict] = []
    for rel, lineno, text in iter_added_lines(diff_text):
        secrets.extend(secret_scan_line(text, rel, lineno))
        injections.extend(injection_scan_line(text, rel, lineno))
    for f, rel in untracked:
        secrets.extend(secret_scan_file(f, rel))
        injections.extend(injection_scan_file(f, rel))
    destructive, n_deleted = destructive_scan(wt, base)
    return {"secrets": secrets, "injections": injections,
            "destructive": destructive, "n_deleted": n_deleted,
            "digest": hashlib.sha256(
                (diff_text + "\n".join(rel for _, rel in untracked)).encode("utf-8", "replace")
            ).hexdigest()}


def _print_hints(result: dict) -> int:
    """Print one pass's findings as hints. Returns the hint count."""
    n = 0
    for f in result["secrets"]:
        print(f"  hint[secret] {f['path']}:{f['line']} [{f['kind']}] {f['masked_excerpt']}")
        n += 1
    for line in injection_format(result["injections"]):
        print(f"  hint[injection] {line}")
        n += 1
    for line in destructive_format(result["destructive"]):
        print(f"  hint[destructive] {line}")
        n += 1
    if result["n_deleted"] >= MASS_DELETE_THRESHOLD:
        print(f"  hint[destructive] (mass deletion) {result['n_deleted']} file(s) deleted vs base")
        n += 1
    if n == 0:
        print("  no hints (secret / injection / destructive sensors, diff-scoped)")
    return n


def cmd_stream_checks(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    _, task = load_task(root, task_id)
    wt_path = task.get("worktree_path")
    base = task.get("base_commit")
    if not wt_path or not pathlib.Path(wt_path).is_dir():
        die(f"task '{task_id}' has no worktree (created with --no-worktree, or already discarded)")
    if not base:
        die(f"task '{task_id}' has no base_commit recorded")
    wt = pathlib.Path(wt_path)

    print(f"## stream-checks: {task_id} (advisory — never blocks the gate; "
          "the same detectors decide pass/fail at gate time)")
    result = _scan_once(wt, base)
    _print_hints(result)
    if not args.watch:
        return

    last_digest = result["digest"]
    passes = 1
    while args.max_passes is None or passes < args.max_passes:
        time.sleep(args.interval)
        if not wt.is_dir():
            print("worktree gone — stopping")
            return
        result = _scan_once(wt, base)
        passes += 1
        if result["digest"] == last_digest:
            continue  # idle worktree: stay quiet
        last_digest = result["digest"]
        print(f"-- change detected (pass {passes}) --")
        _print_hints(result)
