"""One clock per `govern` command, proved by taking the wall clock away.

`govern/cli.py` builds a `Clock` at the process boundary and hands it to every handler. That
was only half the wiring: the handlers took the port and then called into the judgement layer
without passing it on, so the port-defaulted parameters below them fell back to
`SYSTEM_CLOCK`. The reproduction was one command reading two clocks — `cmd_waiver` computed
`clock.today() + max_days` from the injected clock, `waiver.grant` validated that date against
the wall clock, and a frozen `govern waiver grant` refused the expiry it had just computed
(`[ERROR] expiry 2026-08-26 is not in the future`). Nothing user-visible, because production
passes a real `SystemClock()` at every level and two real clocks agree. That is exactly why
nothing caught it.

**These tests fail on the shape, not on the symptom.** Freezing the clock and asserting dates
would pin today's six forwarding sites and nothing else: a seventh handler added next month,
or a new call from an existing one, would read `SYSTEM_CLOCK` and still print dates that agree
with a *real* frozen clock often enough to pass. So `no_wall_clock` disarms `SystemClock`
itself — every method raises `ClockNotForwarded` — for the duration of each command. A port
that is not forwarded lands on the module default, the module default *is* the instance built
from that class, and the command dies where the forwarding stopped. The assertions on the
frozen dates are kept beside it because they say what the right answer is; the tripwire is
what says there is only one clock. `test_the_tripwire_is_armed` checks the mechanism, so a
green run here can never mean the trap was never set.

Scope: the `govern` CLI's ten verbs. `govern/enforce.py` and `govern/stage.py` declare no port
parameters at all yet — they are reached from `workbench accept` and the orchestrator, whose
shells have not been migrated — so they are not driven here and would fail the trap if they
were. That is a statement about the next pillar, not a gap in this one.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess

import pytest

from rig_workbench.govern import cli as govern_cli
from rig_workbench.govern import waiver
from rig_workbench.ports import Clock
from rig_workbench.ports import local as ports_local

#: Deliberately in the past, and it may drift further without rotting anything: every
#: assertion below is relative to this instant, and the only property the distance from
#: "now" has to keep is being non-zero — which grows rather than expires. The window, the
#: waiver lifetime and the approval expiry below are all shorter than the gap, so every date
#: this file asserts is one a wall-clock run would get wrong.
FROZEN = datetime.datetime(2026, 8, 12, 9, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
FROZEN_DAY = FROZEN.date()
FROZEN_STAMP = FROZEN.isoformat(timespec="seconds")

MAX_WAIVER_DAYS = 14
APPROVAL_EXPIRES_HOURS = 168
WINDOW_DAYS = 7


class ClockNotForwarded(AssertionError):
    """A govern command read the wall clock instead of the `Clock` it was handed."""


class FrozenClock:
    """A `Clock` stopped at `FROZEN`, offset and all.

    `stamp()` keeps the port's contract that the moment it renders carries the same offset
    `now()` does — `conformance` compares its window cutoff against stored ISO text
    *lexicographically*, and that only orders correctly when both sides agree about the zone.
    """

    def now(self) -> datetime.datetime:
        return FROZEN

    def today(self) -> datetime.date:
        return FROZEN_DAY

    def stamp(self, when: datetime.datetime | None = None) -> str:
        return (FROZEN if when is None else when).isoformat(timespec="seconds")


def test_the_frozen_clock_is_a_clock() -> None:
    """Otherwise this file could be injecting something govern only happens to tolerate."""
    assert isinstance(FrozenClock(), Clock)


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


@pytest.fixture
def no_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the wall clock away for the length of a test.

    Patched on the **class**, not on the `SYSTEM_CLOCK` name each judgement module imported.
    A default argument binds its value when the `def` is executed, so rebinding
    `waiver.SYSTEM_CLOCK` would leave every signature still holding the original instance and
    the trap would never fire — a tripwire that cannot fire is worse than none, because it
    reports the absence of a bug it could not have seen. The instance the defaults hold *is*
    a `SystemClock`, so disarming the class reaches all of them, including any adapter a
    handler builds for itself.
    """

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise ClockNotForwarded(
            "a govern command reached SystemClock while it was holding an injected Clock. "
            "Some call between the handler and this read did not pass `clock=` on, so the "
            "command is running on two clocks. Forward the port at that call site; see "
            "rig_workbench/govern/cli.py's module docstring for the rule."
        )

    for method in ("now", "today", "stamp"):
        monkeypatch.setattr(ports_local.SystemClock, method, refuse)


def test_the_tripwire_is_armed(no_wall_clock: None) -> None:
    """The mechanism itself, so no assertion below passes because the trap was never set."""
    with pytest.raises(ClockNotForwarded):
        waiver.is_active({"expires": "2999-01-01"})
    # And it is precise: the injected clock still answers.
    assert waiver.is_active({"expires": "2999-01-01"}, clock=FrozenClock())


# ── a governed repository, with one accepted run to score ────────────────────
POLICY = {
    "schema": "rig.policy/v2",
    "id": "acme",
    "scope": "org",
    "org": "acme",
    "version": "1.0.0",
    "roles": {
        "developer": ["task.new", "gate.set", "accept", "discard"],
        "reviewer": ["approve", "accept"],
        "quality-owner": ["waiver.grant", "waiver.revoke", "approve", "accept", "audit.export"],
    },
    "members": {"alice": ["quality-owner"], "bob": ["reviewer"]},
    "approvals": {
        "default": {"quorum": 0},
        "feature": {"quorum": 1, "roles": ["reviewer", "quality-owner"],
                    "separation_of_duties": True, "expires_hours": APPROVAL_EXPIRES_HOURS},
    },
    "waivers": {"max_days": MAX_WAIVER_DAYS, "grant_roles": ["quality-owner"],
                "non_waivable": ["no_secret_leak"], "required_for_force": True},
    "audit": {"chain_required": True},
}

TASK_ID = "run-frozen"


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A git repository bound to a policy, holding one accepted run the frozen clock can see.

    The run is stamped one day before `FROZEN`, which puts it inside a `--since-days 7`
    window measured from the frozen instant and far outside one measured from the wall clock.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "alice"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    (tmp_path / ".rig" / "policy").mkdir(parents=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    (tmp_path / ".rig" / "policy" / "org.json").write_text(json.dumps(POLICY), encoding="utf-8")

    run = tmp_path / ".rig" / "runs" / TASK_ID
    run.mkdir(parents=True)
    day_before = (FROZEN - datetime.timedelta(days=1)).isoformat(timespec="seconds")
    (run / "task.json").write_text(json.dumps(
        {"task_id": TASK_ID, "status": "accepted", "task_type": "feature",
         "input": "add a thing", "actor": "alice",
         "created_at": day_before, "updated_at": day_before}), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RIG_ACTOR", "alice")
    return tmp_path


def govern(*argv: str, clock: Clock | None = None) -> tuple[int, Recorder]:
    """One `rig-wb govern` command, driven through the shell's own entry point."""
    seen = Recorder()
    code = govern_cli.cmd_govern(list(argv), out=seen,
                                 clock=FrozenClock() if clock is None else clock)
    return code, seen


def waivers_on_record(repo: pathlib.Path) -> list[dict]:
    return json.loads((repo / ".rig" / "waivers.json").read_text(encoding="utf-8"))["waivers"]


def ledger_entries(repo: pathlib.Path) -> list[dict]:
    text = (repo / ".rig" / "ledger.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ── waiver: the reproduction, and the rest of what one command touches ───────
def test_waiver_grant_accepts_the_expiry_it_computed(repo: pathlib.Path,
                                                     no_wall_clock: None) -> None:
    """The verifier's finding. The default lifetime is computed from the clock the shell was
    handed, so it has to be valid against that same clock — on the wall clock this date is a
    month in the past and `waiver.grant` refuses it."""
    code, seen = govern("waiver", "grant", "w1", "--criterion", "no_todo",
                        "--reason", "tracked in issue 1")
    expected = (FROZEN_DAY + datetime.timedelta(days=MAX_WAIVER_DAYS)).isoformat()
    assert (code, seen.err_lines) == (0, [])
    assert f"until {expected}" in seen.text

    [record] = waivers_on_record(repo)
    assert record["expires"] == expected
    # The stamp on the record and the stamp in the ledger come off the same clock as the
    # expiry: the disagreement this test exists for was between two of these three.
    assert record["granted_at"] == FROZEN_STAMP
    assert [(e["action"], e["ts"]) for e in ledger_entries(repo)] == [("waiver.grant", FROZEN_STAMP)]


def test_waiver_grant_takes_a_date_that_is_the_future_only_to_the_frozen_clock(
        repo: pathlib.Path, no_wall_clock: None) -> None:
    """An explicit `--expires` the wall clock would call the past and the policy limit would
    call too far away. Only a command running entirely on the injected clock accepts it."""
    tomorrow = (FROZEN_DAY + datetime.timedelta(days=1)).isoformat()
    code, seen = govern("waiver", "grant", "w2", "--criterion", "no_todo",
                        "--reason", "one day only", "--expires", tomorrow)
    assert (code, seen.err_lines) == (0, [])
    assert waivers_on_record(repo)[0]["expires"] == tomorrow


def test_waiver_grant_still_refuses_a_date_the_frozen_clock_calls_the_past(
        repo: pathlib.Path, no_wall_clock: None) -> None:
    """The converse, so the test above cannot be passed by a `grant` that validates nothing.

    This date is in the *real* past too, so only the reason for the refusal distinguishes
    the two clocks — which is why the assertion is on the message and not merely the code.
    """
    yesterday = (FROZEN_DAY - datetime.timedelta(days=1)).isoformat()
    code, seen = govern("waiver", "grant", "w3", "--criterion", "no_todo",
                        "--reason", "already over", "--expires", yesterday)
    assert code == 1
    assert seen.err_lines == [f"[ERROR] expiry {yesterday} is not in the future"]


def test_waiver_list_reads_liveness_off_the_frozen_clock(repo: pathlib.Path,
                                                         no_wall_clock: None) -> None:
    """`[live]` / `[lapsed]` is a clock read in the shell's own body, one frame from the
    presenter. The waiver granted above has lapsed by the wall clock and has not by this one."""
    govern("waiver", "grant", "w1", "--criterion", "no_todo", "--reason", "tracked in issue 1")
    code, seen = govern("waiver", "list")
    expected = (FROZEN_DAY + datetime.timedelta(days=MAX_WAIVER_DAYS)).isoformat()
    assert code == 0
    assert any(line.strip().startswith("[live] w1") for line in seen.out_lines), seen.text
    assert f"until {expected}" in seen.text


def test_waiver_revoke_stamps_the_frozen_moment(repo: pathlib.Path, no_wall_clock: None) -> None:
    govern("waiver", "grant", "w1", "--criterion", "no_todo", "--reason", "tracked in issue 1")
    code, _ = govern("waiver", "revoke", "w1", "--reason", "fixed")
    assert code == 0
    assert waivers_on_record(repo)[0]["revoked_at"] == FROZEN_STAMP
    assert [e["ts"] for e in ledger_entries(repo)] == [FROZEN_STAMP, FROZEN_STAMP]


# ── approval: a decision that ages ───────────────────────────────────────────
def test_approve_records_and_counts_a_decision_at_the_frozen_moment(
        repo: pathlib.Path, no_wall_clock: None) -> None:
    """`expires_hours` makes the approval status a clock read as well as a stamp.

    The decision is written at `FROZEN` and evaluated at `FROZEN`, so it is nought hours old
    and counts. Evaluated against the wall clock it is hundreds of hours old, past the
    168-hour rule, and the same command would print `0/1 … not yet satisfied`.
    """
    code, seen = govern("approve", "grant", TASK_ID, "--actor", "bob", "--note", "looks right")
    assert (code, seen.err_lines) == (0, [])
    assert any("approvals: 1/1" in line and "satisfied" in line for line in seen.out_lines), seen.text

    recorded = json.loads((repo / ".rig" / "runs" / TASK_ID / "approvals.json")
                          .read_text(encoding="utf-8"))
    assert [d["ts"] for d in recorded["decisions"]] == [FROZEN_STAMP]
    assert [(e["action"], e["ts"]) for e in ledger_entries(repo)] == [
        ("approval.grant", FROZEN_STAMP)]


# ── ledger and conformance: the stamp and the window ─────────────────────────
def test_audit_shows_the_entries_the_frozen_clock_stamped(repo: pathlib.Path,
                                                          no_wall_clock: None) -> None:
    govern("waiver", "grant", "w1", "--criterion", "no_todo", "--reason", "tracked in issue 1")
    code, seen = govern("audit")
    assert code == 0
    assert any(FROZEN_STAMP in line for line in seen.out_lines), seen.text

    verified, _ = govern("audit", "verify")
    assert verified == 0


def test_conformance_windows_and_ages_everything_by_the_frozen_clock(
        repo: pathlib.Path, no_wall_clock: None) -> None:
    """Three clock reads in one report, two of them inside the judgement layer.

    `_in_window` decides the run is recent; `_check_approvals` decides the approval on it has
    not aged past `expires_hours`; `_check_waivers` decides the waiver is live. Only the first
    was ever handed the report's clock — the other two took `files` and nothing else, so a
    frozen report scored a windowed set of records against the wall clock. Measured against
    the wall clock, every one of these three flips: the run leaves a 7-day window, the
    approval expires, and the waiver lapses.
    """
    govern("approve", "grant", TASK_ID, "--actor", "bob", "--note", "ok")
    govern("waiver", "grant", "w1", "--criterion", "no_todo", "--reason", "tracked in issue 1")

    code, seen = govern("conformance", "--json", "--since-days", str(WINDOW_DAYS))
    assert code == 0
    report = json.loads("\n".join(seen.out_lines))
    checks = {c["id"]: c for c in report["checks"]}

    assert report["task_records"]["in_window"] == 1
    assert checks["force_rate"]["verdict"] == "pass"
    assert "0/1 accepted runs were forced" in checks["force_rate"]["detail"]
    assert checks["approvals"]["verdict"] == "pass"
    assert "1 accepted run(s) in the window satisfied it" in checks["approvals"]["detail"]
    assert checks["waivers"]["verdict"] == "warn"
    assert "1 live waiver(s), 0 lapsed" in checks["waivers"]["detail"]


def test_rollup_hands_the_same_clock_to_every_project(repo: pathlib.Path,
                                                      no_wall_clock: None) -> None:
    """`rollup` scores several repositories; one wall-clock read anywhere under it would make
    the report a mixture of two moments."""
    govern("approve", "grant", TASK_ID, "--actor", "bob", "--note", "ok")
    govern("waiver", "grant", "w1", "--criterion", "no_todo", "--reason", "tracked in issue 1")
    code, seen = govern("rollup", str(repo), "--json", "--since-days", str(WINDOW_DAYS))
    assert code == 0
    report = json.loads("\n".join(seen.out_lines))
    [project] = report["reports"]
    by_id = {c["id"]: c["verdict"] for c in project["checks"]}
    assert (by_id["waivers"], by_id["approvals"], by_id["force_rate"]) == ("warn", "pass", "pass")
    assert project["task_records"]["in_window"] == 1


# ── the verbs with no clock of their own ─────────────────────────────────────
@pytest.mark.parametrize("argv", [
    ("policy", "show"),
    ("policy", "lint"),
    ("whoami",),
    ("can", "waiver.grant"),
    ("audit", "verify"),
    ("waiver", "list"),
])
def test_no_verb_reaches_for_the_wall_clock(repo: pathlib.Path, no_wall_clock: None,
                                            argv: tuple[str, ...]) -> None:
    """The other half of the claim. A command that needs no clock must not read one either —
    that is what makes `govern` freezable as a whole rather than verb by verb."""
    code, seen = govern(*argv)
    assert (code, seen.err_lines) == (0, [])


def test_init_stamps_its_own_ledger_entry_from_the_injected_clock(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_wall_clock: None) -> None:
    """`govern init` on a fresh repository: the first entry the ledger ever gets."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RIG_ACTOR", "alice")
    code, seen = govern("init", "--org", "acme", "--team", "team-a")
    assert (code, seen.err_lines) == (0, [])
    assert [(e["action"], e["ts"]) for e in ledger_entries(tmp_path)] == [
        ("policy.init", FROZEN_STAMP)]
