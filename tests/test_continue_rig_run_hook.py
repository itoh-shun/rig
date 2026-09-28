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

from rig_workbench.assurance import development_loop
from rig_workbench.workbench import stop_continue, turns

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
    assert signature == "implement|3/6|none|continue"


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
    assert _decide(rows, active, previous="implement|3/6|none|continue") is None
    assert _decide(rows, active, previous="implement|2/6|none|continue") is not None
    # A new stop chain (the person typed something) may push once at the same place.
    assert _decide(rows, {"stop_hook_active": False},
                   previous="implement|3/6|none|continue") is not None


@pytest.mark.parametrize("env", [{"RIG_PROVIDER_SUBPROCESS": "1"}, {"RIG_AUTO_CONTINUE": "0"},
                                 {"RIG_AUTO_CONTINUE": "off"}])
def test_provider_subprocess_and_opt_out_stand_down(env):
    assert _decide(_turn("Working.", mode="autonomous"), env=env) is None


def test_stop_hook_feedback_rows_are_not_a_new_prompt():
    rows = _turn("Working.", mode="autonomous")
    rows.append(_user("Stop hook feedback:\n[rig run-continuity] The rig RUN is at step ..."))
    rows.append(_assistant("Continuing the step."))
    assert _decide(rows) is not None


# ── the rule table: stop wins, retry once, continue only on a fixed next move ──

def _rule(rows, payload=None, previous=None):
    verdict, rule, _ = stop_continue.evaluate(payload or {"stop_hook_active": False}, rows, {},
                                              previous)
    return verdict, rule


def _failed(rows, *errors):
    """Append tool calls whose results are the given errors, then a closing text."""
    for i, error in enumerate(errors):
        rows[-1]["message"]["content"].append(
            {"type": "tool_use", "id": f"toolu_e{i}", "name": "Bash", "input": {"command": "x"}})
        rows.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_e{i}", "is_error": True,
             "content": error}]}})
        rows.append(_assistant("Hmm."))
    return rows


@pytest.mark.parametrize("line", [
    "▸ stop: done",
    "▸ stop: waiting",
    "▸ stop: step-gate",
    "▸ stop: needs-decision:destructive-operation — next is a push to main",
    "▸ stop: blocked:capability-missing — gh is not authenticated",
    "▸ stop: blocked:something-new",
])
def test_a_declared_stop_is_always_honoured(line):
    assert _rule(_turn("Summary of the step.", line, mode="autonomous")) == ("stop", "declared")


def test_parse_stop_knows_its_vocabulary():
    assert turns.parse_stop("x\n▸ stop: needs-decision:budget-exhausted")["known"] is True
    assert turns.parse_stop("▸ stop: blocked:something-new")["known"] is False
    assert turns.parse_stop("no declaration") is None


def test_the_escalation_codes_are_the_development_loops():
    assert turns.STOP_NEEDS_DECISION == development_loop.ESCALATIONS


@pytest.mark.parametrize("ending", [
    "Tests pass. Next: git push origin main.",
    "実装が終わりました。次は本番へデプロイです。",
    "Next I will open a pull request.",
    "Next I will run the migration.",
    "Cleaning up with rm -rf / next.",
])
def test_an_outward_or_irreversible_next_move_is_left_to_the_person(ending):
    assert _rule(_turn(ending, mode="autonomous")) == ("stop", "outward")


@pytest.mark.parametrize("error", [
    "fatal: Authentication failed for 'https://github.com/o/r'",
    "HTTP 403 Forbidden",
    "bash: gh: command not found",
    "ModuleNotFoundError: No module named 'pytest'",
])
def test_a_missing_capability_is_not_retried(error):
    assert _rule(_failed(_turn("Running it.", mode="autonomous"), error)) == ("stop", "capability")


def test_the_same_failure_twice_is_not_retried():
    rows = _failed(_turn("Running it.", mode="autonomous"),
                   "AssertionError: expected 3 got 4 (line 12)",
                   "AssertionError: expected 3 got 4 (line 12)")
    assert _rule(rows) == ("stop", "repeated")


def test_a_transient_failure_is_retried_once():
    rows = _failed(_turn("Running it.", mode="autonomous"), "ReadTimeout: timed out after 30s")
    assert _rule(rows) == ("retry", "retry")
    reason, signature = _decide(rows)
    assert "Re-run it once" in reason and signature.endswith("|retry")
    assert _rule(rows, {"stop_hook_active": True}, previous=signature) == ("stop", "no-progress")


def test_a_deterministic_failure_is_work_not_a_retry():
    rows = _failed(_turn("Running it.", mode="autonomous"), "TypeError: 'NoneType' is not callable")
    assert _rule(rows) == ("continue", "continue")


def test_a_failure_that_a_later_call_fixed_does_not_count():
    rows = _failed(_turn("Running it.", mode="autonomous"), "bash: gh: command not found")
    rows[-1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_ok", "name": "Bash", "input": {"command": "y"}})
    rows.append(_tool_result("toolu_ok", "ok"))
    rows.append(_assistant("Worked around it."))
    assert _rule(rows) == ("continue", "continue")


@pytest.mark.parametrize("header,expected", [
    ("▸ rig | recipe: bugfix | step: implement (3/6) | gate: REJECT | mode: autonomous", "escalated"),
    ("▸ rig | recipe: bugfix | step: report (6/6) | gate: passed | mode: autonomous", "escalated"),
])
def test_escalations_on_screen_win_over_continue(header, expected):
    assert _rule(_turn("Stopping.", header=header)) == ("stop", expected)


def test_rules_name_why_the_session_was_let_go():
    assert _rule([_user("hi"), _assistant("hello")]) == ("stop", "no-run")
    assert _rule(_turn("次へ進みますか？", mode="autonomous")) == ("stop", "asked")
    assert _rule(_turn("── step implement ▸ done", "Done.")) == ("stop", "step-gate")


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
        == "implement|3/6|none|continue"

    again = _run_hook(tmp_path, rows, {"stop_hook_active": True})
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


@pytest.mark.parametrize("payload", ["not json", "null", "{}", '{"transcript_path": "/nope"}'])
def test_hook_fails_open_and_writes_nothing(tmp_path, payload):
    result = subprocess.run(["/bin/sh", str(HOOK)], input=payload, text=True, capture_output=True,
                            env={"PATH": "/usr/bin:/bin", "XDG_STATE_HOME": str(tmp_path)},
                            cwd=tmp_path, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    assert list(tmp_path.iterdir()) == []


def test_hook_output_is_ascii_so_a_lang_c_stream_can_carry_it(tmp_path):
    header = "▸ rig | recipe: bugfix | step: 実装 (3/6) | gate: none | mode: autonomous"
    result = _run_hook(tmp_path, _turn("パーサを直しました。", header=header),
                       env_extra={"LANG": "C", "LC_ALL": "C", "PYTHONIOENCODING": "ascii"})
    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.isascii()
    assert "実装 (3/6)" in json.loads(result.stdout)["reason"]


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
