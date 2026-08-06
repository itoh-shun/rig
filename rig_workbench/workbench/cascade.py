"""workbench cascade: parent/child tasks and the rebase that keeps a stack straight.

Why this exists
---------------
`gh stack` was the plan, and the plan died on a measurement: `gh stack rebase`
switches branches with `git checkout`, and git refuses to check out a branch
another worktree is holding. rig gives every task its own worktree, so the
target branch is *always* held:

    $ gh stack rebase --no-trunk
    ✗ could not start rebase of task2 onto task1: failed to run git:
      fatal: 'task2' is already used by worktree at '.../wt2'

Worktree isolation is load-bearing for rig's safety story, so the tool that
could not live with it was the one that got dropped (`commands/setup.md`).
Plain git has no such problem, because it never needs to check anything out:

    git -C <child worktree> rebase --onto <new parent tip> <old parent tip>

rig already knows every task's worktree path, so this is the whole mechanism.
What was missing — and what this module adds — is the part rig had no model
for at all: **which task is stacked on which**.

The two recorded fields
-----------------------
`parent_task`  the task this one was forked from (`workbench new --parent`).
`stack_base`   the parent branch tip as of the fork, refreshed after each
               successful cascade.

`stack_base` is what `--onto` needs as its *upstream* argument, and it cannot
be recovered after the fact: if the parent's history was rewritten (amend,
its own rebase), `merge-base(parent, child)` walks back past the rewritten
range and would replay the parent's discarded commits into the child. So the
old tip is recorded rather than recomputed.

`base_commit` is deliberately NOT touched. It is the historical record of where
the task started (#312), and `effective_base` recomputes the live merge base
for every diff and sensor — after a successful cascade that live value becomes
the new parent tip on its own. Cascade adds a field; it never edits the one
other code trusts.

Safety
------
- A child with uncommitted changes is refused, not stashed. Rebase would move
  work the user has not committed and rig would own the loss.
- A conflicted rebase is aborted (`git rebase --abort`), leaving the child
  exactly where it was, and its own descendants are skipped — replaying a
  subtree onto a base that failed to move produces nothing but more conflicts.
- Every skip is printed. A cascade that silently left half the stack behind is
  worse than one that refused.

CLI: `rig-wb wb cascade [<task-id>] [--dry-run]`
"""

import argparse
import pathlib

from .state import (die, git, load_json, runs_dir, save_task, task_lock, warn,
                    worktree_dirty)
from .state import repo_root, run_dir


# ── the stack model ──────────────────────────────────────────────────────────
def read_all(root: pathlib.Path) -> dict[str, dict]:
    """{task_id: task} for every recorded run (missing/broken task.json skipped)."""
    base = runs_dir(root)
    if not base.is_dir():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(base.iterdir()):
        tj = p / "task.json"
        if not tj.exists():
            continue
        try:
            task = load_json(tj)
        except Exception:
            continue
        if isinstance(task, dict) and task.get("task_id"):
            out[task["task_id"]] = task
    return out


def children_of(tasks: dict[str, dict], task_id: str) -> list[dict]:
    """Direct children, oldest first (task ids are timestamp-prefixed)."""
    return sorted((t for t in tasks.values() if t.get("parent_task") == task_id),
                  key=lambda t: t["task_id"])


def ancestry(tasks: dict[str, dict], task_id: str) -> list[str]:
    """The parent chain from `task_id` up to its root, cycle-safe.

    A hand-edited task.json can point a task at its own descendant; walking that
    forever is a hang, so the walk stops at the first repeat.
    """
    chain: list[str] = []
    seen: set[str] = {task_id}
    current = tasks.get(task_id, {}).get("parent_task")
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = tasks.get(current, {}).get("parent_task")
    return chain


def roots(tasks: dict[str, dict]) -> list[str]:
    """Task ids that have children but no (resolvable) parent of their own."""
    parented = {t["task_id"] for t in tasks.values()
                if t.get("parent_task") and t["parent_task"] in tasks}
    have_children = {t["parent_task"] for t in tasks.values() if t.get("parent_task")}
    return sorted(tid for tid in have_children if tid in tasks and tid not in parented)


def branch_tip(root: pathlib.Path, branch: str) -> str:
    proc = git(["rev-parse", "--verify", f"{branch}^{{commit}}"], cwd=root, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def resolve_parent(root: pathlib.Path, tasks: dict[str, dict], parent_id: str) -> dict:
    """Validate a `--parent` reference and return the parent task."""
    parent = tasks.get(parent_id)
    if parent is None:
        die(f"parent task '{parent_id}' not found. List tasks with `workbench.py log`")
    if not parent.get("branch"):
        die(f"parent task '{parent_id}' has no branch (created with --no-worktree) — "
            "a task can only be stacked on one that owns a branch")
    if not branch_tip(root, parent["branch"]):
        die(f"parent task '{parent_id}' branch '{parent['branch']}' no longer resolves "
            "(discarded?) — nothing to stack on")
    return parent


# ── the cascade ──────────────────────────────────────────────────────────────
def plan_node(root: pathlib.Path, tasks: dict[str, dict], child: dict) -> dict:
    """What would happen to one child: {action, old, new, reason}."""
    parent = tasks.get(child.get("parent_task") or "")
    if parent is None:
        return {"action": "skip", "reason": "parent task record is gone"}
    branch = parent.get("branch") or ""
    new = branch_tip(root, branch) if branch else ""
    if not new:
        return {"action": "skip", "reason": f"parent branch '{branch}' does not resolve"}
    old = child.get("stack_base") or child.get("base_commit") or ""
    if not old:
        return {"action": "skip", "reason": "no stack_base recorded for this child"}
    if old == new:
        return {"action": "current", "old": old, "new": new,
                "reason": "already on the parent's current tip"}
    wt = child.get("worktree_path")
    if not wt or not pathlib.Path(wt).is_dir():
        return {"action": "skip", "old": old, "new": new,
                "reason": "no worktree (created with --no-worktree, or discarded)"}
    dirty = worktree_dirty(pathlib.Path(wt))
    if dirty:
        return {"action": "blocked", "old": old, "new": new,
                "reason": f"{len(dirty)} uncommitted change(s) — commit or stash them first "
                          "(rebase would move work that was never committed)"}
    return {"action": "rebase", "old": old, "new": new, "reason": ""}


def rebase_child(root: pathlib.Path, child: dict, old: str, new: str) -> tuple[bool, str]:
    """`git rebase --onto <new> <old>` inside the child's own worktree.

    No checkout anywhere: the child worktree already has its branch out, which
    is exactly the arrangement `gh stack` could not work with. On conflict the
    rebase is aborted so the child is left untouched rather than mid-rebase.
    """
    wt = pathlib.Path(child["worktree_path"])
    proc = git(["rebase", "--onto", new, old], cwd=wt, check=False)
    if proc.returncode == 0:
        return True, ""
    git(["rebase", "--abort"], cwd=wt, check=False)
    detail = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
    return False, detail[-1] if detail else f"git rebase exited {proc.returncode}"


def cascade(root: pathlib.Path, start: str, dry_run: bool = False) -> dict:
    """Rebase the subtree under `start` onto its parents' current tips.

    Breadth-first from the top, so a child is only moved after its own parent
    has finished moving — otherwise the grandchild would be replayed onto a tip
    that is about to change again.
    """
    tasks = read_all(root)
    counts = {"rebased": 0, "current": 0, "blocked": 0, "skipped": 0}
    lines: list[str] = []

    def walk(task_id: str, depth: int, blocked_above: bool) -> None:
        for child in children_of(tasks, task_id):
            cid = child["task_id"]
            indent = "  " * (depth + 1)
            if blocked_above:
                counts["skipped"] += 1
                lines.append(f"{indent}- {cid}  SKIPPED (its parent did not move)")
                walk(cid, depth + 1, True)
                continue
            plan = plan_node(root, tasks, child)
            action = plan["action"]
            if action == "current":
                counts["current"] += 1
                lines.append(f"{indent}= {cid}  up to date")
            elif action in ("skip", "blocked"):
                counts["blocked" if action == "blocked" else "skipped"] += 1
                label = "BLOCKED" if action == "blocked" else "SKIPPED"
                lines.append(f"{indent}! {cid}  {label}: {plan['reason']}")
                walk(cid, depth + 1, True)
                continue
            elif dry_run:
                counts["rebased"] += 1
                lines.append(f"{indent}→ {cid}  would rebase --onto {plan['new'][:12]} "
                             f"{plan['old'][:12]}")
            else:
                with task_lock(root, cid):
                    ok, err = rebase_child(root, child, plan["old"], plan["new"])
                if ok:
                    counts["rebased"] += 1
                    child["stack_base"] = plan["new"]
                    save_task(run_dir(root, cid), child)
                    lines.append(f"{indent}→ {cid}  rebased onto {plan['new'][:12]}")
                else:
                    counts["blocked"] += 1
                    lines.append(f"{indent}! {cid}  CONFLICT: {err}")
                    lines.append(f"{indent}  (rebase aborted — the worktree is unchanged; "
                                 f"resolve by hand in {child['worktree_path']})")
                    walk(cid, depth + 1, True)
                    continue
            walk(cid, depth + 1, False)

    walk(start, 0, False)
    return {"counts": counts, "lines": lines}


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_cascade(args: argparse.Namespace) -> None:
    root = repo_root()
    tasks = read_all(root)
    if not tasks:
        die("No run history (.rig/runs/ is empty). Register a task with `/rig \"<task>\"` first")

    if args.task_id:
        if args.task_id not in tasks:
            die(f"task '{args.task_id}' not found. List tasks with `workbench.py log`")
        starts = [args.task_id]
    else:
        starts = roots(tasks)
        if not starts:
            print("## cascade: no stacked tasks")
            print("Nothing is stacked yet. Fork a task onto another with "
                  "`workbench.py new \"<task>\" --type <t> --parent <task-id>`.")
            return

    mode = "dry run — nothing is rebased" if args.dry_run else "rebasing"
    print(f"## cascade: {mode}")
    total = {"rebased": 0, "current": 0, "blocked": 0, "skipped": 0}
    for start in starts:
        print(f"{start}  (branch: {tasks[start].get('branch') or '-'})")
        result = cascade(root, start, dry_run=args.dry_run)
        for line in result["lines"]:
            print(line)
        if not result["lines"]:
            print("  (no children)")
        for k, v in result["counts"].items():
            total[k] += v

    verb = "would rebase" if args.dry_run else "rebased"
    print(f"\n{verb}: {total['rebased']} / up to date: {total['current']} / "
          f"blocked: {total['blocked']} / skipped: {total['skipped']}")
    if total["blocked"] or total["skipped"]:
        warn("part of the stack was not moved (see the lines marked ! above). "
             "Resolve those, then re-run `rig-wb wb cascade`.")
