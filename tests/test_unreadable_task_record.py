"""One malformed task record must not take down every reader of the runs directory (#488).

`read_all_tasks` used to parse every `.rig/runs/*/task.json` and return a list, so a single
malformed file raised before any caller could report anything — Mission Control produced no
page at all rather than a page missing one row. The fix is not "skip it": a task missing from
a board reads as a task that does not exist. It is named, and the count travels with the
records so no total can be rendered without it.
"""

import json
import pathlib
import subprocess

import pytest

from rig_workbench import mission_control, mission_ui
from rig_workbench.workbench.digest import cmd_digest
from rig_workbench.workbench.reporting import (OPTIONAL_FIELDS, REQUIRED_FIELDS,
                                               TaskRecords, cmd_board, cmd_log,
                                               cmd_stats, read_all_tasks)


def _runs(tmp_path):
    runs = tmp_path / ".rig" / "runs"
    runs.mkdir(parents=True)
    return runs


def _task(runs, name, **overrides):
    """A run directory holding a usable record, unless an override makes it unusable."""
    directory = runs / name
    directory.mkdir()
    record = {"task_id": name, "task_type": "bugfix", "recipe": "bugfix",
              "status": "running", "created_at": "2026-08-24T10:00:00+09:00", "input": "x"}
    record.update(overrides)
    (directory / "task.json").write_text(json.dumps(record), encoding="utf-8")
    return directory


# ── the four ways a directory yields no record ───────────────────────────────
@pytest.mark.parametrize("name,write", [
    ("no-file", lambda d: None),
    ("not-json", lambda d: (d / "task.json").write_text("{not json", encoding="utf-8")),
    ("not-a-dict", lambda d: (d / "task.json").write_text("[1, 2]", encoding="utf-8")),
    ("no-task-id", lambda d: (d / "task.json").write_text('{"status": "running"}',
                                                          encoding="utf-8")),
    ("blank-task-id", lambda d: (d / "task.json").write_text('{"task_id": ""}',
                                                             encoding="utf-8")),
    ("task-id-not-a-string", lambda d: (d / "task.json").write_text('{"task_id": 7}',
                                                                    encoding="utf-8")),
])
def test_a_record_that_cannot_be_used_is_named_not_dropped(tmp_path, name, write):
    """All six have to mean the same thing downstream. `load_reviews`, `gate_status_counts`
    and the cockpit all index `task["task_id"]`, so a record that parses without a usable one
    moves the crash a layer down instead of removing it."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    broken = runs / name
    broken.mkdir()
    write(broken)

    records = read_all_tasks(runs)
    assert [t["task_id"] for t in records.tasks] == ["readable"]
    assert records.unreadable == (name,), f"{name} was dropped instead of named"
    assert name in records.note() and "1 of 2" in records.note()


def test_everything_readable_says_nothing(tmp_path):
    runs = _runs(tmp_path)
    _task(runs, "a")
    _task(runs, "b")
    records = read_all_tasks(runs)
    assert len(records.tasks) == 2 and records.unreadable == ()
    assert records.note() == "", "a clean read must not decorate a total"


# ── a total cannot be taken without the shortfall ────────────────────────────
def test_the_records_are_not_a_list(tmp_path):
    """The old return value was a list, and a caller that got a shorter one had no way to
    know. Iterating or measuring this raises rather than quietly answering with the readable
    subset — the type error is what makes every reader show the count."""
    records = read_all_tasks(_runs(tmp_path))
    with pytest.raises(TypeError):
        list(records)
    with pytest.raises(TypeError):
        len(records)


def test_the_note_counts_what_was_attempted_not_what_was_read(tmp_path):
    """`3 of 55 could not be read` is the sentence #488 asks for. Reporting `52` alone is the
    defect; reporting `3 of 52` would be a different wrong number."""
    runs = _runs(tmp_path)
    for i in range(4):
        _task(runs, f"ok-{i}")
    for i in range(3):
        (runs / f"bad-{i}").mkdir()
        (runs / f"bad-{i}" / "task.json").write_text("{", encoding="utf-8")
    note = read_all_tasks(runs).note()
    assert "3 of 7" in note, note


# ── not looking is not the same as finding nothing ───────────────────────────
def test_a_cold_start_is_not_a_failure(tmp_path):
    """No runs directory means nothing has been recorded yet, which is an ordinary state."""
    records = read_all_tasks(tmp_path / ".rig" / "runs")
    assert records.tasks == () and records.unreadable == ()
    assert records.collection_error is None and records.note() == ""


def test_a_cold_start_is_not_reported_as_a_failure_to_look(tmp_path):
    """The two are one `except` clause apart, and they say opposite things: an empty runs
    directory is an ordinary state, while failing to list one means the total is not a count
    of anything. Folding the first into the second would put a scary sentence on every fresh
    checkout."""
    records = read_all_tasks(tmp_path / "never-created")
    assert records.collection_error is None
    assert "could not be listed" not in records.note()


def test_a_stray_file_under_the_runs_directory_is_not_a_task(tmp_path):
    """A run is a directory. A file that happens to sit beside them — a stray export, an
    editor's leftover — is neither a task nor a task that could not be read, and reporting it
    as the latter would invent a shortfall out of housekeeping."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    (runs / "notes.txt").write_text("scratch", encoding="utf-8")

    records = read_all_tasks(runs)
    assert [t["task_id"] for t in records.tasks] == ["readable"]
    assert records.unreadable == (), "a stray file was reported as an unreadable task"


def test_a_directory_that_cannot_be_listed_is_not_zero_tasks(tmp_path, monkeypatch):
    """A permission error or a broken mount is rig failing to look. Answering `0 tasks` would
    be this function reporting on something it never got to read."""
    runs = _runs(tmp_path)
    _task(runs, "unreachable")

    def refuse(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(runs), "iterdir", refuse)
    records = read_all_tasks(runs)
    assert records.tasks == ()
    assert records.collection_error and "PermissionError" in records.collection_error
    assert "not a count of what exists" in records.note()


def test_the_two_shortfalls_are_separate_facts(tmp_path):
    """Some records unreadable and unable to look at all are different statements, and the
    stronger one must not be rendered as the weaker."""
    unread = TaskRecords(tasks=(), unreadable=("a",))
    blind = TaskRecords(tasks=(), collection_error="PermissionError: nope")
    assert "could not be read" in unread.note()
    assert "could not be listed" in blind.note()
    assert unread.note() != blind.note()


# ── every reader survives, and says so ───────────────────────────────────────
def test_mission_control_renders_a_page_missing_one_row(tmp_path):
    """The reported symptom: `build_snapshot` raised `JSONDecodeError` and produced no page
    at all, rather than a page naming the task it could not read."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")

    snapshot = mission_control.build_snapshot(tmp_path)
    operations = snapshot["operations"]
    assert operations["tasks_total"] == 1
    assert operations["tasks_unreadable"] == ["broken"]
    assert operations["tasks_unreadable_collection"] is None


def test_mission_controls_two_sections_name_the_same_task(tmp_path):
    """#488's third requirement. The assurance section used to enumerate the runs directory
    itself, so the two halves of one page could disagree about what a task is."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")

    snapshot = mission_control.build_snapshot(tmp_path)
    assert (snapshot["operations"]["tasks_unreadable"]
            == snapshot["assurance"]["unreadable_tasks"] == ["broken"])


def test_the_digest_total_carries_the_shortfall(tmp_path, monkeypatch, capsys):
    """A total printed without it is the defect this change exists to remove."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(_repo(tmp_path))

    import argparse
    cmd_digest(argparse.Namespace(period="week", out=None))
    printed = capsys.readouterr().out
    assert "could not be read" in printed and "broken" in printed, printed


# ── what review round 1 found ────────────────────────────────────────────────
#: Written out rather than taken from `REQUIRED_FIELDS`, because a test that iterates the
#: constant shrinks with it: dropping a field would remove both the rule and the case that
#: was supposed to object to dropping it.
_FIELDS_THE_READERS_INDEX = ("task_id", "status", "created_at", "input", "task_type")


def test_the_required_fields_are_the_ones_named_here():
    """Both directions. A field added to the constant with no case here would be a rule
    nothing exercises; a field removed from it would be a crash nothing catches."""
    assert set(REQUIRED_FIELDS) == set(_FIELDS_THE_READERS_INDEX)


@pytest.mark.parametrize("field", _FIELDS_THE_READERS_INDEX)
def test_a_record_missing_a_field_its_readers_index_is_not_usable(tmp_path, field):
    """`board` and the cockpit index `status`, `input` and `task_type`; `stats` parses
    `created_at` as a date. A record that parses without one of them does not remove the
    crash, it moves it a layer down — into a reader that has no way to name the task."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    directory = _task(runs, "partial")
    record = json.loads((directory / "task.json").read_text(encoding="utf-8"))
    del record[field]
    (directory / "task.json").write_text(json.dumps(record), encoding="utf-8")

    records = read_all_tasks(runs)
    assert records.unreadable == ("partial",), f"a record with no {field} was called usable"


@pytest.mark.parametrize("field", _FIELDS_THE_READERS_INDEX)
def test_each_required_field_is_one_a_reader_would_have_crashed_without(monkeypatch, field):
    """The list is not a preference. Hand `board` a record missing one field and it raises —
    which is what makes excluding such a record the fix rather than a strictness setting."""
    from rig_workbench.workbench import reporting

    record = {"task_id": "t", "task_type": "bugfix", "status": "running",
              "created_at": "2026-08-24T10:00:00+09:00", "input": "x"}
    del record[field]
    monkeypatch.setattr(reporting, "read_all_tasks",
                        lambda base: TaskRecords(tasks=(record,)))
    monkeypatch.setattr(reporting, "repo_root", lambda: pathlib.Path("/nonexistent"))
    import argparse
    with pytest.raises((KeyError, TypeError, ValueError, AttributeError)):
        reporting.cmd_board(argparse.Namespace(all=True))


@pytest.mark.parametrize("value", [None, 7, [], {}, ""])
def test_a_required_field_of_the_wrong_type_is_not_usable(tmp_path, value):
    runs = _runs(tmp_path)
    _task(runs, "wrong-type", status=value)
    assert read_all_tasks(runs).unreadable == ("wrong-type",)


def test_a_record_that_names_another_directory_is_not_usable(tmp_path):
    """`task_id` is joined onto the runs directory to find a task's acceptance, steps and
    receipt, so a record in one directory claiming to be another sends every reader to the
    wrong task's artefacts — and the readers would never know they had been redirected."""
    runs = _runs(tmp_path)
    _task(runs, "run-a", task_id="run-b")
    _task(runs, "run-c")

    records = read_all_tasks(runs)
    assert [t["task_id"] for t in records.tasks] == ["run-c"]
    assert records.unreadable == ("run-a",), "a record naming another directory was accepted"


@pytest.mark.parametrize("task_id", ["..", "/etc", "../escape", "a/b"])
def test_a_task_id_that_would_leave_the_runs_directory_is_not_usable(tmp_path, task_id):
    """The same check, for the shape that matters most: a value that resolves outside the
    runs directory when joined onto it."""
    runs = _runs(tmp_path)
    _task(runs, "run-a", task_id=task_id)
    assert read_all_tasks(runs).unreadable == ("run-a",)


def test_the_note_renders_both_shortfalls_when_both_are_known(tmp_path):
    """They are separate facts and the object holds both. Rendering only the stronger one
    drops a shortfall whose names it already has."""
    both = TaskRecords(tasks=(), unreadable=("bad",), collection_error="PermissionError: no")
    assert "could not be read" in both.note() and "bad" in both.note()
    assert "could not be listed" in both.note()


def _repo(tmp_path):
    """A git repository, because `cmd_board` and `cmd_stats` locate the runs dir from one."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _one_broken(tmp_path):
    runs = _runs(tmp_path)
    _task(runs, "readable")
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    return runs


# ── every reader that shows a total, including the two that were missed ──────
def test_the_board_survives_and_names_it(tmp_path, monkeypatch, capsys):
    """#488 names `board` first. It enumerated the runs directory inline, so one malformed
    record took it down — and listing fewer tasks silently would have been worse."""
    _one_broken(tmp_path)
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_board(argparse.Namespace(all=True))
    printed = capsys.readouterr().out
    assert "could not be read" in printed and "broken" in printed, printed


def test_the_board_says_it_even_when_nothing_is_active(tmp_path, monkeypatch, capsys):
    """"No active tasks" must not stand alone as the whole answer while a record could not be
    read: the task that could not be read might be the active one."""
    runs = _runs(tmp_path)
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_board(argparse.Namespace(all=False))
    printed = capsys.readouterr().out
    assert "could not be read" in printed and "broken" in printed, printed


def test_stats_reports_the_shortfall_with_and_without_matches(tmp_path, monkeypatch, capsys):
    """A record that could not be read was never compared against the filters, so neither
    `Runs: 1` nor "no matching runs" is the whole answer."""
    import argparse
    args = argparse.Namespace(last=None, recipe=None, verifier=None)

    _one_broken(tmp_path)
    monkeypatch.chdir(_repo(tmp_path))
    cmd_stats(args)
    with_matches = capsys.readouterr().out
    assert "could not be read" in with_matches and "broken" in with_matches, with_matches

    cmd_stats(argparse.Namespace(last=None, recipe="nothing-matches", verifier=None))
    without = capsys.readouterr().out
    assert "No matching runs" in without and "could not be read" in without, without


def test_the_digest_says_it_even_when_the_period_is_empty(tmp_path, monkeypatch, capsys):
    """The early return asserted emptiness about a record whose date was never legible."""
    runs = _runs(tmp_path)
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_digest(argparse.Namespace(period="week", out=None))
    printed = capsys.readouterr().out
    assert "No runs in period" in printed and "could not be read" in printed, printed


def test_the_rendered_page_shows_the_shortfall_beside_the_total(tmp_path):
    """The snapshot carried the fields and the page rendered `1 total` anyway — the exact
    misleading total #488 prohibits."""
    _one_broken(tmp_path)
    page = mission_control.render_html(mission_control.build_snapshot(tmp_path))
    assert "could not be read" in page and "broken" in page


def test_the_live_ui_renders_the_same_note(tmp_path):
    """The static page and the live UI read one preformatted note from the snapshot, so they
    cannot word the same shortfall differently — or one of them omit it."""
    assert "tasks_unreadable_note" in mission_ui.JS_TEMPLATE
    snapshot = mission_control.build_snapshot(_one_broken(tmp_path).parent.parent)
    assert snapshot["operations"]["tasks_unreadable_note"], "the note is not in the snapshot"


# ── what review round 2 found ────────────────────────────────────────────────
@pytest.mark.parametrize("created_at", [
    "not-a-date",
    "2026-08-24",                    # a date, but naive once parsed
    "2026-08-24T10:00:00",           # a datetime, still naive
    "24/08/2026",
])
def test_a_created_at_that_is_not_a_usable_date_is_not_usable(tmp_path, created_at):
    """`created_at` is not read as text: `budget_status` parses it and `stats --last`
    compares it against an aware `now`. A string that is merely non-empty leaves the crash
    exactly where it was, one reader further down — and a naive value raises on comparison
    just as surely as an unparseable one."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    _task(runs, "bad-date", created_at=created_at)

    records = read_all_tasks(runs)
    assert [t["task_id"] for t in records.tasks] == ["readable"]
    assert records.unreadable == ("bad-date",), f"{created_at!r} was accepted as a date"


def test_every_shipped_record_satisfies_the_rule():
    """The rule is only right if the records that exist meet it. A validation that names real
    runs unreadable would have replaced one broken view with another."""
    shipped = read_all_tasks(pathlib.Path(__file__).resolve().parent.parent / ".rig" / "runs")
    assert shipped.unreadable == (), f"the rule rejects real records: {shipped.unreadable}"
    assert shipped.collection_error is None


def test_the_log_survives_and_names_it(tmp_path, monkeypatch, capsys):
    """The third reader that enumerated the runs directory inline. `latest N` counted from a
    list that had silently lost entries would be a different way of saying something untrue."""
    _one_broken(tmp_path)
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_log(argparse.Namespace(limit=10, json=False))
    printed = capsys.readouterr().out
    assert "could not be read" in printed and "broken" in printed, printed


def test_the_logs_json_carries_the_shortfall(tmp_path, monkeypatch, capsys):
    """A consumer parsing the JSON is exactly the caller that cannot see a printed note."""
    _one_broken(tmp_path)
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_log(argparse.Namespace(limit=10, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert [t["task_id"] for t in payload["entries"]] == ["readable"]
    assert payload["unreadable"] == ["broken"]


def test_the_log_does_not_call_a_directory_empty_while_naming_what_is_in_it(
        tmp_path, monkeypatch, capsys):
    runs = _runs(tmp_path)
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_log(argparse.Namespace(limit=10, json=False))
    printed = capsys.readouterr().out
    assert "is empty" not in printed, printed
    assert "broken" in printed


def test_the_board_does_not_call_a_directory_empty_while_naming_what_is_in_it(
        tmp_path, monkeypatch, capsys):
    """Two sentences that contradict each other are worse than either alone: the line above
    has just named what is in the directory this one calls empty."""
    runs = _runs(tmp_path)
    (runs / "broken").mkdir()
    (runs / "broken" / "task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_board(argparse.Namespace(all=True))
    printed = capsys.readouterr().out
    assert "is empty" not in printed, printed
    assert "broken" in printed and "No readable tasks" in printed


def test_an_actually_empty_directory_still_says_so(tmp_path, monkeypatch, capsys):
    """The positive control for the sentence above: with nothing unreadable, the plain wording
    is still what a user sees, because "no readable tasks" would imply a shortfall that is not
    there."""
    _runs(tmp_path)
    monkeypatch.chdir(_repo(tmp_path))
    import argparse
    cmd_board(argparse.Namespace(all=True))
    assert "(.rig/runs/ is empty)" in capsys.readouterr().out


# ── what review round 3 found ────────────────────────────────────────────────
#: Written out rather than derived from `OPTIONAL_FIELDS`, for the same reason as the
#: required ones: a test that iterates the rule shrinks with it. These are values the schema
#: rejects; whether a given reader would also have crashed on each one is a separate and
#: narrower claim, made below.
_OPTIONAL_AND_WHAT_THE_SCHEMA_REJECTS = [
    ("recipe", {"name": "bugfix"}), ("recipe", 7),
    ("updated_at", {"at": "2026"}), ("updated_at", 20260824),
    ("budget_minutes", "30"), ("budget_minutes", []),
]


def test_the_optional_fields_are_the_ones_named_here():
    assert set(OPTIONAL_FIELDS) == {"recipe", "updated_at", "budget_minutes"}


@pytest.mark.parametrize("field,value", _OPTIONAL_AND_WHAT_THE_SCHEMA_REJECTS)
def test_an_optional_field_present_with_an_unusable_type_is_not_usable(tmp_path, field, value):
    """Absent is ordinary — every reader guards these with `.get()`. Present with a type the
    schema does not allow is not the same thing: the record is named rather than handed on,
    whether or not the particular reader looking at it today would have limped along."""
    runs = _runs(tmp_path)
    _task(runs, "readable")
    _task(runs, "bad-optional", **{field: value})

    records = read_all_tasks(runs)
    assert [t["task_id"] for t in records.tasks] == ["readable"]
    assert records.unreadable == ("bad-optional",), f"{field}={value!r} was accepted"


@pytest.mark.parametrize("field", ["recipe", "updated_at", "budget_minutes"])
def test_an_optional_field_that_is_absent_or_null_is_fine(tmp_path, field):
    """The positive control for the rule above: rejecting an absent optional field would name
    most of the real records in this repository unreadable."""
    runs = _runs(tmp_path)
    _task(runs, "absent")
    _task(runs, "null", **{field: None})
    assert read_all_tasks(runs).unreadable == ()


#: Each optional field, paired with a value that genuinely breaks the reader that consumes
#: it. Not every schema violation crashes something — `recipe: 7` formats and counts fine —
#: and claiming otherwise would be this file asserting more than it checks. What each entry
#: establishes is narrower and sufficient: the field is on the list because a reader *can* be
#: killed through it, so validating its type is a fix rather than a strictness setting.
_A_VALUE_THAT_BREAKS_ITS_READER = [
    ("recipe", {"name": "bugfix"}, "board"),
    ("budget_minutes", "30", "board"),
    ("updated_at", {"at": "2026"}, "server"),
]


@pytest.mark.parametrize("field,value,reader", _A_VALUE_THAT_BREAKS_ITS_READER)
def test_each_optional_field_can_kill_the_reader_that_consumes_it(monkeypatch, field, value,
                                                                  reader):
    """The list is not a preference. `board` formats `recipe` to a width and compares
    `budget_minutes` against elapsed minutes; the server sorts `updated_at` against a string.
    Each is reachable, so each is a field whose type has to hold."""
    from rig_workbench import mission_server
    from rig_workbench.workbench import reporting

    record = {"task_id": "t", "task_type": "bugfix", "status": "running",
              "created_at": "2026-08-24T10:00:00+09:00", "input": "x", field: value}
    # The server sorts, and a sort of one element never compares anything — so the record it
    # is handed has a neighbour to be compared against, which is the situation any real runs
    # directory with two tasks is already in.
    neighbour = {"task_id": "u", "task_type": "bugfix", "status": "running",
                 "created_at": "2026-08-24T09:00:00+09:00", "input": "y",
                 "updated_at": "2026-08-24T09:30:00+09:00"}
    monkeypatch.setattr(reporting, "read_all_tasks",
                        lambda base: TaskRecords(tasks=(record,)))
    monkeypatch.setattr(mission_server, "read_all_tasks",
                        lambda base: TaskRecords(tasks=(record, neighbour)))
    monkeypatch.setattr(reporting, "repo_root", lambda: pathlib.Path("/nonexistent"))
    import argparse
    with pytest.raises((KeyError, TypeError, ValueError, AttributeError)):
        if reader == "board":
            reporting.cmd_board(argparse.Namespace(all=True))
        else:
            mission_server.live_snapshot(pathlib.Path("/nonexistent"))


def test_every_field_on_the_optional_list_has_a_case_that_reaches_a_reader():
    """Both directions, so a field cannot be added to the rule without showing why it is
    there, and one cannot be removed while a case still names it."""
    assert {field for field, _value, _reader in _A_VALUE_THAT_BREAKS_ITS_READER} == set(
        OPTIONAL_FIELDS)
