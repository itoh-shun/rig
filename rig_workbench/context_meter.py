"""How much of the parent session's context rig itself is spending.

`context-minimal` is called a hard rule in SKILL.md §6 — "実作業は必ず subagent に
dispatch する", "親コンテキストに長い tool 出力を引き込まない" — and it is stated 152
times across this repository. Nothing checks it, and nothing counts it. By rig's own
`harness-taxonomy` that is two of the named holes at once: enforcement that stops at
prose, and a rule shipped without measurement. A discipline nobody counts degrades
without anyone noticing, which is exactly what "the context feels tighter lately"
sounds like from the inside.

**What this measures, precisely.** Every byte a rig command prints is fed back to the
parent as a tool result, so rig's stdout *is* rig's contribution to the parent's
context. That is the part rig is responsible for and the part it can observe, so that
is what gets counted — per invocation, attributed to a task when one is in scope.

**What it does not measure.** The session's total context, the conversation, files the
parent read on its own, or whether the parent actually dispatched to a subagent instead
of doing the work itself. rig runs as a subprocess and cannot see any of that. A number
that claimed to be "your context usage" would be a fabrication; this one claims only
"what rig printed at you", which is checkable and is the lever rig controls.

Records land in `.rig/context.jsonl` (gitignored, same tier as `runs.jsonl`). Reading
is `rig-wb wb context`; the weekly digest carries the rollup.
"""

from __future__ import annotations

import atexit
import datetime
import io
import json
import os
import pathlib
import sys

CONTEXT_REL = ".rig/context.jsonl"

# Above this, one invocation is worth naming in the report. Not a limit and not a
# gate: the point is to make the top emitters visible, and a threshold that fired on
# every `status` call would bury them.
NOTABLE_BYTES = 2000


class _CountingStream(io.TextIOBase):
    """Pass-through wrapper that tallies what was written.

    Deliberately a wrapper rather than a redirect: rig's output has to keep reaching
    the terminal unchanged and in real time. Counting must never become a reason for
    output to be buffered, reordered, or lost.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self.bytes = 0
        self.lines = 0

    def write(self, text: str) -> int:  # type: ignore[override]
        self.bytes += len(text.encode("utf-8", errors="replace"))
        self.lines += text.count("\n")
        return self._wrapped.write(text)

    def flush(self) -> None:  # type: ignore[override]
        self._wrapped.flush()

    def isatty(self) -> bool:  # type: ignore[override]
        try:
            return self._wrapped.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


_meter: _CountingStream | None = None
_command: str = ""


def _data_root() -> pathlib.Path | None:
    """This repository's `.rig/`, or None. Never creates one, never leaves the repo.

    The walk stops at the first directory holding a `.git`, because `.rig/` lives
    beside it. Walking further would let a command running inside an isolated task
    worktree — which has a `.git` *file* and no `.rig/` of its own — write telemetry
    into some ancestor's tree instead. Dropping an untracked file into a worktree is
    not a cosmetic mistake either: `teardown_isolation` decides whether to remove or
    preserve that worktree by whether it is dirty.

    A repository that has never run rig also should not acquire a telemetry file
    because somebody typed `rig-wb version`.
    """
    try:
        cwd = pathlib.Path.cwd().resolve()
    except OSError:
        return None
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".rig").is_dir():
            return candidate
        if (candidate / ".git").exists():
            return None                # repo root reached, and it has no .rig/
    return None


def _task_id_hint(argv: list[str]) -> str:
    """The task this invocation is about, when it says so on the command line.

    Only recognises the shape rig's own commands use (a bare `rig-<timestamp>-<slug>`
    argument). Guessing beyond that would attribute cost to the wrong task, and a
    wrong attribution is worse than none in a report meant to find the expensive one.
    """
    for arg in argv:
        if arg.startswith("rig-") and not arg.startswith("-"):
            return arg
    return ""


def install(command: str, argv: list[str] | None = None) -> None:
    """Start counting this invocation's stdout. Idempotent; safe to call unguarded."""
    global _meter, _command
    if _meter is not None or os.environ.get("RIG_NO_CONTEXT_METER"):
        return
    _command = command
    _meter = _CountingStream(sys.stdout)
    sys.stdout = _meter  # type: ignore[assignment]
    argv = list(argv if argv is not None else sys.argv[1:])
    atexit.register(_record, argv)


def _record(argv: list[str]) -> None:
    """Append this invocation's tally. Best-effort, like every other telemetry path:
    a metering failure must never be the reason a rig command reports an error."""
    meter = _meter
    if meter is None or meter.bytes == 0:
        return
    root = _data_root()
    if root is None:
        return
    try:
        record = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "command": _command,
            "argv": argv[:8],
            "task_id": _task_id_hint(argv),
            "bytes": meter.bytes,
            "lines": meter.lines,
            "invoker": os.environ.get("RIG_INVOKER") or "direct",
        }
        path = root / CONTEXT_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


# ── reading ──────────────────────────────────────────────────────────────────
def load(root: pathlib.Path, since_days: int | None = None) -> list[dict]:
    path = root / CONTEXT_REL
    if not path.is_file():
        return []
    cutoff = ""
    if since_days is not None:
        cutoff = (datetime.datetime.now().astimezone()
                  - datetime.timedelta(days=since_days)).isoformat(timespec="seconds")
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and (record.get("ts") or "") < cutoff:
            continue
        out.append(record)
    return out


def summarize(records: list[dict]) -> dict:
    """Totals, the per-command breakdown, and the per-task breakdown.

    Sorted by bytes rather than call count on purpose: forty cheap `status` calls are
    not the problem, and one command that dumps a diff into the parent is.
    """
    total = sum(r.get("bytes", 0) for r in records)
    by_command: dict[str, dict] = {}
    by_task: dict[str, int] = {}
    for record in records:
        entry = by_command.setdefault(record.get("command") or "?",
                                      {"bytes": 0, "calls": 0, "max": 0})
        size = record.get("bytes", 0)
        entry["bytes"] += size
        entry["calls"] += 1
        entry["max"] = max(entry["max"], size)
        if record.get("task_id"):
            by_task[record["task_id"]] = by_task.get(record["task_id"], 0) + size
    return {
        "calls": len(records),
        "bytes": total,
        "by_command": dict(sorted(by_command.items(), key=lambda kv: -kv[1]["bytes"])),
        "by_task": dict(sorted(by_task.items(), key=lambda kv: -kv[1])),
    }


def human(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def approx_tokens(size: int) -> int:
    """A rough token count for the byte total.

    ~4 bytes per token is the usual English rule of thumb and rig's output is mostly
    ASCII identifiers, paths and table rows. It is reported as an approximation and
    labelled as one — the byte count is the measurement, this is the intuition.
    """
    return size // 4
