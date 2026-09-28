"""The run-continuity Stop push: block a rig RUN that stops mid-flow, and nothing else.

The two Stop hooks rig retired (2.2.2, 3.3.1) failed by firing where they had no business:
sessions without a rig RUN, provider subprocesses, every turn. Most of these tests pin the
cases that must stay *silent*; the block itself is pinned once end to end through the
real shell entry point.
"""
import json
import subprocess
from pathlib import Path

import pytest

from rig_workbench.workbench import stop_continue

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "continue-rig-run.sh"

HEADER = "▸ rig | recipe: bugfix | step: implement (3/6) | gate: none | backend: manual | mode: {mode}"


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _assistant(*texts, tools=()):
    content = [{"type": "text", "text": t} for t in texts]
    content += [{"type": "tool_use", **tool} for tool in tools]
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _tool_result(tool_use_id, text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}


def _turn(*assistant_texts, mode="gated", header=None):
    head = header if header is not None else HEADER.format(mode=mode)
    return [_user("/rig:go fix the login bug"), _assistant(head, *assistant_texts)]


def _decide(rows, payload=None, env=None, previous=None):
    return stop_continue.decide(payload or {"stop_hook_active": False}, rows, env or {}, previous)


# ── the one case that blocks ────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["gated", "autonomous"])
def test_mid_step_stop_without_a_question_is_pushed(mode):
    verdict = _decide(_turn("── step implement ▸ dispatch → subagent", "Implemented the parser.",
                            mode=mode))
    assert verdict is not None
    reason, signature = verdict
    assert "implement (3/6)" in reason
    assert signature == "implement|3/6|none"


def test_autonomous_stop_after_a_step_boundary_is_pushed():
    rows = _turn("── step implement ▸ done", "Moving on.", mode="autonomous")
    assert _decide(rows) is not None


# ── every case that must let the session stop ───────────────────────────────

def test_no_header_means_no_rig_run_and_no_push():
    rows = [_user("fix it"), _assistant("Done, the parser is fixed.")]
    assert _decide(rows) is None


def test_a_header_from_an_earlier_turn_does_not_count():
    rows = _turn("Working.") + [_user("what does this function do"), _assistant("It parses.")]
    assert _decide(rows) is None


@pytest.mark.parametrize("header", [
    "▸ rig | recipe: bugfix | step: report (6/6) | gate: passed | mode: autonomous",
    "▸ rig | recipe: bugfix | step: implement (3/6) | gate: REJECT | mode: autonomous",
    "▸ rig | recipe: bugfix | step: verify (4/6) | gate: pending (try 2/2) | stuck: 2/2 | mode: autonomous",
    "▸ rig | recipe: bugfix | step: implement | gate: none | mode: autonomous",
])
def test_last_step_reject_stuck_and_unknown_position_are_not_pushed(header):
    assert _decide(_turn("Stopping here.", header=header)) is None


@pytest.mark.parametrize("ending", [
    "次の step に進みますか？",
    "Shall I apply the migration now?",
    "方針 A と B のどちらにしますか。",
    "Which option do you prefer:\n\n```\nA\nB\n```",
])
def test_a_turn_that_asks_the_person_is_not_pushed(ending):
    assert _decide(_turn(ending, mode="autonomous")) is None


def test_gated_run_may_stop_at_a_step_boundary():
    assert _decide(_turn("── step implement ▸ done", "Implemented.", mode="gated")) is None


def test_waiting_for_a_background_task_is_not_pushed():
    rows = _turn("Dispatched the reviewer.", mode="autonomous")
    rows[-1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {"prompt": "review"}})
    rows.append(_tool_result("toolu_1", "Async agent launched successfully. agentId: a1"))
    assert _decide(rows) is None

    notified = rows + [{"type": "user", "origin": {"kind": "task-notification"},
                        "message": {"role": "user", "content":
                                    "<task-notification><task-id>a1</task-id><tool-use-id>toolu_1"
                                    "</tool-use-id><status>completed</status><result>ok</result>"
                                    "</task-notification>"}},
                        _assistant("Review is in; continuing.")]
    assert _decide(notified) is not None


def test_run_in_background_bash_counts_as_pending():
    rows = _turn("Tests are running.", mode="autonomous")
    rows[-1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_2", "name": "Bash",
         "input": {"command": "pytest", "run_in_background": True}})
    assert _decide(rows) is None


def test_a_foreground_agent_is_not_pending():
    rows = _turn("Got the review back.", mode="autonomous")
    rows[-1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_3", "name": "Agent", "input": {"prompt": "review"}})
    rows.append(_tool_result("toolu_3", "VERDICT: PASS"))
    rows.append(_assistant("Continuing."))
    assert _decide(rows) is not None


def test_a_second_push_needs_the_header_to_have_moved():
    rows = _turn("Still implementing.", mode="autonomous")
    active = {"stop_hook_active": True}
    assert _decide(rows, active, previous="implement|3/6|none") is None
    assert _decide(rows, active, previous="implement|2/6|none") is not None
    # A new stop chain (the person typed something) may push once at the same place.
    assert _decide(rows, {"stop_hook_active": False}, previous="implement|3/6|none") is not None


@pytest.mark.parametrize("env", [{"RIG_PROVIDER_SUBPROCESS": "1"}, {"RIG_AUTO_CONTINUE": "0"},
                                 {"RIG_AUTO_CONTINUE": "off"}])
def test_provider_subprocess_and_opt_out_stand_down(env):
    assert _decide(_turn("Working.", mode="autonomous"), env=env) is None


def test_stop_hook_feedback_rows_are_not_a_new_prompt():
    rows = _turn("Working.", mode="autonomous")
    rows.append(_user("Stop hook feedback:\n[rig run-continuity] The rig RUN is at step ..."))
    rows.append(_assistant("Continuing the step."))
    assert _decide(rows) is not None


# ── the shell entry point ───────────────────────────────────────────────────

def _run_hook(tmp_path, rows, payload_extra=None, env_extra=None):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          encoding="utf-8")
    payload = {"hook_event_name": "Stop", "session_id": "s-1", "stop_hook_active": False,
               "transcript_path": str(transcript), **(payload_extra or {})}
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "state"),
           **(env_extra or {})}
    return subprocess.run(["/bin/sh", str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, cwd=tmp_path, timeout=10)


def test_hook_blocks_once_then_lets_the_same_stop_through(tmp_path):
    rows = _turn("Implemented the parser.", mode="autonomous")
    first = _run_hook(tmp_path, rows)
    assert first.returncode == 0 and first.stderr == ""
    out = json.loads(first.stdout)
    assert out["decision"] == "block" and "implement (3/6)" in out["reason"]
    assert (tmp_path / "state" / "rig" / "continue-rig-run" / "s-1").read_text().strip() \
        == "implement|3/6|none"

    again = _run_hook(tmp_path, rows, {"stop_hook_active": True})
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


@pytest.mark.parametrize("payload", ["not json", "null", "{}", '{"transcript_path": "/nope"}'])
def test_hook_fails_open_and_writes_nothing(tmp_path, payload):
    result = subprocess.run(["/bin/sh", str(HOOK)], input=payload, text=True, capture_output=True,
                            env={"PATH": "/usr/bin:/bin", "XDG_STATE_HOME": str(tmp_path)},
                            cwd=tmp_path, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert list(tmp_path.iterdir()) == []


def test_hook_is_silent_in_a_provider_subprocess(tmp_path):
    result = _run_hook(tmp_path, _turn("Working.", mode="autonomous"),
                       env_extra={"RIG_PROVIDER_SUBPROCESS": "1"})
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert not (tmp_path / "state").exists()


def test_hook_is_registered_for_stop_only():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    where = [event for event, entries in hooks.items()
             if "continue-rig-run.sh" in json.dumps(entries)]
    assert where == ["Stop"]
