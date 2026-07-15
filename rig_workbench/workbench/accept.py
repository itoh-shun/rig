"""workbench accept/discard: diff rendering, accept_requirements + squash apply, discard, gc
(split from scripts/workbench.py)."""

import argparse
import datetime
import pathlib
import re
import shutil
import sys

from .config import CHECK_ICON, RECOMMENDATION
from .state import (_diff_lines, audit_append, build_acceptance, die,
                    gate_status, git, load_json, load_task, now_iso,
                    parse_diff_md, repo_root, resolve_task_id, runs_dir,
                    save_task, task_lock, warn, worktree_dirty)

# scripts/ast_diff.py is a standalone, dependency-free script (also runnable directly
# as `python3 scripts/ast_diff.py <base.py> <new.py>`); reuse it here rather than
# duplicating its logic (#280).
_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import ast_diff  # noqa: E402


def _semantic_diff_section(root: pathlib.Path, task: dict, names: list) -> list:
    """Summarize changed *.py files with an AST diff (#280). Augments the text diff, never replaces it.

    Non-Python / unparseable files simply get `supported: False` from `ast_diff` itself —
    this function only narrows down which files to call it on (Modified *.py only) and
    holds no judgment logic of its own.
    """
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else root
    base = task["base_commit"]
    py_modified = []
    for line in names:
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == "M" and parts[-1].endswith(".py"):
            py_modified.append(parts[-1])
    if not py_modified:
        return []
    out = ["", "Semantic diff (Python, #280):"]
    for path in py_modified:
        base_src = git(["show", f"{base}:{path}"], cwd=wt).stdout
        try:
            new_src = (wt / path).read_text(encoding="utf-8")
        except OSError:
            continue
        result = ast_diff.semantic_diff(base_src, new_src)
        out.append(ast_diff.format_summary(result, path))
    return out


def cmd_diff(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    names, stat, dirty = _diff_lines(root, task)

    print(f"## rig diff: {task_id}")
    print(f"base: {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("branch"):
        print(f"branch: {task['branch']}")
    print()
    print("Changed files:")
    if not names and not dirty:
        print("  (no changes)")
    for line in names:
        print(f"  {line}")
    if stat:
        print(f"  {stat}")
    if dirty:
        print(f"\n[WARN] worktree has {len(dirty)} uncommitted change(s) (must be committed before accept):")
        for line in dirty[:20]:
            print(f"  {line}")

    for line in _semantic_diff_section(root, task, names):
        print(line)

    diff_md = d / "diff.md"
    sections = parse_diff_md(diff_md.read_text(encoding="utf-8")) if diff_md.exists() else {}
    for label, key in (("Summary", "summary"), ("Risk", "risk"), ("Tests", "tests")):
        print(f"\n{label}:")
        print(f"  {sections[key]}" if sections.get(key) else "  (not written)")

    print("\nUnrelated diff:")
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    if "unrelated diff" in sections:
        print(f"  {sections['unrelated diff']}")
    elif unrelated:
        print(f"  {CHECK_ICON[unrelated['status']]} {unrelated['status']}"
              + (f" — {unrelated['detail']}" if unrelated.get("detail") else ""))
    else:
        print("  (not checked)")

    if not diff_md.exists():
        print(f"\n[NOTE] {diff_md.relative_to(root)} has not been created. A diff summary is required before accept.")

    print(f"\nRecommended:\n  {RECOMMENDATION[gate_status(acc)]}")


def cmd_accept(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        _cmd_accept_locked(args, root, task_id)


def _cmd_accept_locked(args: argparse.Namespace, root: pathlib.Path, task_id: str) -> None:
    d, task = load_task(root, task_id)

    if task["status"] == "accepted":
        die(f"task '{task_id}' has already been accepted")
    if task["status"] == "discarded":
        die(f"task '{task_id}' has already been discarded")

    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    status = gate_status(acc)
    diff_md = d / "diff.md"
    diff_summary_ok = diff_md.exists() and diff_md.read_text(encoding="utf-8").strip() != ""
    unrelated = next((c for c in acc["checks"] if c["name"] == "no_unrelated_diff"), None)
    unrelated_ok = (unrelated is None) or (unrelated["status"] in ("passed", "warning", "skipped"))
    gate_ok = status in ("passed", "passed_with_warnings", "skipped")

    # ── accept_requirements checklist (Phase 3: show all items first, then judge) ──
    hard = [
        ("worktree_exists", bool(task.get("worktree_path")) and pathlib.Path(task["worktree_path"]).is_dir()),
        ("base_branch_recorded", bool(task.get("base_branch")) and bool(task.get("base_commit"))),
        ("diff_summary_generated", diff_summary_ok),
    ]
    soft = [
        ("acceptance_gate_not_failed", gate_ok),
        ("no_unrelated_diff", unrelated_ok),
    ]
    print(f"## rig accept: {task_id} — accept_requirements")
    for name, ok in hard + soft:
        print(f"  {'✓' if ok else '✗'} {name}")

    hard_fail = [name for name, ok in hard if not ok]
    if hard_fail:
        hints = {
            "worktree_exists": "this task has no worktree (--no-worktree run, or already discarded)",
            "base_branch_recorded": "task.json has no base_branch/base_commit recorded (run-state may be corrupted)",
            "diff_summary_generated": f"{diff_md.relative_to(root)} has not been created. Write the `/rig diff` prose summary first",
        }
        die("Cannot accept (structural preconditions unmet; not overridable even with --force):\n"
            + "\n".join(f"  - {n}: {hints[n]}" for n in hard_fail))

    soft_fail = [name for name, ok in soft if not ok]
    if soft_fail:
        if not args.force:
            failed_checks = [c["name"] for c in acc["checks"] if c["status"] in ("failed", "pending")]
            die(
                f"Cannot accept because the acceptance-gate is {status} (unmet: {', '.join(failed_checks) or 'no_unrelated_diff'}).\n"
                f"  Satisfy the criteria and update via `workbench.py gate {task_id} --set <criterion>=passed`, or\n"
                f"  pass --force if you understand the risk (it will be recorded)"
            )
        warn(f"Accepting with unmet requirements overridden by --force ({', '.join(soft_fail)}). Recording forced: true in task.json")
        task["forced"] = True
        audit_append(root, {
            "ts": now_iso(),
            "action": "accept_force",
            "task_id": task_id,
            "task_type": task.get("task_type"),
            "recipe": task.get("recipe"),
            "bypassed": soft_fail,
            "gate_status": status,
            "failed_checks": [c["name"] for c in acc["checks"]
                              if c["status"] in ("failed", "pending")],
            "invoker": __import__("os").environ.get("RIG_INVOKER") or "direct",
        })
    if status == "passed_with_warnings":
        warns = [f"{c['name']} ({c.get('detail') or 'no detail'})" for c in acc["checks"] if c["status"] == "warning"]
        warn("Accepting with unresolved warnings: " + " / ".join(warns))

    if not task.get("worktree_path"):
        die("This task has no worktree (--no-worktree run). There is no diff to accept")

    # (2) Worktree consistency check
    wt = pathlib.Path(task["worktree_path"])
    if not wt.is_dir():
        die(f"worktree {wt} does not exist")
    dirty = worktree_dirty(wt)
    if dirty:
        die(
            f"worktree has {len(dirty)} uncommitted change(s). "
            f"Commit them in the worktree before accepting (git -C {wt} add -A && git -C {wt} commit)"
        )
    branch = task["branch"]
    ahead = git(["rev-list", "--count", f"{task['base_commit']}..{branch}"], cwd=root).stdout.strip()
    if ahead == "0":
        die(f"branch {branch} has no commits on top of base (no diff to accept)")

    # (2)-b Main working tree consistency check (guarantees up front that a failed
    # squash merge can be safely rolled back with `git reset --hard HEAD`.
    # `git merge --squash` does not create MERGE_HEAD on failure, so
    # `git merge --abort` doesn't work — without this pre-check, reset --hard
    # would wipe out the user's existing uncommitted changes)
    root_dirty = git(["status", "--porcelain"], cwd=root).stdout.splitlines()
    if root_dirty:
        die(
            f"The working tree has {len(root_dirty)} uncommitted change(s). "
            f"accept only runs on a clean working tree so that the squash merge can be safely rolled back. "
            f"Commit or stash first (check with git status)"
        )

    # (3) Squash merge into the main working tree (no commit = the final decision is an explicit human/model action)
    proc = git(["merge", "--squash", branch], cwd=root, check=False)
    if proc.returncode != 0:
        # Conflict: squash merge doesn't create MERGE_HEAD so `merge --abort` doesn't work.
        # Having just guaranteed the working tree was clean, roll back with reset --hard.
        git(["reset", "--hard", "HEAD"], cwd=root, check=False)
        die(
            f"squash merge conflicted (divergence from base). The working tree was restored to its pre-merge state:\n{proc.stderr.strip()}\n"
            f"  Run `git -C {wt} rebase {task['base_branch']}` in the worktree to resolve the conflicts, then retry"
        )

    task["status"] = "accepted"
    task["accepted_at"] = now_iso()
    save_task(d, task)
    names, stat, _ = _diff_lines(root, task)
    print(f"\n## rig accept: {task_id} ✓")
    print(f"Applied the changes from branch {branch} ({ahead} commits) to the main working tree as **staged**.")
    if stat:
        print(f"  {stat}")
    print("Next actions:")
    print("  1) Review: git diff --staged")
    print("  2) Commit: git commit")
    print(f"  3) Clean up: workbench.py discard {task_id} --yes  (removes the worktree and branch; keeps the run log)")


def cmd_discard(args: argparse.Namespace) -> None:
    root = repo_root()
    if not args.task_id:
        die("discard requires an explicit task_id to prevent accidents (see `workbench.py log`)")
    with task_lock(root, args.task_id):
        _cmd_discard_locked(args, root)


def _cmd_discard_locked(args: argparse.Namespace, root: pathlib.Path) -> None:
    d, task = load_task(root, args.task_id)
    task_id = task["task_id"]

    names, stat, dirty = _diff_lines(root, task)
    print(f"## rig discard: {task_id}")
    print(f"input: {task['input']}")
    print("Changed files to be discarded:")
    if names or dirty:
        for line in names:
            print(f"  {line}")
        for line in dirty:
            print(f"  {line}  (uncommitted)")
    else:
        print("  (no changes)")

    if not args.yes:
        die("Re-run with --yes to confirm (the changes listed above will be lost)")

    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        git(["worktree", "remove", "--force", str(wt)], cwd=root)
    if task.get("branch"):
        proc = git(["rev-parse", "--verify", task["branch"]], cwd=root, check=False)
        if proc.returncode == 0:
            git(["branch", "-D", task["branch"]], cwd=root)
    if task["status"] != "accepted":  # cleanup after accept keeps the accepted status
        task["status"] = "discarded"
    task["cleaned_at"] = now_iso()
    task["worktree_path"] = None
    save_task(d, task)

    # Temporary visual-verification artifacts (screenshots etc.) are a means, not
    # a decision record, so delete them immediately on discard
    # (the run log's JSON/MD is kept. See patterns/visual-artifacts).
    visual_dir = d / "visual"
    visual_removed = visual_dir.is_dir()
    if visual_removed:
        shutil.rmtree(visual_dir, ignore_errors=True)

    print(f"Removed the worktree and branch. The run log remains at {d.relative_to(root)}/.")
    if visual_removed:
        print(f"Also removed temporary visual-verification images ({visual_dir.relative_to(root)}/).")


def _dir_age_days(p: pathlib.Path) -> float:
    return (datetime.datetime.now().timestamp() - p.stat().st_mtime) / 86400.0


def cmd_gc(args: argparse.Namespace) -> None:
    """Age-based disposal of temporary visual-verification artifacts (see `patterns/visual-artifacts`).

    Task status (accepted/discarded/running) is irrelevant — the images are a
    regenerable verification means, not permanent records. Never touches
    sources, worktrees, or branches.
    """
    root = repo_root()
    threshold_days = 14
    if args.older_than:
        m = re.match(r"^(\d+)d$", args.older_than)
        if not m:
            die(f"--older-than must be given as '<N>d' (e.g. 14d; got: {args.older_than!r})")
        threshold_days = int(m.group(1))

    candidates: list[pathlib.Path] = []
    runs = runs_dir(root)
    if runs.is_dir():
        candidates.extend(p / "visual" for p in runs.iterdir() if (p / "visual").is_dir())
    adhoc = root / ".rig" / "visual" / "adhoc"
    if adhoc.is_dir():
        candidates.extend(p for p in adhoc.iterdir() if p.is_dir())

    to_remove = [p for p in candidates if _dir_age_days(p) >= threshold_days]

    print(f"## rig gc (threshold: {threshold_days} days{', dry-run' if args.dry_run else ''})")
    if not to_remove:
        print("Nothing to remove.")
        return
    for p in sorted(to_remove):
        rel = p.relative_to(root)
        age = _dir_age_days(p)
        prefix = "[dry-run] " if args.dry_run else ""
        print(f"  {prefix}remove: {rel}/ ({age:.1f} days old)")
        if not args.dry_run:
            shutil.rmtree(p, ignore_errors=True)
    verb = "candidate(s) (not removed due to --dry-run)" if args.dry_run else "removed"
    print(f"\n{len(to_remove)} {verb}.")
