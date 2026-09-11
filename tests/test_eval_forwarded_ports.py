"""One clock, one runner, one environment per `eval` command, proved by taking them away.

`eval/cli.py` builds four adapters at the process boundary and hands them to the verbs.
That is only half the wiring, and the half that was already true of `govern/cli.py` on the
day it shipped a bug: a handler that takes a port and then calls into the judgement layer
without passing it on leaves the *callee's default* in charge, and the default is the real
adapter. `tests/test_govern_frozen_clock.py` names the reproduction — `cmd_waiver` computed
an expiry from the injected clock and handed it to a `waiver.grant` reading the wall clock,
so a frozen command refused the date it had just computed. Nothing user-visible, because
production passes real adapters at every level and two real clocks agree. That is exactly
why nothing caught it.

**These tests fail on the shape, not on the symptom.** Freezing the clock and asserting
dates would pin today's forwarding sites and nothing else; a call added next month would
read `SYSTEM_CLOCK` and still produce dates that agree with a real frozen clock often
enough to pass. So the fixtures below disarm `SystemClock`, `SubprocessRunner`, `OsEnv` and
`ConsolePresenter` themselves — every method raises — for the length of each command. A
port that is not forwarded lands on the module default, the module default *is* an instance
of that class, and the command dies where the forwarding stopped.

**Disarmed on the class, not on the name each module imported.** A default argument binds
its value when the `def` is executed, so rebinding `affected.SUBPROCESS` would leave every
signature still holding the original instance and the trap would never fire — a tripwire
that cannot fire is worse than none, because it reports the absence of a bug it could not
have seen. The instances the defaults hold *are* instances of these classes, so disarming
the class reaches all of them, including any adapter a shell builds for itself.

The assertions on the frozen answers are kept beside the trap because they say what the
right answer is. The frozen instant is deliberately more than `MAX_RESULT_AGE` in the past,
so a run measured by this clock and compared by the wall clock is not merely different: it
is refused as stale. `test_the_tripwire_is_armed` checks the mechanism, so a green run here
can never mean the trap was never set.

Scope: the verbs that can be driven without a paid provider — `list`, `validate`,
`affected`, `gate`, `run`, `compare`, `promote`, `reproduce`. `affected-run` forbids the
mock provider by design, so it cannot be driven here; it is the one verb whose forwarding
is asserted only by reading (`run_affected` takes all four and passes them on).
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
import subprocess

import pytest

from test_eval_cases import valid_case

from rig_workbench.eval import cli as eval_cli
from rig_workbench.eval.cases import canonical_json
from rig_workbench.ports import Clock, Env, Presenter, ProcessRunner
from rig_workbench.ports import local as ports_local

#: More than `compare.MAX_RESULT_AGE` (30 days) before any plausible run of this suite, and
#: it may drift further without rotting anything: every assertion below is relative to this
#: instant, and the only property the distance from "now" has to keep is being larger than
#: that window — which grows rather than expires. A result stamped here and validated
#: against the wall clock is `evaluation result is stale`; against this clock it is nought
#: seconds old.
FROZEN = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
FROZEN_UTC_STAMP = FROZEN.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")

#: 64 hex characters, the shape `RIG_EVAL_ATTESTATION_KEY` has to have. Handed to the
#: commands through the `Env` port and deliberately **not** put into `os.environ`: a command
#: that reaches past the port finds no key, and would mint one in the state directory.
KEY = "819804239a829011972226e7978766152de9a2fa10500de2f4515476505fee16"

PERSONA_REL = "skills/engine/facets/personas/reviewer.md"


class PortNotForwarded(AssertionError):
    """An `eval` command reached a real adapter while holding an injected port."""


class FrozenClock:
    """A `Clock` stopped at `FROZEN`, offset and all."""

    def now(self) -> dt.datetime:
        return FROZEN

    def today(self) -> dt.date:
        return FROZEN.date()

    def stamp(self, when: dt.datetime | None = None) -> str:
        return (FROZEN if when is None else when).isoformat(timespec="seconds")


class DictEnv:
    """An `Env` over a dictionary this file owns, holding the attestation key and nothing else."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._values.get(name, default)

    def expanduser(self, path: str) -> str:
        return path

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)


class RealRunner:
    """A `ProcessRunner` that really runs the process, written here rather than imported.

    It cannot be `SubprocessRunner`: that class is disarmed for the length of every test
    below, which is the whole mechanism. So this is a second implementation of the same
    contract, and `test_the_injected_runner_is_a_runner` checks it satisfies the protocol.
    """

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def run(self, argv, *, cwd=None, env=None, timeout=None, input=None,
            text=True, errors="replace"):
        self.argvs.append(list(argv))
        decoding = {"encoding": "utf-8", "errors": errors} if text else {}
        return subprocess.run(list(argv), cwd=None if cwd is None else str(cwd),
                              env=None if env is None else dict(env), timeout=timeout,
                              input=input, capture_output=True, **decoding)


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


def test_the_injected_ports_are_ports() -> None:
    """Otherwise this file could be injecting something eval only happens to tolerate."""
    assert isinstance(FrozenClock(), Clock)
    assert isinstance(DictEnv({}), Env)
    assert isinstance(Recorder(), Presenter)


def test_the_injected_runner_is_a_runner() -> None:
    assert isinstance(RealRunner(), ProcessRunner)


@pytest.fixture
def no_ambient_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the real adapters away for the length of a test.

    Patched on the **class** for the reason the module docstring gives. `ConsolePresenter`
    is in the set as well: the shell builds one only when no presenter is passed, so a
    `ConsolePresenter` waking up during a test means a second one was constructed somewhere
    below the shell, which is the same defect wearing the presenter's clothes.
    """

    def refuse(name: str):
        def refusing(*_args: object, **_kwargs: object) -> object:
            raise PortNotForwarded(
                f"an eval command reached {name} while it was holding an injected port. "
                "Some call between the shell and this one did not pass the port on, so the "
                "command is running on two of them. Forward it at that call site; see "
                "rig_workbench/eval/cli.py's module docstring for the rule."
            )
        return refusing

    for cls, methods in (
        (ports_local.SystemClock, ("now", "today", "stamp")),
        (ports_local.SubprocessRunner, ("run",)),
        (ports_local.OsEnv, ("get", "expanduser", "snapshot")),
        (ports_local.ConsolePresenter, ("out", "err")),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, refuse(f"{cls.__name__}.{method}"))


def test_the_tripwire_is_armed(no_ambient_ports: None, tmp_path: pathlib.Path) -> None:
    """The mechanism itself, so no assertion below passes because the trap was never set.

    One probe per disarmed adapter, each reaching it the way a forgotten `proc=` / `env=` /
    `clock=` would — through the parameter default, not through a name this file spells.
    And each is precise: the injected port still answers.
    """
    from rig_workbench.eval import attestation, capture
    from rig_workbench.eval.affected import _merge_base

    with pytest.raises(PortNotForwarded):
        capture._now_iso(None)
    assert capture._now_iso(None, clock=FrozenClock()) == (
        FROZEN.astimezone(dt.timezone.utc).isoformat(timespec="seconds"))

    with pytest.raises(PortNotForwarded):
        _merge_base(tmp_path, "HEAD", "working")
    assert _merge_base(tmp_path, "HEAD", "working", proc=RealRunner()) == "HEAD"

    with pytest.raises(PortNotForwarded):
        attestation._trusted_key(create=False)
    assert attestation._trusted_key(create=False, env=DictEnv({"RIG_EVAL_ATTESTATION_KEY": KEY})) \
        == bytes.fromhex(KEY)


# ── a repository with one draft case and one uncovered prompt surface ────────


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _draft_case() -> dict:
    case = copy.deepcopy(valid_case())
    case["status"] = "draft"
    case["id"] = "forwarded-ports"
    case["provenance"]["source_task_id"] = "rig-20260601-forwarded"
    case["red_thresholds"] = {"max_success_rate": 1 / 3}
    case["deterministic_checks"] = ["contains:scenario"]
    # A rubric, because an approved case must carry one — so `promote` only passes if the
    # judge really ran, which puts a second process (and `Env.snapshot`, which builds its
    # child environment) inside the flow.
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    return case


#: A judge that answers the rubric without a model: the `command` provider, started by the
#: injected runner like every other process in these tests.
JUDGE = ["--judge-provider", "command", "--judge-model", "fixture", "--judge-command",
         'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
         '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"']


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A git repository holding a draft case and, after the base commit, an edited persona.

    The persona makes `affected` and `gate` answer about a real surface rather than about
    nothing, and it has no case, so the ratchet calls it debt and the verbs exit 0.
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "eval@test.invalid")
    _git(tmp_path, "config", "user.name", "eval-test")
    (tmp_path / ".eval-root").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", ".eval-root")
    _git(tmp_path, "commit", "-q", "-m", "base")

    case = _draft_case()
    draft = tmp_path / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(canonical_json(case), encoding="utf-8")

    persona = tmp_path / PERSONA_REL
    persona.parent.mkdir(parents=True)
    persona.write_text("---\nname: reviewer\n---\nread it closely\n", encoding="utf-8")

    # Nothing in the ambient environment may carry the key: the commands are meant to read
    # it off the `Env` they are handed, and a leftover variable would hide a missed forward.
    monkeypatch.delenv("RIG_EVAL_ATTESTATION_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def base_commit(repo: pathlib.Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


def run_eval(*argv: str, runner: RealRunner | None = None) -> tuple[int, Recorder]:
    """One `rig-wb eval` command, driven through the shell's own entry point."""
    seen = Recorder()
    code = eval_cli.cmd_eval(list(argv), out=seen, proc=runner or RealRunner(),
                             env=DictEnv({"RIG_EVAL_ATTESTATION_KEY": KEY}),
                             clock=FrozenClock())
    return code, seen


# ── the verbs ────────────────────────────────────────────────────────────────


def test_list_and_validate_speak_only_through_the_presenter(repo: pathlib.Path,
                                                            no_ambient_ports: None) -> None:
    """The two read-only verbs. `ConsolePresenter` is disarmed, so a stray `print` through
    a second presenter ends the command rather than reaching a captured stream."""
    code, seen = run_eval("list", "--repo", str(repo))
    assert code == 0
    assert any(line.startswith("forwarded-ports\t") for line in seen.out_lines), seen.text

    code, seen = run_eval("validate")
    assert (code, seen.err_lines) == (0, [])
    assert seen.out_lines[-1] == "1 case(s) valid"


def test_affected_reads_git_only_through_the_runner_it_was_handed(
        repo: pathlib.Path, no_ambient_ports: None) -> None:
    """`affected` is the verb with the most git in it — merge base, diff, untracked
    listing, rev-parse, ls-tree, cat-file, show. Every one of them has to arrive at the
    runner this test built, or the disarmed `SubprocessRunner` ends the command."""
    runner = RealRunner()
    code, seen = run_eval("affected", "--base", base_commit(repo), "--ratchet",
                          "--repo", str(repo), runner=runner)
    report = json.loads("\n".join(seen.out_lines))
    assert code == 0
    assert report["status"] == "debt" and report["coverage_debt"] == [PERSONA_REL]
    # Non-vacuous: the run really did start git processes, and through this object.
    assert [argv for argv in runner.argvs if argv[:2] == ["git", "merge-base"]]


def test_gate_carries_the_runner_through_the_analysis_it_gates(
        repo: pathlib.Path, no_ambient_ports: None) -> None:
    code, seen = run_eval("gate", "--base", base_commit(repo), "--ratchet",
                          "--evidence-dir", str(repo / "evals" / "evidence"),
                          "--repo", str(repo))
    report = json.loads("\n".join(seen.out_lines))
    assert code == 0, seen.text
    assert report["coverage_debt"] == [PERSONA_REL]


def test_a_run_compare_and_promote_all_happen_at_the_frozen_moment(
        repo: pathlib.Path, no_ambient_ports: None) -> None:
    """The whole measurement flow on one injected clock, one runner and one environment.

    Every stamp is the frozen instant; the comparison's window is measured from the same
    instant, so the evidence is nought seconds old. Measured against the wall clock the
    same evidence is months past `MAX_RESULT_AGE` and `compare` refuses it as stale — which
    is what makes this more than a formatting assertion.

    The attestation is the environment's half: the key arrives through the injected `Env`
    and through nothing else, so a signature that verifies is proof the port was forwarded
    from `run_case` all the way into `attestation.sign_result_attestation` and back out
    through `compare.validate_result`.
    """
    common = ["forwarded-ports", "--provider", "mock", "--model", "fixture",
              "--repeat", "3", "--repo", str(repo)]
    code, seen = run_eval("run", *common, "--phase", "baseline", *JUDGE)
    assert (code, seen.err_lines) == (0, []), seen.text
    baseline_path = pathlib.Path(seen.out_lines[-1])
    code, seen = run_eval("run", *common, "--phase", "current", *JUDGE)
    assert (code, seen.err_lines) == (0, []), seen.text
    current_path = pathlib.Path(seen.out_lines[-1])

    measured = json.loads(current_path.read_text(encoding="utf-8"))
    assert measured["started_at"] == FROZEN_UTC_STAMP
    assert measured["attestation"]["algorithm"] == "HMAC-SHA256"

    code, seen = run_eval("compare", "--baseline", str(baseline_path),
                          "--current", str(current_path), "--repo", str(repo))
    assert code == 0, seen.text
    assert json.loads("\n".join(seen.out_lines))["status"] == "pass"

    code, seen = run_eval("promote", "forwarded-ports", "--baseline", str(baseline_path),
                          "--current", str(current_path), "--repo", str(repo))
    assert (code, seen.err_lines) == (0, []), seen.text
    promoted = json.loads((repo / "evals" / "cases" / "forwarded-ports" / "case.json")
                          .read_text(encoding="utf-8"))
    assert promoted["status"] == "approved"
    assert promoted["updated_at"] == FROZEN.astimezone(dt.timezone.utc).isoformat(
        timespec="seconds")


def test_the_frozen_evidence_is_evidence_the_wall_clock_would_refuse(
        repo: pathlib.Path, no_ambient_ports: None) -> None:
    """The negative control for the test above, and the reason the frozen instant is old.

    Validated against a clock reading *now*, the very result `compare` just accepted is
    stale. So "the flow passed" is a statement about which clock every step used, not a
    property the fixture would have had anyway.
    """
    from rig_workbench.eval.cases import EvalCaseError
    from rig_workbench.eval.compare import validate_result

    common = ["forwarded-ports", "--provider", "mock", "--model", "fixture",
              "--repeat", "3", "--repo", str(repo), "--phase", "current"]
    code, seen = run_eval("run", *common, *JUDGE)
    assert code == 0, seen.text
    result = json.loads(pathlib.Path(seen.out_lines[-1]).read_text(encoding="utf-8"))

    environment = DictEnv({"RIG_EVAL_ATTESTATION_KEY": KEY})
    validate_result(result, clock=FrozenClock(), env=environment)
    with pytest.raises(EvalCaseError, match="stale"):
        validate_result(result, now=dt.datetime.now(dt.timezone.utc), env=environment)


def test_reproduce_drives_a_draft_on_the_injected_ports(repo: pathlib.Path,
                                                        no_ambient_ports: None) -> None:
    """`reproduce` is `run` with the draft's own repeat and a red/green verdict on top.

    It exits 1 here because `--allow-mock` marks the run a dev probe, which is the exit the
    verb is documented to give; what is being measured is that it reaches that answer
    without touching a real adapter.
    """
    code, seen = run_eval("reproduce", "forwarded-ports", "--provider", "mock",
                          "--model", "fixture", "--allow-mock", "--repo", str(repo),
                          *JUDGE)
    assert (code, seen.err_lines) == (1, []), seen.text
    result = json.loads(pathlib.Path(seen.out_lines[-1]).read_text(encoding="utf-8"))
    assert result["started_at"] == FROZEN_UTC_STAMP
