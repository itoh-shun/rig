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
from .detection_corpus import SCORER_VERSION

_CONFIDENCE_THRESHOLD = 0.7  # below this, flagged low-confidence and an extra reviewer is suggested


def aggregate_drill_confidence(root: pathlib.Path, corpus: str | None = None) -> dict[str, dict]:
    """Aggregate per-persona drill measurements (detected/seeded/false_positives) across
    every recorded drill run (pure function; the shared helper so nothing re-derives
    this aggregation independently).

    `corpus` (#270) filters runs by their seed-selection source ("standard" /
    "project"); rows without the field predate the distinction and count as
    "standard" (pre-#270 runs only ever used the shipped catalog). None = all.

    Rows are also filtered by `scorer_version`. A detection rate only means something
    against the rule that produced it, and the rule has changed in ways that move
    every number: version 3 stopped scoring loose prose entirely, after a paragraph
    asserting that each changed function was correct was measured at 4/5 to 5/5 on
    every shipped case. Summing across versions averages a reviewer's real work with
    a number that measured vocabulary placement. Rows with no tag predate the field
    and are dropped rather than counted as version 1 — they were written by at least
    two different rules and there is no way to tell which.

    Rows that did not adjudicate are dropped too (`adjudicated` absent or false).
    From version 4 a detection is credited only once the drill judge has confirmed the
    finding asserts the seeded defect rather than denying it, so a row scored without
    a judge — or with one that could not answer some pair — still carries the
    optimistic pre-judge count. Summing that here would publish a rate nobody
    measured, and it is the high direction that misleads: the attack this aggregate
    must not reward is a review claiming everything is fine.

    Rows whose judge made no live call are dropped too — `offline`, or `calls` of zero.
    A replayed or hand-supplied ledger is not a measurement taken, and the ledger is
    forgeable by anyone who can run `ledger_key`."""
    drill_path = root / ".rig" / "drill-results.jsonl"
    atk: dict[str, dict] = {}
    if not drill_path.exists():
        return atk
    for d in _read_jsonl(drill_path):
        if corpus is not None and d.get("corpus", "standard") != corpus:
            continue
        if d.get("scorer_version") != SCORER_VERSION:
            continue
        if not d.get("adjudicated"):
            continue
        # …and the verdicts have to have been produced, not replayed or supplied.
        # `Ledger.get` re-derives a verdict from the recorded output, which raised the
        # cost of forging one but did not close it: a reviewer wrote plausible judge
        # output into `raw` with `returncode: 0`, ran `--judge-offline`, and published
        # 86.7% detection with no provider call at all. A row whose judge made no call
        # is a replay of an earlier measurement or a fabrication, and neither is a new
        # data point for an aggregate. Replays remain useful — as replays.
        judge = d.get("judge")
        if not isinstance(judge, dict) or judge.get("offline") or not judge.get("calls"):
            continue
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
