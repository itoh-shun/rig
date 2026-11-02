"""Stop the Codex plugin's app-server brokers a rig worktree leaves behind.

The Codex plugin for Claude Code (`openai-codex/codex`) starts one detached broker per
working tree the first time Codex is asked for something there:
`scripts/app-server-broker.mjs serve --endpoint unix:<dir>/broker.sock --cwd <tree>
--pid-file <dir>/broker.pid`, with `codex app-server` and every configured MCP server
hanging under it. The only thing that ever stops one is that plugin's own `SessionEnd`
hook, and it stops the broker of the session's own cwd and no other. A Claude session in
the main checkout that had Codex work inside a rig worktree therefore leaves the broker
running when rig removes the worktree — measured on 2026-11-02 at ten such brokers, the
oldest 22 hours, with up to 60 MCP server processes under one of them, on a machine whose
memory they were the main consumer of.

Removing a worktree is rig's act, so stopping what runs in it is rig's job. This module
does it the way the plugin itself does in `handleSessionEnd`: send `broker/shutdown` over
the broker's socket, signal the process group if it is still there, and remove the state
the plugin would have removed. It never edits the plugin and never stops a broker whose
working tree is not the one being removed.

**Where the plugin keeps its state.** `lib/state.mjs` `resolveStateDir(cwd)` is
`<CLAUDE_PLUGIN_DATA>/state/<slug>-<hash>`, where `hash` is the first 16 hex digits of the
SHA-256 of the realpath of `git rev-parse --show-toplevel`, and `slug` that path's basename
with anything outside `[A-Za-z0-9._-]` folded to `-`. `broker.json` in it holds
`endpoint`, `pid`, `pidFile`, `logFile` and `sessionDir` — and no cwd, so the directory
name is the only link back to the worktree. `CLAUDE_PLUGIN_DATA` is *the Codex plugin's*
data directory, which is not the value rig sees when it runs from a Claude session (that
one is rig's), so the roots are enumerated rather than read from the environment:
`~/.claude/plugins/data/codex-*/state` and the plugin's fallback `$TMPDIR/codex-companion`.

**Two independent ways to find a broker.** The state file can be missing or rewritten, and
a broker whose cwd is a subdirectory of the worktree hashes to the same state directory but
is easier to recognise by its command line, so the process table is read as well: every
`app-server-broker.mjs serve` whose `--cwd` is the worktree or under it. The two are merged
by pid.

**Nothing is signalled on the strength of a file.** `broker.json` outlives its process, pids
are reused, and `$TMPDIR` is shared. Before a shutdown message or a signal, the pid must
still be ours (same real uid), its current command line must still be an
`app-server-broker.mjs serve` with the endpoint the state file recorded — a state file with
no endpoint vouches for nothing — and its `--cwd` must lie inside the tree being cleaned
up. State roots, state directories and state files are only read when the current user owns
them, the shared fallback root only when nobody else can write to it, and the shutdown line
only goes to a socket the current user owns.

Nothing here raises. A cleanup that cannot reach a broker writes one note and lets the
removal go ahead: refusing to remove a worktree because a side process could not be
stopped would turn a leak into a stuck task.
"""

from __future__ import annotations


import dataclasses
import hashlib
import json
import os
import pathlib
import re
import signal
import socket
import stat
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence

from .ports import Env, Presenter, ProcessRunner
from .ports.local import CONSOLE, OS_ENV, SUBPROCESS

BROKER_SCRIPT = "app-server-broker.mjs"
STATE_FILE = "broker.json"
#: `createBrokerSessionDir` makes these with `fs.mkdtempSync(<tmp>/cxc-)`. The files rig
#: removes from a session directory are only these three names, and only in a directory
#: that has this prefix, so a hand-edited `broker.json` cannot aim an unlink anywhere else.
SESSION_DIR_PREFIX = "cxc-"
SESSION_FILES = ("broker.pid", "broker.log", "broker.sock")
#: Test and operator override for where the plugin's state lives, `os.pathsep`-separated.
#: Set to an empty string to search nowhere.
STATE_ROOTS_ENV = "RIG_CODEX_STATE_ROOTS"

SHUTDOWN_TIMEOUT = 2.0
EXIT_WAIT = 5.0
POLL = 0.2


def _uid() -> int | None:
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else None


def _owned(path: pathlib.Path, *, private: bool = False) -> bool:
    """The current user owns `path` (without following a final symlink).

    `private` also refuses group- or world-writable: the plugin's fallback root sits in the
    shared temporary directory, where anybody could have made it first.
    """
    uid = _uid()
    try:
        st = path.lstat()
    except OSError:
        return False
    if uid is not None and st.st_uid != uid:
        return False
    return not (private and st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


@dataclasses.dataclass
class Broker:
    """One broker, from whichever source found it first, filled in by the other."""

    pid: int | None = None
    endpoint: str | None = None
    cwd: str | None = None
    state_file: pathlib.Path | None = None
    session_dir: str | None = None
    #: The tree this broker may be stopped for: its live `--cwd` must lie inside it.
    scope: str | None = None
    #: Why `find_orphans` picked it; empty for a broker found by worktree path.
    reason: str = ""


@dataclasses.dataclass
class Ops:
    """Every effect this module has, so a test can replace each one."""

    #: (pid, argv, cwd as the kernel reports it) for every broker-looking process.
    processes: Callable[[], list[tuple[int, list[str], str | None]]]
    cmdline: Callable[[int], list[str] | None]
    #: The real uid a process runs as, or None when it cannot be read.
    owner: Callable[[int], int | None]
    pgid: Callable[[int], int | None]
    send_shutdown: Callable[[str, float], bool]
    #: Signal what is left of the group after the leader was asked to stop.
    terminate: Callable[[int, int], None]
    sleep: Callable[[float], None]
    uid: Callable[[], int | None] = _uid


# ── naming, as the plugin computes it ─────────────────────────────────────────
def state_slug(workspace: str | os.PathLike[str]) -> str:
    """`resolveStateDir`'s slug: the basename, folded the way the plugin folds it."""
    base = os.path.basename(os.fspath(workspace).rstrip("/")) or "workspace"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-")
    return slug or "workspace"


def state_hash(workspace: str | os.PathLike[str]) -> str:
    """`resolveStateDir`'s hash: SHA-256 of the canonical path, 16 hex digits.

    The plugin canonicalises with `fs.realpathSync.native` and falls back to the path as
    given when that fails; `os.path.realpath` does the same for a path that no longer
    exists, resolving whatever prefix still does.
    """
    canonical = os.path.realpath(os.fspath(workspace))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def state_roots(env: Env = OS_ENV) -> list[pathlib.Path]:
    """The directories the plugin may have written `<slug>-<hash>/broker.json` under."""
    override = env.get(STATE_ROOTS_ENV)
    if override is not None:
        return [pathlib.Path(p) for p in override.split(os.pathsep) if p]
    config = env.get("CLAUDE_CONFIG_DIR") or env.expanduser("~/.claude")
    data = pathlib.Path(config) / "plugins" / "data"
    roots = sorted(p for p in data.glob("codex-*/state") if p.is_dir()) if data.is_dir() else []
    fallback = pathlib.Path(tempfile.gettempdir()) / "codex-companion"
    if fallback.is_dir() and _owned(fallback, private=True):
        roots.append(fallback)
    return roots


def _read_state(path: pathlib.Path) -> Broker | None:
    if not (_owned(path.parent) and _owned(path)):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    endpoint = data.get("endpoint")
    session_dir = data.get("sessionDir")
    return Broker(pid=pid if isinstance(pid, int) and pid > 0 else None,
                  endpoint=endpoint if isinstance(endpoint, str) and endpoint else None,
                  session_dir=session_dir if isinstance(session_dir, str) else None,
                  state_file=path)


def _state_files(roots: Iterable[pathlib.Path], suffix: str | None = None) -> list[Broker]:
    found = []
    for root in roots:
        if not _owned(root):
            continue
        try:
            dirs = sorted(root.iterdir())
        except OSError:
            continue
        for d in dirs:
            if suffix is not None and not d.name.endswith(suffix):
                continue
            state = d / STATE_FILE
            if state.is_file():
                broker = _read_state(state)
                if broker is not None:
                    found.append(broker)
    return found


# ── the process table ─────────────────────────────────────────────────────────
def parse_broker_argv(argv: Sequence[str]) -> dict[str, str] | None:
    """`--cwd`/`--endpoint`/`--pid-file` of a broker command line, or None if it is not one."""
    script = next((i for i, a in enumerate(argv) if a.endswith(BROKER_SCRIPT)), None)
    if script is None or "serve" not in argv[script + 1:script + 2]:
        return None
    flags: dict[str, str] = {}
    rest = list(argv[script + 2:])
    for flag, value in zip(rest, rest[1:]):
        if flag in ("--cwd", "--endpoint", "--pid-file"):
            flags[flag[2:]] = value
    return flags


def _proc_cmdline(pid: int) -> list[str] | None:
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode("utf-8", "surrogateescape") for a in raw.split(b"\0") if a]


def _ps_field(field: str, pid: int) -> str | None:
    try:
        proc = SUBPROCESS.run(["ps", "-o", f"{field}=", "-p", str(pid)], timeout=10)
    except OSError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _ps_lines(runner: ProcessRunner) -> list[tuple[int, list[str]]]:
    proc = runner.run(["ps", "-eo", "pid=,args="], timeout=10)
    rows = []
    for line in proc.stdout.splitlines() if proc.returncode == 0 else []:
        head, _, args = line.strip().partition(" ")
        if head.isdigit():
            rows.append((int(head), args.split()))
    return rows


def _local_processes(runner: ProcessRunner = SUBPROCESS
                     ) -> list[tuple[int, list[str], str | None]]:
    """Every process that looks like a broker.

    `/proc` where it exists, because it carries the argv unsplit and the cwd — including
    the `" (deleted)"` the kernel appends when the directory has been removed. `ps`
    elsewhere, which gives neither faithfully, so the cwd is reported as unknown.
    """
    proc_root = pathlib.Path("/proc")
    rows: list[tuple[int, list[str], str | None]] = []
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            argv = _proc_cmdline(int(entry.name))
            if not argv or parse_broker_argv(argv) is None:
                continue
            try:
                cwd: str | None = os.readlink(entry / "cwd")
            except OSError:
                cwd = None
            rows.append((int(entry.name), argv, cwd))
        return rows
    try:
        for pid, argv in _ps_lines(runner):
            if parse_broker_argv(argv) is not None:
                rows.append((pid, argv, None))
    except (OSError, ValueError):
        pass
    return rows


def _local_cmdline(pid: int) -> list[str] | None:
    argv = _proc_cmdline(pid)
    if argv is not None or pathlib.Path("/proc").is_dir():
        return argv
    args = _ps_field("args", pid)
    return args.split() if args else None


def _local_owner(pid: int) -> int | None:
    """The real uid, from `/proc/<pid>/status` (`Uid:` real, effective, saved, fs)."""
    try:
        for line in pathlib.Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
        return None
    except (OSError, ValueError, IndexError):
        pass
    if pathlib.Path("/proc").is_dir():
        return None
    value = _ps_field("ruid", pid)
    return int(value) if value and value.isdigit() else None


def _local_pgid(pid: int) -> int | None:
    getpgid = getattr(os, "getpgid", None)
    try:
        return getpgid(pid) if getpgid is not None else None
    except OSError:
        return None


def _send_shutdown(endpoint: str, timeout: float) -> bool:
    """`sendBrokerShutdown`: one JSON-RPC line, then wait for any reply or the close.

    Only to a socket the current user owns: an endpoint is a path out of a state file and a
    command line, and the line is not sent to whatever happens to be listening there.
    """
    if not endpoint.startswith("unix:") or not hasattr(socket, "AF_UNIX"):
        return False
    path = endpoint[len("unix:"):]
    try:
        st = os.lstat(path)
    except OSError:
        return False
    uid = _uid()
    if not stat.S_ISSOCK(st.st_mode) or (uid is not None and st.st_uid != uid):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            message = {"id": 1, "method": "broker/shutdown", "params": {}}
            sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
            sock.recv(4096)
        return True
    except OSError:
        return False


def _group_alive(pgid: int) -> bool:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        return False
    try:
        killpg(pgid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _terminate(pid: int, pgid: int) -> None:
    """`terminateProcessTree` on POSIX: the process group, else the process.

    The broker is spawned `detached`, so it leads its own group, and `codex app-server`
    and the MCP servers under it are in that group. The plugin signals the group whether
    or not the broker already exited, because those children are what hold the memory and
    a group outlives its leader while any member lives; this does the same. The group is
    never signalled when it is rig's own (rig run by Codex inside the tree it removes).
    """
    killpg = getattr(os, "killpg", None)
    getpgrp = getattr(os, "getpgrp", None)
    own_group = getpgrp is not None and getpgrp() == pgid
    if killpg is not None and pgid == pid and not own_group and _group_alive(pgid):
        try:
            killpg(pgid, signal.SIGTERM)
            return
        except OSError:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


LOCAL_OPS = Ops(processes=_local_processes, cmdline=_local_cmdline, owner=_local_owner,
                pgid=_local_pgid, send_shutdown=_send_shutdown, terminate=_terminate,
                sleep=time.sleep)


# ── stopping ──────────────────────────────────────────────────────────────────
def _within(path: str, root: str) -> bool:
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _in_scope(cwd: str, scope: str) -> bool:
    return _within(cwd, scope) or _within(os.path.realpath(cwd), os.path.realpath(scope))


def _is_ours(ops: Ops, broker: Broker) -> bool:
    """Whether `broker.pid` is, right now, the broker rig was asked to stop.

    All four must hold: the process runs as the current user, it is an
    `app-server-broker.mjs serve`, its `--endpoint` is the one recorded for it (a broker
    with no recorded endpoint is never accepted), and its `--cwd` lies inside the tree
    being cleaned up.
    """
    if broker.pid is None or not broker.endpoint or not broker.scope:
        return False
    uid = ops.uid()
    if uid is not None and ops.owner(broker.pid) != uid:
        return False
    argv = ops.cmdline(broker.pid)
    flags = parse_broker_argv(argv) if argv else None
    if flags is None or flags.get("endpoint") != broker.endpoint:
        return False
    cwd = flags.get("cwd")
    return bool(cwd) and _in_scope(cwd, broker.scope)


def _remove_state(broker: Broker) -> None:
    if broker.state_file is not None and _owned(broker.state_file):
        try:
            broker.state_file.unlink()
        except OSError:
            pass
    if broker.session_dir and os.path.basename(broker.session_dir).startswith(SESSION_DIR_PREFIX):
        session = pathlib.Path(broker.session_dir)
        if not _owned(session):
            return
        for name in SESSION_FILES:
            target = session / name
            if _owned(target):
                try:
                    target.unlink()
                except OSError:
                    pass
        try:
            session.rmdir()
        except OSError:
            pass


def stop(broker: Broker, ops: Ops = LOCAL_OPS) -> str:
    """Stop one broker and remove its state. Returns what happened, for the note.

    A broker that is not :func:`_is_ours` is neither messaged nor signalled. Its state is
    removed only when the recorded pid is no broker at all any more (gone, or reused by
    something else); a live broker that failed the check is reported and left alone, files
    and all.
    """
    if not _is_ours(ops, broker):
        argv = ops.cmdline(broker.pid) if broker.pid is not None else None
        if argv and parse_broker_argv(argv) is not None:
            return "left alone (not verifiably this tree's broker)"
        _remove_state(broker)
        return "not running"
    assert broker.pid is not None and broker.endpoint is not None
    pgid = ops.pgid(broker.pid)
    ops.send_shutdown(broker.endpoint, SHUTDOWN_TIMEOUT)
    waited = 0.0
    while _is_ours(ops, broker) and waited < EXIT_WAIT:
        ops.sleep(POLL)
        waited += POLL
    survived = _is_ours(ops, broker)
    if pgid is not None:
        ops.terminate(broker.pid, pgid)
    elif survived:
        ops.terminate(broker.pid, broker.pid)
    _remove_state(broker)
    return "terminated" if survived else "shut down"


def _merge(found: list[Broker], extra: Broker) -> None:
    for b in found:
        if (extra.pid is not None and b.pid == extra.pid) or (
                extra.endpoint is not None and b.endpoint == extra.endpoint):
            b.pid = b.pid or extra.pid
            b.endpoint = b.endpoint or extra.endpoint
            b.cwd = b.cwd or extra.cwd
            b.state_file = b.state_file or extra.state_file
            b.session_dir = b.session_dir or extra.session_dir
            b.scope = b.scope or extra.scope
            b.reason = b.reason or extra.reason
            return
    found.append(extra)


def _from_process(pid: int, argv: list[str]) -> Broker:
    flags = parse_broker_argv(argv) or {}
    pid_file = flags.get("pid-file")
    return Broker(pid=pid, endpoint=flags.get("endpoint"), cwd=flags.get("cwd"),
                  session_dir=os.path.dirname(pid_file) if pid_file else None)


def _our_processes(ops: Ops) -> list[tuple[int, list[str], str | None]]:
    uid = ops.uid()
    return [r for r in ops.processes() if uid is None or ops.owner(r[0]) == uid]


def _note(out: Presenter, broker: Broker, outcome: str) -> None:
    where = f" (cwd {broker.cwd})" if broker.cwd else ""
    reason = f" — {broker.reason}" if broker.reason else ""
    out.err(f"◇ Codex broker pid {broker.pid or '?'}{where}: {outcome}{reason}")


def brokers_for(worktree: str | os.PathLike[str], *, ops: Ops = LOCAL_OPS,
                roots: Sequence[pathlib.Path] | None = None) -> list[Broker]:
    """Every broker serving `worktree` or a directory under it."""
    path = os.fspath(worktree)
    found: list[Broker] = []
    # Matched on the hash alone: the plugin takes the slug from git's spelling of the
    # toplevel, which can differ from this path's basename when a symlink is involved,
    # while the hash is of the realpath either way.
    suffix = f"-{state_hash(path)}"
    for b in _state_files(state_roots() if roots is None else roots, suffix):
        if not b.endpoint:
            continue        # a state file with no endpoint vouches for no process
        b.cwd = path
        b.scope = path
        _merge(found, b)
    for pid, argv, _cwd in _our_processes(ops):
        b = _from_process(pid, argv)
        if b.cwd and _in_scope(b.cwd, path):
            b.scope = path
            _merge(found, b)
    _attach_state_files(found, roots)
    return found


def _attach_state_files(found: list[Broker], roots: Sequence[pathlib.Path] | None) -> None:
    """Give a broker found in the process table the state file that names its endpoint."""
    if all(b.state_file is not None for b in found):
        return
    by_endpoint = {s.endpoint: s for s in _state_files(state_roots() if roots is None else roots)
                   if s.endpoint}
    for b in found:
        state = by_endpoint.get(b.endpoint) if b.endpoint else None
        if b.state_file is None and state is not None and state.pid in (None, b.pid):
            b.state_file = state.state_file
            b.session_dir = b.session_dir or state.session_dir


def stop_for_worktree(worktree: str | os.PathLike[str] | None, *, ops: Ops = LOCAL_OPS,
                      roots: Sequence[pathlib.Path] | None = None,
                      out: Presenter = CONSOLE) -> list[Broker]:
    """Stop every broker serving `worktree`, before the worktree is removed. Never raises."""
    if not worktree:
        return []
    try:
        found = brokers_for(worktree, ops=ops, roots=roots)
        for b in found:
            _note(out, b, stop(b, ops))
        return found
    except Exception as exc:  # noqa: BLE001 - cleanup must never block the removal
        out.err(f"◇ could not stop Codex brokers for {os.fspath(worktree)}: {exc}")
        return []


# ── orphans, for gc ───────────────────────────────────────────────────────────
def _worktree_gone(cwd: str, base: str) -> bool:
    """True when nothing at or above `cwd` (up to `base`) is still a git worktree.

    Asked of the filesystem rather than of `git worktree list`, because the base can be
    shared by several repositories (`RIG_WORKTREE_ROOT`) and one repository's list says
    nothing about another's worktrees. A `.git` file whose `gitdir:` no longer exists is a
    worktree git has already pruned.
    """
    current = pathlib.Path(cwd)
    stop_at = pathlib.Path(base)
    while True:
        marker = current / ".git"
        if marker.is_dir():
            return False
        if marker.is_file():
            try:
                text = marker.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
            target = text.strip().removeprefix("gitdir:").strip()
            if not target:
                return False
            gitdir = pathlib.Path(target)
            if not gitdir.is_absolute():
                gitdir = current / gitdir
            return not gitdir.exists()
        if current == stop_at or current.parent == current:
            return True
        current = current.parent


def find_orphans(bases: Sequence[str | os.PathLike[str]], *, ops: Ops = LOCAL_OPS,
                 roots: Sequence[pathlib.Path] | None = None) -> list[Broker]:
    """Brokers whose working tree was a rig worktree under `bases` and is gone now.

    Only the current user's brokers whose `--cwd` lies under one of `bases` are considered
    at all, so a broker for any other checkout — somebody's ordinary session — is never
    touched. Of those, one is an orphan when its directory no longer exists, when the
    kernel reports its cwd as deleted (the directory was removed, perhaps recreated), or
    when the directory is still there but is no longer a worktree. Each is scoped to its
    own `--cwd`, so `stop` re-checks that it is still serving exactly that directory.
    """
    base_paths = [os.path.normpath(os.fspath(b)) for b in bases]
    found: list[Broker] = []
    for pid, argv, kernel_cwd in _our_processes(ops):
        b = _from_process(pid, argv)
        if not b.cwd:
            continue
        base = next((x for x in base_paths if _within(b.cwd, x) and
                     os.path.normpath(b.cwd) != x), None)
        if base is None:
            continue
        if kernel_cwd is not None and kernel_cwd.endswith(" (deleted)"):
            b.reason = "its working directory was deleted"
        elif not os.path.isdir(b.cwd):
            b.reason = "its working directory no longer exists"
        elif _worktree_gone(b.cwd, base):
            b.reason = "its directory is no longer a git worktree"
        else:
            continue
        b.scope = b.cwd
        _merge(found, b)
    _attach_state_files(found, roots)
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m rig_workbench.codex_broker <worktree>…`: stop each tree's brokers.

    The entry point for callers that may not import this module — `orchestrate` is a
    pillar behind the ports, and reaches it through its `ProcessRunner` instead. Always
    exits 0, for the same reason `stop_for_worktree` never raises.
    """
    import sys
    for path in (sys.argv[1:] if argv is None else argv):
        stop_for_worktree(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
