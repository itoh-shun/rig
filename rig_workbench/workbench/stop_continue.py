"""Stop hook: do not let a rig RUN end its turn mid-flow without handing the person a
decision.

A session that stops between steps for no reason costs a turn in which the person types
「進めて」 (`wb nudges` counts them). This hook reads the transcript when the model tries
to end its turn and, **only** when every condition below holds, answers
`{"decision": "block", "reason": …}` so the model carries on with the current step:

1. Not a provider subprocess (`RIG_PROVIDER_SUBPROCESS`) and not switched off
   (`RIG_AUTO_CONTINUE=0|off|false|no`). A blocking Stop hook inside `claude -p` /
   `codex exec` replaces the verdict the orchestrator is waiting for (#592).
2. This turn's assistant text carries a run-status header (`▸ rig | …`) whose step has a
   position and is not the last (`n < N`). No header — no rig RUN — nothing happens; the
   Stop reminder retired in 2.2.2/3.3.1 fired in sessions rig had no business in.
3. The gate is not `REJECT` and the stuck-guard is not at its limit (`stuck: 2/2`): those
   are escalations the person must answer.
4. The turn's last text does not ask the person anything (`turns.asks_user`).
5. In `mode: gated` (the default), the turn did not reach a step boundary
   (`── step <id> ▸ done`): a gated RUN stops after each step by design (SKILL.md §6, step
   gates). Only a stop in the middle of a step is premature. `mode: autonomous` never
   stops between steps, so any such stop is.
6. No background task launched in this session is still waiting for its notification:
   ending the turn is how a session waits for one (`patterns/monitor`).
7. Progress since the last push: when the model is already continuing because of this
   hook (`stop_hook_active`), it is pushed again only if the header moved (step, position
   or gate) since the previous push. A model that stops twice at the same place is let go,
   so the number of pushes per stop chain is bounded by the steps left.

Anything unexpected — unreadable payload, missing transcript, a parse error — means no
output and exit 0: this hook fails open. State (the last pushed header, per session) is
written only when it blocks, under `$XDG_STATE_HOME/rig/continue-rig-run/`.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

from . import turns
from .wakeups import _blocks, _is_notification, _text, fields, parse_lines

_OFF = frozenset({"0", "off", "false", "no"})
_DONE_RE = re.compile(r"── step [^\n]*▸ done")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
_BACKGROUND_WORDS = ("background", "async")


def _tool_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def pending_background(rows: list[dict]) -> bool:
    """True when a background launch in `rows` has no task-notification naming it yet.

    A launch is a tool_use with `run_in_background: true`, or an `Agent`/`Task` call whose
    tool_result says it went to the background. A launch that never notifies (a killed
    session) keeps this True for the rest of the transcript — the hook then stays quiet,
    which is the safe direction."""
    launched: set[str] = set()
    agent_calls: set[str] = set()
    for row in rows:
        if row.get("type") == "assistant":
            for block in _blocks(row):
                if block.get("type") != "tool_use" or not isinstance(block.get("id"), str):
                    continue
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                if args.get("run_in_background") is True:
                    launched.add(block["id"])
                elif block.get("name") in ("Agent", "Task"):
                    agent_calls.add(block["id"])
        elif row.get("type") == "user":
            for block in _blocks(row):
                if block.get("type") == "tool_result" and block.get("tool_use_id") in agent_calls:
                    text = _tool_result_text(block).lower()
                    if any(word in text for word in _BACKGROUND_WORDS):
                        launched.add(block["tool_use_id"])
    for row in rows:
        if row.get("type") == "user" and (_is_notification(row)
                                          or "<task-notification>" in _text(row)):
            launched.discard(fields(_text(row))["launch_id"])
    return bool(launched)


def _signature(header: dict) -> str:
    return f"{header['step_id']}|{header['n']}/{header['total']}|{header.get('gate', '')}"


def decide(payload: dict, rows: list[dict], env: dict,
           previous_signature: str | None) -> tuple[str, str] | None:
    """(reason, signature) when the stop should be blocked, else None."""
    if env.get("RIG_PROVIDER_SUBPROCESS"):
        return None
    if env.get("RIG_AUTO_CONTINUE", "").strip().lower() in _OFF:
        return None
    turn = turns.current_turn(rows)
    said = turns.assistant_text(turn)
    header = turns.parse_header(said)
    if header is None or header["n"] is None or header["total"] is None:
        return None
    if header["n"] >= header["total"]:
        return None
    gate = header.get("gate", "").lower()
    if gate.startswith("reject") or header.get("stuck", "").replace(" ", "") == "2/2":
        return None
    if turns.asks_user(turns.last_assistant_text(turn)):
        return None
    autonomous = header.get("mode", "").lower().startswith("autonomous")
    if not autonomous and _DONE_RE.search(said):
        return None
    if pending_background(rows):
        return None
    signature = _signature(header)
    if payload.get("stop_hook_active") and signature == previous_signature:
        return None
    step = f"{header['step_id']} ({header['n']}/{header['total']})"
    reason = (f"[rig run-continuity] The rig RUN is at step {step} and this turn ended "
              "without handing the person a decision. Carry on with the current step now "
              "(SKILL.md §6: do not wait for 「進めて」). If you genuinely need the person's "
              "decision, ask it as an explicit question and stop; if the RUN has actually "
              "ended, say so in one line.")
    return reason, signature


def _state_path(env: dict, session_id: str) -> pathlib.Path:
    base = env.get("XDG_STATE_HOME") or str(pathlib.Path(env.get("HOME") or "~").expanduser()
                                             / ".local" / "state")
    return pathlib.Path(base) / "rig" / "continue-rig-run" / _SAFE_ID_RE.sub("_", session_id)


def run(stdin: str, env: dict) -> str:
    """The hook's stdout for one Stop payload ('' for "let it stop")."""
    try:
        payload = json.loads(stdin)
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str) or not os.path.isfile(transcript):
        return ""
    session_id = payload.get("session_id")
    if payload.get("stop_hook_active") and not (isinstance(session_id, str) and session_id):
        return ""  # no way to tell progress from a loop
    state = _state_path(env, session_id) if isinstance(session_id, str) and session_id else None
    previous = None
    if state is not None and state.is_file():
        previous = state.read_text(encoding="utf-8").strip()
    with open(transcript, encoding="utf-8", errors="replace") as handle:
        rows, _ = parse_lines(handle)
    verdict = decide(payload, rows, env, previous)
    if verdict is None:
        return ""
    reason, signature = verdict
    if state is not None:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(signature + "\n", encoding="utf-8")
    return json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False)


def main() -> None:
    try:
        out = run(sys.stdin.read(), dict(os.environ))
    except Exception:  # noqa: BLE001 — a Stop hook must never break the session
        return
    if out:
        sys.stdout.write(out + "\n")


if __name__ == "__main__":
    main()
