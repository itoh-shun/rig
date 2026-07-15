"""workbench lifecycle: task registration and progress recording — new/step/gate/review
(split from scripts/workbench.py)."""

import argparse
import pathlib
import re
import sys

from .config import (CHECK_ICON, TASK_TYPES, VALID_CRITERION_STATUS,
                     VALID_STEP_STATUS, VALID_VERDICT)
from .hardening import apply_tamper_sensor
from .injection import apply_injection_sensor
from .schema_diff import apply_schema_sensor
from .secrets import apply_secret_sensor
from .state import (build_acceptance, current_branch, default_worktree_path,
                    die, gate_status, git, load_json, load_task, make_slug,
                    make_task_id, now_iso, repo_root, resolve_task_id, run_dir,
                    runs_dir, save_json, save_task, task_lock)


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


_STOPWORDS = {"の", "を", "に", "は", "が", "で", "と", "も", "て", "た", "する", "して", "ください",
              "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are"}


def _tokenize(text: str) -> set[str]:
    """Rough tokenization (contiguous alphanumerics, everything else char-by-char). A lightweight
    heuristic that skips bringing in a real morphological analyzer — a hint of overlap is enough,
    exact matching isn't the goal."""
    words = re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def find_similar_tasks(root: pathlib.Path, text: str, exclude_task_id: str | None = None,
                       limit: int = 3, threshold: float = 0.25) -> list[dict]:
    """Return past tasks whose `input` is most similar (Jaccard coefficient over rough
    tokenization) to `text`, highest first (#290, deja-vu detection). No dedicated
    embeddings/search engine is brought in — this is just a lightweight scan of task.json."""
    base = runs_dir(root)
    if not base.is_dir():
        return []
    query = _tokenize(text)
    if not query:
        return []
    scored: list[tuple[float, dict]] = []
    for p in base.iterdir():
        tj = p / "task.json"
        if not tj.exists():
            continue
        t = load_json(tj)
        if t["task_id"] == exclude_task_id:
            continue
        candidate = _tokenize(t.get("input", ""))
        if not candidate:
            continue
        overlap = query & candidate
        union = query | candidate
        score = len(overlap) / len(union) if union else 0.0
        if score >= threshold:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:limit]]


def cmd_new(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.type not in TASK_TYPES:
        die(f"task_type '{args.type}' is invalid. Valid: {', '.join(TASK_TYPES)}")
    slug = args.slug or make_slug(args.input)
    task_id = make_task_id(slug)
    d = runs_dir(root) / task_id
    if d.exists():
        die(f"task '{task_id}' already exists")

    # Compose the gate first: a malformed `.rig/gates.json` must abort here,
    # before any run dir / worktree is created (no partial state on error).
    acc = build_acceptance(task_id, args.type, root)

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

    similar = find_similar_tasks(root, args.input, exclude_task_id=task_id)
    if similar:
        print("\nSimilar tasks (past runs, deja-vu detection #290):")
        for t in similar:
            label = t["input"][:50] + ("…" if len(t["input"]) > 50 else "")
            print(f"  - {t['task_id']} ({t['status']}): {label}")


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
        acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))

        known = {c["name"]: c for c in acc["checks"]}
        explicit_set: set[str] = set()
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
            explicit_set.add(name)

        # Machine sensor (issue #288): verify public_api_changes_documented
        # against the actual base↔worktree OpenAPI diff before evaluating.
        sensor_notes = apply_schema_sensor(root, d, task, acc)
        # Machine sensor (issue #273): diff-scoped secret scan backing
        # no_secret_leak. Fail-grade: findings block accept; an explicit
        # --set no_secret_leak=passed in this invocation is the escape hatch.
        sensor_notes += apply_secret_sensor(root, d, task, acc, explicit_set=explicit_set)
        # Anti-tamper sensor: gate/CI-config edits in the diff are fail-grade,
        # test-weakening patterns warning-grade; --set no_gate_tampering=passed
        # is the recorded escape hatch (tamper_override).
        sensor_notes += apply_tamper_sensor(root, d, task, acc, explicit_set=explicit_set)
        # Injection-marker sensor: invisible Unicode is fail-grade,
        # instruction-override phrases warning-grade; --set
        # no_injection_markers=passed is the recorded escape hatch.
        sensor_notes += apply_injection_sensor(root, d, task, acc, explicit_set=explicit_set)

        acc["status"] = gate_status(acc)
        acc["checked_at"] = now_iso()
        save_json(d / "acceptance.json", acc)

        if task["status"] == "running" and acc["status"] in ("passed", "passed_with_warnings", "failed", "skipped"):
            task["status"] = "gate_failed" if acc["status"] == "failed" else "gate_passed"
            save_task(d, task)

        print(f"## acceptance-gate: {task_id}  [{acc['status'].upper()}]")
        print(f"presets: {' + '.join(acc['presets'])}")
        for c in acc["checks"]:
            origin = " [project]" if c.get("origin") == "project" else ""
            detail = f" — {c['detail']}" if c.get("detail") else ""
            print(f"  {CHECK_ICON[c['status']]} {c['name']}{origin}{detail}")
        for note in sensor_notes:
            print(note)
        if acc["status"] == "failed":
            sys.exit(1)


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
