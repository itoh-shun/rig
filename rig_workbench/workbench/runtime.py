"""Where a task's work lives, behind one seam (#461, #462).

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

**Auto-detection is bounded.** Only an environment already identified as an Orca session
is allowed to probe Orca. The probe is `orca status --json`; a binary on PATH, an exported
session variable, a zero exit, and structured create output are four different facts. Auto
falls back to native and says so when any required fact is absent. Explicit Orca refuses.

**A handle is not a path.** `create` returns a :class:`WorktreeHandle` carrying the runtime
that made it and a `ref` for identifiers only that runtime understands. Native leaves `ref`
empty, and that is the point: the field exists now so that a backend with a workspace id can
record one without a state-shape migration later. `remove` takes the handle back, so the
backend that created a worktree is the backend that disposes of it — reading a path out of
task state and calling `git worktree remove` on it would work today and be wrong the moment
something else owns the directory.

This module does not prove that Orca's checkout is a sandbox, that setup hooks completed,
or that a provider process started. It creates/removes the checkout and returns its cwd;
the existing provider layer remains responsible for processes. Nor does a successful
status probe guarantee a later mutation will succeed: every CLI call is checked separately.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import subprocess
import sys

from . import orca
from .injection import INVISIBLE_RE
from .state import default_worktree_path, git

#: The runtime rig uses when nothing says otherwise, and the only one this version
#: implements. `orca` arrives in #462; the registry is what makes that an addition rather
#: than a rewrite.
NATIVE = "native"

#: What `--runtime auto` means today. It is spelled out rather than assumed so that the
#: day a second backend exists, the resolution rule is a change to this function and not a
#: discovery about what `auto` quietly did.
AUTO = "auto"
ORCA = "orca"


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


class OrcaWorktreeBackend(WorktreeBackend):
    """An Orca-owned checkout addressed by the full id Orca returned.

    The public CLI currently has no separate branch-name flag: `--name` chooses it. Rig
    therefore records `worktree.branch` from the response rather than claiming the
    `rig/<task-id>` requested by the native interface was created.
    """

    name = ORCA

    def __init__(self) -> None:
        self.unavailable_reason = "Orca CLI has not been probed"

    def _executable(self) -> str:
        executable = shutil.which("orca")
        if not executable:
            raise RuntimeError_("Orca CLI executable 'orca' was not found on PATH")
        return executable

    def _json(self, args: list[str], root: pathlib.Path, *, timeout: int = 30) -> dict:
        command = [self._executable(), *args, "--json"]
        try:
            proc = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                  timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError_(f"Orca CLI {' '.join(args)} did not respond: {exc}") from exc
        if proc.returncode:
            detail = proc.stderr.strip() or proc.stdout.strip() or "no diagnostic"
            raise RuntimeError_(f"Orca CLI {' '.join(args)} failed with exit "
                                f"{proc.returncode}: {detail}")
        try:
            value = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError_(f"Orca CLI {' '.join(args)} did not return valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError_(f"Orca CLI {' '.join(args)} returned JSON, but not an object")
        return value

    def available(self, root: pathlib.Path) -> bool:
        try:
            status = self._json(["status"], root)
        except RuntimeError_ as exc:
            self.unavailable_reason = str(exc)
            return False
        runtime = status.get("result", {}).get("runtime")
        if (status.get("ok") is not True or not isinstance(runtime, dict)
                or runtime.get("state") != "ready" or runtime.get("reachable") is not True):
            self.unavailable_reason = (
                "Orca CLI status JSON did not report a ready, reachable runtime")
            return False
        self.unavailable_reason = ""
        return True

    @staticmethod
    def _safe(value: object) -> bool:
        return (isinstance(value, str) and bool(value)
                and not INVISIBLE_RE.search(value) and not any(c in value for c in "\n\r"))

    def create(self, root: pathlib.Path, task_id: str, base_commit: str,
               branch: str, *, agent: str | None = None,
               prompt: str | None = None) -> WorktreeHandle:
        """Create the worktree, and with `agent` start that agent's session inside it (#460).

        `--agent` and `--prompt` are the flags the Orca CLI reference documents for
        `worktree create` (launch the named agent in the worktree's first terminal, and
        hand it initial work). rig passes them through and records what Orca reports back
        — the startup terminal's handle when there is one — and invents nothing: a
        response that names no terminal leaves `orca_terminal` absent, not guessed.
        Measured against the documented CLI, not against a live Orca in this repository's
        own tests; a CLI that rejects the flags fails loudly here rather than downgrading.
        """
        args = ["worktree", "create", "--name", task_id,
                "--base-branch", base_commit, "--setup", "skip"]
        if agent:
            if not self._safe(agent) or not agent.replace("-", "").isalnum():
                raise RuntimeError_(f"agent name {agent!r} is not a plain identifier")
            args += ["--agent", agent]
            if prompt:
                args += ["--prompt", prompt]
        result = self._json(args, root, timeout=600)
        worktree = result.get("worktree")
        if not isinstance(worktree, dict):
            raise RuntimeError_("Orca worktree create returned no structured worktree object")
        identity = worktree.get("id")
        path = worktree.get("path")
        actual_branch = worktree.get("branch")
        if not self._safe(identity):
            raise RuntimeError_("Orca worktree create returned no safe stable worktree id; "
                                "Rig will not invent one from its path")
        if not self._safe(path) or not pathlib.Path(path).is_absolute():
            raise RuntimeError_("Orca worktree create returned no safe absolute worktree path")
        if not self._safe(actual_branch):
            raise RuntimeError_("Orca worktree create returned no safe branch name")
        ref = {"orca_worktree_id": identity}
        if agent:
            ref["orca_agent"] = agent
            # The handle Orca gives the agent's terminal: what `orca terminal send/read
            # --terminal <handle>` and a reconnect after restart address. Runtime-scoped
            # per Orca's own docs, so a stale one is re-listed rather than trusted.
            startup = result.get("startupTerminal")
            handle = startup.get("handle") if isinstance(startup, dict) else None
            if self._safe(handle):
                ref["orca_terminal"] = handle
        return WorktreeHandle(runtime=self.name, path=path, branch=actual_branch, ref=ref)

    def terminals(self, root: pathlib.Path, handle: WorktreeHandle) -> list[dict]:
        """The terminals Orca currently holds for this worktree, for reconnecting (#460).

        Asked of Orca every time, because terminal handles are runtime-scoped: the one
        recorded at creation may not survive an Orca restart, and the documented way back
        is to list again. Returns what Orca returned, filtered to safe strings.
        """
        identity = handle.ref.get("orca_worktree_id")
        if not self._safe(identity):
            raise RuntimeError_("Orca task state has no safe stable worktree id")
        result = self._json(["terminal", "list", "--worktree", f"id:{identity}"], root,
                            timeout=30)
        items = result.get("terminals")
        if not isinstance(items, list):
            return []
        return [{"handle": t.get("handle"), "title": t.get("title"),
                 "agent": t.get("agent")}
                for t in items if isinstance(t, dict) and self._safe(t.get("handle"))]

    def remove(self, root: pathlib.Path, handle: WorktreeHandle, *,
               strict: bool = True) -> None:
        identity = handle.ref.get("orca_worktree_id")
        if not self._safe(identity):
            if strict:
                raise RuntimeError_("Orca task state has no safe stable worktree id; refusing "
                                    "to substitute its filesystem path")
            return
        try:
            self._json(["worktree", "rm", "--worktree", f"id:{identity}", "--force"],
                       root, timeout=60)
        except RuntimeError_:
            if strict:
                raise


#: Every runtime rig can select, by name. A registry rather than an if-chain so that the
#: set of runtimes is one readable list, and so a caller can ask what exists without
#: knowing what is implemented.
BACKENDS: dict[str, WorktreeBackend] = {
    NATIVE: NativeGitWorktreeBackend(),
    ORCA: OrcaWorktreeBackend(),
}


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
        session = orca.detect()
        backend = BACKENDS[ORCA]
        if session is not None and backend.available(root):
            return backend
        reason = ("no active Orca session was detected" if session is None
                  else getattr(backend, "unavailable_reason", "Orca CLI is unusable"))
        print(f"◇ runtime auto: {reason}; falling back to native", file=sys.stderr)
        return BACKENDS[NATIVE]
    backend = BACKENDS.get(name)
    if backend is None:
        raise RuntimeError_(
            f"unknown runtime {name!r}; available: {', '.join(names())}")
    if not backend.available(root):
        detail = getattr(backend, "unavailable_reason", "runtime probe failed")
        raise RuntimeError_(
            f"runtime {name!r} is not available here: {detail}. Rig will not quietly fall back to "
            f"another one — re-run with --runtime auto if that is what you want")
    return backend


def for_task(task: dict, root: pathlib.Path) -> WorktreeBackend:
    """The backend that owns an existing task's worktree.

    Disposal goes back to whoever created it. Looking at a recorded path and calling
    `git worktree remove` on it happens to work while native is the only backend, and
    stops being true the moment something else owns the directory.

    Raises when that backend is not usable here. Callers that have to keep working anyway —
    disposal is the one that matters — should ask :func:`reconnect` instead, which answers
    the same question without making "the runtime is gone" unrepresentable.
    """
    handle = WorktreeHandle.from_task(task)
    return select(handle.runtime if handle else NATIVE, root)


#: Every way an existing task's worktree can stand in relation to the runtime that made it.
#: Named so the four are distinguishable at a glance, and so nothing has to infer them from
#: whether some call raised (#463).
READY = "ready"                              #: runtime usable, worktree present
WORKTREE_MISSING = "worktree-missing"        #: runtime usable, the directory is gone
RUNTIME_UNAVAILABLE = "runtime-unavailable"  #: the runtime that owns it cannot be used here
NO_WORKTREE = "no-worktree"                  #: the task never had one


def reconnect(task: dict, root: pathlib.Path) -> dict:
    """How an existing task's worktree stands, without raising and without changing it.

    Resuming a task means picking up a workspace somebody else's tool may own, and the
    honest answers are more than two. A caller that only learns "this raised" cannot tell a
    missing directory from an uninstalled CLI, and those need opposite responses: one is
    state loss to report, the other is a machine that simply is not set up.

    Read-only, and it never substitutes a backend. Silently answering with native because
    Orca is absent would be the implicit migration #463 forbids — it would send disposal at a
    directory rig no longer owns and report success.
    """
    handle = WorktreeHandle.from_task(task)
    if handle is None:
        return {"state": NO_WORKTREE, "runtime": None, "handle": None,
                "detail": "this task has no worktree recorded"}

    backend = BACKENDS.get(handle.runtime)
    if backend is None:
        return {"state": RUNTIME_UNAVAILABLE, "runtime": handle.runtime, "handle": handle,
                "detail": f"unknown runtime {handle.runtime!r}; this rig knows: "
                          f"{', '.join(names())}"}
    if not backend.available(root):
        return {"state": RUNTIME_UNAVAILABLE, "runtime": handle.runtime, "handle": handle,
                "detail": getattr(backend, "unavailable_reason", "runtime probe failed")}

    present = pathlib.Path(handle.path).is_dir()
    return {"state": READY if present else WORKTREE_MISSING,
            "runtime": handle.runtime, "handle": handle, "backend": backend,
            "detail": "" if present else f"no directory at {handle.path}"}
