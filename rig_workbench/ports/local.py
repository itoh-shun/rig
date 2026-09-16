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
from typing import Literal, overload

from .. import gitroot
from ..orchestrate import secure_fs


class ConsolePresenter:
    """`print`, exactly as `govern/cli.py` calls it."""

    def out(self, text: str = "") -> None:
        print(text)

    def err(self, text: str = "") -> None:
        print(text, file=sys.stderr)


#: The error handler a text-mode child is given when the operator named none. It is what
#: CPython resolves to under `C`, `POSIX` and the C-locale-coercion targets (`C.utf8`), and
#: it is **not** the universal default: under a generated `en_US.UTF-8` a child reports
#: `utf-8 strict`. So this preserves the handler in the first case and overrides it in the
#: second, deliberately — declaring `utf-8` alone would declare `strict` everywhere and take
#: from a C-locale child the handler that lets it print a filename which is not valid UTF-8.
#: `SubprocessRunner.run` carries the whole argument, including what the override costs.
PIPE_ERRORS = "surrogateescape"


class SubprocessRunner:
    """`subprocess.run(..., capture_output=True)`, decoded the way the call sites decode.

    `cwd` is stringified rather than passed through, matching `govern/identity.current_actor`
    (`cwd=str(root) if root else None`) and `gitroot._git`; `subprocess` accepts a `Path` either
    way, but doing it here keeps one shape for every caller.

    **Text mode is `encoding="utf-8", errors="replace"`, not a bare `text=True`.** A bare
    `text=True` decodes with `locale.getencoding()` and *strict* errors, so it does not merely
    disagree with the sites that spell the pair out (`eval/` among them) — on a
    process whose output will not decode it raises `UnicodeDecodeError` out of the call,
    where those sites get U+FFFD and carry on. `git` produces exactly that output the moment a
    repository holds a path or an author name in another encoding. With `text=False` nothing
    is decoded and `stdout`/`stderr` come back as the `bytes` the process wrote, which is what
    `eval/execution.py:43`, `eval/gate.py:45` and `eval/affected.py:412` need.

    `errors` names the handler and defaults to the one those sites spell; `eval/affected.py`
    asks for `surrogateescape` on the one read whose output is turned back into filenames.
    It is refused with `text=False` rather than dropped, because `subprocess.run` reads
    `errors=` as a request for text mode: passing it through in the bytes arm would silently
    decode a stream the caller asked for raw, and dropping it silently would let a caller
    believe a decoding it named had been applied to bytes that were never decoded.
    """

    @overload
    def run(self, argv: Sequence[str], *, cwd: str | pathlib.Path | None = ...,
            env: Mapping[str, str] | None = ..., timeout: float | None = ...,
            input: str | bytes | None = ...,
            text: Literal[True] = ..., errors: str = ...) -> subprocess.CompletedProcess[str]:
        ...

    @overload
    def run(self, argv: Sequence[str], *, cwd: str | pathlib.Path | None = ...,
            env: Mapping[str, str] | None = ..., timeout: float | None = ...,
            input: str | bytes | None = ...,
            text: Literal[False]) -> subprocess.CompletedProcess[bytes]:
        ...

    def run(self, argv: Sequence[str], *, cwd: str | pathlib.Path | None = None,
            env: Mapping[str, str] | None = None, timeout: float | None = None,
            input: str | bytes | None = None, text: bool = True, errors: str = "replace",
            ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        if not text and errors != "replace":
            raise ValueError(
                "ProcessRunner.run: errors= names a decoding and text=False does not decode. "
                "Drop one of the two; the overloads in rig_workbench/ports/__init__.py say "
                "which combinations exist."
            )
        # `encoding` implies text mode to `subprocess`, so the two kwargs are the whole of the
        # switch; passing `text=True` alongside them would add nothing and passing it in the
        # bytes arm would undo the arm. `input=None` is what `subprocess.run` sees when a
        # caller omits it, so there is no second call shape for the no-stdin case.
        decoding: dict[str, str] = {"encoding": "utf-8", "errors": errors} if text else {}
        # **Text mode tells the child what it has already decided about the pipe.** The line
        # above pins this pipe to UTF-8 in both directions; the child, if it is a Python
        # program, reads and writes it through `PYTHONIOENCODING`, which it inherits from
        # whatever console the operator happens to have. Those two can disagree, and when
        # they do it is rig's own bytes the child chokes on: with `PYTHONIOENCODING=ascii`
        # set, a provider handed a prompt containing an em dash exits 1 on
        # `sys.stdin.read()` before it has read a word, and the orchestrator records that
        # as `generator failed (exit 1)` — a verdict about the work, reached because of the
        # operator's terminal. One half of a UTF-8 pipe is not a decision anyone made.
        #
        # **The codec is pinned; the error handler is carried, never replaced.** Declaring
        # a bare `utf-8` also declares `strict`, and that reintroduces the same class of
        # failure through the other door: with nothing set, a child's stdio handler is
        # `surrogateescape` (measured: `LANG=C` and `en_US.UTF-8` both give
        # `utf-8 surrogateescape`), which is what lets it print a filename that is not
        # valid UTF-8 — `os.fsdecode(b'weird-\xff.txt')` — instead of dying on it. So the
        # handler the child would have had is kept: whatever the operator named in their
        # own `PYTHONIOENCODING`, and `surrogateescape` when they named nothing. The
        # child's *stderr* is untouched either way; CPython pins that one to
        # `backslashreplace` and `PYTHONIOENCODING` does not move it.
        #
        # One case does change, and it is named rather than hidden: a child under a
        # non-C UTF-8 locale whose handler would have resolved to `strict` now gets
        # `surrogateescape`. That is the same direction as the rest of this — a child that
        # would have died writing a lone surrogate now writes it and rig reads it through
        # `errors=` above — and it is the price of not having to run the child's own locale
        # resolution to find out what it would have picked.
        #
        # Bytes mode is deliberately left alone: there rig decodes nothing and claims
        # nothing, so it has no encoding to declare on the child's behalf either.
        #
        # This does not hide a provider that genuinely failed. Nothing about the child's
        # status, output or timeout changes; the only thing removed is rig telling it to
        # read rig's own UTF-8 as something else.
        child_env = env
        if text:
            source = os.environ if env is None else env
            # `encodingname:errorhandler`, either half optionally empty — so the handler is
            # what follows the first colon, and its absence is the operator naming none.
            handler = (source.get("PYTHONIOENCODING") or "").partition(":")[2].strip()
            child_env = dict(source, PYTHONIOENCODING=f"utf-8:{handler or PIPE_ERRORS}")
        return subprocess.run(list(argv), cwd=None if cwd is None else str(cwd),
                              env=None if child_env is None else dict(child_env), timeout=timeout,
                              input=input, capture_output=True, **decoding)


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

    def presence(self, path: pathlib.Path) -> Literal["present", "absent", "unknown"]:
        # `os.lstat` and not the pair the signer asked before this method existed
        # (`Path.is_symlink() or Path.exists()`): `exists()` follows the link, so a
        # dangling symlink comes back `False` from it, and both re-raise `EACCES` out of
        # callers whose contract is to report. One call rather than two also means one
        # answer — the pair could have a sibling change the path between its halves.
        #
        # **Absence is the allow-list; the refusal is the fall-through.** The two errnos
        # below are the ones where the lookup itself settled the question: `ENOENT` walked
        # the path and found nothing, `ENOTDIR` found a non-directory above it, so nothing
        # can be at the path either way. Everything else — `EACCES` on a directory above,
        # `ELOOP` in the prefix, `EIO`, and the `ValueError` a path with a NUL in it
        # raises — is this process failing to look, and it lands on `"unknown"` because it
        # is what the `except` clauses do not name rather than because a reader remembered
        # to route it. An errno nobody has thought of is caught by the same shape.
        try:
            os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return "absent"
        except (OSError, ValueError):
            return "unknown"
        return "present"

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
