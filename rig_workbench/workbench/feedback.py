"""Production outcome feedback loop: record-commit / record-outcome / trace-commit (#289, #300).

`accept` lands a staged diff, so this package never sees the final commit SHA a human
creates. record-commit links task_id -> sha explicitly. record-outcome logs what actually
happened in production (ok/incident) — the real-world counterpart to drill's synthetic
detection rate. trace-commit reverse-looks-up a sha to its task, shows the original gate
prediction plus any recorded outcome, and drafts a revert plan (command + PR title/body)
when the outcome is "incident" — it doesn't create the PR or run the revert itself, that
stays a human/GH-tool step.
"""

import argparse

from .state import (die, gate_status, git, load_json, load_task, now_iso,
                    repo_root, resolve_task_id, runs_dir, save_json, save_task)


def cmd_record_commit(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    sha = args.sha or git(["rev-parse", "HEAD"], cwd=root).stdout.strip()
    task["commit_sha"] = sha
    save_task(d, task)
    print(f"Linked {task_id} to commit {sha[:12]}.")


def cmd_record_outcome(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, _task = load_task(root, task_id)
    outcome = {
        "task_id": task_id,
        "status": args.status,
        "note": args.note or "",
        "recorded_at": now_iso(),
    }
    save_json(d / "outcome.json", outcome)
    print(f"Recorded production outcome for {task_id}: {args.status}" + (f" ({args.note})" if args.note else ""))
    if args.status == "incident":
        print("\nNext step: `workbench.py trace-commit <sha>` to re-check this task's gate verdict "
              "and, if needed, draft a `git revert` plan.")


def cmd_trace_commit(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    if not base.is_dir():
        die("`.rig/runs/` doesn't exist (no run history)")
    sha = args.sha
    matches = []
    for p in base.iterdir():
        tj = p / "task.json"
        if not tj.exists():
            continue
        t = load_json(tj)
        recorded_sha = t.get("commit_sha") or ""
        if recorded_sha and (recorded_sha == sha or recorded_sha.startswith(sha) or sha.startswith(recorded_sha)):
            matches.append(t)
    if not matches:
        die(f"No task is linked to commit {sha} "
            f"(link one first with `workbench.py record-commit <task_id> {sha}`)")
    for t in matches:
        task_id = t["task_id"]
        d = base / task_id
        print(f"## rig trace-commit: {sha} -> {task_id}")
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        print(f"Gate prediction (at accept time): {gs}")
        prov_path = d / "provenance.json"
        if prov_path.exists():
            print(f"Signed provenance: {prov_path.relative_to(root)} "
                  f"(verify with `workbench.py verify-provenance {task_id}`)")
        outcome_path = d / "outcome.json"
        if outcome_path.exists():
            outcome = load_json(outcome_path)
            print(f"Recorded outcome: {outcome['status']}"
                  + (f" ({outcome['note']})" if outcome.get("note") else "")
                  + f" @ {outcome['recorded_at']}")
            if outcome["status"] == "incident":
                print("\n⚠ The gate passed but a production incident was recorded. Treat this as "
                      "drill calibration material (what did this gate/reviewer miss?).")
                print("\nRevert plan draft:")
                print(f"  git revert {sha}")
                print(f"  # PR title: Revert \"{task_id}\" (production incident)")
                print(f"  # PR body: {task_id} caused a production incident and is being reverted. "
                      f"Details: {outcome.get('note') or '(no note recorded)'}")
        else:
            print("Recorded outcome: none (record one with `workbench.py record-outcome`)")
