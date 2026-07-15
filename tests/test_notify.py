"""Slack/Teams webhook notifications (#287).

scripts/notify.py posts to Slack/Teams incoming webhooks via urllib only (no
SDK dependency). Verified here against a local HTTP server, plus the
dry-run and no-webhook error paths.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTIFY = REPO_ROOT / "scripts" / "notify.py"

_SPEC = importlib.util.spec_from_file_location("notify", NOTIFY)
notify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(notify)


def test_build_payload_slack_folds_title_into_text():
    payload = notify.build_payload("slack", "rig", "accept pending")
    assert payload == {"text": "*rig*\naccept pending"}


def test_build_payload_slack_without_title():
    payload = notify.build_payload("slack", "", "just the message")
    assert payload == {"text": "just the message"}


def test_build_payload_teams_uses_message_card_shape():
    payload = notify.build_payload("teams", "rig", "REJECT")
    assert payload["@type"] == "MessageCard"
    assert payload["title"] == "rig"
    assert payload["text"] == "REJECT"


def test_build_payload_unknown_format_raises():
    try:
        notify.build_payload("discord", "t", "m")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "unsupported format" in str(e)


def run_cli(args, env=None):
    import os

    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(NOTIFY), *args],
                          capture_output=True, text=True, timeout=30, env=full_env)


def test_dry_run_prints_payload_without_sending():
    r = run_cli(["--format", "slack", "--message", "hi", "--dry-run"])
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"text": "hi"}


def test_missing_webhook_fails_with_a_clear_error():
    env = {"RIG_NOTIFY_WEBHOOK": ""}
    r = run_cli(["--format", "slack", "--message", "hi"], env=env)
    assert r.returncode != 0
    assert "neither --webhook nor RIG_NOTIFY_WEBHOOK" in r.stderr


class _CapturingHandler(BaseHTTPRequestHandler):
    captured = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _CapturingHandler.captured.append(json.loads(body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):  # silence default request logging
        pass


def _start_mock_server():
    server = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_sends_slack_payload_to_a_real_webhook_endpoint():
    _CapturingHandler.captured.clear()
    server = _start_mock_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/webhook"
        r = run_cli(["--webhook", url, "--format", "slack", "--title", "rig", "--message", "accept pending"])
        assert r.returncode == 0
        assert "notification sent" in r.stdout
        assert _CapturingHandler.captured == [{"text": "*rig*\naccept pending"}]
    finally:
        server.shutdown()


def test_sends_teams_payload_to_a_real_webhook_endpoint():
    _CapturingHandler.captured.clear()
    server = _start_mock_server()
    try:
        url = f"http://127.0.0.1:{server.server_port}/webhook"
        r = run_cli(["--webhook", url, "--format", "teams", "--title", "rig", "--message", "REJECT"])
        assert r.returncode == 0
        assert _CapturingHandler.captured[0]["text"] == "REJECT"
    finally:
        server.shutdown()
