"""`wb wakeups`: stale wake-ups, classified and ratcheted.

Every fixture is synthetic and built here — real transcripts are private. Each fixture
carries exactly the one feature that separates the branch under test from its
neighbours, so that removing the rule that reads the feature flips the answer.
"""

import hashlib
import itertools
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from rig_workbench.workbench import wakeups

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

OUT = "/tmp/claude-1000/proj/session/tasks/{}.output"

#: Every harness-written row carries a uuid; builders hand out unique ones by default.
_UUIDS = itertools.count()


def _uuid():
    return f"uuid-{next(_UUIDS)}"


# ── row builders ──────────────────────────────────────────────────────────────

def notification(task_id, *, status="completed", result="done", launch=None,
                 output_file=None, as_list=False):
    body = (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
            f"<tool-use-id>{launch or 'toolu_' + task_id}</tool-use-id>\n"
            f"<output-file>{output_file or OUT.format(task_id)}</output-file>\n"
            f"<status>{status}</status>\n<summary>Background command finished</summary>\n"
            f"<result>{result}\n</result>\n</task-notification>")
    content = [{"type": "text", "text": body}] if as_list else body
    return {"type": "user", "uuid": _uuid(), "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": content}}


def handback(task_id):
    return {"type": "user",
            "origin": {"kind": "peer", "handback": True, "from": task_id,
                       "senderTaskId": task_id},
            "message": {"role": "user", "content": f"<agent-message from=\"{task_id}\">report"}}


def tool_use(tool_id, name, **inputs):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inputs}


def text(value):
    return {"type": "text", "text": value}


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def tool_result(tool_id):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


def human(value):
    return {"type": "user", "origin": {"kind": "human"},
            "message": {"role": "user", "content": value}}


def launch_bash(task_id, command="make test"):
    return assistant(tool_use("toolu_" + task_id, "Bash", command=command,
                              run_in_background=True))


def kinds(rows):
    return [entry["kind"] for entry in wakeups.classify_rows(rows)]


#: The harness's sentence for a subagent that already handed back (see
#: `wakeups.HANDBACK_SENTENCE`), written out here rather than imported so a change to the
#: constant is caught. Synthetic id; the sentence is Claude Code's, not a transcript's.
def marker(task_id):
    return (f"This agent's report was delivered to you as a message from \"{task_id}\" "
            "(its SubagentHandback call). Read it there; it is not repeated here.")


MARKER = marker("a1")


def polls(rows):
    return [entry["polls"] for entry in wakeups.classify_rows(rows)]


def with_uuid(row, uuid):
    return {**row, "uuid": uuid}


def event_notification(task_id="m1", line="ERROR: disk full"):
    body = (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
            f"<event>{line}</event>\n</task-notification>")
    return {"type": "user", "uuid": _uuid(), "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": body}}


def bare_notification(body):
    return {"type": "user", "uuid": _uuid(), "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": body}}


# ── classification ────────────────────────────────────────────────────────────

def test_completed_with_the_harness_marker_is_a_handback_dup():
    assert kinds([notification("a1", result=MARKER)]) == ["handback-dup"]


@pytest.mark.parametrize("variant", [
    MARKER.upper(),
    MARKER.replace(" ", "\n  "),
    "\n\t" + MARKER.replace("it is not", "It  is\tNOT") + "  \n",
])
def test_the_sentence_matches_regardless_of_case_and_whitespace(variant):
    assert kinds([notification("a1", result=variant)]) == ["handback-dup"]


@pytest.mark.parametrize("partial", [
    "the report was delivered to you as a message",
    "it is not repeated here",
    "report was DELIVERED  to\tyou as a message.  It is Not\nRepeated here.",
])
def test_part_of_the_sentence_is_not_the_sentence(partial):
    assert kinds([notification("a1", result=partial)]) == ["fresh"]


@pytest.mark.parametrize("result", [
    "Summary of findings. The harness would say: " + MARKER,
    MARKER + " Also: three new failures in test_x.",
])
def test_a_report_quoting_the_sentence_is_fresh(result):
    assert kinds([notification("a1", result=result)]) == ["fresh"]


def test_the_sentence_for_another_task_id_is_fresh():
    assert kinds([notification("a2", result=MARKER)]) == ["fresh"]
    assert kinds([notification("a2", result=marker("a2"))]) == ["handback-dup"]


def test_an_event_tag_inside_the_result_does_not_make_an_event():
    rows = [notification("a1", result="log line: <event>disk full</event>"),
            notification("a2", result="<event>x</event> " + marker("a2"))]
    assert kinds(rows) == ["fresh", "fresh"]


def test_a_status_quoted_inside_the_result_does_not_set_the_status():
    body = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
            "<result>the job printed <status>failed</status> once\n</result>\n"
            "</task-notification>")
    assert kinds([bare_notification(body)]) == ["fresh"]


def test_an_earlier_handback_message_alone_does_not_make_a_notification_stale():
    """A resumed subagent may deliver something new; only the harness saying "not
    repeated" is evidence."""
    assert kinds([handback("a1"), notification("a1", result="new findings")]) == ["fresh"]


def test_failed_is_never_stale_even_after_a_handback_and_with_the_marker():
    rows = [handback("a1"), notification("a1", status="failed", result=MARKER)]
    assert kinds(rows) == ["failed"]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["stale"] == 0 and report["kinds"]["failed"] == 1
    assert report["total_notifications"] == 1


@pytest.mark.parametrize("status", ["killed", "stopped", "KILLED"])
def test_killed_after_a_poll_is_killed_and_never_stale(status):
    rows = [launch_bash("b1"),
            assistant(tool_use("toolu_r", "Read", file_path=OUT.format("b1"))),
            notification("b1", status=status, result=MARKER)]
    assert kinds(rows) == ["killed"]
    assert polls(rows) == [1]
    assert wakeups.measure({"s.jsonl": (rows, 0)})["stale"] == 0


def test_an_unknown_status_is_fresh():
    assert kinds([notification("a1", status="running", result=MARKER)]) == ["fresh"]


def test_monitor_events_and_untagged_notices_are_events():
    rows = [event_notification(),
            bare_notification("<task-notification>3 background tasks finished</task-notification>"),
            bare_notification("<task-notification><task-id>x</task-id></task-notification>"),
            bare_notification("<task-notification><status>completed</status></task-notification>")]
    assert kinds(rows) == ["event"] * 4


def test_events_are_left_out_of_the_denominator():
    rows = [notification("a1", result=MARKER), notification("a2")]
    rows += [event_notification(f"m{i}") for i in range(6)]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["total_notifications"] == 2
    assert report["events_excluded"] == 6
    assert report["stale_bp"] == 5000


def test_list_content_reads_the_same_as_string_content():
    assert kinds([notification("a1", result=MARKER, as_list=True)]) == ["handback-dup"]


def test_rows_that_are_not_task_notifications_are_ignored():
    rows = [human(MARKER), assistant(text(MARKER)), handback("a1")]
    assert kinds(rows) == []


# ── polls: behaviour, not staleness ───────────────────────────────────────────

def test_reading_only_the_output_file_path_counts_as_a_poll():
    """The output path here does not contain the task id, so only the output-file needle
    can see this read."""
    path = "/tmp/elsewhere/build.log"
    rows = [launch_bash("b1"),
            assistant(tool_use("toolu_r", "Bash", command=f"tail -n 5 {path}")),
            notification("b1", output_file=path)]
    assert polls(rows) == [1]


def test_naming_the_task_id_counts_as_a_poll():
    rows = [launch_bash("b1"),
            assistant(tool_use("toolu_r", "TaskOutput", task_id="b1")),
            notification("b1", output_file="/tmp/x.log")]
    assert polls(rows) == [1]


def test_every_poll_is_counted_not_just_the_first():
    rows = [launch_bash("b1")]
    rows += [assistant(tool_use(f"toolu_r{i}", "Read", file_path=OUT.format("b1")))
             for i in range(3)]
    rows.append(notification("b1"))
    assert polls(rows) == [3]


def test_the_launch_and_sendmessage_are_not_polls():
    rows = [assistant(tool_use("toolu_b1", "Bash", command=f"make > {OUT.format('b1')}",
                               run_in_background=True)),
            assistant(tool_use("toolu_s", "SendMessage", to="b1", message="and also?")),
            notification("b1")]
    assert polls(rows) == [0]


def test_a_tool_use_before_the_launch_is_not_a_poll():
    rows = [assistant(tool_use("toolu_r", "Read", file_path=OUT.format("b1"))),
            launch_bash("b1"), notification("b1")]
    assert polls(rows) == [0]


def test_the_window_restarts_after_each_notification_of_the_same_task():
    """A handback-dup, then a read, then a re-notification: the read belongs to the second
    notification's window only."""
    rows = [launch_bash("a1"),
            notification("a1", result=MARKER),
            assistant(tool_use("toolu_r", "Read", file_path=OUT.format("a1"))),
            notification("a1", result="more")]
    assert kinds(rows) == ["handback-dup", "fresh"]
    assert polls(rows) == [0, 1]


def test_polls_never_make_a_notification_stale():
    rows = [launch_bash("b1"),
            assistant(tool_use("toolu_r", "Read", file_path=OUT.format("b1"))),
            notification("b1")]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["kinds"]["fresh"] == 1
    assert report["stale"] == 0 and report["stale_bp"] == 0
    assert report["polls"] == 1 and report["polled_notifications"] == 1


# ── reading and aggregation ───────────────────────────────────────────────────

def test_malformed_lines_are_counted_never_dropped():
    rows, unreadable = wakeups.parse_lines(['{"type": "user"}', "not json", "[1, 2]", "", "  "])
    assert len(rows) == 1 and unreadable == 2


def test_reply_chars_sum_visible_text_until_the_next_real_user_turn():
    rows = [notification("a1", result=MARKER),
            assistant(text("12345"), tool_use("toolu_x", "Bash", command="ls")),
            tool_result("toolu_x"),
            assistant(text("678")),
            human("next"),
            assistant(text("not counted"))]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["stale_reply_chars"] == 8 and report["stale_reply_turns"] == 1


def test_stale_bp_is_integer_basis_points_and_zero_notifications_is_unmeasured():
    rows = [notification("a1", result=MARKER)] + [notification(f"f{i}") for i in range(2)]
    assert wakeups.measure({"s.jsonl": (rows, 0)})["stale_bp"] == 3333
    empty = wakeups.measure({"s.jsonl": ([human("hi")], 0)})
    assert empty["state"] == "unmeasured" and empty["stale_bp"] is None


def test_rows_repeated_under_the_same_uuid_in_another_file_count_once():
    first = [with_uuid(notification("a1", result=MARKER), "u1"),
             with_uuid(notification("a2"), "u2")]
    resumed = first + [with_uuid(notification("a3"), "u3")]
    report = wakeups.measure({"a.jsonl": (first, 0), "b.jsonl": (resumed, 0)})
    assert report["total_notifications"] == 3
    assert report["stale"] == 1
    assert report["duplicate_rows_skipped"] == 2


def test_repeats_that_differ_only_in_session_metadata_are_duplicates():
    row = with_uuid(notification("a1"), "u1")
    resumed = {**row, "sessionId": "other", "cwd": "/elsewhere", "version": "9.9"}
    report = wakeups.measure({"a.jsonl": ([row], 0), "b.jsonl": ([resumed], 0)})
    assert report["duplicate_rows_skipped"] == 1 and report["uuid_conflicts"] == 0
    assert report["total_notifications"] == 1


def test_one_uuid_with_different_content_is_a_conflict_not_a_duplicate(tmp_path):
    first = with_uuid(notification("a1", result=MARKER), "u1")
    other = with_uuid(notification("a1", result="new findings"), "u1")
    rows = [first] + [notification(f"f{i}") for i in range(30)]
    report = wakeups.measure({"a.jsonl": (rows, 0), "b.jsonl": ([other], 0)})
    assert report["uuid_conflicts"] == 1 and report["duplicate_rows_skipped"] == 0
    path = ceiling(tmp_path, 5000)
    before = path.read_bytes()
    verdict = wakeups.apply_ratchet(report, path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("not-judged", 3)
    assert "uuid" in verdict["message"]
    assert path.read_bytes() == before


def test_a_notification_without_a_uuid_is_unreadable_and_not_judged(tmp_path):
    bare = {k: v for k, v in notification("a1").items() if k != "uuid"}
    rows = [bare] + [notification(f"f{i}") for i in range(30)]
    report = wakeups.measure({"a.jsonl": (rows, 0)})
    assert report["unreadable_lines"] == 1
    assert report["total_notifications"] == 30
    verdict = wakeups.apply_ratchet(report, ceiling(tmp_path, 5000))
    assert (verdict["state"], verdict["exit"]) == ("not-judged", 3)


def test_other_rows_without_a_uuid_are_kept():
    rows = [human("hi"), human("hi")]
    transcripts, skipped, conflicts = wakeups.dedupe({"a.jsonl": (rows, 0)})
    assert transcripts["a.jsonl"] == (rows, 0) and (skipped, conflicts) == (0, 0)


def write_transcript(path, stale, fresh, extra_lines=()):
    rows = [notification(f"d{i}", result=marker(f"d{i}")) for i in range(stale)]
    rows += [notification(f"f{i}") for i in range(fresh)]
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_one_file_reached_through_dotdot_and_a_symlink_is_read_once(tmp_path):
    (tmp_path / "d").mkdir()
    real = write_transcript(tmp_path / "d" / "s.jsonl", stale=1, fresh=1)
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    dotted = tmp_path / "d" / ".." / "d" / "s.jsonl"
    report = wakeups.measure_paths([str(real), str(dotted), str(link), str(tmp_path / "d")])
    assert report["files"] == 1
    assert report["total_notifications"] == 2


def test_directories_are_searched_non_recursively(tmp_path):
    write_transcript(tmp_path / "b.jsonl", 0, 1)
    write_transcript(tmp_path / "a.jsonl", 0, 1)
    (tmp_path / "sub").mkdir()
    write_transcript(tmp_path / "sub" / "c.jsonl", 0, 1)
    (tmp_path / "notes.txt").write_text("x")
    names = [p.name for p in wakeups.collect_files([str(tmp_path)])]
    assert names == ["a.jsonl", "b.jsonl"]


def test_since_days_keeps_a_file_exactly_at_the_cutoff_and_drops_one_second_older(tmp_path):
    now = 2_000_000_000.0
    cutoff = now - 3 * 86400
    at = write_transcript(tmp_path / "at.jsonl", 0, 1)
    older = write_transcript(tmp_path / "older.jsonl", 0, 1)
    os.utime(at, (cutoff, cutoff))
    os.utime(older, (cutoff - 1, cutoff - 1))
    names = [p.name for p in wakeups.collect_files([str(tmp_path)], since_days=3, now=now)]
    assert names == ["at.jsonl"]


def test_a_missing_path_is_an_error_not_an_empty_measurement(tmp_path):
    with pytest.raises(FileNotFoundError):
        wakeups.collect_files([str(tmp_path / "nope.jsonl")])


def test_the_report_states_what_it_does_not_see():
    report = wakeups.measure({"s.jsonl": ([notification("a1")], 0)})
    assert report["not_seen"] == wakeups.NOT_SEEN
    assert "not a staleness claim" in report["polls_note"].lower()
    human_text = wakeups.render_human(report)
    assert wakeups.NOT_SEEN in human_text and wakeups.POLLS_NOTE in human_text


# ── ratchet ───────────────────────────────────────────────────────────────────

def report_with(stale, fresh, unreadable=0):
    rows = [notification(f"d{i}", result=marker(f"d{i}")) for i in range(stale)]
    rows += [notification(f"f{i}") for i in range(fresh)]
    return wakeups.measure({"s.jsonl": (rows, unreadable)})


def ceiling(tmp_path, stale_bp_max, min_notifications=20, **extra):
    path = tmp_path / ".rig" / "wakeups-ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "rig.wakeups-ceiling/v1",
                                "stale_bp_max": stale_bp_max,
                                "min_notifications": min_notifications, **extra}))
    return path


def stored(path):
    return json.loads(path.read_text())


def test_over_the_ceiling_exits_1_and_leaves_the_file(tmp_path):
    path = ceiling(tmp_path, 1000)
    verdict = wakeups.apply_ratchet(report_with(3, 17), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("over", 1)
    assert stored(path)["stale_bp_max"] == 1000


def test_exactly_at_the_ceiling_exits_0(tmp_path):
    path = ceiling(tmp_path, 1500)
    verdict = wakeups.apply_ratchet(report_with(3, 17), path)
    assert (verdict["state"], verdict["exit"]) == ("ok", 0)


@pytest.mark.parametrize("stale,fresh,unreadable", [
    (0, 0, 0),     # nothing measured
    (1, 18, 0),    # 19 < 20
    (1, 29, 1),    # enough, but a line could not be read
])
def test_what_cannot_be_judged_exits_3_and_writes_nothing(tmp_path, stale, fresh, unreadable):
    path = ceiling(tmp_path, 5000)
    before = path.read_bytes()
    verdict = wakeups.apply_ratchet(report_with(stale, fresh, unreadable), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("not-judged", 3)
    assert path.read_bytes() == before


def test_tighten_lowers_the_ceiling_and_keeps_other_keys(tmp_path):
    path = ceiling(tmp_path, 2000, note="kept")
    verdict = wakeups.apply_ratchet(report_with(1, 19), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("tightened", 0)
    assert stored(path) == {"schema": "rig.wakeups-ceiling/v1", "stale_bp_max": 500,
                            "min_notifications": 20, "note": "kept"}


def test_below_the_ceiling_without_tighten_leaves_the_file_alone(tmp_path):
    path = ceiling(tmp_path, 2000)
    assert wakeups.apply_ratchet(report_with(1, 19), path)["state"] == "ok"
    assert stored(path)["stale_bp_max"] == 2000


def test_tighten_never_raises_the_ceiling(tmp_path):
    path = ceiling(tmp_path, 100)
    verdict = wakeups.apply_ratchet(report_with(4, 16), path, tighten=True)
    assert verdict["exit"] == 1
    assert stored(path)["stale_bp_max"] == 100


def test_the_lowering_helper_refuses_anything_not_lower():
    for value in (100, 101):
        with pytest.raises(ValueError):
            wakeups._lowered({"stale_bp_max": 100}, value)


def test_a_missing_ceiling_is_an_error_even_with_tighten(tmp_path):
    path = tmp_path / ".rig" / "wakeups-ceiling.json"
    for tighten in (False, True):
        verdict = wakeups.apply_ratchet(report_with(1, 19), path, tighten=tighten)
        assert (verdict["state"], verdict["exit"]) == ("missing", 2)
        assert "--init" in verdict["message"]
    assert not path.exists()


def test_init_creates_a_missing_ceiling_at_the_measured_value(tmp_path):
    path = tmp_path / ".rig" / "wakeups-ceiling.json"
    verdict = wakeups.apply_ratchet(report_with(1, 19), path, init=True)
    assert (verdict["state"], verdict["exit"]) == ("created", 0)
    assert stored(path) == {"schema": "rig.wakeups-ceiling/v1", "stale_bp_max": 500,
                            "min_notifications": 20}


def test_init_never_overwrites_and_never_creates_from_what_cannot_be_judged(tmp_path):
    path = ceiling(tmp_path, 100)
    verdict = wakeups.apply_ratchet(report_with(1, 19), path, init=True)
    assert verdict["exit"] == 2 and stored(path)["stale_bp_max"] == 100
    fresh_path = tmp_path / "new" / "c.json"
    assert wakeups.apply_ratchet(report_with(1, 5), fresh_path, init=True)["exit"] == 3
    assert wakeups.apply_ratchet(report_with(1, 29, 1), fresh_path, init=True)["exit"] == 3
    assert not fresh_path.exists()


def test_init_and_tighten_together_are_refused(tmp_path):
    verdict = wakeups.apply_ratchet(report_with(1, 19), tmp_path / "c.json",
                                    init=True, tighten=True)
    assert verdict["exit"] == 2 and not (tmp_path / "c.json").exists()


@pytest.mark.parametrize("key", ["stale_bp_max", "min_notifications"])
@pytest.mark.parametrize("bad", [True, False, -1, "x", 1.5, None])
def test_a_ceiling_with_a_bad_value_is_an_error(tmp_path, key, bad):
    path = ceiling(tmp_path, 1000)
    doc = stored(path)
    doc[key] = bad
    path.write_text(json.dumps(doc))
    verdict = wakeups.apply_ratchet(report_with(1, 19), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("invalid", 2)
    assert stored(path)[key] == bad


@pytest.mark.parametrize("body", ["not json", "[]", '{"schema": "rig.other/v1"}'])
def test_an_unreadable_ceiling_is_an_error(tmp_path, body):
    path = tmp_path / "c.json"
    path.write_text(body)
    assert wakeups.apply_ratchet(report_with(1, 19), path)["exit"] == 2


def test_a_failed_write_leaves_the_old_ceiling_and_no_temp_file(tmp_path, monkeypatch):
    path = ceiling(tmp_path, 2000)
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(wakeups.os, "replace", boom)
    with pytest.raises(OSError):
        wakeups.apply_ratchet(report_with(1, 19), path, tighten=True)
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


# ── the CLI ───────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    env = dict(os.environ, RIG_NO_CONTEXT_METER="1")
    return subprocess.run([sys.executable, str(WORKBENCH), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=env)


def test_cli_over_the_ceiling_prints_the_report_then_exits_1(tmp_path):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=10, fresh=10)
    path = ceiling(tmp_path, 1000)
    result = run_cli(["wakeups", "--transcripts", transcript, "--ratchet", path,
                      "--tighten", "--json"], tmp_path)
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["stale_bp"] == 5000 and payload["ratchet"]["state"] == "over"
    assert "exceed the ceiling" in result.stderr
    assert stored(path)["stale_bp_max"] == 1000


def test_cli_not_judged_exits_3_with_a_line_saying_so(tmp_path):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=0, fresh=30,
                                  extra_lines=["{broken"])
    path = ceiling(tmp_path, 1000)
    result = run_cli(["wakeups", "--transcripts", transcript, "--ratchet", path], tmp_path)
    assert result.returncode == 3, result.stderr
    assert "[NOT JUDGED]" in result.stderr and "unreadable" in result.stderr
    assert "stale (handback-dup): 0" in result.stdout


def test_cli_runs_outside_a_git_repository_and_names_the_counts(tmp_path):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=1, fresh=3)
    result = run_cli(["wakeups", "--transcripts", transcript], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "stale (handback-dup): 1 = 2500 bp" in result.stdout
    assert "Only task-notifications recorded" in result.stdout


@pytest.mark.parametrize("flag", ["--init", "--tighten"])
def test_cli_init_and_tighten_need_ratchet(tmp_path, flag):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=1, fresh=3)
    result = run_cli(["wakeups", "--transcripts", transcript, flag], tmp_path)
    assert result.returncode == 2
    assert "need --ratchet" in result.stderr


def test_cli_requires_transcripts(tmp_path):
    assert run_cli(["wakeups"], tmp_path).returncode == 2


def test_context_no_longer_carries_the_wakeup_flags(tmp_path):
    result = run_cli(["context", "--help"], tmp_path)
    assert result.returncode == 0
    for flag in ("--transcripts", "--ratchet", "--tighten", "--json"):
        assert flag not in result.stdout


#: sha256 of what `workbench.py context` printed for CONTEXT_FIXTURE at v3.3.1, before
#: `--transcripts` existed (2103 bytes). Without `--transcripts` the output must not move
#: by a byte. An intentional change to the legacy output must regenerate this from the
#: new expected output on purpose — never by re-running whatever this tree prints.
V331_CONTEXT_SHA256 = "c881cca248dd9fb94ffa93ce2e7c6029464485ecad69717cdff2e6eff6691c4b"
CONTEXT_FIXTURE = "\n".join([
    '{"ts": "2026-09-01T10:00:00+00:00", "command": "wb board", "bytes": 1200}',
    '{"ts": "2026-09-01T10:05:00+00:00", "command": "wb status", "bytes": 300, '
    '"task_id": "rig-20260901-100000-demo"}',
    '{"ts": "2026-09-01T11:30:00+00:00", "command": "wb diff", "bytes": 90000, '
    '"task_id": "rig-20260901-100000-demo"}',
    "not json"]) + "\n"


def test_context_without_transcripts_is_byte_identical_to_v331(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".rig").mkdir()
    (tmp_path / ".rig" / "context.jsonl").write_text(CONTEXT_FIXTURE)
    result = run_cli(["context"], tmp_path)
    assert result.returncode == 0
    assert len(result.stdout.encode()) == 2103
    assert hashlib.sha256(result.stdout.encode()).hexdigest() == V331_CONTEXT_SHA256


