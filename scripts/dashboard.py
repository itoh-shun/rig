#!/usr/bin/env python3
"""rig dashboard — generate a self-contained HTML metrics page.

Reads `.rig/runs.jsonl` (append-only run log written by orchestrate.py /
workbench.py) and emits a single HTML file with inline CSS/SVG. No external
dependencies — safe to open offline.

Usage:
  python3 scripts/dashboard.py [--repo <path>] [--out <file>] [--limit N]
                               [--recipe <name>] [--since YYYY-MM-DD]

The dashboard shows:
  - KPI cards (total runs, recipes, DONE / ESCALATE ratio, retries)
  - Runs per day (sparkline bar chart)
  - Runs by recipe (horizontal bar chart)
  - Verifier vote counts (detects "rubber-stamp" reviewers with 0 REJECT)
  - Drill detection rate over time (`.rig/drill-results.jsonl`, /rig:drill; #266)
  - Acceptance-gate per-criterion failure counts (`.rig/runs/*/acceptance.json`; #266)
  - Recent runs table (last N)
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import pathlib
import statistics
import sys


# ── data loading ────────────────────────────────────────────────────────────


def find_repo_root(start: pathlib.Path) -> pathlib.Path:
    p = start.resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists() or (parent / ".rig").exists():
            return parent
    return p


def load_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_runs(runs_path: pathlib.Path) -> list[dict]:
    return load_jsonl(runs_path)


def filter_runs(runs: list[dict], recipe: str | None, since: str | None) -> list[dict]:
    def keep(r: dict) -> bool:
        if recipe and r.get("recipe") != recipe:
            return False
        if since:
            ts = r.get("ts", "")
            if ts and ts[:10] < since:
                return False
        return True

    return [r for r in runs if keep(r)]


# ── aggregation ─────────────────────────────────────────────────────────────


def kpi(runs: list[dict]) -> dict:
    total = len(runs)
    recipes = len({r.get("recipe") for r in runs if r.get("recipe")})
    done = sum(1 for r in runs if r.get("final") == "DONE")
    escalated = sum(1 for r in runs if r.get("final") == "ESCALATE")
    stopped = sum(1 for r in runs if r.get("final") == "STOPPED")
    retries = [int(r.get("retries") or 0) for r in runs]
    avg_retries = round(statistics.mean(retries), 2) if retries else 0.0
    return {
        "total": total,
        "recipes": recipes,
        "done": done,
        "escalated": escalated,
        "stopped": stopped,
        "avg_retries": avg_retries,
        "done_ratio": round(done / total * 100, 1) if total else 0.0,
    }


def by_day(runs: list[dict]) -> list[tuple[str, int]]:
    counts: collections.Counter[str] = collections.Counter()
    for r in runs:
        ts = r.get("ts") or ""
        if not ts:
            continue
        day = ts[:10]
        counts[day] += 1
    return sorted(counts.items())


def by_recipe(runs: list[dict]) -> list[tuple[str, int]]:
    counts: collections.Counter[str] = collections.Counter()
    for r in runs:
        counts[r.get("recipe") or "?"] += 1
    return counts.most_common()


def verifier_votes(runs: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    for r in runs:
        for st in r.get("steps") or []:
            for v in st.get("verdicts") or []:
                name = v.get("by") or "?"
                rec = stats.setdefault(name, {"total": 0, "ok": 0, "reject": 0})
                rec["total"] += 1
                if v.get("ok") is True:
                    rec["ok"] += 1
                elif v.get("ok") is False:
                    rec["reject"] += 1
    out: list[dict] = []
    for name, rec in stats.items():
        total = rec["total"]
        ok = rec["ok"]
        reject = rec["reject"]
        rubber_stamp = total >= 5 and reject == 0
        out.append({
            "name": name,
            "total": total,
            "ok": ok,
            "reject": reject,
            "reject_ratio": round(reject / total * 100, 1) if total else 0.0,
            "rubber_stamp": rubber_stamp,
        })
    return sorted(out, key=lambda x: -x["total"])


def recent(runs: list[dict], n: int) -> list[dict]:
    return list(reversed(runs[-n:]))


def drill_series(results: list[dict]) -> list[dict]:
    """Per /rig:drill run: overall detection rate (schema: facets/instructions/drill.md §③).

    Each `.rig/drill-results.jsonl` line is
    {"ts": …, "seeds": n, "scores": [{"reviewer": …, "detected": d, "seeded": s, …}]}.
    """
    out: list[dict] = []
    for r in results:
        scores = [s for s in (r.get("scores") or []) if isinstance(s, dict)]
        try:
            detected = sum(int(s.get("detected") or 0) for s in scores)
            seeded = sum(int(s.get("seeded") or 0) for s in scores)
        except (TypeError, ValueError):
            continue
        if seeded <= 0:
            continue
        out.append({
            "ts": str(r.get("ts") or "?"),
            "rate": round(detected / seeded * 100, 1),
            "detected": detected,
            "seeded": seeded,
            "reviewers": len(scores),
        })
    return out


def gate_criteria_failures(root: pathlib.Path) -> list[dict]:
    """Per-criterion acceptance-gate outcomes across `.rig/runs/*/acceptance.json`.

    acceptance.json schema (workbench state.py build_acceptance):
    {"task_id": …, "status": …, "checks": [{"name": …, "status":
    pending|passed|failed|warning|skipped, "detail": …}]}.
    """
    stats: dict[str, dict] = {}
    base = root / ".rig" / "runs"
    if base.is_dir():
        for acc_path in sorted(base.glob("*/acceptance.json")):
            try:
                acc = json.loads(acc_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for c in acc.get("checks") or []:
                if not isinstance(c, dict):
                    continue
                name = c.get("name") or "?"
                status = c.get("status")
                rec = stats.setdefault(name, {"evaluated": 0, "failed": 0, "warning": 0})
                if status in ("passed", "failed", "warning"):
                    rec["evaluated"] += 1
                if status == "failed":
                    rec["failed"] += 1
                elif status == "warning":
                    rec["warning"] += 1
    out = [{"name": name, **rec} for name, rec in stats.items() if rec["evaluated"]]
    return sorted(out, key=lambda x: (-x["failed"], -x["warning"], x["name"]))


# ── rendering ───────────────────────────────────────────────────────────────


CSS = """
:root {
  --bg: #f8fafc;
  --card: #ffffff;
  --ink: #0f172a;
  --dim: #64748b;
  --accent: #0d9488;
  --warn: #d97706;
  --bad: #dc2626;
  --border: #e2e8f0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b1220;
    --card: #111827;
    --ink: #f1f5f9;
    --dim: #94a3b8;
    --accent: #14b8a6;
    --warn: #f59e0b;
    --bad: #f87171;
    --border: #1f2937;
  }
}
* { box-sizing: border-box; }
body {
  font-family: 'Zen Kaku Gothic New', -apple-system, BlinkMacSystemFont, 'Segoe UI',
               Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: var(--bg);
  color: var(--ink);
  margin: 0;
  padding: 2rem 2rem 4rem;
  line-height: 1.6;
}
h1 { margin: 0 0 0.25rem; font-size: 1.75rem; }
h2 { margin: 2rem 0 0.75rem; font-size: 1.1rem; color: var(--dim); font-weight: 500;
     letter-spacing: 0.04em; text-transform: uppercase; }
.sub { color: var(--dim); margin: 0 0 2rem; font-size: 0.9rem; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 0.75rem; margin: 0 0 2rem; }
.kpi {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 1rem 1.25rem;
}
.kpi .label { color: var(--dim); font-size: 0.75rem; text-transform: uppercase;
              letter-spacing: 0.05em; }
.kpi .value { font-size: 2rem; font-weight: 700; margin-top: 0.25rem;
              font-variant-numeric: tabular-nums; }
.kpi .value.warn { color: var(--warn); }
.kpi .value.bad { color: var(--bad); }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 1rem 1.25rem;
}
.bar-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0;
           font-size: 0.9rem; }
.bar-row .label { flex: 0 0 12rem; font-family: 'JetBrains Mono', ui-monospace,
                  SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }
.bar-row .bar {
  height: 14px; background: var(--accent); border-radius: 3px; min-width: 2px;
}
.bar-row .n { color: var(--dim); font-variant-numeric: tabular-nums;
              min-width: 3rem; text-align: right; }
.spark {
  display: flex; align-items: flex-end; gap: 2px; height: 60px;
  padding: 0.5rem 0; overflow-x: auto;
}
.spark .bin { flex: 0 0 8px; background: var(--accent); border-radius: 1px;
              position: relative; }
.spark .bin:hover::after {
  content: attr(data-label); position: absolute; bottom: 100%; left: 50%;
  transform: translateX(-50%); background: var(--ink); color: var(--bg);
  padding: 2px 6px; border-radius: 3px; font-size: 10px; white-space: nowrap;
}
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { text-align: left; color: var(--dim); font-weight: 500; padding: 0.5rem 0.5rem;
     border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }
td { padding: 0.5rem 0.5rem; border-bottom: 1px solid var(--border);
     font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono', ui-monospace,
     SFMono-Regular, Menlo, monospace; font-size: 0.8rem; }
td.status-done { color: var(--accent); }
td.status-escalate { color: var(--bad); }
td.status-stopped { color: var(--warn); }
.badge { display: inline-block; padding: 1px 6px; border-radius: 999px;
         font-size: 0.7rem; background: var(--border); color: var(--dim); }
.badge.rubber-stamp { background: var(--warn); color: white; }
footer { margin-top: 3rem; color: var(--dim); font-size: 0.75rem; text-align: center; }
"""


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def render_kpi(k: dict) -> str:
    def value_class(name: str, v) -> str:
        if name == "escalated" and v > 0:
            return "bad"
        if name == "avg_retries" and isinstance(v, (int, float)) and v > 1:
            return "warn"
        return ""

    tiles = [
        ("total runs", k["total"], ""),
        ("recipes", k["recipes"], ""),
        ("DONE ratio", f"{k['done_ratio']}%", ""),
        ("escalated", k["escalated"], value_class("escalated", k["escalated"])),
        ("avg retries", k["avg_retries"], value_class("avg_retries", k["avg_retries"])),
    ]
    return "\n".join(
        f'<div class="kpi"><div class="label">{esc(label)}</div>'
        f'<div class="value {c}">{esc(v)}</div></div>'
        for label, v, c in tiles
    )


def render_bars(pairs: list[tuple[str, int]], label_width: str = "12rem") -> str:
    if not pairs:
        return '<p class="sub">no data</p>'
    top = max(v for _, v in pairs)
    rows = []
    for name, v in pairs:
        pct = int(round(v / top * 100)) if top else 0
        rows.append(
            f'<div class="bar-row"><span class="label">{esc(name)}</span>'
            f'<span class="bar" style="width:{pct}%"></span>'
            f'<span class="n">{v}</span></div>'
        )
    return "\n".join(rows)


def render_spark(days: list[tuple[str, int]]) -> str:
    if not days:
        return '<p class="sub">no data</p>'
    top = max(v for _, v in days)
    bins = []
    for d, v in days:
        h = int(round(v / top * 100)) if top else 0
        bins.append(
            f'<div class="bin" style="height:{h}%" data-label="{esc(d)}: {v}"></div>'
        )
    first = days[0][0] if days else ""
    last = days[-1][0] if days else ""
    total = sum(v for _, v in days)
    caption = f'<p class="sub">{esc(first)} → {esc(last)} · {total} runs total</p>'
    return f'<div class="spark">{"".join(bins)}</div>{caption}'


def render_verifiers(votes: list[dict]) -> str:
    if not votes:
        return '<p class="sub">no verdicts recorded</p>'
    rows = ['<tr><th>verifier</th><th>total</th><th>OK</th><th>REJECT</th><th>REJECT%</th><th></th></tr>']
    for v in votes:
        badge = '<span class="badge rubber-stamp">rubber stamp?</span>' if v["rubber_stamp"] else ""
        rows.append(
            f'<tr><td>{esc(v["name"])}</td><td>{v["total"]}</td>'
            f'<td>{v["ok"]}</td><td>{v["reject"]}</td>'
            f'<td>{v["reject_ratio"]}%</td><td>{badge}</td></tr>'
        )
    return f'<table>{"".join(rows)}</table>'


def render_drill(series: list[dict]) -> str:
    if not series:
        return ('<p class="sub">no data — run <code>/rig:drill</code> to measure reviewer '
                'detection rates (accumulates in <code>.rig/drill-results.jsonl</code>)</p>')
    bins = []
    for s in series:
        bins.append(
            f'<div class="bin" style="height:{int(round(s["rate"]))}%" '
            f'data-label="{esc(s["ts"])}: {s["rate"]}% ({s["detected"]}/{s["seeded"]} seeds, '
            f'{s["reviewers"]} reviewers)"></div>'
        )
    latest = series[-1]
    caption = (f'<p class="sub">{esc(series[0]["ts"])} → {esc(latest["ts"])} · '
               f'latest {latest["rate"]}% ({latest["detected"]}/{latest["seeded"]}) · '
               f'{len(series)} drill runs</p>')
    return f'<div class="spark">{"".join(bins)}</div>{caption}'


def render_gate_criteria(criteria: list[dict]) -> str:
    if not criteria:
        return ('<p class="sub">no data — acceptance-gate criteria accumulate in '
                '<code>.rig/runs/&lt;task&gt;/acceptance.json</code> as tasks pass the gate</p>')
    rows = ['<tr><th>criterion</th><th>evaluated</th><th>failed</th><th>warning</th>'
            '<th>fail%</th></tr>']
    for c in criteria:
        fail_ratio = round(c["failed"] / c["evaluated"] * 100, 1) if c["evaluated"] else 0.0
        fail_cls = ' class="status-escalate"' if c["failed"] else ""
        rows.append(
            f'<tr><td>{esc(c["name"])}</td><td>{c["evaluated"]}</td>'
            f'<td{fail_cls}>{c["failed"]}</td><td>{c["warning"]}</td>'
            f'<td>{fail_ratio}%</td></tr>'
        )
    return f'<table>{"".join(rows)}</table>'


def render_recent(rows: list[dict]) -> str:
    if not rows:
        return '<p class="sub">no data</p>'
    head = "<tr><th>ts</th><th>recipe</th><th>backend</th><th>final</th><th>steps</th><th>retries</th></tr>"
    body = []
    for r in rows:
        status = r.get("final", "?")
        cls = f"status-{status.lower()}" if status in ("DONE", "ESCALATE", "STOPPED") else ""
        body.append(
            f'<tr><td>{esc(r.get("ts", ""))}</td>'
            f'<td>{esc(r.get("recipe", "?"))}</td>'
            f'<td>{esc(r.get("backend", "?"))}</td>'
            f'<td class="{cls}">{esc(status)}</td>'
            f'<td>{r.get("steps_passed", 0)}/{r.get("steps_total", 0)}</td>'
            f'<td>{r.get("retries", 0)}</td></tr>'
        )
    return f'<table>{head}{"".join(body)}</table>'


def render(runs: list[dict], meta: dict,
           drill_results: list[dict] | None = None,
           gate_criteria: list[dict] | None = None) -> str:
    k = kpi(runs)
    days = by_day(runs)
    recipes = by_recipe(runs)
    votes = verifier_votes(runs)
    drill = drill_series(drill_results or [])
    recent_rows = recent(runs, meta.get("limit", 20))
    sub_bits = []
    if meta.get("recipe"):
        sub_bits.append(f"recipe={meta['recipe']}")
    if meta.get("since"):
        sub_bits.append(f"since={meta['since']}")
    sub_bits.append(f"generated={meta['generated']}")
    sub = " · ".join(sub_bits)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rig dashboard</title>
<style>{CSS}</style>
</head>
<body>
<h1>rig stats</h1>
<p class="sub">{esc(sub)}</p>

<div class="kpi-grid">
{render_kpi(k)}
</div>

<h2>runs / day</h2>
<div class="card">
{render_spark(days)}
</div>

<h2>runs by recipe</h2>
<div class="card">
{render_bars(recipes)}
</div>

<h2>verifiers · vote counts</h2>
<div class="card">
{render_verifiers(votes)}
</div>

<h2>drill · detection rate over time</h2>
<div class="card">
{render_drill(drill)}
</div>

<h2>acceptance gate · per-criterion failures</h2>
<div class="card">
{render_gate_criteria(gate_criteria or [])}
</div>

<h2>last {min(len(runs), meta.get("limit", 20))} runs</h2>
<div class="card">
{render_recent(recent_rows)}
</div>

<footer>rig dashboard · <code>scripts/dashboard.py</code></footer>
</body>
</html>
"""


# ── entry ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(prog="dashboard.py",
                                     description="rig HTML metrics dashboard")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd(),
                        help="repo root (default: cwd, auto-detected)")
    parser.add_argument("--out", type=pathlib.Path,
                        help="output HTML path (default: stdout)")
    parser.add_argument("--limit", type=int, default=20,
                        help="recent runs to show (default: 20)")
    parser.add_argument("--recipe", type=str,
                        help="filter runs by recipe name")
    parser.add_argument("--since", type=str,
                        help="only include runs with ts >= YYYY-MM-DD")
    args = parser.parse_args()

    root = find_repo_root(args.repo)
    runs_path = root / ".rig" / "runs.jsonl"
    runs = load_runs(runs_path)
    runs = filter_runs(runs, args.recipe, args.since)
    drill_results = load_jsonl(root / ".rig" / "drill-results.jsonl")
    gate_criteria = gate_criteria_failures(root)

    meta = {
        "limit": args.limit,
        "recipe": args.recipe,
        "since": args.since,
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "repo": str(root),
    }
    output = render(runs, meta, drill_results=drill_results, gate_criteria=gate_criteria)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"[OK] wrote {args.out} ({len(runs)} runs from {runs_path})", file=sys.stderr)
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
