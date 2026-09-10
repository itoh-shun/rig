"""The default adapter for each of the six ports: today's behaviour, unmoved.

One class per port and one module-level instance per class, so the first migrating function can
be written as `def append(root, ..., *, clock: Clock = SYSTEM_CLOCK, files: FileStore = LOCAL_FILES)`
and every existing call site keeps working untouched. That is the whole point of the defaults:
stage 3 moves 15 effect sites in `govern/` one at a time, and each move has to be a swap that a
green tree can confirm, not a rewrite whose correctness is argued.

**These adapters wrap; they do not improve.** Where the tree already owns a behaviour, the
adapter delegates to it rather than growing a second copy:

* `LocalFileStore.write_secret_bytes` / `read_secret_bytes` / `append_secret_line` call
  `orchestrate/secure_fs.py`. Atomic replace, `O_NOFOLLOW`, descriptor-relative opens and the
  0600/0700 checks are hard to get right and already right there.
* `GitCli.main_worktree` / `invocation_worktree` call `gitroot.py`, whose docstring says why a
  second implementation that merely looks the same is how the first one stops being true.

And what they deliberately do **not** wrap:

* `LocalFileStore.write_text` and `append_line` are plain `pathlib` writes, *not* `secure_fs`.
  Governance records (`approvals.json`, `waivers.json`, `ledger.jsonl`, `.rig/org.json`) are
  ordinary files with ordinary permissions today. `secure_fs` refuses any target that is not
  caller-owned mode 0600 in a 0700 directory, so routing these through it would change the mode
  of every new record and raise on every existing one — a behaviour change wearing a refactor's
  clothes, in the one part of rig whose job is to be auditable.
* `GitCli` does not cache. `gitroot` answers from the working directory each time, and a port
  that remembered would answer the wrong repository the moment a command crossed a worktree.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import subprocess
import sys
from collections.abc import Mapping, Sequence

from .. import gitroot
from ..orchestrate import secure_fs


class ConsolePresenter:
    """`print`, exactly as `govern/cli.py` calls it."""

    def out(self, text: str = "") -> None:
        print(text)

    def err(self, text: str = "") -> None:
        print(text, file=sys.stderr)


class SubprocessRunner:
    """`subprocess.run(..., capture_output=True, text=True)`.

    `cwd` is stringified rather than passed through, matching `govern/identity.current_actor`
    (`cwd=str(root) if root else None`) and `gitroot._git`; `subprocess` accepts a `Path` either
    way, but doing it here keeps one shape for every caller.
    """

    def run(self, argv: Sequence[str], *, cwd: str | pathlib.Path | None = None,
            env: Mapping[str, str] | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(argv), cwd=None if cwd is None else str(cwd),
                              env=None if env is None else dict(env), timeout=timeout,
                              capture_output=True, text=True)


class LocalFileStore:
    """The real filesystem: `pathlib` for records, `secure_fs` for secrets."""

    def read_text(self, path: pathlib.Path) -> str:
        return pathlib.Path(path).read_text(encoding="utf-8")

    def read_bytes(self, path: pathlib.Path) -> bytes:
        return pathlib.Path(path).read_bytes()

    def write_text(self, path: pathlib.Path, text: str) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def append_line(self, path: pathlib.Path, line: str) -> None:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def is_file(self, path: pathlib.Path) -> bool:
        return pathlib.Path(path).is_file()

    def is_dir(self, path: pathlib.Path) -> bool:
        return pathlib.Path(path).is_dir()

    def glob(self, path: pathlib.Path, pattern: str) -> list[pathlib.Path]:
        return sorted(pathlib.Path(path).glob(pattern))

    def mkdir(self, path: pathlib.Path) -> None:
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)

    def write_secret_bytes(self, path: pathlib.Path, payload: bytes) -> None:
        secure_fs.atomic_write_bytes(pathlib.Path(path), payload)

    def read_secret_bytes(self, path: pathlib.Path) -> bytes:
        return secure_fs.read_bytes(pathlib.Path(path))

    def append_secret_line(self, path: pathlib.Path, line: bytes) -> None:
        secure_fs.atomic_append_line(pathlib.Path(path), line)


class OsEnv:
    """`os.environ`, read-only.

    `snapshot` copies, as `gitroot.unrouted_env` does: handing out the live mapping would let a
    caller mutate the process environment through a port that promises only to read it.
    """

    def get(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)

    def expanduser(self, path: str) -> str:
        return os.path.expanduser(path)

    def snapshot(self) -> dict[str, str]:
        return dict(os.environ)


class GitCli:
    """Git through the command line, asked the way `gitroot` asks it.

    Every method goes through `gitroot._git`, which is one call with two properties worth not
    re-deriving: git's routing variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`) are
    stripped first, and "I could not even ask" — no git binary, an unreadable directory — comes
    back as a non-zero status rather than an exception.

    **Note for the migration, because it is a behaviour change and not a swap.**
    `govern/identity.current_actor` and `govern/cli._head` call `subprocess.run` directly today
    and therefore inherit those routing variables; moving them onto this port also strips them.
    That is the fix `gitroot`'s docstring describes (#471: governance writing its ledger into
    whichever repository an inherited `GIT_DIR` pointed at), but it belongs in the commit that
    moves the call site, with its own test, and not silently inside a port swap.
    """

    def config_value(self, name: str, *, cwd: str | pathlib.Path | None = None) -> str | None:
        proc = gitroot._git(["config", name], cwd)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def head(self, *, cwd: str | pathlib.Path | None = None) -> str | None:
        proc = gitroot._git(["rev-parse", "HEAD"], cwd)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def main_worktree(self, cwd: str | pathlib.Path | None = None) -> pathlib.Path | None:
        return gitroot.main_worktree(cwd)

    def invocation_worktree(self, cwd: str | pathlib.Path | None = None) -> pathlib.Path | None:
        return gitroot.invocation_worktree(cwd)


class SystemClock:
    """The wall clock, read through the local offset — never naive, never UTC.

    `astimezone()` on a naive `now()` attaches the local offset, which is what all six govern
    sites do and what makes the ISO text they store comparable with `>=`. `today()` is
    `date.today()`'s answer, reached through the same offset so the two can never disagree
    about which day it is at 23:59.
    """

    def now(self) -> datetime.datetime:
        return datetime.datetime.now().astimezone()

    def today(self) -> datetime.date:
        return self.now().date()

    def stamp(self, when: datetime.datetime | None = None) -> str:
        return (self.now() if when is None else when).isoformat(timespec="seconds")


#: The default adapters. One instance each, so a migrating signature can name it as a default
#: argument and every existing caller keeps calling the function the way it always did.
CONSOLE = ConsolePresenter()
SUBPROCESS = SubprocessRunner()
LOCAL_FILES = LocalFileStore()
OS_ENV = OsEnv()
GIT = GitCli()
SYSTEM_CLOCK = SystemClock()
