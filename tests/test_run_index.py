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

from rig_workbench.workbench.run_index import DEFAULT_LIMIT, run_index


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

    result = run_index(path=path)

    assert result["projects"] == ["/a", "/b"]
    assert len(result["rows"]) == 3


def test_a_resumed_run_is_one_row_that_counts_its_attempts(tmp_path):
    """`telemetry_append` fires once per invocation of the runner, including when a run stops
    — so a run that stopped and resumed appends twice under one id. Two rows would be two runs
    that never happened; one row that hides the second record loses that it took two goes."""
    path = _log(tmp_path,
                _record(run_id="orc-x", final="ESCALATE", ts="2026-08-29T00:00:00+00:00"),
                _record(run_id="orc-x", final="DONE", ts="2026-08-29T01:00:00+00:00"))

    rows = run_index(path=path)["rows"]

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

    rows = run_index(path=path)["rows"]

    assert len(rows) == 2
    assert [row["attempts"] for row in rows] == [1, 1]


def test_rows_are_ordered_by_when_the_run_was_last_heard_from(tmp_path):
    """Not by position. A run that started first and resumed last carries the newest timestamp
    while keeping its first record's place in the file, so position would bury a fresh row."""
    path = _log(tmp_path,
                _record(run_id="orc-old", ts="2026-08-01T00:00:00+00:00"),
                _record(run_id="orc-mid", ts="2026-08-15T00:00:00+00:00"),
                _record(run_id="orc-old", ts="2026-08-29T00:00:00+00:00"))

    rows = run_index(path=path)["rows"]

    assert [row["run_id"] for row in rows] == ["orc-old", "orc-mid"]


def test_timestamps_are_compared_as_instants_and_not_as_strings(tmp_path):
    """These are written with `.astimezone().isoformat()`, so the offset is whatever was local
    to the machine that wrote the record — and a cross-project log is exactly where records
    from different offsets meet. `01:00+09:00` sorts after `02:00+00:00` as text and before it
    in fact; the text order is the wrong one."""
    path = _log(tmp_path,
                _record(run_id="orc-tokyo", ts="2026-08-29T01:00:00+09:00"),   # 2026-08-28T16:00Z
                _record(run_id="orc-utc", ts="2026-08-28T20:00:00+00:00"))

    rows = run_index(path=path)["rows"]

    assert [row["run_id"] for row in rows] == ["orc-utc", "orc-tokyo"]


def test_an_unreadable_line_is_counted_rather_than_dropped(tmp_path):
    """Every writer of this log swallows its own failures by design, so a half-written final
    line is a normal thing to find. A board that quietly showed two of three runs would be
    worse than one that showed two and said so."""
    path = _log(tmp_path, _record(run_id="orc-1"), _record(run_id="orc-2"),
                raw='{"run_id": "orc-3", "ts": "2026-0\n')

    result = run_index(path=path)

    assert len(result["rows"]) == 2
    assert result["unreadable"] == 1


def test_a_json_value_that_is_not_an_object_is_refused(tmp_path):
    """A bare list or string parses cleanly and is not a record. Treating it as one would put
    a row on the board with every field missing and nothing to say why."""
    path = _log(tmp_path, _record(run_id="orc-1"), ["not", "a", "record"], "neither")

    result = run_index(path=path)

    assert len(result["rows"]) == 1
    assert result["unreadable"] == 2


def test_only_the_tail_is_read_and_the_view_says_so(tmp_path):
    """The board polls and the log never shrinks, so reading it whole would make the poll cost
    grow without limit. Reading from the middle means starting mid-record, so the first
    fragment is dropped — and `truncated` is reported rather than letting a tail imply it is
    the whole history."""
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:00:{n:02d}+00:00")
                            for n in range(60)])
    whole = run_index(path=path)
    assert whole["truncated"] is False and whole["total"] == 60

    result = run_index(path=path, tail_bytes=2000)

    assert result["truncated"] is True
    assert 0 < result["total"] < 60


def test_a_missing_log_is_an_empty_board_and_not_an_error(tmp_path):
    """A machine that has never run rig has no global log. That is a state to render, not a
    failure to raise inside a request handler."""
    result = run_index(path=tmp_path / "absent.jsonl")

    assert result["exists"] is False
    assert result["rows"] == [] and result["unreadable"] == 0


def test_the_limit_bounds_the_rows_without_hiding_the_count(tmp_path):
    """`shown` and `total` are separate so the board can say it is showing part of what it
    read, rather than presenting a page as the whole."""
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:00:{n:02d}+00:00")
                            for n in range(10)])

    result = run_index(path=path, limit=3)

    assert result["shown"] == 3 and result["total"] == 10
    assert len(result["rows"]) == 3


def test_the_default_limit_is_applied(tmp_path):
    path = _log(tmp_path, *[_record(run_id=f"orc-{n}", ts=f"2026-08-29T00:{n // 60:02d}:{n % 60:02d}+00:00")
                            for n in range(DEFAULT_LIMIT + 5)])

    assert run_index(path=path)["shown"] == DEFAULT_LIMIT


def _verdict(by: object, ok: object = True) -> dict:
    return {"by": by, "ok": ok}


def test_the_verifier_provider_is_the_part_before_the_first_colon(tmp_path):
    """`by` is written as `provider:persona`, and a persona is free text that may contain a
    colon of its own. Splitting on the last, or on all of them, would attribute a verdict to a
    provider nobody ran."""
    path = _log(tmp_path, _record(steps=[{"verdicts": [
        _verdict("codex:security-reviewer"),
        _verdict("claude:styles/qiita:tech-writer"),
    ]}]))

    verifiers = run_index(path=path)["rows"][0]["verifiers"]

    assert sorted(verifiers) == ["claude", "codex"]


def test_a_verdict_with_no_attributable_provider_is_kept_and_labelled(tmp_path):
    """Dropping it would quietly shrink the denominator of every provider it sat beside —
    the board would show fewer verdicts than were cast and say nothing about the difference."""
    path = _log(tmp_path, _record(steps=[{"verdicts": [
        _verdict("codex:security"), _verdict("no-colon-here"), _verdict(None),
    ]}]))

    verifiers = run_index(path=path)["rows"][0]["verifiers"]

    assert verifiers["(unattributed)"]["ok"] == 2
    assert verifiers["codex"]["ok"] == 1


def test_an_unrecorded_outcome_is_not_a_failure(tmp_path):
    """`unknown` is a third counter, not a bucket folded into `not_ok`. A verdict whose result
    was never recorded is an absence, and reporting an absence as a failure would make a
    provider look worse for a defect in the record rather than in its judgement."""
    path = _log(tmp_path, _record(steps=[{"verdicts": [
        _verdict("codex:a", True), _verdict("codex:b", False),
        _verdict("codex:c", None), _verdict("codex:d", "yes"),
    ]}]))

    counts = run_index(path=path)["rows"][0]["verifiers"]["codex"]

    assert counts == {"ok": 1, "not_ok": 1, "unknown": 2}


def test_no_rate_or_score_is_computed_anywhere(tmp_path):
    """The refusal, pinned so a later convenience cannot quietly add it.

    `/rig:drill` measures reviewer detection by injecting known-bad code and counting what each
    persona catches. A verdict pass rate over live runs is a different quantity — it moves with
    what was submitted, not only with who judged it — and publishing it under a name the
    drill's number has earned would be the substitution this project refuses elsewhere.

    Counts also keep their denominator: one verdict at 1.0 and a hundred verdicts at 1.0 are
    not the same measurement, and a bare ratio cannot tell them apart."""
    path = _log(tmp_path, _record(steps=[{"verdicts": [_verdict("codex:a")]}]))

    result = run_index(path=path)
    forbidden = {"rate", "pass_rate", "score", "quality", "accuracy", "success_rate"}

    assert set(result["by_verifier"]["codex"]) == {"ok", "not_ok", "unknown", "runs"}
    assert not forbidden & set(result["by_verifier"]["codex"])
    assert not forbidden & set(result["rows"][0]["verifiers"]["codex"])


def test_a_generator_model_that_was_not_recorded_is_counted_as_unmeasured(tmp_path):
    """`model: null` means the provider's default was used and which model that was is not
    known here (#293) — and it is the common case: 368 of 432 steps in the log this was written
    against. Rendering it as a name would invent one; rendering it as a blank cell would read
    as 'none'. It is counted, so the board can say how much of the generator side it cannot
    see."""
    path = _log(tmp_path, _record(steps=[
        {"model": "sonnet", "verdicts": []},
        {"model": None, "verdicts": []},
        {"model": "", "verdicts": []},
        {"verdicts": []},
    ]))

    generator = run_index(path=path)["rows"][0]["generator"]

    assert generator == {"models": ["sonnet"], "unmeasured": 3}


def test_verdicts_are_totalled_across_runs_and_projects(tmp_path):
    """The axis the board exists for: rig's claim is that the generator and the verifier are
    separate roles on separate providers, and this is the first place that claim is visible
    rather than asserted."""
    path = _log(tmp_path,
                _record(run_id="orc-1", project="/a",
                        steps=[{"verdicts": [_verdict("codex:x"), _verdict("claude:y", False)]}]),
                _record(run_id="orc-2", project="/b",
                        steps=[{"verdicts": [_verdict("codex:z")]}]))

    by_verifier = run_index(path=path)["by_verifier"]

    assert by_verifier["codex"] == {"ok": 2, "not_ok": 0, "unknown": 0, "runs": 2}
    assert by_verifier["claude"] == {"ok": 0, "not_ok": 1, "unknown": 0, "runs": 1}


def test_the_aggregate_describes_the_rows_shown_and_not_the_whole_log(tmp_path):
    """`limit` bounds the rows, and the totals are computed over those rows. Summing the whole
    tail while showing a page of it would put a number on screen that no visible row accounts
    for — the reader would have no way to reconcile the two."""
    path = _log(tmp_path, *[
        _record(run_id=f"orc-{n}", ts=f"2026-08-29T00:00:{n:02d}+00:00",
                steps=[{"verdicts": [_verdict("codex:x")]}])
        for n in range(10)])

    result = run_index(path=path, limit=3)

    assert result["shown"] == 3
    assert result["by_verifier"]["codex"]["runs"] == 3
