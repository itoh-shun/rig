"""Record a finished workbench task as one line of run telemetry.

`.rig/runs.jsonl` was written by exactly one producer — `orchestrate`'s
`telemetry_append` — while `/rig:go`, the entry point a person actually types, kept
its state in `.rig/runs/<task_id>/` and appeared in that log not at all. Everything
built on the log inherited the blind spot: `rig-wb usage` reports how much rig is
used and structurally could not see workbench runs, so the answer came back
dominated by benchmarks and selftests.

SKILL.md §6 already said the non-orchestrate backends append the same format. It
assigned that to the model, in prose. This module assigns it to code.

Two fields have no source on this side and are recorded as `null` rather than zero:

  `retries`      a workbench task has no retry counter; a gate that fails leaves
                 the task in `gate_failed` for a human to act on.
  `token_usage`  nothing here meters providers.

`null` reads as "not measured", which is true, where `0` would read as "measured,
and it was none" — a claim this backend cannot make. Consumers already treat a
missing `token_usage` as unmeasured (`runs --cost` skips a row with no usage), so
this needs no special case downstream.
"""

import os
import pathlib

from ..orchestrate.runstate import append_run_record
from .state import load_json, now_iso, runs_dir, warn

# The workbench's own vocabulary for how a task ended, mapped onto the `final` values
# already present in the log. `discarded` has no counterpart there — a human throwing
# the work away is not a run that failed — so it keeps its own name rather than being
# folded into the nearest existing word.
_FINAL_BY_STATUS = {
    "accepted": "DONE",
    "discarded": "DISCARDED",
    "gate_failed": "BLOCKED",
}


def _steps_record(d: pathlib.Path) -> list[dict]:
    """`steps.json` plus `review.json`, in the shape the log's readers expect.

    The two files use this side's names (`name`, `persona`, `verdict`); the log uses
    `id`, `by`, `ok`. Translating here keeps the difference in one place instead of in
    every aggregator. Verdict polarity matches `reporting.verifier_counters`: REJECT is
    the only value that counts as a rejection, so the two views of the same review
    cannot disagree.
    """
    steps = (load_json(d / "steps.json", {"steps": []}) or {}).get("steps") or []
    verdicts = [
        {"by": v.get("persona") or "?", "ok": v.get("verdict") != "REJECT"}
        for v in (load_json(d / "review.json", {"verdicts": []}) or {}).get("verdicts") or []
    ]
    out = []
    for i, st in enumerate(steps):
        rec = {"id": st.get("name") or f"step-{i + 1}", "status": st.get("status"),
               "retries": None, "model": None, "verdicts": []}
        out.append(rec)
    # Reviews are recorded per task, not per step, so they attach to the last step —
    # the only placement that does not invent a step boundary the data does not have.
    if verdicts:
        if not out:
            out.append({"id": "review", "status": "passed", "retries": None,
                        "model": None, "verdicts": []})
        out[-1]["verdicts"] = verdicts
    return out


def record_task_run(root: pathlib.Path, task: dict, status: str) -> None:
    """Append one telemetry line for a task that has just reached a terminal state.

    Best-effort for the whole body, not only for the write. `append_run_record` swallows
    its own failures, but everything before it can raise on its own: a corrupt
    `steps.json` raises from `json.loads`, a `steps.json` holding a list raises from
    `.get`, and a missing run directory calls `die()`, which is a `SystemExit`. Any of
    those would propagate into the caller — which is a task that has already ended — and
    take down whatever the caller had not done yet. A log line is not worth that.
    """
    try:
        _record_task_run(root, task, status)
    except (Exception, SystemExit) as e:
        # One line, then carry on. Silence here would hide a telemetry path that is
        # broken for every task — which is the same shape as the under-count this
        # module exists to fix, and `usage`'s coverage note would blame the wrong cause.
        warn(f"run telemetry not recorded for {task.get('task_id')}: {e!r}")


def _record_task_run(root: pathlib.Path, task: dict, status: str) -> None:
    # Not `run_dir()`: it calls `die()`, which prints `[ERROR] task not found` to stderr
    # before raising. Swallowing the SystemExit leaves the message, so a fully successful
    # accept would end with a red error line about a failure that did not affect it.
    d = runs_dir(root) / task["task_id"]
    if not d.is_dir():
        raise FileNotFoundError(d)
    steps = _steps_record(d)
    # `root`, not the process-wide default: the log belongs to the repository the task
    # is in, which is not always the directory the CLI was started from.
    append_run_record(runs_path=root / ".rig" / "runs.jsonl", project=root, rec={
        "ts": now_iso(),
        "recipe": task.get("recipe") or f"(no recipe, {task.get('task_type')})",
        "backend": "workbench",
        # Same source orchestrate uses. Hard-coding "workbench" here would be a category
        # error — `backend` is which engine ran it, `invoker` is what launched the process
        # — and it would keep every workbench run out of the "via rig-wb" share even when
        # the rig-wb wrapper is exactly what set this.
        "invoker": os.environ.get("RIG_INVOKER") or "workbench",
        "final": _FINAL_BY_STATUS.get(status, status.upper()),
        "steps_total": len(steps),
        "steps_passed": sum(1 for s in steps if s.get("status") == "passed"),
        "retries": None,
        "escalated_at": None,
        "token_usage": None,
        "task_id": task["task_id"],
        # Absent rather than null when nothing was declared (#548). This log is read by
        # aggregation that treats a present key as a recorded fact, which is the rule
        # `perf` and `run_id` already follow — a null here would put every run without a
        # declared issue into a group of its own.
        **({"issue": task["issue"]} if isinstance(task.get("issue"), dict) else {}),
        # Same absent-not-null rule, and the same reason the issue block follows it. The log
        # carried no caller at all before this: measured across 370 records here, `caller`
        # appeared in none, so a board column for who invoked a run had nothing to read.
        **({"caller": task["caller"]} if isinstance(task.get("caller"), dict) else {}),
        "steps": steps,
    })
