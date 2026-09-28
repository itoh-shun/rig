"""Turns in a Claude Code transcript: which user rows a person typed, and what the
assistant said in reply.

Shared by `wb nudges` (which counts the prompts that only said "carry on") and the Stop
hook that stops a rig RUN from ending its turn mid-flow (`stop_continue`). Both need the
same two answers, so they are answered once here:

* **Is this row a prompt a person typed?** A `user` row is not, when it is only tool
  results, a task-notification, harness metadata (`isMeta`, `isCompactSummary`), a
  subagent's sidechain, local-command output, or the interruption marker. A slash-command
  invocation (`<command-name>`) *is* a prompt — a person typed it — though never a nudge.
* **What is the run-status header of this turn?** The last `▸ rig | …` line in the
  assistant text (`SKILL.md` §6 ①), parsed into its fields.

Row access (`_text`, `_blocks`, …) is `wakeups`'s, so the two meters read a row the same
way. Stdlib only.
"""

from __future__ import annotations

import re
import unicodedata

from .wakeups import _blocks, _is_notification, _is_tool_result_row, _text

_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
#: Harness-written user rows that no person typed.
_NOT_TYPED_PREFIXES = ("<local-command-", "<task-notification>", "[Request interrupted",
                       "Caveat: The messages below were generated", "Stop hook feedback",
                       "[rig run-continuity]")

#: `▸ rig | recipe: bugfix | step: implement (4/7) | gate: pending (try 1/2) | stuck: 1/2
#: | backend: manual | mode: gated`. Fields are `key: value`, separated by `|`.
_HEADER_RE = re.compile(r"▸ rig \|([^\n]*)")
_STEP_RE = re.compile(r"^(?P<id>.*?)\s*\((?P<n>\d+)\s*/\s*(?P<total>\d+)\)\s*$")


def prompt_text(row: dict) -> str | None:
    """The text a person typed in this row, or None when no person typed it."""
    if row.get("type") != "user":
        return None
    if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary"):
        return None
    if _is_notification(row) or _is_tool_result_row(row):
        return None
    text = _REMINDER_RE.sub("", _text(row)).strip()
    if not text or text.startswith(_NOT_TYPED_PREFIXES):
        return None
    return text


def is_prompt(row: dict) -> bool:
    return prompt_text(row) is not None


def assistant_text(rows: list[dict]) -> str:
    """Every visible assistant text block in `rows`, joined by newlines."""
    parts: list[str] = []
    for row in rows:
        if row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        parts.extend(b["text"] for b in _blocks(row)
                     if b.get("type") == "text" and isinstance(b.get("text"), str))
    return "\n".join(parts)


def last_assistant_text(rows: list[dict]) -> str:
    """The last visible assistant text block in `rows` — what the turn ended on."""
    for row in reversed(rows):
        if row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        texts = [b["text"] for b in _blocks(row)
                 if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip()]
        if texts:
            return texts[-1]
    return ""


def current_turn(rows: list[dict]) -> list[dict]:
    """The rows after the last prompt a person typed (the whole file when there is none)."""
    for i in range(len(rows) - 1, -1, -1):
        if is_prompt(rows[i]):
            return rows[i + 1:]
    return rows


def parse_header(text: str) -> dict | None:
    """The last run-status header in `text`, as a dict of its fields, or None.

    `step` is split into `step_id`, `n` and `total` when it carries a position; a header
    without one keeps `n`/`total` as None, which callers read as "position unknown"."""
    matches = _HEADER_RE.findall(text)
    if not matches:
        return None
    fields: dict = {}
    for part in matches[-1].split("|"):
        key, sep, value = part.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip().strip("`").strip()
    step = _STEP_RE.match(fields.get("step", ""))
    fields["step_id"] = step.group("id").strip() if step else fields.get("step", "")
    fields["n"] = int(step.group("n")) if step else None
    fields["total"] = int(step.group("total")) if step else None
    return fields


#: The stop declaration (SKILL.md §6 ⑤): the one line a RUN writes when it ends its turn
#: on purpose, `▸ stop: <code>[ — <detail>]`. The escalation codes are
#: `assurance.development_loop.ESCALATIONS`, copied rather than imported so the Stop hook
#: does not pay for that package on every stop; tests/test_continue_rig_run_hook.py holds
#: the two equal.
STOP_PLAIN = ("done", "waiting", "step-gate")
STOP_NEEDS_DECISION = ("destructive-operation", "ambiguous-requirement",
                       "policy-requires-approval", "budget-exhausted", "capability-missing")
STOP_BLOCKED = ("capability-missing", "gate-reject", "repeated-failure")
_STOP_RE = re.compile(r"▸ stop:\s*([A-Za-z-]+)(?::([A-Za-z-]+))?")


def parse_stop(text: str) -> dict | None:
    """The last stop declaration in `text`: {"code", "kind", "reason", "known"}, or None.

    `known` is False for a code outside the vocabulary. A misspelt declaration is still a
    declaration that the stop was meant, so callers let it stop and count it apart."""
    matches = _STOP_RE.findall(text)
    if not matches:
        return None
    kind, reason = matches[-1][0].lower(), matches[-1][1].lower()
    known = ((kind in STOP_PLAIN and not reason)
             or (kind == "needs-decision" and reason in STOP_NEEDS_DECISION)
             or (kind == "blocked" and reason in STOP_BLOCKED))
    code = f"{kind}:{reason}" if reason else kind
    return {"code": code, "kind": kind, "reason": reason, "known": known}


#: Phrases that hand the decision to the person. Matching errs towards "it asked": for the
#: Stop hook a false "asked" only means one push is not given, the cheap direction.
_ASKING = ("進めますか", "進めてよいですか", "進めてもよいですか", "よろしいですか", "よいですか",
           "いいですか", "しますか", "どうしますか", "どちらにしますか", "どれにしますか",
           "ご確認ください", "確認をお願いします", "承認してください", "ご判断ください",
           "判断を仰", "選んでください", "教えてください", "お知らせください", "指示をください",
           "ご指示ください", "shall i", "should i", "do you want", "would you like",
           "let me know", "please confirm", "which option", "your call")
_TRAILING_FENCE_RE = re.compile(r"(?:\n```[^\n]*)+\s*$")


def asks_user(text: str) -> bool:
    """True when `text` ends on a question to the person, or hands them a decision."""
    body = _TRAILING_FENCE_RE.sub("", text).strip()
    if not body:
        return False
    last = body.splitlines()[-1].strip().rstrip("*_` ")
    if last.endswith(("?", "？")):
        return True
    lowered = body.lower()
    return any(phrase in lowered for phrase in _ASKING)


def normalise(text: str) -> str:
    """NFKC, lower-case, whitespace and punctuation removed — for matching short prompts."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text
                   if not ch.isspace() and not unicodedata.category(ch).startswith(("P", "S")))
