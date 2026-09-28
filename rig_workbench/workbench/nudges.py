"""`wb nudges` — count the prompts that said nothing but "carry on".

A prompt such as 「進めて」, 「続けて」 or "continue" carries no instruction: the person is
paying a turn to restart work the session stopped by itself. Its share of all prompts is
a direct measure of how often a session stops before it should. This module reads Claude
Code session transcripts (`*.jsonl`) and classifies every prompt a person typed
(`turns.prompt_text`):

1. ``nudge`` — the whole prompt, normalised (`turns.normalise`), is a continuation word
   with optional filler and politeness around it (`is_nudge`): 進めて, 続けてください,
   そのまま続行, "keep going", "go ahead". The words are a closed list; a prompt that adds
   anything else ("continue, but skip the tests") is ``other``.
2. ``ack`` — the whole prompt is a bare acknowledgement: はい, ok, お願いします. Counted apart
   and **not** a nudge, because it is just as often the answer to a question the session
   was right to ask.
3. ``other`` — everything else.

Each nudge is further marked by the assistant turn it answered:

* ``after_question`` — that turn ended by asking the person something (`turns.asks_user`),
  e.g. a gated step asking whether to go on. The nudge answered a question.
* ``unprompted`` — it did not. The session simply stopped. These are the ones the Stop
  hook `hooks/continue-rig-run.sh` exists to remove.
* ``in_run`` — that turn carried a rig run-status header (a rig RUN was active).

stale = nudge. The denominator is every prompt. The ratchet judges `nudge_bp`.

Rows are de-duplicated exactly as `wb wakeups` does (`wakeups.dedupe`). What it cannot see
is stated in every report. This module measures and judges; `cmd_nudges` in
`context_report` prints and sets the exit code. Stdlib only.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Iterable

from . import turns
from .wakeups import (EXIT_ERROR, EXIT_NOT_JUDGED, EXIT_OK, EXIT_OVER, MAX_BP,
                      _no_duplicate_keys, _write_atomic, collect_files, dedupe,
                      parse_lines)
from .wakeups import render_json as render_json  # re-exported: the same stable JSON form

SCHEMA = "rig.nudges/v1"
CEILING_SCHEMA = "rig.nudges-ceiling/v1"
DEFAULT_MIN_PROMPTS = 30
DEFAULT_CEILING_PATH = ".rig/nudges-ceiling.json"

KINDS = ("nudge", "ack", "other")

_LEAD = r"(?:はい|うん|ok|okay|yes|では|じゃあ|じゃ|それでは|よし|了解|りょ|please)?"
_MOOD = r"(?:そのまま|引き続き|ひきつづき|どんどん|最後まで|全部)?"
_CORE = (r"(?:進めて|すすめて|進んで|進めよう|進行|続けて|つづけて|続き(?:を)?|つづき(?:を)?"
         r"|続行|継続|再開|やって|次(?:へ|に進んで)?|go|goon|goahead|continue|keepgoing"
         r"|proceed|carryon|resume|next)")
_TAIL = r"(?:して)?(?:ください|下さい|お願いします|おねがいします|お願い|ちょうだい|please|で|ね|よ)?"
_NUDGE_RE = re.compile(rf"^{_LEAD}{_MOOD}{_CORE}{_TAIL}$")
#: 「引き続きお願いします」「そのままで」— the mood word alone, with politeness.
_MOOD_ONLY_RE = re.compile(r"^(?:引き続き|ひきつづき|そのまま(?:で)?)"
                           r"(?:よろしく)?(?:お願いします|おねがいします|お願い)?$")
_ACK = frozenset({"はい", "うん", "ok", "okay", "yes", "y", "いいよ", "いいです", "いいね",
                  "お願いします", "おねがいします", "お願い", "よろしく", "よろしくお願いします",
                  "sure", "lgtm", "了解", "りょうかい", "承知", "承知しました", "どうぞ",
                  "okです", "はいお願いします"})

NOT_SEEN = ("Only prompts recorded in the given transcripts are counted. Transcripts not "
            "passed in, and prompts typed into other tools, are invisible here. A nudge is "
            "judged from its words alone: a prompt that says more than a continuation "
            "word is `other` even when it meant \"carry on\", so this is a floor, not a "
            "total.")


def is_nudge(text: str) -> bool:
    norm = turns.normalise(text)
    return bool(norm) and bool(_NUDGE_RE.match(norm) or _MOOD_ONLY_RE.match(norm))


def classify_prompt(text: str) -> str:
    if is_nudge(text):
        return "nudge"
    if turns.normalise(text) in _ACK:
        return "ack"
    return "other"


def classify_rows(rows: list[dict]) -> list[dict]:
    """Every prompt a person typed in one transcript, in file order, with its kind and
    the assistant turn it answered (`after_question`, `in_run`)."""
    entries: list[dict] = []
    previous = -1
    for i, row in enumerate(rows):
        text = turns.prompt_text(row)
        if text is None:
            continue
        answered = rows[previous + 1:i]
        kind = classify_prompt(text)
        entry = {"row": i, "kind": kind, "after_question": False, "in_run": False}
        if kind == "nudge":
            entry["after_question"] = turns.asks_user(turns.last_assistant_text(answered))
            entry["in_run"] = turns.parse_header(turns.assistant_text(answered)) is not None
        entries.append(entry)
        previous = i
    return entries


def measure(transcripts: dict[str, tuple[list[dict], int]],
            since_days: int | None = None) -> dict:
    """Aggregate per-file (rows, unreadable) into the report dict."""
    transcripts, duplicates, conflicts = dedupe(transcripts)
    per_file: dict[str, dict] = {}
    entries: list[dict] = []
    unreadable_total = 0
    for name in sorted(transcripts):
        rows, unreadable = transcripts[name]
        found = classify_rows(rows)
        entries.extend(found)
        unreadable_total += unreadable
        per_file[name] = {"prompts": len(found), "unreadable_lines": unreadable,
                          **{kind: sum(1 for e in found if e["kind"] == kind) for kind in KINDS}}
    total = len(entries)
    nudges = [e for e in entries if e["kind"] == "nudge"]
    unprompted = [e for e in nudges if not e["after_question"]]
    return {
        "schema": SCHEMA,
        "state": "measured" if total else "unmeasured",
        "window_days": since_days,
        "window_basis": "file-mtime",
        "files": len(transcripts),
        "unreadable_lines": unreadable_total,
        "duplicate_rows_skipped": duplicates,
        "uuid_conflicts": conflicts,
        "total_prompts": total,
        "kinds": {kind: sum(1 for e in entries if e["kind"] == kind) for kind in KINDS},
        "nudges": len(nudges),
        "nudge_bp": len(nudges) * MAX_BP // total if total else None,
        "unprompted": len(unprompted),
        "unprompted_bp": len(unprompted) * MAX_BP // total if total else None,
        "after_question": len(nudges) - len(unprompted),
        "in_run": sum(1 for e in nudges if e["in_run"]),
        "unprompted_in_run": sum(1 for e in unprompted if e["in_run"]),
        "per_file": per_file,
        "not_seen": NOT_SEEN,
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
    doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    if not isinstance(doc, dict) or doc.get("schema") != CEILING_SCHEMA:
        raise ValueError(f"not a {CEILING_SCHEMA} document")
    for key in ("nudge_bp_max", "min_prompts"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"`{key}` must be a non-negative integer, not {value!r}")
    if doc["nudge_bp_max"] > MAX_BP:
        raise ValueError(f"`nudge_bp_max` is basis points, at most {MAX_BP}, "
                         f"not {doc['nudge_bp_max']}")
    return doc


def _not_judged_reason(report: dict, min_n: int) -> str | None:
    total = report["total_prompts"]
    reasons = []
    if report["nudge_bp"] is None:
        reasons.append("no prompts were measured")
    if report["unreadable_lines"]:
        reasons.append(f"{report['unreadable_lines']} unreadable line(s) in the transcripts")
    if report["uuid_conflicts"]:
        reasons.append(f"{report['uuid_conflicts']} row(s) share a uuid with a different "
                       "row; the transcripts disagree")
    if 0 < total < min_n:
        reasons.append(f"{total} prompt(s) < {min_n} required")
    return "; ".join(reasons) or None


def apply_ratchet(report: dict, path: str | os.PathLike, *, tighten: bool = False,
                  init: bool = False) -> dict:
    """Judge the report against a ceiling file; optionally lower or create it. The same
    matrix as `wakeups.apply_ratchet`, over `nudge_bp` and `min_prompts`: 2 for bad usage
    or a missing/invalid ceiling, 3 not judged (nothing written), 1 over (never
    rewritten), 0 ok / created / tightened. `--tighten` only lowers."""
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

    measured = report["nudge_bp"]
    min_n = doc["min_prompts"] if doc is not None else DEFAULT_MIN_PROMPTS
    base = {"path": str(path), "measured_bp": measured,
            "total_prompts": report["total_prompts"], "min_prompts": min_n,
            "nudge_bp_max": doc["nudge_bp_max"] if doc is not None else None}

    reason = _not_judged_reason(report, min_n)
    if reason is not None:
        return {**base, "state": "not-judged", "exit": EXIT_NOT_JUDGED,
                "message": f"not judged: {reason}; ceiling untouched"}

    if doc is None:
        _write_atomic(path, {"schema": CEILING_SCHEMA, "nudge_bp_max": measured,
                             "min_prompts": DEFAULT_MIN_PROMPTS})
        return {**base, "state": "created", "exit": EXIT_OK, "nudge_bp_max": measured,
                "message": f"created {path} at nudge_bp_max {measured}"}

    ceiling = doc["nudge_bp_max"]
    if measured > ceiling:
        return {**base, "state": "over", "exit": EXIT_OVER,
                "message": f"nudges {measured} bp exceed the ceiling {ceiling} bp ({path})"}
    if tighten and measured < ceiling:
        _write_atomic(path, {**doc, "nudge_bp_max": measured})
        return {**base, "state": "tightened", "exit": EXIT_OK, "nudge_bp_max": measured,
                "previous_bp_max": ceiling,
                "message": f"ceiling lowered {ceiling} → {measured} bp ({path})"}
    return {**base, "state": "ok", "exit": EXIT_OK,
            "message": f"nudges {measured} bp within the ceiling {ceiling} bp"}


# ── rendering ─────────────────────────────────────────────────────────────────

def render_human(report: dict) -> str:
    kinds = report["kinds"]
    window = (f"files modified in the last {report['window_days']} days"
              if report["window_days"] is not None else "all files")
    lines = [f"## rig nudges ({window})", ""]
    lines.append(f"transcripts: {report['files']} file(s), "
                 f"{report['unreadable_lines']} unreadable line(s), "
                 f"{report['duplicate_rows_skipped']} duplicate row(s) skipped, "
                 f"{report['uuid_conflicts']} uuid conflict(s)")
    if report["state"] == "unmeasured":
        lines.append("prompts: 0 — unmeasured (nothing to judge, not a clean result)")
    else:
        bp, ubp = report["nudge_bp"], report["unprompted_bp"]
        lines.append(f"prompts: {report['total_prompts']}  "
                     f"nudges: {report['nudges']} = {bp} bp ({bp / 100:.2f}%)")
        lines.append(f"  unprompted (the session just stopped): {report['unprompted']} = "
                     f"{ubp} bp ({ubp / 100:.2f}%), {report['unprompted_in_run']} during a rig RUN")
        lines.append(f"  after a question (answering a confirmation): {report['after_question']}")
        lines.append(f"  during a rig RUN: {report['in_run']}")
        lines.append(f"acks (はい / ok — not counted as nudges): {kinds['ack']}")
    ratchet = report.get("ratchet")
    if ratchet:
        lines.append(f"ratchet: {ratchet['state']} — {ratchet['message']}")
    lines.append("")
    lines.append(NOT_SEEN)
    return "\n".join(lines)
