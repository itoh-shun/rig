#!/usr/bin/env python3
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
import contextlib
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys

try:
    import fcntl  # POSIX: mutual exclusion for concurrent task operations (task_lock)
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows fallback (locking disabled)
from collections import Counter

# ── acceptance-gate presets (source of truth; the instruction references this) ──
GATE_PRESETS: dict[str, list[str]] = {
    # Standard gate shared by all task_types
    "standard": [
        "task_intent_satisfied",
        "no_unrelated_diff",
        "diff_summary_written",
        "risk_summary_written",
        "tests_pass_or_explained",
        "no_type_errors_or_explained",
        "no_secret_leak",
        "no_destructive_operation",
    ],
    # bugfix-specific (layered on top of standard)
    "bugfix": [
        "bug_cause_identified",
        "fix_is_minimal",
        "regression_test_added_or_explained",
        "existing_behavior_preserved",
        "no_unrelated_refactor",
    ],
    # feature-specific (layered on top of standard)
    "feature": [
        "requirement_summary_written",
        "implementation_matches_requirement",
        "tests_added_or_explained",
        "public_api_changes_documented",
        "migration_or_backward_compatibility_considered",
    ],
    # refactor-specific (layered on top of standard)
    "refactor": [
        "behavior_boundaries_identified",
        "no_unintended_behavior_change",
        "tests_confirm_behavior_preserved",
        "no_unrelated_refactor",
        "public_api_changes_documented_if_any",
    ],
    # For review tasks (produces no diff, so standard is not included)
    "review": [
        "findings_are_concrete",
        "severity_labeled",
        "file_references_included",
        "blocking_and_non_blocking_separated",
        "false_positive_risk_considered",
    ],
    # For security checks (layered on top of review)
    "security": [
        "authn_authz_impact_checked",
        "user_input_flow_checked",
        "secret_exposure_checked",
        "unsafe_eval_or_shell_checked",
        "dependency_risk_checked",
    ],
}

# task_type → applied gate presets (listed in composition order: first is base, rest are layered on)
TASK_TYPES: dict[str, list[str]] = {
    "bugfix": ["standard", "bugfix"],
    "feature": ["standard", "feature"],
    "refactor": ["standard", "refactor"],
    "test": ["standard", "feature"],
    "performance": ["standard", "bugfix"],
    "documentation": ["standard"],
    "design": ["standard"],
    "investigation": ["standard"],
    "release_support": ["standard"],
    "review": ["review"],
    "security_review": ["review", "security"],
}

VALID_STEP_STATUS = ("pending", "running", "passed", "failed", "skipped")
VALID_CRITERION_STATUS = ("pending", "passed", "failed", "warning", "skipped")
VALID_VERDICT = ("APPROVE", "REJECT", "APPROVE_WITH_CONDITIONS")

STEP_ICON = {"passed": "✓", "failed": "✗", "running": "▸", "pending": "…", "skipped": "-"}
CHECK_ICON = {"passed": "✓", "failed": "✗", "warning": "⚠", "pending": "…", "skipped": "-"}

NEXT_ACTIONS = {
    "running": "Running. Evaluate the gate after completion (workbench.py gate <id> --set …)",
    "gate_passed": "Review the diff with /rig diff → apply with /rig accept (or drop with /rig discard)",
    "gate_failed": "Fix the unmet criteria and re-evaluate the gate (if still failed, /rig discard)",
    "accepted": "Review git diff --staged and commit → clean up the worktree with /rig discard <id>",
    "discarded": "Finished (only the run log is kept)",
}

RECOMMENDATION = {
    "failed": "Fix the failed acceptance-gate criteria before accept (check with `workbench.py gate`).",
    "pending": "Evaluate the remaining acceptance criteria before accepting.",
    "passed_with_warnings": "Review the warnings, then accept if they are acceptable.",
    "passed": "Safe to accept.",
    "skipped": "This task has no gate criteria configured — verify manually before accepting.",
}


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def die(msg: str) -> "NoReturn":  # noqa: F821
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


# ── git helpers ───────────────────────────────────────────────────────────────
def git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        die(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def repo_root() -> pathlib.Path:
    proc = git(["rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        die("Run this inside a git repository")
    return pathlib.Path(proc.stdout.strip())


def current_branch(root: pathlib.Path) -> str:
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()


def runs_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "runs"


def audit_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "audit.jsonl"


def locks_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "locks"


@contextlib.contextmanager
def task_lock(root: pathlib.Path, task_id: str):
    """Per-task mutual exclusion (prevents concurrent `accept`/`discard`/`gate`/`step`/`review`).

    Non-blocking acquisition via fcntl.flock. If acquisition fails, another
    process is definitely operating on the same task, so `die` with an explicit
    error (never race silently). The lock is released automatically on process
    exit (flock is fd-tied, so it doesn't linger even on kill). Without fcntl
    (e.g. Windows) this is a no-op — the safety net applies to parallel
    rig:queue go on WSL/Linux. Lock files are left in place (`.rig/` is
    gitignored; the files are empty).
    """
    if fcntl is None:
        yield
        return
    ld = locks_dir(root)
    ld.mkdir(parents=True, exist_ok=True)
    lock_file = ld / f"{task_id}.lock"
    with lock_file.open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die(f"task '{task_id}' is being operated on by another process ({lock_file.relative_to(root)}). "
                "Wait for it to finish, or inspect the process if it appears stuck")
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def audit_append(root: pathlib.Path, event: dict) -> None:
    """Append a single JSON line to `.rig/audit.jsonl`.

    Permanent record of "--force overrides of an unmet gate", complementing the
    force-proof of accept_requirements. Evidence log that makes the physical
    strength of the differentiator visible. Read via `workbench.py audit`.
    Write failures are swallowed silently (best-effort, like telemetry).
    """
    try:
        p = audit_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


# ── run-state I/O ────────────────────────────────────────────────────────────
def run_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    d = runs_dir(root) / task_id
    if not d.is_dir():
        die(f"task '{task_id}' not found ({d.relative_to(root)}). List tasks with `workbench.py log`")
    return d


def load_json(path: pathlib.Path, default: dict | None = None) -> dict:
    if not path.exists():
        if default is not None:
            return default
        die(f"{path} does not exist")
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_task(root: pathlib.Path, task_id: str) -> tuple[pathlib.Path, dict]:
    d = run_dir(root, task_id)
    return d, load_json(d / "task.json")


def save_task(d: pathlib.Path, task: dict) -> None:
    task["updated_at"] = now_iso()
    save_json(d / "task.json", task)


def latest_task_id(root: pathlib.Path) -> str | None:
    base = runs_dir(root)
    if not base.is_dir():
        return None
    candidates = sorted((p.name for p in base.iterdir() if (p / "task.json").exists()), reverse=True)
    return candidates[0] if candidates else None


def resolve_task_id(root: pathlib.Path, given: str | None) -> str:
    if given:
        return given
    tid = latest_task_id(root)
    if not tid:
        die("No run history (.rig/runs/ is empty). Run `/rig \"<task>\"` first")
    return tid


# ── task-id / slug ───────────────────────────────────────────────────────────
def make_slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    slug = "-".join(w.lower() for w in words)[:32].strip("-")
    return slug or "task"


def make_task_id(slug: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"rig-{ts}-{slug}"


# ── gate construction / evaluation ───────────────────────────────────────────
def build_acceptance(task_id: str, task_type: str) -> dict:
    presets = TASK_TYPES[task_type]
    checks: list[dict] = []
    seen: set[str] = set()
    for preset in presets:
        for name in GATE_PRESETS[preset]:
            if name not in seen:
                seen.add(name)
                checks.append({"name": name, "status": "pending", "detail": ""})
    return {"task_id": task_id, "task_type": task_type, "presets": presets,
            "status": "pending", "checks": checks, "checked_at": None}


def gate_status(acc: dict) -> str:
    """Evaluate with priority: failed > pending > (skipped if all skipped) > warning > passed."""
    statuses = [c["status"] for c in acc["checks"]]
    if not statuses:
        return "skipped"
    if any(s == "failed" for s in statuses):
        return "failed"
    if any(s == "pending" for s in statuses):
        return "pending"
    if all(s == "skipped" for s in statuses):
        return "skipped"
    if any(s == "warning" for s in statuses):
        return "passed_with_warnings"
    return "passed"


# ── worktree ─────────────────────────────────────────────────────────────────
def default_worktree_path(root: pathlib.Path, task_id: str) -> pathlib.Path:
    import os
    wt_root = os.environ.get("RIG_WORKTREE_ROOT")
    base = pathlib.Path(wt_root) if wt_root else root.parent / "rig-worktrees" / root.name
    return base / task_id


def worktree_dirty(wt: pathlib.Path) -> list[str]:
    proc = git(["status", "--porcelain"], cwd=wt)
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ── structured diff.md parser ────────────────────────────────────────────────
def parse_diff_md(text: str) -> dict[str, str]:
    """Split diff.md, delimited by `## <heading>`, into a section dict (lowercase keys)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


# ── subcommand implementations ────────────────────────────────────────────────
def ensure_rig_gitignored(root: pathlib.Path) -> bool:
    """Append `.rig/` to the repo's `.gitignore` if missing. Returns whether it was appended.

    `.rig/` holds worktree state / runs / audit / locks, so it is appended
    automatically on the first task creation to keep it from slipping into a PR.
    If it is already ignored as `.rig/` / `.rig` / `/.rig/` (any variant), do
    nothing (never clobber the user's entries on a false positive).
    If `.gitignore` is missing, create it. Do nothing when root is not git-managed.
    """
    if not (root / ".git").exists():
        return False
    gi = root / ".gitignore"
    already = False
    lines: list[str] = []
    if gi.exists():
        lines = gi.read_text(encoding="utf-8").splitlines()
        for ln in lines:
            s = ln.strip()
            if s in (".rig/", ".rig", "/.rig/", "/.rig"):
                already = True
                break
    if already:
        return False
    with gi.open("a", encoding="utf-8") as f:
        # The existing file may not end with a newline, so lead with one
        f.write("\n# rig workbench state (task worktrees, telemetry, audit, locks)\n.rig/\n")
    return True


def cmd_new(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' is invalid. Valid: {', '.join(TASK_TYPES)}")
    slug = args.slug or make_slug(args.input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' already exists")

    # Auto-append `.rig/` to .gitignore if missing. Insurance against accidental PR contamination.
    if ensure_rig_gitignored(root):
        print("◇ Appended .rig/ to .gitignore (prevents PR contamination)")

    base_branch = args.base or current_branch(root)
    base_commit = git(["rev-parse", "HEAD"], cwd=root).stdout.strip()

    worktree_path: str | None = None
    branch: str | None = None
    if not args.no_worktree:
        wt = default_worktree_path(root, task_id)
        branch = f"rig/{task_id}"
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-b", branch, str(wt), "HEAD"], cwd=root)
        worktree_path = str(wt)

    task = {
        "task_id": task_id,
        "input": args.input,
        "task_type": args.type,
        "recipe": args.recipe or "",
        "recipe_reason": args.reason or "",
        "base_branch": base_branch,
        "base_commit": base_commit,
        "branch": branch,
        "worktree_path": worktree_path,
        "status": "running",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    d.mkdir(parents=True, exist_ok=True)
    save_json(d / "task.json", task)
    save_json(d / "steps.json", {"steps": []})
    acc = build_acceptance(task_id, args.type)
    save_json(d / "acceptance.json", acc)

    # ── Selection-rationale banner (Phase 1 §3: code prints this deterministically instead of leaving it to prose) ──
    print("▸ rig")
    print(f"task: {args.input}")
    print(f"detected: {args.type}")
    print(f"recipe: {args.recipe or '(unspecified)'}" + (f" — {args.reason}" if args.reason else ""))
    print(f"mode: {'isolated worktree' if worktree_path else 'not isolated (--no-worktree)'}")
    print(f"gate: {' + '.join(acc['presets'])}")
    print()
    print(f"task_id: {task_id}")
    print(f"base_branch: {base_branch} @ {base_commit[:12]}")
    if worktree_path:
        print(f"worktree: {worktree_path} (branch: {branch})")
    else:
        print("worktree: none (--no-worktree specified)")
    print(f"state: {d.relative_to(root)}/")


def cmd_step(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "steps.json", {"steps": []})
        for pair in args.set:
            if "=" not in pair:
                die(f"--set must be given as <step>=<status> (got: {pair!r})")
            name, status = pair.split("=", 1)
            if status not in VALID_STEP_STATUS:
                die(f"step status '{status}' is invalid. Valid: {', '.join(VALID_STEP_STATUS)}")
            for step in data["steps"]:
                if step["name"] == name:
                    step["status"] = status
                    step["updated_at"] = now_iso()
                    break
            else:
                data["steps"].append({"name": name, "status": status, "updated_at": now_iso()})
        save_json(d / "steps.json", data)
        print(f"{task_id} steps: " + " ".join(f"{s['name']}={s['status']}" for s in data["steps"]))


def cmd_gate(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d, task = load_task(root, task_id)
        acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"]))

        known = {c["name"]: c for c in acc["checks"]}
        for pair in args.set or []:
            if "=" not in pair:
                die(f"--set must be given as <criterion>=<status>[:detail] (got: {pair!r})")
            name, status = pair.split("=", 1)
            detail = ""
            if ":" in status:
                status, detail = status.split(":", 1)
            if status not in VALID_CRITERION_STATUS:
                die(f"criterion status '{status}' is invalid. Valid: {', '.join(VALID_CRITERION_STATUS)}")
            if name not in known:
                die(f"criterion '{name}' does not exist in this task's gate. Valid: {', '.join(known)}")
            known[name]["status"] = status
            if detail:
                known[name]["detail"] = detail

        acc["status"] = gate_status(acc)
        acc["checked_at"] = now_iso()
        save_json(d / "acceptance.json", acc)

        if task["status"] == "running" and acc["status"] in ("passed", "passed_with_warnings", "failed", "skipped"):
            task["status"] = "gate_failed" if acc["status"] == "failed" else "gate_passed"
            save_task(d, task)

        print(f"## acceptance-gate: {task_id}  [{acc['status'].upper()}]")
        print(f"presets: {' + '.join(acc['presets'])}")
        for c in acc["checks"]:
            detail = f" — {c['detail']}" if c.get("detail") else ""
            print(f"  {CHECK_ICON[c['status']]} {c['name']}{detail}")
        if acc["status"] == "failed":
            sys.exit(1)


def _diff_lines(root: pathlib.Path, task: dict) -> tuple[list[str], str, list[str]]:
    """Return (name-status lines, shortstat, uncommitted worktree lines)."""
    wt = pathlib.Path(task["worktree_path"]) if task.get("worktree_path") else None
    if wt and wt.is_dir():
        base = task["base_commit"]
        names = git(["diff", "--name-status", f"{base}...HEAD"], cwd=wt).stdout.splitlines()
        stat = git(["diff", "--shortstat", f"{base}...HEAD"], cwd=wt).stdout.strip()
        dirty = worktree_dirty(wt)
        return names, stat, dirty
    # Worktree-less runs (reviews etc.) diff against the current state of the main working tree
    names = git(["diff", "--name-status", "HEAD"], cwd=root).stdout.splitlines()
    stat = git(["diff", "--shortstat", "HEAD"], cwd=root).stdout.strip()
    return names, stat, []


def cmd_diff(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"]))
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

    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"]))
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


def _print_steps(d: pathlib.Path) -> None:
    steps = load_json(d / "steps.json", {"steps": []})["steps"]
    print("Steps:")
    if not steps:
        print("  (none recorded)")
        return
    for s in steps:
        print(f"  {STEP_ICON.get(s['status'], '?')} {s['name']}"
              + (f" ({s['status']})" if s["status"] not in ("passed",) else ""))


def _print_checks(acc: dict) -> None:
    print(f"Gate: {acc['status'].upper()}  ({' + '.join(acc['presets'])})")
    for c in acc["checks"]:
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  {CHECK_ICON[c['status']]} {c['name']}{detail}")


def cmd_status(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"]))
    acc["status"] = gate_status(acc)

    print(f"## rig status: {task_id}")
    print(f"task:        {task['input']}")
    print(f"type:        {task['task_type']}" + (f" / recipe: {task['recipe']}" if task.get("recipe") else ""))
    print(f"status:      {task['status']}" + (" (forced)" if task.get("forced") else ""))
    print(f"mode:        {'isolated worktree' if task.get('worktree_path') else 'not isolated'}")
    print(f"base:        {task['base_branch']} @ {task['base_commit'][:12]}")
    if task.get("worktree_path"):
        print(f"worktree:    {task['worktree_path']} (branch: {task['branch']})")
    print()
    _print_steps(d)
    print()
    _print_checks(acc)
    print()
    if task["status"] not in ("accepted", "discarded"):
        names, stat, dirty = _diff_lines(root, task)
        pending = f"{len(names)} file(s) changed" + (f", {len(dirty)} uncommitted" if dirty else "")
        print(f"Pending diff: {pending if names or dirty else 'none'}" + (f" ({stat})" if stat else ""))
    print(f"Next: {NEXT_ACTIONS.get(task['status'], '-')}")


ACTIVE_STATUSES = ("running", "gate_passed", "gate_failed")


def cmd_board(args: argparse.Namespace) -> None:
    """Single dashboard listing all tasks.

    Tasks started directly via `/rig:rig` and tasks run in parallel via
    `/rig:queue go --provider rig` all land in the same `.rig/runs/`, so even
    with several tasks in flight you can **see the whole picture with one
    command instead of juggling terminals** — structurally solving
    "I forgot what I was doing".
    """
    root = repo_root()
    base = runs_dir(root)
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))

    if not args.all:
        tasks = [t for t in tasks if t["status"] in ACTIVE_STATUSES]
    tasks.sort(key=lambda t: t["created_at"])

    scope = "all tasks" if args.all else "active"
    print(f"## rig board ({scope}: {len(tasks)})\n")
    if not tasks:
        print("No active tasks." if not args.all else "No tasks (.rig/runs/ is empty).")
        print("\nTo start a new task: /rig:rig \"<task>\"")
        return

    for t in tasks:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        steps = load_json(d / "steps.json", {"steps": []})["steps"]
        last_step = f"{steps[-1]['name']}({steps[-1]['status']})" if steps else "-"
        mode = "isolated" if t.get("worktree_path") else "not-isolated"

        print(f"[{t['status']:<11}] {t['task_id']}")
        print(f"    {t['input'][:70]}{'…' if len(t['input']) > 70 else ''}")
        print(f"    type={t['task_type']:<14} recipe={t.get('recipe') or '-':<14} "
              f"mode={mode:<13} step={last_step:<20} gate={gs}")
    if not args.all:
        print(f"\n({sum(1 for t in tasks if t['status'] == 'gate_failed')} gate_failed / "
              f"{sum(1 for t in tasks if t['status'] == 'gate_passed')} awaiting diff/accept)")
        print("Next actions: /rig:rig diff <task_id> · /rig:rig accept <task_id> · /rig:rig discard <task_id> --yes")


def cmd_log(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    entries = []
    if base.is_dir():
        for p in sorted(base.iterdir(), reverse=True):
            tj = p / "task.json"
            if tj.exists():
                entries.append(load_json(tj))
    entries = entries[: args.limit]
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return
    if not entries:
        print("No run history (.rig/runs/ is empty)")
        return
    print(f"## rig log (latest {len(entries)})\n")
    for t in entries:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": [], "presets": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        print(f"- {t['task_id']}  [{t['status']}]")
        print(f"    input: {t['input'][:60]}{'…' if len(t['input']) > 60 else ''}")
        print(f"    type: {t['task_type']}"
              + (f" / recipe: {t['recipe']}" if t.get("recipe") else "")
              + f" / gate: {gs}"
              + f" / created: {t['created_at']}")


def cmd_gates(_args: argparse.Namespace) -> None:
    print("## acceptance-gate presets (source of truth)\n")
    for name, criteria in GATE_PRESETS.items():
        print(f"### {name}")
        for c in criteria:
            print(f"  - {c}")
        print()
    print("### task_type → presets")
    for tt, presets in TASK_TYPES.items():
        print(f"  {tt}: {' + '.join(presets)}")


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


def cmd_review(args: argparse.Namespace) -> None:
    """Record per-persona verdicts for review tasks (used by stats for rubber-stamp detection)."""
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    with task_lock(root, task_id):
        d = run_dir(root, task_id)
        data = load_json(d / "review.json", {"task_id": task_id, "verdicts": []})
        by_persona = {v["persona"]: v for v in data["verdicts"]}
        for pair in args.set:
            if "=" not in pair:
                die(f"--set must be given as <persona>=<APPROVE|REJECT|APPROVE_WITH_CONDITIONS> (got: {pair!r})")
            persona, verdict = pair.split("=", 1)
            if verdict not in VALID_VERDICT:
                die(f"verdict '{verdict}' is invalid. Valid: {', '.join(VALID_VERDICT)}")
            by_persona[persona] = {"persona": persona, "verdict": verdict, "recorded_at": now_iso()}
        data["verdicts"] = list(by_persona.values())
        save_json(d / "review.json", data)
        print(f"{task_id} review verdicts: " + " ".join(f"{v['persona']}={v['verdict']}" for v in data["verdicts"]))


def _load_audit(root: pathlib.Path) -> list[dict]:
    p = audit_path(root)
    if not p.exists():
        return []
    events: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def cmd_audit(args: argparse.Namespace) -> None:
    """List force-bypass records from `.rig/audit.jsonl`.

    Separate from the "not overridable with --force" premise of
    accept_requirements, this audit log permanently records cases where an
    unmet gate was overridden with --force (evidence of the differentiator's
    physical strength).
    """
    root = repo_root()
    events = _load_audit(root)
    if args.action:
        events = [e for e in events if e.get("action") == args.action]
    if args.since:
        events = [e for e in events if (e.get("ts") or "")[:10] >= args.since]
    if not events:
        print("## rig audit\n\nNo records (entries are appended by `accept --force`).")
        return
    limit = args.limit if args.limit else len(events)
    shown = events[-limit:]
    print(f"## rig audit (latest {len(shown)} / {len(events)} total)\n")
    for e in shown:
        ts = e.get("ts", "?")
        action = e.get("action", "?")
        tid = e.get("task_id", "?")
        by = ", ".join(e.get("bypassed") or [])
        gate = e.get("gate_status", "?")
        print(f"  {ts}  {action:16s}  task={tid}")
        print(f"    bypassed: {by}  gate: {gate}")
        if e.get("failed_checks"):
            print(f"    failed: {', '.join(e['failed_checks'])}")


def cmd_stats(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))

    if args.last:
        m = re.match(r"^(\d+)d$", args.last)
        if not m:
            die(f"--last must be given as '<N>d' (e.g. 30d; got: {args.last!r})")
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        tasks = [t for t in tasks if datetime.datetime.fromisoformat(t["created_at"]) >= cutoff]

    if args.recipe:
        tasks = [t for t in tasks if t.get("recipe") == args.recipe]

    def _load_reviews(task_list: list[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for t in task_list:
            rj = base / t["task_id"] / "review.json"
            if rj.exists():
                out[t["task_id"]] = load_json(rj)
        return out

    if args.verifier:
        candidate_reviews = _load_reviews(tasks)
        tasks = [t for t in tasks
                 if any(v["persona"] == args.verifier
                        for v in candidate_reviews.get(t["task_id"], {}).get("verdicts", []))]

    # Read review.json only for the final task set after filtering (so the
    # stats are always rebuilt from the final set, never leaking the candidate
    # set from before --verifier was applied)
    review_by_task = _load_reviews(tasks)

    if not tasks:
        print("## rig stats\n\nNo matching runs (check the filters, or run `/rig \"<task>\"`)")
        return

    accepted = sum(1 for t in tasks if t["status"] == "accepted")
    discarded = sum(1 for t in tasks if t["status"] == "discarded")

    gate_counts: Counter[str] = Counter()
    for t in tasks:
        acc = load_json(base / t["task_id"] / "acceptance.json", {"checks": []})
        gate_counts[gate_status(acc) if acc.get("checks") else "skipped"] += 1
    failed_gate = gate_counts.get("failed", 0)

    recipe_counts = Counter(t.get("recipe") or f"(no recipe, {t['task_type']})" for t in tasks)

    verifier_stats: Counter[str] = Counter()
    verifier_rejects: Counter[str] = Counter()
    for tid, rv in review_by_task.items():
        for v in rv.get("verdicts", []):
            verifier_stats[v["persona"]] += 1
            if v["verdict"] == "REJECT":
                verifier_rejects[v["persona"]] += 1

    print("## rig stats\n")
    print(f"Runs: {len(tasks)}")
    print(f"Accepted: {accepted}")
    print(f"Discarded: {discarded}")
    print(f"Failed gate: {failed_gate}")

    print("\nMost used recipes:")
    for name, n in recipe_counts.most_common(5):
        print(f"- {name}: {n}")

    print("\nGate results:")
    for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped"):
        if gate_counts.get(status):
            print(f"- {status}: {gate_counts[status]}")

    if verifier_stats:
        print("\nVerifier behavior:")
        rubber_stamp_warnings = []
        for persona, runs in sorted(verifier_stats.items(), key=lambda kv: -kv[1]):
            rejects = verifier_rejects.get(persona, 0)
            print(f"- {persona}: {runs} runs, {rejects} rejects")
            if runs >= 5 and rejects == 0:
                rubber_stamp_warnings.append(f"{persona} has 0 rejects across {runs} runs. Possible rubber-stamp behavior.")
        if rubber_stamp_warnings:
            print("\nWarning:")
            for w in rubber_stamp_warnings:
                print(w)
    else:
        print("\nVerifier behavior: (none recorded. Record with `workbench.py review <task_id> --set <persona>=<verdict>` to include here)")

    audit_events = _load_audit(root)
    if args.last:
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        audit_events = [e for e in audit_events
                        if e.get("ts") and datetime.datetime.fromisoformat(e["ts"]) >= cutoff]
    force_events = [e for e in audit_events if e.get("action") == "accept_force"]
    if force_events:
        by_bypass: Counter[str] = Counter()
        for e in force_events:
            for name in e.get("bypassed", []):
                by_bypass[name] += 1
        print(f"\nForce bypass ({len(force_events)}): "
              "`accept --force` cannot bypass the hard preconditions of accept_requirements (structural strength); "
              "this records the cases where soft preconditions were overridden.")
        for name, n in by_bypass.most_common():
            print(f"- {name}: {n}")
        print("(see `workbench.py audit` for details)")


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
