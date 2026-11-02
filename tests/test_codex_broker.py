"""Codex brokers left behind by removed rig worktrees (rig_workbench/codex_broker.py).

The Codex plugin starts one detached `app-server-broker.mjs` per working tree and only its
own `SessionEnd` hook ever stops one — for the session's cwd and no other. A rig worktree
Codex worked in therefore kept its broker, `codex app-server` and every MCP server under
it after rig removed the directory. These tests hold rig to stopping them, and to stopping
nothing else: no real process is signalled here; every effect goes through a fake `Ops`.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import threading

import pytest

from rig_workbench import codex_broker
from rig_workbench.codex_broker import Broker, Ops


def _argv(cwd: str, session: str) -> list[str]:
    return ["/usr/bin/node", "/x/codex/1.0.5/scripts/app-server-broker.mjs", "serve",
            "--endpoint", f"unix:{session}/broker.sock", "--cwd", cwd,
            "--pid-file", f"{session}/broker.pid"]


ME = 1000


class FakeOps:
    """A process table the test owns. A pid "exits" when it is removed from `alive`.

    `group_signals` records every (pid, pgid) handed to `terminate` — the plugin sweeps the
    group even after a clean exit — and `killed` the pids that were still running then.
    """

    def __init__(self, rows=(), *, exits_on_shutdown=True, owners=None):
        self.rows = list(rows)                         # (pid, argv, kernel_cwd)
        self.alive = {pid: argv for pid, argv, _ in self.rows}
        self.owners = owners or {}
        self.exits_on_shutdown = exits_on_shutdown
        self.shutdowns: list[str] = []
        self.group_signals: list[tuple[int, int]] = []
        self.killed: list[int] = []

    def ops(self) -> Ops:
        return Ops(processes=lambda: [r for r in self.rows if r[0] in self.alive],
                   cmdline=self.alive.get,
                   owner=lambda pid: self.owners.get(pid, ME) if pid in self.alive else None,
                   pgid=lambda pid: pid if pid in self.alive else None,
                   send_shutdown=self._shutdown, terminate=self._terminate,
                   sleep=lambda _s: None, uid=lambda: ME)

    @property
    def touched(self) -> bool:
        return bool(self.shutdowns or self.group_signals)

    def _shutdown(self, endpoint, _timeout):
        self.shutdowns.append(endpoint)
        if self.exits_on_shutdown:
            for pid, argv in list(self.alive.items()):
                if f"{endpoint}" in argv:
                    del self.alive[pid]
        return True

    def _terminate(self, pid, pgid):
        self.group_signals.append((pid, pgid))
        if pid in self.alive:
            self.killed.append(pid)
            del self.alive[pid]


def _session(tmp_path, name="cxc-AbC123") -> pathlib.Path:
    d = tmp_path / "tmp" / name
    d.mkdir(parents=True)
    for f in codex_broker.SESSION_FILES:
        (d / f).write_text("x", encoding="utf-8")
    return d


def _state(root: pathlib.Path, worktree: pathlib.Path, *, pid, session) -> pathlib.Path:
    d = root / f"{codex_broker.state_slug(worktree)}-{codex_broker.state_hash(worktree)}"
    d.mkdir(parents=True)
    f = d / "broker.json"
    f.write_text(json.dumps({"endpoint": f"unix:{session}/broker.sock",
                             "pidFile": f"{session}/broker.pid",
                             "logFile": f"{session}/broker.log",
                             "sessionDir": str(session), "pid": pid}), encoding="utf-8")
    return f


# ── naming, pinned to the plugin's own computation ───────────────────────────
@pytest.mark.parametrize("path, slug, digest", [
    # Generated with node from lib/state.mjs's expression (realpath falls back to the
    # path itself when it does not exist), so a drift in encoding or folding shows here.
    ("/nonexistent-rig-codex/ワーク木 x", "x", "17ae45424c180f26"),
    ("/nonexistent-rig-codex/task-1", "task-1", "bbba2759e5a32c64"),
])
def test_the_state_directory_name_is_the_one_the_plugin_computes(path, slug, digest):
    assert codex_broker.state_slug(path) == slug
    assert codex_broker.state_hash(path) == digest


def test_only_a_broker_serve_command_line_is_recognised():
    flags = codex_broker.parse_broker_argv(_argv("/w/t", "/tmp/cxc-1"))
    assert flags == {"cwd": "/w/t", "endpoint": "unix:/tmp/cxc-1/broker.sock",
                     "pid-file": "/tmp/cxc-1/broker.pid"}
    assert codex_broker.parse_broker_argv(["node", "app-server-broker.mjs", "status"]) is None
    assert codex_broker.parse_broker_argv(["codex", "app-server"]) is None


def test_the_state_roots_come_from_the_codex_plugin_not_from_the_callers_plugin_data(
        tmp_path, monkeypatch):
    """`CLAUDE_PLUGIN_DATA` in rig's environment is rig's own directory, not codex's."""
    monkeypatch.delenv(codex_broker.STATE_ROOTS_ENV, raising=False)
    config = tmp_path / "claude"
    (config / "plugins" / "data" / "codex-openai-codex" / "state").mkdir(parents=True)
    (config / "plugins" / "data" / "rig-sito-plugins" / "state").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(config / "plugins" / "data" / "rig-sito-plugins"))
    (tmp_path / "no-fallback").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "no-fallback"))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert codex_broker.state_roots() == [
        config / "plugins" / "data" / "codex-openai-codex" / "state"]


# ── stopping the brokers of one worktree ─────────────────────────────────────
def test_a_broker_in_the_state_file_is_shut_down_and_its_state_removed(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    roots = [tmp_path / "state"]
    state = _state(roots[0], wt, pid=4242, session=session)
    fake = FakeOps([(4242, _argv(str(wt), str(session)), str(wt))])
    found = codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=roots)
    assert [b.pid for b in found] == [4242]
    assert fake.shutdowns == [f"unix:{session}/broker.sock"]
    assert fake.killed == []
    assert fake.group_signals == [(4242, 4242)]   # the group is swept, as the plugin does
    assert not state.exists()
    assert not session.exists()


def test_a_broker_that_ignores_shutdown_has_its_process_group_terminated(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    fake = FakeOps([(77, _argv(str(wt), str(session)), str(wt))], exits_on_shutdown=False)
    codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=[])
    assert fake.shutdowns and fake.killed == [77]


def test_a_broker_serving_a_subdirectory_is_found_by_its_command_line(tmp_path):
    """No state file at all: the process table alone must be enough."""
    wt = tmp_path / "wt" / "task-1"
    (wt / "pkg").mkdir(parents=True)
    fake = FakeOps([(5, _argv(str(wt / "pkg"), "/tmp/cxc-z"), None)])
    found = codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=[])
    assert [b.pid for b in found] == [5]


def test_brokers_of_other_trees_are_left_alone(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    fake = FakeOps([(1, _argv(str(tmp_path / "wt" / "task-10"), "/tmp/cxc-a"), None),
                    (2, _argv(str(tmp_path / "main"), "/tmp/cxc-b"), None)])
    assert codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=[]) == []
    assert not fake.touched


def test_a_stale_pid_in_the_state_file_is_never_signalled(tmp_path):
    """`broker.json` outlives its process and pids are reused: identity is re-read."""
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    roots = [tmp_path / "state"]
    state = _state(roots[0], wt, pid=900, session=session)
    fake = FakeOps()
    fake.alive[900] = ["/usr/bin/vim", "notes.txt"]      # pid 900 is somebody else now
    found = codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=roots)
    assert [b.pid for b in found] == [900]
    assert not fake.touched
    assert not state.exists()           # the dead broker's state is still cleared


def test_a_reused_pid_serving_another_endpoint_is_not_signalled(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    roots = [tmp_path / "state"]
    _state(roots[0], wt, pid=901, session=session)
    fake = FakeOps()
    fake.alive[901] = _argv("/elsewhere", "/tmp/cxc-other")
    codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=roots)
    assert not fake.touched


def test_files_outside_a_plugin_session_directory_are_not_unlinked(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    precious = _session(tmp_path, name="not-a-session")
    roots = [tmp_path / "state"]
    _state(roots[0], wt, pid=902, session=precious)
    codex_broker.stop_for_worktree(wt, ops=FakeOps().ops(), roots=roots)
    assert all((precious / f).exists() for f in codex_broker.SESSION_FILES)


def test_without_the_codex_plugin_nothing_happens_and_nothing_is_said(tmp_path, capsys):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    assert codex_broker.stop_for_worktree(wt, ops=FakeOps().ops(),
                                          roots=[tmp_path / "absent"]) == []
    assert capsys.readouterr().err == ""


def test_a_failure_while_stopping_never_blocks_the_removal(tmp_path, capsys):
    def boom():
        raise PermissionError("no /proc for you")
    ops = Ops(processes=boom, cmdline=lambda _p: None, owner=lambda _p: None,
              pgid=lambda _p: None, send_shutdown=lambda *_: False,
              terminate=lambda *_: None, sleep=lambda _s: None)
    assert codex_broker.stop_for_worktree(tmp_path, ops=ops, roots=[]) == []
    assert "could not stop Codex brokers" in capsys.readouterr().err


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="unix sockets only")
def test_the_shutdown_message_is_the_one_the_broker_answers(tmp_path):
    """The real socket path: one JSON-RPC line, as `sendBrokerShutdown` writes it."""
    path = str(tmp_path / "b.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    received = []

    def serve():
        conn, _ = server.accept()
        with conn:
            received.append(conn.makefile().readline())
            conn.sendall(b'{"id":1,"result":{}}\n')

    t = threading.Thread(target=serve)
    t.start()
    try:
        assert codex_broker._send_shutdown(f"unix:{path}", 2.0) is True
    finally:
        t.join(5)
        server.close()
    assert json.loads(received[0]) == {"id": 1, "method": "broker/shutdown", "params": {}}
    assert codex_broker._send_shutdown(f"unix:{tmp_path / 'gone.sock'}", 0.5) is False
    assert codex_broker._send_shutdown("pipe:\\\\.\\pipe\\x", 0.5) is False


# ── orphans, for gc ──────────────────────────────────────────────────────────
def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_find_orphans_stops_only_brokers_of_rig_worktrees_that_are_gone(tmp_path):
    base = tmp_path / "rig-worktrees" / "repo"
    live = base / "live"
    (live / "src").mkdir(parents=True)
    gitdir = tmp_path / "repo" / ".git" / "worktrees" / "live"
    gitdir.mkdir(parents=True)
    (live / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    pruned = base / "pruned"
    pruned.mkdir()
    (pruned / ".git").write_text(f"gitdir: {tmp_path / 'repo/.git/worktrees/pruned'}\n",
                                 encoding="utf-8")
    plain = base / "plain"
    plain.mkdir()
    recreated = base / "recreated"
    recreated.mkdir()
    (recreated / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    rows = [
        (1, _argv(str(base / "deleted"), "/tmp/cxc-1"), str(base / "deleted") + " (deleted)"),
        (2, _argv(str(live / "src"), "/tmp/cxc-2"), str(live / "src")),
        (3, _argv(str(pruned), "/tmp/cxc-3"), str(pruned)),
        (4, _argv(str(plain), "/tmp/cxc-4"), str(plain)),
        (5, _argv(str(tmp_path / "elsewhere-gone"), "/tmp/cxc-5"), None),
        (6, _argv(str(recreated), "/tmp/cxc-6"), str(recreated) + " (deleted)"),
        (7, _argv(str(base), "/tmp/cxc-7"), str(base)),
    ]
    found = codex_broker.find_orphans([base], ops=FakeOps(rows).ops(), roots=[])
    assert sorted(b.pid for b in found) == [1, 3, 4, 6]
    assert all(b.reason for b in found)


def test_find_orphans_attaches_the_state_file_by_endpoint(tmp_path):
    base = tmp_path / "rig-worktrees" / "repo"
    base.mkdir(parents=True)
    gone = base / "task-9"
    session = _session(tmp_path, name="cxc-q")
    roots = [tmp_path / "state"]
    state = _state(roots[0], gone, pid=31, session=session)
    fake = FakeOps([(31, _argv(str(gone), str(session)), None)])
    [b] = codex_broker.find_orphans([base], ops=fake.ops(), roots=roots)
    assert b.state_file == state
    assert codex_broker.stop(b, fake.ops()) == "shut down"
    assert not state.exists()


# ── every removal path stops the brokers first ───────────────────────────────
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
def stops(monkeypatch):
    """Record each call, and whether the directory still existed at the time."""
    calls: list[tuple[str, bool]] = []

    def record(worktree, **_kw):
        calls.append((str(worktree), os.path.isdir(worktree)))
        return []
    monkeypatch.setattr(codex_broker, "stop_for_worktree", record)
    return calls


def test_the_native_backend_stops_brokers_before_removing_the_worktree(
        repo, tmp_path, monkeypatch, stops):
    from rig_workbench.workbench.runtime import NativeGitWorktreeBackend
    monkeypatch.setenv("RIG_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    backend = NativeGitWorktreeBackend()
    handle = backend.create(repo, "task-1", _git(repo, "rev-parse", "HEAD"), "rig/task-1")
    backend.remove(repo, handle)
    assert stops == [(handle.path, True)]
    assert not pathlib.Path(handle.path).exists()


def test_an_isolated_run_stops_brokers_before_removing_its_worktree(repo, monkeypatch):
    """`orchestrate` may reach only the ports, so it runs this module through its runner."""
    from rig_workbench.orchestrate import config, isolate
    from rig_workbench.ports.local import SUBPROCESS
    monkeypatch.setattr(config, "INVOCATION_CWD", repo)
    calls: list[tuple[list[str], bool]] = []

    class Recording:
        def run(self, argv, **kw):
            calls.append((list(argv), os.path.isdir(iso["dir"])))
            return SUBPROCESS.run(argv, **kw)

    iso = isolate.setup_isolation("feature")
    assert isolate.teardown_isolation(iso, "DONE", proc=Recording()) == "clean-removed"
    stop = next(i for i, (argv, _) in enumerate(calls) if "rig_workbench.codex_broker" in argv)
    remove = next(i for i, (argv, _) in enumerate(calls) if argv[-3:-1] == ["remove", "--force"])
    assert stop < remove and calls[stop][1]
    assert calls[stop][0][-1] == iso["dir"]
    # and the command really runs: a module that does not resolve would fail silently
    result = SUBPROCESS.run(calls[stop][0][:-1] + [str(repo / "nothing-here")],
                            cwd=str(pathlib.Path(codex_broker.__file__).parents[1]))
    assert result.returncode == 0, result.stderr


def test_gc_lists_orphaned_brokers_under_dry_run_and_stops_them_otherwise(
        repo, monkeypatch, capsys):
    from rig_workbench.workbench import accept
    orphan = Broker(pid=11, cwd=str(repo.parent / "rig-worktrees" / "repo" / "t1"),
                    reason="its working directory no longer exists")
    asked: list[list[pathlib.Path]] = []
    monkeypatch.setattr(codex_broker, "find_orphans",
                        lambda bases: asked.append(list(bases)) or [orphan])
    stopped: list[Broker] = []
    monkeypatch.setattr(codex_broker, "stop", lambda b: stopped.append(b) or "shut down")
    monkeypatch.delenv("RIG_WORKTREE_ROOT", raising=False)
    monkeypatch.chdir(repo)

    class Args:
        older_than = None
        dry_run = True
    accept.cmd_gc(Args())
    out = capsys.readouterr().out
    assert "[dry-run] stop Codex broker: pid 11" in out
    assert "1 Codex broker(s) (not stopped due to --dry-run)." in out
    assert stopped == []
    assert [p.resolve() for p in asked[0]] == [
        (repo.parent / "rig-worktrees" / "repo").resolve(), (repo / ".rig" / "worktrees").resolve()]

    Args.dry_run = False
    accept.cmd_gc(Args())
    assert stopped == [orphan]
    assert "1 Codex broker(s) stopped." in capsys.readouterr().out


def test_the_orca_backend_stops_brokers_before_asking_orca_to_remove(
        tmp_path, monkeypatch, stops):
    from rig_workbench.workbench.runtime import OrcaWorktreeBackend, WorktreeHandle
    backend = OrcaWorktreeBackend()
    asked: list[list[str]] = []
    monkeypatch.setattr(backend, "_json",
                        lambda args, root, timeout=30: asked.append(args) or {"ok": True})
    wt = tmp_path / "orca-wt"
    wt.mkdir()
    backend.remove(tmp_path, WorktreeHandle(runtime="orca", path=str(wt),
                                            ref={"orca_worktree_id": "w-1"}))
    assert stops == [(str(wt), True)]
    assert asked and asked[0][:2] == ["worktree", "rm"]


def test_discarding_a_task_whose_worktree_is_already_gone_still_stops_its_brokers(
        repo, monkeypatch, stops):
    """No backend runs on this path, so it is the one that would otherwise be skipped."""
    from rig_workbench.workbench import accept
    gone = repo.parent / "wt-gone"
    runs = repo / ".rig" / "runs" / "t1"
    runs.mkdir(parents=True)
    (runs / "task.json").write_text(json.dumps({
        "task_id": "t1", "branch": "rig/t1", "worktree_path": str(gone), "input": "x",
        "status": "in_progress", "task_type": "bugfix", "recipe": "bugfix",
        "base_branch": "main", "base_commit": _git(repo, "rev-parse", "HEAD")}),
        encoding="utf-8")
    monkeypatch.chdir(repo)

    class Args:
        task_id = "t1"
        yes = True
        local_cleanup = False
    accept.cmd_discard(Args())
    assert stops == [(str(gone), False)]


# ── refusals: nothing is messaged or signalled on unverified input ───────────
def test_a_broker_owned_by_another_user_is_never_touched(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    roots = [tmp_path / "state"]
    state = _state(roots[0], wt, pid=600, session=session)
    fake = FakeOps([(600, _argv(str(wt), str(session)), str(wt))], owners={600: ME + 1})
    codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=roots)
    assert not fake.touched
    assert state.exists()               # a live broker that failed the check keeps its files
    base = tmp_path / "wt"
    orphan_rows = [(601, _argv(str(base / "gone"), "/tmp/cxc-g"), None)]
    assert codex_broker.find_orphans([base], ops=FakeOps(orphan_rows, owners={601: 0}).ops(),
                                     roots=[]) == []


def test_state_owned_by_another_user_is_not_read(tmp_path, monkeypatch):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    roots = [tmp_path / "state"]
    state = _state(roots[0], wt, pid=602, session=_session(tmp_path))
    monkeypatch.setattr(codex_broker, "_uid", lambda: os.getuid() + 1)
    assert codex_broker.brokers_for(wt, ops=FakeOps().ops(), roots=roots) == []
    assert state.exists()


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
@pytest.mark.parametrize("mode, accepted", [(0o700, True), (0o777, False), (0o770, False)])
def test_the_shared_fallback_root_is_refused_when_others_can_write_it(
        tmp_path, monkeypatch, mode, accepted):
    monkeypatch.delenv(codex_broker.STATE_ROOTS_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    tmp = tmp_path / "tmp"
    fallback = tmp / "codex-companion"
    fallback.mkdir(parents=True)
    fallback.chmod(mode)
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    assert (codex_broker.state_roots() == [fallback]) is accepted


def test_a_state_file_without_an_endpoint_vouches_for_no_process(tmp_path):
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    d = tmp_path / "state" / f"task-1-{codex_broker.state_hash(wt)}"
    d.mkdir(parents=True)
    (d / "broker.json").write_text(json.dumps({"pid": 603}), encoding="utf-8")
    # pid 603 is a real broker, but of another tree: only the state file links it to `wt`.
    fake = FakeOps([(603, _argv(str(tmp_path / "other"), "/tmp/cxc-o"), None)])
    assert codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=[tmp_path / "state"]) == []
    assert not fake.touched


def test_a_broker_whose_cwd_is_outside_the_worktree_is_not_stopped(tmp_path):
    """The state file names the pid and the endpoint, but the live process serves elsewhere."""
    wt = tmp_path / "wt" / "task-1"
    wt.mkdir(parents=True)
    session = _session(tmp_path)
    roots = [tmp_path / "state"]
    _state(roots[0], wt, pid=604, session=session)
    fake = FakeOps()
    fake.alive[604] = _argv(str(tmp_path / "main-checkout"), str(session))
    [b] = codex_broker.stop_for_worktree(wt, ops=fake.ops(), roots=roots)
    assert not fake.touched
    assert codex_broker.stop(b, fake.ops()).startswith("left alone")


def test_an_orphan_that_moved_on_is_not_stopped(tmp_path):
    """gc scopes each orphan to its own --cwd and re-checks it before acting."""
    base = tmp_path / "rig-worktrees" / "repo"
    base.mkdir(parents=True)
    fake = FakeOps([(605, _argv(str(base / "gone"), "/tmp/cxc-m"), None)])
    [b] = codex_broker.find_orphans([base], ops=fake.ops(), roots=[])
    fake.alive[605] = _argv(str(tmp_path / "elsewhere"), "/tmp/cxc-m")   # pid reused
    assert codex_broker.stop(b, fake.ops()).startswith("left alone")
    assert not fake.touched


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="unix sockets only")
def test_the_shutdown_line_is_not_sent_to_a_socket_another_user_owns(tmp_path, monkeypatch):
    path = str(tmp_path / "b.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    server.settimeout(0.5)
    try:
        monkeypatch.setattr(codex_broker, "_uid", lambda: os.getuid() + 1)
        assert codex_broker._send_shutdown(f"unix:{path}", 0.5) is False
        with pytest.raises(socket.timeout):
            server.accept()
    finally:
        server.close()
    regular = tmp_path / "not-a-socket"
    regular.write_text("", encoding="utf-8")
    monkeypatch.setattr(codex_broker, "_uid", os.getuid)
    assert codex_broker._send_shutdown(f"unix:{regular}", 0.5) is False


def test_rig_never_signals_its_own_process_group(monkeypatch):
    """Codex can run rig inside the tree rig is removing; the sweep must not take rig down."""
    sent = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append(("killpg", pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(("kill", pid, sig)))
    own = os.getpgrp()
    codex_broker._terminate(own, own)
    assert all(kind == "kill" for kind, *_ in sent)
