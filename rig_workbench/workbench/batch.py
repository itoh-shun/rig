"""What a finished `queue go` batch actually leaves on your desk.

`queue go` printed one `[DONE]`/`[FAIL]` line per item and a `3/4 done` tally. Both
are about the *queue's* bookkeeping, and neither is the thing the operator needs next:
`DONE` here means "the gate settled and the verifier passed", which is not "merged" and
not even "nothing left to do" — every one of those tasks is sitting in an isolated
worktree waiting for a human to look at the diff and accept or discard it. A four-item
batch that reports `4/4 done` and leaves four undisclosed decisions behind is the same
defect as a green build that never ran the tests: the number is true and it answers a
question nobody asked.

So the summary regroups the batch by **the move each item is waiting on**, reusing the
exact `next_action` wording `board` uses — one vocabulary for "whose turn is it", not
two that drift apart.

**Linking is evidence-based, and its absence is reported.** A queue item becomes a
workbench task only inside the provider's own session, so the only trace back is the
task id the provider printed. When it is there, the item is grouped by that task's real
state; when it is not, the item is listed under "could not link" rather than being
folded into a bucket on a guess. A wrong attribution in a screen whose whole purpose is
"which of these needs me" is worse than an admitted gap.
"""

from __future__ import annotations

import pathlib
import re

from .progress import from_state as progress_from_state
from .progress import next_action
from .state import gate_status, load_json, runs_dir

# `make_task_id`: rig-<YYYYMMDD>-<HHMMSS>-<slug>. Anchored to that exact shape so a
# stray "rig-queue" or "rig-running" label quoted in provider output cannot match.
TASK_ID_RE = re.compile(r"\brig-\d{8}-\d{6}-[a-z0-9][a-z0-9-]*")

# Buckets, strongest claim on the reader first. `next_action` returns free text, so the
# order is decided by its leading marker rather than by the sentence.
_ORDER = {"→": 0, "⏸": 1, "…": 2, "済": 3}


def find_task_id(text: str) -> str:
    """The workbench task a provider run produced, or "".

    The *last* match wins: the provider transcript quotes the id many times (register,
    step, gate, board hint) and a retry inside the same session registers a second task.
    The last one mentioned is the run whose state the operator is being pointed at.
    """
    matches = TASK_ID_RE.findall(text or "")
    return matches[-1] if matches else ""


def task_action(root: pathlib.Path, task_id: str) -> tuple[str, dict] | None:
    """`(action, task)` for a registered task, or None when it is not on disk.

    None is not an error — it is the honest answer for an id that was printed but never
    registered (a provider that failed before `new`, or a hallucinated id). Callers
    report it as unlinked.
    """
    directory = runs_dir(root) / task_id
    try:
        task = load_json(directory / "task.json", {})
        if not task.get("task_id"):
            return None
        acceptance = load_json(directory / "acceptance.json", {"checks": []})
        gate = gate_status(acceptance) if acceptance.get("checks") else "-"
        step_state = load_json(directory / "steps.json", {"steps": []})
    except (OSError, ValueError, KeyError, TypeError):
        # A half-written run directory is an unlinkable item, not a crash in the
        # summary that runs after every batch.
        return None
    return next_action(task, progress_from_state(step_state), gate), task


def group_batch(root: pathlib.Path, results: list[dict]) -> dict:
    """Regroup finished queue items by the move each one is waiting on.

    `results` items carry `id`, `task`, `ok` and optionally `task_id` (whatever
    `find_task_id` recovered). Returns `{"groups": {action: [row, ...]}, "unlinked":
    [row, ...], "failed": [row, ...]}` — `failed` being items the queue itself could not
    settle, which need a queue-level decision (`retry`) and not a diff review.
    """
    groups: dict[str, list[dict]] = {}
    unlinked: list[dict] = []
    failed: list[dict] = []
    for result in results:
        row = dict(result)
        if not result.get("ok"):
            failed.append(row)
            continue
        resolved = task_action(root, result.get("task_id") or "") if result.get("task_id") else None
        if resolved is None:
            unlinked.append(row)
            continue
        action, task = resolved
        row["workbench_status"] = task.get("status")
        groups.setdefault(action, []).append(row)
    ordered = dict(sorted(groups.items(), key=lambda kv: (_ORDER.get(kv[0][:1], 9), kv[0])))
    return {"groups": ordered, "unlinked": unlinked, "failed": failed}


def render_batch(grouped: dict) -> list[str]:
    """The lines `queue go` prints after its tally. Empty when there is nothing to add."""
    groups, unlinked, failed = grouped["groups"], grouped["unlinked"], grouped["failed"]
    if not (groups or unlinked or failed):
        return []
    out = ["", "次にやること（バッチが残した判断）"]
    for action, rows in groups.items():
        out.append(f"  {action}  ({len(rows)})")
        for row in rows:
            out.append(f"    #{row['id']}  {row['task_id']}")
            out.append(f"        {_clip(row.get('task'))}")
    if groups:
        out.append("    → /rig:rig diff <task_id> · /rig:rig accept <task_id> "
                   "· /rig:rig discard <task_id> --yes")
    if failed:
        out.append(f"  ✗ キュー側で失敗（差分レビュー以前）  ({len(failed)})")
        for row in failed:
            out.append(f"    #{row['id']}  {_clip(row.get('task'))}")
        out.append("    → 原因を確認して `queue retry <id>`")
    if unlinked:
        # Named rather than hidden: these items ran, and the summary genuinely does not
        # know what they left behind. Silently omitting them would make the batch look
        # smaller than it was.
        out.append(f"  ? task id を出力に残さず、状態を確認できませんでした  ({len(unlinked)})")
        for row in unlinked:
            out.append(f"    #{row['id']}  {_clip(row.get('task'))}")
        out.append("    → `/rig:rig board` で確認")
    return out


def _clip(text, width: int = 66) -> str:
    text = str(text or "")
    return text[:width] + ("…" if len(text) > width else "")
