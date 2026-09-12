"""One presenter per `orchestrate` command, and four more ports proved at their own
call sites, all by taking the real adapter away.

`orchestrate/cli.py` builds one `ConsolePresenter` in `main()` and hands it to every verb
in `COMMANDS`. That is only half the wiring, and the half that was already true of
`govern/cli.py` on the day it shipped a bug: a handler that takes a port and then calls
into the judgement layer without passing it on leaves the *callee's default* in charge, and
the default is the real adapter. `tests/test_govern_frozen_clock.py` names the
reproduction — `cmd_waiver` computed an expiry from the injected clock and handed it to a
`waiver.grant` reading the wall clock, so a frozen command refused the date it had just
computed. Nothing user-visible, because production passes real adapters at every level and
two real clocks agree. That is exactly why nothing caught it.

**These tests fail on the shape, not on the symptom.** Asserting the text a command prints
would pin today's wording and nothing else; a call added next month would reach `CONSOLE`
and still print the same words onto the same stream. So the fixtures below disarm the
adapter *classes* — every method raises — for the length of each test. A port that is not
forwarded lands on the module default, the module default *is* an instance of that class,
and the command dies where the forwarding stopped.

**Disarmed on the class, not on the name each module imported.** A default argument binds
its value when the `def` is executed, so rebinding `commands.CONSOLE` would leave every
signature still holding the original instance and the trap would never fire — a tripwire
that cannot fire is worse than none, because it reports the absence of a bug it could not
have seen. The instances the defaults hold *are* instances of these classes, so disarming
the class reaches all of them, including the presenter `main()` builds for itself.

**Five ports reach this pillar, and only one of them is threaded from the shell.** `main()`
passes `out=` and nothing else, so `Presenter` is the only port a whole command can be
asked about: `no_ambient_ports` disarms `ConsolePresenter` alone, and the verb tests below
drive commands end to end under it. The other four are on signatures inside the pillar
without a path down from `main()` — `cmd_init` reaches `runstate.make_run_id`'s
`SYSTEM_CLOCK` because `cmd_init` holds no clock to forward, not because a forward was
dropped — so disarming them for a whole command would assert a forwarding nobody built.
They get one fixture and one probe each instead, at the call sites that *do* hold them:
`cmd_resume`'s `clock`, `cmd_install_shim`'s `env`, `cmd_models`' `files`, and
`providers`' `proc`. That is the division `packs`' tripwire drew for `OsEnv` and
`SubprocessRunner`, arrived at the same way — by measuring which adapter a real command
actually reaches.

**The `proc` probe is the one that answers pass 2's audit.** Two `providers` helpers take a
`ProcessRunner` and then call `_git_untracked_files`, a second git read one level down;
before pass 2 neither forwarded it, so an injected runner was silently bypassed for that
nested call and the diff evidence came from the real `git` in the real cwd. A tripwire that
only checked the top-level parameter would have said nothing. So that test asserts twice
over: the disarmed `SubprocessRunner` kills a dropped forward, and the stub's own call log
must contain the nested `git ls-files` — which it cannot if the inner call went elsewhere.

**`OsEnv` is in its own fixture and never disarmed for a whole command, and that is a
measurement rather than a preference.** `cmd_models` reaches `discover_models`, which reads
`ANTHROPIC_API_KEY` through `providers`' own `Env` default; `cmd_plan` and `cmd_graph`
reach `recipes._trust_store_path` and `resolve_recipe`, which read theirs. None of those
commands takes an `env` to forward, so a run with `OsEnv` disarmed refuses at a site this
pillar has no keyword to fix — the trap would be answering for wiring nobody built. It is
therefore disarmed only in `no_ambient_env`, over `cmd_install_shim`, which is one of the
two commands that does hold an `Env`.

**`selftest` is deliberately not driven here.** `cmd_selftest` calls `cmd_runs([])` and
`cmd_resume([...])` without `out=`, on purpose and with the reason written at both sites:
those two scenarios read what the sub-command *said* by capturing stdout, and handing them
the caller's presenter would send the captured report to whoever ran `selftest` and leave
the assertions reading an empty buffer. Driving `selftest` under `no_ambient_ports` would
fail on that decision rather than on a defect, so it is named here instead of being made to
pass by widening the fixture. Replacing the two redirects with a recording `Presenter` is
the change that would bring it in, and the site already says so.

**`resolve_recipe` is not a forwarding site either, and also on purpose.** `packs/cli.py`
substitutes `commands.resolve_recipe` with a one-argument lambda at run time, and three
test modules do the same at thirteen sites, so an added `out=` keyword would stop every one
of those substitutions silently — `tests/test_architecture_inventory.py` records the
decision where the count of moved `print`s is kept. The verbs that call it are driven here
against recipes that resolve without the trust gate speaking, and the two tests that need a
particular recipe substitute it the same one-argument way, which is what keeps this file
from asserting the opposite of that decision.

`test_the_tripwire_is_armed` checks all five mechanisms, so a green run here can never mean
a trap was never set.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.orchestrate import (commands, config, graph, mcp_scan, providers,
                                       queueing, recipes)
from rig_workbench.ports import Clock, Env, FileStore, Presenter, ProcessRunner
from rig_workbench.ports import local as ports_local

#: A moment with an offset that is neither UTC nor any plausible CI machine's, so a reading
#: that came from the wall clock cannot coincide with one that came from here. Every
#: assertion below is relative to this instant; nothing about it expires.
FROZEN = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


class PortNotForwarded(AssertionError):
    """An `orchestrate` command reached a real adapter while holding an injected port."""


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


class MemoryFiles:
    """A `FileStore` over a dictionary, so a write that should have been injected is
    visible as a key here rather than as a file on somebody's disk."""

    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    def read_text(self, path) -> str:
        return str(self.written[str(path)])

    def read_bytes(self, path) -> bytes:
        return str(self.written[str(path)]).encode("utf-8")

    def write_text(self, path, text: str) -> None:
        self.written[str(path)] = text

    def append_line(self, path, line: str) -> None:
        self.written[str(path)] = str(self.written.get(str(path), "")) + line + "\n"

    def is_file(self, path) -> bool:
        return str(path) in self.written

    def is_dir(self, path) -> bool:
        return False

    def glob(self, path, pattern: str) -> list[pathlib.Path]:
        return []

    def mkdir(self, path) -> None:
        return None

    def write_secret_bytes(self, path, payload: bytes) -> None:
        self.written[str(path)] = payload

    def read_secret_bytes(self, path) -> bytes:
        return bytes(self.written[str(path)])  # type: ignore[arg-type]

    def append_secret_line(self, path, line: bytes) -> None:
        self.written[str(path)] = line


class StubRunner:
    """A `ProcessRunner` that answers a scripted table and records every argv it saw.

    The call log is the half of the nested-forward test that does not depend on the
    disarmed adapter: a `git ls-files` that never appears here went somewhere else.
    """

    def __init__(self, answers: list[tuple[list[str], int, object]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def run(self, argv, *, cwd=None, env=None, timeout=None, input=None,
            text=True, errors="replace"):
        argv = list(argv)
        self.calls.append(argv)
        empty: object = "" if text else b""
        for prefix, code, payload in self.answers:
            if argv[:len(prefix)] == prefix:
                return subprocess.CompletedProcess(argv, code, stdout=payload, stderr=empty)
        return subprocess.CompletedProcess(argv, 1, stdout=empty, stderr=empty)


def test_the_injected_ports_are_ports() -> None:
    """Otherwise this file could be injecting something `orchestrate` only tolerates."""
    assert isinstance(Recorder(), Presenter)
    assert isinstance(FrozenClock(), Clock)
    assert isinstance(DictEnv({}), Env)
    assert isinstance(MemoryFiles(), FileStore)
    assert isinstance(StubRunner([]), ProcessRunner)


# ── the traps ────────────────────────────────────────────────────────────────


def _refusing(name: str, note: str):
    def refusing(*_args: object, **_kwargs: object) -> object:
        raise PortNotForwarded(
            f"an orchestrate command reached {name} while it was holding an injected "
            f"port. {note} Forward it at that call site; see "
            "rig_workbench/orchestrate/cli.py's module docstring for the rule."
        )
    return refusing


def _disarm(monkeypatch: pytest.MonkeyPatch, cls: type, methods: tuple[str, ...],
            note: str) -> None:
    for method in methods:
        monkeypatch.setattr(cls, method, _refusing(f"{cls.__name__}.{method}", note))


@pytest.fixture
def no_ambient_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the one adapter the shell owns away for the length of a test.

    Patched on the **class** for the reason the module docstring gives. `main()` builds
    exactly one `ConsolePresenter`, so a second one waking up during a command means some
    call below the verb reached for a presenter instead of being passed one.
    """
    _disarm(monkeypatch, ports_local.ConsolePresenter, ("out", "err"),
            "main() builds one presenter and hands it to the verb, so some call between "
            "the verb and this one did not pass it on.")


@pytest.fixture
def no_ambient_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SystemClock`, disarmed over the one command that holds a `Clock`."""
    _disarm(monkeypatch, ports_local.SystemClock, ("now", "today", "stamp"),
            "cmd_resume takes a clock and this read did not use it.")


@pytest.fixture
def no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OsEnv`, disarmed separately and only over `cmd_install_shim` — see the docstring.

    A whole command reaches `recipes` and `providers` defaults that no verb holds an `Env`
    to replace, so disarming this adapter for a run refuses where this pillar has no call
    site to forward at.
    """
    _disarm(monkeypatch, ports_local.OsEnv, ("get", "expanduser", "snapshot"),
            "cmd_install_shim takes an env and this read did not use it.")


@pytest.fixture
def no_ambient_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LocalFileStore`, disarmed over the one command that holds a `FileStore`."""
    _disarm(monkeypatch, ports_local.LocalFileStore,
            ("read_text", "read_bytes", "write_text", "append_line", "is_file", "is_dir",
             "glob", "mkdir", "write_secret_bytes", "read_secret_bytes",
             "append_secret_line"),
            "cmd_models takes a file store and this write did not use it.")


@pytest.fixture
def no_ambient_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SubprocessRunner`, disarmed over the two helpers that forward a `ProcessRunner`."""
    _disarm(monkeypatch, ports_local.SubprocessRunner, ("run",),
            "the caller was handed a runner and a git read one level down did not get it.")


# ── the fixtures the measurements run against ───────────────────────────────


@pytest.fixture
def project(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """An empty project directory, with the run log pointed inside it.

    `config.RUNS_PATH` is derived from the invocation directory, which is frozen at import
    and is this repository — a `runs` verb driven here would otherwise read the developer's
    own run history and say whatever that happens to contain.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / ".rig" / "runs.jsonl")
    return tmp_path


@pytest.fixture
def run_state(project: pathlib.Path) -> pathlib.Path:
    """A one-step run-state on disk, built with the real adapters.

    This is the fixture, not the measurement: `new_state` stamps a run id from
    `SYSTEM_CLOCK`, which is why it is built here and not inside a disarmed test.
    """
    from rig_workbench.orchestrate.runstate import new_state, save_state

    state = new_state("tripwire", [{
        "id": "design", "instruction": "x", "gate": None, "pattern": None, "personas": [],
        "needs": [], "acceptance": [], "checks": ["true"], "max_retries": 2,
        "output_contract": None,
    }], None)
    path = project / "run-state.json"
    save_state(state, path)
    return path


@pytest.fixture
def nonexecutable_recipe(project: pathlib.Path,
                         monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A recipe `_require_executable_recipe` refuses, substituted in the way the existing
    suite already substitutes it — `resolve_recipe` takes no `out=`, and this file does not
    change that."""
    path = project / "manual.md"
    path.write_text(
        "---\nname: preflight\ndescription: test\nscope: project\n"
        "autonomy: interactive\nno_orchestrate: true\nsteps:\n"
        "  - id: vote\n    instruction: test\n    gate: custom-vote\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: path)
    return path


def test_the_tripwire_is_armed(project: pathlib.Path, run_state: pathlib.Path,
                               no_ambient_ports: None, no_ambient_clock: None,
                               no_ambient_env: None, no_ambient_files: None,
                               no_ambient_process: None) -> None:
    """The five mechanisms, so no assertion below passes because a trap was never set.

    One probe per disarmed adapter, each reaching it the way a forgotten keyword would —
    through the parameter default, not through a name this file spells.
    """
    # Presenter, through `cmd_status`'s default.
    with pytest.raises(PortNotForwarded):
        commands.cmd_status([str(run_state)])

    # Clock, through `cmd_resume`'s default. It is reached before any check runs, on the
    # mtime gap the digest reports.
    with pytest.raises(PortNotForwarded):
        commands.cmd_resume([str(run_state)], out=Recorder())

    # Env, through `cmd_install_shim`'s default.
    with pytest.raises(PortNotForwarded):
        commands.cmd_install_shim(["--to", str(project / "bin" / "rig")], out=Recorder())

    # FileStore, through `cmd_models`' default. `--save` is the one write it makes, and
    # `env=` is injected at the one read that would otherwise refuse first.
    with pytest.raises(PortNotForwarded):
        providers.cmd_models(["--save", "--json"], out=Recorder())

    # ProcessRunner, through `_git_diff_evidence`'s default.
    with pytest.raises(PortNotForwarded):
        providers._git_diff_evidence({"cwd": str(project)})


# ── the presenter, across the verbs the shell dispatches ────────────────────


def test_the_read_only_verbs_speak_only_through_the_presenter_the_shell_built(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """Six verbs that answer a question and change nothing.

    `ConsolePresenter` is disarmed, so a stray `print` through a second presenter ends the
    command rather than reaching a captured stream. Each verb is checked by a line the
    recorder holds, so a command that said nothing at all cannot pass either.
    """
    seen = Recorder()
    commands.cmd_plan(["review-only"], out=seen)
    assert any("Execution:" in line for line in seen.out_lines), seen.text

    seen = Recorder()
    graph.cmd_graph(["--json"], out=seen)
    assert json.loads("\n".join(seen.out_lines))["nodes"], seen.text

    seen = Recorder()
    mcp_scan.cmd_mcp_scan(["--json"], out=seen)
    assert "module_findings" in json.loads("\n".join(seen.out_lines)), seen.text

    seen = Recorder()
    commands.cmd_runs([], out=seen)
    assert seen.out_lines[0].startswith("No run records yet"), seen.text

    seen = Recorder()
    queueing.cmd_queue(["list"], out=seen)
    assert seen.out_lines, seen.text

    seen = Recorder()
    commands.cmd_fleet(["--repos", str(project)], out=seen)
    assert seen.out_lines, seen.text


def test_the_run_state_verbs_carry_the_presenter_through_the_whole_cycle(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """`init` -> `status` -> `next` -> `check`, each on the presenter it was handed.

    Four verbs and four renderings, all of which a second presenter would swallow. The
    forward `init` makes *before* it renders — into `parse_frontmatter(path, out=out)` —
    is not measured here, because that helper speaks on one branch only; it has its own
    test below, where the branch is made to run.
    """
    seen = Recorder()
    commands.cmd_init(["review-only", "--goal", "tripwire"], out=seen)
    assert any("run-state:" in line for line in seen.out_lines), seen.text
    state_path = project / "run-state.json"
    assert state_path.is_file()

    seen = Recorder()
    commands.cmd_status([str(state_path)], out=seen)
    assert seen.out_lines, seen.text

    seen = Recorder()
    commands.cmd_next([str(state_path)], out=seen)
    assert any("▶" in line for line in seen.out_lines), seen.text

    seen = Recorder()
    commands.cmd_check([str(state_path)], out=seen)
    assert seen.out_lines, seen.text



def test_the_recipe_parse_carries_the_presenter_on_the_branch_that_speaks(
        project: pathlib.Path, no_ambient_ports: None,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """`cmd_init`, `cmd_run` and `cmd_ab` each forward into `parse_frontmatter(path,
    out=out)`, and that helper says exactly one thing: the PyYAML refusal.

    Which is why the forward cannot be measured by driving the three verbs normally — the
    branch never runs, and a dropped `out=` there is invisible. So the absence is
    simulated the way `tests/test_orchestrate_yaml_guard.py` documents and asserts,
    `sys.modules["yaml"] = None`, and the refusal has to arrive on the recorder while
    `ConsolePresenter` is disarmed. Three verbs rather than one because the keyword is
    typed three times.
    """
    recipe = project / "any.md"
    recipe.write_text("---\nname: any\n---\n", encoding="utf-8")
    monkeypatch.setattr(commands, "resolve_recipe", lambda _name: recipe)
    monkeypatch.setitem(sys.modules, "yaml", None)

    for argv, verb in (
        (["any", "--out", "s.json"], commands.cmd_init),
        (["any", "--provider", "mock", "--out", "s.json"], commands.cmd_run),
        (["any", "other", "--provider", "mock"], commands.cmd_ab),
    ):
        seen = Recorder()
        with pytest.raises(SystemExit) as stopped:
            verb(argv, out=seen)
        assert stopped.value.code == 1
        assert seen.out_lines[-1] == "[ERROR] PyYAML not found. `pip install pyyaml`."


def test_a_refusal_is_reported_through_the_presenter_the_command_was_handed(
        nonexecutable_recipe: pathlib.Path, no_ambient_ports: None) -> None:
    """The `Refusal` hop, which is the one presenter forward that happens in a decorator.

    `_require_executable_recipe` raises and `_reports_refusals` reports — through
    `kwargs.get("out") or CONSOLE`, so a verb that took its presenter positionally, or a
    decorator applied in the wrong order, falls straight onto the disarmed adapter. The
    exit status travels with the refusal and is asserted beside the lines, because that is
    the other half of what was moved out of the judgement layer.
    """
    seen = Recorder()
    with pytest.raises(SystemExit) as stopped:
        commands.cmd_init(["manual", "--out", "blocked.json"], out=seen)
    assert stopped.value.code == 2
    assert seen.out_lines[0].startswith("[BLOCKED] recipe preflight is computationally "
                                        "nonexecutable"), seen.text
    assert not (nonexecutable_recipe.parent / "blocked.json").exists()


def test_the_auto_route_regret_report_is_two_hops_from_the_shell(
        project: pathlib.Path, no_ambient_ports: None) -> None:
    """`cmd_runs` -> `_print_auto_route_regret(rows, out=out)`.

    The one helper in `commands.py` that renders a whole report of its own, and the only
    presenter forward in this pillar that is neither a decorator nor a recipe parse. It is
    reached only past the "no run records" guard, so the log has one record in it.
    """
    (project / ".rig").mkdir()
    config.RUNS_PATH.write_text(json.dumps({
        "ts": "2026-06-01T12:00:00+09:00", "recipe": "bugfix", "backend": "orchestrate",
        "final": "DONE", "steps_total": 1, "steps_passed": 1, "retries": 0, "steps": [],
    }) + "\n", encoding="utf-8")

    seen = Recorder()
    commands.cmd_runs(["--auto-route-regret"], out=seen)
    assert seen.out_lines[0].startswith("No auto-routed steps recorded yet"), seen.text


# ── the four ports that are not threaded from the shell ─────────────────────


def test_resume_reads_the_clock_it_was_handed(run_state: pathlib.Path,
                                              no_ambient_ports: None,
                                              no_ambient_clock: None) -> None:
    """`cmd_resume` is the pillar's one command-level `Clock`. It reads it once, for the
    gap between now and the run-state's mtime, and the digest reports that gap in words.

    The file is stamped two hours before `FROZEN`, so the sentence the command prints is
    arithmetic on the injected instant and could not have come from a wall clock — which
    is the observable half, on top of the disarmed adapter proving no second clock answered.
    """
    stamped = FROZEN.timestamp() - 2 * 3600
    os.utime(run_state, (stamped, stamped))
    seen = Recorder()
    commands.cmd_resume([str(run_state)], out=seen, clock=FrozenClock())
    assert any(line.startswith("↺ resumed after ~2h00m") for line in seen.out_lines), seen.text


def test_install_shim_reads_the_environment_it_was_handed(
        project: pathlib.Path, no_ambient_ports: None, no_ambient_env: None) -> None:
    """`cmd_install_shim` asks the `Env` for `PATH` to decide whether to add a hint. The
    injected mapping is this file's dictionary rather than this process's environment, so
    the hint is a statement about the port and not about the machine."""
    seen = Recorder()
    target = project / "bin" / "rig"
    commands.cmd_install_shim(["--to", str(target)], out=seen,
                              env=DictEnv({"PATH": "/nowhere/injected"}))
    assert target.is_symlink()
    assert any("does not seem to be on $PATH" in line for line in seen.out_lines), seen.text

    seen = Recorder()
    commands.cmd_install_shim(["--to", str(target), "--force"], out=seen,
                              env=DictEnv({"PATH": str(target.parent)}))
    assert not any("does not seem to be on $PATH" in line for line in seen.out_lines), \
        seen.text


def test_models_save_writes_through_the_file_store_it_was_handed(
        project: pathlib.Path, no_ambient_ports: None, no_ambient_files: None) -> None:
    """`cmd_models --save` is the pillar's one command-level `FileStore` write, and its
    target is a path in the caller's real home — so a dropped `files=` does not merely read
    the wrong adapter, it writes to somebody's `~/.claude/rig/models.json`. The memory
    store holds the write instead, and the disarmed adapter refuses the alternative."""
    files = MemoryFiles()
    seen = Recorder()
    providers.cmd_models(["--save", "--json"], out=seen, files=files)
    assert len(files.written) == 1
    written = next(iter(files.written))
    assert written.endswith("models.json"), written
    assert any(line.startswith("\nSaved: ") for line in seen.out_lines), seen.text


def test_the_nested_git_read_is_handed_the_runner_its_caller_was_given(
        project: pathlib.Path, no_ambient_process: None) -> None:
    """The miss pass 2's audit found, kept findable.

    `_git_diff_evidence` and `_git_changed_files` each take a `ProcessRunner`, and each
    calls `_git_untracked_files` — a second git read one level down, which used to be made
    without the runner. A tripwire that only checked the top-level parameter would have
    passed on that, so this asserts twice: the disarmed `SubprocessRunner` kills a dropped
    forward, and the stub's own call log must contain the nested `git ls-files`, which it
    cannot if the inner call reached anywhere else.
    """
    (project / "new.txt").write_text("untracked\n", encoding="utf-8")
    nested = ["git", "ls-files", "--others", "--exclude-standard", "-z"]

    runner = StubRunner([(["git", "diff", "HEAD"], 0, "tracked diff\n"),
                         (nested, 0, b"new.txt\0")])
    evidence = providers._git_diff_evidence({"cwd": str(project)}, proc=runner)
    assert nested in runner.calls, runner.calls
    assert evidence is not None and "tracked diff" in evidence and "new.txt" in evidence

    runner = StubRunner([(["git", "diff", "--name-only", "-z", "HEAD"], 0, b"tracked.py\0"),
                         (nested, 0, b"new.txt\0")])
    assert providers._git_changed_files({"cwd": str(project)}, proc=runner) == [
        "new.txt", "tracked.py"]
    assert nested in runner.calls, runner.calls


def test_the_pillar_s_own_env_reads_are_injectable_at_their_own_call_sites(
        no_ambient_env: None) -> None:
    """`Env` is on twelve signatures in this pillar and threaded from the shell at two, so
    most of it is proved the way `packs`' tripwire proves its unthreaded pair: the sites
    take a port and read it, rather than reaching past it to the process. Both probes are
    read-only — they answer a path and a boolean and touch nothing."""
    store = pathlib.Path("/nowhere/trust.json")
    assert recipes._trust_store_path(env=DictEnv({"RIG_TRUST_STORE": str(store)})) == store
    # Unset is a different answer from set-to-empty, and the port keeps them apart.
    assert recipes._trust_store_path(env=DictEnv({})) != store

    found = providers.discover_models({}, env=DictEnv({"ANTHROPIC_API_KEY": "k"}))
    assert found["anthropic"]["available"] is True
    assert providers.discover_models({}, env=DictEnv({}))["anthropic"]["available"] is False
