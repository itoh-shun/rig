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
