"""One presenter and one clock per `pack` command, proved by taking them away.

`packs/cli.py` builds two adapters at the process boundary and hands them to the verbs.
That is only half the wiring, and the half that was already true of `govern/cli.py` on
the day it shipped a bug: a handler that takes a port and then calls into the judgement
layer without passing it on leaves the *callee's default* in charge, and the default is
the real adapter. `tests/test_govern_frozen_clock.py` names the reproduction — `cmd_waiver`
computed an expiry from the injected clock and handed it to a `waiver.grant` reading the
wall clock, so a frozen command refused the date it had just computed. Nothing
user-visible, because production passes real adapters at every level and two real clocks
agree. That is exactly why nothing caught it.

**These tests fail on the shape, not on the symptom.** Freezing the clock and asserting
timestamps would pin today's forwarding sites and nothing else; a call added next month
would read `SYSTEM_CLOCK` and still produce stamps that agree with a real frozen clock
often enough to pass. So the fixture below disarms `SystemClock` and `ConsolePresenter`
themselves — every method raises — for the length of each command. A port that is not
forwarded lands on the module default, the module default *is* an instance of that class,
and the command dies where the forwarding stopped.

**Disarmed on the class, not on the name each module imported.** A default argument binds
its value when the `def` is executed, so rebinding `lock.SYSTEM_CLOCK` would leave every
signature still holding the original instance and the trap would never fire — a tripwire
that cannot fire is worse than none, because it reports the absence of a bug it could not
have seen. The instances the defaults hold *are* instances of these classes, so disarming
the class reaches all of them, including the adapter the shell builds for itself.

**Why `OsEnv` and `SubprocessRunner` are not in the disarmed set.** Pass 1 of this pillar
put the environment reads in `resolver.py` and `trust.py` and the one `subprocess` call in
`sources.py` behind their ports with the default adapter on the signature, but did not
thread either port down from the shell: `cmd_pack` does not take an `env` or a `proc`,
because handing them down moves 23 call sites across 7 files, most of them in pillars that
have not migrated. Disarming those two classes here would therefore assert a forwarding
nobody built and fail on `pack list` before it reached anything worth measuring. When the
shell grows them, they belong in `no_ambient_ports` alongside the two that are there —
and `test_the_env_and_process_ports_are_injectable_at_their_own_call_sites` is what holds
the ground in the meantime: it shows the ports really are reachable by injection, which is
the property pass 2 will thread through.

The assertions on the frozen stamps are kept beside the trap because they say what the
right answer is: a pack scaffolded by `pack init` and the lock entry `pack install` writes
both carry the frozen instant rendered in UTC, which is the one spelling
`lock.validate_lock_root` parses back out. `test_the_tripwire_is_armed` checks the
mechanism, so a green run here can never mean the trap was never set.

Scope: the verbs that reach a forwarded port — `init` (the clock, through `init_pack`),
`install` (the clock, through `install_pack` into `lock.make_entry`), `invoke` (the
presenter, through `invoke_pack`), and `list`, `doctor` and `sync`, which speak through
the presenter the shell holds directly.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib

import pytest

from test_eval_cases import valid_case

from rig_workbench.eval.cases import canonical_json
from rig_workbench.packs import cli as pack_cli
from rig_workbench.packs.manifest import canonical
from rig_workbench.ports import Clock, Env, Presenter
from rig_workbench.ports import local as ports_local

#: A moment with an offset that is neither UTC nor any plausible CI machine's, so a stamp
#: that came from the wall clock cannot coincide with one that came from here. Every
#: assertion below is relative to this instant; nothing about it expires.
FROZEN = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
FROZEN_UTC_STAMP = FROZEN.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


class PortNotForwarded(AssertionError):
    """A `pack` command reached a real adapter while holding an injected port."""


class FrozenClock:
    """A `Clock` stopped at `FROZEN`, offset and all."""

    def now(self) -> dt.datetime:
        return FROZEN

    def today(self) -> dt.date:
        return FROZEN.date()

    def stamp(self, when: dt.datetime | None = None) -> str:
        return (FROZEN if when is None else when).isoformat(timespec="seconds")


class Recorder:
    """A `Presenter` that keeps the lines instead of printing them."""

    def __init__(self) -> None:
        self.out_lines: list[str] = []
        self.err_lines: list[str] = []

    def out(self, text: str = "") -> None:
        self.out_lines.append(text)

    def err(self, text: str = "") -> None:
        self.err_lines.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self.out_lines + self.err_lines)


class DictEnv:
    """An `Env` over a dictionary this file owns."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._values.get(name, default)

    def expanduser(self, path: str) -> str:
        return path

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)


def test_the_injected_ports_are_ports() -> None:
    """Otherwise this file could be injecting something `packs` only happens to tolerate."""
    assert isinstance(FrozenClock(), Clock)
    assert isinstance(Recorder(), Presenter)
    assert isinstance(DictEnv({}), Env)


@pytest.fixture
def no_ambient_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the two real adapters the shell owns away for the length of a test.

    Patched on the **class** for the reason the module docstring gives. `ConsolePresenter`
    is in the set as well as `SystemClock`: the shell holds one presenter, so a
    `ConsolePresenter` waking up during a test means a second one was reached for
    somewhere below it, which is the same defect wearing the presenter's clothes.
    """

    def refuse(name: str):
        def refusing(*_args: object, **_kwargs: object) -> object:
            raise PortNotForwarded(
                f"a pack command reached {name} while it was holding an injected port. "
                "Some call between the shell and this one did not pass the port on, so the "
                "command is running on two of them. Forward it at that call site; see "
                "rig_workbench/packs/cli.py's module docstring for the rule."
            )
        return refusing

    for cls, methods in (
        (ports_local.SystemClock, ("now", "today", "stamp")),
        (ports_local.ConsolePresenter, ("out", "err")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, refuse(f"{cls.__name__}.{method}"))


def test_the_tripwire_is_armed(no_ambient_ports: None, tmp_path: pathlib.Path,
                               monkeypatch: pytest.MonkeyPatch) -> None:
    """The mechanism itself, so no assertion below passes because the trap was never set.

    One probe per disarmed adapter, each reaching it the way a forgotten `clock=` / `out=`
    would — through the parameter default, not through a name this file spells. And each
    is precise: the injected port still answers.
    """
    with pytest.raises(PortNotForwarded):
        pack_cli.init_pack("probeone", kind="project", type_="skill", root=tmp_path)
    scaffolded = pack_cli.init_pack("probetwo", kind="project", type_="skill",
                                    root=tmp_path, clock=FrozenClock())
    manifest = json.loads((scaffolded / "pack.yaml").read_text(encoding="utf-8"))
    assert manifest["provenance"]["created_at"] == FROZEN_UTC_STAMP

    # `scope_root` refuses a project-scope root outside the project, so the probe stands
    # in the directory it is asking about.
    monkeypatch.chdir(tmp_path)
    empty = tmp_path / "empty-scope"
    empty.mkdir()
    with pytest.raises(PortNotForwarded):
        pack_cli.cmd_pack(["list", "--root", str(empty)])
    seen = Recorder()
    assert pack_cli.cmd_pack(["list", "--root", str(empty)], out=seen,
                             clock=FrozenClock()) == 0
    assert seen.out_lines == ["no packs installed in this scope"]


def test_the_env_and_process_ports_are_injectable_at_their_own_call_sites() -> None:
    """`Env` and `ProcessRunner` are behind their defaults but not yet threaded from the
    shell, so they are not in `no_ambient_ports`. This is what pass 1 did claim: the two
    sites take a port and read it, rather than reaching past it to the process.

    Both are read-only probes on functions with no side effect — `resolver._rig_home` and
    `trust._store_path` answer a path and touch nothing.
    """
    from rig_workbench.packs import resolver, trust

    home = pathlib.Path("/nowhere/rig-home")
    assert resolver._rig_home(env=DictEnv({"RIG_HOME": str(home)})) == home
    # Unset is a different answer from set-to-empty, and the port keeps them apart.
    roots = dict(resolver.pack_roots("/tmp/project", env=DictEnv({"RIG_ORG_HOME": "/org"})))
    assert roots["org"] == pathlib.Path("/org/packs")
    assert "org" not in dict(resolver.pack_roots("/tmp/project", env=DictEnv({})))

    store = pathlib.Path("/nowhere/trusted.json")
    assert trust._store_path(env=DictEnv({"RIG_PACK_TRUST_STORE": str(store)})) == store
    assert trust._store_path(env=DictEnv({"RIG_TRUST_STORE": str(store)})) == store


# ── a project with one scaffolded pack, bundled and ready to install ─────────


def _bound_case(surface: str) -> dict:
    """An approved case bound to the pack's one command.

    `validation.validate_pack` refuses a prompt-bearing pack with no approved case, and
    refuses a case that is not bound to a prompt asset the pack owns — so a pack with a
    command entrypoint cannot be installed without both. This is the smallest thing that
    satisfies them, borrowed from the eval suite's own fixture so the schema stays in one
    place.
    """
    case = copy.deepcopy(valid_case())
    case["id"] = "hello"
    case["prompt_surfaces"] = [surface]
    return case


@pytest.fixture
def project(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A project holding a source pack with one command, and that pack bundled to a zip.

    Built with the real adapters — this is the fixture, not the measurement. The commands
    under test run afterwards, with the adapters disarmed.
    """
    monkeypatch.chdir(tmp_path)
    # No other tier may answer: `pack_roots` reads these, and a developer's own installed
    # packs showing up would make `list` and `doctor` say something this file did not put
    # there. `RIG_ALLOW_PROJECT_PACKS` is the consent `pack invoke` needs for a
    # project-tier asset, which is what makes the invoke test reach its presenter.
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "trusted.json"))
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")

    source = tmp_path / "src"
    pack = pack_cli.init_pack("demo", kind="project", type_="skill", root=source)
    (pack / "commands" / "hello.md").write_text(
        "---\nname: hello\n---\n\nsay hello\n", encoding="utf-8")
    case_path = pack / "evals" / "cases" / "hello" / "case.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(canonical_json(_bound_case("command:hello")), encoding="utf-8")
    manifest = json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))
    manifest["entrypoints"] = [{"id": "hello", "kind": "command", "target": "hello"}]
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    from rig_workbench.packs.bundler import bundle_pack
    from rig_workbench.packs.sync import sync_manifest
    sync_manifest(str(pack))
    bundle_pack(str(pack), to=str(tmp_path / "dist" / "demo.zip"))
    return tmp_path


def run_pack(*argv: str) -> tuple[int, Recorder]:
    """One `rig-wb pack` command, driven through the shell's own entry point."""
    seen = Recorder()
    return pack_cli.cmd_pack(list(argv), out=seen, clock=FrozenClock()), seen


# ── the verbs ────────────────────────────────────────────────────────────────


def test_init_stamps_the_new_pack_with_the_clock_it_was_handed(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """`init` is the shell's one clock read. It is two hops from `cmd_pack` — the verb
    hands its clock to `init_pack`, which renders it into `provenance.created_at` — so a
    dropped `clock=` at either hop lands on the disarmed adapter."""
    code, seen = run_pack("init", "second", "--type", "skill", "--root", "src")
    assert code == 0, seen.text
    assert seen.out_lines[0] == f"initialized: {project / 'src' / 'second'}"
    manifest = json.loads((project / "src" / "second" / "pack.yaml").read_text(encoding="utf-8"))
    assert manifest["provenance"]["created_at"] == FROZEN_UTC_STAMP


def test_list_and_doctor_speak_only_through_the_presenter(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """The two read-only verbs. `ConsolePresenter` is disarmed, so a stray `print` through
    a second presenter ends the command rather than reaching a captured stream. `doctor`
    is driven twice because its two renderings are different calls — a line through
    `out.out` and a canonical document through `_emit_document`."""
    code, seen = run_pack("list")
    assert (code, seen.out_lines) == (0, ["no packs installed in this scope"])

    code, seen = run_pack("doctor", str(project / "src" / "demo"))
    assert code == 0, seen.text
    assert seen.out_lines[0] == "pack doctor: ok"

    code, seen = run_pack("doctor", str(project / "src" / "demo"), "--json")
    assert code == 0, seen.text
    # One line and no trailing blank: `_emit_document` removes the newline `canonical`
    # appends, because `Presenter.out` supplies the line ending itself.
    assert len(seen.out_lines) == 1
    assert json.loads(seen.out_lines[0])["status"] == "ok"


def test_sync_reports_every_declared_asset_through_the_presenter(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    code, seen = run_pack("sync", str(project / "src" / "demo"))
    assert code == 0, seen.text
    assert seen.out_lines[-1] == "pack sync: 2 asset(s) declared and hashed"


def test_install_writes_a_lock_entry_stamped_by_the_injected_clock(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """`install` is the deepest forward in this pillar: `cmd_pack` -> `install_pack` ->
    `lock.make_entry`, which is where the stamp is rendered. The entry's `installed_at` is
    the frozen instant in UTC, so this fails if any of those three hops drops the clock —
    and it fails by dying on the disarmed adapter rather than by comparing dates, which is
    what makes it a statement about the wiring."""
    code, seen = run_pack("install", str(project / "dist" / "demo.zip"),
                          "--scope", "project")
    assert code == 0, seen.text
    assert seen.out_lines == ["installed: demo@0.1.0 [unverified] -> "
                              f"{project / '.rig' / 'packs' / 'demo'}"]
    lock = json.loads((project / ".rig" / "packs" / "pack.lock.json")
                      .read_text(encoding="utf-8"))
    assert [entry["installed_at"] for entry in lock["packs"]] == [FROZEN_UTC_STAMP]

    # And the installed pack is visible to `list`, which is a second presenter forward
    # over state the first command wrote.
    code, seen = run_pack("list")
    assert code == 0, seen.text
    assert seen.out_lines[0].startswith("demo@0.1.0\tskill\tproject\t")


def test_invoke_hands_its_document_to_the_presenter_the_shell_built(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """`invoke_pack` is the only helper outside `cmd_pack` that speaks, so it is the only
    place a presenter could be reached for instead of passed. It is also the one document
    a caller parses out of `pack invoke`, so the blank-line question `_emit_document`
    settles is load-bearing here."""
    code, seen = run_pack("install", str(project / "dist" / "demo.zip"),
                          "--scope", "project")
    assert code == 0, seen.text

    code, seen = run_pack("invoke", "demo:hello")
    assert code == 0, seen.text
    assert len(seen.out_lines) == 1
    document = json.loads(seen.out_lines[0])
    assert document["entrypoint"] == "demo:hello"
    assert document["mode"] == "manual-command"
    assert document["asset"] == str(project / ".rig" / "packs" / "demo"
                                    / "commands" / "hello.md")


def test_the_error_line_still_goes_to_stderr(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """The one `err` call in the pillar, and the reason `Presenter` has two methods rather
    than one. A `PackError` writes `[ERROR] ...` to stderr and nothing to stdout, so a
    caller parsing a document off stdout is not handed prose."""
    code, seen = run_pack("validate", str(project / "does-not-exist"))
    assert code == 2
    assert seen.out_lines == []
    assert seen.err_lines == [
        f"[ERROR] pack directory does not exist: {project / 'does-not-exist'}"]
