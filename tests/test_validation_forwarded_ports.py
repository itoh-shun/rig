"""Four adapters per `rig-wb validate` run, proved by taking them away.

`validation/cli.py` builds a `Presenter`, a `ProcessRunner`, an `Env` and a `Clock` at the
process boundary and hands them to `cmd_validate`. That is only half the wiring, and the
half that was already true of `govern/cli.py` on the day it shipped a bug: a handler that
takes a port and then calls into the judgement layer without passing it on leaves the
*callee's default* in charge, and the default is the real adapter.
`tests/test_govern_frozen_clock.py` names the reproduction — `cmd_waiver` computed an
expiry from the injected clock and handed it to a `waiver.grant` reading the wall clock, so
a frozen command refused the date it had just computed. Nothing user-visible, because
production passes real adapters at every level and two real clocks agree. That is exactly
why nothing caught it.

**These tests fail on the shape, not on the symptom.** Freezing the clock and asserting the
freshness verdict would pin today's forwarding sites and nothing else — and
`tests/test_validation_catalog_ports.py` already pins that verdict on both sides of its
boundary, which is a statement about the rule rather than about the wiring. So the fixture
below disarms `ConsolePresenter`, `SubprocessRunner`, `OsEnv` and `SystemClock`
themselves — every method raises — for the length of each run. A port that is not forwarded
lands on the module default, the module default *is* an instance of that class, and the
command dies where the forwarding stopped.

**Disarmed on the class, not on the name each module imported.** A default argument binds
its value when the `def` is executed, so rebinding `catalog.SYSTEM_CLOCK` would leave
`check_wiki`'s signature still holding the original instance and the trap would never fire —
a tripwire that cannot fire is worse than none, because it reports the absence of a bug it
could not have seen. The instances the defaults hold *are* instances of these classes, so
disarming the class reaches all of them, including the four adapters `main()` builds for
itself.

**`SubprocessRunner` is in the disarmed set and `OsEnv` is in a second fixture, and the
difference is a measurement, not a preference.** `packs`' tripwire leaves both armed
because that pillar's shell threads neither. This one threads both — `cmd_validate` takes
`proc` and `env` and hands both to `check_graph`, the pillar's only process call and only
environment read. But `validate` does not stop inside `validation/`: `check_manifest` asks
`packs.resolver.resolve_asset` whether a `default_recipe` / `default_personas[]` name
resolves in any tier, that helper reads the tier roots through **`packs`' own `Env`
default** (pass 1 of that pillar put the port on the signature and left the threading to a
later one), and `manifest._resolve` wraps the call in `except Exception: return True` so a
broken pack collection is not reported as a manifest typo.

Disarming `OsEnv` for a whole run therefore does not fail loudly where the forwarding
stopped — it is swallowed, and two selftest scenarios (`manifest-default-recipe-typo`,
`manifest-default-persona-typo`) quietly flip from FAIL to no-FAIL. A trap that produces a
wrong answer instead of an exception is worse than no trap, so `OsEnv` is disarmed only in
`no_ambient_env`, over `check_graph` alone, where nothing reaches past this pillar. The
`env` forward through a whole run is proved the other way instead, and just as tightly:
the stub runner records the mapping it was handed, and that mapping is this file's
dictionary rather than this process's environment.

**The shell swallows exceptions per check, so the trap is also read back out of the
report.** `cmd_validate` wraps every one of its twenty-odd checks in `try/except
Exception: _emit("FAIL", …)` — that is what makes a crashing check one FAIL line instead
of a lost run, and it is not something this file should change. `PortNotForwarded` is an
`AssertionError`, so a disarmed adapter reached *inside* a check does not propagate: it
lands in `state.results` as a FAIL carrying its own traceback. The whole-run test
therefore reads those lines back and fails on them by name, and
`test_the_tripwire_is_armed` probes the check functions directly, outside the wrapper,
where the refusal is raised.

Scope: the two things `validate` does. `cmd_validate([])` is one run of the whole checker,
which reaches all four ports — the report through `out`, `check_wiki` through `clock`,
`check_graph` through `proc` and `env` — and `cmd_validate(["selftest"])`, whose presenter
travels two hops into `run_selftest`. `test_the_tripwire_is_armed` checks the mechanism, so
a green run here can never mean the trap was never set.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess

import pytest

from rig_workbench.ports import Clock, Env, Presenter, ProcessRunner
from rig_workbench.ports import local as ports_local
from rig_workbench.validation import catalog, cli as validate_cli, selftest, state

#: A day with an offset that is neither UTC nor any plausible CI machine's, so a date that
#: came from the wall clock cannot coincide with one that came from here.
FROZEN = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

#: A typed graph with nothing unresolved, so the injected runner's answer is a PASS and the
#: assertions below are about the wiring rather than about the repository's real edges.
RESOLVED_GRAPH = {
    "nodes": [{"id": "recipe:bugfix"}],
    "edges": [{"from": "recipe:bugfix", "to": "wiki:x", "rel": "links-to", "resolved": True}],
}


class PortNotForwarded(AssertionError):
    """A `validate` run reached a real adapter while holding an injected port."""


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


class StubRunner:
    """A `ProcessRunner` that answers the typed graph without starting a process."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict | None]] = []

    def run(self, argv, *, cwd=None, env=None, timeout=None, input=None,
            text=True, errors="replace"):
        self.calls.append((list(argv), None if env is None else dict(env)))
        return subprocess.CompletedProcess(
            list(argv), 0, stdout=json.dumps(RESOLVED_GRAPH), stderr="")


def test_the_injected_ports_are_ports() -> None:
    """Otherwise this file could be injecting something `validation` only tolerates."""
    assert isinstance(Recorder(), Presenter)
    assert isinstance(StubRunner(), ProcessRunner)
    assert isinstance(DictEnv({}), Env)
    assert isinstance(FrozenClock(), Clock)


@pytest.fixture(autouse=True)
def clean_results():
    """`state` accumulates into module-level counters the whole pillar shares."""
    state.results.clear()
    state._pass = state._warn = state._fail = 0
    yield
    state.results.clear()
    state._pass = state._warn = state._fail = 0


@pytest.fixture
def no_ambient_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the four real adapters the shell owns away for the length of a test.

    Patched on the **class** for the reason the module docstring gives.
    """

    def refuse(name: str):
        def refusing(*_args: object, **_kwargs: object) -> object:
            raise PortNotForwarded(
                f"a validate run reached {name} while it was holding an injected port. "
                "Some call between the shell and this one did not pass the port on, so the "
                "run is using two of them. Forward it at that call site; see "
                "rig_workbench/validation/cli.py's module docstring for the rule."
            )
        return refusing

    for cls, methods in (
        (ports_local.ConsolePresenter, ("out", "err")),
        (ports_local.SubprocessRunner, ("run",)),
        (ports_local.SystemClock, ("now", "today", "stamp")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, refuse(f"{cls.__name__}.{method}"))


@pytest.fixture
def no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OsEnv`, disarmed separately and only over `check_graph` — see the module docstring.

    A whole run reaches `packs.resolver` through `check_manifest`, and that reach is
    swallowed by `manifest._resolve`'s `except Exception: return True`, so disarming this
    adapter for a whole run produces a wrong answer rather than a refusal.
    """

    def refusing(*_args: object, **_kwargs: object) -> object:
        raise PortNotForwarded(
            "check_graph reached OsEnv while it was holding an injected Env. "
            "cmd_validate did not pass its `env` on; see "
            "rig_workbench/validation/cli.py's module docstring for the rule."
        )

    for method in ("get", "expanduser", "snapshot"):
        monkeypatch.setattr(ports_local.OsEnv, method, refusing)


def run_validate(*argv: str) -> tuple[int, Recorder, StubRunner]:
    """One `rig-wb validate` run, driven through the shell's own entry point."""
    seen, runner = Recorder(), StubRunner()
    code = validate_cli.cmd_validate(
        list(argv), out=seen, proc=runner,
        env=DictEnv({"PATH": "/usr/bin", "HOME": "/home/injected"}), clock=FrozenClock())
    return code, seen, runner


def test_the_tripwire_is_armed(no_ambient_ports: None, no_ambient_env: None) -> None:
    """The mechanism itself, so no assertion below passes because the trap was never set.

    One probe per disarmed adapter, each reaching it the way a forgotten keyword would —
    through the parameter default, not through a name this file spells. And each is
    precise: the injected port still answers.
    """
    # Presenter, through `run_selftest`'s default. It ends in `sys.exit` either way, so the
    # refusal has to be caught ahead of that — which it is, on the very first line it says.
    with pytest.raises(PortNotForwarded):
        selftest.run_selftest()

    # ProcessRunner and Env, through `check_graph`'s two defaults.
    with pytest.raises(PortNotForwarded):
        catalog.check_graph()
    with pytest.raises(PortNotForwarded):
        catalog.check_graph(proc=StubRunner())
    state.results.clear()
    catalog.check_graph(proc=StubRunner(), env=DictEnv({}))
    assert state.results == [
        "[PASS] graph: 1 nodes / 1 edges — no unresolved edges in the typed graph"]

    # Clock, through `check_wiki`'s default — reached only by a page carrying a
    # `reviewed_at`, which the shipped tier has.
    with pytest.raises(PortNotForwarded):
        catalog.check_wiki()


def test_a_whole_validate_run_speaks_and_acts_only_through_the_injected_ports(
        no_ambient_ports: None) -> None:
    """The forward that matters: one run, four ports, every hop.

    `cmd_validate` is the only verb `validate` has, so this is the whole surface. Every
    line of the report arrives on the recorder, the graph check reaches the stub runner
    rather than a process, that runner is handed the injected environment with `RIG_HOME`
    composed on top, and `check_wiki` dates its pages from the frozen day. Drop any one of
    the four keywords in `cmd_validate` and this dies on the disarmed adapter instead of
    disagreeing about a date.
    """
    code, seen, runner = run_validate()

    # First, out of the report: the shell turns any exception inside a check into a FAIL
    # line, and `PortNotForwarded` is one. A forward dropped at `check_wiki` or
    # `check_graph` shows up here, named, rather than as a puzzle three assertions down.
    refusals = [line for line in state.results if "PortNotForwarded" in line]
    assert refusals == [], refusals[0][:600]

    assert code in (0, 1), seen.text
    assert seen.out_lines[0] == "## rig --validate report (CI / shipped tier)\n"
    assert seen.err_lines == []
    assert any(line.startswith("PASS: ") for line in seen.out_lines), seen.text

    # `proc` and `env` arrived together: the port's `env=` replaces the environment, so a
    # forwarded `env` is visible as the injected mapping and not as this process's.
    assert len(runner.calls) == 1
    argv, passed = runner.calls[0]
    assert argv[1:] == [str(catalog.ROOT / "scripts" / "orchestrate.py"), "graph", "--json"]
    assert passed == {"PATH": "/usr/bin", "HOME": "/home/injected",
                      "RIG_HOME": str(catalog.ROOT)}

    # `clock` arrived: `check_wiki` ran and judged, which it can only have done by reading
    # the frozen day — `SystemClock.today` is disarmed.
    assert any(line.startswith("[PASS] wiki: ") for line in state.results), state.results


def test_the_selftest_verb_carries_the_presenter_two_hops(no_ambient_ports: None) -> None:
    """`run_selftest` is the only helper outside `cmd_validate` that speaks.

    It is two hops from the shell and it ends in `sys.exit`, so the assertion is on the
    lines that reached the recorder before it did.
    """
    seen, runner = Recorder(), StubRunner()
    with pytest.raises(SystemExit) as stopped:
        validate_cli.cmd_validate(["selftest"], out=seen, proc=runner,
                                  env=DictEnv({}), clock=FrozenClock())
    assert stopped.value.code == 0, seen.text
    assert seen.out_lines[-1].startswith("\nselftest: ")
    assert all(line.startswith("  [OK]") for line in seen.out_lines[:-1]), seen.text
    # The selftest starts no process and reads no clock, so neither port was consulted.
    assert runner.calls == []
