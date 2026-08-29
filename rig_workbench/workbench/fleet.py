"""Read the cross-project run log — the half of rig's telemetry the board never opened.

Two run stores exist and Mission Control read only one. `.rig/runs/<task_id>/` is rich and
scoped to the current project; `~/.rig/runs.jsonl` is the append-only mirror every backend
writes, carrying `project` on each record, and until now only `rig-wb usage` ever read it. The
board's single-repository view was not a missing feature so much as an unopened file.

Three things this is careful about.

**A run is not a record.** `telemetry_append` fires once per invocation of the runner,
including when a run stops — so a run that stopped and was resumed appends a second record
under the same `run_id`. Collapsing to one row per run and reporting how many records back it
is the honest projection: the run is one thing, and the number of attempts it took is a fact
about it rather than a duplicate to hide. Records without a `run_id` predate identity and
cannot be collapsed at all, so each stands alone and says so.

**The log is written best-effort and must be read the same way.** Every writer swallows its
own failures by design, so a truncated final line is a normal thing to find. An unreadable
line is counted and skipped, never silently dropped: a board that quietly showed nine of ten
runs would be worse than one that showed nine and said so.

**Only the tail is read.** The board polls, and the log never shrinks. Reading it whole would
make the poll cost grow forever for a view that shows the newest rows.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from ..orchestrate import config

#: How much of the log's end to read. Generous next to a record (a few hundred bytes to a few
#: kilobytes), small next to a log that has been accumulating for months.
TAIL_BYTES = 1024 * 1024

#: Rows returned by default. The board shows the newest work; the whole history is what
#: `rig-wb usage` is for.
DEFAULT_LIMIT = 100


def _tail_lines(path: pathlib.Path, *,
                tail_bytes: int | None = None) -> tuple[list[str], bool]:
    """The last complete lines of `path`, and whether reading started mid-file.

    A read that begins mid-file almost certainly begins mid-record, so the first fragment is
    dropped. Returning the truncation flag rather than hiding it lets the caller say the view
    is a tail instead of implying it is everything.
    """
    # Resolved here, not captured as a default argument: a default binds at definition time,
    # so the module constant would stay the value it had at import and any caller that set it
    # would be quietly ignored — a knob that does nothing is worse than no knob.
    limit = TAIL_BYTES if tail_bytes is None else tail_bytes
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            start = max(0, size - limit)
            if start:
                stream.seek(start)
            payload = stream.read()
    except OSError:
        return [], False
    truncated = bool(start)
    text = payload.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if truncated and lines:
        lines = lines[1:]                   # a partial record is not a record
    return lines, truncated


def _row(record: dict[str, Any]) -> dict[str, Any]:
    steps = record.get("steps")
    steps = steps if isinstance(steps, list) else []
    return {
        "run_id": record.get("run_id"),
        "ts": record.get("ts"),
        "project": record.get("project"),
        "recipe": record.get("recipe"),
        "backend": record.get("backend"),
        "final": record.get("final"),
        "task_id": record.get("task_id"),
        "steps_total": record.get("steps_total"),
        "steps_passed": record.get("steps_passed"),
        "failure_mode": record.get("failure_mode"),
        "step_count": len(steps),
    }


def _sort_key(row: dict[str, Any], position: int) -> tuple[int, float, int]:
    """Sort by when the run was last heard from, falling back to file order.

    Position alone is wrong once rows are collapsed: a run that started yesterday and resumed
    ten minutes ago keeps the position of its *first* record while carrying the timestamp of
    its latest, so it would show a fresh time near the bottom of the board.

    The timestamp is parsed rather than compared as a string. These are written with
    `.astimezone().isoformat()`, so the offset is whatever was local to the machine that wrote
    it — `...T01:00:00+09:00` sorts after `...T02:00:00+00:00` lexically and before it in fact,
    and a cross-project log is precisely where records from different offsets meet.

    A timestamp that cannot be parsed sorts last among its neighbours rather than being
    guessed at, and file order breaks the tie.
    """
    stamp = row.get("ts")
    if isinstance(stamp, str):
        try:
            return (1, dt.datetime.fromisoformat(stamp).timestamp(), position)
        except ValueError:
            pass
    return (0, 0.0, position)


def _newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for _key, row in sorted(
        ((_sort_key(row, position), row) for position, row in enumerate(rows)),
        key=lambda pair: pair[0], reverse=True)]


def fleet_rows(*, limit: int = DEFAULT_LIMIT,
               path: pathlib.Path | None = None,
               tail_bytes: int | None = None) -> dict[str, Any]:
    """One row per run, newest first, across every project that has recorded one.

    `attempts` counts the records collapsed into a row — a run that stopped and resumed
    appends twice under one id, and that is worth showing rather than deduplicating away.
    """
    target = pathlib.Path(path) if path is not None else config.GLOBAL_RUNS_PATH
    lines, truncated = _tail_lines(target, tail_bytes=tail_bytes)

    unreadable = 0
    ordered: list[dict[str, Any]] = []
    by_run: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeError):
            unreadable += 1
            continue
        if not isinstance(record, dict):
            unreadable += 1
            continue
        row = _row(record)
        run_id = row["run_id"]
        if not isinstance(run_id, str) or not run_id:
            # Predates run ids: nothing to collapse it with, and pretending otherwise would
            # merge unrelated runs that happen to share a recipe.
            row["attempts"] = 1
            ordered.append(row)
            continue
        previous = by_run.get(run_id)
        if previous is None:
            row["attempts"] = 1
            by_run[run_id] = row
            ordered.append(row)
        else:
            previous.update({k: v for k, v in row.items() if k != "attempts"})
            previous["attempts"] += 1

    rows = _newest_first(ordered)[:limit]
    return {
        "path": str(target),
        "exists": target.exists(),
        "rows": rows,
        "projects": sorted({row["project"] for row in ordered
                            if isinstance(row["project"], str)}),
        "unreadable": unreadable,
        "truncated": truncated,
        "shown": len(rows),
        "total": len(ordered),
    }
