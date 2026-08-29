"""The board read one of rig's two run stores.

`.rig/runs/<task_id>/` is rich and scoped to the current project. `~/.rig/runs.jsonl` is the
append-only mirror every backend writes, carrying `project` on each record — and until this,
only `rig-wb usage` ever opened it. Mission Control's single-repository view was not a missing
feature so much as an unopened file.

What is pinned here is mostly what the projection refuses to do: merge runs it cannot tell
apart, hide a line it could not read, imply a tail is the whole log, or order rows by a
timestamp string whose offset it did not check.
"""

from __future__ import annotations

import json
import pathlib

from rig_workbench.workbench.fleet import DEFAULT_LIMIT, fleet_rows


def _log(tmp_path: pathlib.Path, *records: object, raw: str = "") -> pathlib.Path:
    path = tmp_path / "runs.jsonl"
    body = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    path.write_text(body + raw, encoding="utf-8")
    return path


def _record(**overrides: object) -> dict:
    record = {"run_id": "orc-20260829-000000-demo-aaaaaa", "ts": "2026-08-29T00:00:00+00:00",
              "recipe": "demo", "backend": "orchestrate", "final": "DONE",
              "project": "/p", "steps_total": 2, "steps_passed": 2, "steps": []}
    record.update(overrides)
    return record


def test_runs_from_every_project_appear(tmp_path):
    """The point of the change. The log has carried `project` all along; nothing read it."""
    path = _log(tmp_path,
                _record(run_id="orc-1", project="/a"),
                _record(run_id="orc-2", project="/b"),
                _record(run_id="orc-3", project="/a"))

    result = fleet_rows(path=path)

    assert result["projects"] == ["/a", "/b"]
    assert len(result["rows"]) == 3


def test_a_resumed_run_is_one_row_that_counts_its_attempts(tmp_path):
    """`telemetry_append` fires once per invocation of the runner, including when a run stops
    — so a run that stopped and resumed appends twice under one id. Two rows would be two runs
    that never happened; one row that hides the second record loses that it took two goes."""
    path = _log(tmp_path,
                _record(run_id="orc-x", final="ESCALATE", ts="2026-08-29T00:00:00+00:00"),
                _record(run_id="orc-x", final="DONE", ts="2026-08-29T01:00:00+00:00"))

    rows = fleet_rows(path=path)["rows"]

    assert len(rows) == 1
    assert rows[0]["attempts"] == 2
    assert rows[0]["final"] == "DONE"       # the latest record, not the first


def test_records_from_before_run_ids_are_never_merged(tmp_path):
    """Two records with no id and the same recipe are not evidence of one run. Collapsing them
    would invent a resumed run out of two unrelated ones — the failure this projection would
    make most confidently and be least able to notice."""
    path = _log(tmp_path,
                _record(run_id=None, recipe="bugfix", ts="2026-08-29T00:00:00+00:00"),
                _record(run_id=None, recipe="bugfix", ts="2026-08-29T01:00:00+00:00"))

    rows = fleet_rows(path=path)["rows"]

    assert len(rows) == 2
    assert [row["attempts"] for row in rows] == [1, 1]


def test_rows_are_ordered_by_when_the_run_was_last_heard_from(tmp_path):
    """Not by position. A run that started first and resumed last carries the newest timestamp
    while keeping its first record's place in the file, so position would bury a fresh row."""
    path = _log(tmp_path,
                _record(run_id="orc-old", ts="2026-08-01T00:00:00+00:00"),
                _record(run_id="orc-mid", ts="2026-08-15T00:00:00+00:00"),
                _record(run_id="orc-old", ts="2026-08-29T00:00:00+00:00"))

    rows = fleet_rows(path=path)["rows"]

    assert [row["run_id"] for row in rows] == ["orc-old", "orc-mid"]


def test_timestamps_are_compared_as_instants_and_not_as_strings(tmp_path):
    """These are written with `.astimezone().isoformat()`, so the offset is whatever was local
    to the machine that wrote the record — and a cross-project log is exactly where records
    from different offsets meet. `01:00+09:00` sorts after `02:00+00:00` as text and before it
    in fact; the text order is the wrong one."""
    path = _log(tmp_path,
                _record(run_id="orc-tokyo", ts="2026-08-29T01:00:00+09:00"),   # 2026-08-28T16:00Z
                _record(run_id="orc-utc", ts="2026-08-28T20:00:00+00:00"))

    rows = fleet_rows(path=path)["rows"]

    assert [row["run_id"] for row in rows] == ["orc-utc", "orc-tokyo"]


def test_an_unreadable_line_is_counted_rather_than_dropped(tmp_path):
    """Every writer of this log swallows its own failures by design, so a half-written final
    line is a normal thing to find. A board that quietly showed two of three runs would be
    worse than one that showed two and said so."""
    path = _log(tmp_path, _record(run_id="orc-1"), _record(run_id="orc-2"),
                raw='{"run_id": "orc-3", "ts": "2026-0\n')

    result = fleet_rows(path=path)

    assert len(result["rows"]) == 2
    assert result["unreadable"] == 1


def test_a_json_value_that_is_not_an_object_is_refused(tmp_path):
    """A bare list or string parses cleanly and is not a record. Treating it as one would put
    a row on the board with every field missing and nothing to say why."""
    path = _log(tmp_path, _record(run_id="orc-1"), ["not", "a", "record"], "neither")

    result = fleet_rows(path=path)

    assert len(result["rows"]) == 1
    assert result["unreadable"] == 2


def test_only_the_tail_is_read_and_the_view_says_so(tmp_path):
    """The board polls and the log never shrinks, so reading it whole would make the poll cost
    grow without limit. Reading from the middle means starting mid-record, so the first
    fragment is dropped — and `truncated` is reported rather than letting a tail imply it is
    the whole history."""
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:00:{n:02d}+00:00")
                            for n in range(60)])
    whole = fleet_rows(path=path)
    assert whole["truncated"] is False and whole["total"] == 60

    result = fleet_rows(path=path, tail_bytes=2000)

    assert result["truncated"] is True
    assert 0 < result["total"] < 60


def test_a_missing_log_is_an_empty_board_and_not_an_error(tmp_path):
    """A machine that has never run rig has no global log. That is a state to render, not a
    failure to raise inside a request handler."""
    result = fleet_rows(path=tmp_path / "absent.jsonl")

    assert result["exists"] is False
    assert result["rows"] == [] and result["unreadable"] == 0


def test_the_limit_bounds_the_rows_without_hiding_the_count(tmp_path):
    """`shown` and `total` are separate so the board can say it is showing part of what it
    read, rather than presenting a page as the whole."""
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:00:{n:02d}+00:00")
                            for n in range(10)])

    result = fleet_rows(path=path, limit=3)

    assert result["shown"] == 3 and result["total"] == 10
    assert len(result["rows"]) == 3


def test_the_default_limit_is_applied(tmp_path):
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:{n // 60:02d}:{n % 60:02d}+00:00")
                            for n in range(DEFAULT_LIMIT + 5)])

    assert fleet_rows(path=path)["shown"] == DEFAULT_LIMIT
