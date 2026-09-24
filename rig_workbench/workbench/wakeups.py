"""`wb wakeups` — count the wake-ups that told the parent nothing new.

Claude Code re-invokes the parent session when a background task finishes: a
`<task-notification>` arrives as a user turn. Under rig every such turn costs a
run-status header plus narration, so a notification that repeats nothing is a turn the
user pays for and learns nothing from.

This module reads Claude Code session transcripts (`*.jsonl`) and classifies every
task-notification row. The classification makes one staleness claim and no guesses:

1. ``failed`` — status `failed`. Never stale: a failure is always news.
2. ``killed`` — status `killed` or `stopped`. Never stale, for the same reason.
3. ``event`` — a notification with an `<event>` tag (a `Monitor` event), or one without a
   `<task-id>` or `<status>` (an untagged batch notice). Not a completion; excluded from
   the denominator and counted apart. Tags are read outside `<result>…</result>`, so a
   result that quotes `<event>` does not turn a completion into an event.
4. ``handback-dup`` — status `completed` and the whole `<result>` is the harness's own
   sentence saying the report was already delivered as a message from this task's id
   and is not repeated (`HANDBACK_SENTENCE`). The harness saying so is the evidence; a
   report that merely quotes the sentence, or an earlier handback message alone, is
   not, because a resumed subagent may deliver something new.
5. ``fresh`` — everything else, including a status this module does not know.

stale = handback-dup only. The denominator is every notification except ``event``.

Separately, and **not** a staleness claim, ``polls`` counts behaviour
`patterns/monitor.md` forbids: assistant tool_uses that name a background task's id or
its output file between the launch (or that task's previous notification) and its
notification. The launching tool_use and `SendMessage` (resuming a subagent is not
reading its output) are excluded. Whether a poll made the later notification redundant
is not knowable from the transcript — the output may have been incomplete when read — so
polls never make a notification stale and are not ratcheted. Known blind spots: a read
that reaches the file without naming it (a glob, `ls -t | head -1`, a variable, a parent
directory) is not counted, and a task id that happens to appear inside unrelated input is.

Rows are de-duplicated by `uuid` across every file (a resumed session repeats earlier
rows under the same uuids); rows without a uuid are kept. Paths are resolved first, so
one file reached twice (`../`, a symlink) is read once.

What it cannot see is stated in every report: only notifications recorded in the
transcripts it was given. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from typing import Any, Iterable

from .. import console
from .state import die, reject

SCHEMA = "rig.wakeups/v1"
CEILING_SCHEMA = "rig.wakeups-ceiling/v1"
DEFAULT_MIN_NOTIFICATIONS = 20
DEFAULT_CEILING_PATH = ".rig/wakeups-ceiling.json"

KINDS = ("handback-dup", "failed", "killed", "fresh", "event")
STALE_KINDS = ("handback-dup",)

#: Harness wording, not rig's: Claude Code's `<result>` for a subagent that already handed
#: back is exactly this sentence, with `{task_id}` the notification's own `<task-id>`. The
#: whole result must be the sentence, matched case-insensitively with whitespace
#: collapsed; a report that quotes it among other text is not the harness speaking. If
#: the harness rewords it, handback-dup drops to 0 and the ratchet passes trivially — so
#: a sudden 0 after a Claude Code upgrade is a reason to look here, not a win. Kept in
#: this one place.
HANDBACK_SENTENCE = ("This agent's report was delivered to you as a message from "
                     "\"{task_id}\" (its SubagentHandback call). Read it there; "
                     "it is not repeated here.")

#: Exit statuses of the ratchet. 3 is rig's existing "no verdict was reached" code
#: (`wb gate` / `wb contract` pending, the orchestrator parked on a human gate): the
#: sample could not be judged, which is neither a pass nor a rejection.
EXIT_OK, EXIT_OVER, EXIT_ERROR, EXIT_NOT_JUDGED = 0, 1, 2, 3

NOT_SEEN = ("Only task-notifications recorded in the given transcripts are counted. "
            "Transcripts not passed in, subagent transcripts, and wake-ups a session "
            "never wrote down are invisible here; a low number means few stale "
            "wake-ups in these files, not in every session. Notifications delivered "
            "mid-turn after a tool_result (queued_command attachments) are not "
            "wake-ups and are not counted.")

POLLS_NOTE = ("polls: tool_uses naming a background task's id or output file before its "
              "notification — what patterns/monitor forbids. Not a staleness claim and not "
              "ratcheted; reads through a glob, `ls -t`, a variable or a directory are not seen.")

_TAG_RE = {tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL)
           for tag in ("task-id", "tool-use-id", "output-file", "status", "result")}
_EVENT_RE = re.compile(r"<event[\s>]")


def _normalise(text: str) -> str:
    return " ".join(text.split()).lower()


def is_handback_sentence(result: str, task_id: str) -> bool:
    """True when the whole result is the harness's handback sentence for `task_id`."""
    return bool(task_id) and (_normalise(result)
                              == _normalise(HANDBACK_SENTENCE.format(task_id=task_id)))


# ── row access ────────────────────────────────────────────────────────────────

def parse_lines(lines: Iterable[str]) -> tuple[list[dict], int]:
    """Parse jsonl lines into row dicts. Blank lines are skipped; anything else that is
    not a JSON object is counted as unreadable rather than silently dropped."""
    rows: list[dict] = []
    unreadable = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if not isinstance(row, dict):
            unreadable += 1
            continue
        rows.append(row)
    return rows, unreadable


def _origin(row: dict) -> dict:
    origin = row.get("origin")
    return origin if isinstance(origin, dict) else {}


def _content(row: dict) -> Any:
    message = row.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(row: dict) -> list[dict]:
    content = _content(row)
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _text(row: dict) -> str:
    """A row's text whether its content is a plain string or a list of blocks."""
    content = _content(row)
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in _blocks(row)
                   if b.get("type") == "text" and isinstance(b.get("text"), str))


def _tag(text: str, tag: str) -> str:
    match = _TAG_RE[tag].search(text)
    return match.group(1).strip() if match else ""


def _is_notification(row: dict) -> bool:
    return _origin(row).get("kind") == "task-notification"


def _is_tool_result_row(row: dict) -> bool:
    blocks = _blocks(row)
    return bool(blocks) and all(b.get("type") == "tool_result" for b in blocks)


def _tool_uses(row: dict) -> list[dict]:
    if row.get("type") != "assistant":
        return []
    return [b for b in _blocks(row) if b.get("type") == "tool_use"]


def _reply_chars(rows: list[dict], start: int) -> int:
    """Visible assistant text after row `start`, up to the next user row that is not
    just tool results — what the user actually had to read for this wake-up."""
    total = 0
    for row in rows[start + 1:]:
        kind = row.get("type")
        if kind == "user":
            if _is_tool_result_row(row):
                continue
            break
        if kind == "assistant":
            total += sum(len(b["text"]) for b in _blocks(row)
                         if b.get("type") == "text" and isinstance(b.get("text"), str))
    return total


# ── classification ────────────────────────────────────────────────────────────

def _outside_result(text: str) -> str:
    """The notification with its `<result>` bodies removed: what the harness wrote, not
    what the task reported."""
    return _TAG_RE["result"].sub("", text)


def classify(text: str) -> str:
    """The kind of one notification, from its text alone. Status is read before the
    sentence (and before the event test), so a failed or killed task is never filed as a
    repeat nor dropped from the denominator. Tags other than `<result>` are read outside
    the result body. A status not listed here (`running`, a future one) is ``fresh``:
    never stale, and still in the denominator."""
    outside = _outside_result(text)
    task_id = _tag(outside, "task-id")
    status = _tag(outside, "status").lower()
    if status == "failed":
        return "failed"
    if status in ("killed", "stopped"):
        return "killed"
    if _EVENT_RE.search(outside) or not task_id or not status:
        return "event"
    if status == "completed" and is_handback_sentence(_tag(text, "result"), task_id):
        return "handback-dup"
    return "fresh"


def classify_rows(rows: list[dict]) -> list[dict]:
    """Classify every task-notification row in one transcript, in file order."""
    launch_pos: dict[str, tuple[int, int]] = {}
    for i, row in enumerate(rows):
        for b, block in enumerate(_tool_uses(row)):
            tool_id = block.get("id")
            if isinstance(tool_id, str) and tool_id not in launch_pos:
                launch_pos[tool_id] = (i, b)

    previous_notification: dict[str, int] = {}
    results: list[dict] = []
    for i, row in enumerate(rows):
        if not _is_notification(row):
            continue
        text = _text(row)
        task_id = _tag(text, "task-id")
        launch_id = _tag(text, "tool-use-id")
        kind = classify(text)
        polls = 0
        if kind != "event":
            polls = count_polls(rows, i, task_id, launch_id, _tag(text, "output-file"),
                                launch_pos.get(launch_id), previous_notification.get(task_id))
        if task_id:
            previous_notification[task_id] = i
        results.append({"row": i, "task_id": task_id, "status": _tag(text, "status").lower(),
                        "kind": kind, "polls": polls, "reply_chars": _reply_chars(rows, i)})
    return results


def count_polls(rows: list[dict], at: int, task_id: str, launch_id: str,
                output_file: str, launch: tuple[int, int] | None,
                previous: int | None) -> int:
    """tool_uses before row `at` that name the task id or the output file, after the
    launch and after the previous notification of the same task. Behaviour, not staleness."""
    needles = [n for n in (task_id, output_file) if n]
    if not needles:
        return 0
    start = (-1, 0)
    if launch is not None:
        start = launch
    if previous is not None and (previous, 0) > start:
        start = (previous, 0)
    count = 0
    for i in range(max(start[0], 0), at):
        for b, block in enumerate(_tool_uses(rows[i])):
            if (i, b) < start:
                continue
            if block.get("id") == launch_id:
                continue
            if block.get("name") == "SendMessage":
                continue
            serialised = json.dumps(block.get("input"), sort_keys=True, ensure_ascii=False)
            if any(n in serialised for n in needles):
                count += 1
    return count


# ── aggregation ───────────────────────────────────────────────────────────────

def collect_files(paths: Iterable[str], since_days: int | None = None,
                  now: float | None = None) -> list[pathlib.Path]:
    """Expand files and directories (non-recursive `*.jsonl`) into a sorted list of
    resolved paths, so one file reached twice is read once. `since_days` keeps files whose
    mtime is at or after now - N days. Raises FileNotFoundError for a missing path."""
    found: set[pathlib.Path] = set()
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            found.update(p.resolve() for p in path.glob("*.jsonl") if p.is_file())
        elif path.is_file():
            found.add(path.resolve())
        else:
            raise FileNotFoundError(raw)
    if since_days is not None:
        cutoff = (time.time() if now is None else now) - since_days * 86400
        found = {p for p in found if p.stat().st_mtime >= cutoff}
    return sorted(found, key=str)


def dedupe(transcripts: dict[str, tuple[list[dict], int]]
           ) -> tuple[dict[str, tuple[list[dict], int]], int]:
    """Drop rows whose `uuid` an earlier row (in name order) already carried."""
    seen: set[str] = set()
    skipped = 0
    out: dict[str, tuple[list[dict], int]] = {}
    for name in sorted(transcripts):
        rows, unreadable = transcripts[name]
        kept: list[dict] = []
        for row in rows:
            uuid = row.get("uuid")
            if isinstance(uuid, str) and uuid:
                if uuid in seen:
                    skipped += 1
                    continue
                seen.add(uuid)
            kept.append(row)
        out[name] = (kept, unreadable)
    return out, skipped


def _counts(entries: list[dict]) -> dict[str, int]:
    counts = {kind: 0 for kind in KINDS}
    for entry in entries:
        counts[entry["kind"]] += 1
    return counts


def measure(transcripts: dict[str, tuple[list[dict], int]],
            since_days: int | None = None) -> dict:
    """Aggregate per-file (rows, unreadable) into the report dict."""
    transcripts, duplicates = dedupe(transcripts)
    per_file: dict[str, dict] = {}
    all_entries: list[dict] = []
    unreadable_total = 0
    for name in sorted(transcripts):
        rows, unreadable = transcripts[name]
        entries = classify_rows(rows)
        all_entries.extend(entries)
        unreadable_total += unreadable
        per_file[name] = {"notifications": len(entries), "unreadable_lines": unreadable,
                          **_counts(entries)}

    kinds = _counts(all_entries)
    counted = [e for e in all_entries if e["kind"] != "event"]
    total = len(counted)
    stale_entries = [e for e in counted if e["kind"] in STALE_KINDS]
    stale = len(stale_entries)
    return {
        "schema": SCHEMA,
        "state": "measured" if total else "unmeasured",
        "window_days": since_days,
        "window_basis": "file-mtime",
        "files": len(transcripts),
        "unreadable_lines": unreadable_total,
        "duplicate_rows_skipped": duplicates,
        "total_notifications": total,
        "events_excluded": kinds["event"],
        "kinds": kinds,
        "stale": stale,
        "stale_bp": stale * 10000 // total if total else None,
        "stale_reply_chars": sum(e["reply_chars"] for e in stale_entries),
        "stale_reply_turns": sum(1 for e in stale_entries if e["reply_chars"] > 0),
        "polls": sum(e["polls"] for e in counted),
        "polled_notifications": sum(1 for e in counted if e["polls"]),
        "per_file": per_file,
        "not_seen": NOT_SEEN,
        "polls_note": POLLS_NOTE,
    }


def measure_paths(paths: Iterable[str], since_days: int | None = None,
                  now: float | None = None) -> dict:
    transcripts: dict[str, tuple[list[dict], int]] = {}
    for path in collect_files(paths, since_days, now=now):
        with path.open(encoding="utf-8", errors="replace") as handle:
            transcripts[str(path)] = parse_lines(handle)
    return measure(transcripts, since_days)


# ── ratchet ───────────────────────────────────────────────────────────────────

def _load_ceiling(path: pathlib.Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema") != CEILING_SCHEMA:
        raise ValueError(f"not a {CEILING_SCHEMA} document")
    for key in ("stale_bp_max", "min_notifications"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"`{key}` must be a non-negative integer, not {value!r}")
    return doc


def _write_atomic(path: pathlib.Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(doc, handle, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def _not_judged_reason(report: dict, min_n: int) -> str | None:
    total = report["total_notifications"]
    if report["stale_bp"] is None:
        return "no notifications were measured"
    if report["unreadable_lines"]:
        return f"{report['unreadable_lines']} unreadable line(s) in the transcripts"
    if total < min_n:
        return f"{total} notification(s) < {min_n} required"
    return None


def apply_ratchet(report: dict, path: str | os.PathLike, *, tighten: bool = False,
                  init: bool = False) -> dict:
    """Judge the report against a ceiling file; optionally lower or create it.

    Returns {"state", "exit", "message", ...}. The matrix, first match wins:

    * --init and --tighten together                    → error, exit 2
    * file unreadable / wrong schema / bad values      → invalid, exit 2
    * file present and --init                          → error, exit 2 (never overwritten)
    * file missing without --init                      → missing, exit 2
    * nothing measured, unreadable lines, or fewer than
      min_notifications                                → not-judged, exit 3, nothing written
    * file missing, --init                             → created at the measured value, exit 0
    * measured > stale_bp_max                          → over, exit 1 (never rewritten)
    * measured < stale_bp_max with --tighten           → tightened down to measured, exit 0
    * otherwise                                        → ok, exit 0

    `--tighten` only lowers. The ceiling is a local file: deleting it and running
    `--init` resets it, so this is a guard against drift, not against tampering.
    """
    path = pathlib.Path(path)
    if init and tighten:
        return {"state": "error", "exit": EXIT_ERROR, "path": str(path),
                "message": "--init and --tighten are separate steps; pass one"}
    doc: dict | None = None
    if path.exists():
        try:
            doc = _load_ceiling(path)
        except (OSError, ValueError) as exc:
            return {"state": "invalid", "exit": EXIT_ERROR, "path": str(path),
                    "message": f"{path}: unreadable ceiling ({exc})"}
        if init:
            return {"state": "error", "exit": EXIT_ERROR, "path": str(path),
                    "message": f"{path} already exists; --init never overwrites a ceiling"}
    elif not init:
        return {"state": "missing", "exit": EXIT_ERROR, "path": str(path),
                "message": f"{path} does not exist; create it once with --init "
                           f"(suggested: {DEFAULT_CEILING_PATH})"}

    measured = report["stale_bp"]
    min_n = doc["min_notifications"] if doc is not None else DEFAULT_MIN_NOTIFICATIONS
    base = {"path": str(path), "measured_bp": measured,
            "total_notifications": report["total_notifications"],
            "min_notifications": min_n,
            "stale_bp_max": doc["stale_bp_max"] if doc is not None else None}

    reason = _not_judged_reason(report, min_n)
    if reason is not None:
        return {**base, "state": "not-judged", "exit": EXIT_NOT_JUDGED,
                "message": f"not judged: {reason}; ceiling untouched"}

    if doc is None:
        created = {"schema": CEILING_SCHEMA, "stale_bp_max": measured,
                   "min_notifications": DEFAULT_MIN_NOTIFICATIONS}
        _write_atomic(path, created)
        return {**base, "state": "created", "exit": EXIT_OK, "stale_bp_max": measured,
                "message": f"created {path} at stale_bp_max {measured}"}

    ceiling = doc["stale_bp_max"]
    if measured > ceiling:
        return {**base, "state": "over", "exit": EXIT_OVER,
                "message": f"stale wake-ups {measured} bp exceed the ceiling "
                           f"{ceiling} bp ({path})"}
    if tighten and measured < ceiling:
        _write_atomic(path, _lowered(doc, measured))
        return {**base, "state": "tightened", "exit": EXIT_OK, "stale_bp_max": measured,
                "previous_bp_max": ceiling,
                "message": f"ceiling lowered {ceiling} → {measured} bp ({path})"}
    return {**base, "state": "ok", "exit": EXIT_OK,
            "message": f"stale wake-ups {measured} bp within the ceiling {ceiling} bp"}


def _lowered(doc: dict, value: int) -> dict:
    if value >= doc["stale_bp_max"]:
        raise ValueError("the wake-up ceiling only moves down")
    return {**doc, "stale_bp_max": value}


# ── rendering ─────────────────────────────────────────────────────────────────

def render_json(report: dict) -> str:
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False)


def render_human(report: dict) -> str:
    kinds = report["kinds"]
    window = (f"files modified in the last {report['window_days']} days"
              if report["window_days"] is not None else "all files")
    lines = [f"## rig wake-ups ({window})", ""]
    lines.append(f"transcripts: {report['files']} file(s), "
                 f"{report['unreadable_lines']} unreadable line(s), "
                 f"{report['duplicate_rows_skipped']} duplicate row(s) skipped")
    if report["state"] == "unmeasured":
        lines.append("notifications: 0 — unmeasured (nothing to judge, not a clean result)")
    else:
        bp = report["stale_bp"]
        lines.append(f"notifications: {report['total_notifications']}  "
                     f"stale (handback-dup): {report['stale']} = {bp} bp ({bp / 100:.2f}%)")
        lines.append(f"  failed {kinds['failed']}, killed {kinds['killed']}, "
                     f"fresh {kinds['fresh']} (never stale)")
        lines.append(f"felt: {report['stale_reply_turns']} stale wake-up(s) answered with "
                     f"visible text, {report['stale_reply_chars']:,} char(s)")
    lines.append(f"events excluded from the denominator: {report['events_excluded']}")
    lines.append(f"polls: {report['polls']} tool_use(s) across "
                 f"{report['polled_notifications']} notification(s)")
    ratchet = report.get("ratchet")
    if ratchet:
        lines.append(f"ratchet: {ratchet['state']} — {ratchet['message']}")
    lines.append("")
    lines.append(POLLS_NOTE)
    lines.append(NOT_SEEN)
    return "\n".join(lines)


# ── command ───────────────────────────────────────────────────────────────────

def cmd_wakeups(args: argparse.Namespace) -> None:
    """`wb wakeups`: print the report, then let the ratchet's verdict set the exit code
    (1 over the ceiling, 2 for a missing/invalid ceiling or bad usage, 3 not judged)."""
    if (args.init or args.tighten) and not args.ratchet:
        die("--init and --tighten need --ratchet FILE")
    if args.since_days is not None and args.since_days < 0:
        die("--since-days must be 0 or more")
    try:
        report = measure_paths(args.transcripts, since_days=args.since_days)
    except FileNotFoundError as exc:
        die(f"--transcripts: no such file or directory: {exc}")
    if args.ratchet:
        report["ratchet"] = apply_ratchet(report, args.ratchet, tighten=args.tighten,
                                          init=args.init)
    print(render_json(report) if args.json else render_human(report))
    ratchet = report.get("ratchet")
    if not ratchet:
        return
    if ratchet["exit"] == EXIT_OVER:
        reject(ratchet["message"])
    if ratchet["exit"] == EXIT_ERROR:
        die(ratchet["message"])
    if ratchet["exit"] == EXIT_NOT_JUDGED:
        # 3, not 0: a sample too small or too damaged to judge is not a pass
        # (commands/go.md: unmeasured is never success), and not 1 either — nothing was
        # judged. Same meaning `wb gate` and `wb contract` give 3.
        console.write_line(f"[NOT JUDGED] {ratchet['message']}", stream=sys.stderr)
        sys.exit(EXIT_NOT_JUDGED)
