"""workbench digest: periodic run-telemetry digest (issue #285).

`workbench.py digest [--period week|month] [--out <path>]` aggregates the
telemetry accumulated in the cwd project over a rolling window (week = last
7 days, month = last 30 days) so the trends are visible without anyone
actively running `stats`:

  .rig/runs.jsonl               orchestrate run records → counts by final status
  .rig/runs/*/task.json         workbench tasks → counts by task status
  .rig/runs/*/acceptance.json   gate pass/fail rates + most-failed criteria
  .rig/runs/*/review.json       rubber-stamp suspects (reuses the stats helpers)
  .rig/audit.jsonl              force-accept (accept --force) count
  .rig/drill-results.jsonl      reviewer detection rate (only when present)

Output is Markdown on stdout by default; `--out <path>` writes the same text
to a file instead. The aggregation deliberately REUSES reporting.py's stats
helpers (gate_status_counts / load_reviews / verifier_counters /
rubber_stamp_warnings / force_bypass_counter) so stats and digest cannot
drift apart (acceptance criterion of #285: no duplicated stats logic).
"""

import argparse
import datetime
import json
import pathlib
from collections import Counter

from .reporting import (force_bypass_counter, gate_status_counts, load_reviews,
                        read_all_tasks, rubber_stamp_warnings, verifier_counters)
from .state import _load_audit, load_json, maybe_repo_root, runs_dir

PERIOD_DAYS = {"week": 7, "month": 30}


def _parse_ts(value) -> datetime.datetime | None:
    """Best-effort ISO timestamp parse → aware datetime (naive assumed local)."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo else dt.astimezone(datetime.timezone.utc).astimezone()


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _in_period(rec_ts, cutoff: datetime.datetime) -> bool:
    dt = _parse_ts(rec_ts)
    return dt is not None and dt >= cutoff


def build_digest(root: pathlib.Path, period: str,
                 now: datetime.datetime | None = None) -> str:
    """Render the Markdown digest for `.rig/` telemetry under `root`."""
    days = PERIOD_DAYS[period]
    now = now or datetime.datetime.now().astimezone()
    cutoff = now - datetime.timedelta(days=days)
    iso = now.isocalendar()
    label = (f"{iso[0]}-W{iso[1]:02d}" if period == "week" else f"{now:%Y-%m}")

    lines: list[str] = []
    lines.append(f"# rig digest — {label} ({period}: {cutoff:%Y-%m-%d} → {now:%Y-%m-%d})")
    lines.append("")

    # ── orchestrate runs (.rig/runs.jsonl) ────────────────────────────────────
    runs = [r for r in _read_jsonl(root / ".rig" / "runs.jsonl") if _in_period(r.get("ts"), cutoff)]

    # ── workbench tasks (.rig/runs/*/…) ───────────────────────────────────────
    base = runs_dir(root)
    tasks = [t for t in read_all_tasks(base) if _in_period(t.get("created_at"), cutoff)]

    if not runs and not tasks:
        lines.append(f"No runs in period (last {days} days). Nothing to digest.")
        return "\n".join(lines) + "\n"

    lines.append("## Runs")
    lines.append("")
    lines.append(f"- Orchestrate runs (`.rig/runs.jsonl`): {len(runs)}")
    for final, n in Counter(str(r.get("final") or "?") for r in runs).most_common():
        lines.append(f"  - {final}: {n}")
    lines.append(f"- Workbench tasks (`.rig/runs/`): {len(tasks)}")
    for status, n in Counter(t.get("status") or "?" for t in tasks).most_common():
        lines.append(f"  - {status}: {n}")
    lines.append("")

    # ── gate pass/fail rates + most-failed criteria ───────────────────────────
    lines.append("## Acceptance gates")
    lines.append("")
    if tasks:
        gate_counts = gate_status_counts(base, tasks)
        evaluated = sum(n for s, n in gate_counts.items() if s not in ("pending", "skipped"))
        passed = gate_counts.get("passed", 0) + gate_counts.get("passed_with_warnings", 0)
        failed = gate_counts.get("failed", 0)
        if evaluated:
            lines.append(f"- Evaluated: {evaluated} — passed {passed} "
                         f"({passed / evaluated * 100:.0f}%), failed {failed} "
                         f"({failed / evaluated * 100:.0f}%)")
        else:
            lines.append("- No evaluated gates in period (all pending/skipped).")
        for status in ("passed", "passed_with_warnings", "failed", "pending", "skipped"):
            if gate_counts.get(status):
                lines.append(f"  - {status}: {gate_counts[status]}")

        failed_criteria: Counter[str] = Counter()
        for t in tasks:
            acc = load_json(base / t["task_id"] / "acceptance.json", {"checks": []})
            for c in acc.get("checks") or []:
                if isinstance(c, dict) and c.get("status") == "failed":
                    failed_criteria[c.get("name") or "?"] += 1
        if failed_criteria:
            lines.append("- Most-failed criteria:")
            for name, n in failed_criteria.most_common(5):
                lines.append(f"  - {name}: {n}")
        else:
            lines.append("- Most-failed criteria: (none failed)")
    else:
        lines.append("- No workbench tasks in period.")
    lines.append("")

    # ── force accepts (.rig/audit.jsonl) ──────────────────────────────────────
    audit_events = [e for e in _load_audit(root) if _in_period(e.get("ts"), cutoff)]
    n_force, by_bypass = force_bypass_counter(audit_events)
    lines.append("## Force accepts")
    lines.append("")
    lines.append(f"- `accept --force` in period: {n_force}")
    for name, n in by_bypass.most_common():
        lines.append(f"  - bypassed {name}: {n}")
    lines.append("")

    # ── rubber-stamp suspects (reuses the stats logic) ────────────────────────
    lines.append("## Rubber-stamp suspects")
    lines.append("")
    verifier_stats, verifier_rejects = verifier_counters(load_reviews(base, tasks))
    warnings = rubber_stamp_warnings(verifier_stats, verifier_rejects)
    if warnings:
        lines.extend(f"- {w}" for w in warnings)
    elif verifier_stats:
        lines.append("- None (every persona with ≥5 runs has at least one reject).")
    else:
        lines.append("- No review verdicts recorded in period.")

    # ── drill detection rate (only when drill telemetry exists) ───────────────
    drill_path = root / ".rig" / "drill-results.jsonl"
    if drill_path.exists():
        drills = [r for r in _read_jsonl(drill_path) if _in_period(r.get("ts"), cutoff)]
        detected = seeded = 0
        for r in drills:
            for s in r.get("scores") or []:
                if isinstance(s, dict):
                    try:
                        detected += int(s.get("detected") or 0)
                        seeded += int(s.get("seeded") or 0)
                    except (TypeError, ValueError):
                        continue
        lines.append("")
        lines.append("## Drill detection rate")
        lines.append("")
        if seeded:
            lines.append(f"- {detected / seeded * 100:.1f}% ({detected}/{seeded} seeds "
                         f"across {len(drills)} drill run(s))")
        else:
            lines.append("- No drill runs in period.")

    return "\n".join(lines) + "\n"


def cmd_digest(args: argparse.Namespace) -> None:
    # The digest only reads `.rig/` telemetry, so it works from the repo root
    # when inside a git checkout and falls back to plain cwd otherwise.
    root = maybe_repo_root() or pathlib.Path.cwd()
    text = build_digest(root, args.period)
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"digest written: {out}")
    else:
        print(text, end="")
