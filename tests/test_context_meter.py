"""Metering rig's own contribution to the parent session's context.

`context-minimal` is stated 152 times in this repository and called a hard rule, and
until now nothing counted a single byte of it — the exact pair of holes rig's own
`harness-taxonomy` names (enforcement that stops at prose; a rule shipped without
measurement). These tests pin the two properties that decide whether the number can be
trusted at all:

- **it counts what was actually printed** — the wrapper is pass-through, so output
  reaches the terminal unchanged and the tally equals the bytes emitted, and
- **it never writes outside the repository it was invoked in** — the walk that finds
  `.rig/` stops at the repo boundary, because dropping an untracked file into an
  isolated task worktree changes whether `teardown_isolation` removes or preserves it.
"""

import io
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench import context_meter

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


# ── the counting wrapper ─────────────────────────────────────────────────────
def test_counting_stream_passes_output_through_unchanged():
    """Counting must never cost output. A meter that swallowed, buffered or reordered
    a line would be trading the thing rig exists to print for a statistic about it."""
    sink = io.StringIO()
    meter = context_meter._CountingStream(sink)
    meter.write("abc\n")
    meter.write("de")
    assert sink.getvalue() == "abc\nde"
    assert meter.bytes == 6
    assert meter.lines == 1


def test_counting_stream_counts_bytes_not_characters():
    """rig prints Japanese in every user-facing line. Counting characters would
    understate the real cost by ~3x on exactly the output that matters."""
    sink = io.StringIO()
    meter = context_meter._CountingStream(sink)
    meter.write("あなた待ち")
    assert meter.bytes == 15          # 5 chars, 3 bytes each in UTF-8
    assert len(sink.getvalue()) == 5


def test_counting_stream_isatty_survives_a_sink_without_one():
    meter = context_meter._CountingStream(object())
    assert meter.isatty() is False


# ── where records are allowed to land ────────────────────────────────────────
def test_data_root_finds_the_repository_that_has_rig(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".rig").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert context_meter._data_root() == tmp_path.resolve()


def test_data_root_stops_at_a_repo_boundary_and_never_climbs_out(tmp_path, monkeypatch):
    """The isolated-worktree case, which is the one that can do damage.

    A task worktree has a `.git` *file* and no `.rig/` of its own. If the walk kept
    climbing it would find the parent checkout's `.rig/` and write telemetry into it
    while cwd is the worktree — and, worse, the reverse arrangement drops an untracked
    file into a worktree whose cleanliness `teardown_isolation` uses to decide whether
    to delete or preserve it. Neither is a cosmetic mistake, so the walk stops.
    """
    outer = tmp_path / "outer"
    (outer / ".rig").mkdir(parents=True)
    (outer / ".git").mkdir()
    worktree = outer / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ../.git/worktrees/wt\n", encoding="utf-8")
    monkeypatch.chdir(worktree)
    assert context_meter._data_root() is None


def test_data_root_declines_a_repo_that_never_ran_rig(tmp_path, monkeypatch):
    """`rig-wb version` in an unrelated checkout must not create `.rig/` there."""
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    assert context_meter._data_root() is None
    assert not (tmp_path / ".rig").exists()


def test_record_writes_nothing_when_there_is_no_data_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(context_meter, "_meter", context_meter._CountingStream(io.StringIO()))
    context_meter._meter.write("x" * 100)
    context_meter._record(["some", "argv"])
    assert list(tmp_path.rglob("context.jsonl")) == []


def test_record_appends_one_json_line_per_invocation(tmp_path, monkeypatch):
    (tmp_path / ".rig").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(context_meter, "_command", "wb board")
    monkeypatch.setattr(context_meter, "_meter", context_meter._CountingStream(io.StringIO()))
    context_meter._meter.write("hello\n")
    context_meter._record(["board", "rig-20260101-101010-demo"])

    lines = (tmp_path / context_meter.CONTEXT_REL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["command"] == "wb board"
    assert record["bytes"] == 6
    assert record["task_id"] == "rig-20260101-101010-demo"


def test_task_id_hint_only_recognises_rigs_own_shape():
    """A wrong attribution is worse than none in a report whose job is to find the
    expensive task, so nothing is inferred from a flag or a bare word."""
    assert context_meter._task_id_hint(["board", "rig-20260101-101010-x"]) == "rig-20260101-101010-x"
    assert context_meter._task_id_hint(["accept", "--force"]) == ""
    assert context_meter._task_id_hint(["digest"]) == ""


# ── reading ──────────────────────────────────────────────────────────────────
def test_summarize_orders_by_bytes_not_by_call_count():
    """Forty cheap `status` calls are not the problem; one command that dumps a diff
    into the parent is. Sorting by calls would bury the thing worth fixing."""
    records = [{"command": "wb status", "bytes": 100} for _ in range(40)]
    records.append({"command": "wb diff", "bytes": 90_000, "task_id": "rig-1"})
    summary = context_meter.summarize(records)
    assert list(summary["by_command"]) == ["wb diff", "wb status"]
    assert summary["by_command"]["wb status"]["calls"] == 40
    assert summary["by_command"]["wb diff"]["max"] == 90_000
    assert summary["bytes"] == 94_000
    assert summary["by_task"] == {"rig-1": 90_000}


def test_load_skips_unparseable_lines_rather_than_failing(tmp_path):
    (tmp_path / ".rig").mkdir()
    path = tmp_path / context_meter.CONTEXT_REL
    path.write_text('{"bytes": 1, "ts": "2026-01-01T00:00:00+00:00"}\nnot json\n\n',
                    encoding="utf-8")
    assert len(context_meter.load(tmp_path)) == 1


def test_load_since_days_drops_older_records(tmp_path):
    import datetime
    (tmp_path / ".rig").mkdir()
    now = datetime.datetime.now().astimezone()
    old = (now - datetime.timedelta(days=30)).isoformat(timespec="seconds")
    fresh = now.isoformat(timespec="seconds")
    (tmp_path / context_meter.CONTEXT_REL).write_text(
        json.dumps({"ts": old, "bytes": 1}) + "\n" + json.dumps({"ts": fresh, "bytes": 2}) + "\n",
        encoding="utf-8")
    assert [r["bytes"] for r in context_meter.load(tmp_path, since_days=7)] == [2]
    assert len(context_meter.load(tmp_path)) == 2


def test_load_on_a_repo_that_has_no_records(tmp_path):
    assert context_meter.load(tmp_path) == []


@pytest.mark.parametrize("size,expected", [(0, "0B"), (512, "512B"),
                                           (2048, "2.0KB"), (3 * 1024 * 1024, "3.0MB")])
def test_human(size, expected):
    assert context_meter.human(size) == expected


def test_approx_tokens_is_labelled_and_rough():
    assert context_meter.approx_tokens(4000) == 1000


# ── end to end ───────────────────────────────────────────────────────────────
def test_a_real_invocation_records_itself(git_repo):
    """The property that makes the whole thing worth having: running a rig command
    leaves a measurement of that command behind, without being asked to."""
    (git_repo / ".rig").mkdir()
    assert run_cli(["board"], git_repo).returncode == 0

    records = context_meter.load(git_repo)
    assert [r["command"] for r in records] == ["wb board"]
    assert records[0]["bytes"] > 0

    report = run_cli(["context"], git_repo)
    assert report.returncode == 0
    assert "wb board" in report.stdout
    assert "tokens" in report.stdout


def test_the_report_states_what_it_does_not_measure(git_repo):
    """The number is only defensible because its scope is stated. "Your context usage"
    would be a fabrication — rig is a subprocess and cannot see the session."""
    (git_repo / ".rig").mkdir()
    run_cli(["board"], git_repo)
    out = run_cli(["context"], git_repo).stdout
    assert "not the session's total context" in out
    assert "rig's own output only" in out


def test_context_report_on_an_unmeasured_repo_says_so(git_repo):
    out = run_cli(["context"], git_repo)
    assert out.returncode == 0
    assert "No records yet." in out.stdout


def test_the_meter_can_be_switched_off(git_repo):
    """An escape hatch that is actually checked. A telemetry file appearing in someone
    else's repository because they ran one command is a legitimate objection."""
    (git_repo / ".rig").mkdir()
    env_run = subprocess.run([sys.executable, str(WORKBENCH), "board"],
                             capture_output=True, text=True, cwd=git_repo, timeout=60,
                             env={**dict(__import__("os").environ),
                                  "RIG_NO_CONTEXT_METER": "1"})
    assert env_run.returncode == 0
    assert not (git_repo / context_meter.CONTEXT_REL).exists()
