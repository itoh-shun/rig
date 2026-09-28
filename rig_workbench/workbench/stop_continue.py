"""Stop hook: when a rig RUN tries to end its turn, decide stop, retry or continue.

A session that stops mid-flow for no reason costs a turn in which the person types
「進めて」 (`wb nudges` counts them). A session that is pushed on when it should have
stopped is worse: it walks into a push, a deploy or the same failure again. So the rules
are ordered with **stop first**, and anything the rules do not recognise is a stop
(SKILL.md §6 ⑤). `evaluate` returns `(verdict, rule)`; only `continue` and `retry` block.

0. Stand down — provider subprocess (`RIG_PROVIDER_SUBPROCESS`, #592) or
   `RIG_AUTO_CONTINUE=0|off|false|no`.                                  rule `off`
1. Not a RUN — no run-status header with a step position in this turn.  rule `no-run`
2. Declared — the turn ends with `▸ stop: <code>` (`turns.parse_stop`). A declared stop
   is always honoured; an unknown code is still a declaration.         rule `declared`
3. Escalation already on screen — gate `REJECT`, `stuck: 2/2`, or the last step (`n >= N`).
                                                                       rule `escalated`
4. The person was asked — the last text is a question or hands over a decision.
                                                                       rule `asked`
5. Gated step boundary — `mode: gated` and the turn reached `── step … ▸ done`: gated
   RUNs stop after each step by design.                                 rule `step-gate`
6. Waiting — a background launch has no task-notification yet.         rule `waiting`
7. Next move is outward or irreversible — the last text names a push, a PR, a deploy, a
   publish, a migration or a delete, or a line in it trips the `scan-destructive`
   patterns. The person decides those, declared or not.                 rule `outward`
8. The last tool call failed:
   * capability — auth (401/403), permission denied, a missing command or module, an
     unreachable host. Retrying cannot help.                            rule `capability`
   * repeated — the same error text as the failure before it.           rule `repeated`
   * transient — timeout, connection reset, 429, 502/503/504, rate limit. Retried
     **once** (verdict `retry`); if the model stops again at the same place it is let go.
   * anything else (a failing test, a type error) is not retried as-is: it is work — fix
     and re-run — and falls through to 9, where the stuck-guard counts it.
9. Continue — the header's step is not the last and nothing above applies (verdict
   `continue`). When the model is already continuing because of this hook
   (`stop_hook_active`), it is pushed again only if the header moved since the previous
   push, so pushes per stop chain are bounded by the steps left.       rule `no-progress`

Anything unexpected — unreadable payload, missing transcript, a parse error — means no
output and exit 0: this hook fails open. State (the last push, per session) is written
only when it blocks, under `$XDG_STATE_HOME/rig/continue-rig-run/`.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys

from ..ports.local import OS_ENV
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


_CAPABILITY_RE = re.compile(
    r"\b40[13]\b|permission denied|not authenticated|authentication (?:failed|required)"
    r"|unauthori[sz]ed|forbidden|command not found|not installed|no module named"
    r"|could not resolve host|network is unreachable|access denied", re.IGNORECASE)
_TRANSIENT_RE = re.compile(
    r"timed? ?out|timeout|econnreset|connection reset|\b429\b|rate.?limit|\b50[234]\b"
    r"|temporarily unavailable|service unavailable|eai_again|remote end hung up|early eof",
    re.IGNORECASE)
#: Next moves the person decides. Deliberately broad: a false match only means the stop
#: is honoured, the cheap direction.
_OUTWARD_RE = re.compile(
    r"git push|gh pr (?:create|merge)|pull request|プルリク|\bPR\s*を|npm publish|twine upload"
    r"|terraform apply|kubectl (?:apply|delete)|deploy|デプロイ|本番|リリース|publish"
    r"|migrat|マイグレーション|drop (?:table|database)|削除します|force-push|--force\b",
    re.IGNORECASE)


def _signature(header: dict, verdict: str) -> str:
    return (f"{header['step_id']}|{header['n']}/{header['total']}|{header.get('gate', '')}"
            f"|{verdict}")


def _outward(text: str) -> bool:
    if _OUTWARD_RE.search(text):
        return True
    from .destructive import scan_line  # only reached for a RUN about to be pushed
    return any(scan_line(line, "stop", i) for i, line in enumerate(text.splitlines(), 1))


def _tool_results(rows: list[dict]) -> list[dict]:
    return [b for row in rows if row.get("type") == "user"
            for b in _blocks(row) if b.get("type") == "tool_result"]


def _normalised_error(text: str) -> str:
    return " ".join(re.sub(r"\d+", "#", text).split())[:400]


def last_failure(turn: list[dict]) -> str | None:
    """How the turn's last tool call failed: `capability`, `repeated`, `transient`,
    `deterministic`, or None when the last tool call did not fail."""
    results = _tool_results(turn)
    if not results or not results[-1].get("is_error"):
        return None
    text = _tool_result_text(results[-1])
    if _CAPABILITY_RE.search(text):
        return "capability"
    earlier = [r for r in results[:-1] if r.get("is_error")]
    if earlier and _normalised_error(_tool_result_text(earlier[-1])) == _normalised_error(text):
        return "repeated"
    if _TRANSIENT_RE.search(text):
        return "transient"
    return "deterministic"


def evaluate(payload: dict, rows: list[dict], env: dict,
             previous_signature: str | None) -> tuple[str, str, dict | None]:
    """(verdict, rule, header). verdict is `stop`, `retry` or `continue`; rule names the
    numbered rule in the module docstring that decided it."""
    if env.get("RIG_PROVIDER_SUBPROCESS") or \
            env.get("RIG_AUTO_CONTINUE", "").strip().lower() in _OFF:
        return "stop", "off", None
    turn = turns.current_turn(rows)
    said = turns.assistant_text(turn)
    header = turns.parse_header(said)
    if header is None or header["n"] is None or header["total"] is None:
        return "stop", "no-run", header
    last = turns.last_assistant_text(turn)
    if turns.parse_stop(last) is not None:
        return "stop", "declared", header
    gate = header.get("gate", "").lower()
    if (gate.startswith("reject") or header.get("stuck", "").replace(" ", "") == "2/2"
            or header["n"] >= header["total"]):
        return "stop", "escalated", header
    if turns.asks_user(last):
        return "stop", "asked", header
    if not header.get("mode", "").lower().startswith("autonomous") and _DONE_RE.search(said):
        return "stop", "step-gate", header
    if pending_background(rows):
        return "stop", "waiting", header
    if _outward(last):
        return "stop", "outward", header
    failure = last_failure(turn)
    if failure in ("capability", "repeated"):
        return "stop", failure, header
    verdict = "retry" if failure == "transient" else "continue"
    if payload.get("stop_hook_active") and _signature(header, verdict) == previous_signature:
        return "stop", "no-progress", header
    return verdict, verdict, header


_DECLARE = ("If you should stop, end with one line `▸ stop: <code>` instead — done | waiting | "
            "step-gate | needs-decision:<destructive-operation|ambiguous-requirement|"
            "policy-requires-approval|budget-exhausted|capability-missing> | "
            "blocked:<capability-missing|gate-reject|repeated-failure>.")


def decide(payload: dict, rows: list[dict], env: dict,
           previous_signature: str | None) -> tuple[str, str] | None:
    """(reason, signature) when the stop should be blocked, else None."""
    verdict, _rule, header = evaluate(payload, rows, env, previous_signature)
    if verdict == "stop" or header is None:
        return None
    step = f"{header['step_id']} ({header['n']}/{header['total']})"
    if verdict == "retry":
        reason = (f"[rig run-continuity] Step {step}: the last command failed in a way that "
                  "looks transient (timeout, reset, rate limit, 5xx). Re-run it once, "
                  "unchanged. If it fails again, stop with `▸ stop: blocked:repeated-failure`. "
                  + _DECLARE)
    else:
        reason = (f"[rig run-continuity] Step {step}: this turn ended without a question, a "
                  "decision for the person, or a stop reason. If the next move is already "
                  "fixed by the plan and stays inside the worktree, carry on with it now "
                  "(SKILL.md §6 ⑤). " + _DECLARE)
    return reason, _signature(header, verdict)


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
        out = run(sys.stdin.read(), dict(OS_ENV.snapshot()))
    except Exception:  # noqa: BLE001 — a Stop hook must never break the session
        return
    if out:
        sys.stdout.write(out + "\n")


if __name__ == "__main__":
    main()
