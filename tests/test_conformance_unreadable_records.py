"""A conformance rate must say how many records it could not read (#493).

`govern conformance` and the `usage` task counter each walked `.rig/runs/*/task.json`
themselves and `continue`d past anything they could not parse. Nothing crashed — that was
the problem. A rate computed from 52 of 55 records was printed as *the* rate, and the runs
that vanished were exactly the ones nobody could inspect. This is the same fail-open #488
rejected for the board ("a task missing from it reads as a task that does not exist"), one
layer over, where the number leaves the repository and goes to an org.

The decision this file pins down: an unreadable record is **not** put in the denominator —
counting it as non-compliant would assert something nobody read — and it is **not** dropped.
It is named beside the total, in the same sentence every other reader of the runs directory
uses (`TaskRecords.note()`), and it travels in the JSON, because a machine consumer cannot
see a printed note.

Every assertion here names the *attempted* total ("1 of 2"), not merely that some note
appeared: an implementation that silently skipped a record could still render a note about
the records it did read.
"""

import json
import pathlib

import pytest

from rig_workbench.govern import conformance as conf

WINDOW = 90


def govern_repo(tmp_path: pathlib.Path, **policy_overrides) -> pathlib.Path:
    (tmp_path / ".rig" / "policy").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    doc = {"schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
           "version": "1.0.0",
           "roles": {"dev": ["accept", "approve"]}, "members": {"alice": ["dev"]},
           "require_criteria": {"feature": ["tests_pass"]},
           "approvals": {"feature": {"quorum": 1}}}
    doc.update(policy_overrides)
    (tmp_path / ".rig" / "policy" / "org.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def add_task(root: pathlib.Path, task_id: str, **fields) -> pathlib.Path:
    """A run directory holding a record every reader can use."""
    d = root / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    record = {"task_id": task_id, "task_type": "feature", "status": "accepted",
              "input": f"do {task_id}", "created_at": "2026-08-24T10:00:00+09:00",
              "updated_at": "2026-08-24T11:00:00+09:00"}
    record.update(fields)
    (d / "task.json").write_text(json.dumps(record), encoding="utf-8")
    (d / "acceptance.json").write_text(json.dumps(
        {"task_id": task_id, "presets": ["standard", "feature"],
         "checks": [{"name": "tests_pass", "status": "passed"}]}), encoding="utf-8")
    return d


def add_unreadable(root: pathlib.Path, task_id: str = "broken") -> pathlib.Path:
    d = root / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text("{not json", encoding="utf-8")
    return d


def check(report, check_id):
    return next(c for c in report.checks if c.id == check_id)


# ── the rate carries the shortfall ───────────────────────────────────────────
@pytest.mark.parametrize("check_id", ["required_criteria", "approvals", "force_rate"])
def test_every_check_that_states_a_run_count_names_what_it_could_not_read(tmp_path, check_id):
    """The count lives in the check details, so that is where it has to say so.

    `score` is a fraction of *checks*, and these three are the ones whose verdict is
    computed from run records. Naming the shortfall only in the header would leave
    "2 run(s) in the window are clean" standing as a claim about the directory.
    """
    repo = govern_repo(tmp_path)
    add_task(repo, "t1", forced=True)
    add_unreadable(repo)

    report = conf.evaluate_project(repo, since_days=WINDOW)

    assert "1 of 2 records could not be read: broken" in check(report, check_id).detail


def test_the_force_rate_is_stated_over_the_denominator_it_actually_had(tmp_path):
    """The number a lost record moves the most.

    `broken` may have been an accepted run that was not forced, in which case the true rate
    is 1/2 = 50% and not 1/1 = 100%. This does not guess: it prints the rate it measured and
    the count of records behind it that it never opened.
    """
    repo = govern_repo(tmp_path)
    add_task(repo, "t1", forced=True)
    add_unreadable(repo)

    detail = check(conf.evaluate_project(repo, since_days=WINDOW), "force_rate").detail

    assert "1/1 accepted runs were forced (100%)" in detail
    assert "1 of 2 records could not be read: broken" in detail


def test_no_accepted_runs_is_not_reported_as_a_clean_window(tmp_path):
    """"No accepted runs" is the fail-open in its purest form: the one record in the
    directory is the one that could not be read."""
    repo = govern_repo(tmp_path)
    add_unreadable(repo)

    detail = check(conf.evaluate_project(repo, since_days=WINDOW), "force_rate").detail

    assert detail.startswith("no accepted runs in the last 90 days")
    assert "1 of 1 records could not be read: broken" in detail


def test_an_unreadable_record_is_not_filtered_out_by_the_window(tmp_path):
    """A record whose `updated_at` was never read cannot be shown to fall outside
    `--since-days`, so the window cannot be the reason it disappears.

    And the window must not shrink the *attempted* total either: the directory holds two
    records here, one of them outside the window, so "1 of 2" is the only true sentence.
    "1 of 1" would be the same quietly smaller denominator in the very sentence that exists
    to remove it.
    """
    repo = govern_repo(tmp_path)
    add_task(repo, "old", created_at="2020-01-01T00:00:00+09:00",
             updated_at="2020-01-01T00:00:00+09:00")
    add_unreadable(repo)

    report = conf.evaluate_project(repo, since_days=1)

    assert report.runs_in_window == 0                     # the old run is outside the window
    assert "1 of 2 records could not be read: broken" in report.unreadable_note
    assert report.to_dict()["task_records"] == {"read": 1, "in_window": 0,
                                               "unreadable": ["broken"],
                                               "collection_error": None}


def test_a_directory_that_cannot_be_listed_is_not_a_clean_report(tmp_path):
    """Failing to look is not the same as finding nothing, and the report says which."""
    repo = govern_repo(tmp_path)
    (repo / ".rig" / "runs").write_text("not a directory", encoding="utf-8")

    report = conf.evaluate_project(repo, since_days=WINDOW)

    assert "the runs directory could not be listed" in report.unreadable_note
    assert report.to_dict()["task_records"]["collection_error"].startswith("NotADirectoryError")


# ── the machine-readable side ────────────────────────────────────────────────
def test_the_json_carries_the_count_a_printed_note_cannot_reach(tmp_path):
    repo = govern_repo(tmp_path)
    add_task(repo, "t1")
    add_unreadable(repo)

    payload = conf.evaluate_project(repo, since_days=WINDOW).to_dict()

    assert payload["task_records"] == {"read": 1, "in_window": 1, "unreadable": ["broken"],
                                       "collection_error": None}


def test_a_report_that_never_read_the_runs_says_so_instead_of_zero(tmp_path):
    """An unbound repository stops before any run is read. Reporting `unreadable: []` there
    would be a claim about a directory this report never opened."""
    payload = conf.evaluate_project(tmp_path, since_days=WINDOW).to_dict()

    assert payload["task_records"] is None


# ── nothing unreadable renders exactly what it used to ───────────────────────
def test_a_repository_with_no_unreadable_records_reads_as_before(tmp_path):
    """The wording is written out literally, both directions: the clause has to appear when
    a record could not be read, and be absent — not merely different — when none could."""
    repo = govern_repo(tmp_path)
    add_task(repo, "t1", forced=False)

    report = conf.evaluate_project(repo, since_days=WINDOW)

    assert report.unreadable_note == ""
    assert check(report, "force_rate").detail == "0/1 accepted runs were forced (0%)"
    assert check(report, "required_criteria").detail == (
        "1 policy-required criterion/criteria wired into the gate; "
        "1 run(s) in the window are clean")
    assert check(report, "approvals").detail == (
        "1 of 1 accepted run(s) were applied without their approvals")
    assert report.to_dict()["task_records"] == {"read": 1, "in_window": 1, "unreadable": [],
                                                "collection_error": None}
    for c in report.checks:
        assert "could not be read" not in c.detail


# ── the aggregate, where one project's shortfall is averaged away ────────────
def test_the_rollup_puts_the_count_in_the_cell_next_to_the_rate(tmp_path):
    clean = govern_repo(tmp_path / "clean")
    add_task(clean, "t1")
    lossy = govern_repo(tmp_path / "lossy")
    add_task(lossy, "t1")
    add_unreadable(lossy)

    result = conf.rollup([clean, lossy], since_days=WINDOW)
    markdown = result.markdown()

    assert "1 task record(s) could not be read (lossy/broken)" in markdown
    # The full rows, not substrings that could be true of two different ones: the count has
    # to be in the cell beside the rate it qualifies, and only in the project it came from.
    assert "| team-a | 2 | 88% (1 unread) | approvals |" in markdown
    assert "| lossy | team-a | \u2717 fail | 88% (1 unread) | " in markdown
    assert "| clean | team-a | \u2717 fail | 88% | " in markdown
    assert result.to_dict()["unreadable_task_records"] == ["lossy/broken"]
    assert result.to_dict()["teams"]["team-a"]["unreadable_task_records"] == ["lossy/broken"]


def test_the_rollup_of_readable_projects_adds_no_clause(tmp_path):
    clean = govern_repo(tmp_path / "clean")
    add_task(clean, "t1")

    result = conf.rollup([clean], since_days=WINDOW)

    assert "could not be read" not in result.markdown()
    assert "unread" not in result.markdown()
    assert result.to_dict()["unreadable_task_records"] == []


def test_the_fleet_tile_says_how_many_records_stand_behind_its_percentage():
    """Mission Control shows the rollup as one number for a whole fleet — the place a lost
    record is least visible, because it is averaged before anyone sees it."""
    from rig_workbench.mission_control import _fleet_window

    assert _fleet_window({"since_days": 30}) == "window: 30 days"
    assert _fleet_window({"since_days": 30, "unreadable_task_records": ["a/b", "c/d"]}) == (
        "window: 30 days · 2 task record(s) could not be read")
