"""A hand-off note is attached to a run, about an artifact, and survives the session (#548).

Slice 5 of the Mission Control issue, and the one it argues *against* building as a chat: a
free-form panel is a place for unmeasured claims. What is worth keeping is a note whose
author is a run and whose subject is an artifact — "this run changed the lock format; a
later run touching `pack.lock.json` should read `diff.md`" — filed with the run and shown
beside its other records.
"""

import argparse
import json
import pathlib

import pytest

from rig_workbench import mission_server
from rig_workbench.workbench import lifecycle


@pytest.fixture
def task_repo(tmp_path, monkeypatch):
    d = tmp_path / ".rig" / "runs" / "rig-1"
    d.mkdir(parents=True)
    (d / "task.json").write_text(json.dumps({
        "task_id": "rig-1", "task_type": "bugfix", "status": "running",
        "caller": {"id": "claude-code", "declared": True},
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "repo_root", lambda: tmp_path)
    return tmp_path


def note(task_id="rig-1", text="changed the lock format; read diff.md first", about=None):
    return argparse.Namespace(task_id=task_id, text=text, about=about)


def read_notes(root: pathlib.Path) -> list[dict]:
    return json.loads((root / ".rig" / "runs" / "rig-1" / "handoff.json")
                      .read_text(encoding="utf-8"))["notes"]


def test_a_note_is_filed_with_the_run_and_carries_its_subject(task_repo, capsys):
    lifecycle.cmd_note(note(about=["pack.lock.json", "diff.md"]))
    (entry,) = read_notes(task_repo)
    assert entry["text"] == "changed the lock format; read diff.md first"
    assert entry["about"] == ["pack.lock.json", "diff.md"]
    assert entry["caller"] == {"id": "claude-code", "declared": True}
    assert entry["recorded_at"]
    assert "hand-off note #1" in capsys.readouterr().out


def test_notes_append_and_are_never_rewritten(task_repo):
    lifecycle.cmd_note(note(text="first"))
    lifecycle.cmd_note(note(text="second"))
    assert [n["text"] for n in read_notes(task_repo)] == ["first", "second"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../secrets", "a\nb", "", "x" * 201])
def test_a_subject_that_is_not_an_artifact_inside_the_task_is_refused(task_repo, bad):
    with pytest.raises(SystemExit):
        lifecycle.cmd_note(note(about=[bad]))
    assert not (task_repo / ".rig" / "runs" / "rig-1" / "handoff.json").exists()


def test_an_empty_or_oversized_note_is_refused(task_repo):
    for text in ("", "   ", "y" * 2001):
        with pytest.raises(SystemExit):
            lifecycle.cmd_note(note(text=text))


def test_mission_control_projects_the_notes_and_renders_none_as_an_empty_list(task_repo):
    assert mission_server.task_detail(task_repo, "rig-1")["handoff"] == []
    lifecycle.cmd_note(note(about=["diff.md"]))
    detail = mission_server.task_detail(task_repo, "rig-1")
    assert [n["about"] for n in detail["handoff"]] == [["diff.md"]]


def test_the_page_escapes_every_note_field():
    from rig_workbench.mission_ui import JS_TEMPLATE as HTML
    fragment = HTML[HTML.index("function renderHandoff"):]
    fragment = fragment[:fragment.index("\n}\n") + 3]
    for field in ("n.recorded_at", "n.text", "esc(a)"):
        assert field in fragment
    assert "esc(n.text" in fragment and "esc(n.recorded_at" in fragment
