"""Confidence-weighted gate via drill detection rate (#301).

Surfaces drill-measured detection rate per reviewer persona as a supplementary
signal alongside the existing pass/fail gate. Never changes gate logic itself:
task-scoped calls record `reviewer_confidence` into acceptance.json, and below
the confidence threshold an additional reviewer is *suggested* (printed), never
auto-dispatched. Unmeasured personas stay "unmeasured" rather than a fabricated
score.
"""

import argparse
import pathlib

from .digest import _read_jsonl
from .state import build_acceptance, load_json, load_task, repo_root, resolve_task_id, save_json

_CONFIDENCE_THRESHOLD = 0.7  # below this, flagged low-confidence and an extra reviewer is suggested


def aggregate_drill_confidence(root: pathlib.Path) -> dict[str, dict]:
    """Aggregate per-persona drill measurements (detected/seeded/false_positives) across
    every recorded drill run (pure function; the shared helper so nothing re-derives
    this aggregation independently)."""
    drill_path = root / ".rig" / "drill-results.jsonl"
    atk: dict[str, dict] = {}
    if not drill_path.exists():
        return atk
    for d in _read_jsonl(drill_path):
        for s in d.get("scores") or []:
            if not isinstance(s, dict):
                continue
            a = atk.setdefault(s.get("reviewer", "?"), {"detected": 0, "seeded": 0, "fp": 0})
            a["detected"] += s.get("detected", 0) or 0
            a["seeded"] += s.get("seeded", 0) or 0
            a["fp"] += s.get("false_positives", 0) or 0
    return atk


def cmd_confidence(args: argparse.Namespace) -> None:
    root = repo_root()
    atk = aggregate_drill_confidence(root)

    if not args.task_id and not args.persona:
        if not atk:
            print("No drill measurements yet (run `/rig:drill` to measure detection rate).")
            return
        print("## rig confidence (all personas, drill-measured)")
        for name, a in sorted(atk.items()):
            if a["seeded"]:
                rate = a["detected"] / a["seeded"]
                flag = "  ⚠ low confidence" if rate < _CONFIDENCE_THRESHOLD else ""
                print(f"  {name}: {rate:.0%}{flag}")
            else:
                print(f"  {name}: unmeasured")
        return

    task_id = resolve_task_id(root, args.task_id)
    d, task = load_task(root, task_id)
    rj = d / "review.json"
    reviewers = sorted({v["persona"] for v in load_json(rj, {"verdicts": []}).get("verdicts", [])}) if rj.exists() else []
    if not reviewers:
        print(f"task '{task_id}' has no review.json record "
              "(run `workbench.py review` to record reviewer verdicts first)")
        return

    confidences: dict[str, float | None] = {}
    for name in reviewers:
        a = atk.get(name)
        confidences[name] = round(a["detected"] / a["seeded"], 3) if (a and a["seeded"]) else None

    acc = load_json(d / "acceptance.json", build_acceptance(task_id, task["task_type"], root))
    acc["reviewer_confidence"] = confidences
    save_json(d / "acceptance.json", acc)

    print(f"## rig confidence: {task_id}")
    low = []
    for name, c in confidences.items():
        if c is None:
            print(f"  {name}: unmeasured")
        else:
            flag = "  ⚠ low confidence" if c < _CONFIDENCE_THRESHOLD else ""
            print(f"  {name}: {c:.0%}{flag}")
            if c < _CONFIDENCE_THRESHOLD:
                low.append(name)
    if low:
        print(f"\nLow-confidence reviewer(s): {', '.join(low)}. Consider bringing in an additional reviewer "
              f"(`workbench.py review {task_id} --set <extra persona>=<verdict>` to record one).")
