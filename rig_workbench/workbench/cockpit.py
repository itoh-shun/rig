"""workbench cockpit: read-only Mission Control dashboard (issue #307).

Aggregates run timeline, gate radar, drill-measured reviewer confidence, a
cost meter, and a force-bypass safety strip onto one screen by reusing
board/stats/audit/confidence's existing aggregation functions — no new
persistence, no duplicated logic. v1 is read-only: accept/discard stay in
the existing commands, cockpit only recommends. Missing data (no drill run,
no token usage recorded) is shown as "unmeasured" rather than a blank that
could be misread as healthy.
"""

import argparse
import json
import pathlib

from .config import ACTIVE_STATUSES
from .confidence import aggregate_drill_confidence
from .reporting import force_bypass_counter, gate_status_counts, read_all_tasks
from .state import _load_audit, gate_status, load_json, repo_root, runs_dir


def _aggregate_token_usage(root: pathlib.Path) -> dict:
    """Sum token usage across every recorded orchestrate run (`.rig/runs.jsonl`,
    the same telemetry `orchestrate.py runs --cost` reads — #271/#296).
    A single running total for the cockpit's one-screen view; the full
    per-recipe/per-provider breakdown stays in `orchestrate.py runs --cost`."""
    path = root / ".rig" / "runs.jsonl"
    if not path.exists():
        return {}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cache_read_input_tokens": 0, "calls": 0}
    any_usage = False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for usage in (rec.get("token_usage") or {}).values():
            any_usage = True
            totals["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
            totals["completion_tokens"] += usage.get("completion_tokens", 0) or 0
            totals["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
            totals["calls"] += usage.get("calls", 0) or 0
    return totals if any_usage else {}


def cmd_cockpit(_args: argparse.Namespace) -> None:
    """Aggregate board/gate/drill/audit onto one read-only Mission Control screen (#307).

    No new resident service or database — this reads the existing
    `.rig/runs/`, `drill-results.jsonl`, `runs.jsonl`, and `audit.jsonl`, and
    nothing else. Destructive operations (accept/discard) don't happen here;
    cockpit only points at the existing command to run next.
    """
    root = repo_root()
    base = runs_dir(root)
    tasks = read_all_tasks(base)
    tasks.sort(key=lambda t: t["created_at"])
    active = [t for t in tasks if t["status"] in ACTIVE_STATUSES]

    print("━━━ rig cockpit — Mission Control (read-only) ━━━━━━━━━━━━━━")

    # ── Run timeline ──────────────────────────────────────────────────────
    print(f"\n┌─ Run timeline ({len(active)} active / {len(tasks)} total)")
    if not active:
        print("│ No active tasks.")
    for t in active:
        d = base / t["task_id"]
        acc = load_json(d / "acceptance.json", {"checks": []})
        gs = gate_status(acc) if acc.get("checks") else "-"
        label = t["input"][:44] + ("…" if len(t["input"]) > 44 else "")
        print(f"│ [{t['status']:<11}] {t['task_id']:<28} gate={gs:<20} {label}")

    # ── Gate radar ────────────────────────────────────────────────────────
    print("├─ Gate radar")
    if tasks:
        gate_counts = gate_status_counts(base, tasks)
        for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped"):
            if gate_counts.get(status):
                print(f"│ {status}: {gate_counts[status]}")
    else:
        print("│ No runs yet.")

    # ── Reviewer confidence (drill-measured) ───────────────────────────────
    print("├─ Reviewer confidence (drill-measured)")
    atk = aggregate_drill_confidence(root)
    if not atk:
        print("│ Unmeasured (run `/rig:drill` to see per-persona detection rate).")
    else:
        for name, a in sorted(atk.items()):
            if a["seeded"]:
                rate = a["detected"] / a["seeded"] * 100
                fp = f", {a['fp']} false positive(s)" if a["fp"] else ""
                print(f"│ {name}: {rate:.0f}% detection ({a['detected']}/{a['seeded']}{fp})")
            else:
                print(f"│ {name}: unmeasured")

    # ── Cost meter (#271/#296) ──────────────────────────────────────────────
    print("├─ Cost meter")
    usage = _aggregate_token_usage(root)
    if not usage:
        print("│ Unmeasured (no token usage recorded yet — HTTP providers "
              "ollama/lmstudio/anthropic are metered automatically; claude/codex CLI providers "
              "don't expose structured usage. See `orchestrate.py runs --cost` for the full breakdown).")
    else:
        total = usage["prompt_tokens"] + usage["completion_tokens"]
        cache = f", cache_read={usage['cache_read_input_tokens']}" if usage["cache_read_input_tokens"] else ""
        print(f"│ {usage['calls']} call(s), prompt={usage['prompt_tokens']}, "
              f"completion={usage['completion_tokens']}, total={total}{cache}")

    # ── Safety strip ──────────────────────────────────────────────────────
    print("├─ Safety strip")
    n_force, _ = force_bypass_counter(_load_audit(root))
    if n_force:
        print(f"│ force-bypass: {n_force} (details: `workbench.py audit`)")
    else:
        print("│ No force-bypass records.")

    # ── Next action rail ────────────────────────────────────────────────────
    gate_passed = [t for t in active if t["status"] == "gate_passed"]
    gate_failed = [t for t in active if t["status"] == "gate_failed"]
    print("└─ Next action rail")
    if gate_passed:
        ids = ", ".join(t["task_id"] for t in gate_passed[:3])
        more = " …" if len(gate_passed) > 3 else ""
        print(f"  {len(gate_passed)} awaiting diff/accept: {ids}{more}")
        print("    -> `workbench.py diff <id>` / `workbench.py accept <id>`")
    if gate_failed:
        ids = ", ".join(t["task_id"] for t in gate_failed[:3])
        more = " …" if len(gate_failed) > 3 else ""
        print(f"  {len(gate_failed)} gate not met: {ids}{more}")
        print("    -> fix the unmet criteria and re-evaluate, or `workbench.py discard <id> --yes`")
    if not gate_passed and not gate_failed:
        print("  No action needed right now.")

    if active:
        print("\nEvidence: each task's plan.md / diff.md / acceptance.json / review.json "
              "live under .rig/runs/<task-id>/.")
