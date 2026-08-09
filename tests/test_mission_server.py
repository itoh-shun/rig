"""Interactive Mission Control: browser actions must stay behind RIG Core."""

import json

import pytest

from rig_workbench import mission_server


def test_accept_has_no_force_path():
    argv = mission_server.action_argv("accept", "rig-20260809-login", {})
    assert argv == ["wb", "accept", "rig-20260809-login"]
    assert "--force" not in argv


def test_discard_requires_exact_task_confirmation():
    with pytest.raises(ValueError, match="exact task-id"):
        mission_server.action_argv(
            "discard", "rig-20260809-login", {"confirm": "yes"}
        )
    assert mission_server.action_argv(
        "discard", "rig-20260809-login", {"confirm": "rig-20260809-login"}
    ) == ["wb", "discard", "rig-20260809-login", "--yes"]


def test_browser_cannot_spoof_governance_actor():
    argv = mission_server.action_argv(
        "approval",
        "rig-20260809-login",
        {"decision": "grant", "actor": "root", "note": "looks good"},
    )
    assert argv == [
        "govern", "approve", "grant", "rig-20260809-login", "--note", "looks good"
    ]
    assert "--actor" not in argv
    assert "root" not in argv


def test_outcome_is_constrained_to_known_statuses():
    assert mission_server.action_argv(
        "outcome", "rig-20260809-login", {"status": "incident", "note": "rollback"}
    ) == [
        "wb", "record-outcome", "rig-20260809-login", "--status", "incident",
        "--note", "rollback",
    ]
    with pytest.raises(ValueError, match=r"ok\|incident"):
        mission_server.action_argv(
            "outcome", "rig-20260809-login", {"status": "ignored"}
        )


def test_new_task_uses_workbench_new_and_validates_type():
    task_type = sorted(mission_server.TASK_TYPES)[0]
    argv = mission_server.action_argv(
        "new", None, {"input": "fix the login race", "task_type": task_type}
    )
    assert argv == ["wb", "new", "fix the login race", "--type", task_type]
    with pytest.raises(ValueError, match="unknown task type"):
        mission_server.action_argv(
            "new", None, {"input": "x", "task_type": "definitely-not-a-type"}
        )


def test_task_id_cannot_escape_runs_directory(tmp_path):
    with pytest.raises(ValueError, match="invalid task id"):
        mission_server._task_dir(tmp_path, "../secrets")


def test_task_detail_reads_existing_artifacts(tmp_path):
    task_id = "rig-20260809-example"
    run = tmp_path / ".rig" / "runs" / task_id
    run.mkdir(parents=True)
    (run / "task.json").write_text(json.dumps({
        "task_id": task_id,
        "input": "example",
        "task_type": "bugfix",
        "status": "running",
    }), encoding="utf-8")
    (run / "steps.json").write_text(json.dumps({
        "steps": [{"name": "inspect", "status": "passed"}]
    }), encoding="utf-8")
    (run / "acceptance.json").write_text(json.dumps({
        "status": "pending",
        "presets": [],
        "checks": [{"name": "tests", "status": "passed"}],
    }), encoding="utf-8")

    detail = mission_server.task_detail(tmp_path, task_id)
    assert detail["task"]["task_id"] == task_id
    assert detail["steps"]["steps"][0]["status"] == "passed"
    assert detail["acceptance"]["status"] in {"passed", "passed_with_warnings"}


def test_server_uses_random_csrf_and_loopback_address(tmp_path):
    token = "test-token"
    server = mission_server.MissionControlHTTPServer(("127.0.0.1", 0), tmp_path, token)
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1"
        assert port > 0
        assert server.csrf_token == token
        assert f"http://127.0.0.1:{port}" in server.allowed_origins
    finally:
        server.server_close()


def test_interactive_html_contains_live_controls_but_no_force():
    page = mission_server.interactive_html("csrf-secret")
    assert "RIG Mission Control" in page
    assert "Register isolated task" in page
    assert "View Diff" in page
    assert "Accept" in page
    assert "Discard" in page
    assert "Approve" in page
    assert "Outcome: Incident" in page
    assert "csrf-secret" in page
    assert "--force" not in page


def test_body_limit_is_bounded():
    assert mission_server.MAX_BODY_BYTES <= 64 * 1024
