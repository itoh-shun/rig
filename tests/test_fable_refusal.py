"""Fable 5 refusal-classifier and server-side fallback handling (#297).

Mocks the Anthropic Messages API's response shape (direct refusal, successful
fallback, normal response) at the urllib.request.urlopen boundary — not
connected to the real Anthropic API (would mean live traffic and billing risk).
"""

import json
from contextlib import contextmanager
from unittest.mock import patch

from rig_workbench.orchestrate.providers import run_anthropic_provider
from rig_workbench.orchestrate.runstate import new_state


@contextmanager
def _mock_response(payload):
    class _Resp:
        def read(self):
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_Resp()):
        yield


def test_normal_response_returns_text_and_records_usage():
    payload = {"stop_reason": "end_turn",
               "content": [{"type": "text", "text": "hello"}],
               "usage": {"input_tokens": 10, "output_tokens": 5}}
    cfg = {"_token_usage": {}}
    with _mock_response(payload):
        rc, out = run_anthropic_provider("hi", cfg)
    assert rc == 0
    assert out == "hello"
    assert cfg["_token_usage"]["anthropic"] == {"prompt_tokens": 10, "completion_tokens": 5,
                                                 "cache_read_input_tokens": 0, "calls": 1}


def test_direct_refusal_returns_failure_and_records_history(step_factory):
    payload = {"stop_reason": "refusal", "content": [],
               "stop_details": {"category": "cyber", "explanation": "attack tooling"}}
    state = new_state("t", [step_factory(id="a")], None)
    with _mock_response(payload):
        rc, out = run_anthropic_provider("hi", {}, state=state, step_id="a")
    assert rc == 1
    assert "cyber" in out
    event = next(h for h in state["history"] if h["action"] == "FABLE_REFUSAL")
    assert event["category"] == "cyber" and event["explanation"] == "attack tooling"


def test_refusal_without_state_still_fails_but_does_not_crash():
    payload = {"stop_reason": "refusal", "content": [],
               "stop_details": {"category": "bio", "explanation": "x"}}
    with _mock_response(payload):
        rc, out = run_anthropic_provider("hi", {})
    assert rc == 1 and "bio" in out


def test_successful_fallback_is_treated_as_transparent_success(step_factory):
    payload = {"stop_reason": "end_turn",
               "content": [{"type": "fallback", "from": {"model": "claude-fable-5"},
                           "to": {"model": "claude-opus-4-8"}},
                          {"type": "text", "text": "fallback answer"}],
               "usage": {"input_tokens": 20, "output_tokens": 8, "cache_read_input_tokens": 100}}
    state = new_state("t", [step_factory(id="a")], None)
    cfg = {"_token_usage": {}}
    with _mock_response(payload):
        rc, out = run_anthropic_provider("hi", cfg, state=state, step_id="a")
    assert rc == 0
    assert out == "fallback answer"
    event = next(h for h in state["history"] if h["action"] == "FABLE_FALLBACK")
    assert event["from_model"] == "claude-fable-5" and event["to_model"] == "claude-opus-4-8"
    assert cfg["_token_usage"]["anthropic"]["cache_read_input_tokens"] == 100


def test_fallback_requested_sets_beta_header_and_fallbacks_body():
    captured = {}

    class _Resp:
        def read(self):
            return json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        run_anthropic_provider("hi", {"fallback_model": "claude-opus-4-8"})
    assert captured["body"]["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert captured["headers"]["Anthropic-beta"] == "server-side-fallback-2026-06-01"


def test_connection_error_returns_failure_not_a_crash():
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        rc, out = run_anthropic_provider("hi", {})
    assert rc == 1 and "anthropic error" in out
