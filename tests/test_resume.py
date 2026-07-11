"""Unit tests for the verify-first resume ritual (rig_workbench.orchestrate.commands.cmd_resume).

`resume` re-verifies the world before continuing a persisted run: it prints a digest,
re-runs the current running step's declared machine checks, refuses to advance when a
previously-passing check now fails ("world drift"), and otherwise continues via compute_next.
"""

import os

import pytest

from rig_workbench.orchestrate.commands import cmd_resume
from rig_workbench.orchestrate.runstate import load_state, new_state, save_state


def _write_state(tmp_path, steps, **mutate):
    """Persist a run-state and return its path. `mutate` maps step-id → step_state overrides."""
    state = new_state("resume-test", steps, None)
    for sid, overrides in mutate.items():
        state["step_state"][sid].update(overrides)
    path = tmp_path / "run-state.json"
    save_state(state, path)
    return path


def test_digest_reports_machine_tokens(tmp_path, step_factory, capsys):
    """The digest exposes recipe, cursor/total, done count, per-step status, and REJECTs."""
    steps = [step_factory(id="design"),
             step_factory(id="review", gate="review-gate")]
    path = _write_state(
        tmp_path, steps,
        design={"status": "passed"},
        review={"status": "running",
                "verdicts": [{"by": "security-reviewer", "ok": False, "note": ""}]})
    cmd_resume([str(path)])
    out = capsys.readouterr().out
    assert "## resume: resume-test" in out
    assert "cursor=0/2" in out
    assert "done=1/2" in out
    assert "design" in out and "passed" in out
    assert "review" in out and "running" in out
    assert "REJECT" in out and "security-reviewer" in out


def test_rerun_pass_advances(tmp_path, step_factory, capsys):
    """A running step whose recorded checks still pass re-verifies then ADVANCEs (exit 0)."""
    steps = [step_factory(id="verify", gate="acceptance-gate", checks=["true"]),
             step_factory(id="review", gate="review-gate")]
    path = _write_state(
        tmp_path, steps,
        verify={"status": "running", "checks": [{"cmd": "true", "ok": True}]})
    cmd_resume([str(path)])  # ADVANCE does not raise SystemExit
    out = capsys.readouterr().out
    assert "re-verify" in out
    assert "world still matches" in out
    assert "▶ ADVANCE" in out
    # side effect matches check+next: verify passed, cursor advanced
    state = load_state(path)
    assert state["step_state"]["verify"]["status"] == "passed"
    assert state["cursor"] == 1


def test_rerun_fail_refuses(tmp_path, step_factory, capsys):
    """A recorded-passing check that now fails is world drift → refuse + non-zero exit."""
    steps = [step_factory(id="verify", gate="acceptance-gate", checks=["false"])]
    path = _write_state(
        tmp_path, steps,
        verify={"status": "running", "checks": [{"cmd": "false", "ok": True}]})
    with pytest.raises(SystemExit) as exc:
        cmd_resume([str(path)])
    assert exc.value.code != 0
    out = capsys.readouterr().out
    assert "WORLD DRIFTED" in out
    assert "DRIFT" in out
    assert "▶ ADVANCE" not in out  # refused to advance
    # the step is NOT advanced; it stays running for a re-run
    state = load_state(path)
    assert state["step_state"]["verify"]["status"] == "running"
    assert state["cursor"] == 0


def test_no_checks_step_passes_through(tmp_path, step_factory, capsys):
    """A running step that declares no checks skips re-verify and continues via next."""
    steps = [step_factory(id="design"),
             step_factory(id="review", gate="review-gate")]
    path = _write_state(tmp_path, steps, design={"status": "running"})
    cmd_resume([str(path)])
    out = capsys.readouterr().out
    assert "re-verify" not in out  # nothing to re-run
    assert "▶ ADVANCE" in out
    state = load_state(path)
    assert state["step_state"]["design"]["status"] == "passed"


def test_mtime_gap_note_for_old_state(tmp_path, step_factory, capsys):
    """An old run-state.json prints the informational 'resumed after ~' compaction cue."""
    steps = [step_factory(id="design")]
    path = _write_state(tmp_path, steps, design={"status": "running"})
    old = os.stat(path).st_mtime - 7200  # 2 hours ago
    os.utime(path, (old, old))
    cmd_resume([str(path)])
    out = capsys.readouterr().out
    assert "resumed after ~" in out
    assert "2h" in out


def test_fresh_state_has_no_mtime_note(tmp_path, step_factory, capsys):
    """A just-written run-state (< 1h old) does not emit the mtime-gap cue."""
    steps = [step_factory(id="design")]
    path = _write_state(tmp_path, steps, design={"status": "running"})
    cmd_resume([str(path)])
    out = capsys.readouterr().out
    assert "resumed after ~" not in out
