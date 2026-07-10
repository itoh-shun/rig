"""Unit tests for rig_workbench.orchestrate.queueing (local backend + label plumbing).

QUEUE_PATH is rebound to a tmp file via the tmp_queue fixture (same module-attribute
monkeypatch pattern the shipped selftest uses), so the real .rig/queue.json is untouched.
"""

from rig_workbench.orchestrate import queueing
from rig_workbench.orchestrate.queueing import (_local_load, _queue_relabel_args,
                                                queue_add, queue_list, queue_set_status)


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


def test_queue_add_remote_backend_graceful_error(tmp_queue, monkeypatch):
    # Simulate gh CLI failure: queue_add must return an error item, not crash.
    monkeypatch.setattr(queueing, "_cli_run", lambda argv: (127, "", "cli missing"))
    it = queue_add("github", "task", {})
    assert it["status"] == "error"
    assert it["id"] is None
    # local store untouched by the remote-backend attempt
    assert _local_load()["items"] == []
