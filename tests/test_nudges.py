"""`wb nudges`: which prompts only said "carry on", and the ratchet over their share."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rig_workbench.workbench import nudges

ROOT = Path(__file__).resolve().parents[1]


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _assistant(text):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


@pytest.mark.parametrize("text", [
    "進めて", "続けて", "続けてください", "進めてください", "そのまま続行で", "引き続きお願いします",
    "はい、進めて", "次", "再開", "continue", "Continue.", "keep going!", "go ahead", "Please continue",
    "ｃｏｎｔｉｎｕｅ", "続きを", "やって",
])
def test_continuation_words_are_nudges(text):
    assert nudges.classify_prompt(text) == "nudge"


@pytest.mark.parametrize("text", ["はい", "OK", "お願いします", "LGTM", "了解"])
def test_bare_acks_are_counted_apart(text):
    assert nudges.classify_prompt(text) == "ack"


@pytest.mark.parametrize("text", [
    "continue, but skip the tests", "テストも直して進めて", "/rig:go fix the bug",
    "次のファイルはどこ？", "進め方を教えて",
])
def test_anything_more_than_a_continuation_word_is_other(text):
    assert nudges.classify_prompt(text) == "other"


def test_classify_rows_skips_what_no_person_typed_and_marks_what_the_nudge_answered():
    rows = [
        _user("/rig:go fix the login bug"),
        _assistant("▸ rig | recipe: bugfix | step: implement (3/6) | gate: none | mode: gated\nDone."),
        _user("進めて"),                                              # unprompted, in a run
        _assistant("── step verify ▸ done\n次へ進みますか？"),
        _user("続けて"),                                              # after a question
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
        {"type": "user", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "<task-notification>…</task-notification>"}},
        _user("<local-command-stdout>ok</local-command-stdout>"),
        _user("[Request interrupted by user]"),
        _user("Stop hook feedback:\n[rig run-continuity] carry on"),
        _user("<system-reminder>noise</system-reminder>"),
        _assistant("Plain answer."),
        _user("はい"),
        _assistant("Tests pass.\n▸ stop: needs-decision:destructive-operation — push next"),
        _user("進めて"),                                              # answers a declared stop
    ]
    entries = nudges.classify_rows(rows)
    assert [e["kind"] for e in entries] == ["other", "nudge", "nudge", "ack", "nudge"]
    assert entries[4]["after_question"] is True
    assert entries[4]["stop"] == "needs-decision:destructive-operation"
    assert nudges.measure({"t": (rows, 0)})["declared_stops"] == \
        {"needs-decision:destructive-operation": 1}
    assert [(e["after_question"], e["in_run"]) for e in entries[1:3]] == [(False, True), (True, False)]


def _write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _session(n_prompts, n_nudges):
    rows = []
    for i in range(n_prompts):
        rows.append({**_user("進めて" if i < n_nudges else f"task {i}"), "uuid": f"u{i}"})
        rows.append({**_assistant("ok"), "uuid": f"a{i}"})
    return rows


def test_measure_counts_basis_points_and_dedupes_by_uuid(tmp_path):
    _write(tmp_path / "a.jsonl", _session(40, 5))
    _write(tmp_path / "b.jsonl", _session(40, 5))    # a resumed copy: same uuids
    report = nudges.measure_paths([str(tmp_path)])
    assert report["schema"] == "rig.nudges/v1"
    assert report["total_prompts"] == 40
    assert report["nudges"] == 5 and report["nudge_bp"] == 1250
    assert report["unprompted"] == 5 and report["duplicate_rows_skipped"] == 80


def test_nothing_measured_is_unmeasured_not_zero(tmp_path):
    _write(tmp_path / "a.jsonl", [_assistant("hi")])
    report = nudges.measure_paths([str(tmp_path)])
    assert report["state"] == "unmeasured" and report["nudge_bp"] is None


def test_ratchet_matrix(tmp_path):
    ceiling = tmp_path / "c.json"
    report = nudges.measure({"t": (_session(40, 4), 0)})
    assert nudges.apply_ratchet(report, ceiling)["exit"] == 2              # missing
    created = nudges.apply_ratchet(report, ceiling, init=True)
    assert (created["state"], created["exit"]) == ("created", 0)
    assert json.loads(ceiling.read_text())["nudge_bp_max"] == 1000
    assert nudges.apply_ratchet(report, ceiling, init=True)["exit"] == 2   # never overwritten

    worse = nudges.measure({"t": (_session(40, 8), 0)})
    assert nudges.apply_ratchet(worse, ceiling, tighten=True)["exit"] == 1
    assert json.loads(ceiling.read_text())["nudge_bp_max"] == 1000

    better = nudges.measure({"t": (_session(40, 2), 0)})
    tightened = nudges.apply_ratchet(better, ceiling, tighten=True)
    assert (tightened["state"], json.loads(ceiling.read_text())["nudge_bp_max"]) == ("tightened", 500)

    small = nudges.measure({"t": (_session(10, 0), 0)})
    assert nudges.apply_ratchet(small, ceiling)["exit"] == 3


def test_cli_prints_json_and_exits_1_over_the_ceiling(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _write(transcript, _session(40, 20))
    ceiling = tmp_path / "c.json"
    ceiling.write_text(json.dumps({"schema": "rig.nudges-ceiling/v1", "nudge_bp_max": 100,
                                   "min_prompts": 30}))
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "workbench.py"), "nudges",
                             "--transcripts", str(transcript), "--json", "--ratchet", str(ceiling)],
                            text=True, capture_output=True, cwd=tmp_path, timeout=60)
    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    assert payload["nudge_bp"] == 5000 and payload["ratchet"]["state"] == "over"
