"""Acceptance as a dependency edge — `rig.queue-dependencies/v1` (#427).

The claim under test is narrow and easy to overstate: a queue item can be made to wait
for another item's *result* to clear rig's acceptance boundary. Not for the previous
agent to finish — the queue already knew that, and it is the weaker condition that lets
unreviewed work become the input to the next task.

So most of what follows is about the gap between those two. A dependency that reached
`done` is not satisfied; a dependency accepted over a failed gate is satisfied but says
so; and anything rig tried to read and could not stops the dependent rather than
releasing it.
"""

import json
import re
import pathlib

import pytest

from rig_workbench.orchestrate import dependencies as deps
from rig_workbench.orchestrate import queueing
from rig_workbench.orchestrate.queueing import (_local_load, queue_add, queue_list,
                                                queue_set_status, resolve_dependencies)


@pytest.fixture
def runs(tmp_queue, monkeypatch):
    """The workbench run directory that sits beside the scratch queue store."""
    d = tmp_queue.parent / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _task(runs: pathlib.Path, task_id: str, *, status: str, gate: str = "passed",
          forced: bool = False) -> str:
    run = runs / task_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "task.json").write_text(
        json.dumps({"task_id": task_id, "status": status, "forced": forced}),
        encoding="utf-8")
    (run / "acceptance.json").write_text(json.dumps({"status": gate}), encoding="utf-8")
    return task_id


def _link(item_id, task_id: str) -> None:
    queue_set_status("local", item_id, _status_of(item_id), "", {}, task_id)


def _status_of(item_id) -> str:
    return next(it["status"] for it in _local_load()["items"]
                if str(it["id"]) == str(item_id))


def _item(item_id) -> dict:
    return next(it for it in _local_load()["items"] if str(it["id"]) == str(item_id))


# ── 1. the edge is persisted ─────────────────────────────────────────────────
def test_an_item_can_carry_zero_or_more_dependencies(tmp_queue):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    c = queue_add("local", "release", {}, [a["id"], b["id"]])
    assert "depends_on" not in _item(a["id"])
    assert _item(b["id"])["depends_on"] == [str(a["id"])]
    assert _item(c["id"])["depends_on"] == [str(a["id"]), str(b["id"])]
    assert _item(c["id"])["dependency_policy"] == "accepted"


def test_ids_are_normalised_so_an_int_and_a_string_are_the_same_edge(tmp_queue):
    """Local ids are ints and the issue-tracker backends hand back strings. A `3` that
    fails to match a `"3"` reads as a missing dependency rather than as a type mismatch."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [str(a["id"])])
    assert _item(b["id"])["depends_on"] == ["1"]
    assert deps.normalise([1, "1", 2]) == ["1", "2"]


def test_the_graph_survives_being_written_and_read_again(tmp_queue):
    """Durability is the whole point of the persistent queue; an edge that lives only in
    a running process is not one."""
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    reread = json.loads(tmp_queue.read_text(encoding="utf-8"))
    assert reread["items"][1]["depends_on"] == [str(a["id"])]
    assert reread["items"][1]["dependency_policy"] == "accepted"


# ── 2. finished is not accepted ──────────────────────────────────────────────
def test_a_dependency_that_merely_finished_does_not_release_its_dependent(tmp_queue, runs):
    """The distinction the whole feature exists for. A queue item reaching `done` means
    its gate settled — `_build_queue_task_prompt` says accepting is left to a person — so
    the dependent must still wait."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-1-migration", status="running")
    queue_set_status("local", a["id"], "done", "gate settled", {}, "rig-1-migration")
    runnable, held = resolve_dependencies()
    # `a` is already `done`, so it is not resolvable and not in the runnable set; `b` is
    # the only candidate, and it is held.
    assert runnable == []
    assert [row["id"] for row in held] == [b["id"]]
    assert _status_of(b["id"]) == "waiting"
    assert "has not been accepted" in _item(b["id"])["dependency_note"]


def test_an_accepted_dependency_releases_its_dependent(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-1-migration", status="accepted")
    queue_set_status("local", a["id"], "done", "", {}, "rig-1-migration")
    resolve_dependencies()
    assert _status_of(b["id"]) == "queued"
    assert "dependency_note" not in _item(b["id"])


def test_every_dependency_must_be_accepted_not_just_one(tmp_queue, runs):
    a = queue_add("local", "ui", {})
    b = queue_add("local", "security review", {})
    c = queue_add("local", "release candidate", {}, [a["id"], b["id"]])
    _task(runs, "rig-a", status="accepted")
    _task(runs, "rig-b", status="running")
    queue_set_status("local", a["id"], "done", "", {}, "rig-a")
    queue_set_status("local", b["id"], "done", "", {}, "rig-b")
    resolve_dependencies()
    assert _status_of(c["id"]) == "waiting"
    _task(runs, "rig-b", status="accepted")
    resolve_dependencies()
    assert _status_of(c["id"]) == "queued"


def test_an_accept_that_overrode_the_gate_counts_but_says_so(tmp_queue, runs):
    """The policy is named `accepted`, and a forced accept is an accept. Hiding that it
    was forced would make the edge quietly weaker than it reads; re-deciding it here would
    put a second opinion on the gate inside the queue."""
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-forced", status="accepted", gate="failed", forced=True)
    queue_set_status("local", a["id"], "done", "", {}, "rig-forced")
    edge = deps.resolve(_item(2), _local_load()["items"], runs)["edges"][0]
    assert edge["satisfied"] is True
    assert edge["acceptance"]["forced"] is True
    assert edge["acceptance"]["gate_status"] == "failed"
    assert "forced" in edge["reason"]


# ── 3. terminal states block, and say why ────────────────────────────────────
def test_a_discarded_dependency_blocks_its_dependent(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-gone", status="discarded")
    queue_set_status("local", a["id"], "done", "", {}, "rig-gone")
    resolve_dependencies()
    assert _status_of(b["id"]) == "blocked"
    assert "discarded" in _item(b["id"])["dependency_note"]


def test_a_failed_dependency_blocks_its_dependent_with_the_way_out(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "failed", "provider crashed", {})
    resolve_dependencies()
    assert _status_of(b["id"]) == "blocked"
    assert "queue retry" in _item(b["id"])["dependency_note"]


def test_a_block_clears_once_the_dependency_is_retried_and_accepted(tmp_queue, runs):
    """Blocked is a verdict about the store as it stands, not a tombstone. Every GO
    re-resolves, so the graph heals without anyone having to un-block by hand."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "failed", "", {})
    resolve_dependencies()
    assert _status_of(b["id"]) == "blocked"
    _task(runs, "rig-ok", status="accepted")
    queue_set_status("local", a["id"], "done", "", {}, "rig-ok")
    resolve_dependencies()
    assert _status_of(b["id"]) == "queued"


# ── 4. nothing unreadable is ever ready ──────────────────────────────────────
def test_a_dependency_with_no_workbench_task_waits_and_says_it_may_never_clear(
        tmp_queue, runs):
    """A provider that registers no rig task leaves nothing to ask about acceptance. That
    is an absence, not a refusal — so `waiting` — but the reason has to say it will not
    resolve itself, or the item looks temporarily slow forever."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    resolve_dependencies()
    assert _status_of(b["id"]) == "waiting"
    note = _item(b["id"])["dependency_note"]
    assert "no workbench task recorded" in note and "not clear on its own" in note


def test_a_dependency_whose_run_state_is_missing_waits_rather_than_releasing(
        tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {}, "rig-never-written")
    resolve_dependencies()
    assert _status_of(b["id"]) == "waiting"
    assert "no run state" in _item(b["id"])["dependency_note"]


def test_an_edge_pointing_at_nothing_blocks_rather_than_being_ignored(tmp_queue, runs):
    """`validate_new` refuses this at declaration time, so it only arises from a store
    someone edited by hand or an item removed after it was depended on. Treating a
    dangling edge as absent would run the dependent — the exact silent release the whole
    resolver is built to avoid — so the resolver checks it again where the answer is
    used, not only where the value is entered."""
    queue_add("local", "api", {})
    store = _local_load()
    store["items"][0]["depends_on"] = ["99"]
    store["items"][0]["dependency_policy"] = "accepted"
    queueing._local_save(store)
    resolve_dependencies()
    assert _status_of(1) == "blocked"
    assert "does not exist" in _item(1)["dependency_note"]


def test_a_dependency_declaration_rig_cannot_read_blocks_the_item(tmp_queue, runs):
    """The surrounding module swallows exceptions and returns a benign default in several
    places, and is right to — the batch has already run by then. Here the same reflex
    would start work whose dependencies were never checked."""
    queue_add("local", "api", {})
    store = _local_load()
    store["items"][0]["depends_on"] = {"not": "a list"}
    queueing._local_save(store)
    resolve_dependencies()
    assert _status_of(1) == "blocked"
    assert "cannot be read" in _item(1)["dependency_note"]


def test_an_unknown_policy_blocks_rather_than_falling_back_to_the_known_one(
        tmp_queue, runs):
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    store = _local_load()
    store["items"][1]["dependency_policy"] = "merged"
    queueing._local_save(store)
    resolve_dependencies()
    assert _status_of(2) == "blocked"
    assert "not one this version defines" in _item(2)["dependency_note"]


# ── 5. declarations rig refuses to store ─────────────────────────────────────
def test_a_dependency_on_something_that_does_not_exist_is_refused(tmp_queue):
    with pytest.raises(deps.DependencyError) as exc:
        queue_add("local", "api", {}, ["999"])
    assert "999" in str(exc.value)
    assert _local_load()["items"] == []


def test_an_item_cannot_depend_on_itself(tmp_queue):
    """A self-reference is unknown at the moment it is declared — the id is not issued
    until the item is stored — so it is refused by the same check, and nothing is
    written."""
    queue_add("local", "migration", {})
    with pytest.raises(deps.DependencyError):
        queue_add("local", "api", {}, ["2"])
    assert [it["id"] for it in _local_load()["items"]] == [1]


def test_an_unknown_policy_is_refused_at_declaration_time(tmp_queue):
    a = queue_add("local", "migration", {})
    with pytest.raises(deps.DependencyError):
        queue_add("local", "api", {}, [a["id"]], "merged")


def test_a_cycle_in_a_hand_edited_store_is_detected(tmp_queue, runs):
    """Monotonic ids and existing-only references mean the CLI cannot build a cycle: every
    new edge points backwards. `.rig/queue.json` is a file people edit, though — the store
    loader already repairs a stale `next_id` for that reason — so the check is over the
    store, and this is how it gets there."""
    queue_add("local", "a", {})
    queue_add("local", "b", {})
    store = _local_load()
    store["items"][0]["depends_on"] = ["2"]
    store["items"][0]["dependency_policy"] = "accepted"
    store["items"][1]["depends_on"] = ["1"]
    store["items"][1]["dependency_policy"] = "accepted"
    queueing._local_save(store)
    rings = deps.cycles(_local_load()["items"])
    assert rings and {"1", "2"} <= set(rings[0])
    resolve_dependencies()
    assert _status_of(1) == "blocked" and _status_of(2) == "blocked"
    assert "cycle" in _item(1)["dependency_note"]


def test_a_self_edge_written_by_hand_is_a_cycle_too(tmp_queue, runs):
    queue_add("local", "a", {})
    store = _local_load()
    store["items"][0]["depends_on"] = ["1"]
    queueing._local_save(store)
    resolve_dependencies()
    assert _status_of(1) == "blocked"


def test_dependencies_need_the_local_backend_and_are_refused_elsewhere(tmp_queue):
    """Issue labels cannot hold an edge list. Dropping the flag would run the dependent
    immediately, which is the one outcome it exists to prevent."""
    with pytest.raises(deps.DependencyError) as exc:
        queue_add("github", "api", {}, ["1"])
    assert "local queue backend" in str(exc.value)


# ── 6. the existing queue is untouched ───────────────────────────────────────
def test_an_item_with_no_dependencies_is_ready_and_stays_queued(tmp_queue, runs):
    """The one absence that means "go". Nothing was declared, so nothing is unmet — the
    opposite rule from every other missing value in this module, and deliberately so."""
    queue_add("local", "standalone", {})
    runnable, held = resolve_dependencies()
    assert [it["id"] for it in runnable] == [1]
    assert held == []
    assert _status_of(1) == "queued"
    assert set(_item(1)) == {"id", "task", "status", "note"}


def test_resolution_never_rewrites_an_item_a_provider_is_running(tmp_queue, runs):
    """A live provider owns a `running` item. Rewriting its status from under that process
    is the lost-update class this file already carries a lock for."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", b["id"], "running", "", {})
    resolve_dependencies()
    assert _status_of(b["id"]) == "running"


def test_a_held_item_is_not_counted_as_queued_by_the_detached_worker(tmp_queue, runs):
    """`mission_worker` loops while anything is `queued`. A dependent parked at `queued`
    would spin that worker several times a second with nothing to run, forever — which is
    why the verdict is a persisted status rather than a filter applied at GO."""
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    resolve_dependencies()
    still_queued = [it for it in _local_load()["items"] if it.get("status") == "queued"]
    assert still_queued == []


def test_held_items_stay_visible_in_the_listing(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    resolve_dependencies()
    listed = {it["id"]: it for it in queue_list("local", {})}
    assert listed[b["id"]]["status"] == "waiting"


def test_the_task_link_survives_a_transition_that_cannot_re_derive_it(tmp_queue):
    """Before this the link was computed by GO and thrown away, leaving the queue unable
    to say anything about whether an item's result was accepted."""
    queue_add("local", "migration", {})
    queue_set_status("local", 1, "done", "", {}, "rig-linked")
    assert _item(1)["task_id"] == "rig-linked"
    queue_set_status("local", 1, "failed", "verifier said no", {})
    assert _item(1)["task_id"] == "rig-linked"


def test_requeueing_drops_the_link_to_the_result_it_is_replacing(tmp_queue):
    """`queue retry` says this item is going to produce a *different* result. Keeping the
    old link would leave a dependent released against work being replaced."""
    queue_add("local", "migration", {})
    queue_set_status("local", 1, "done", "", {}, "rig-old")
    queue_set_status("local", 1, "queued", "retry", {})
    assert "task_id" not in _item(1)


def test_a_retried_dependency_stops_satisfying_its_dependent(tmp_queue, runs):
    """The whole failure this pair of guards exists for: A was accepted once, B depends on
    A, and A is then retried. Releasing B on the old acceptance would start work on a base
    that is being rebuilt.

    Two independent guards close it — the link is dropped on requeue, and an edge is only
    read once the dependency's own run is over — so neither alone is load-bearing."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-old", status="accepted")
    queue_set_status("local", a["id"], "done", "", {}, "rig-old")
    resolve_dependencies()
    assert _status_of(b["id"]) == "queued"

    queue_set_status("local", a["id"], "queued", "retry", {})
    resolve_dependencies()
    assert _status_of(b["id"]) == "waiting"
    assert "current run has not finished" in _item(b["id"])["dependency_note"]


def test_an_edge_is_not_read_while_its_dependency_is_still_running(tmp_queue, runs):
    """Same guard from the other side: a stale link plus a live rerun must not read as
    satisfied even before the requeue clears anything."""
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    _task(runs, "rig-old", status="accepted")
    queue_set_status("local", a["id"], "done", "", {}, "rig-old")
    store = _local_load()
    store["items"][0]["status"] = "running"   # a rerun that kept the old link
    queueing._local_save(store)
    resolve_dependencies()
    assert _status_of(b["id"]) == "waiting"


# ── 8. one item, one runner ──────────────────────────────────────────────────
def test_an_item_can_only_be_claimed_once(tmp_queue):
    """GO has always marked an item `running` unconditionally at dispatch, so two
    concurrent `queue go` processes could both execute it. That predates dependencies and
    the compare-and-set does not change what happens when GO dies mid-batch — but with
    edges the cost rises: two runs produce two workbench tasks, only one of which gets
    linked, so a dependent can be released against a result nobody kept."""
    queue_add("local", "migration", {})
    assert queueing.queue_claim("local", 1, {}) is True
    assert _status_of(1) == "running"
    assert queueing.queue_claim("local", 1, {}) is False


def test_a_held_item_cannot_be_claimed(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    resolve_dependencies()
    assert queueing.queue_claim("local", b["id"], {}) is False


def test_claiming_an_item_that_is_not_there_reports_failure(tmp_queue):
    assert queueing.queue_claim("local", 99, {}) is False


# ── 7. the data a client draws from ──────────────────────────────────────────
_PRESENTATION = ("color", "colour", "css", "class", "style", "px", "svg", "width",
                 "height", "x", "y")


def test_the_graph_carries_no_presentation(tmp_queue, runs):
    """Presentation-neutral in the same sense as `rig.assurance-graph/v1`: a second client
    reads this without adopting Mission Control's stylesheet."""
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    graph = deps.graph(_local_load()["items"], runs)
    keys = {k for node in graph["nodes"] for k in node} | {
        k for edge in graph["edges"] for k in edge}
    assert not (keys & set(_PRESENTATION))
    assert graph["schema"] == "rig.queue-dependencies/v1"


def test_the_graph_states_the_edge_and_the_reason_it_is_unmet(tmp_queue, runs):
    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    graph = deps.graph(_local_load()["items"], runs)
    node = next(n for n in graph["nodes"] if n["id"] == str(b["id"]))
    assert node["dependency_state"] == "waiting"
    assert node["blocked_reason"]
    assert graph["edges"] == [{"from": str(a["id"]), "to": str(b["id"]),
                               "state": "waiting", "satisfied": False,
                               "reason": node["blocked_reason"]}]


def test_mission_control_can_obtain_the_dependency_graph(tmp_path):
    """AC 8 asks that the data be obtainable, and Mission Control serves a repository it
    was given — not the queue of its own invocation directory."""
    from rig_workbench.mission_server import durable_snapshot

    rig = tmp_path / ".rig"
    (rig / "runs").mkdir(parents=True)
    (rig / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "task": "migration", "status": "done"},
        {"id": 2, "task": "api", "status": "waiting", "depends_on": ["1"],
         "dependency_policy": "accepted"},
    ], "next_id": 3}), encoding="utf-8")
    snapshot = durable_snapshot(tmp_path)
    graph = snapshot["dependencies"]
    assert graph["schema"] == "rig.queue-dependencies/v1"
    assert [e["to"] for e in graph["edges"]] == ["2"]


def test_a_dependency_graph_that_cannot_be_built_costs_a_panel_not_the_page(
        tmp_path, monkeypatch):
    from rig_workbench import mission_server
    from rig_workbench.orchestrate import dependencies as module

    rig = tmp_path / ".rig"
    rig.mkdir(parents=True)
    (rig / "queue.json").write_text(json.dumps({"items": [], "next_id": 1}),
                                    encoding="utf-8")
    monkeypatch.setattr(module, "graph",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    snapshot = mission_server.durable_snapshot(tmp_path)
    assert "nope" in snapshot["dependencies"]["error"]
    assert snapshot["counts"] == {}


def test_the_browser_page_shows_the_edge_and_still_parses():
    import json as _json
    import shutil
    import subprocess

    from rig_workbench.mission_ui import JS_TEMPLATE, interactive_html

    page = interactive_html("token")
    assert "depends on" in page and "dependency_note" in page
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    proc = subprocess.run([node, "--check", "-"],
                          input=JS_TEMPLATE.replace("__CSRF__", _json.dumps("t")),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_every_dependency_value_reaches_the_page_through_the_escaper():
    """A task title containing `<` renders safely everywhere else on this page; the new
    lines must not be the exception.

    Scanned rather than grepped: a grep for `esc(` passes as soon as *one* interpolation
    is escaped, which is exactly the shape of the bug it is supposed to catch. This walks
    every `${...}` in the fragment and requires each to be escaped or to be markup the
    code chose itself.
    """
    from rig_workbench.mission_ui import JS_TEMPLATE

    start = JS_TEMPLATE.index("(x.depends_on||[]).length")
    end = JS_TEMPLATE.index("x.status===", start)
    fragment = JS_TEMPLATE[start:end]
    interpolations = re.findall(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", fragment)
    assert interpolations, "the scan found nothing to check — the fragment moved"
    unescaped = [expr for expr in interpolations
                 if "esc(" not in expr and "'#'" not in expr]
    assert unescaped == [], unescaped
