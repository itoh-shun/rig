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

**Dispatch is not detectable, and this was checked rather than assumed.** The obvious
wish is a `dispatch rate`: of the rig commands run, how many ran inside a subagent
rather than in the parent thread. Claude Code exports no signal that answers it —
checked against Claude Code 2.1.224 and 2.1.227, which is the clause to re-test before
trusting this paragraph on a much later version. The environment a Bash tool call
receives is built from a fixed set — `CLAUDECODE`,
`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION=1`, `CLAUDE_PID`, `AI_AGENT`,
`CLAUDE_EFFORT` — with no field for agent depth; `CLAUDE_CODE_CHILD_SESSION` is set
unconditionally, so the parent's own shell carries it too, and `CLAUDE_CODE_SESSION_ID`
is the *session's* id, which a subagent shares with its parent. Compared directly
inside one session, the parent's shell and its subagent's shell receive an identical
set of variables. Session transcripts are no better: rig cannot reliably tell which
transcript record is the invocation currently running (concurrent tool calls, colliding
command strings, write ordering, a private format).

So dispatch is left unmeasured on purpose. A guessed dispatch rate would be the failure
mode this repository already has a name for — a sensor that reports green on an axis it
never inspected is worse than no sensor, because it ends the search.

Records land in `.rig/context.jsonl` (gitignored, same tier as `runs.jsonl`). Reading
is `rig-wb wb context`, and that is the only reader: nothing else in rig consumes these
records, and no digest rolls them up.
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

# The budget line one invocation is judged against. A convention rig declares about
# itself, not a measured limit: nothing established that 2000 bytes is where an
# invocation starts hurting the parent. It is printed next to its verdict so a reader
# can disagree with the number rather than with the word "ok".
#
# Still not a gate. Nothing in rig consumes the verdict this produces — no acceptance
# gate, no exit code, no other flow reads it.
INVOCATION_BUDGET_BYTES = 2000

# The same budget line for one task's accumulated rig output, expressed as a multiple
# so the relationship stays visible. Also a convention and nothing more: no measurement
# established that ten invocation budgets' worth is the point where a task stopped
# dispatching. It is printed alongside the verdict for exactly that reason.
TASK_BUDGET_BYTES = 10 * INVOCATION_BUDGET_BYTES

# Above this, one invocation is worth naming in the report. Deliberately *not* the
# budget line, and higher than it, because the two answer different questions: the
# budget asks "did rig stay inside what it said it would", the report asks "which
# invocations are worth trimming first". One number cannot do both, and trying made the
# report useless — over a 164-invocation sample taken 2026-08-11 from the main
# checkout's `.rig/context.jsonl`, 55% of invocations cleared 2000 bytes and `wb status`
# alone peaked at 3593, so a section meant to surface the top emitters was listing the
# median. That is a correction of what this comment used to claim, which was that 2000
# was high enough to keep routine calls out.
#
# 8000 is the midpoint of the widest empty band in that sample: it splits into a clump
# at or below 6235 bytes and a clump at or above 11883, with nothing in between.
# Cutting mid-gap keeps the partition from moving because one run happened to land near
# an edge. Re-measure before trusting the number on a repository — or a worktree, which
# resolves to a different file — that runs a different mix of commands.
REPORT_THRESHOLD_BYTES = 8000


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


def _redact_argv(argv: list[str]) -> list[str]:
    """Remove goal bodies before the command telemetry closure can persist them."""
    redacted = []
    redact_next = False
    for argument in argv:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
        elif argument == "--goal":
            redacted.append(argument)
            redact_next = True
        elif argument.startswith("--goal="):
            redacted.append("--goal=[REDACTED]")
        else:
            redacted.append(argument)
    return redacted


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
        safe_argv = _redact_argv(argv)
        record = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "command": _command,
            "argv": safe_argv[:8],
            "task_id": _task_id_hint(safe_argv),
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

    Per-task entries carry the same shape as per-command ones plus the timestamps of
    the first and last invocation, which is what turns "this task was expensive" into
    "this task kept feeding the parent for six hours". `first_ts`/`last_ts` are computed
    here and never persisted; the persisted field is `ts`, and rig has written it on
    every line unconditionally since the file's first one — no record rig wrote lacks
    it. The undated case is still handled, because this is a pure function over dicts
    the caller supplies and `.rig/context.jsonl` is a plain text file anyone can append
    to or edit by hand. A record with no `ts` reports its span as unknown ("") rather
    than guessed.
    """
    total = sum(r.get("bytes", 0) for r in records)
    by_command: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    for record in records:
        entry = by_command.setdefault(record.get("command") or "?",
                                      {"bytes": 0, "calls": 0, "max": 0})
        size = record.get("bytes", 0)
        entry["bytes"] += size
        entry["calls"] += 1
        entry["max"] = max(entry["max"], size)
        if record.get("task_id"):
            task = by_task.setdefault(record["task_id"],
                                      {"bytes": 0, "calls": 0, "max": 0,
                                       "first_ts": "", "last_ts": ""})
            task["bytes"] += size
            task["calls"] += 1
            task["max"] = max(task["max"], size)
            stamp = record.get("ts")
            if stamp:
                # Undated records are skipped rather than folded in. Two things go
                # wrong without this: `min(None, None)` raises, taking `wb context`
                # down for the whole history over one bad line; and folding the record
                # in as "" instead sorts it below every real timestamp, so an undated
                # line arriving after dated ones resets the task's start to nothing.
                task["first_ts"] = min(task["first_ts"] or stamp, stamp)
                task["last_ts"] = max(task["last_ts"], stamp)
    return {
        "calls": len(records),
        "bytes": total,
        "by_command": dict(sorted(by_command.items(), key=lambda kv: -kv[1]["bytes"])),
        "by_task": dict(sorted(by_task.items(), key=lambda kv: -kv[1]["bytes"])),
    }


def budget_verdicts(records: list[dict], summary: dict) -> list[dict]:
    """Judge what was measured against the declared budgets, and nothing else.

    Two lines, both derived from numbers this module states out loud: how many single
    invocations went over `INVOCATION_BUDGET_BYTES`, and how many tasks went over
    `TASK_BUDGET_BYTES`. Each verdict carries the budget it was judged against so the
    report can print it — an "ok" whose threshold is invisible is unfalsifiable, and
    that is the shape of sensor this repository has already been burned by.

    Judged against the budgets, never against `REPORT_THRESHOLD_BYTES`. When those were one
    number the invocation verdict and the report's heavy section were the same
    predicate over the same set — the line could only read `ok` when the section was
    empty, so it printed one fact twice. They can now disagree, which is the only way
    either of them carries information.

    Returns dicts, not a pass/fail: the caller prints them. Deliberately nothing here
    exits non-zero or feeds a gate.
    """
    over_invocations = [r for r in records
                        if r.get("bytes", 0) >= INVOCATION_BUDGET_BYTES]
    over_tasks = [(task_id, entry) for task_id, entry in summary["by_task"].items()
                  if entry["bytes"] >= TASK_BUDGET_BYTES]
    return [
        {
            "label": "single invocation",
            "budget": INVOCATION_BUDGET_BYTES,
            "over": len(over_invocations),
            "checked": len(records),
            "worst": max((r.get("bytes", 0) for r in records), default=0),
            "unit": "invocation(s)",
        },
        {
            "label": "one task's total",
            "budget": TASK_BUDGET_BYTES,
            "over": len(over_tasks),
            "checked": len(summary["by_task"]),
            "worst": max((e["bytes"] for e in summary["by_task"].values()), default=0),
            "unit": "task(s)",
        },
    ]


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
