"""Where the repository is, asked of git once and answered the same way everywhere.

Two subsystems needed this question answered and each had grown its own answer.
`workbench` asked `git rev-parse --show-toplevel`; `orchestrate` never asked at all and took
the process's initial working directory. Both were wrong in the same way once a task's work
started happening in a linked worktree: run state, locks, the queue, the audit ledger and
every installed recipe are one set per repository, and each subsystem was finding a
different one depending on which directory the operator happened to be standing in (#471).

Two questions, kept apart:

* :func:`main_worktree` — *where does this repository keep the things it keeps once*.
  Gitignored install and run state: `.rig/runs`, `.rig/queue.json`, `.rig/packs`, the locks,
  the audit log, governance.
* :func:`invocation_worktree` — *which working tree is the caller standing in*. `HEAD`, the
  current branch, the commit someone just made, and tracked files that differ per branch.

**Git's routing variables are removed before either question is asked.** `GIT_DIR`,
`GIT_WORK_TREE` and `GIT_COMMON_DIR` re-point git at a repository other than the one the
caller is standing in, and they are inherited: a shell or a hook that exported one for
another checkout would send rig's state writes, its locks and its governance ledger
somewhere the operator never chose and cannot see. Resolving from the working directory is
the answer that matches what the operator is looking at, so that is the only input.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

#: Variables that make git answer about a repository other than the one at `cwd`.
ROUTING_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")

_WORKTREE_PREFIX = "worktree "


def unrouted_env(env: dict | None = None) -> dict:
    """A copy of the environment with git's repository-routing variables removed."""
    clean = dict(os.environ if env is None else env)
    for name in ROUTING_VARS:
        clean.pop(name, None)
    return clean


def _git(args: list[str], cwd: pathlib.Path | str | None = None) -> subprocess.CompletedProcess:
    """Ask git, and treat "I could not even ask" as "no repository".

    A directory that does not exist, or that cannot be entered, is not a repository — and
    answering that with an exception makes merely *reading* a derived path fail. `monkeypatch`
    reads the current value of an attribute before replacing it, so a test that points rig at
    a temporary project it has not created yet would raise from the read rather than from
    anything it did.
    """
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              cwd=None if cwd is None else str(cwd), env=unrouted_env())
    except OSError:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")


def main_worktree(cwd: pathlib.Path | str | None = None) -> pathlib.Path | None:
    """The repository's main checkout, asked of git rather than derived.

    `git worktree list --porcelain` names the main worktree on its first line and gives the
    same answer from every working tree of the repository. Deriving it from
    `--git-common-dir`'s parent would assume `.git` sits beside the checkout root, which
    both `GIT_DIR` and `core.worktree` can make false.

    A truncated first line (`worktree ` with nothing after it) is refused rather than sliced:
    `pathlib.Path("")` is the *current* directory, so a partial read would quietly root
    everything wherever the caller happened to be standing.
    """
    proc = _git(["worktree", "list", "--porcelain"], cwd)
    if proc.returncode != 0:
        return None
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    if not first.startswith(_WORKTREE_PREFIX):
        return None
    path = pathlib.Path(first[len(_WORKTREE_PREFIX):])
    return path if str(path) and path.is_absolute() else None


def invocation_worktree(cwd: pathlib.Path | str | None = None) -> pathlib.Path | None:
    """The working tree the caller is standing in, which is not where state lives."""
    proc = _git(["rev-parse", "--show-toplevel"], cwd)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return pathlib.Path(proc.stdout.strip())
