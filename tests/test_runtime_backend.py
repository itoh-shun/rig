"""Runtime / worktree backend abstraction (#461).

The seam exists so that something other than `git worktree` can hold a task's work without
rig's gate noticing. What these tests hold it to is narrower than that ambition: the native
backend must behave exactly as the inline code it replaced, the choice of *where* work runs
must stay independent of the choice of *which model* does it, and no path a repository
without Orca takes may gain a dependency on one.
"""

import ast
import pathlib
import subprocess

import pytest

from rig_workbench.workbench import runtime
from rig_workbench.workbench.runtime import (NATIVE, NativeGitWorktreeBackend,
                                             RuntimeError_, WorktreeHandle)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "tester")
    (root / "f.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def worktree_root(tmp_path, monkeypatch):
    d = tmp_path / "worktrees"
    monkeypatch.setenv("RIG_WORKTREE_ROOT", str(d))
    return d


# ── the native backend is the old code, moved ────────────────────────────────
def test_the_native_backend_creates_the_worktree_git_would_have(repo, worktree_root):
    head = _git(repo, "rev-parse", "HEAD")
    handle = NativeGitWorktreeBackend().create(repo, "task-1", head, "rig/task-1")
    assert pathlib.Path(handle.path).is_dir()
    assert handle.runtime == NATIVE
    assert handle.branch == "rig/task-1"
    assert "task-1" in _git(repo, "worktree", "list")
    assert _git(repo, "rev-parse", "rig/task-1") == head


def test_the_native_backend_removes_what_it_created(repo, worktree_root):
    head = _git(repo, "rev-parse", "HEAD")
    backend = NativeGitWorktreeBackend()
    handle = backend.create(repo, "task-1", head, "rig/task-1")
    backend.remove(repo, handle)
    assert not pathlib.Path(handle.path).exists()
    assert "task-1" not in _git(repo, "worktree", "list")


def test_removing_something_already_gone_is_not_an_error(repo, worktree_root):
    """`discard` runs after `accept` may already have cleaned up."""
    backend = NativeGitWorktreeBackend()
    backend.remove(repo, WorktreeHandle(runtime=NATIVE, path=str(repo / "nope")))


def test_a_rollback_removal_does_not_replace_the_error_it_is_cleaning_up_after(
        repo, worktree_root, monkeypatch):
    """`strict` is not a convenience. `discard` must fail loudly when it cannot remove a
    worktree; a rollback after a half-finished create must not, because a cleanup error
    there would bury the failure the operator actually needs to see."""
    head = _git(repo, "rev-parse", "HEAD")
    backend = NativeGitWorktreeBackend()
    handle = backend.create(repo, "task-1", head, "rig/task-1")
    # A removal git will refuse: the directory is gone but the administrative entry is not,
    # so `git worktree remove` fails rather than succeeding quietly.
    subprocess.run(["rm", "-rf", handle.path], check=True)
    pathlib.Path(handle.path).mkdir(parents=True)
    (pathlib.Path(handle.path) / "not-a-worktree").write_text("x", encoding="utf-8")
    backend.remove(repo, handle, strict=False)   # must not raise


def test_discard_fails_loudly_when_it_cannot_remove_a_worktree(repo, worktree_root):
    """The other half of `strict`, and the half a repository notices.

    A `discard` that swallows a removal failure leaves a directory behind and reports
    success, which is how a checkout accumulates worktrees nobody can account for. The
    rollback case above and this one are the same code path with opposite obligations —
    testing only the quiet one lets `check=strict` degrade to `check=False` unnoticed.
    """
    head = _git(repo, "rev-parse", "HEAD")
    backend = NativeGitWorktreeBackend()
    handle = backend.create(repo, "task-1", head, "rig/task-1")
    subprocess.run(["rm", "-rf", handle.path], check=True)
    pathlib.Path(handle.path).mkdir(parents=True)
    (pathlib.Path(handle.path) / "not-a-worktree").write_text("x", encoding="utf-8")
    # `state.git` turns a failed command into `die()`, so loudly here means the operator
    # gets git's own reason on stderr and a non-zero exit, not a swallowed return.
    with pytest.raises(SystemExit):
        backend.remove(repo, handle)


# ── selection ────────────────────────────────────────────────────────────────
def test_the_default_is_native_and_asks_nothing_else_whether_it_is_installed(repo):
    """A repository with no other runtime installed behaves exactly as before."""
    assert runtime.select(None, repo).name == NATIVE
    assert runtime.select("auto", repo).name == NATIVE
    assert runtime.select("native", repo).name == NATIVE
    # An empty string is what an unset config key deserialises to, and it means the same
    # thing as an absent one — not an unknown runtime named "".
    assert runtime.select("", repo).name == NATIVE


def test_an_unknown_runtime_is_refused_and_names_what_exists(repo):
    with pytest.raises(RuntimeError_) as exc:
        runtime.select("orca", repo)
    assert "native" in str(exc.value)


def test_a_named_runtime_that_is_unavailable_never_falls_back(repo, monkeypatch):
    """A silent downgrade would run the task somewhere the operator did not ask for and
    did not check — which is the whole reason an opt-in runtime is worth having."""

    class Unavailable(runtime.WorktreeBackend):
        name = "elsewhere"

        def available(self, root):
            return False

    monkeypatch.setitem(runtime.BACKENDS, "elsewhere", Unavailable())
    with pytest.raises(RuntimeError_) as exc:
        runtime.select("elsewhere", repo)
    assert "fall back" in str(exc.value)
    # …and `auto` still finds native rather than the unavailable one.
    assert runtime.select("auto", repo).name == NATIVE


# ── the handle carries what the next backend will need ───────────────────────
def test_the_handle_round_trips_through_task_state(repo, worktree_root):
    handle = WorktreeHandle(runtime="elsewhere", path="/tmp/x", branch="b",
                            ref={"workspace": "w-1"})
    restored = WorktreeHandle.from_task({"worktree": handle.as_state()})
    assert restored == handle
    assert restored.ref["workspace"] == "w-1"


def test_a_task_recorded_before_this_change_reads_as_native(repo):
    """The absence means "written before runtimes existed", which is a fact about history
    — unlike the absences the assurance receipt refuses to fill in, where nobody measured.
    """
    handle = WorktreeHandle.from_task({"worktree_path": "/tmp/old", "branch": "rig/old"})
    assert handle.runtime == NATIVE
    assert handle.path == "/tmp/old"
    # The branch comes back too. Native never reads it off the handle, which is exactly
    # why it is worth asserting: a backend that needs the branch would be the first to
    # find out it had been dropped, and by then the state it was dropped from is gone.
    assert handle.branch == "rig/old"
    assert runtime.for_task({"worktree_path": "/tmp/old"}, repo).name == NATIVE


def test_a_worktree_recorded_without_a_runtime_reads_as_native(repo):
    """Task state is a file an operator can open and edit, so a handle missing the field
    the code always writes is a shape that reaches this function. Native is the reading
    that keeps `discard` working on it; refusing would strand the worktree instead."""
    handle = WorktreeHandle.from_task({"worktree": {"path": "/tmp/x"}})
    assert handle.runtime == NATIVE
    assert runtime.for_task({"worktree": {"path": "/tmp/x"}}, repo).name == NATIVE


def test_a_disagreement_between_the_two_recorded_paths_is_settled_by_the_older_one(repo):
    """Both fields are written in one place and cannot drift on their own — but task.json
    is a file an operator can open, and the two failure modes here are not symmetric.
    Deferring to `worktree` would aim `remove` at a directory nothing else in rig touched:
    the wrong one is deleted and the real one is left behind. Deferring to `worktree_path`
    aims it at the worktree accept, the sensors and the receipt all operated on."""
    handle = WorktreeHandle.from_task({
        "worktree_path": "/tmp/real", "branch": "rig/real",
        "worktree": {"runtime": "elsewhere", "path": "/tmp/stale", "branch": "rig/stale",
                     "ref": {"workspace": "w-1"}},
    })
    assert handle.path == "/tmp/real"
    assert handle.branch == "rig/real"
    # …and the block is still believed about what only it knows.
    assert handle.runtime == "elsewhere"
    assert handle.ref == {"workspace": "w-1"}


def test_a_task_with_no_worktree_has_no_handle(repo):
    assert WorktreeHandle.from_task({}) is None
    assert WorktreeHandle.from_task({"worktree": {}}) is None


def test_disposal_is_routed_to_the_backend_that_created_it(repo, monkeypatch):
    """Reading a path out of task state and calling `git worktree remove` on it happens to
    work while native is the only backend, and stops being true the moment another runtime
    owns the directory."""
    taken = []

    class Elsewhere(runtime.WorktreeBackend):
        name = "elsewhere"

        def available(self, root):
            return True

        def remove(self, root, handle, *, strict=True):
            taken.append(handle.path)

    monkeypatch.setitem(runtime.BACKENDS, "elsewhere", Elsewhere())
    task = {"worktree": {"runtime": "elsewhere", "path": "/tmp/x", "branch": "b",
                         "ref": {"workspace": "w-1"}}}
    backend = runtime.for_task(task, repo)
    assert backend.name == "elsewhere"
    backend.remove(repo, WorktreeHandle.from_task(task))
    assert taken == ["/tmp/x"]


# ── runtime is not provider ──────────────────────────────────────────────────
_PROVIDERS = ("claude", "codex", "ollama", "lmstudio", "mock")


def _code_identifiers(module) -> set[str]:
    """Every name, attribute and imported symbol the module's *code* mentions.

    Scanned as an AST rather than as text, so a comment or a docstring naming another
    runtime — this module's own docstring names both `providers.py` and Orca, on purpose —
    does not read as a dependency on one. What the test is about is what the code reaches
    for, and prose reaches for nothing.
    """
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
            found.update(a.name for a in node.names)
    return found


def test_choosing_where_work_runs_never_consults_which_model_runs_it():
    """Structural, not aspirational. Folded together, "run this on Codex" and "run this in
    another workspace" become the same choice, and then neither can be made without the
    other."""
    identifiers = _code_identifiers(runtime)
    assert [p for p in _PROVIDERS if p in identifiers] == []


def test_the_provider_layer_knows_nothing_about_runtimes():
    from rig_workbench.orchestrate import providers

    source = pathlib.Path(providers.__file__).read_text(encoding="utf-8")
    for token in ("WorktreeBackend", "workbench.runtime", "from .runtime", "runtime_mod"):
        assert token not in source


def test_the_default_path_gains_no_dependency_on_another_runtime():
    """`native` must be reachable without importing, invoking or probing anything else.

    Checked against what the code imports and calls, not against the words in the file:
    the module deliberately explains in prose which runtime it was extracted for, and
    saying so is not the same as reaching for it.
    """
    identifiers = _code_identifiers(runtime)
    for token in ("orca", "Orca", "subprocess", "shutil", "which", "run"):
        assert token not in identifiers, token
