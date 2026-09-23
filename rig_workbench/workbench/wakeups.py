"""`wb context --transcripts` — count the wake-ups that told the parent nothing new.

Claude Code re-invokes the parent session when a background task finishes: a
`<task-notification>` arrives as a user turn. Twice over, that turn carries nothing:

* a subagent that handed back already delivered its report as an agent message
  (`origin.handback`), and the later notification says so and repeats nothing;
* a background command whose output file the parent already read (or polled) is
  re-announced after the parent has moved on.

Under rig each such turn costs a run-status header plus narration. This module reads
Claude Code session transcripts (`*.jsonl`) and classifies every task-notification row,
first match wins:

1. ``handback-dup`` — `<result>` says the report was "delivered to you as a message",
   or an earlier row is a handback agent message from the same task id;
2. ``read-early`` — an assistant tool_use after the launch and after the previous
   notification of the same task id, and before this one, whose serialised input names
   the task id or the output file. The launching tool_use itself and `SendMessage`
   (resuming a subagent is not consuming its result) do not count;
3. ``killed`` — status `killed` or `stopped`;
4. ``fresh`` — everything else.

stale = handback-dup + read-early. ``killed`` is reported apart and is not stale: a
watcher killed by the OOM killer is a different defect with a different fix.

What it cannot see is stated in every report: only notifications recorded in the
transcripts it was given. Pure functions over parsed rows; stdlib only.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from typing import Any, Iterable

SCHEMA = "rig.wakeups/v1"
CEILING_SCHEMA = "rig.wakeups-ceiling/v1"
DEFAULT_MIN_NOTIFICATIONS = 20
DEFAULT_CEILING_PATH = ".rig/wakeups-ceiling.json"

KINDS = ("handback-dup", "read-early", "killed", "fresh")
STALE_KINDS = ("handback-dup", "read-early")

DELIVERED_MARKER = "delivered to you as a message"

NOT_SEEN = ("Only task-notifications recorded in the given transcripts are counted. "
            "Transcripts not passed in, subagent transcripts, and wake-ups a session "
            "never wrote down are invisible here; a low number means few stale "
            "wake-ups in these files, not in every session.")

_TAG_RE = {tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.DOTALL)
           for tag in ("task-id", "tool-use-id", "output-file", "status", "result")}


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

def classify_rows(rows: list[dict]) -> list[dict]:
    """Classify every task-notification row in one transcript, in file order."""
    launch_pos: dict[str, tuple[int, int]] = {}
    for i, row in enumerate(rows):
        for b, block in enumerate(_tool_uses(row)):
            tool_id = block.get("id")
            if isinstance(tool_id, str) and tool_id not in launch_pos:
                launch_pos[tool_id] = (i, b)

    handback_seen: set[str] = set()
    previous_notification: dict[str, int] = {}
    results: list[dict] = []
    for i, row in enumerate(rows):
        origin = _origin(row)
        if origin.get("handback") is True and isinstance(origin.get("senderTaskId"), str):
            handback_seen.add(origin["senderTaskId"])
            continue
        if not _is_notification(row):
            continue
        text = _text(row)
        task_id = _tag(text, "task-id")
        launch_id = _tag(text, "tool-use-id")
        output_file = _tag(text, "output-file")
        status = _tag(text, "status").lower()
        result = _tag(text, "result")

        if DELIVERED_MARKER in result or (task_id and task_id in handback_seen):
            kind = "handback-dup"
        elif _read_early(rows, i, task_id, launch_id, output_file,
                         launch_pos.get(launch_id), previous_notification.get(task_id)):
            kind = "read-early"
        elif status in ("killed", "stopped"):
            kind = "killed"
        else:
            kind = "fresh"

        if task_id:
            previous_notification[task_id] = i
        results.append({"row": i, "task_id": task_id, "status": status, "kind": kind,
                        "reply_chars": _reply_chars(rows, i)})
    return results


def _read_early(rows: list[dict], at: int, task_id: str, launch_id: str,
                output_file: str, launch: tuple[int, int] | None,
                previous: int | None) -> bool:
    needles = [n for n in (task_id, output_file) if n]
    if not needles:
        return False
    start = (-1, 0)
    if launch is not None:
        start = launch
    if previous is not None and (previous, 0) > start:
        start = (previous, 0)
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
                return True
    return False


# ── aggregation ───────────────────────────────────────────────────────────────

def collect_files(paths: Iterable[str], since_days: int | None = None,
                  now: float | None = None) -> list[pathlib.Path]:
    """Expand files and directories (non-recursive `*.jsonl`) into a sorted list.
    Raises FileNotFoundError for a path that does not exist."""
    found: set[pathlib.Path] = set()
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            found.update(p for p in path.glob("*.jsonl") if p.is_file())
        elif path.is_file():
            found.add(path)
        else:
            raise FileNotFoundError(raw)
    if since_days is not None:
        cutoff = (time.time() if now is None else now) - since_days * 86400
        found = {p for p in found if p.stat().st_mtime >= cutoff}
    return sorted(found, key=str)


def _counts(entries: list[dict]) -> dict[str, int]:
    counts = {kind: 0 for kind in KINDS}
    for entry in entries:
        counts[entry["kind"]] += 1
    return counts


def measure(transcripts: dict[str, tuple[list[dict], int]],
            since_days: int | None = None) -> dict:
    """Aggregate per-file (rows, unreadable) into the report dict."""
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
    total = len(all_entries)
    stale_entries = [e for e in all_entries if e["kind"] in STALE_KINDS]
    stale = len(stale_entries)
    return {
        "schema": SCHEMA,
        "state": "measured" if total else "unmeasured",
        "window_days": since_days,
        "files": len(transcripts),
        "unreadable_lines": unreadable_total,
        "total_notifications": total,
        "kinds": kinds,
        "stale": stale,
        "stale_bp": stale * 10000 // total if total else None,
        "stale_reply_chars": sum(e["reply_chars"] for e in stale_entries),
        "stale_reply_turns": sum(1 for e in stale_entries if e["reply_chars"] > 0),
        "per_file": per_file,
        "not_seen": NOT_SEEN,
    }


def measure_paths(paths: Iterable[str], since_days: int | None = None) -> dict:
    transcripts: dict[str, tuple[list[dict], int]] = {}
    for path in collect_files(paths, since_days):
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
            raise ValueError(f"`{key}` must be a non-negative integer")
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


def apply_ratchet(report: dict, path: str | os.PathLike, tighten: bool) -> dict:
    """Judge the report against a ceiling file; optionally lower the ceiling.

    Returns {"state", "exit", "message", ...}. The matrix:

    * file unreadable / wrong schema                   → invalid, exit 2
    * file missing, no --tighten                       → missing, exit 2 (with a hint)
    * nothing measured, or total < min_notifications   → insufficient-sample, exit 0,
      nothing written (a file that is missing stays missing)
    * file missing, --tighten, sample sufficient       → created at the measured value
    * measured > stale_bp_max                          → fail, exit 1 (never rewritten)
    * measured < stale_bp_max with --tighten           → tightened down to measured
    * otherwise                                        → ok, exit 0

    The ceiling only ever moves down: the tool has no path that raises it.
    """
    path = pathlib.Path(path)
    doc: dict | None = None
    if path.exists():
        try:
            doc = _load_ceiling(path)
        except (OSError, ValueError) as exc:
            return {"state": "invalid", "exit": 2, "path": str(path),
                    "message": f"{path}: unreadable ceiling ({exc})"}
    elif not tighten:
        return {"state": "missing", "exit": 2, "path": str(path),
                "message": f"{path} does not exist; run once with --tighten to create it "
                           f"at the measured value (suggested: {DEFAULT_CEILING_PATH})"}

    measured = report["stale_bp"]
    total = report["total_notifications"]
    min_n = doc["min_notifications"] if doc is not None else DEFAULT_MIN_NOTIFICATIONS
    base = {"path": str(path), "measured_bp": measured, "total_notifications": total,
            "min_notifications": min_n,
            "stale_bp_max": doc["stale_bp_max"] if doc is not None else None}

    if measured is None or total < min_n:
        return {**base, "state": "insufficient-sample", "exit": 0,
                "message": f"{total} notification(s) < {min_n} required: not judged, "
                           f"ceiling untouched"}

    if doc is None:
        created = {"schema": CEILING_SCHEMA, "stale_bp_max": measured,
                   "min_notifications": DEFAULT_MIN_NOTIFICATIONS}
        _write_atomic(path, created)
        return {**base, "state": "created", "exit": 0, "stale_bp_max": measured,
                "message": f"created {path} at stale_bp_max {measured}"}

    ceiling = doc["stale_bp_max"]
    if measured > ceiling:
        return {**base, "state": "fail", "exit": 1,
                "message": f"stale wake-ups {measured} bp exceed the ceiling "
                           f"{ceiling} bp ({path})"}
    if tighten and measured < ceiling:
        _write_atomic(path, _lowered(doc, measured))
        return {**base, "state": "tightened", "exit": 0, "stale_bp_max": measured,
                "previous_bp_max": ceiling,
                "message": f"ceiling lowered {ceiling} → {measured} bp ({path})"}
    return {**base, "state": "ok", "exit": 0,
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
    window = (f"last {report['window_days']} days" if report["window_days"] is not None
              else "all files")
    lines = [f"## rig wake-ups ({window})", ""]
    lines.append(f"transcripts: {report['files']} file(s), "
                 f"{report['unreadable_lines']} unreadable line(s)")
    if report["state"] == "unmeasured":
        lines.append("notifications: 0 — unmeasured (nothing to judge, not a clean result)")
    else:
        bp = report["stale_bp"]
        lines.append(f"notifications: {report['total_notifications']}  "
                     f"stale: {report['stale']} = {bp} bp ({bp / 100:.2f}%)")
        lines.append(f"  handback-dup {kinds['handback-dup']}, read-early "
                     f"{kinds['read-early']} | killed {kinds['killed']} (not stale), "
                     f"fresh {kinds['fresh']}")
        lines.append(f"felt: {report['stale_reply_turns']} stale wake-up(s) answered with "
                     f"visible text, {report['stale_reply_chars']:,} char(s)")
    ratchet = report.get("ratchet")
    if ratchet:
        lines.append(f"ratchet: {ratchet['state']} — {ratchet['message']}")
    lines.append("")
    lines.append(NOT_SEEN)
    return "\n".join(lines)
