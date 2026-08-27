"""The Orca runtime is exercised through Rig's public runtime selector and CLI."""

import json
import os
import subprocess
import sys

import pytest

from rig_workbench.workbench import runtime
from rig_workbench.workbench.cli import build_parser

_READY = '{"ok":true,"result":{"runtime":{"state":"ready","reachable":true}}}'


def _fake_orca(tmp_path, body):
    binary = tmp_path / "orca"
    binary.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


@pytest.fixture
def orca_session(monkeypatch):
    monkeypatch.setenv("ORCA_WORKTREE_ID", "parent::/repo")


def test_new_and_import_expose_the_runtime_choice():
    parser = build_parser()
    new = parser.parse_args(["new", "task", "--type", "feature", "--runtime", "orca"])
    imported = parser.parse_args(["import", "--head", "HEAD", "--type", "feature",
                                  "--producer", "other", "--runtime", "native"])
    assert new.runtime == "orca"
    assert imported.runtime == "native"


def test_explicit_orca_rejects_missing_cli_but_native_is_the_positive_control(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(runtime.RuntimeError_, match="executable.*not found"):
        runtime.select("orca", tmp_path)
    assert runtime.select("native", tmp_path).name == "native"


def test_explicit_orca_rejects_nonresponding_cli_but_accepts_a_json_response(
        tmp_path, monkeypatch, orca_session):
    _fake_orca(tmp_path, "exit 7\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(runtime.RuntimeError_, match="status.*exit 7"):
        runtime.select("orca", tmp_path)

    _fake_orca(tmp_path, f"printf '%s\\n' '{_READY}'\n")
    assert runtime.select("orca", tmp_path).name == "orca"


def test_explicit_orca_rejects_broken_json_but_accepts_structured_json(
        tmp_path, monkeypatch, orca_session):
    _fake_orca(tmp_path, "printf 'not-json\\n'\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(runtime.RuntimeError_, match="valid JSON"):
        runtime.select("orca", tmp_path)

    _fake_orca(tmp_path, f"printf '%s\\n' '{_READY}'\n")
    assert runtime.select("orca", tmp_path).name == "orca"


def test_explicit_orca_rejects_structured_but_unreachable_status_with_ready_control(
        tmp_path, monkeypatch, orca_session):
    unavailable = '{"ok":true,"result":{"runtime":{"state":"starting","reachable":false}}}'
    _fake_orca(tmp_path, f"printf '%s\\n' '{unavailable}'\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(runtime.RuntimeError_, match="ready, reachable"):
        runtime.select("orca", tmp_path)

    _fake_orca(tmp_path, f"printf '%s\\n' '{_READY}'\n")
    assert runtime.select("orca", tmp_path).name == "orca"


def test_auto_uses_orca_only_for_an_active_session_and_reports_fallback(
        tmp_path, monkeypatch, capsys):
    _fake_orca(tmp_path, f"printf '%s\\n' '{_READY}'\n")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("ORCA_WORKTREE_ID", raising=False)
    monkeypatch.delenv("ORCA_WORKSPACE_ID", raising=False)
    assert runtime.select("auto", tmp_path).name == "native"
    assert "native" in capsys.readouterr().err

    monkeypatch.setenv("ORCA_WORKTREE_ID", "parent::/repo")
    assert runtime.select("auto", tmp_path).name == "orca"


def test_create_requires_the_cli_identifier_and_path_and_keeps_them_verbatim(
        tmp_path, monkeypatch, orca_session):
    calls = tmp_path / "calls"
    created = tmp_path / "created"
    payload = {"worktree": {"id": f"repo-id::{created}", "path": str(created),
                             "branch": "task-1"}}
    _fake_orca(tmp_path, f'''printf '%s\\n' "$*" >> '{calls}'
case "$1 $2" in
  "status --json") printf '%s\\n' '{_READY}' ;;
  "worktree create") mkdir -p '{created}'; printf '%s\\n' '{json.dumps(payload)}' ;;
  *) exit 9 ;;
esac
''')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    backend = runtime.select("orca", tmp_path)
    handle = backend.create(tmp_path, "task-1", "abc123", "rig/task-1")
    assert handle.path == str(created)
    assert handle.branch == "task-1"
    assert handle.ref == {"orca_worktree_id": f"repo-id::{created}"}
    assert "worktree create --name task-1 --base-branch abc123 --setup skip --json" in calls.read_text()

    payload["worktree"].pop("id")
    _fake_orca(tmp_path, f'''case "$1 $2" in
  "status --json") printf '%s\\n' '{_READY}' ;;
  "worktree create") printf '%s\\n' '{json.dumps(payload)}' ;;
esac
''')
    with pytest.raises(runtime.RuntimeError_, match="stable worktree id"):
        backend.create(tmp_path, "task-2", "abc123", "rig/task-2")


def test_new_entrypoint_selects_orca_and_persists_its_structured_identity(
        tmp_path, monkeypatch, orca_session):
    from rig_workbench.workbench import cli, lifecycle

    root = _repo(tmp_path)
    created = tmp_path / "orca-task"
    payload = {"worktree": {"id": f"repo::{created}", "path": str(created),
                             "branch": "orca-task"}}
    _fake_orca(tmp_path, f'''case "$1 $2" in
  "status --json") printf '%s\\n' '{_READY}' ;;
  "worktree create") mkdir -p '{created}'; printf '%s\\n' '{json.dumps(payload)}' ;;
  *) exit 9 ;;
esac
''')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.chdir(root)
    monkeypatch.setattr(lifecycle, "make_task_id", lambda _slug: "orca-task")
    monkeypatch.setattr(sys, "argv", ["workbench.py", "new", "task", "--type",
                                      "feature", "--runtime", "orca"])
    cli.main()
    state = json.loads((root / ".rig/runs/orca-task/task.json").read_text())
    assert state["branch"] == "orca-task"
    assert state["worktree_path"] == str(created)
    assert state["worktree"]["runtime"] == "orca"
    assert state["worktree"]["ref"] == {"orca_worktree_id": f"repo::{created}"}


def test_remove_targets_the_recorded_identifier_not_the_path(
        tmp_path, monkeypatch, orca_session):
    calls = tmp_path / "calls"
    _fake_orca(tmp_path, f'''printf '%s\\n' "$*" >> '{calls}'
printf '%s\\n' '{_READY}'
''')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    backend = runtime.select("orca", tmp_path)
    handle = runtime.WorktreeHandle(runtime="orca", path="/not/identity", branch="task-1",
                                    ref={"orca_worktree_id": "repo::/real/path"})
    backend.remove(tmp_path, handle)
    assert "worktree rm --worktree id:repo::/real/path --force --json" in calls.read_text()


@pytest.mark.parametrize("bad_id", ["", "repo\u200b::/x", "repo\n::/x"])
def test_create_rejects_missing_or_deceptive_identity_with_an_ordinary_control(
        tmp_path, monkeypatch, orca_session, bad_id):
    good = {"worktree": {"id": "repo::/x", "path": "/x", "branch": "task"}}
    bad = {"worktree": {**good["worktree"], "id": bad_id}}
    _fake_orca(tmp_path, f'''case "$1 $2" in
  "status --json") printf '%s\\n' '{_READY}' ;;
  "worktree create") printf '%s\\n' '{json.dumps(bad)}' ;;
esac
''')
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    backend = runtime.select("orca", tmp_path)
    with pytest.raises(runtime.RuntimeError_):
        backend.create(tmp_path, "task", "base", "rig/task")

    _fake_orca(tmp_path, f'''case "$1 $2" in
  "status --json") printf '%s\\n' '{_READY}' ;;
  "worktree create") printf '%s\\n' '{json.dumps(good)}' ;;
esac
''')
    assert backend.create(tmp_path, "task", "base", "rig/task").ref["orca_worktree_id"] == "repo::/x"
