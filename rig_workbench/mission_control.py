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

from . import exitcodes
from .evidence import find_repo_root, fleet_shortfall, mission_control_snapshot
from .workbench.cockpit import _aggregate_token_usage
from .workbench.confidence import aggregate_drill_confidence
from .workbench.config import ACTIVE_STATUSES
from .workbench.reporting import force_bypass_counter, gate_status_counts, read_all_tasks
from .workbench.assurance import build_receipt
from .workbench.assurance_wiring import ABSENT, INVALID, UNREADABLE_FILE
from .workbench.state import _load_audit, runs_dir


def _operational_snapshot(root: pathlib.Path) -> dict:
    base = runs_dir(root)
    records = read_all_tasks(base)
    tasks = list(records.tasks)
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
        # Beside the total, never folded into it: a page that renders 52 where 55 records
        # exist has answered a question nobody asked. The fields are for callers that build
        # their own text; `tasks_unreadable_note` is the sentence itself, carried in the
        # snapshot so the static page and the live UI cannot word the same shortfall
        # differently — neither of them renders a total without it.
        "tasks_unreadable": list(records.unreadable),
        "tasks_unreadable_collection": records.collection_error,
        "tasks_unreadable_note": records.note().lstrip(" —") or None,
        "gate_counts": gates,
        "reviewer_confidence": confidence,
        "token_usage": _aggregate_token_usage(root),
        "force_bypass_count": force_count,
    }


#: What a task's assurance comparison can be. The three the receipt reports, and the three
#: reasons it has nothing to report. Kept apart all the way to the page: `unobservable` is not
#: a softer `unmet` — one says rig looked and what it found falls short, the other says it
#: cannot look — and "nobody asked for anything" is not a task that fell short of nothing.
ASSURANCE_STATES = ("assurance-complete", "assurance-incomplete", "assurance-unobservable",
                    ABSENT, UNREADABLE_FILE, INVALID)


def _assurance_snapshot(root: pathlib.Path) -> dict:
    """Every task's assurance comparison, copied from its receipt.

    This page decides nothing about assurance. `assurance_target.evaluate` runs once, inside
    the receipt, and what arrives here is already the answer; a dashboard that read the same
    files and reached its own would eventually disagree with the receipt about whether an
    assurance held, which is worse than either of them being wrong alone.

    So there is no rate here and no score. Counting what the receipt returned is reporting;
    dividing it by something would be this page grading the result, on a page whose whole
    claim is that it holds no verdicts of its own.
    """
    base = runs_dir(root)
    counts = {state: 0 for state in ASSURANCE_STATES}
    rows = []
    # The same list every other reader of the runs directory gets (#488). This section used
    # to enumerate the directories itself, because `read_all_tasks` raised on one malformed
    # record and would have died before the guard below could name it. It no longer does, and
    # a second enumeration here would be a second place for "what counts as a task" to be
    # decided — and the two could disagree on the page that shows both.
    records = read_all_tasks(base)
    unreadable_collection = records.collection_error
    # A directory whose record cannot be read is a task whose assurance cannot be read. It is
    # named here for the same reason it is named there: a row missing from a dashboard reads
    # as a task that has nothing to report.
    unreadable_tasks = list(records.unreadable)
    for task_id in [str(task["task_id"]) for task in records.tasks]:
        try:
            asked = build_receipt(root, task_id)["assurance_target"]
        except Exception:  # noqa: BLE001
            # One task whose state cannot be read must not take the page down with it. The
            # task is named rather than dropped: a row missing from a dashboard reads as a
            # task that has nothing to report.
            unreadable_tasks.append(str(task_id))
            continue
        if asked.get("observed"):
            counts[asked["status"]] = counts.get(asked["status"], 0) + 1
            rows.append({
                "task_id": task_id,
                "status": asked["status"],
                "met": asked["met"], "unmet": asked["unmet"],
                "unobservable": asked["unobservable"],
                "axes": asked["axes"],
            })
        else:
            state = asked.get("not_recorded")
            counts[state] = counts.get(state, 0) + 1
            if state != ABSENT:
                # Absent is the ordinary case and would be most of the list. A target that is
                # there and cannot be read is the one worth a row.
                rows.append({"task_id": task_id, "status": state,
                             "reason": asked.get("reason"), "axes": {}})
    return {"counts": counts, "tasks": rows, "unreadable_tasks": unreadable_tasks,
            "unreadable_collection": unreadable_collection}


def build_snapshot(root: pathlib.Path, *, since: str | None = None) -> dict:
    result = mission_control_snapshot(root, since=since)
    result["operations"] = _operational_snapshot(root)
    result["assurance"] = _assurance_snapshot(root)
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
            f'<td>{_esc(item["defects_caught"] if item["defects_caught"] is not None else "unmeasured")} '
            f'<span class="muted">/ {item["defects_measured_n"]} measured</span></td>'
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


#: How an outcome is read aloud. `unobservable` says what it is rather than borrowing a word
#: that would let it be counted with the shortfalls.
_OUTCOME_WORDS = {"met": "met", "unmet": "not met", "unobservable": "not observable"}


def _render_assurance(snapshot: dict) -> str:
    """Requested against achieved, per task, copied from each receipt."""
    section = snapshot["assurance"]
    counts = section["counts"]
    asked_for = counts["assurance-complete"] + counts["assurance-incomplete"] + \
        counts["assurance-unobservable"]
    tiles = "".join([
        _metric("assurance complete", counts["assurance-complete"],
                "everything asked for was recorded"),
        _metric("assurance incomplete", counts["assurance-incomplete"],
                "asked for, and the receipt records otherwise"),
        # Its own tile, never added to the one above. "we do not measure that" read as "we
        # measured it and it was insufficient" is the confusion `assurance_target` names as
        # the reason the outcome exists at all.
        _metric("not observable", counts["assurance-unobservable"],
                "asked for on an axis rig cannot answer"),
        _metric("no target recorded", counts[ABSENT],
                "nobody wrote down what was asked for"),
        _metric("target unreadable", counts[UNREADABLE_FILE] + counts[INVALID],
                "a target is there and cannot be read"),
    ])
    if section.get("unreadable_collection"):
        # Not the cold-start line. "Nobody has recorded a target" and "rig could not read the
        # run records at all" are different facts, and only one of them is about the targets.
        body = ('<div class="callout"><strong>The run records could not be read.</strong> '
                'These counts are not a statement about what was asked for — nothing was '
                f'looked at. {_esc(section["unreadable_collection"])}</div>')
    elif (not asked_for and not counts[UNREADABLE_FILE] and not counts[INVALID]
          and not section["unreadable_tasks"]):
        body = ('<div class="empty">No task has recorded an assurance target yet. Write one to '
                '<code>.rig/runs/&lt;task&gt;/assurance-target.json</code> and the comparison '
                'appears here — this page reports what was asked for, and asks for nothing on '
                'anyone\'s behalf.</div>')
    else:
        rows = []
        for task in section["tasks"]:
            if not task["axes"]:
                rows.append(
                    "<tr>"
                    f'<td><code>{_esc(task["task_id"])}</code></td>'
                    f'<td>{_esc(task["status"])}</td>'
                    f'<td colspan="2">{_esc(task.get("reason") or "")}</td>'
                    "</tr>")
                continue
            for axis, entry in sorted(task["axes"].items()):
                outcome = entry["outcome"]
                # `achieved` is `None` on the unobservable path by construction, and printing
                # the receipt's own reason there is the difference between "rig found this
                # insufficient" and "rig cannot answer this axis".
                recorded = (_esc(entry.get("reason") or "") if outcome == "unobservable"
                            else f'<code>{_esc(entry["achieved"])}</code>')
                rows.append(
                    "<tr>"
                    f'<td><code>{_esc(task["task_id"])}</code></td>'
                    f'<td>{_esc(axis)}: asked for <code>{_esc(entry["required"])}</code></td>'
                    f'<td class="{"bad" if outcome == "unmet" else ""}">'
                    f'{_esc(_OUTCOME_WORDS[outcome])}</td>'
                    f"<td>{recorded}</td>"
                    "</tr>")
        body = ('<div class="card table-wrap"><table><thead><tr><th>task</th><th>asked for'
                "</th><th>outcome</th><th>recorded</th></tr></thead><tbody>"
                + "".join(rows) + "</tbody></table></div>")
    unreadable = section["unreadable_tasks"]
    if unreadable and not asked_for and not counts[UNREADABLE_FILE] and not counts[INVALID]:
        # Every task that could have had a target was one this page could not read. Saying
        # "no task has recorded an assurance target yet" would be a verdict about the targets,
        # reached without looking at a single one.
        body = ('<div class="empty">Nothing could be read about what was asked for: every '
                'task state this page tried to open failed. These counts are not a statement '
                'about the targets.</div>')
    note = ""
    if unreadable:
        # Named, not dropped. A task missing from this table reads as a task with nothing to
        # report, which is the one thing an unreadable task is not.
        note = ('<div class="callout"><strong>State could not be read for '
                f'{len(unreadable)} task(s):</strong> '
                + _esc(", ".join(unreadable[:8]))
                + (" …" if len(unreadable) > 8 else "") + "</div>")
    return f'<div class="metric-grid">{tiles}</div>{body}{note}'


def _fleet_window(fleet: dict) -> str:
    """The detail line under the org conformance tile: the window, and what it could not read.

    The tile is one number for a whole fleet, which is the place a lost task record is least
    visible — it is averaged into a percentage before anyone sees it. `govern rollup` counts
    those records rather than dropping them (#493), so the tile says how many stand behind
    the number it shows, and names the projects whose runs directory it could not list at all
    — a project measured against no records still contributes a score to this average.
    """
    return f"window: {fleet.get('since_days', 90)} days" + fleet_shortfall(fleet)


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
{_metric('org conformance', f"{float(fleet.get('score', 0)):.0%}", _fleet_window(fleet))}
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
{_metric('active tasks', ops['tasks_active'], f"{ops['tasks_total']} total" + (f" · {ops['tasks_unreadable_note']}" if ops.get('tasks_unreadable_note') else ""))}
{_metric('gate failures', gate_failed, 'current recorded task history')}
{_metric('RIG tokens', total_tokens if usage else 'unmeasured', f"{usage.get('calls', 0)} metered calls" if usage else 'CLI providers may be unmetered')}
{_metric('production incident rate', production_rate, f"{prod['incidents']} incident(s) / {prod['outcomes_recorded']} recorded")}
{_metric('outcome coverage', production_coverage, f"{prod['outcomes_recorded']} / {prod['accepted_tasks']} accepted tasks")}
</div>

<h2>Reviewer confidence · measured, not asserted</h2>{_render_reviewers(ops)}

<h2>Real-project evidence · RIG vs bare</h2>{_render_field(snapshot)}

<h2>Assurance · asked for vs recorded</h2>{_render_assurance(snapshot)}

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


@exitcodes.guard
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
