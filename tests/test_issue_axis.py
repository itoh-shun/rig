"""A run can say which issue it is against, and the board groups by it (#548, slice 3).

Nothing linked a run to an issue. The queue came closest and only in the other direction:
`queue_set_status(..., task_id=...)` writes the link onto the item, and its `gh` branch —
the backend this axis is actually about — relabels, comments and closes the issue and drops
the task id entirely. So the run itself had nothing to say.

Two properties are pinned here, and they pull in opposite directions on purpose:

* the reference is recorded when somebody declares it, and refused when it is not something
  that resolves — a board cell rendered as an identity must not hold a string nobody can
  follow;
* the grouping speaks only in the past tense. "Which session is working on #N" is a claim
  about the present, and a crashed run leaves a record that says `RUNNING` forever.
"""

import json
import pathlib

import pytest

from rig_workbench.workbench import run_index
from rig_workbench.workbench.issue_link import FORMS, IssueRefError, declared, parse


@pytest.mark.parametrize("value", ["#1", "#123", "#4294967295", "owner/repo#7",
                                   "Owner-1/repo.name_2#88", "  #12  "])
def test_the_two_forms_a_reference_may_take(value):
    assert parse(value) == value.strip()


@pytest.mark.parametrize("value", [
    "",
    "   ",
    "123",                                    # no marker at all
    "#0",                                     # issues are 1-based; #0 resolves to nothing
    "#01",                                    # a leading zero is not the number it looks like
    "#12a",
    "issue #12",                              # prose, which is what free-text scraping yields
    "owner#12",                               # half a repository
    "owner/repo/extra#12",
    "https://github.com/owner/repo/issues/12",
])
def test_anything_that_would_not_resolve_is_refused(value):
    with pytest.raises(IssueRefError):
        parse(value)


def test_a_pasted_url_is_told_what_to_paste_instead():
    """The likely mistake, and the one worth a specific message: deriving `owner/repo#n` from
    a URL means deciding which hosts map to that shape, and a wrong mapping produces a
    reference that resolves to somebody else's issue."""
    with pytest.raises(IssueRefError) as raised:
        parse("https://github.com/owner/repo/issues/12")
    assert "paste the reference, not the URL" in str(raised.value)


def test_a_refusal_names_the_forms_that_would_work():
    """A refusal that does not say what to type instead sends the operator to the source."""
    with pytest.raises(IssueRefError) as raised:
        parse("issue 12")
    assert FORMS in str(raised.value)


def test_nothing_declared_records_nothing():
    """Absent, not a block with an empty ref. A present key holding nothing would put "no
    issue" and "an issue we failed to record" in the same row."""
    assert declared(None) is None


def test_a_declaration_is_marked_as_one():
    assert declared("#12") == {"ref": "#12", "source": "flag", "declared": True}


@pytest.mark.parametrize("value", ["issue 12", "#0", "https://github.com/o/r/issues/12"])
def test_the_recorded_path_refuses_what_the_parser_refuses(value):
    """`parse` being strict buys nothing if the function that records goes around it. This is
    the path `--issue` actually takes, and it is the one that decides whether a task file can
    end up holding a reference nobody can follow."""
    with pytest.raises(IssueRefError):
        declared(value)


def _log(tmp_path: pathlib.Path, records: list[dict]) -> pathlib.Path:
    path = tmp_path / "runs.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _record(run_id: str, *, issue=None, caller=None, final="DONE",
            ts="2026-08-30T10:00:00+00:00", project="/repo/a"):
    record = {"run_id": run_id, "ts": ts, "project": project, "recipe": "dev",
              "backend": "workbench", "final": final, "steps": []}
    if issue is not None:
        record["issue"] = issue
    if caller is not None:
        record["caller"] = caller
    return record


def test_the_row_carries_the_declared_reference(tmp_path):
    path = _log(tmp_path, [_record("orc-1", issue={"ref": "#12", "source": "flag",
                                                   "declared": True})])
    index = run_index.run_index(path=path)
    assert index["rows"][0]["issue"] == "#12"


def test_a_run_with_no_issue_carries_none_and_joins_no_group(tmp_path):
    path = _log(tmp_path, [_record("orc-1")])
    index = run_index.run_index(path=path)
    assert index["rows"][0]["issue"] is None
    assert index["by_issue"] == {}


@pytest.mark.parametrize("block", ["#12", 12, [], {"ref": ""}, {"ref": 12}, {}])
def test_an_issue_block_of_another_shape_is_not_coerced(tmp_path, block):
    """Several writers append to this log. A record whose `issue` is not the block rig writes
    is one this cannot interpret, and turning it into a string would invent a group with a
    name nobody chose."""
    path = _log(tmp_path, [_record("orc-1", issue=block)])
    index = run_index.run_index(path=path)
    assert index["rows"][0]["issue"] is None
    assert index["by_issue"] == {}


def test_runs_against_one_issue_are_counted_together_across_projects(tmp_path):
    issue = {"ref": "owner/repo#7", "source": "flag", "declared": True}
    path = _log(tmp_path, [
        _record("orc-1", issue=issue, ts="2026-08-30T09:00:00+00:00", project="/repo/a"),
        _record("orc-2", issue=issue, ts="2026-08-30T11:00:00+00:00", project="/repo/b"),
    ])
    entry = run_index.run_index(path=path)["by_issue"]["owner/repo#7"]
    assert entry["runs"] == 2
    assert entry["projects"] == ["/repo/a", "/repo/b"]


def test_the_group_reports_the_newest_record_not_the_first_one_read(tmp_path):
    """`last_final` has to follow the newest record. Taking whichever appeared first in the
    file would leave a finished issue showing the outcome of a run that has since been
    superseded."""
    issue = {"ref": "#7", "source": "flag", "declared": True}
    path = _log(tmp_path, [
        _record("orc-old", issue=issue, ts="2026-08-30T09:00:00+00:00", final="REJECTED"),
        _record("orc-new", issue=issue, ts="2026-08-30T11:00:00+00:00", final="DONE"),
    ])
    entry = run_index.run_index(path=path)["by_issue"]["#7"]
    assert (entry["last_final"], entry["last_run_id"]) == ("DONE", "orc-new")
    assert entry["last_ts"] == "2026-08-30T11:00:00+00:00"


def test_newest_is_decided_by_instant_not_by_string(tmp_path):
    """The offsets in this log are whatever was local to the machine that wrote each record,
    and a cross-project log is exactly where they meet. `09:00+09:00` sorts after
    `02:00+00:00` as text and before it in fact."""
    issue = {"ref": "#7", "source": "flag", "declared": True}
    path = _log(tmp_path, [
        _record("orc-tokyo", issue=issue, ts="2026-08-30T09:00:00+09:00", final="REJECTED"),
        _record("orc-utc", issue=issue, ts="2026-08-30T02:30:00+00:00", final="DONE"),
    ])
    entry = run_index.run_index(path=path)["by_issue"]["#7"]
    assert entry["last_run_id"] == "orc-utc", "09:00+09:00 is 00:00Z, so the UTC row is newer"


def test_the_grouping_never_claims_a_run_is_still_going(tmp_path):
    """The refusal the slice exists to encode. A record says `RUNNING` for as long as the log
    survives, whether the run finished, crashed, or was abandoned — so every key here is about
    what was recorded, and none of them says anything about now."""
    path = _log(tmp_path, [_record("orc-1", issue={"ref": "#7", "source": "flag",
                                                   "declared": True}, final="RUNNING")])
    entry = run_index.run_index(path=path)["by_issue"]["#7"]

    assert set(entry) == {"runs", "last_final", "last_ts", "last_run_id", "projects",
                          "sessions"}
    assert entry["last_final"] == "RUNNING", "the recorded value is reported as recorded"
    assert not {"active", "in_progress", "working", "current", "live", "stale",
                "is_running"} & set(entry)


def test_the_sessions_that_worked_an_issue_are_listed_flat(tmp_path):
    """The join the whole axis was asked for: which session against which issue (#548,
    slice 4). Listed, deduplicated, and sorted — never nested. Claude Code hands a subagent's
    shell the same variables as its parent's, so this log cannot tell a child session from
    the one that dispatched it, and a tree drawn from it would be a drawing."""
    issue = {"ref": "#7", "source": "flag", "declared": True}
    path = _log(tmp_path, [
        _record("orc-1", issue=issue, ts="2026-08-30T09:00:00+00:00",
                caller={"id": "claude-code", "source": "env:CLAUDECODE", "declared": False,
                        "session": "s-aaa"}),
        _record("orc-2", issue=issue, ts="2026-08-30T10:00:00+00:00",
                caller={"id": "claude-code", "source": "env:CLAUDECODE", "declared": False,
                        "session": "s-bbb"}),
        _record("orc-3", issue=issue, ts="2026-08-30T11:00:00+00:00",
                caller={"id": "claude-code", "source": "env:CLAUDECODE", "declared": False,
                        "session": "s-bbb"}),
    ])
    entry = run_index.run_index(path=path)["by_issue"]["#7"]
    # Rows arrive newest first, so first sighting would give ["s-bbb", "s-aaa"]. Sorted, so
    # nobody reads the first entry as "the one working on it now".
    assert entry["sessions"] == ["s-aaa", "s-bbb"]
    assert entry["runs"] == 3


def test_a_run_outside_a_harness_contributes_no_session(tmp_path):
    """A plain terminal names no session, and `unknown` is a recorded caller rather than a
    session of its own."""
    path = _log(tmp_path, [
        _record("orc-1", issue={"ref": "#7", "source": "flag", "declared": True},
                caller={"id": "unknown", "source": "none", "declared": False}),
    ])
    index = run_index.run_index(path=path)
    assert index["rows"][0]["caller"] == "unknown"
    assert index["rows"][0]["session"] is None
    assert index["by_issue"]["#7"]["sessions"] == []


def test_a_record_written_before_the_caller_field_reads_as_absent(tmp_path):
    """370 records on this machine predate it. They must read as "not recorded", not as a
    caller named None."""
    path = _log(tmp_path, [_record("orc-1")])
    row = run_index.run_index(path=path)["rows"][0]
    assert (row["caller"], row["session"]) == (None, None)


def test_the_row_has_nowhere_to_put_a_session_hierarchy(tmp_path):
    """The refusal slice 4 exists to encode, pinned as a key set. `context_meter` already
    declines to publish a dispatch rate on this same evidence."""
    path = _log(tmp_path, [
        _record("orc-1", caller={"id": "claude-code", "source": "env:CLAUDECODE",
                                 "declared": False, "session": "s-1"}),
    ])
    row = run_index.run_index(path=path)["rows"][0]
    assert not {"parent_session", "session_parent", "depth", "session_depth",
                "children", "session_tree"} & set(row)


def test_the_gate_module_records_a_caller_only_when_handed_one(tmp_path, monkeypatch):
    """`runstate` holds gate evaluation, and `test_caller_contract` forbids it from mentioning
    the caller — at file granularity, because a gate that can see who called it is a gate that
    can soften for one harness. So it takes the value as data and decides nothing: handed
    nothing, it writes nothing, no matter what the environment says."""
    from rig_workbench.orchestrate import config, runstate

    local = tmp_path / "runs.jsonl"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-should-not-appear")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "global.jsonl")
    monkeypatch.setattr(config, "RUNS_PATH", local)
    state = {"run_id": "orc-1", "recipe": "dev", "steps": [], "step_state": {},
             "token_usage": {}}

    runstate.telemetry_append(state, "DONE")
    runstate.telemetry_append(state, "DONE", caller_record={"id": "claude-code",
                                                           "source": "env:CLAUDECODE",
                                                           "declared": False,
                                                           "session": "s-given"})

    rows = [json.loads(ln) for ln in local.read_text().splitlines() if ln.strip()]
    assert "caller" not in rows[0], "no caller was handed in, so none is recorded"
    assert rows[1]["caller"]["session"] == "s-given"
