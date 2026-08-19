"""Unit tests for rig_workbench.orchestrate.queueing (local backend + label plumbing).

QUEUE_PATH is rebound to a tmp file via the tmp_queue fixture (same module-attribute
monkeypatch pattern the shipped selftest uses), so the real .rig/queue.json is untouched.
"""

import concurrent.futures as futures
import collections
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.orchestrate import queueing
from rig_workbench.orchestrate.queueing import (QueueCorrupt, _local_load, _queue_relabel_args,
                                                queue_add, queue_list, queue_set_status)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_queue_add_assigns_incrementing_ids(tmp_queue):
    a = queue_add("local", "task A", {})
    b = queue_add("local", "task B", {})
    assert (a["id"], a["status"], a["task"]) == (1, "queued", "task A")
    assert (b["id"], b["status"]) == (2, "queued")
    assert tmp_queue.exists()
    raw = _local_load()
    assert raw["next_id"] == 3
    assert [it["id"] for it in raw["items"]] == [1, 2]


def test_queue_list_excludes_done(tmp_queue):
    queue_add("local", "task A", {})
    queue_add("local", "task B", {})
    queue_set_status("local", 1, "done", "", {})
    listed = queue_list("local", {})
    assert [it["id"] for it in listed] == [2]
    # but the raw store still holds the done item
    raw = [it for it in _local_load()["items"] if it["status"] == "done"]
    assert [it["id"] for it in raw] == [1]


def test_queue_status_transitions_and_note(tmp_queue):
    it = queue_add("local", "task A", {})
    queue_set_status("local", it["id"], "failed", "some machine note", {})
    failed = next(x for x in queue_list("local", {}) if x["id"] == it["id"])
    assert failed["status"] == "failed"
    assert failed["note"] == "some machine note"
    # retry: back to queued, note cleared
    queue_set_status("local", it["id"], "queued", "", {})
    retried = next(x for x in queue_list("local", {}) if x["id"] == it["id"])
    assert retried["status"] == "queued"
    assert retried["note"] == ""


def test_queue_note_truncated_to_300(tmp_queue):
    it = queue_add("local", "task A", {})
    queue_set_status("local", it["id"], "failed", "x" * 1000, {})
    got = next(x for x in queue_list("local", {}) if x["id"] == it["id"])
    assert got["note"] == "x" * 300


def test_queue_relabel_args_removes_all_other_labels():
    args = _queue_relabel_args("failed")
    assert args[:2] == ["--add-label", "rig-failed"]
    removed = [args[i + 1] for i in range(len(args) - 1) if args[i] == "--remove-label"]
    assert sorted(removed) == ["rig-done", "rig-queue", "rig-running"]
    assert _queue_relabel_args("bogus-status") == []


# ── concurrency (#360): `queue go` mutates the store from --max-parallel threads ──────────
# Before the fix these were unlocked load->modify->save cycles, so concurrent writers
# clobbered each other: 30 concurrent adds left 1 item, and 16 of 20 status transitions
# vanished — GO printed DONE while the store still said running/queued.

def test_concurrent_queue_add_keeps_every_item_with_unique_ids(tmp_queue):
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda i: queue_add("local", f"task {i}", {}), range(30)))
    ids = [it["id"] for it in _local_load()["items"]]
    assert len(ids) == 30, "concurrent add lost items (unlocked read-modify-write)"
    assert [i for i, c in collections.Counter(ids).items() if c > 1] == []


def test_concurrent_status_updates_are_not_lost(tmp_queue):
    ids = [queue_add("local", f"task {i}", {})["id"] for i in range(20)]
    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda i: queue_set_status("local", i, "running", f"note {i}", {}), ids))
    stale = [it["id"] for it in _local_load()["items"] if it["status"] != "running"]
    assert stale == [], f"status updates lost for {stale}"


def test_concurrent_add_across_processes_keeps_every_item(tmp_path):
    """Covers the fcntl.flock layer: separate processes, not just in-process threads.

    A `queue add` in another terminal while `queue go` runs is the real-world case the
    threading.Lock alone cannot serialize.
    """
    procs = [subprocess.Popen([sys.executable, str(REPO_ROOT / "scripts" / "orchestrate.py"),
                               "queue", "add", f"p{i}"],
                              cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             for i in range(6)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    stored = json.loads((tmp_path / ".rig" / "queue.json").read_text(encoding="utf-8"))["items"]
    assert len(stored) == 6
    assert len({it["id"] for it in stored}) == 6


# ── corrupt / hand-edited store ───────────────────────────────────────────────────────────

def test_corrupt_store_raises_instead_of_silently_wiping(tmp_queue):
    tmp_queue.parent.mkdir(parents=True, exist_ok=True)
    tmp_queue.write_text('{"items": [{"id": 1, "task": "important", "st', encoding="utf-8")
    before = tmp_queue.read_bytes()
    with pytest.raises(QueueCorrupt):
        queue_add("local", "new task", {})
    assert tmp_queue.read_bytes() == before, "an unreadable store must never be rewritten"


def test_hand_edited_store_without_next_id_is_normalized(tmp_queue):
    tmp_queue.parent.mkdir(parents=True, exist_ok=True)
    tmp_queue.write_text(json.dumps({"items": [{"id": 7, "task": "kept", "status": "queued"}]}),
                         encoding="utf-8")
    it = queue_add("local", "appended", {})
    assert it["id"] == 8, "next_id must be derived from max(id)+1, not collide or raise"
    assert [x["task"] for x in _local_load()["items"]] == ["kept", "appended"]


def test_save_leaves_no_temp_file_behind(tmp_queue):
    queue_add("local", "task", {})
    assert list(tmp_queue.parent.glob("*.tmp")) == []


def test_queue_add_remote_backend_graceful_error(tmp_queue, monkeypatch):
    # Simulate gh CLI failure: queue_add must return an error item, not crash.
    monkeypatch.setattr(queueing, "_cli_run", lambda argv: (127, "", "cli missing"))
    it = queue_add("github", "task", {})
    assert it["status"] == "error"
    assert it["id"] is None
    # local store untouched by the remote-backend attempt
    assert _local_load()["items"] == []


# ── queue cancel (#459) ──────────────────────────────────────────────────────
# `done` means the item ran and finished; using it to throw away work that never ran
# files a discard under the completion count that a dashboard reads as throughput.


def test_cancel_records_its_own_status_rather_than_done(tmp_queue):
    queue_add("local", "typo'd task", {})
    queueing._cmd_queue_dispatch("cancel", ["1"], "local", {}, "rig", "rig", 1)
    item = _local_load()["items"][0]
    assert item["status"] == "cancelled"
    assert item["status"] != "done"
    assert "never executed" in item["note"]


def test_a_cancelled_item_leaves_the_listing(tmp_queue):
    queue_add("local", "keep", {})
    queue_add("local", "drop", {})
    queueing._cmd_queue_dispatch("cancel", ["2"], "local", {}, "rig", "rig", 1)
    assert [it["id"] for it in queue_list("local", {})] == [1]
    # …but the store still holds it, so "cancelled" stays answerable after the fact.
    assert [it["status"] for it in _local_load()["items"]] == ["queued", "cancelled"]


def test_cancelling_an_id_that_is_not_there_is_an_error(tmp_queue):
    queue_add("local", "only one", {})
    with pytest.raises(SystemExit) as exc:
        queueing._cmd_queue_dispatch("cancel", ["99"], "local", {}, "rig", "rig", 1)
    assert exc.value.code == 1


def test_cancel_without_an_id_is_an_error(tmp_queue):
    """With an item present, so the refusal has to come from the missing argument rather
    than from the id lookup failing on an empty store."""
    queue_add("local", "still wanted", {})
    with pytest.raises(SystemExit):
        queueing._cmd_queue_dispatch("cancel", [], "local", {}, "rig", "rig", 1)
    assert _local_load()["items"][0]["status"] == "queued"


def test_a_running_item_cannot_be_cancelled(tmp_queue):
    """The provider writes this item's final status when it finishes and would overwrite
    `cancelled` with `done`/`failed`. A cancellation the next write erases is worse than
    a refused one, because the operator believes it took."""
    queue_add("local", "in flight", {})
    queue_set_status("local", 1, "running", "", {})
    with pytest.raises(SystemExit):
        queueing._cmd_queue_dispatch("cancel", ["1"], "local", {}, "rig", "rig", 1)
    assert _local_load()["items"][0]["status"] == "running"


def test_cancel_needs_the_local_backend(tmp_queue, monkeypatch):
    """Issue labels have no state for work that never ran, and `queue_set_status` would
    post a note without relabelling or closing — leaving the item listed as queued while
    the operator was told it was cancelled.

    The refusal has to happen before anything reaches the tracker, so the CLI runner is
    replaced with one that fails the test if it is called at all. That is not only a
    sharper assertion than `SystemExit`: without the guard this path runs `gh issue
    comment` against whatever repository the checkout points at."""
    queue_add("local", "exists locally", {})
    monkeypatch.setattr(queueing, "_cli_run",
                        lambda argv: pytest.fail(f"reached the tracker: {argv}"))
    with pytest.raises(SystemExit):
        queueing._cmd_queue_dispatch("cancel", ["1"], "github", {}, "rig", "rig", 1)
    assert _local_load()["items"][0]["status"] == "queued"


def test_cancel_is_an_accepted_subcommand(tmp_queue):
    """`cmd_queue` gates on a literal tuple; a dispatch branch nothing routes to is dead."""
    queue_add("local", "drop", {})
    queueing.cmd_queue(["cancel", "1"])
    assert _local_load()["items"][0]["status"] == "cancelled"


def test_the_dashboard_counts_a_cancellation_apart_from_a_completion(tmp_path):
    """The number someone reads off this line to judge throughput."""
    from rig_workbench.workbench.cockpit import queue_depth_lines

    rig = tmp_path / ".rig"
    rig.mkdir(parents=True)
    (rig / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "task": "ran", "status": "done"},
        {"id": 2, "task": "thrown away", "status": "cancelled"},
    ], "next_id": 3}), encoding="utf-8")
    lines = queue_depth_lines(tmp_path)
    assert lines == ["Nothing pending (1 done, 1 cancelled)."]


def test_a_cancelled_item_is_not_pending_but_a_waiting_one_is(tmp_path):
    from rig_workbench.workbench.cockpit import queue_depth_lines

    rig = tmp_path / ".rig"
    rig.mkdir(parents=True)
    (rig / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "task": "thrown away", "status": "cancelled"},
        {"id": 2, "task": "held on a dependency", "status": "waiting"},
    ], "next_id": 3}), encoding="utf-8")
    assert queue_depth_lines(tmp_path)[0] == "waiting=1"


def test_a_cancelled_dependency_blocks_rather_than_leaving_a_dependent_waiting(tmp_queue):
    """A cancelled item never becomes `done`, so an edge on it can never be satisfied.
    Reporting that as `waiting` would leave the dependent looking temporarily slow
    forever (#427 + #459)."""
    from rig_workbench.orchestrate.dependencies import BLOCKED, resolve

    a = queue_add("local", "migration", {})
    b = queue_add("local", "api", {}, [a["id"]])
    queueing._cmd_queue_dispatch("cancel", [str(a["id"])], "local", {}, "rig", "rig", 1)
    items = _local_load()["items"]
    verdict = resolve(items[1], items, tmp_queue.parent / "runs")
    assert verdict["state"] == BLOCKED
    assert "will not run" in verdict["reason"]
    assert b["id"] == 2


def test_cancel_is_one_locked_compare_and_set(tmp_queue):
    """The status check and the write have to be the same critical section. Split, a
    concurrent `queue go` claims the item between them: the check sees `queued`, the claim
    wins, and the provider overwrites `cancelled` with its own result — the cancellation is
    silently ineffective while the operator believes it took."""
    queue_add("local", "contested", {})
    assert queueing.queue_claim("local", 1, {}) is True   # `queue go` got there first
    assert queueing.queue_cancel("local", 1, {})[0] == "running"
    assert _local_load()["items"][0]["status"] == "running"


def test_a_finished_item_cannot_be_recorded_as_never_having_run(tmp_queue):
    queue_add("local", "it ran", {})
    queue_set_status("local", 1, "done", "finished", {})
    assert queueing.queue_cancel("local", 1, {})[0] == "done"
    assert _local_load()["items"][0]["status"] == "done"


def test_a_failed_item_can_be_cancelled_and_says_it_ran(tmp_queue):
    """Cancelling a failure is "do not retry this", which is a different fact from "this
    never ran" — so it is allowed, and the note does not claim otherwise."""
    queue_add("local", "it broke", {})
    queue_set_status("local", 1, "failed", "provider crashed", {})
    assert queueing.queue_cancel("local", 1, {})[0] == "cancelled"
    item = _local_load()["items"][0]
    assert item["status"] == "cancelled"
    assert "after a failed run" in item["note"]


def test_a_held_item_can_be_cancelled(tmp_queue):
    """An item waiting on a dependency has not run either, so it is cancellable."""
    a = queue_add("local", "migration", {})
    queue_add("local", "api", {}, [a["id"]])
    queue_set_status("local", a["id"], "done", "", {})
    queueing.resolve_dependencies()
    assert _local_load()["items"][1]["status"] == "waiting"
    assert queueing.queue_cancel("local", 2, {})[0] == "cancelled"


def test_cancelling_something_that_is_not_there_reports_missing(tmp_queue):
    assert queueing.queue_cancel("local", 99, {})[0] == "missing"


def test_mission_control_and_the_cli_agree_on_what_can_be_retried(tmp_path):
    """The CLI's `queue retry` accepts a cancelled item; Mission Control refusing the same
    item is the two disagreeing about one queue."""
    from rig_workbench.mission_jobs import assert_retryable

    rig = tmp_path / ".rig"
    rig.mkdir(parents=True)
    (rig / "queue.json").write_text(json.dumps({"items": [
        {"id": 1, "task": "changed my mind", "status": "cancelled"},
    ], "next_id": 2}), encoding="utf-8")
    assert assert_retryable(tmp_path, "1")["status"] == "cancelled"


def test_no_read_that_decides_a_cancellation_happens_outside_the_lock(tmp_queue, monkeypatch):
    """The invariant, stated as a test rather than as a comment.

    A status read outside the critical section can be overtaken: the read sees `queued`,
    a concurrent `queue go` claims the item, and the write lands anyway — so the store
    says `cancelled` while a provider is running, and the provider's own final write
    erases it. The operator is told the cancellation took, twice over wrongly.

    Rather than race real threads and hope the interleaving shows up, this makes it
    deterministic: `_local_load` is wrapped in a probe that tries the queue's own lock
    without blocking. Succeeding means this read is happening *outside* the critical
    section — the only place a competing claim can slip in — so the probe performs that
    claim. An implementation that reads only inside the lock never gives it the chance.
    """
    queue_add("local", "contested", {})
    real_load = queueing._local_load
    state = {"reentrant": False, "raced": False}

    def probing_load():
        # The snapshot is taken first, then the claim lands. That order is the point: it
        # reproduces "this read already observed the old status, and a competing claim
        # arrived before the write" — claiming first would let even a broken
        # implementation see the new status and refuse.
        result = real_load()
        if not state["reentrant"] and queueing._QUEUE_WRITE_LOCK.acquire(blocking=False):
            queueing._QUEUE_WRITE_LOCK.release()
            state["reentrant"] = True
            try:
                queueing.queue_claim("local", 1, {})   # the concurrent GO gets there
                state["raced"] = True
            finally:
                state["reentrant"] = False
        return result

    monkeypatch.setattr(queueing, "_local_load", probing_load)
    outcome = queueing.queue_cancel("local", 1, {})[0]
    # Deliberately no `monkeypatch.undo()`: it would also revert the `tmp_queue` fixture's
    # QUEUE_PATH, and every read after it would answer from the real repository's queue
    # instead of this test's. `real_load` is already the unpatched function.
    store = real_load()["items"][0]

    if state["raced"]:
        # A read outside the lock existed, and a claim slipped into the window it opened.
        # The cancellation must not have landed on top of it.
        assert outcome == "running"
        assert store["status"] == "running"
    else:
        assert outcome == "cancelled"
        assert store["status"] == "cancelled"


def test_the_page_offers_retry_on_a_cancelled_item(tmp_queue):
    """Allowing `cancelled` through `assert_retryable` while the page never renders the
    button is the same CLI/Mission-Control disagreement in a different place."""
    from rig_workbench.mission_ui import JS_TEMPLATE

    start = JS_TEMPLATE.index('queue-actions')
    condition = JS_TEMPLATE[max(0, start - 160):start]
    assert "x.status==='cancelled'" in condition


def test_a_cancellation_never_says_the_item_cannot_come_back(tmp_queue):
    """`queue retry` and Mission Control both requeue a cancellation. A note claiming
    otherwise would talk an operator out of an action that works."""
    for status in ("queued", "failed"):
        queueing.QUEUE_PATH.unlink(missing_ok=True)
        queue_add("local", "item", {})
        if status != "queued":
            queue_set_status("local", 1, status, "", {})
        outcome, message = queueing.queue_cancel("local", 1, {})
        assert outcome == "cancelled"
        note = _local_load()["items"][0]["note"]
        for text in (note, message):
            assert "will not be retried" not in text
            assert "cannot" not in text


def test_the_wording_matches_whether_the_item_actually_ran(tmp_queue):
    """Two facts, two texts. Telling an operator that a failed item "never ran" distorts
    exactly the audit the status exists to keep honest."""
    queue_add("local", "never started", {})
    _, fresh_message = queueing.queue_cancel("local", 1, {})
    assert "never ran" in fresh_message
    assert "never executed" in _local_load()["items"][0]["note"]

    queue_add("local", "broke", {})
    queue_set_status("local", 2, "failed", "provider crashed", {})
    _, failed_message = queueing.queue_cancel("local", 2, {})
    assert "never ran" not in failed_message
    assert "it ran and failed" in failed_message
    assert "it ran" in _local_load()["items"][1]["note"]


def test_the_printed_line_carries_the_wording_the_cancellation_chose(tmp_queue, capsys):
    """Through the CLI path, not the function under it. The message is decided inside the
    locked section that knows which status the item came from; a dispatcher that prints its
    own fixed line throws that away and tells an operator a failed item never ran."""
    queue_add("local", "broke", {})
    queue_set_status("local", 1, "failed", "provider crashed", {})
    capsys.readouterr()
    queueing._cmd_queue_dispatch("cancel", ["1"], "local", {}, "rig", "rig", 1)
    out = capsys.readouterr().out
    assert "it ran and failed" in out
    assert "never ran" not in out
    assert "queue retry 1" in out
