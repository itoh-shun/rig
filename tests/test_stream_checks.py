"""Mid-implementation lightweight checks (#302): `workbench.py stream-checks`.

Pins the structural guarantees: hints for secret/injection/destructive
findings in the task diff and evidence-anchor findings in the task's recorded
review bodies, exit 0 ALWAYS (advisory), acceptance.json never touched, and
the --watch loop's change detection — including the trap that the fourth
sensor's input lives outside the diff the digest used to hash.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import types

import pytest

from rig_workbench.workbench import streaming

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _new_task(git_repo):
    r = run_cli(["new", "test task", "--type", "feature"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    task = json.loads((git_repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    return task_id, pathlib.Path(task["worktree_path"])


def test_clean_worktree_reports_no_hints_and_exits_zero(git_repo):
    task_id, _wt = _new_task(git_repo)
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    assert "no hints" in r.stdout
    assert "never blocks the gate" in r.stdout  # the advisory framing is part of the output


def test_findings_become_hints_but_exit_stays_zero(git_repo):
    task_id, wt = _new_task(git_repo)
    (wt / "oops.sh").write_text(
        'TOKEN="ghp_' + "a" * 40 + '"\ngit clean -fdx\n', encoding="utf-8")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0  # advisory: findings never change the exit code
    assert "hint[secret]" in r.stdout
    assert "hint[destructive]" in r.stdout
    assert "ghp_aaaa" not in r.stdout  # secret excerpts stay masked


def test_stream_checks_never_touches_acceptance_json(git_repo):
    task_id, wt = _new_task(git_repo)
    acc_path = git_repo / ".rig" / "runs" / task_id / "acceptance.json"
    before = acc_path.read_text(encoding="utf-8")
    (wt / "oops.sh").write_text("rm -rf /\n", encoding="utf-8")
    run_cli(["stream-checks", task_id], git_repo)
    assert acc_path.read_text(encoding="utf-8") == before  # gate state untouched


def test_gate_still_decides_pass_fail_independently(git_repo):
    # The same detector that hinted also fails the gate — streaming is a preview,
    # not a substitute.
    task_id, wt = _new_task(git_repo)
    (wt / "oops.sh").write_text("rm -rf /\n", encoding="utf-8")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    r = run_cli(["gate", task_id, "--set", "task_intent_satisfied=passed"], git_repo)
    assert r.returncode != 0  # fail-grade destructive finding fails the gate

def test_watch_mode_bounded_by_max_passes(git_repo):
    task_id, _wt = _new_task(git_repo)
    r = run_cli(["stream-checks", task_id, "--watch", "--interval", "0.05", "--max-passes", "3"],
                git_repo)
    assert r.returncode == 0  # terminates on its own


def test_no_worktree_task_errors(git_repo):
    r = run_cli(["new", "read only", "--type", "review", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode != 0
    assert "no worktree" in (r.stdout + r.stderr)


# ── the fourth lane: evidence anchors in the task's recorded review bodies ────
def _write_body(git_repo, task_id, persona, text):
    """Record a reviewer body the way `review --body` does. Note *where*: under
    the main repo's `.rig/runs/<task_id>/`, never inside the task worktree."""
    d = git_repo / ".rig" / "runs" / task_id / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{persona}.md").write_text(text, encoding="utf-8")
    return d / f"{persona}.md"


def test_anchor_findings_become_hints_but_exit_stays_zero(git_repo):
    task_id, _wt = _new_task(git_repo)
    # f.txt holds one line, so :99 is a line that cannot exist.
    _write_body(git_repo, task_id, "security", "1. 握りつぶし（`f.txt:99`）\n")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0  # advisory: a fail-grade anchor still exits 0
    assert "hint[anchor] reviews/security.md:1 [line_out_of_range/fail]" in r.stdout


def test_a_review_body_with_no_anchors_is_hinted_not_passed_over(git_repo):
    task_id, _wt = _new_task(git_repo)
    _write_body(git_repo, task_id, "design", "判定: APPROVE\n根拠:\n1. 特に問題なし\n")
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    assert "hint[anchor] reviews/design.md:0 [no_anchors/warning]" in r.stdout
    assert "no hints" not in r.stdout


def test_no_hints_line_names_the_anchor_sensor_and_its_scope(git_repo):
    """A clean run has to say what it looked at. Claiming "no hints" while
    naming only the three diff sensors would hide that a fourth one ran."""
    task_id, _wt = _new_task(git_repo)
    r = run_cli(["stream-checks", task_id], git_repo)
    assert r.returncode == 0
    assert "no hints" in r.stdout and "evidence-anchor sensor" in r.stdout


def test_anchor_lane_never_touches_acceptance_json(git_repo):
    task_id, _wt = _new_task(git_repo)
    acc_path = git_repo / ".rig" / "runs" / task_id / "acceptance.json"
    before = acc_path.read_text(encoding="utf-8")
    _write_body(git_repo, task_id, "security", "1. （`f.txt:99`）\n")
    run_cli(["stream-checks", task_id], git_repo)
    assert acc_path.read_text(encoding="utf-8") == before


def test_watch_rescans_when_a_review_body_changes(git_repo, monkeypatch, capsys):
    """The trap the fourth sensor walks into, pinned end to end.

    Review bodies live under the MAIN repo's `.rig/runs/<task_id>/reviews/`, so
    neither `worktree_diff_text(wt, base)` nor `untracked_files(wt)` can ever
    observe one being written — a digest hashing only those two would leave the
    anchor lane permanently un-retriggerable under `--watch`. This drives the
    real loop rather than the digest function: pass 1 must have no anchor hint,
    and the hint must appear *after* a "change detected" banner, which only
    happens if the digest actually moved.
    """
    task_id, _wt = _new_task(git_repo)
    body_written = []

    def fake_sleep(_seconds):
        # Write the body during the first sleep — i.e. between two passes,
        # exactly as an implementer recording a review mid-step would.
        if not body_written:
            _write_body(git_repo, task_id, "security", "1. 握りつぶし（`f.txt:99`）\n")
            body_written.append(True)

    monkeypatch.setattr(streaming, "time", types.SimpleNamespace(sleep=fake_sleep))
    monkeypatch.chdir(git_repo)
    streaming.cmd_stream_checks(argparse.Namespace(
        task_id=task_id, watch=True, interval=0.0, max_passes=3))

    out = capsys.readouterr().out
    first_pass, banner, after = out.partition("-- change detected")
    assert banner, out  # the loop noticed at all
    assert "hint[anchor]" not in first_pass  # ...and not for a hint it printed already
    assert "hint[anchor] reviews/security.md:1 [line_out_of_range/fail]" in after


def test_watch_stays_quiet_while_the_review_bodies_hold_still(git_repo, monkeypatch, capsys):
    """The widened digest must not turn every poll into a re-print: reading the
    same bodies twice has to hash the same."""
    task_id, _wt = _new_task(git_repo)
    _write_body(git_repo, task_id, "security", "1. （`f.txt:99`）\n")
    monkeypatch.setattr(streaming, "time", types.SimpleNamespace(sleep=lambda _s: None))
    monkeypatch.chdir(git_repo)
    streaming.cmd_stream_checks(argparse.Namespace(
        task_id=task_id, watch=True, interval=0.0, max_passes=4))
    out = capsys.readouterr().out
    assert "-- change detected" not in out
    assert out.count("hint[anchor]") == 1  # the initial pass only
