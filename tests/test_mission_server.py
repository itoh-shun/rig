"""Interactive Mission Control: browser actions must stay behind RIG Core."""

import http.client
import json
import threading

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


def test_interactive_html_contains_live_and_durable_controls_but_no_force():
    page = mission_server.interactive_html("csrf-secret")
    assert "RIG Mission Control" in page
    assert "Autonomous AI Run" in page
    assert "Start AI Run" in page
    assert "AI Queue" in page
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


# ── DNS rebinding: the read side needed a Host check ─────────────────────────


@pytest.fixture
def live_server(tmp_path):
    """A real Mission Control on a real socket.

    Driven over HTTP rather than by calling the handler, because the defect this covers lived
    between the socket and the router: every unit-level test of this module passed while
    `GET /api/snapshot` answered any caller that could reach the port.
    """
    server = mission_server.MissionControlHTTPServer(("127.0.0.1", 0), tmp_path, "test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(server, method, path, host=None, headers=None):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            connection.putheader("Host", host)
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        connection.putheader("Content-Length", "0")
        connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/", "/api/snapshot"])
def test_a_loopback_request_is_served(live_server, path):
    """The positive control, and it earns its place: a Host check that refused everything
    would satisfy the rebinding test below while breaking the tool for its only real user."""
    port = live_server.server_address[1]
    status, _ = _request(live_server, "GET", path, host=f"127.0.0.1:{port}")
    assert status == 200


def test_the_hostname_alias_is_served_too(live_server):
    port = live_server.server_address[1]
    status, _ = _request(live_server, "GET", "/", host=f"localhost:{port}")
    assert status == 200


@pytest.mark.parametrize("path", ["/", "/api/snapshot"])
def test_a_request_addressed_to_somebody_elses_domain_is_refused(live_server, path):
    """DNS rebinding. Binding loopback stops a remote client from connecting; it does not stop
    a browser being told that `evil.example` resolves to 127.0.0.1. In that attack the socket
    is local and the page is same-origin with us as far as the browser is concerned, so the
    CSRF token is readable and Origin is absent on a GET — neither defends the read side.
    The Host header is what the attack cannot forge: it carries the name the victim's browser
    was sent to."""
    status, body = _request(live_server, "GET", path, host="evil.example")
    assert status == 403
    assert b"localhost" in body


def test_a_request_with_no_host_header_is_refused(live_server):
    """An HTTP/1.0 client may omit it. Absent is not loopback, and treating a missing header
    as permission is how a check ends up applying to everybody who bothers to set it."""
    status, _ = _request(live_server, "GET", "/", host=None)
    assert status == 403


def test_the_write_side_still_needs_its_token(live_server):
    """The Host check is added in front of the CSRF check, not in place of it."""
    port = live_server.server_address[1]
    status, _ = _request(live_server, "POST", "/api/jobs", host=f"127.0.0.1:{port}")
    assert status == 403


def test_the_snapshot_carries_the_cross_project_fleet(tmp_path, monkeypatch):
    """Mission Control read `.rig/runs/` and nothing else, so its view ended at the repository
    it was started in. `~/.rig/runs.jsonl` has carried `project` on every record the whole
    time; this is the snapshot finally opening it."""
    import json

    from rig_workbench import mission_server
    from rig_workbench.orchestrate import config

    log = tmp_path / "global-runs.jsonl"
    log.write_text("".join(json.dumps(record) + "\n" for record in (
        {"run_id": "orc-a", "ts": "2026-08-29T00:00:00+00:00", "recipe": "bugfix",
         "backend": "orchestrate", "final": "DONE", "project": "/elsewhere", "steps": []},
        {"run_id": "orc-b", "ts": "2026-08-29T01:00:00+00:00", "recipe": "review-only",
         "backend": "workbench", "final": "DONE", "project": "/another", "steps": []},
    )), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", log)

    fleet = mission_server.live_snapshot(tmp_path)["fleet"]

    assert fleet["projects"] == ["/another", "/elsewhere"]
    assert [row["run_id"] for row in fleet["rows"]] == ["orc-b", "orc-a"]


def test_a_machine_with_no_global_log_still_renders(tmp_path, monkeypatch):
    """The board must open on a machine that has never finished a run — the empty state is a
    thing to draw, not an exception to raise inside a request handler."""
    from rig_workbench import mission_server
    from rig_workbench.orchestrate import config

    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "nothing-here.jsonl")

    fleet = mission_server.live_snapshot(tmp_path)["fleet"]

    assert fleet["exists"] is False and fleet["rows"] == []
