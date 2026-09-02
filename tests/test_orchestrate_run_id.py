"""An orchestrate run had no identity of any kind.

The state carried `recipe`, `goal`, `steps` and `cursor`; the telemetry record in
`.rig/runs.jsonl` identified a run by timestamp, recipe and project. Two consequences, and
the second is the one that bites.

A board row cannot point at the run behind it, because there is nothing to point with — which
is why Mission Control's cross-project view could not be built on this log at all.

And two runs of the same recipe starting in the same second had one record's worth of identity
between them. Not theoretical: the queue dispatches items in parallel by design, so the case
this cannot distinguish is exactly the case the feature exists for.

A workbench task avoids the second problem by accident — `make_task_id` has no collision guard
either, but creating the task directory surfaces a clash. An orchestrate run creates no
directory, so nothing would have surfaced.
"""

from __future__ import annotations

import datetime
import json

from rig_workbench.orchestrate import runstate
from rig_workbench.orchestrate.runstate import (
    RUN_ID_RE, append_run_record, load_state, make_run_id, new_state, save_state,
    telemetry_append,
)

STEPS = [{"id": "one", "gate": "manual"}]


def _state(recipe: str = "demo-recipe") -> dict:
    return new_state(recipe, [dict(step) for step in STEPS], "a goal")


def test_a_new_run_carries_an_id_of_the_documented_shape():
    """`orc-` and not `rig-`: a workbench task and an orchestrate run are different execution
    models, and a joined board should not have to guess which kind of thing a row is."""
    state = _state()

    assert RUN_ID_RE.match(state["run_id"]), state["run_id"]
    assert state["run_id"].startswith("orc-")


def test_two_runs_of_one_recipe_in_the_same_second_are_told_apart():
    """The reason the id carries a random suffix rather than being a timestamp and a name.
    The queue dispatches in parallel, so same-second starts of the same recipe are the normal
    case, and without this they would land in the log as one run's identity shared by two."""
    ids = {_state("bugfix")["run_id"] for _ in range(50)}

    assert len(ids) == 50


def test_a_resumed_run_is_the_same_run(tmp_path):
    """Created once in `new_state` and carried through, so telemetry for a run that stopped
    and continued is one run's telemetry. A second id would split it in two and make the board
    show a run that never finished beside one that started from nowhere.

    The id is captured *before* the first save, and every later assertion compares against that
    captured value rather than against `state["run_id"]`. An earlier version of this test did
    the latter and could not see a `save_state` that re-minted the id in place — the written
    file and the in-memory dict agreed with each other, both being wrong. Found by mutation.

    The sequence is a real resume: write, read back, advance, write, read back."""
    state = _state()
    original = state["run_id"]
    path = tmp_path / "run-state.json"

    save_state(state, path)
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == original

    resumed = load_state(path)
    assert resumed["run_id"] == original

    resumed["cursor"] = 1
    save_state(resumed, path)
    assert load_state(path)["run_id"] == original


def test_the_id_sorts_chronologically(monkeypatch):
    """The board orders by it. Lexical order has to match time order down to the second, which
    is what the fixed-width `%Y%m%d-%H%M%S` prefix buys; within one second the suffix decides,
    and no order is claimed there because none is known."""
    stamps = ["20260101-000000", "20260829-114455", "20261231-235959"]
    produced = []
    for stamp in stamps:
        class _Frozen(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        monkeypatch.setattr(runstate.datetime, "datetime", _Frozen)
        produced.append(make_run_id("demo"))

    assert produced == sorted(produced)


def test_the_telemetry_record_carries_the_id(tmp_path, monkeypatch):
    """The point of the whole change: the log gains the key that lets a row be resolved back
    to the run it summarises."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runstate.config, "RUNS_PATH", tmp_path / ".rig" / "runs.jsonl")
    state = _state()
    state["step_state"]["one"]["status"] = "passed"

    telemetry_append(state, "DONE")

    record = json.loads((tmp_path / ".rig" / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert record["run_id"] == state["run_id"]


def test_a_run_state_written_before_run_ids_omits_the_key_rather_than_nulling_it(
        tmp_path, monkeypatch):
    """An absent measurement and a measurement of nothing are different facts, and this log is
    read by aggregation that treats a present key as measured. The same rule `perf` already
    follows — a record carrying `"perf": {}` would read as 'timed, and it took nothing'."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runstate.config, "RUNS_PATH", tmp_path / ".rig" / "runs.jsonl")
    state = _state()
    del state["run_id"]                     # a state file from before this existed
    state["step_state"]["one"]["status"] = "passed"

    telemetry_append(state, "DONE")

    record = json.loads((tmp_path / ".rig" / "runs.jsonl").read_text(encoding="utf-8").strip())
    assert "run_id" not in record


def test_an_odd_recipe_name_still_produces_a_usable_id():
    """The slug is derived from a recipe name, which is author-supplied. An empty or
    punctuation-only name must not produce an id that fails its own shape check."""
    for name, expected in (("", "run"), ("   ", "run"), ("Review / Diff!!", "review-diff"),
                           ("x" * 80, "x" * 32)):
        run_id = make_run_id(name)
        assert RUN_ID_RE.match(run_id), (name, run_id)
        assert run_id.split("-", 3)[3].rsplit("-", 1)[0] == expected


def test_the_global_mirror_gets_the_id_too(tmp_path, monkeypatch):
    """Cross-project rollup is the reason this log is mirrored to `~/.rig/runs.jsonl`, and the
    cross-project board is the reason the id exists. A record that carried it only locally
    would leave the board exactly where it started."""
    # No `raising=False`: if this attribute is ever renamed the patch must fail here rather
    # than quietly create a new name and leave the real mirror untouched.
    mirror = tmp_path / "home" / ".rig" / "runs.jsonl"
    monkeypatch.setattr(runstate.config, "GLOBAL_RUNS_PATH", mirror)
    local = tmp_path / "project" / ".rig" / "runs.jsonl"
    state = _state()

    append_run_record({"run_id": state["run_id"], "recipe": "demo", "backend": "orchestrate"},
                      runs_path=local, project=tmp_path / "project")

    mirrored = json.loads(mirror.read_text(encoding="utf-8").strip())
    assert mirrored["run_id"] == state["run_id"]
    assert mirrored["project"] == str(tmp_path / "project")
    assert json.loads(local.read_text(encoding="utf-8").strip())["run_id"] == state["run_id"]


def test_the_providers_a_run_was_configured_with_reach_the_record(tmp_path, monkeypatch):
    """`runs.jsonl` had no provider field at all (#501): the only trace of who verified a run
    was the `provider:persona` inside each verdict, and who generated it was nowhere. The run
    now carries its providers as data, and the record copies them; a state that predates the
    field writes no key, so an older run does not read as "generated by nothing"."""
    monkeypatch.setattr(runstate.config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(runstate.config, "GLOBAL_RUNS_PATH", tmp_path / "global.jsonl")
    state = _state()
    state["providers"] = {"generator": "codex", "verifier": ["claude", "ollama"],
                          "model": "gpt-5-codex"}
    telemetry_append(state, "DONE")
    older = _state()
    telemetry_append(older, "DONE")

    with_providers, without = [json.loads(line) for line in
                               (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert with_providers["providers"] == {"generator": "codex",
                                           "verifier": ["claude", "ollama"],
                                           "model": "gpt-5-codex"}
    assert "providers" not in without
