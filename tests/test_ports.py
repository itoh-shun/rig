"""The six ports, and the one claim a default adapter makes.

`rig_workbench/ports/local.py` exists so that stage 3 can move an effect site by adding a
keyword argument and nothing else. That is only safe while each adapter returns exactly what
the raw call it replaces returns, so every test below compares the adapter against **the real
thing** — `subprocess.run` itself, a `pathlib` write, `secure_fs`, `gitroot`, and where possible
the govern function that does it today (`ledger.append`, `approval.save_approvals`,
`identity.current_actor`, `govern.cli._head`, `waiver._today`, `approval._age_hours`). A test
that restated the adapter's own body would pass for as long as the adapter was wrong.

`ports/__init__.py` is also asserted to be a leaf structurally rather than by convention: the
discipline the design brief settles on is "the judgement layer imports these six and nothing
else that touches the outside", and a port module that reached back into the package would make
that sentence unenforceable from the first day.
"""

import ast
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

from rig_workbench import gitroot
from rig_workbench.govern import approval, identity, ledger, waiver
from rig_workbench.govern import cli as govern_cli
from rig_workbench.orchestrate import secure_fs
from rig_workbench.ports import Clock, Env, FileStore, GitRepo, Presenter, ProcessRunner
from rig_workbench.ports import local as ports_local
from rig_workbench.ports.local import (CONSOLE, GIT, LOCAL_FILES, OS_ENV, SUBPROCESS,
                                       SYSTEM_CLOCK)

PORTS_INIT = pathlib.Path(ports_local.__file__).parent / "__init__.py"
PORT_NAMES = ("Presenter", "ProcessRunner", "FileStore", "Env", "GitRepo", "Clock")


# ── the leaf property, asserted structurally ─────────────────────────────────
def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    """Every imported module name in `tree`, with its relative-import level."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


def test_ports_init_imports_nothing_from_rig_workbench():
    """The one rule that makes the import discipline checkable instead of aspirational."""
    found = _imports(ast.parse(PORTS_INIT.read_text(encoding="utf-8")))
    assert found, "the module should still be importing typing"
    assert not [m for m, _ in found if m.split(".")[0] == "rig_workbench"]
    # A relative import is a `rig_workbench` import with the package name left off.
    assert not [m for m, level in found if level]


def test_ports_init_pulls_in_no_effect_module_at_runtime():
    """`subprocess` under TYPE_CHECKING only: importing the port must not import the thing.

    A judgement module that imports `ports` and thereby acquires `subprocess` has cut nothing —
    the dependency the port exists to remove would arrive through the port itself.
    """
    tree = ast.parse(PORTS_INIT.read_text(encoding="utf-8"))
    runtime = [node for node in tree.body
               if not (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                       and node.test.id == "TYPE_CHECKING")]
    names = {m for m, _ in _imports(ast.Module(body=runtime, type_ignores=[]))}
    assert names == {"__future__", "typing"}


def test_ports_init_declares_the_six_and_nothing_else():
    """No implementations and no defaults live beside the protocols."""
    tree = ast.parse(PORTS_INIT.read_text(encoding="utf-8"))
    assert tuple(n.name for n in tree.body if isinstance(n, ast.ClassDef)) == PORT_NAMES
    assert not [n for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign,
                                                      ast.FunctionDef))]


@pytest.mark.parametrize("adapter,port", [
    (CONSOLE, Presenter), (SUBPROCESS, ProcessRunner), (LOCAL_FILES, FileStore),
    (OS_ENV, Env), (GIT, GitRepo), (SYSTEM_CLOCK, Clock)])
def test_each_default_instance_satisfies_its_port(adapter, port):
    assert isinstance(adapter, port)


# ── Presenter vs print ───────────────────────────────────────────────────────
def test_presenter_writes_what_print_writes(capsys):
    block = json.dumps({"a": 1, "b": [2, 3]}, ensure_ascii=False, indent=2)
    for text in ("## rig govern policy", "", block, "  ✓ alice (dev) 2026-01-01T00:00:00+00:00"):
        CONSOLE.out(text)
        got = capsys.readouterr()
        print(text)
        raw = capsys.readouterr()
        assert (got.out, got.err) == (raw.out, raw.err)


def test_presenter_error_goes_where_govern_cli_sends_it(capsys):
    """`govern/cli._err` is `print(..., file=sys.stderr)`; nothing else in govern uses stderr."""
    CONSOLE.err("[ERROR] no policy layer found")
    got = capsys.readouterr()
    print("[ERROR] no policy layer found", file=sys.stderr)
    raw = capsys.readouterr()
    assert (got.out, got.err) == (raw.out, raw.err) == ("", "[ERROR] no policy layer found\n")


# ── ProcessRunner vs subprocess.run ──────────────────────────────────────────
_SPEAK = ("import os, sys; sys.stdout.write(os.getcwd() + '|' + str(os.environ.get('RIG_PORT_T')));"
          " sys.stderr.write('grumble'); sys.exit(3)")


def _raw(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def test_runner_returns_what_subprocess_run_returns():
    argv = [sys.executable, "-c", _SPEAK]
    got = SUBPROCESS.run(argv)
    raw = _raw(argv)
    assert (got.returncode, got.stdout, got.stderr) == (raw.returncode, raw.stdout, raw.stderr)
    assert got.returncode == 3 and isinstance(got.stdout, str)


def test_runner_passes_cwd_and_env_through(tmp_path):
    argv = [sys.executable, "-c", _SPEAK]
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    env = {**os.environ, "RIG_PORT_T": "seen"}
    got = SUBPROCESS.run(argv, cwd=workdir, env=env)
    raw = _raw(argv, cwd=str(workdir), env=env)
    assert got.stdout == raw.stdout
    assert got.stdout.endswith("|seen") and str(workdir.resolve()) in got.stdout
    # A Path and its string spell the same directory, which is what the call sites pass.
    assert SUBPROCESS.run(argv, cwd=str(workdir), env=env).stdout == got.stdout


def test_runner_times_out_the_way_the_raw_call_does():
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]
    with pytest.raises(subprocess.TimeoutExpired):
        SUBPROCESS.run(argv, timeout=0.3)
    with pytest.raises(subprocess.TimeoutExpired):
        _raw(argv, timeout=0.3)


# ── FileStore vs pathlib, and vs the govern writes it replaces ───────────────
def test_write_text_matches_the_write_approval_does_today(tmp_path):
    """`save_approvals`: mkdir parents, then `write_text` of indented JSON plus a newline."""
    data = {"task_id": "t-1", "decisions": [{"actor": "alice", "decision": "approve"}]}
    real = tmp_path / "real"
    approval.save_approvals(real, "t-1", data)

    mine = tmp_path / "mine"
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    LOCAL_FILES.write_text(approval.approvals_path(mine, "t-1"), payload)

    assert (approval.approvals_path(mine, "t-1").read_bytes()
            == approval.approvals_path(real, "t-1").read_bytes())


def test_append_line_matches_the_append_the_ledger_does_today(tmp_path):
    """`ledger.append`: `open("a")` and one JSON line, so the hash chain only ever grows."""
    real, mine = tmp_path / "real", tmp_path / "mine"
    entries = [ledger.append(real, "accept", actor="alice", subject=f"t-{i}") for i in range(3)]
    for entry in entries:
        LOCAL_FILES.append_line(ledger.ledger_path(mine),
                                json.dumps(entry, ensure_ascii=False, sort_keys=True))
    assert ledger.ledger_path(mine).read_bytes() == ledger.ledger_path(real).read_bytes()
    assert ledger.verify(mine).ok and ledger.read_ledger(mine) == ledger.read_ledger(real)


def test_reads_and_probes_match_pathlib(tmp_path):
    p = tmp_path / "nested" / "waivers.json"
    waiver.save_waivers(tmp_path, [{"id": "w-1", "criteria": ["tests_pass"]}])
    real = waiver.waivers_path(tmp_path)
    assert LOCAL_FILES.read_text(real) == real.read_text(encoding="utf-8")
    assert LOCAL_FILES.read_bytes(real) == real.read_bytes()
    assert LOCAL_FILES.is_file(real) is real.is_file() is True
    assert LOCAL_FILES.is_file(p) is p.is_file() is False
    assert LOCAL_FILES.is_dir(tmp_path / ".rig") is (tmp_path / ".rig").is_dir() is True
    with pytest.raises(FileNotFoundError):
        LOCAL_FILES.read_text(p)


def test_glob_returns_what_policy_resolution_asks_for(tmp_path):
    """`policy.resolve_layer_paths` sorts its glob because policy layers stack in order."""
    policy_dir = tmp_path / ".rig" / "policy"
    policy_dir.mkdir(parents=True)
    for name in ("team.json", "org.json", "project.json", "notes.md"):
        (policy_dir / name).write_text("{}", encoding="utf-8")
    assert LOCAL_FILES.glob(policy_dir, "*.json") == sorted(policy_dir.glob("*.json"))
    assert [p.name for p in LOCAL_FILES.glob(policy_dir, "*.json")] == [
        "org.json", "project.json", "team.json"]


def test_mkdir_matches_the_parents_exist_ok_call_every_site_makes(tmp_path):
    LOCAL_FILES.mkdir(tmp_path / "a" / "b")
    (tmp_path / "c" / "d").mkdir(parents=True, exist_ok=True)
    assert (tmp_path / "a" / "b").is_dir()
    LOCAL_FILES.mkdir(tmp_path / "a" / "b")  # exist_ok, as every govern site relies on
    assert (tmp_path / "a" / "b").stat().st_mode == (tmp_path / "c" / "d").stat().st_mode


# ── FileStore's private half vs secure_fs ────────────────────────────────────
def _private_dir(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    d = tmp_path / name
    d.mkdir()
    d.chmod(0o700)
    return d


def test_secret_writes_are_secure_fs_and_not_a_second_implementation(tmp_path):
    mine, real = _private_dir(tmp_path, "mine") / "key", _private_dir(tmp_path, "real") / "key"
    LOCAL_FILES.write_secret_bytes(mine, b"provenance-key-bytes")
    secure_fs.atomic_write_bytes(real, b"provenance-key-bytes")
    assert mine.read_bytes() == real.read_bytes()
    assert mine.stat().st_mode == real.stat().st_mode

    LOCAL_FILES.append_secret_line(mine, b"one\n")
    secure_fs.atomic_append_line(real, b"one\n")
    assert mine.read_bytes() == real.read_bytes()
    assert (LOCAL_FILES.read_secret_bytes(mine) == secure_fs.read_bytes(real)
            == real.read_bytes())


def test_plain_writes_deliberately_do_not_go_through_secure_fs(tmp_path):
    """Governance records are ordinary files, and the port must keep them that way.

    `secure_fs` refuses a target whose directory is not 0700 caller-owned. `.rig/` is not, so a
    `write_text` routed through it would raise on every repository that has one — which is why
    the port has two write methods rather than one strict one.
    """
    open_dir = tmp_path / "rig"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    LOCAL_FILES.write_text(open_dir / "ledger.jsonl", "{}\n")
    (open_dir / "raw.jsonl").write_text("{}\n", encoding="utf-8")
    assert (open_dir / "ledger.jsonl").stat().st_mode == (open_dir / "raw.jsonl").stat().st_mode
    with pytest.raises(OSError):
        secure_fs.atomic_write_bytes(open_dir / "refused.jsonl", b"{}\n")


# ── Env vs os.environ ────────────────────────────────────────────────────────
def test_env_get_matches_os_environ_get(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_INVOKER", "claude-code")
    monkeypatch.delenv("RIG_POLICY_HOME", raising=False)
    assert OS_ENV.get("RIG_INVOKER") == os.environ.get("RIG_INVOKER") == "claude-code"
    assert OS_ENV.get("RIG_POLICY_HOME") is os.environ.get("RIG_POLICY_HOME") is None
    assert OS_ENV.get("RIG_POLICY_HOME", "/policy") == os.environ.get("RIG_POLICY_HOME", "/policy")
    # `ledger.append` reads this one and falls back; the port must not change what it sees.
    entry = ledger.append(tmp_path, "noop", actor="a")
    assert entry["invoker"] == (OS_ENV.get("RIG_INVOKER") or "direct") == "claude-code"


def test_env_expanduser_matches_os_path_expanduser(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for text in ("~/policy/acme.json", "~", "policy/acme.json", "/abs/acme.json"):
        assert OS_ENV.expanduser(text) == os.path.expanduser(text)


def test_env_snapshot_is_the_environment_and_a_copy(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    snap = OS_ENV.snapshot()
    assert snap == dict(os.environ) == gitroot.unrouted_env(dict(os.environ)) | {
        k: os.environ[k] for k in gitroot.ROUTING_VARS if k in os.environ}
    snap.pop("GIT_DIR")
    assert os.environ.get("GIT_DIR") == "/somewhere/else/.git"


def test_env_is_what_current_actor_reads(monkeypatch):
    """`identity.current_actor` prefers RIG_ACTOR, then RIG_USER — both through this port."""
    monkeypatch.delenv("RIG_USER", raising=False)
    monkeypatch.setenv("RIG_ACTOR", "zoe")
    assert identity.current_actor() == OS_ENV.get("RIG_ACTOR") == "zoe"
    monkeypatch.delenv("RIG_ACTOR")
    monkeypatch.setenv("RIG_USER", "yves")
    assert identity.current_actor() == OS_ENV.get("RIG_USER") == "yves"


# ── Clock vs the six govern reads ────────────────────────────────────────────
def test_now_is_local_and_aware_and_between_two_raw_reads():
    before = datetime.datetime.now().astimezone()
    got = SYSTEM_CLOCK.now()
    after = datetime.datetime.now().astimezone()
    assert before <= got <= after
    assert got.tzinfo is not None and got.utcoffset() == before.utcoffset()


def test_stamp_is_exactly_what_the_govern_sites_write():
    when = datetime.datetime.now().astimezone() - datetime.timedelta(days=90)
    assert SYSTEM_CLOCK.stamp(when) == when.isoformat(timespec="seconds")

    mine = datetime.datetime.fromisoformat(SYSTEM_CLOCK.stamp())
    theirs = datetime.datetime.fromisoformat(ledger._now())
    assert abs((mine - theirs).total_seconds()) < 2
    assert mine.utcoffset() == theirs.utcoffset() and mine.microsecond == 0


def test_stamp_carries_the_offset_the_conformance_window_compares_on(tmp_path):
    """`conformance._load_tasks` compares ISO *text* with `>=`, so the offsets must match."""
    cutoff = SYSTEM_CLOCK.stamp(SYSTEM_CLOCK.now() - datetime.timedelta(days=90))
    recent = ledger.append(tmp_path, "noop", actor="a")["ts"]
    stale = SYSTEM_CLOCK.stamp(SYSTEM_CLOCK.now() - datetime.timedelta(days=365))
    assert cutoff[-6:] == recent[-6:]          # same offset, or the ordering means nothing
    assert stale < cutoff < recent             # lexicographic, exactly as the window does it


def test_today_matches_what_waiver_calls_today():
    assert SYSTEM_CLOCK.today() == waiver._today() == datetime.date.today()
    assert SYSTEM_CLOCK.today() == SYSTEM_CLOCK.now().date()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="TZ cannot be changed on this platform")
@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Niue"])
def test_today_is_the_local_date_and_not_utc(monkeypatch, zone):
    """A waiver expires on a date, and the date is the one the person granting it is living in.

    The two zones are +14 and -11: at every hour of the UTC day at least one of them is on a
    different calendar date from UTC, so a clock that quietly answered in UTC fails this
    whatever time the suite runs. `waiver.grant` refuses an expiry that is not in the future and
    `is_active` compares against this date, so a day's drift is a waiver that dies early or
    lives an extra day.
    """
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        assert SYSTEM_CLOCK.today() == waiver._today() == datetime.date.today()
        assert SYSTEM_CLOCK.stamp()[-6:] == ledger._now()[-6:] != "+00:00"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_clock_output_is_consumable_by_the_approval_freshness_rule():
    """`approval._age_hours` parses the stamp a decision was written with."""
    ts = SYSTEM_CLOCK.stamp(SYSTEM_CLOCK.now() - datetime.timedelta(hours=5))
    assert 4.9 < approval._age_hours(ts) < 5.1


# ── GitRepo vs git, and vs gitroot ───────────────────────────────────────────
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    def run(*args):
        return subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                              text=True, check=True)

    run("init", "-q")
    run("config", "user.name", "Ada Lovelace")
    run("config", "user.email", "ada@example.com")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-q", "-m", "first")
    return root


@needs_git
def test_config_value_matches_the_call_identity_makes(repo, monkeypatch):
    raw = _raw(["git", "config", "user.name"], cwd=str(repo))
    assert GIT.config_value("user.name", cwd=repo) == raw.stdout.strip() == "Ada Lovelace"
    monkeypatch.delenv("RIG_ACTOR", raising=False)
    monkeypatch.delenv("RIG_USER", raising=False)
    assert identity.current_actor(repo) == GIT.config_value("user.name", cwd=repo)


@needs_git
def test_config_value_is_none_when_git_has_no_answer(repo, tmp_path):
    assert GIT.config_value("rig.nosuchkey", cwd=repo) is None
    assert _raw(["git", "config", "rig.nosuchkey"], cwd=str(repo)).returncode != 0
    assert GIT.config_value("user.name", cwd=tmp_path / "not-a-repo") is None


@needs_git
def test_head_matches_the_call_govern_cli_makes(repo):
    raw = _raw(["git", "rev-parse", "HEAD"], cwd=str(repo))
    assert GIT.head(cwd=repo) == raw.stdout.strip() == govern_cli._head(repo, {})
    assert len(GIT.head(cwd=repo)) == 40


@needs_git
def test_head_is_none_before_there_is_a_commit(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(empty), check=True)
    assert GIT.head(cwd=empty) is None and govern_cli._head(empty, {}) is None


@needs_git
def test_worktree_questions_are_gitroots_answers(repo):
    assert GIT.main_worktree(repo) == gitroot.main_worktree(repo)
    assert GIT.invocation_worktree(repo) == gitroot.invocation_worktree(repo)
    assert GIT.main_worktree(repo) == repo.resolve()


@needs_git
def test_git_port_ignores_the_routing_variables_gitroot_strips(repo, tmp_path, monkeypatch):
    """The property `gitroot` exists for: an inherited GIT_DIR must not re-point governance."""
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(other), check=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert GIT.head(cwd=repo) == _raw(["git", "rev-parse", "HEAD"], cwd=str(repo),
                                      env=gitroot.unrouted_env()).stdout.strip()
    assert GIT.main_worktree(repo) == gitroot.main_worktree(repo) == repo.resolve()
