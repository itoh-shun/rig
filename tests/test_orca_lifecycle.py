"""Accept, discard and reconnect for a task whose worktree another runtime owns (#463).

The lifecycle guarantees have to survive the runtime being something other than git, and the
hard case is not the happy one — it is the machine where the owning runtime is no longer
installed. Before this, that machine got a Python traceback out of `wb discard`, advice naming
a flag `discard` does not have, and a task that could never be cleaned up. These tests pin the
four states apart, and pin that neither disposal nor resume ever quietly substitutes a backend.
"""

import json
import pathlib
import subprocess

import pytest

from rig_workbench.workbench import runtime
from rig_workbench.workbench.runtime import WorktreeHandle


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "init")
    return root


def _orca_task(worktree: pathlib.Path) -> dict:
    return {"task_id": "t1", "branch": "rig/t1", "worktree_path": str(worktree),
            "worktree": {"runtime": "orca", "path": str(worktree), "branch": "rig/t1",
                         "ref": {"worktree_id": "w-1"}}}


class _Absent(runtime.WorktreeBackend):
    name = "orca"
    unavailable_reason = "Orca CLI executable 'orca' was not found on PATH"

    def available(self, root):
        return False


class _Present(runtime.WorktreeBackend):
    name = "orca"

    def __init__(self):
        self.removed = []

    def available(self, root):
        return True

    def remove(self, root, handle, *, strict=True):
        self.removed.append(handle.path)


# ── the four states are distinguishable ──────────────────────────────────────
def test_a_usable_runtime_with_its_worktree_present_is_ready(repo, monkeypatch):
    monkeypatch.setitem(runtime.BACKENDS, "orca", _Present())
    worktree = repo / "wt"
    worktree.mkdir()
    state = runtime.reconnect(_orca_task(worktree), repo)
    assert state["state"] == runtime.READY
    assert state["handle"].ref == {"worktree_id": "w-1"}


def test_a_usable_runtime_whose_worktree_vanished_is_not_the_same_as_a_missing_runtime(
        repo, monkeypatch):
    """A caller that only learns 'this raised' cannot tell state loss from a machine that is
    simply not set up, and those need opposite responses."""
    monkeypatch.setitem(runtime.BACKENDS, "orca", _Present())
    state = runtime.reconnect(_orca_task(repo / "gone"), repo)
    assert state["state"] == runtime.WORKTREE_MISSING
    assert "gone" in state["detail"]


def test_an_unusable_runtime_is_reported_not_raised(repo, monkeypatch):
    monkeypatch.setitem(runtime.BACKENDS, "orca", _Absent())
    worktree = repo / "wt"
    worktree.mkdir()
    state = runtime.reconnect(_orca_task(worktree), repo)
    assert state["state"] == runtime.RUNTIME_UNAVAILABLE
    assert "not found on PATH" in state["detail"]


def test_a_runtime_this_rig_has_never_heard_of_says_what_it_does_know(repo):
    task = {"worktree": {"runtime": "hypercube", "path": str(repo / "wt")}}
    state = runtime.reconnect(task, repo)
    assert state["state"] == runtime.RUNTIME_UNAVAILABLE
    assert "hypercube" in state["detail"] and "native" in state["detail"]


def test_a_task_that_never_had_a_worktree_says_so(repo):
    assert runtime.reconnect({"task_id": "t"}, repo)["state"] == runtime.NO_WORKTREE


def test_reconnect_changes_nothing(repo, monkeypatch):
    """Read-only: resuming must not create, remove or migrate anything on the way to an
    answer."""
    backend = _Present()
    monkeypatch.setitem(runtime.BACKENDS, "orca", backend)
    worktree = repo / "wt"
    worktree.mkdir()
    before = sorted(p.name for p in repo.iterdir())
    runtime.reconnect(_orca_task(worktree), repo)
    assert sorted(p.name for p in repo.iterdir()) == before
    assert backend.removed == []


def test_reconnect_never_substitutes_a_backend(repo, monkeypatch):
    """Answering 'native' because Orca is absent would send disposal at a directory rig no
    longer owns and report success — the implicit migration #463 forbids."""
    monkeypatch.setitem(runtime.BACKENDS, "orca", _Absent())
    state = runtime.reconnect(_orca_task(repo / "wt"), repo)
    assert state["runtime"] == "orca"
    assert "backend" not in state


# ── disposal goes to the owner, and stays survivable when the owner is gone ──
def _run_discard(repo, task, *extra):
    runs = repo / ".rig" / "runs" / "t1"
    runs.mkdir(parents=True, exist_ok=True)
    # `base_commit` matters: discard diffs the worktree against it to list what is about to be
    # lost, and an empty one makes git reject the revision. The Orca cases never reach that
    # line, so leaving it out would have made only the native test fail — for a reason that has
    # nothing to do with what it checks.
    (runs / "task.json").write_text(json.dumps({**task, "input": "x", "status": "in_progress",
                                                "task_type": "bugfix", "recipe": "bugfix",
                                                "base_branch": "main",
                                                "base_commit": _git(repo, "rev-parse", "HEAD")}),
                                    encoding="utf-8")
    (repo / ".rig" / "current").write_text("t1", encoding="utf-8")
    return subprocess.run(
        ["python", "-m", "rig_workbench.cli", "wb", "discard", "t1", "--yes", *extra],
        cwd=repo, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1]),
             "HOME": str(repo)})


def test_discard_refuses_rather_than_disposing_with_the_wrong_runtime(repo):
    """The regression this file exists for. Before: a traceback, advice naming a flag this
    command does not have, and a task nobody could ever clean up."""
    result = _run_discard(repo, _orca_task(repo / "wt"))
    combined = result.stdout + result.stderr

    assert "Traceback" not in combined
    assert "belongs to the 'orca' runtime" in combined
    assert "--local-cleanup" in combined
    assert json.loads((repo / ".rig/runs/t1/task.json").read_text())["status"] == "in_progress"


def test_the_explicit_escape_cleans_up_and_writes_down_that_it_did(repo):
    """A task that can never be discarded is its own failure, so there is a way out — but it
    is explicit, and the audit shows the worktree was disposed of by something other than its
    owner."""
    result = _run_discard(repo, _orca_task(repo / "wt"), "--local-cleanup")
    assert result.returncode == 0, result.stdout + result.stderr

    task = json.loads((repo / ".rig/runs/t1/task.json").read_text())
    assert task["status"] == "discarded"
    assert task["worktree"] is None
    assert "orca" in task["cleanup_note"] and "unavailable" in task["cleanup_note"]


def test_the_run_log_survives_discard(repo):
    _run_discard(repo, _orca_task(repo / "wt"), "--local-cleanup")
    assert (repo / ".rig/runs/t1/task.json").is_file()


def test_a_native_task_discards_exactly_as_before(repo):
    """The whole point of the seam: adding a runtime changed nothing for the one that was
    already there."""
    worktree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "rig/t1", str(worktree), "HEAD")
    result = _run_discard(repo, {"task_id": "t1", "branch": "rig/t1",
                                 "worktree_path": str(worktree)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not worktree.exists()
    assert "local-cleanup" not in result.stdout


def test_disposal_is_handed_to_the_backend_that_created_it(repo, monkeypatch):
    backend = _Present()
    monkeypatch.setitem(runtime.BACKENDS, "orca", backend)
    worktree = repo / "wt"
    worktree.mkdir()
    task = _orca_task(worktree)
    state = runtime.reconnect(task, repo)
    state["backend"].remove(repo, state["handle"])
    assert backend.removed == [str(worktree)]


def test_for_task_still_raises_for_callers_that_want_that(repo, monkeypatch):
    """`reconnect` exists beside `for_task`, not instead of it: a caller that cannot proceed
    without the runtime is better served by an exception than by a dict it might not read."""
    monkeypatch.setitem(runtime.BACKENDS, "orca", _Absent())
    with pytest.raises(runtime.RuntimeError_):
        runtime.for_task(_orca_task(repo / "wt"), repo)


def test_a_handle_written_before_runtimes_existed_still_reconnects(repo):
    """Absence of a runtime field means 'before runtimes existed', which is a fact about
    history rather than an unknown."""
    worktree = repo / "wt"
    worktree.mkdir()
    state = runtime.reconnect({"worktree_path": str(worktree)}, repo)
    assert state["state"] == runtime.READY and state["runtime"] == runtime.NATIVE
    assert WorktreeHandle.from_task({"worktree_path": str(worktree)}).runtime == runtime.NATIVE
