"""Interactive Mission Control: browser actions must stay behind RIG Core."""

import http.client
import html
import json
import re
import shutil
import subprocess
import threading

import pytest

from rig_workbench import mission_server


_MISSING = object()


def _rendered_run_index(run_index=_MISSING):
    """Run the page refresh against a tiny DOM and return the section's rendered HTML."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    snapshot = {
        "operations": {"tasks_active": 0, "tasks_total": 0, "gate_counts": {}},
        "production": {
            "incident_rate_pct": None,
            "incidents": 0,
            "outcomes_recorded": 0,
            "outcome_coverage_pct": None,
            "accepted_tasks": 0,
        },
        "core": [],
        "live": {"tasks": [], "task_types": []},
        "jobs": {"worker": {}, "items": [], "counts": {}, "providers": []},
    }
    if run_index is not _MISSING:
        snapshot["run_index"] = run_index

    from rig_workbench.mission_ui import JS_TEMPLATE

    harness = r"""
const payload=__PAYLOAD__;
const elements=new Map();
function element(){return {innerHTML:'',textContent:'',value:'',dataset:{},hidden:false,
  classList:{add(){},remove(){}}};}
global.document={
  querySelector(q){if(!elements.has(q))elements.set(q,element());return elements.get(q);},
  querySelectorAll(){return [];}
};
global.fetch=async()=>({ok:true,status:200,json:async()=>payload});
global.confirm=()=>false;
global.prompt=()=>null;
global.setInterval=()=>0;
__PAGE_JS__
setImmediate(()=>process.stdout.write(JSON.stringify({
  state:document.querySelector('#live-state').textContent,
  html:document.querySelector('#run-index').innerHTML
})));
"""
    script = harness.replace("__PAYLOAD__", json.dumps(snapshot)).replace(
        "__PAGE_JS__", JS_TEMPLATE.replace("__CSRF__", json.dumps("test-token"))
    )
    proc = subprocess.run(
        [node, "-"], input=script, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout)
    assert rendered["state"] == "LIVE", proc.stderr
    return rendered["html"]


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


def test_populated_run_index_renders_its_cross_project_issue_values():
    run_index = {
        "rows": [{"run_id": "run-3"}],
        "projects": ["/alpha", "/beta"],
        "shown": 1,
        "total": 3,
        "unreadable": 2,
        "truncated": True,
        "by_issue": {
            "#558": {
                "last_final": "DONE",
                "runs": 3,
                "projects": ["/alpha", "/beta"],
                "sessions": ["session-a", "session-b"],
            }
        },
    }

    page = mission_server.interactive_html("csrf-secret")
    rendered = _rendered_run_index(run_index)

    assert 'id="run-index"' in page
    assert "projects" in rendered and '<div class="value">2</div>' in rendered
    assert "runs" in rendered and '<div class="value">3</div>' in rendered
    assert "#558" in rendered and "last final: DONE" in rendered
    assert "3 runs" in rendered
    assert "projects: /alpha, /beta" in rendered
    assert "sessions: session-a, session-b" in rendered
    assert "2 unreadable records" in rendered
    assert "history truncated" in rendered


def test_populated_run_index_renders_verdict_counts_without_a_scoreboard():
    rendered = _rendered_run_index({
        "rows": [{"run_id": "run-1"}],
        "projects": ["/alpha"],
        "total": 1,
        "by_verifier": {
            "codex": {"ok": 40, "not_ok": 3, "unknown": 2, "runs": 1},
        },
    })

    assert "Recorded verifier verdicts" in rendered
    assert "not-OK verdicts: 3 · unknown verdicts: 2 · OK verdicts: 40" in rendered
    assert "1 run represented" in rendered
    assert "not pass rates, reviewer detection rates, or quality scores" in rendered
    assert "/rig:drill" in rendered
    # The card carries the disavowal too, not only the section above it. A reader who
    # sees one provider's counts without scrolling to the heading is the reader most
    # likely to divide them.
    assert "Recorded verdicts, not a quality score" in rendered
    # `_by_verifier` returns counts and the docstring forbids presenting a rate. Sampling
    # for the strings we happen to have written would not catch a rate appearing later,
    # so this asserts the property: every number in the verifier section is one the
    # projection supplied. 40/43 = 93 would fail here, and so would any percentage.
    section = rendered.split("Recorded verifier verdicts", 1)[1].split("run-index-issues", 1)[0]
    supplied = {"40", "3", "2", "1"}
    assert set(re.findall(r"\d+", section)) <= supplied, re.findall(r"\d+", section)


def test_absent_by_verifier_renders_its_empty_state_without_error():
    rendered = _rendered_run_index({
        "rows": [{"run_id": "run-1"}],
        "projects": ["/alpha"],
        "by_issue": {},
    })

    assert '<div class="empty">No verifier verdicts recorded.</div>' in rendered


def test_empty_by_verifier_renders_its_empty_state_without_error():
    rendered = _rendered_run_index({
        "rows": [{"run_id": "run-1"}],
        "projects": ["/alpha"],
        "by_verifier": {},
        "by_issue": {},
    })

    assert '<div class="empty">No verifier verdicts recorded.</div>' in rendered


def test_verifier_provider_name_with_html_metacharacters_is_escaped():
    provider = '<provider&"name>'
    rendered = _rendered_run_index({
        "rows": [{"run_id": "run-1"}],
        "projects": ["/alpha"],
        "by_verifier": {
            provider: {"ok": 1, "not_ok": 0, "unknown": 0, "runs": 1},
        },
    })

    assert provider not in rendered
    assert html.escape(provider, quote=True) in rendered


def test_absent_run_index_renders_the_empty_state_without_taking_the_page_down():
    rendered = _rendered_run_index()

    assert rendered == '<div class="empty">No indexed runs yet.</div>'


def test_run_index_with_no_rows_renders_the_empty_state_without_error():
    rendered = _rendered_run_index({"rows": [], "by_issue": {}, "projects": []})

    assert rendered == '<div class="empty">No indexed runs yet.</div>'


def test_every_run_index_value_with_html_metacharacters_is_escaped():
    unsafe = {
        "issue": '<issue&"ref>',
        "final": '<final&"state>',
        "project": '<project&"name>',
        "session": '<session&"id>',
    }
    rendered = _rendered_run_index({
        "rows": [{"run_id": "run-1"}],
        "projects": [unsafe["project"]],
        "total": 1,
        "by_issue": {
            unsafe["issue"]: {
                "last_final": unsafe["final"],
                "runs": 1,
                "projects": [unsafe["project"]],
                "sessions": [unsafe["session"]],
            }
        },
    })

    for value in unsafe.values():
        assert value not in rendered
        assert html.escape(value, quote=True) in rendered


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


def test_the_snapshot_carries_the_cross_project_run_index(tmp_path, monkeypatch):
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

    index = mission_server.live_snapshot(tmp_path)["run_index"]

    assert index["projects"] == ["/another", "/elsewhere"]
    assert [row["run_id"] for row in index["rows"]] == ["orc-b", "orc-a"]


def test_a_machine_with_no_global_log_still_renders(tmp_path, monkeypatch):
    """The board must open on a machine that has never finished a run — the empty state is a
    thing to draw, not an exception to raise inside a request handler."""
    from rig_workbench import mission_server
    from rig_workbench.orchestrate import config

    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "nothing-here.jsonl")

    index = mission_server.live_snapshot(tmp_path)["run_index"]

    assert index["exists"] is False and index["rows"] == []


def test_the_run_index_does_not_take_the_governance_fleet_key(tmp_path, monkeypatch):
    """The sensor for a mistake already made. `build_snapshot` puts the multi-repository
    governance conformance rollup under `fleet`, and `mission_control._render_fleet` reads it
    from there. A first version of the cross-project run index was written to that same key,
    silently replacing one measurement with an unrelated one that happened to be about several
    projects too.

    Nothing caught it: the new tests asserted the new shape and found it, and no existing test
    covered what `live_snapshot` leaves in `fleet`. This asserts both keys, so the two
    meanings cannot merge again."""
    from rig_workbench import mission_server
    from rig_workbench.orchestrate import config

    monkeypatch.setattr(config, "GLOBAL_RUNS_PATH", tmp_path / "none.jsonl")

    snapshot = mission_server.live_snapshot(tmp_path)

    assert set(snapshot["fleet"]) >= {"configured"}          # the governance rollup, intact
    assert "rows" not in snapshot["fleet"]
    assert set(snapshot["run_index"]) >= {"rows", "projects"}
