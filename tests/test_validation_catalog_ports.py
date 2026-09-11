"""`catalog.py`'s three effects, checked at the two places their answers can change.

`check_wiki` and `check_graph` were the whole of this pillar's non-`print` scatter: one
`subprocess.run`, one `os.environ` spread feeding it, and one `datetime.date.today()`.
They are now `ProcessRunner`, `Env` and `Clock` parameters with the real adapters as
defaults, and this file is what makes that more than a rename.

**The freshness boundary is pinned on both sides.** `check_wiki` warns when
`(today - reviewed_at).days > 180`, so 180 days is silent and 181 days warns, and those
are the two days below. A single frozen date that happens to produce the expected verdict
would prove nothing: it would pass just as happily against a wall clock, on every day but
two per page. Pinning the pair is what makes the assertion a statement about the rule
rather than about the afternoon it was written. The frozen instant carries an offset that
is neither UTC nor any plausible CI machine's, so a date that came from the real clock
cannot coincide with one that came from here.

**The environment is checked for composition, not for presence.** `ProcessRunner.run`'s
`env=` *replaces* the environment rather than adding to it — `ports/__init__.py` says so
and `subprocess` has always behaved that way — so the migration of

    env={**os.environ, "RIG_HOME": str(ROOT)}

is only equivalent if the call still spreads a full snapshot underneath the one variable
it sets. Asserting that `RIG_HOME` is in there would pass for the mapping
`{"RIG_HOME": …}` alone, which is a child process with no `PATH` and a different command.
So the recording runner below is handed an `Env` this file owns, and the assertion is that
every one of its variables arrived.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess

import pytest

from rig_workbench.ports import Clock, Env, ProcessRunner
from rig_workbench.validation import catalog, state

#: The day `check_wiki` is told it is. Offset +09:00 so nothing here can coincide with a
#: real clock read, and far enough out that nothing about it expires.
FROZEN = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))

#: The two sides of `> 180`. Inside is silent, outside warns; they differ by one day.
JUST_INSIDE_DAYS = 180
JUST_OUTSIDE_DAYS = 181


class FrozenClock:
    """A `Clock` stopped at `FROZEN`, offset and all."""

    def now(self) -> dt.datetime:
        return FROZEN

    def today(self) -> dt.date:
        return FROZEN.date()

    def stamp(self, when: dt.datetime | None = None) -> str:
        return (FROZEN if when is None else when).isoformat(timespec="seconds")


class DictEnv:
    """An `Env` over a dictionary this file owns, so the snapshot is checkable."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = dict(values)

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._values.get(name, default)

    def expanduser(self, path: str) -> str:
        return path

    def snapshot(self) -> dict[str, str]:
        return dict(self._values)


class RecordingRunner:
    """A `ProcessRunner` that answers a canned graph and keeps how it was called."""

    def __init__(self, payload: dict, returncode: int = 0, stderr: str = "") -> None:
        self._payload = payload
        self._returncode = returncode
        self._stderr = stderr
        self.calls: list[tuple[list[str], dict | None]] = []

    def run(self, argv, *, cwd=None, env=None, timeout=None, input=None,
            text=True, errors="replace"):
        self.calls.append((list(argv), None if env is None else dict(env)))
        return subprocess.CompletedProcess(
            list(argv), self._returncode,
            stdout=json.dumps(self._payload), stderr=self._stderr)


@pytest.fixture(autouse=True)
def clean_results():
    """`state` accumulates into module-level counters shared by the whole pillar."""
    state.results.clear()
    state._pass = state._warn = state._fail = 0
    yield
    state.results.clear()
    state._pass = state._warn = state._fail = 0


def test_the_injected_ports_are_ports() -> None:
    """Otherwise this file could be injecting something `catalog` only happens to tolerate."""
    assert isinstance(FrozenClock(), Clock)
    assert isinstance(DictEnv({}), Env)
    assert isinstance(RecordingRunner({}), ProcessRunner)


# ── check_wiki: the 180-day boundary ─────────────────────────────────────────


def _wiki_page(monkeypatch, tmp_path, slug: str, reviewed_at: dt.date) -> None:
    """A shipped-tier wiki page with one `reviewed_at`, in a tree this test owns.

    `check_wiki` reads `FACETS / "knowledge" / "wiki"` off the module global at call time,
    so pointing `FACETS` at a temporary directory is enough; nothing about the real
    repository's pages, whose dates move, can reach the assertions.
    """
    wiki = tmp_path / "knowledge" / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / f"{slug}.md").write_text(
        f"---\nslug: {slug}\nstatus: canonical\nreviewed_at: {reviewed_at.isoformat()}\n"
        f"---\n\n# {slug}\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "FACETS", tmp_path)


@pytest.mark.parametrize("days, expect_warning", [
    (JUST_INSIDE_DAYS, False),
    (JUST_OUTSIDE_DAYS, True),
])
def test_the_freshness_rule_is_pinned_on_both_sides_of_180_days(
        monkeypatch, tmp_path, days: int, expect_warning: bool) -> None:
    """180 days is silent, 181 warns — the rule, not the day this ran.

    Both cases run against the same frozen clock and differ only in the page's date, so a
    `check_wiki` that had kept reading the wall clock would fail whichever way today fell:
    the pair cannot both be right by accident.
    """
    reviewed = FROZEN.date() - dt.timedelta(days=days)
    _wiki_page(monkeypatch, tmp_path, "freshness", reviewed)

    catalog.check_wiki(clock=FrozenClock())

    warnings = [line for line in state.results if line.startswith("[WARN]")]
    assert bool(warnings) is expect_warning, state.results
    if expect_warning:
        assert warnings == [
            f"[WARN] wiki freshness — reviewed_at is over 180 days old ({reviewed}): "
            "review and update the content or mark it deprecated (knowledge freshness)"]
    # Either way the page is schema-clean: the WARN is freshness, not hygiene.
    assert "[PASS] wiki: 1/1 schema OK (shipped tier)" in state.results
    assert [line for line in state.results if line.startswith("[FAIL]")] == []


def test_a_page_with_no_reviewed_at_is_not_dated_at_all(monkeypatch, tmp_path) -> None:
    """The third case the boundary pair does not cover, so the clock is not consulted."""
    wiki = tmp_path / "knowledge" / "wiki"
    wiki.mkdir(parents=True)
    (wiki / "undated.md").write_text(
        "---\nslug: undated\nstatus: canonical\n---\n\n# undated\n", encoding="utf-8")
    monkeypatch.setattr(catalog, "FACETS", tmp_path)

    catalog.check_wiki(clock=FrozenClock())
    assert state.results == ["[PASS] wiki: 1/1 schema OK (shipped tier)"]


# ── check_graph: the runner and the environment it is handed ─────────────────


RESOLVED_GRAPH = {
    "nodes": [{"id": "recipe:bugfix"}],
    "edges": [{"from": "recipe:bugfix", "to": "wiki:x", "rel": "links-to", "resolved": True}],
}


def test_the_graph_check_runs_through_the_port_it_is_handed(tmp_path) -> None:
    runner = RecordingRunner(RESOLVED_GRAPH)
    catalog.check_graph(proc=runner, env=DictEnv({"PATH": "/usr/bin", "HOME": "/home/x"}))

    assert len(runner.calls) == 1
    argv, _ = runner.calls[0]
    assert argv[1:] == [str(catalog.ROOT / "scripts" / "orchestrate.py"), "graph", "--json"]
    assert state.results == [
        "[PASS] graph: 1 nodes / 1 edges — no unresolved edges in the typed graph"]


def test_the_child_gets_the_whole_environment_plus_rig_home(tmp_path) -> None:
    """`env=` replaces rather than extends, so the snapshot has to still be underneath."""
    ambient = {"PATH": "/usr/bin", "HOME": "/home/x", "LANG": "C.UTF-8"}
    runner = RecordingRunner(RESOLVED_GRAPH)
    catalog.check_graph(proc=runner, env=DictEnv(ambient))

    _, passed = runner.calls[0]
    assert passed == {**ambient, "RIG_HOME": str(catalog.ROOT)}


def test_an_ambient_rig_home_is_the_one_thing_the_call_overrides() -> None:
    """The composition order is load-bearing: `RIG_HOME` wins over what was inherited."""
    runner = RecordingRunner(RESOLVED_GRAPH)
    catalog.check_graph(proc=runner, env=DictEnv({"RIG_HOME": "/somewhere/else"}))

    _, passed = runner.calls[0]
    assert passed == {"RIG_HOME": str(catalog.ROOT)}


def test_a_failing_child_is_reported_from_the_ports_status_and_stderr() -> None:
    """The port never raises on a non-zero status, so this branch is still reachable."""
    runner = RecordingRunner({}, returncode=2, stderr="boom")
    catalog.check_graph(proc=runner, env=DictEnv({}))
    assert state.results == ["[FAIL] graph — orchestrate.py graph --json failed: boom"]


def test_an_unresolved_wiki_link_is_still_a_fail() -> None:
    """The check's own judgement, driven entirely through the injected runner."""
    runner = RecordingRunner({
        "nodes": [{"id": "recipe:bugfix"}],
        "edges": [{"from": "recipe:bugfix", "to": "wiki:ghost", "rel": "links-to",
                   "resolved": False}],
    })
    catalog.check_graph(proc=runner, env=DictEnv({}))
    assert state.results == [
        "[FAIL] graph — broken wiki link: recipe:bugfix → [[ghost]] does not exist"]
