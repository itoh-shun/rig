"""`fleet` could only show you projects you already remembered.

`orchestrate fleet --repos a,b,c` compares repositories on per-persona detection rate, and it
has always required the caller to name them. rig has been recording where it ran the whole
time — every backend mirrors each run into `~/.rig/runs.jsonl` with the project attached — and
nothing connected the two, so the command that answers "how are my projects doing" could only
answer it for the projects you could list from memory.

This is the extension rather than a second command beside it. The original `tests/test_fleet.py`
covers `--repos`; what is pinned here is discovery and the refusals around it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from rig_workbench.orchestrate import config
from rig_workbench.orchestrate.commands import cmd_fleet
from rig_workbench.workbench.run_index import known_projects


def _global_log(tmp_path: pathlib.Path, *projects: str) -> pathlib.Path:
    """A global mirror naming `projects`, oldest first."""
    path = tmp_path / "global.jsonl"
    path.write_text("".join(
        json.dumps({"run_id": f"orc-{n}", "ts": f"2026-08-29T00:00:{n:02d}+00:00",
                    "recipe": "demo", "backend": "orchestrate", "final": "DONE",
                    "project": project, "steps": []}) + "\n"
        for n, project in enumerate(projects)), encoding="utf-8")
    return path


def test_projects_are_discovered_from_the_log_newest_activity_first(tmp_path):
    """The board and the rollup should agree on where rig has been, so both read this."""
    log = _global_log(tmp_path, "/a", "/b", "/a", "/c")

    assert known_projects(path=log) == ["/c", "/a", "/b"]


def test_a_project_is_named_once_however_many_runs_it_has(tmp_path):
    """`--repos` takes a set of repositories; discovery has to produce one too, or a busy
    project would be read and reported several times over."""
    log = _global_log(tmp_path, "/a", "/a", "/a")

    assert known_projects(path=log) == ["/a"]


def test_discovery_reads_the_log_and_never_the_filesystem(tmp_path, monkeypatch):
    """The property `cmd_fleet` has always stated — no auto-discovery — is kept, not
    abandoned. Nothing is scanned and no network is touched: rig reads where it has actually
    run. A directory walk would have found repositories rig never touched, and would have
    turned a rollup of rig's own work into a survey of the disk."""
    log = _global_log(tmp_path, str(tmp_path / "recorded"))
    (tmp_path / "recorded").mkdir()
    (tmp_path / "never-ran-rig").mkdir()          # would be found by any walk of tmp_path

    assert known_projects(path=log) == [str(tmp_path / "recorded")]


def test_naming_repositories_two_ways_at_once_is_refused(tmp_path, monkeypatch, capsys):
    """Unioning them would make the report's scope depend on which flag the reader noticed
    first, and there is no resolution here that is not a guess at what was meant."""
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", _global_log(tmp_path, "/a"))

    with pytest.raises(SystemExit):
        cmd_fleet(["--repos", "/x", "--discovered"])

    assert "pass one" in capsys.readouterr().out


def test_naming_them_no_way_at_all_is_still_refused(capsys):
    """Discovery is opt-in. `--repos` says "compare these" and `--discovered` says "show me
    everywhere I have run"; a default that silently became the second would change what an
    existing invocation means."""
    with pytest.raises(SystemExit):
        cmd_fleet([])

    assert "--discovered" in capsys.readouterr().out


def test_a_machine_that_has_never_run_rig_says_so(tmp_path, monkeypatch, capsys):
    """An empty discovery is a state to report, not an empty table to render as if it were a
    measurement of nothing."""
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "absent.jsonl")

    with pytest.raises(SystemExit):
        cmd_fleet(["--discovered"])

    assert "no project has recorded a run yet" in capsys.readouterr().out


def test_the_discovered_repositories_are_the_ones_reported(tmp_path, monkeypatch, capsys):
    """End to end: discovery feeds the same rollup `--repos` feeds, rather than a parallel
    one that could drift from it."""
    for name in ("alpha", "beta"):
        (tmp_path / name / ".rig").mkdir(parents=True)
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH",
                        _global_log(tmp_path, str(tmp_path / "alpha"), str(tmp_path / "beta")))

    cmd_fleet(["--discovered", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert sorted(row["repo"] for row in report["repos"]) == sorted(
        str(tmp_path / name) for name in ("alpha", "beta"))
    assert all(row["exists"] for row in report["repos"])


def test_rig_wb_can_reach_fleet():
    """It was reachable only through `scripts/orchestrate.py` — the historical entrypoint, not
    the installed one. The same gap #544 closed for four other commands."""
    from rig_workbench.cli import _orch_delegates

    assert "fleet" in _orch_delegates
