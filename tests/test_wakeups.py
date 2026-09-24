"""`wb context --transcripts`: stale wake-ups, classified and ratcheted.

Every fixture is synthetic and built here — real transcripts are private. Each fixture
carries exactly the one feature that separates the branch under test from its
neighbours, so that removing the rule that reads the feature flips the answer.
"""

import hashlib
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


# ── row builders ──────────────────────────────────────────────────────────────

def notification(task_id, *, status="completed", result="done", launch=None,
                 output_file=None, as_list=False):
    body = (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
            f"<tool-use-id>{launch or 'toolu_' + task_id}</tool-use-id>\n"
            f"<output-file>{output_file or OUT.format(task_id)}</output-file>\n"
            f"<status>{status}</status>\n<summary>Background command finished</summary>\n"
            f"<result>{result}\n</result>\n</task-notification>")
    content = [{"type": "text", "text": body}] if as_list else body
    return {"type": "user", "origin": {"kind": "task-notification"},
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


# ── classification branches ───────────────────────────────────────────────────

def test_a_result_saying_it_was_delivered_as_a_message_is_a_handback_dup():
    rows = [notification("a1", result="This agent's report was delivered to you as a "
                                      "message from \"a1\". Read it there; it is not "
                                      "repeated here.")]
    assert kinds(rows) == ["handback-dup"]


def test_an_earlier_handback_from_the_same_task_is_a_handback_dup_without_the_marker():
    assert kinds([handback("a1"), notification("a1")]) == ["handback-dup"]


def test_a_handback_from_another_task_or_after_the_notification_does_not_count():
    assert kinds([handback("other"), notification("a1")]) == ["fresh"]
    assert kinds([notification("a1"), handback("a1")]) == ["fresh"]


def test_reading_the_output_file_before_the_notification_is_read_early():
    rows = [launch_bash("b1"), tool_result("toolu_b1"),
            assistant(tool_use("r1", "Read", file_path=OUT.format("b1"))),
            tool_result("r1"), notification("b1")]
    assert kinds(rows) == ["read-early"]


def test_naming_the_task_id_in_any_tool_input_is_read_early():
    rows = [launch_bash("b1"), assistant(tool_use("t1", "TaskOutput", task_id="b1")),
            notification("b1")]
    assert kinds(rows) == ["read-early"]


def test_resuming_with_sendmessage_is_not_consuming_the_result():
    rows = [launch_bash("b1"), assistant(tool_use("s1", "SendMessage", to="b1",
                                                  message="one more thing")),
            notification("b1")]
    assert kinds(rows) == ["fresh"]


def test_the_launching_tool_use_does_not_count_as_reading_its_own_output():
    """A background command may name its own output path; launching is not reading."""
    rows = [assistant(tool_use("toolu_b1", "Bash", run_in_background=True,
                               command=f"make test | tee {OUT.format('b1')}")),
            notification("b1")]
    assert kinds(rows) == ["fresh"]


def test_a_read_before_the_previous_notification_of_the_same_task_is_already_spent():
    rows = [launch_bash("m1"), assistant(tool_use("r1", "Read", file_path=OUT.format("m1"))),
            notification("m1"), notification("m1")]
    assert kinds(rows) == ["read-early", "fresh"]


def test_a_read_after_the_previous_notification_counts_for_the_next_one():
    rows = [launch_bash("m1"), notification("m1"),
            assistant(tool_use("r1", "Read", file_path=OUT.format("m1"))),
            notification("m1")]
    assert kinds(rows) == ["fresh", "read-early"]


def test_a_tool_use_before_the_launch_does_not_count():
    rows = [assistant(tool_use("r0", "Read", file_path=OUT.format("b1"))),
            launch_bash("b1"), notification("b1")]
    assert kinds(rows) == ["fresh"]


@pytest.mark.parametrize("status", ["killed", "stopped", "KILLED"])
def test_killed_and_stopped_are_their_own_kind(status):
    assert kinds([launch_bash("k1"), notification("k1", status=status)]) == ["killed"]


def test_killed_is_not_stale():
    report = wakeups.measure({"s.jsonl": ([launch_bash("k1"),
                                           notification("k1", status="killed")], 0)})
    assert report["kinds"]["killed"] == 1
    assert report["stale"] == 0
    assert report["stale_bp"] == 0


def test_failed_is_fresh():
    assert kinds([launch_bash("f1"), notification("f1", status="failed")]) == ["fresh"]


# ── precedence ────────────────────────────────────────────────────────────────

def test_handback_dup_wins_over_read_early():
    rows = [launch_bash("a1"), assistant(tool_use("r1", "Read", file_path=OUT.format("a1"))),
            notification("a1", result="delivered to you as a message")]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["kinds"]["handback-dup"] == 1
    assert report["kinds"]["read-early"] == 0


def test_read_early_wins_over_killed():
    rows = [launch_bash("k1"), assistant(tool_use("r1", "Read", file_path=OUT.format("k1"))),
            notification("k1", status="killed")]
    assert kinds(rows) == ["read-early"]


# ── robustness ────────────────────────────────────────────────────────────────

def test_malformed_lines_are_counted_never_dropped():
    good = json.dumps(notification("x1"))
    rows, unreadable = wakeups.parse_lines([good, "{not json", "", "[1, 2]", "  \n", good])
    assert len(rows) == 2
    assert unreadable == 2


def test_unreadable_lines_reach_the_report_per_file_and_in_total(tmp_path):
    (tmp_path / "a.jsonl").write_text(json.dumps(notification("x1")) + "\n{broken\n",
                                      encoding="utf-8")
    report = wakeups.measure_paths([str(tmp_path)])
    assert report["unreadable_lines"] == 1
    assert report["per_file"][str(tmp_path / "a.jsonl")]["unreadable_lines"] == 1


def test_list_content_reads_the_same_as_string_content():
    rows = [launch_bash("b1"), assistant(tool_use("r1", "Read", file_path=OUT.format("b1"))),
            notification("b1", as_list=True)]
    assert kinds(rows) == ["read-early"]
    assert kinds([notification("a1", result="delivered to you as a message",
                               as_list=True)]) == ["handback-dup"]


def test_a_notification_missing_every_tag_is_fresh_and_does_not_match_everything():
    bare = {"type": "user", "origin": {"kind": "task-notification"},
            "message": {"content": "<task-notification></task-notification>"}}
    rows = [assistant(tool_use("r1", "Read", file_path="/anything")), bare,
            {"type": "user", "origin": {"kind": "task-notification"}}, {"origin": "odd"}]
    assert kinds(rows) == ["fresh", "fresh"]


# ── what the user felt ────────────────────────────────────────────────────────

def test_reply_chars_sum_visible_text_until_the_next_real_user_turn():
    rows = [notification("a1", result="delivered to you as a message"),
            assistant(text("abc"), tool_use("t1", "Bash", command="ls")),
            tool_result("t1"),
            assistant(text("de")),
            human("next"),
            assistant(text("not counted"))]
    [entry] = wakeups.classify_rows(rows)
    assert entry["reply_chars"] == 5


def test_stale_reply_totals_count_only_stale_wakeups_with_visible_text():
    rows = [notification("a1", result="delivered to you as a message"),
            assistant(text("中身は先ほど伝えたとおり")),
            notification("a2", result="delivered to you as a message"),
            notification("f1"), assistant(text("fresh reply, not counted"))]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["stale"] == 2
    assert report["stale_reply_turns"] == 1
    assert report["stale_reply_chars"] == len("中身は先ほど伝えたとおり")


# ── aggregation ───────────────────────────────────────────────────────────────

def test_stale_bp_is_integer_basis_points():
    rows = [notification("a1", result="delivered to you as a message"),
            notification("f1"), notification("f2")]
    report = wakeups.measure({"s.jsonl": (rows, 0)})
    assert report["state"] == "measured"
    assert report["stale_bp"] == 3333


def test_nothing_measured_is_unmeasured_not_zero():
    report = wakeups.measure({})
    assert report["state"] == "unmeasured"
    assert report["stale_bp"] is None
    assert "unmeasured" in wakeups.render_human(report)


def test_the_report_states_what_it_does_not_see():
    report = wakeups.measure({})
    assert "Only task-notifications recorded in the given transcripts" in report["not_seen"]
    assert report["not_seen"] in wakeups.render_human(report)


def test_directories_are_searched_non_recursively_and_output_is_sorted(tmp_path):
    (tmp_path / "subagents").mkdir()
    (tmp_path / "subagents" / "agent.jsonl").write_text(json.dumps(notification("s")) + "\n")
    (tmp_path / "b.jsonl").write_text(json.dumps(notification("b")) + "\n")
    (tmp_path / "a.jsonl").write_text(json.dumps(notification("a")) + "\n")
    (tmp_path / "notes.txt").write_text("x")
    report = wakeups.measure_paths([str(tmp_path)])
    assert list(report["per_file"]) == [str(tmp_path / "a.jsonl"), str(tmp_path / "b.jsonl")]
    assert report["total_notifications"] == 2
    assert wakeups.render_json(report) == wakeups.render_json(
        wakeups.measure_paths([str(tmp_path / "b.jsonl"), str(tmp_path)]))


def test_since_days_filters_files_by_mtime(tmp_path):
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    for path in (old, new):
        path.write_text(json.dumps(notification(path.stem)) + "\n")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old, (ten_days_ago, ten_days_ago))
    assert [p.name for p in wakeups.collect_files([str(tmp_path)], since_days=7)] == ["new.jsonl"]


def test_a_missing_path_is_an_error_not_an_empty_measurement(tmp_path):
    with pytest.raises(FileNotFoundError):
        wakeups.collect_files([str(tmp_path / "nope.jsonl")])


# ── ratchet ───────────────────────────────────────────────────────────────────

def report_with(stale, fresh):
    rows = [notification(f"d{i}", result="delivered to you as a message") for i in range(stale)]
    rows += [notification(f"f{i}") for i in range(fresh)]
    return wakeups.measure({"s.jsonl": (rows, 0)})


def ceiling(tmp_path, stale_bp_max, min_notifications=20, **extra):
    path = tmp_path / ".rig" / "wakeups-ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "rig.wakeups-ceiling/v1",
                                "stale_bp_max": stale_bp_max,
                                "min_notifications": min_notifications, **extra}))
    return path


def stored(path):
    return json.loads(path.read_text())


def test_a_small_sample_is_never_judged_and_never_tightens(tmp_path):
    path = ceiling(tmp_path, 0, min_notifications=20)
    verdict = wakeups.apply_ratchet(report_with(stale=5, fresh=0), path, tighten=True)
    assert verdict["state"] == "insufficient-sample"
    assert verdict["exit"] == 0
    assert stored(path)["stale_bp_max"] == 0


def test_over_the_ceiling_fails(tmp_path):
    path = ceiling(tmp_path, 1000, min_notifications=4)
    verdict = wakeups.apply_ratchet(report_with(stale=2, fresh=2), path, tighten=False)
    assert (verdict["state"], verdict["exit"]) == ("fail", 1)


def test_exactly_at_the_ceiling_passes(tmp_path):
    path = ceiling(tmp_path, 5000, min_notifications=4)
    verdict = wakeups.apply_ratchet(report_with(stale=2, fresh=2), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("ok", 0)
    assert stored(path)["stale_bp_max"] == 5000


def test_tighten_lowers_the_ceiling_and_keeps_other_keys(tmp_path):
    path = ceiling(tmp_path, 9000, min_notifications=4, note="kept")
    verdict = wakeups.apply_ratchet(report_with(stale=1, fresh=3), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("tightened", 0)
    assert stored(path) == {"schema": "rig.wakeups-ceiling/v1", "stale_bp_max": 2500,
                            "min_notifications": 4, "note": "kept"}


def test_below_the_ceiling_without_tighten_leaves_the_file_alone(tmp_path):
    path = ceiling(tmp_path, 9000, min_notifications=4)
    verdict = wakeups.apply_ratchet(report_with(stale=1, fresh=3), path, tighten=False)
    assert (verdict["state"], verdict["exit"]) == ("ok", 0)
    assert stored(path)["stale_bp_max"] == 9000


def test_tighten_never_raises_the_ceiling(tmp_path):
    path = ceiling(tmp_path, 100, min_notifications=4)
    verdict = wakeups.apply_ratchet(report_with(stale=3, fresh=1), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("fail", 1)
    assert stored(path)["stale_bp_max"] == 100


def test_a_missing_ceiling_without_tighten_is_an_error_with_a_hint(tmp_path):
    verdict = wakeups.apply_ratchet(report_with(1, 30), tmp_path / "c.json", tighten=False)
    assert (verdict["state"], verdict["exit"]) == ("missing", 2)
    assert "--tighten" in verdict["message"]
    assert not (tmp_path / "c.json").exists()


def test_a_missing_ceiling_with_tighten_is_created_at_the_measured_value(tmp_path):
    path = tmp_path / ".rig" / "wakeups-ceiling.json"
    verdict = wakeups.apply_ratchet(report_with(stale=1, fresh=19), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("created", 0)
    assert stored(path) == {"schema": "rig.wakeups-ceiling/v1", "stale_bp_max": 500,
                            "min_notifications": 20}


def test_a_missing_ceiling_is_not_created_from_a_small_sample(tmp_path):
    path = tmp_path / "c.json"
    verdict = wakeups.apply_ratchet(report_with(stale=0, fresh=19), path, tighten=True)
    assert verdict["state"] == "insufficient-sample"
    assert not path.exists()


def test_nothing_measured_never_tightens_even_when_the_file_asks_for_no_sample(tmp_path):
    path = ceiling(tmp_path, 500, min_notifications=0)
    verdict = wakeups.apply_ratchet(wakeups.measure({}), path, tighten=True)
    assert verdict["state"] == "insufficient-sample"
    assert verdict["exit"] == 0
    assert stored(path)["stale_bp_max"] == 500


@pytest.mark.parametrize("body", ["{broken", json.dumps({"schema": "other/v1"}),
                                  json.dumps({"schema": "rig.wakeups-ceiling/v1",
                                              "stale_bp_max": "10", "min_notifications": 1})])
def test_an_unreadable_ceiling_is_an_error(tmp_path, body):
    path = tmp_path / "c.json"
    path.write_text(body)
    verdict = wakeups.apply_ratchet(report_with(1, 30), path, tighten=True)
    assert (verdict["state"], verdict["exit"]) == ("invalid", 2)
    assert path.read_text() == body


# ── the CLI ───────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    env = dict(os.environ, RIG_NO_CONTEXT_METER="1")
    return subprocess.run([sys.executable, str(WORKBENCH), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd, timeout=60, env=env)


def write_transcript(path, stale, fresh):
    rows = [notification(f"d{i}", result="delivered to you as a message") for i in range(stale)]
    rows += [notification(f"f{i}") for i in range(fresh)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_cli_ratchet_failure_prints_the_report_then_exits_1(tmp_path):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=10, fresh=10)
    path = ceiling(tmp_path, 1000)
    result = run_cli(["context", "--transcripts", transcript, "--ratchet", path,
                      "--tighten", "--json"], tmp_path)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["stale_bp"] == 5000
    assert payload["ratchet"]["state"] == "fail"
    assert "exceed the ceiling" in result.stderr
    assert stored(path)["stale_bp_max"] == 1000


def test_cli_human_output_names_the_counts(tmp_path):
    transcript = write_transcript(tmp_path / "s.jsonl", stale=1, fresh=3)
    result = run_cli(["context", "--transcripts", transcript], tmp_path)
    assert result.returncode == 0
    assert "stale: 1 = 2500 bp" in result.stdout
    assert "Only task-notifications recorded" in result.stdout


@pytest.mark.parametrize("flag", [["--json"], ["--ratchet", "c.json"], ["--tighten"]])
def test_cli_transcript_flags_need_transcripts(tmp_path, flag):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = run_cli(["context", *flag], tmp_path)
    assert result.returncode == 2
    assert "needs --transcripts" in result.stderr


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


def test_the_writer_itself_refuses_to_raise_or_keep_the_ceiling():
    """Defence in depth: `apply_ratchet` checks the verdict before it tightens, and the
    one helper that builds the lowered document refuses anything that is not lower."""
    for value in (100, 101):
        with pytest.raises(ValueError):
            wakeups._lowered({"stale_bp_max": 100}, value)
    assert wakeups._lowered({"stale_bp_max": 100, "k": 1}, 99) == {"stale_bp_max": 99, "k": 1}
