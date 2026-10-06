"""`wb autonomy` — the record and the brakes of an --autonomous run.

`--autonomous` takes the step gate away, and with it two things the gate did: a chance to stop
the run and a moment where somebody saw what it decided. These tests pin the replacement for
both: every brake `check` applies is computed from the journal and the kill switches alone, and
the report hands the reader what was decided for them.
"""

import datetime
import json

import pytest

from rig_workbench.workbench import autonomy


class FixedClock:
    def __init__(self, when):
        self.when = when

    def now(self):
        return self.when

    def today(self):
        return self.when.date()

    def stamp(self, when=None):
        return (when or self.when).isoformat(timespec="seconds")


T0 = datetime.datetime(2026, 10, 6, 9, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


class NoGit:
    def head(self, *, cwd=None):
        return None


@pytest.fixture
def run(tmp_path):
    autonomy.start(tmp_path, "r1", {"max_steps": 3, "max_minutes": 60, "max_recoveries": 3},
                   "fix the login bug", clock=FixedClock(T0))
    return tmp_path


def entry(root, kind, summary="x", **kw):
    return autonomy.log(root, "r1", kind, summary, clock=FixedClock(T0), git=NoGit(), **kw)


def verdict(root, minutes=1):
    records = autonomy.read_journal(root, "r1")
    return autonomy.evaluate(root, "r1", records,
                             clock=FixedClock(T0 + datetime.timedelta(minutes=minutes)))


def test_a_fresh_run_may_continue(run):
    v = verdict(run)
    assert v["continue"] is True
    assert v["reasons"] == []
    assert v["limits"]["max_recoveries_per_step"] == autonomy.DEFAULT_LIMITS["max_recoveries_per_step"]


def test_the_global_kill_switch_stops_every_run(run):
    (run / ".rig" / "STOP").write_text("", encoding="utf-8")
    v = verdict(run)
    assert v["continue"] is False
    assert any("kill switch" in r for r in v["reasons"])


def test_stop_sets_a_kill_switch_for_this_run_only(run):
    autonomy.set_stop(run, "r1", "going to lunch")
    assert verdict(run)["continue"] is False
    autonomy.start(run, "r2", {}, clock=FixedClock(T0))
    other = autonomy.evaluate(run, "r2", autonomy.read_journal(run, "r2"),
                              clock=FixedClock(T0))
    assert other["continue"] is True


def test_a_hard_stop_always_stops_and_needs_a_named_reason(run):
    with pytest.raises(autonomy.UsageError):
        entry(run, autonomy.HARD_STOP, "force push needed")
    entry(run, autonomy.HARD_STOP, "force push needed", reason="destructive-operation")
    v = verdict(run)
    assert v["continue"] is False
    assert any("destructive-operation" in r for r in v["reasons"])


def test_the_step_ceiling_stops_the_run(run):
    for step in ("design", "implement", "verify"):
        entry(run, autonomy.STEP_DONE, step=step)
    assert "max steps reached (3/3)" in verdict(run)["reasons"]


def test_the_minute_ceiling_stops_the_run(run):
    assert verdict(run, minutes=59)["continue"] is True
    assert verdict(run, minutes=60)["continue"] is False


def test_the_recovery_ladder_is_per_step_and_resets_when_the_step_finishes(run):
    entry(run, autonomy.RECOVERY, "retry with narrower scope", step="implement")
    assert verdict(run)["continue"] is True
    entry(run, autonomy.RECOVERY, "retry with a different approach", step="implement")
    v = verdict(run)
    assert v["continue"] is False
    assert any("recovery ladder exhausted on step 'implement'" in r for r in v["reasons"])


def test_a_finished_step_does_not_carry_its_recoveries_into_the_next_round(tmp_path):
    autonomy.start(tmp_path, "r1", {}, clock=FixedClock(T0))
    entry(tmp_path, autonomy.RECOVERY, step="implement")
    entry(tmp_path, autonomy.STEP_DONE, step="implement")
    entry(tmp_path, autonomy.RECOVERY, step="implement")
    v = verdict(tmp_path)
    assert v["continue"] is True
    assert v["usage"]["recoveries_on_current_step"] == 1


def test_the_total_recovery_ceiling_holds_across_steps(run):
    for step in ("a", "b", "c"):
        entry(run, autonomy.RECOVERY, step=step)
    assert any("max recoveries reached" in r for r in verdict(run)["reasons"])


def test_ceilings_are_fixed_at_start_and_cannot_be_restarted(run):
    with pytest.raises(autonomy.UsageError):
        autonomy.start(run, "r1", {"max_steps": 999})
    with pytest.raises(autonomy.UsageError):
        autonomy.start(run, "r9", {"max_steps": 0})


@pytest.mark.parametrize("bad", ["", "../x", "a/b", ".hidden", "x" * 65, "a..b"])
def test_a_run_id_cannot_name_a_file_outside_the_journal_directory(bad):
    with pytest.raises(autonomy.UsageError):
        autonomy.check_run_id(bad)


def test_unknown_kinds_and_logging_before_start_are_refused(tmp_path, run):
    with pytest.raises(autonomy.UsageError):
        entry(run, "vibes")
    with pytest.raises(autonomy.UsageError):
        autonomy.log(tmp_path / "elsewhere", "r1", "decision", "x")


def test_a_checkpoint_without_a_readable_head_is_refused(run):
    with pytest.raises(autonomy.UsageError):
        entry(run, autonomy.CHECKPOINT, "before implement", step="implement")
    rec = entry(run, autonomy.CHECKPOINT, "before implement", step="implement", ref="abc123")
    assert rec["ref"] == "abc123"


def test_the_report_hands_over_what_was_decided_and_what_to_confirm(run):
    entry(run, autonomy.GATE_SKIPPED, "design → implement", step="design")
    entry(run, autonomy.DECISION, "kept the session cookie name", step="implement")
    entry(run, autonomy.ASSUMPTION, "the bug only affects SSO users", step="implement")
    entry(run, autonomy.DEFERRED_QUESTION, "should the old endpoint be removed?")
    entry(run, autonomy.CHECKPOINT, "before implement", step="implement", ref="abc123")
    entry(run, autonomy.FINISH, "all criteria met")
    records = autonomy.read_journal(run, "r1")
    text = autonomy.render_report("r1", records, verdict(run))
    assert "status: finished (all criteria met) | goal: fix the login bug" in text
    assert text.index("Questions deferred to you") < text.index("Assumptions to confirm")
    assert "- [implement] before implement (ref abc123)" in text
    assert "should the old endpoint be removed?" in text


def test_a_run_that_logged_nothing_does_not_read_as_clean(run):
    text = autonomy.render_report("r1", autonomy.read_journal(run, "r1"), verdict(run))
    assert "Nothing was logged after start" in text


def test_an_unreadable_line_is_reported_not_dropped(run):
    path = autonomy.journal_path(run, "r1")
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    records = autonomy.read_journal(run, "r1")
    assert "[WARN] 1 journal line(s) could not be read" in autonomy.render_report(
        "r1", records, verdict(run))


def test_an_unreadable_start_time_stops_rather_than_disabling_the_minute_ceiling(tmp_path):
    path = autonomy.journal_path(tmp_path, "r1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"kind": "start", "started_at": "yesterday", "limits": {}}) + "\n",
                    encoding="utf-8")
    v = autonomy.evaluate(tmp_path, "r1", autonomy.read_journal(tmp_path, "r1"),
                          clock=FixedClock(T0))
    assert v["continue"] is False


def test_check_exits_1_when_the_run_must_stop_and_2_without_a_journal(rig_git_repo, rig_cli):
    missing = rig_cli("wb", "autonomy", "check", "--run", "nope", cwd=rig_git_repo)
    assert missing.returncode == 2
    assert rig_cli("wb", "autonomy", "start", "--run", "r1", cwd=rig_git_repo).returncode == 0
    assert rig_cli("wb", "autonomy", "check", "--run", "r1", cwd=rig_git_repo).returncode == 0
    assert rig_cli("wb", "autonomy", "log", "--run", "r1", "--kind", "checkpoint",
                   "--summary", "before implement", "--step", "implement",
                   cwd=rig_git_repo).returncode == 0
    assert rig_cli("wb", "autonomy", "stop", "--run", "r1", cwd=rig_git_repo).returncode == 0
    stopped = rig_cli("wb", "autonomy", "check", "--run", "r1", cwd=rig_git_repo)
    assert stopped.returncode == 1
    assert "kill switch" in stopped.stderr
    report = rig_cli("wb", "autonomy", "report", "--run", "r1", cwd=rig_git_repo)
    assert report.returncode == 0
    assert "Checkpoints (roll back here) (1)" in report.stdout
