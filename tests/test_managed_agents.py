"""Experimental Managed Agents API backend for review fan-out (#295).

Mocks the full call sequence at the urllib.request.urlopen boundary (worker/
coordinator creation, session creation, event send, threads polling) — not
connected to the real Anthropic API (would mean live traffic and billing
risk, and the real Managed Agents REST paths are themselves unconfirmed;
see run_managed_agents_fanout's docstring).
"""

import json
from unittest.mock import patch

from rig_workbench.orchestrate.providers import run_managed_agents_fanout
from rig_workbench.orchestrate.runstate import new_state


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(worker_ids, thread_rows):
    """Route each call by (method, path) to a canned response, mimicking the real
    create-worker -> create-coordinator -> create-session -> send-event -> poll-threads
    sequence."""
    calls = []

    def _fake_urlopen(req, timeout=None):
        path = req.full_url
        calls.append((req.get_method(), path))
        if path.endswith("/v1/agents"):
            body = json.loads(req.data.decode("utf-8"))
            if body["name"] == "coordinator":
                return _Resp({"id": "agent-coordinator"})
            idx = len([c for c in calls if c[1].endswith("/v1/agents")]) - 1
            return _Resp({"id": worker_ids[idx]})
        if path.endswith("/v1/sessions"):
            return _Resp({"id": "session-1"})
        if path.endswith("/events"):
            return _Resp({"ok": True})
        if path.endswith("/threads"):
            return _Resp({"data": thread_rows})
        raise AssertionError(f"unexpected request: {req.get_method()} {path}")

    return _fake_urlopen, calls


def test_missing_environment_id_errors_without_calling_the_api():
    with patch("urllib.request.urlopen", side_effect=AssertionError("must not be called")):
        results = run_managed_agents_fanout("review this", ["security"], {})
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "environment_id" in results[0]["note"]


def test_two_workers_report_pass_and_fail():
    worker_ids = ["agent-security", "agent-qa"]
    threads = [
        {"agent_id": "agent-security", "content": [{"type": "text", "text": "VERDICT: PASS"}],
         "usage": {"input_tokens": 5, "output_tokens": 2}},
        {"agent_id": "agent-qa", "content": [{"type": "text", "text": "VERDICT: FAIL"}],
         "usage": {"input_tokens": 4, "output_tokens": 3}},
        {"agent_id": "agent-coordinator", "content": [{"type": "text", "text": "aggregated"}]},
    ]
    fake, calls = _fake_urlopen_factory(worker_ids, threads)
    cfg = {"environment_id": "env-123", "_token_usage": {}}
    state = new_state("t", [], None)
    with patch("urllib.request.urlopen", side_effect=fake):
        results = run_managed_agents_fanout("review this", ["security", "qa"], cfg,
                                            state=state, step_id="review")
    assert [r["persona"] for r in results] == ["qa", "security"]  # sorted deterministically
    by_persona = {r["persona"]: r for r in results}
    assert by_persona["security"]["ok"] is True
    assert by_persona["qa"]["ok"] is False
    assert all(r["provider"] == "managed-agents" for r in results)
    # Token usage accumulated from worker threads only (coordinator thread has none).
    assert cfg["_token_usage"]["managed-agents"]["prompt_tokens"] == 9
    assert cfg["_token_usage"]["managed-agents"]["completion_tokens"] == 5
    event = next(h for h in state["history"] if h["action"] == "MANAGED_AGENTS_SESSION")
    assert event["workers"] == 2 and event["session_id"] == "session-1"


def test_coordinator_thread_is_not_counted_as_a_review_vote():
    worker_ids = ["agent-security"]
    threads = [
        {"agent_id": "agent-security", "content": [{"type": "text", "text": "VERDICT: PASS"}]},
        {"agent_id": "agent-coordinator", "content": [{"type": "text", "text": "summary"}]},
    ]
    fake, _ = _fake_urlopen_factory(worker_ids, threads)
    with patch("urllib.request.urlopen", side_effect=fake):
        results = run_managed_agents_fanout("review this", ["security"],
                                            {"environment_id": "env-1"})
    assert len(results) == 1
    assert results[0]["persona"] == "security"


def test_worker_that_never_reports_in_is_marked_timeout_not_dropped():
    worker_ids = ["agent-security", "agent-qa"]
    # Only the security worker's thread ever shows up; qa never reports in.
    threads = [{"agent_id": "agent-security", "content": [{"type": "text", "text": "VERDICT: PASS"}]}]
    fake, _ = _fake_urlopen_factory(worker_ids, threads)
    cfg = {"environment_id": "env-1", "managed_agents_max_polls": 1, "managed_agents_poll_interval": 0}
    with patch("urllib.request.urlopen", side_effect=fake):
        results = run_managed_agents_fanout("review this", ["security", "qa"], cfg)
    by_persona = {r["persona"]: r for r in results}
    assert by_persona["security"]["ok"] is True
    assert by_persona["qa"]["ok"] is False
    assert "timeout" in by_persona["qa"]["note"]


def test_api_error_is_caught_and_reported_not_raised():
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        results = run_managed_agents_fanout("review this", ["security"], {"environment_id": "env-1"})
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "managed-agents error" in results[0]["note"]
