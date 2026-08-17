"""workbench reporting: status/board/log/gates/audit/stats rendering (split from scripts/workbench.py)."""

import argparse
import datetime
import json
import pathlib
import re
from collections import Counter

from .. import jsonio
from .progress import from_state as progress_from_state
from .progress import next_action
from .config import (ACTIVE_STATUSES, CHECK_ICON, GATE_PRESETS, NEXT_ACTIONS,
                     STEP_ICON, TASK_TYPES)
from .state import (_diff_lines, _load_audit, budget_status, build_acceptance,
                    die, drift_lines, effective_base, gate_status, load_json,
                    load_project_gates, load_task,
                    maybe_repo_root, repo_root, resolve_task_id, runs_dir)


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
        origin = " [project]" if c.get("origin") == "project" else ""
        detail = f" — {c['detail']}" if c.get("detail") else ""
        print(f"  {CHECK_ICON[c['status']]} {c['name']}{origin}{detail}")
        for line in c.get("api_diff") or []:
            print(f"      api: {line}")
        for line in c.get("secret_findings") or []:
            print(f"      secret: {line}")
        for line in c.get("tamper_findings") or []:
            print(f"      tamper: {line}")
        for line in c.get("injection_findings") or []:
            print(f"      inject: {line}")
        for line in c.get("anchor_findings") or []:
            print(f"      anchor: {line}")


def cmd_status(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    acc["status"] = gate_status(acc)

    print(f"## rig status: {task_id}")
    print(f"task:        {task['input']}")
    print(f"type:        {task['task_type']}" + (f" / recipe: {task['recipe']}" if task.get("recipe") else ""))
    print(f"status:      {task['status']}" + (" (forced)" if task.get("forced") else ""))
    print(f"mode:        {'isolated worktree' if task.get('worktree_path') else 'not isolated'}")
    print(f"base:        {task['base_branch']} @ {task['base_commit'][:12]}")
    # The recorded value above stays as recorded; drift (#312) is reported next to it,
    # and only when there is any.
    eff_base, drifted_from = effective_base(root, task)
    for line in drift_lines(task, drifted_from, eff_base, indent="             "):
        print(line)
    if task.get("worktree_path"):
        print(f"worktree:    {task['worktree_path']} (branch: {task['branch']})")
    if task.get("budget_minutes"):
        elapsed, budget, over = budget_status(task)
        print(f"budget:      {elapsed:.0f}min / {budget:.0f}min" + ("  ⚠ over budget" if over else ""))
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

    waiting_on_you: list[str] = []
    waiting_on_others: list[str] = []
    for t in tasks:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        step_state = load_json(d / "steps.json", {"steps": []})
        steps = step_state["steps"]
        prog = progress_from_state(step_state)
        mode = "isolated" if t.get("worktree_path") else "not-isolated"
        action = next_action(t, prog, gs)

        # Seeded runs get a real position; runs registered before seeding existed have
        # no denominator, so they keep echoing the last name reported rather than
        # being given a fabricated one.
        if prog.known:
            where = f"[{prog.bar}] {prog.label(width=14)}"
        else:
            where = f"{steps[-1]['name']}({steps[-1]['status']})" if steps else "-"

        _, _, over_budget = budget_status(t)
        print(f"[{t['status']:<11}] {t['task_id']}" + ("  ⚠ over budget" if over_budget else ""))
        print(f"    {t['input'][:70]}{'…' if len(t['input']) > 70 else ''}")
        print(f"    {t.get('recipe') or t['task_type']:<14} {where:<26} "
              f"gate={gs:<20} {action}")
        if mode != "isolated":
            # Worth a line of its own: an un-isolated task writes into the working
            # tree as it goes, which is the one case where "discard" is not free.
            print(f"    ⚠ {mode}")
        if action.startswith("→"):
            waiting_on_you.append(t["task_id"])
        elif action.startswith("⏸"):
            waiting_on_others.append(t["task_id"])
    if not args.all:
        # The count that decides whether this screen needs anything from the reader.
        # A list of tasks that does not say which ones are yours is a list you have to
        # open one by one, which is the thing `board` exists to replace.
        print(f"\nあなた待ち {len(waiting_on_you)} / 他人待ち {len(waiting_on_others)} / "
              f"実行中 {len(tasks) - len(waiting_on_you) - len(waiting_on_others)}")
        if waiting_on_you:
            print("Next actions: /rig:rig diff <task_id> · /rig:rig accept <task_id> "
                  "· /rig:rig discard <task_id> --yes")


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
    # SKILL.md points here for the acceptance-criteria IDs, and until now the only
    # way to read them was to parse this listing. `--json` is the first adopter of
    # the envelope contract (`rig_workbench/jsonio.py`) precisely because there was
    # no prior JSON shape here to break.
    if getattr(_args, "json", False):
        root = maybe_repo_root()
        gates = load_project_gates(root) if root else {}
        jsonio.emit("gates", {
            "presets": {name: list(criteria) for name, criteria in GATE_PRESETS.items()},
            "task_types": {tt: list(presets) for tt, presets in TASK_TYPES.items()},
            # Project extensions are additive only and never remove a criterion, so
            # they are reported beside the presets rather than merged into them —
            # a consumer can tell which the repository added.
            "project_extra_criteria": {
                target: list(crits) for target, crits in (gates.get("extra_criteria") or {}).items()
            },
            "project_descriptions": dict(gates.get("descriptions") or {}),
        })
        return
    print("## acceptance-gate presets (source of truth)\n")
    for name, criteria in GATE_PRESETS.items():
        print(f"### {name}")
        for c in criteria:
            print(f"  - {c}")
        print()
    print("### task_type → presets")
    for tt, presets in TASK_TYPES.items():
        print(f"  {tt}: {' + '.join(presets)}")

    # Project-level extensions (.rig/gates.json; origin: project) — shown only
    # inside a git repo that declares them (additive only, never removals).
    root = maybe_repo_root()
    gates = load_project_gates(root) if root else {}
    extra = gates.get("extra_criteria") or {}
    if extra:
        descs = gates.get("descriptions") or {}
        print("\n### project extra criteria (.rig/gates.json, origin: project)")
        for target, crits in extra.items():
            for c in crits:
                print(f"  {target} + {c}" + (f" — {descs[c]}" if c in descs else ""))


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


# ── stats helpers (shared with digest.py — issue #285: reuse, don't duplicate) ──
def read_all_tasks(base: pathlib.Path) -> list[dict]:
    """Load every `.rig/runs/*/task.json` under the runs dir."""
    tasks: list[dict] = []
    if base.is_dir():
        for p in sorted(base.iterdir()):
            tj = p / "task.json"
            if tj.exists():
                tasks.append(load_json(tj))
    return tasks


def load_reviews(base: pathlib.Path, task_list: list[dict]) -> dict[str, dict]:
    """Map task_id → review.json contents for the tasks that recorded verdicts."""
    out: dict[str, dict] = {}
    for t in task_list:
        rj = base / t["task_id"] / "review.json"
        if rj.exists():
            out[t["task_id"]] = load_json(rj)
    return out


def gate_status_counts(base: pathlib.Path, tasks: list[dict]) -> Counter:
    """Counter of evaluated gate statuses across the given tasks."""
    counts: Counter[str] = Counter()
    for t in tasks:
        acc = load_json(base / t["task_id"] / "acceptance.json", {"checks": []})
        counts[gate_status(acc) if acc.get("checks") else "skipped"] += 1
    return counts


def verifier_counters(review_by_task: dict[str, dict]) -> tuple[Counter, Counter]:
    """(runs per persona, REJECTs per persona) from recorded review verdicts."""
    verifier_stats: Counter[str] = Counter()
    verifier_rejects: Counter[str] = Counter()
    for rv in review_by_task.values():
        for v in rv.get("verdicts", []):
            verifier_stats[v["persona"]] += 1
            if v["verdict"] == "REJECT":
                verifier_rejects[v["persona"]] += 1
    return verifier_stats, verifier_rejects


def rubber_stamp_warnings(verifier_stats: Counter, verifier_rejects: Counter) -> list[str]:
    """Personas with enough runs and zero rejects — possible rubber-stamps."""
    return [f"{persona} has 0 rejects across {runs} runs. Possible rubber-stamp behavior."
            for persona, runs in sorted(verifier_stats.items(), key=lambda kv: -kv[1])
            if runs >= 5 and verifier_rejects.get(persona, 0) == 0]


def force_bypass_counter(audit_events: list[dict]) -> tuple[int, Counter]:
    """(number of accept_force events, Counter of bypassed criteria)."""
    force_events = [e for e in audit_events if e.get("action") == "accept_force"]
    by_bypass: Counter[str] = Counter()
    for e in force_events:
        for name in e.get("bypassed", []):
            by_bypass[name] += 1
    return len(force_events), by_bypass


def cmd_stats(args: argparse.Namespace) -> None:
    root = repo_root()
    base = runs_dir(root)
    tasks = read_all_tasks(base)

    if args.last:
        m = re.match(r"^(\d+)d$", args.last)
        if not m:
            die(f"--last must be given as '<N>d' (e.g. 30d; got: {args.last!r})")
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        tasks = [t for t in tasks if datetime.datetime.fromisoformat(t["created_at"]) >= cutoff]

    if args.recipe:
        tasks = [t for t in tasks if t.get("recipe") == args.recipe]

    if args.verifier:
        candidate_reviews = load_reviews(base, tasks)
        tasks = [t for t in tasks
                 if any(v["persona"] == args.verifier
                        for v in candidate_reviews.get(t["task_id"], {}).get("verdicts", []))]

    # Read review.json only for the final task set after filtering (so the
    # stats are always rebuilt from the final set, never leaking the candidate
    # set from before --verifier was applied)
    review_by_task = load_reviews(base, tasks)

    if not tasks:
        print("## rig stats\n\nNo matching runs (check the filters, or run `/rig \"<task>\"`)")
        return

    accepted = sum(1 for t in tasks if t["status"] == "accepted")
    discarded = sum(1 for t in tasks if t["status"] == "discarded")

    gate_counts = gate_status_counts(base, tasks)
    failed_gate = gate_counts.get("failed", 0)

    recipe_counts = Counter(t.get("recipe") or f"(no recipe, {t['task_type']})" for t in tasks)

    verifier_stats, verifier_rejects = verifier_counters(review_by_task)

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
        for persona, runs in sorted(verifier_stats.items(), key=lambda kv: -kv[1]):
            print(f"- {persona}: {runs} runs, {verifier_rejects.get(persona, 0)} rejects")
        warnings = rubber_stamp_warnings(verifier_stats, verifier_rejects)
        if warnings:
            print("\nWarning:")
            for w in warnings:
                print(w)
    else:
        print("\nVerifier behavior: (none recorded. Record with `workbench.py review <task_id> --set <persona>=<verdict>` to include here)")

    audit_events = _load_audit(root)
    if args.last:
        cutoff = datetime.datetime.now().astimezone() - datetime.timedelta(days=int(m.group(1)))
        audit_events = [e for e in audit_events
                        if e.get("ts") and datetime.datetime.fromisoformat(e["ts"]) >= cutoff]
    n_force, by_bypass = force_bypass_counter(audit_events)
    if n_force:
        print(f"\nForce bypass ({n_force}): "
              "`accept --force` cannot bypass the hard preconditions of accept_requirements (structural strength); "
              "this records the cases where soft preconditions were overridden.")
        for name, n in by_bypass.most_common():
            print(f"- {name}: {n}")
        print("(see `workbench.py audit` for details)")
