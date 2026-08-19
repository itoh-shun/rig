"""Where a task's work lives, behind one seam (#461).

Rig creates a git worktree because isolation is a precondition for its gate, not because
git worktrees are the only thing that could hold a task. A tool that manages workspaces of
its own — Orca is the case this was extracted for (#460) — could hold one just as well, and
rig's gate would not know the difference. This module is that seam, and nothing else: it
moves the three places that create or remove a worktree behind one interface and leaves
every one of them behaving exactly as before.

Three lines it does not cross.

**A runtime is not a provider.** Which model writes the code and where the code is written
are unrelated questions, and rig already answers the first one in `orchestrate/providers.py`.
Folding them together would make "run this on Codex" and "run this in an Orca workspace"
the same kind of choice, and then neither could be made without the other. Nothing here
mentions a provider, and `providers.py` mentions no runtime; the test suite checks that
structurally rather than trusting this paragraph.

**The default path gains no dependency.** `native` is selected without asking any other
tool whether it is installed, and it is what an absent runtime resolves to. A repository
with no such tool behaves byte-identically to before this change.

**A handle is not a path.** `create` returns a :class:`WorktreeHandle` carrying the runtime
that made it and a `ref` for identifiers only that runtime understands. Native leaves `ref`
empty, and that is the point: the field exists now so that a backend with a workspace id can
record one without a state-shape migration later. `remove` takes the handle back, so the
backend that created a worktree is the backend that disposes of it — reading a path out of
task state and calling `git worktree remove` on it would work today and be wrong the moment
something else owns the directory.
"""

from __future__ import annotations

import dataclasses
import pathlib

from .state import default_worktree_path, git

#: The runtime rig uses when nothing says otherwise, and the only one this version
#: implements. `orca` arrives in #462; the registry is what makes that an addition rather
#: than a rewrite.
NATIVE = "native"

#: What `--runtime auto` means today. It is spelled out rather than assumed so that the
#: day a second backend exists, the resolution rule is a change to this function and not a
#: discovery about what `auto` quietly did.
AUTO = "auto"


class RuntimeError_(RuntimeError):
    """A runtime was asked for and cannot be used. Never downgraded silently."""


@dataclasses.dataclass(frozen=True)
class WorktreeHandle:
    """Where a task's work lives, and who is responsible for it.

    `ref` holds identifiers meaningful only to the backend that created the handle — a
    workspace id, a session token, whatever the next backend needs. Native writes nothing
    into it. Having the field from the start is what keeps adding a backend from becoming
    a migration of every task.json ever written.
    """

    runtime: str
    path: str
    branch: str | None = None
    ref: dict = dataclasses.field(default_factory=dict)

    def as_state(self) -> dict:
        return {"runtime": self.runtime, "path": self.path,
                "branch": self.branch, "ref": dict(self.ref)}

    @classmethod
    def from_task(cls, task: dict) -> "WorktreeHandle | None":
        """Rebuild a handle from task state, including state written before #461.

        A task with no runtime recorded was created by native git worktrees, because that
        is the only thing rig had. This absence means "before runtimes existed", which is
        a fact rather than an unknown — unlike the absences the assurance receipt refuses
        to fill in, where nobody measured. Reading it as native is a statement about
        history, not a guess about capability.
        """
        stored = task.get("worktree")
        legacy = task.get("worktree_path")
        if isinstance(stored, dict) and (legacy or stored.get("path")):
            # Where the two disagree, `worktree_path` wins. Both are written together and
            # cannot drift on their own, but task.json is a file an operator can edit, and
            # a handle that disagreed with it would send `remove` at a directory nothing
            # else in rig ever touched — deleting the wrong one and stranding the real one.
            # `worktree_path` is what accept's dirty check, every sensor and the receipt
            # read, so taking the path from there is what keeps disposal aimed at the
            # worktree the task was actually judged in. The block still supplies what only
            # it has: the runtime and its `ref`.
            return cls(runtime=str(stored.get("runtime") or NATIVE),
                       path=str(legacy or stored["path"]),
                       branch=task.get("branch") or stored.get("branch"),
                       ref=dict(stored.get("ref") or {}))
        if legacy:
            return cls(runtime=NATIVE, path=str(legacy), branch=task.get("branch"))
        return None


class WorktreeBackend:
    """One place a task's work can live."""

    name = "abstract"

    def available(self, root: pathlib.Path) -> bool:
        raise NotImplementedError

    def create(self, root: pathlib.Path, task_id: str, base_commit: str,
               branch: str) -> WorktreeHandle:
        raise NotImplementedError

    def remove(self, root: pathlib.Path, handle: WorktreeHandle, *,
               strict: bool = True) -> None:
        """Dispose of a worktree.

        `strict` is not a convenience. `discard` must fail loudly when it cannot remove a
        worktree — silently leaving one behind is how a repository accumulates directories
        nobody knows the origin of. A rollback after a half-finished create must not, because
        the removal failing there would replace the error the operator needs to see with a
        second one about cleanup.
        """
        raise NotImplementedError


class NativeGitWorktreeBackend(WorktreeBackend):
    """`git worktree` — what rig has always done, moved and not changed.

    Every call here is the one the lifecycle made inline before this module existed,
    including `--force` on removal and the choice to let `git` fail loudly rather than
    checking preconditions twice.
    """

    name = NATIVE

    def available(self, root: pathlib.Path) -> bool:
        # Git is not optional for rig; a repository is the precondition for everything
        # else it does. Asking would suggest there is a case where the answer is no.
        return True

    def create(self, root: pathlib.Path, task_id: str, base_commit: str,
               branch: str) -> WorktreeHandle:
        wt = default_worktree_path(root, task_id)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(["worktree", "add", "-b", branch, str(wt), base_commit], cwd=root)
        return WorktreeHandle(runtime=self.name, path=str(wt), branch=branch)

    def remove(self, root: pathlib.Path, handle: WorktreeHandle, *,
               strict: bool = True) -> None:
        wt = pathlib.Path(handle.path)
        if wt.is_dir():
            git(["worktree", "remove", "--force", str(wt)], cwd=root, check=strict)


#: Every runtime rig can select, by name. A registry rather than an if-chain so that the
#: set of runtimes is one readable list, and so a caller can ask what exists without
#: knowing what is implemented.
BACKENDS: dict[str, WorktreeBackend] = {NATIVE: NativeGitWorktreeBackend()}


def names() -> list[str]:
    return sorted(BACKENDS)


def select(name: str | None, root: pathlib.Path) -> WorktreeBackend:
    """Resolve a runtime name to its backend.

    `auto` picks the first available backend in a fixed order, which today is a list of
    one. An explicitly named runtime that is unavailable **raises rather than falling back**
    — a silent downgrade would run the task somewhere the operator did not ask for and did
    not check, which is the failure mode that makes an opt-in runtime worth having at all.
    """
    if name in (None, "", AUTO):
        for candidate in names():
            backend = BACKENDS[candidate]
            if backend.available(root):
                return backend
        raise RuntimeError_("no worktree runtime is available in this environment")
    backend = BACKENDS.get(name)
    if backend is None:
        raise RuntimeError_(
            f"unknown runtime {name!r}; available: {', '.join(names())}")
    if not backend.available(root):
        raise RuntimeError_(
            f"runtime {name!r} is not available here. Rig will not quietly fall back to "
            f"another one — re-run with --runtime auto if that is what you want")
    return backend


def for_task(task: dict, root: pathlib.Path) -> WorktreeBackend:
    """The backend that owns an existing task's worktree.

    Disposal goes back to whoever created it. Looking at a recorded path and calling
    `git worktree remove` on it happens to work while native is the only backend, and
    stops being true the moment something else owns the directory.
    """
    handle = WorktreeHandle.from_task(task)
    return select(handle.runtime if handle else NATIVE, root)
