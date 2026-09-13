"""The six ports the judgement layer is allowed to reach the world through.

`docs/v3-architecture-design-brief.ja.md` §3 counts the scatter these exist to stop: 1,073
`print` sites, 98 `subprocess`, 53 `write_text` and 17 `open(w,a)`, 68 `os.environ`, git asked
three different ways in three modules, and freshness rules that read the clock where they are
standing. Those are the AST walk in `tests/test_architecture_inventory.py` run over the tree as
it stood before the first pillar moved; run over the tree today, with `govern` and `eval`
behind these ports, the same walk counts 989 / 72 / 53 / 16 / 62 outside `ports/` itself, which
is what `BASELINE_EFFECT_SITES` freezes as a ceiling. (`write_text` has not moved at all yet:
`govern`'s six are four shell writes plus two port calls the walk counts by attribute name,
and `eval`'s one is a real `write_bytes` into a temporary tree — see that file's comment.) The discipline the brief settles on is a single
sentence — *a judgement module may import these six and nothing else that touches the outside*
— and the point of writing them down here is that the sentence becomes checkable by an import
rule instead of staying prose.

Every method below was written from a call site that exists today, and the docstring names it.
That is deliberate: a port designed from its own name grows methods nobody calls and misses the
one shape the caller actually needs. `Clock` is the worked example. `now()` alone would have
been useless to `govern/conformance.py`, which subtracts a window and compares the result
*lexicographically* against stored ISO text — a comparison that only orders correctly when both
sides carry the same offset (`tests/test_conformance_unreadable_records.py` says so in its own
header). So `Clock` also formats, because the format and the offset are one fact, and splitting
them puts half of it in a caller that has no way to know it matters.

**This module is a leaf, and more strictly than `registry/model.py`, which allows itself one
package import.** Here there are none at all, and at runtime no stdlib import either beyond
`typing`: `subprocess`, `pathlib` and `datetime` appear only under `TYPE_CHECKING`. A
judgement module that imports `ports` must not acquire `subprocess` transitively — that is
precisely the dependency the port is there to cut, and re-admitting it through the back door
would leave the import rule passing while the thing it checks for is untrue.

Protocols only. No implementations and no defaults live here; `rig_workbench.ports.local`
holds one adapter per port, each wrapping today's behaviour exactly, plus a module-level
default instance so a migrating function can take `*, clock: Clock = SYSTEM_CLOCK` and leave
its call sites untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, overload, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - annotations only; see the leaf note above
    import datetime
    import pathlib
    import subprocess
    from collections.abc import Mapping, Sequence


@runtime_checkable
class Presenter(Protocol):
    """Where a command's words go.

    Shaped by the 68 `print` calls in `rig_workbench/govern/cli.py`, which are two things and
    not more: a line to stdout (including a bare `print()` for a blank one, and a
    `json.dumps(...)` block handed over whole), and exactly one line to stderr.

    There is no `warn()`, and its absence is a decision rather than an omission. `govern/cli.py`
    prints `[WARN] ...` to **stdout** today (the unreadable-binding notice and the
    separation-of-duties notice). A `warn()` on this port would be assumed to go to stderr, and
    adopting it during migration would silently move those two lines out of the stream a caller
    is piping — a contract change disguised as a refactor. The prefix is text; the caller keeps
    passing it to `out`, and the day rig wants warnings on stderr, that is its own change with
    its own test.
    """

    def out(self, text: str = "") -> None:
        """One line to standard output. Empty text is a blank line, as `print()` is."""
        ...

    def err(self, text: str = "") -> None:
        """One line to standard error, as `govern/cli._err` does before returning its code."""
        ...


@runtime_checkable
class ProcessRunner(Protocol):
    """Running another program and reading back what it said.

    Shaped by `govern/identity.current_actor` (`git config user.name`, `cwd=`, captured text),
    `govern/cli._head` (`git rev-parse HEAD`, the same shape) and `gitroot._git`, which adds
    `env=`. Those are the sites this port must serve on day one.

    Capture is not optional, and that is the one place this port is narrower than
    `subprocess.run`. A runner that lets output through to the real stdout hands a judgement
    module a second way to speak, next to `Presenter` and outside anything a test can capture
    or a caller can redirect — the exact scatter this stage exists to end. A command whose
    output is meant for the operator is two calls: run it here, print it there.

    Text mode also declares one variable to the child, which is the single way this port
    adds to an environment rather than replacing it; `run`'s own docstring states it in full,
    and an implementation that stops reading here will not reproduce this port.

    **Text is the default; it is no longer the rule.** `text=False` exists because three sites
    in the second pillar read bytes and decoding would destroy what they read:
    `eval/affected.py:412` (`git cat-file --batch`, whose stdout is length-framed binary),
    `eval/execution.py:43` (`git diff --binary` and `ls-files -z`, hashed byte for byte into
    an execution identity) and `eval/gate.py:45` (which reads no output at all). In text mode
    the adapter decodes `encoding="utf-8", errors="replace"` rather than passing a bare
    `text=True`: bare `text=True` decodes with the locale's encoding and strict errors, which
    raises on output that will not decode, and the 25 text-mode sites in that pillar all name
    the pair themselves. The result type follows the mode, stated as two overloads —
    `CompletedProcess[str]` with `text` on, `CompletedProcess[bytes]` with it off — so a caller
    reaching for `.stdout` is told which of the two it is holding rather than handed something
    it has to narrow.

    `input=` is here because callers brought one, which was the condition for adding it:
    `eval/runner.py:305` and `:409` feed a prompt as `str`, `eval/affected.py:412` feeds object
    ids as `bytes`. Hence `str | bytes`, and hence matching it to `text` is the caller's
    business, exactly as `subprocess.run` leaves it.

    **`errors=` is a parameter for the same reason `input=` is: a caller brought one.**
    `eval/affected.py:381` decodes `git ls-tree -z` with `surrogateescape`, and it is not a
    preference. The paths it reads are used to *write files* that `_graph`'s adapter then
    re-reads through `rglob`, which spells an undecodable name with surrogates; decoding it
    as U+FFFD here would write a name that reader spells differently, and the branch's graph
    would stop matching the base branch's for that surface. That is a wrong answer, not a
    cosmetic one. The alternative was to leave one `subprocess.run` outside the port with a
    comment — which keeps the pillar's ledger line alive for a single call and puts an
    exemption where the whole point is that there are none — so the port takes the parameter
    and the default stays `"replace"`, which is what the other 24 sites spell.

    It belongs to text mode alone, and the overloads say so by omitting it from the bytes
    arm: `errors=` on a call that decodes nothing describes nothing, and an adapter that
    passed it through would be worse than useless — `subprocess.run` treats `errors=` as a
    request for text mode, so forwarding it in the bytes arm would undo the arm.

    `check=` is absent because no site uses it: `identity` wraps the call in `try/except` and
    reads `stdout`, `gitroot` reads `returncode`, and the one remaining site in `packs`
    (`sources.py`'s `_git`) does not spell `check=` at all, which is `check=False`. An
    exception-raising variant would give every one of them a second failure mode to handle.
    `shell=` is absent on purpose — the tree holds three `shell=True` calls
    (`orchestrate/providers.py:2314` and `:2787`, and `orchestrate/commands.py:248`) and none
    of them is coming through here.
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
        """Run `argv` to completion with its output captured.

        Returns what `subprocess.run(..., capture_output=True)` returns, with the decoding the
        call sites already ask for: `encoding="utf-8"` and the error handler they name when
        `text` is on, and no decoding at all when it is off. That is what today's callers
        already read (`proc.stdout.strip()`, `proc.returncode`, and `bytes` at the three sites
        above), so the migration stays a swap rather than a rewrite. `env` replaces the
        environment rather than adding to it, as `subprocess` does — **with one declared
        exception, stated here because a second implementation or a test fake written from
        this protocol has to reproduce it**: in text mode the child is given
        `PYTHONIOENCODING="utf-8:<handler>"`, and that is the whole of the exception. Text
        mode pins the pipe to UTF-8 in both directions and a Python child resolves its own
        stdio from that variable, so leaving the operator's console to answer it is how rig
        comes to hand a child UTF-8 and tell it to read ASCII — the child then dies on rig's
        own bytes and the caller reads that as the program failing. The codec is pinned and
        the error handler is carried, never replaced: whatever the operator named in their
        own `PYTHONIOENCODING`, and `local.PIPE_ERRORS` (`surrogateescape`) when they named
        none. **That default preserves a handler under some locales and replaces one under
        others, and an implementer has to be told which.** Under `C`, `POSIX` and the
        C-locale-coercion targets — the locales a bare container actually ships — CPython
        resolves an unset handler to `surrogateescape` itself, so the declaration changes
        nothing but the codec. Under a genuine UTF-8 locale it resolves to `strict`
        (measured against a generated `en_US.UTF-8`), and there the declaration overrides it
        on purpose: `strict` is what makes a child die printing a filename that is not valid
        UTF-8, and rig owns both ends of this pipe. The override is bounded — a lone
        surrogate the child re-encodes to its raw byte comes back through the `errors=`
        above as U+FFFD, so it never reaches rig's strings — and the one text-mode read that
        would want surrogates preserved (`eval/affected.py`, `git ls-tree`) has a child that
        is not a Python process and cannot see this variable at all. In bytes mode nothing
        is declared, because nothing is claimed. Callers that curate a child's environment
        keep every other key exactly:
        `workbench/adjudication.py` builds the drill judge's environment as an allowlist and
        this adds one constant to it, with one value, fixed in rig's source — pinned by
        `tests/test_drill_judge_adjudication.py` so it stays a constant. `input` is written
        to the process's stdin and has to be `str` in text mode and `bytes` out of it, again
        as `subprocess` has it. `errors` with `text=False` is a contradiction and is refused
        rather than ignored.
        """
        ...


@runtime_checkable
class FileStore(Protocol):
    """Reading and writing files, including the two files rig keeps privately.

    Shaped by the govern sites: `approval.save_approvals` and `waiver.save_waivers`
    (`mkdir(parents=True)` then `write_text`), `ledger.append` (`mkdir` then `open("a")` and one
    JSON line), `ledger._key` (`read_bytes` of `.rig/provenance.key`),
    `policy.resolve_layer_paths` (`is_dir` plus `sorted(glob("*.json"))`), and the `is_file` /
    `read_text` pairs in `identity`, `ledger`, `approval`, `waiver` and `policy`.

    **The plain writes and the private writes are different methods on purpose.**
    `orchestrate/secure_fs.py` already implements atomic, link-safe, descriptor-relative writes,
    and `write_secret_bytes` / `read_secret_bytes` / `append_secret_line` are that contract:
    caller-owned, mode 0600, in a 0700 directory, replaced by rename. `.rig/runs/<id>/approvals.json`
    is not written that way today — it is an ordinary file with ordinary permissions — so routing
    `write_text` through the same code would change the mode of every governance record and make
    every pre-existing one unwritable. One port, two guarantees, each named, is the only way to
    hold both facts without one of them quietly becoming false.
    """

    def read_text(self, path: pathlib.Path) -> str:
        """UTF-8 text. Raises as `pathlib.Path.read_text` does; callers already guard."""
        ...

    def read_bytes(self, path: pathlib.Path) -> bytes:
        """Raw bytes, as `ledger._key` reads the provenance key."""
        ...

    def write_text(self, path: pathlib.Path, text: str) -> None:
        """UTF-8 text, creating the parent directories.

        Creating parents is part of the method because it is part of all three call sites:
        every `write_text` in govern is preceded by `p.parent.mkdir(parents=True, exist_ok=True)`.
        """
        ...

    def append_line(self, path: pathlib.Path, line: str) -> None:
        """Append `line` and a newline, creating the parent directories.

        `ledger.append` is the site. It appends and does not rewrite, which is what makes the
        hash chain an append-only record rather than a file that gets rebuilt.
        """
        ...

    def is_file(self, path: pathlib.Path) -> bool:
        """Whether a readable regular file is there — the guard in front of most reads."""
        ...

    def is_dir(self, path: pathlib.Path) -> bool:
        """Whether a directory is there, as `policy.resolve_layer_paths` asks of `.rig/policy`."""
        ...

    def presence(self, path: pathlib.Path) -> Literal["present", "absent", "unknown"]:
        """Whether anything is at `path` at all — the entry itself, not what it resolves to.

        Shaped by the two commands that contradicted each other without it.
        `govern audit verify` (through `ledger.key_path_present`) and `accept`'s key loader
        (`workbench.state.load_or_create_provenance_key`) both have to tell *the key is
        gone* from *something is at the key path that is not a key*, and the two probes
        above cannot: `is_file` and `is_dir` both answer `False` to a FIFO, a device and a
        dangling symlink, so the only reading left on this side was "gone". Measured, on a
        one-entry signed ledger with a FIFO at `.rig/provenance.key`: `govern audit verify`
        printed `.rig/provenance.key is absent, so no signature could be checked (the key
        was removed, or this is a checkout that never had it)` while `accept` on the same
        repository moved that same path aside as *not a regular file*. A device and a
        dangling symlink gave the same pair.

        **The entry and not its target.** A dangling symlink is one of those shapes, and
        it is a name in a directory an operator can see and `rm`; a question that resolved
        the link would report it gone, which is the wrong half of exactly this defect.

        **Three answers, because `"unknown"` is an answer and not a failure to give one.**
        A denial on a directory above the path, a symlink loop in the prefix, an I/O
        error: each leaves this process unable to establish either reading, and both
        readings are load-bearing here — `"absent"` sends the signer to *create a key* and
        the verifier to report *the key was removed*. A `bool` has nowhere to put that, so
        a caller holding one is forced to guess, which is what `pathlib`'s `False` already
        made it do. Callers ask `!= "absent"`, so everything that is not a settled absence
        counts as presence: on this path that is the direction that neither generates a
        key over something unread nor tells an operator their key was removed.

        **And it does not raise.** `is_file` and `is_dir` above hand back `pathlib`'s own
        behaviour, which swallows `ENOENT`, `ENOTDIR`, `EBADF` and `ELOOP` and re-raises
        the rest; a bare `EACCES` out of that pair, and out of `Path.is_symlink()` beside
        it, is how `PermissionError` reached the key loader's caller. An adapter that
        raised here would put the same crash back, one call further in, so answering is
        part of the contract and not of one implementation.
        """
        ...

    def glob(self, path: pathlib.Path, pattern: str) -> list[pathlib.Path]:
        """Matching entries directly under `path`, in sorted order.

        Sorted rather than in directory order: `policy.resolve_layer_paths` already wraps its
        `glob` in `sorted()` because policy layers stack and the stack must not depend on which
        order a filesystem hands its entries back. Sorting here makes that property belong to
        the port instead of to each caller remembering it.
        """
        ...

    def mkdir(self, path: pathlib.Path) -> None:
        """Create a directory and its parents; do nothing if it is already there."""
        ...

    def write_secret_bytes(self, path: pathlib.Path, payload: bytes) -> None:
        """Atomically replace a private file: caller-owned, mode 0600, no symlink followed."""
        ...

    def read_secret_bytes(self, path: pathlib.Path) -> bytes:
        """Read a private file, refusing one whose ownership or mode has been widened."""
        ...

    def append_secret_line(self, path: pathlib.Path, line: bytes) -> None:
        """Append to a private file without ever opening an unverified inode."""
        ...


@runtime_checkable
class Env(Protocol):
    """The process environment, read but never written.

    Shaped by `identity.current_actor` (`RIG_ACTOR`, then `RIG_USER`), `ledger.append`
    (`RIG_INVOKER` with a fallback), `policy.resolve_layer_paths` (`RIG_POLICY_HOME`) and
    `gitroot.unrouted_env`, which copies the whole environment so it can drop git's routing
    variables before handing it to a subprocess.

    There is no setter. Every govern site reads; a judgement layer that could write the
    environment would be able to change the answer another judgement gets, which is the property
    this whole stage is trying to remove.

    `expanduser` is on this port and not on `FileStore`, which surprises people until they look:
    `os.path.expanduser` resolves `~` out of `HOME` (or `USERPROFILE`), so it is an environment
    read wearing a path's clothes. `policy.resolve_layer_paths` calls it twice. Leaving it out
    would let a module hold `Env` and still read the environment behind the port's back.
    """

    def get(self, name: str, default: str | None = None) -> str | None:
        """One variable, or `default` when it is unset."""
        ...

    def expanduser(self, path: str) -> str:
        """Expand a leading `~`, which is a read of `HOME`."""
        ...

    def snapshot(self) -> dict[str, str]:
        """A copy of the whole environment, as `gitroot.unrouted_env` takes before filtering it."""
        ...


@runtime_checkable
class GitRepo(Protocol):
    """The questions rig asks git.

    Four, not one. `govern/identity.current_actor` needs a single config value, but the port
    that only answered that would be the fourth place in this tree to know how to call git, and
    `gitroot.py` exists precisely because there were three. So the two questions `gitroot`
    already answers — *where does this repository keep the things it keeps once* and *which
    working tree is the caller standing in* — are on the port too, along with the tip that
    `govern/cli._head` asks for so an approval can be bound to the commit it approved.

    `cwd` is a parameter on every method rather than state on the port. rig runs from linked
    worktrees, and `_head` deliberately asks from the task's worktree while `_repo_root` asks
    from the main one; a port carrying one repository would have to be constructed twice per
    command to say the same thing.
    """

    def config_value(self, name: str, *, cwd: str | pathlib.Path | None = None) -> str | None:
        """One `git config` value, or None when it is unset or git cannot be asked.

        `identity.current_actor` reads `user.name` this way and treats every failure the same:
        empty output, a non-zero status and a missing git binary all mean "ask the next source".
        """
        ...

    def head(self, *, cwd: str | pathlib.Path | None = None) -> str | None:
        """The commit `HEAD` names, or None when there is not one to name.

        `govern/cli._head`, whose whole purpose is freshness: an approval records the tip it
        approved, and an approval whose tip has moved stops counting.
        """
        ...

    def main_worktree(self, cwd: str | pathlib.Path | None = None) -> pathlib.Path | None:
        """The repository's main checkout — where `.rig/` state lives, one set per repository."""
        ...

    def invocation_worktree(self, cwd: str | pathlib.Path | None = None) -> pathlib.Path | None:
        """The working tree the caller is standing in, which is not where state lives."""
        ...


@runtime_checkable
class Clock(Protocol):
    """What time it is, and how rig writes that down.

    Shaped by six sites that all read the clock through the local offset, never naive and never
    UTC: `ledger._now`, `approval.make_decision`, `approval._age_hours`, `waiver._today` with
    `grant`/`revoke`, `govern/cli` computing an expiry date, and `conformance._in_window`.

    `stamp` is on the clock rather than left to callers because of what `conformance` does with
    it: `now() - timedelta(days=n)`, rendered to ISO seconds, then compared with `>=` against
    the `updated_at` text stored in each task record. That comparison is lexicographic — it
    orders correctly only while both sides carry the same offset, which
    `tests/test_conformance_unreadable_records.py` had to write into its own fixtures after a
    dated record expired underneath the suite. Format and offset are therefore one decision, and
    it belongs to whoever owns the clock. Passing `when` keeps that formatting available without
    a second read of the time, which is what the window calculation needs.
    """

    def now(self) -> datetime.datetime:
        """The current moment, timezone-aware, carrying the local offset (`astimezone()`)."""
        ...

    def today(self) -> datetime.date:
        """The current local date — a waiver's expiry is a date, not a moment."""
        ...

    def stamp(self, when: datetime.datetime | None = None) -> str:
        """ISO 8601 to the second, with offset: how rig writes a time into a record.

        `when` omitted means now. Given, it formats that moment and reads no clock, which is
        what a window calculation needs.
        """
        ...
