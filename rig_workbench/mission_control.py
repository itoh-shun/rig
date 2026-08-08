"""Read-only HTML Mission Control for RIG.

This is deliberately a view, not a second control plane.  It reads the same
workbench/evidence/governance artifacts as the CLI and never accepts, discards,
approves, waives, or mutates a run.  A future interactive GUI can call the
existing commands after their normal policy checks; v1 keeps that trust
boundary visually obvious.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys

from .evidence import find_repo_root, mission_control_snapshot
from .workbench.cockpit import _aggregate_token_usage
from .workbench.confidence import aggregate_drill_confidence
from .workbench.config import ACTIVE_STATUSES
from .workbench.reporting import force_bypass_counter, gate_status_counts, read_all_tasks
from .workbench.state import _load_audit, runs_dir


def _operational_snapshot(root: pathlib.Path) -> dict:
    base = runs_dir(root)
    tasks = read_all_tasks(base)
    active = [task for task in tasks if task.get("status") in ACTIVE_STATUSES]
    gates = gate_status_counts(base, tasks) if tasks else {}
    raw_confidence = aggregate_drill_confidence(root)
    confidence = {}
    for name, values in sorted(raw_confidence.items()):
        seeded = int(values.get("seeded") or 0)
        confidence[name] = {
            "seeded": seeded,
            "detected": int(values.get("detected") or 0),
            "false_positives": int(values.get("fp") or 0),
            "detection_rate_pct": (
                round(int(values.get("detected") or 0) / seeded * 100, 1) if seeded else None
            ),
        }
    force_count, _ = force_bypass_counter(_load_audit(root))
    return {
        "tasks_total": len(tasks),
        "tasks_active": len(active),
        "gate_counts": gates,
        "reviewer_confidence": confidence,
        "token_usage": _aggregate_token_usage(root),
        "force_bypass_count": force_count,
    }


def build_snapshot(root: pathlib.Path, *, since: str | None = None) -> dict:
    result = mission_control_snapshot(root, since=since)
    result["operations"] = _operational_snapshot(root)
    return result


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt_pct(value: object) -> str:
    return "unmeasured" if value is None else f"{float(value):.1f}%"


def _metric(label: str, value: object, detail: str = "") -> str:
    return (
        '<div class="metric">'
        f'<div class="metric-label">{_esc(label)}</div>'
        f'<div class="metric-value">{_esc(value)}</div>'
        + (f'<div class="metric-detail">{_esc(detail)}</div>' if detail else "")
        + "</div>"
    )


def _render_core(snapshot: dict) -> str:
    stages = snapshot["core"]
    chunks = []
    for index, stage in enumerate(stages):
        chunks.append(
            '<div class="stage">'
            f'<div class="stage-number">{index + 1:02d}</div>'
            f'<div class="stage-name">{_esc(stage["label"])}</div>'
            f'<div class="stage-meaning">{_esc(stage["meaning"])}</div>'
            "</div>"
        )
        if index != len(stages) - 1:
            chunks.append('<div class="arrow" aria-hidden="true">→</div>')
    return '<div class="core-flow">' + "".join(chunks) + "</div>"


def _render_field(snapshot: dict) -> str:
    field = snapshot["field_study"]
    rows = []
    for arm in ("rig", "bare"):
        item = field["arms"][arm]
        rows.append(
            "<tr>"
            f'<td><span class="arm {arm}">{arm}</span></td>'
            f'<td>{item["n"]}</td>'
            f'<td>{_fmt_pct(item["incident_rate_pct"])}</td>'
            f'<td>{item["defects_caught"]} <span class="muted">/ {item["defects_measured_n"]} measured</span></td>'
            f'<td>{_esc(item["tokens_mean"] if item["tokens_mean"] is not None else "unmeasured")}</td>'
            f'<td>{_esc(item["minutes_mean"] if item["minutes_mean"] is not None else "unmeasured")}</td>'
            f'<td>{_esc(item["tokens_per_defect_caught"] if item["tokens_per_defect_caught"] is not None else "unmeasured")}</td>'
            "</tr>"
        )
    compare = field["comparison"]
    if compare.get("available"):
        compare_text = (
            f"Observed incident-rate delta (bare − rig): "
            f"{compare['incident_rate_delta_pp_bare_minus_rig']:+.2f} pp"
        )
    else:
        compare_text = "Record both RIG and bare observations before comparing the arms."
    return f"""
<div class="card table-wrap">
<table>
<thead><tr><th>arm</th><th>n</th><th>incident rate</th><th>defects caught</th><th>mean tokens</th><th>mean minutes</th><th>tokens / caught defect</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<div class="callout"><strong>{_esc(compare_text)}</strong><br>{_esc(field['note'])}</div>
</div>
"""


def _render_reviewers(operations: dict) -> str:
    reviewers = operations["reviewer_confidence"]
    if not reviewers:
        return '<div class="empty">Unmeasured — run /rig:drill to turn reviewer confidence into a number.</div>'
    rows = []
    for name, item in reviewers.items():
        rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td>"
            f"<td>{_fmt_pct(item['detection_rate_pct'])}</td>"
            f"<td>{item['detected']}/{item['seeded']}</td>"
            f"<td>{item['false_positives']}</td>"
            "</tr>"
        )
    return (
        '<div class="card table-wrap"><table><thead><tr><th>reviewer</th><th>detection</th>'
        '<th>seeded defects</th><th>false positives</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )


def _render_fleet(snapshot: dict) -> str:
    fleet = snapshot["fleet"]
    if not fleet.get("configured"):
        return (
            '<div class="empty">Fleet not configured. Save repository paths with '
            '<code>rig-evidence fleet-config --project ../repo-a --project ../repo-b</code>.</div>'
        )
    if fleet.get("error"):
        return f'<div class="empty bad">Fleet measurement error: {_esc(fleet["error"])}</div>'
    teams = fleet.get("teams") or {}
    team_rows = []
    for name, item in sorted(teams.items()):
        team_rows.append(
            "<tr>"
            f"<td>{_esc(name)}</td><td>{item.get('projects', 0)}</td>"
            f"<td>{float(item.get('score', 0)):.0%}</td>"
            f"<td>{_esc(', '.join(item.get('failing') or []) or '—')}</td>"
            f"<td>{_esc(', '.join(item.get('findings') or []) or '—')}</td>"
            "</tr>"
        )
    return f"""
<div class="metric-grid compact">
{_metric('projects', fleet.get('projects', 0))}
{_metric('org conformance', f"{float(fleet.get('score', 0)):.0%}", f"window: {fleet.get('since_days', 90)} days")}
</div>
<div class="card table-wrap"><table>
<thead><tr><th>team</th><th>projects</th><th>conformance</th><th>failing projects</th><th>findings</th></tr></thead>
<tbody>{''.join(team_rows)}</tbody></table></div>
"""


CSS = r"""
:root{color-scheme:light dark;--bg:#f6f7f9;--panel:#fff;--ink:#121417;--muted:#69707a;--line:#dde1e6;--soft:#eef1f4;--accent:#3d5afe;--good:#147d58;--bad:#b3261e}
@media(prefers-color-scheme:dark){:root{--bg:#0e1116;--panel:#151a21;--ink:#edf1f5;--muted:#9da7b3;--line:#2a313b;--soft:#1d242d;--accent:#8da2ff;--good:#6fd6aa;--bad:#ff8a84}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.45}main{max-width:1320px;margin:auto;padding:36px 28px 72px}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;margin-bottom:28px}.eyebrow{font:600 12px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.13em;text-transform:uppercase;color:var(--accent)}h1{font-size:34px;line-height:1.05;margin:7px 0 6px}h2{font-size:19px;margin:34px 0 12px}.muted,.metric-detail{color:var(--muted)}.repo{text-align:right;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}.core-flow{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr) auto minmax(0,1fr);gap:9px;align-items:stretch}.stage,.metric,.card,.empty{background:var(--panel);border:1px solid var(--line);border-radius:14px}.stage{padding:15px}.stage-number{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}.stage-name{font-size:18px;font-weight:720;margin:6px 0}.stage-meaning{font-size:12px;color:var(--muted)}.arrow{display:flex;align-items:center;color:var(--muted);font-size:22px}.metric-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.metric-grid.compact{grid-template-columns:repeat(2,minmax(0,220px));margin-bottom:10px}.metric{padding:15px}.metric-label{text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-size:10px;font-weight:700}.metric-value{font-size:26px;font-weight:760;margin-top:5px}.metric-detail{font-size:11px;margin-top:3px}.table-wrap{overflow:auto}.card{padding:4px 14px}table{width:100%;border-collapse:collapse;font-size:13px}th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:650;text-align:left}th,td{padding:11px 9px;border-bottom:1px solid var(--line);white-space:nowrap}tbody tr:last-child td{border-bottom:0}.arm{font:700 11px ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}.callout,.empty{padding:14px;margin:10px 0;font-size:12px}.callout{background:var(--soft);border-radius:10px;color:var(--muted)}.callout strong{color:var(--ink)}.bad{color:var(--bad)}code{font:11px ui-monospace,SFMono-Regular,Menlo,monospace}.safety{display:flex;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--panel);font-size:12px}.safety strong{color:var(--good)}footer{margin-top:42px;border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:11px}
@media(max-width:900px){.core-flow{grid-template-columns:1fr}.arrow{transform:rotate(90deg);justify-content:center;height:16px}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}header{display:block}.repo{text-align:left;margin-top:12px}}
"""


def render_html(snapshot: dict) -> str:
    ops = snapshot["operations"]
    prod = snapshot["production"]
    usage = ops["token_usage"]
    total_tokens = (usage.get("prompt_tokens", 0) or 0) + (usage.get("completion_tokens", 0) or 0)
    gate_failed = (ops.get("gate_counts") or {}).get("failed", 0)
    production_rate = _fmt_pct(prod["incident_rate_pct"])
    production_coverage = _fmt_pct(prod["outcome_coverage_pct"])
    generated = snapshot["generated_at"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RIG Mission Control</title><style>{CSS}</style></head><body><main>
<header><div><div class="eyebrow">RIG · read-only control plane</div><h1>Mission Control</h1><div class="muted">Quality is measured here. Mutating actions still go through the existing CLI and governance checks.</div></div><div class="repo">{_esc(snapshot['repo'])}<br>{_esc(generated)}</div></header>

<h2>Core contract</h2>{_render_core(snapshot)}

<h2>Now</h2><div class="metric-grid">
{_metric('active tasks', ops['tasks_active'], f"{ops['tasks_total']} total")}
{_metric('gate failures', gate_failed, 'current recorded task history')}
{_metric('RIG tokens', total_tokens if usage else 'unmeasured', f"{usage.get('calls', 0)} metered calls" if usage else 'CLI providers may be unmetered')}
{_metric('production incident rate', production_rate, f"{prod['incidents']} incident(s) / {prod['outcomes_recorded']} recorded")}
{_metric('outcome coverage', production_coverage, f"{prod['outcomes_recorded']} / {prod['accepted_tasks']} accepted tasks")}
</div>

<h2>Reviewer confidence · measured, not asserted</h2>{_render_reviewers(ops)}

<h2>Real-project evidence · RIG vs bare</h2>{_render_field(snapshot)}

<h2>Fleet governance · multiple repositories</h2>{_render_fleet(snapshot)}

<h2>Safety</h2><div class="safety"><strong>READ ONLY</strong><span>force-bypass records: {ops['force_bypass_count']}. Accept / discard / approve / waiver are intentionally not buttons in this UI.</span></div>
<footer>schema {_esc(snapshot['schema'])} · generated from existing .rig evidence, workbench state, drill results and governance conformance · no resident service, no new policy engine</footer>
</main></body></html>"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-mission-control", description="read-only RIG Mission Control")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--out", type=pathlib.Path,
                        help="HTML output (default: <repo>/.rig/mission-control.html)")
    parser.add_argument("--since", help="only field-study observations since YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="emit the exact presentation-neutral snapshot")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = find_repo_root(args.repo)
    try:
        snapshot = build_snapshot(root, since=args.since)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return
        out = args.out or (root / ".rig" / "mission-control.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(snapshot), encoding="utf-8")
        print(f"[OK] wrote {out}")
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
