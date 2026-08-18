"""Interactive localhost Mission Control for RIG.

The browser is a presentation/interaction surface, never a second policy engine.
Every mutating request is translated into an argv list for ``rig_workbench.cli``
and executed without a shell, so the existing workbench/governance enforcement
remains the only authority for accept, discard and approvals.

Autonomous work is durable without becoming a second scheduler: Mission Control
persists work through RIG's existing local queue and launches a detached queue
drain worker. Closing the browser or this HTTP server does not own that worker's
lifetime.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import exitcodes
from .evidence import find_repo_root
from .mission_control import build_snapshot
from .workbench import assurance, graph as assurance_graph
from .mission_jobs import (
    ALLOWED_PROVIDERS,
    assert_retryable,
    assert_worker_compatible,
    ensure_worker,
    queue_items,
    submission_lock,
    validate_run_request,
    worker_log_tail,
    worker_state,
)
from .mission_ui import interactive_html
from .workbench.config import TASK_TYPES
from .workbench.reporting import read_all_tasks
from .workbench.state import gate_status, load_json, runs_dir

MAX_BODY_BYTES = 64 * 1024
COMMAND_TIMEOUT_SECONDS = 120
POLL_MS = 2000
_TASK_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_QUEUE_ID = re.compile(r"^[0-9]+$")
_JOB_START_LOCK = threading.Lock()


def _json_file(path: pathlib.Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _task_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    if not _TASK_ID.fullmatch(task_id):
        raise ValueError("invalid task id")
    path = root / ".rig" / "runs" / task_id
    if not (path / "task.json").is_file():
        raise ValueError(f"task not found: {task_id}")
    return path


def task_detail(root: pathlib.Path, task_id: str) -> dict[str, Any]:
    base = _task_dir(root, task_id)
    task = _json_file(base / "task.json", {})
    steps = _json_file(base / "steps.json", {"steps": []})
    acceptance = _json_file(base / "acceptance.json", {"checks": []})
    review = _json_file(base / "review.json", {})
    outcome = _json_file(base / "outcome.json", None)
    if isinstance(acceptance, dict) and acceptance.get("checks"):
        acceptance = dict(acceptance)
        acceptance["status"] = gate_status(acceptance)
    return {"task": task, "steps": steps, "acceptance": acceptance,
            "review": review, "outcome": outcome,
            "assurance": _assurance(root, task_id),
            "graph": _graph(root, task_id)}


def _graph(root: pathlib.Path, task_id: str) -> dict[str, Any] | None:
    """The task's resolved execution graph (#426), or None with the reason.

    Same containment as the receipt: a projection failing must cost a panel, never
    the page. Returned as its own key rather than folded into `assurance` because a
    client may want one without the other.
    """
    try:
        return assurance_graph.build_graph(root, task_id)
    except Exception as e:                                     # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "nodes": [], "edges": []}


def _assurance(root: pathlib.Path, task_id: str) -> dict[str, Any]:
    """The task's Assurance Receipt, built fresh rather than read off disk (#428).

    Built here so the answer describes the task as it is now: a receipt written to
    `.rig/runs/<id>/assurance.json` at some earlier point may be stale, and Mission
    Control serving a stale one without saying so would be the failure the receipt's
    own freshness check exists to prevent. `freshness` reports how the file on disk
    compares, so a client can show both without either being silently substituted.

    A failure here must not take the task detail down with it. The receipt is a
    projection of the same files this response already carries; losing it costs a
    panel, and returning nothing costs the operator the page.
    """
    try:
        receipt = assurance.build_receipt(root, task_id)
    except Exception as e:                                     # noqa: BLE001
        return {"receipt": None, "error": f"{type(e).__name__}: {e}", "stored": None}
    stored = _json_file(_task_dir(root, task_id) / "assurance.json", None)
    return {
        "receipt": receipt,
        "stored": assurance.verify(root, stored) if isinstance(stored, dict) else None,
    }


def durable_snapshot(root: pathlib.Path) -> dict[str, Any]:
    items, error = queue_items(root)
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "?")
        counts[status] = counts.get(status, 0) + 1
    return {
        "queue_error": error,
        "items": list(reversed(items[-40:])),
        "counts": counts,
        "worker": worker_state(root),
        "worker_log_tail": worker_log_tail(root),
        "providers": list(ALLOWED_PROVIDERS),
    }


def live_snapshot(root: pathlib.Path) -> dict[str, Any]:
    snapshot = build_snapshot(root)
    base = runs_dir(root)
    tasks = read_all_tasks(base)
    tasks.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "",
               reverse=True)
    index = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        acceptance = load_json(base / task_id / "acceptance.json", {"checks": []})
        gate = gate_status(acceptance) if acceptance.get("checks") else "unmeasured"
        index.append({
            "task_id": task_id,
            "input": task.get("input") or "",
            "task_type": task.get("task_type") or "?",
            "recipe": task.get("recipe"),
            "status": task.get("status") or "?",
            "gate": gate,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
        })
    snapshot["live"] = {
        "poll_ms": POLL_MS,
        "task_types": sorted(TASK_TYPES),
        "tasks": index,
        "interactive": True,
        "force_available": False,
    }
    snapshot["jobs"] = durable_snapshot(root)
    return snapshot


def _run_cli(root: pathlib.Path, argv: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    env["RIG_INVOKER"] = "mission-control/v2"
    command = [sys.executable, "-m", "rig_workbench.cli", *argv]
    try:
        proc = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True,
                              timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "exit_code": None, "stdout": exc.stdout or "",
                "stderr": f"command timed out after {COMMAND_TIMEOUT_SECONDS}s",
                "argv": argv}
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr, "argv": argv}


def action_argv(action: str, task_id: str | None, payload: dict[str, Any]) -> list[str]:
    if action == "new":
        text = str(payload.get("input") or "").strip()
        task_type = str(payload.get("task_type") or "").strip()
        if not text or len(text) > 4000:
            raise ValueError("task input is required and must be <= 4000 characters")
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type!r}")
        return ["wb", "new", text, "--type", task_type]
    if task_id is None or not _TASK_ID.fullmatch(task_id):
        raise ValueError("a valid task id is required")
    if action == "accept":
        return ["wb", "accept", task_id]
    if action == "discard":
        if payload.get("confirm") != task_id:
            raise ValueError("discard requires exact task-id confirmation")
        return ["wb", "discard", task_id, "--yes"]
    if action == "approval":
        decision = str(payload.get("decision") or "")
        if decision not in {"grant", "deny"}:
            raise ValueError("approval decision must be grant|deny")
        argv = ["govern", "approve", decision, task_id]
        note = str(payload.get("note") or "").strip()
        if note:
            argv += ["--note", note[:2000]]
        return argv
    if action == "outcome":
        status = str(payload.get("status") or "")
        if status not in {"ok", "incident"}:
            raise ValueError("outcome status must be ok|incident")
        argv = ["wb", "record-outcome", task_id, "--status", status]
        note = str(payload.get("note") or "").strip()
        if note:
            argv += ["--note", note[:4000]]
        return argv
    raise ValueError(f"unsupported action: {action}")


def start_durable_run(root: pathlib.Path, payload: dict[str, Any]) -> dict[str, Any]:
    spec = validate_run_request(payload)
    # _JOB_START_LOCK serializes threads in this HTTP process; submission_lock
    # closes the same race across two Mission Control processes on one repo.
    with _JOB_START_LOCK, submission_lock(root):
        assert_worker_compatible(
            root,
            provider=spec["provider"],
            verifier_provider=spec["verifier_provider"],
            max_parallel=spec["max_parallel"],
        )
        queued = _run_cli(root, ["queue", "add", spec["task"], "--backend", "local"])
        if not queued["ok"]:
            return queued
        worker = ensure_worker(
            root,
            provider=spec["provider"],
            verifier_provider=spec["verifier_provider"],
            max_parallel=spec["max_parallel"],
        )
    return {"ok": True, "queued": queued, "worker": worker, "spec": spec}


def retry_durable_run(root: pathlib.Path, queue_id: str,
                      payload: dict[str, Any]) -> dict[str, Any]:
    if not _QUEUE_ID.fullmatch(queue_id):
        raise ValueError("invalid local queue id")
    with _JOB_START_LOCK, submission_lock(root):
        # A live provider still owns a running item. Requeueing it would create
        # a misleading transition that its eventual DONE/FAIL could overwrite.
        assert_retryable(root, queue_id)
        current = worker_state(root)
        defaults = {
            "task": "retry",
            "provider": payload.get("provider") or current.get("provider") or "rig",
            "verifier_provider": (
                payload.get("verifier_provider") or current.get("verifier_provider")
                or payload.get("provider") or current.get("provider") or "rig"
            ),
            "max_parallel": payload.get("max_parallel") or current.get("max_parallel") or 1,
        }
        spec = validate_run_request(defaults)
        assert_worker_compatible(
            root,
            provider=spec["provider"],
            verifier_provider=spec["verifier_provider"],
            max_parallel=spec["max_parallel"],
        )
        retried = _run_cli(root, ["queue", "retry", queue_id, "--backend", "local"])
        if not retried["ok"]:
            return retried
        worker = ensure_worker(
            root,
            provider=spec["provider"],
            verifier_provider=spec["verifier_provider"],
            max_parallel=spec["max_parallel"],
        )
    return {"ok": True, "retried": retried, "worker": worker}


class MissionControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: pathlib.Path, csrf_token: str):
        super().__init__(address, MissionControlHandler)
        self.root = root
        self.csrf_token = csrf_token
        _host, port = self.server_address[:2]
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class MissionControlHandler(BaseHTTPRequestHandler):
    server: MissionControlHTTPServer

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[mission-control] " + (fmt % args) + "\n")

    def _json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _payload(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if size <= 0 or size > MAX_BODY_BYTES:
            raise ValueError("request body must be non-empty and <= 64 KiB")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("Content-Type must be application/json")
        try:
            value = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _authorize_post(self) -> bool:
        supplied = self.headers.get("X-RIG-CSRF", "")
        if not secrets.compare_digest(supplied, self.server.csrf_token):
            self._json(403, {"error": "invalid or missing CSRF token"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in self.server.allowed_origins:
            self._json(403, {"error": "origin is not this Mission Control server"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/":
                self._html(interactive_html(self.server.csrf_token))
                return
            if path == "/api/snapshot":
                self._json(200, live_snapshot(self.server.root))
                return
            match = re.fullmatch(r"/api/tasks/([^/]+)(/diff)?", path)
            if match:
                task_id = urllib.parse.unquote(match.group(1))
                _task_dir(self.server.root, task_id)
                if match.group(2):
                    result = _run_cli(self.server.root, ["wb", "diff", task_id])
                    self._json(200 if result["ok"] else 409, result)
                else:
                    self._json(200, task_detail(self.server.root, task_id))
                return
            self._json(404, {"error": "not found"})
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_post():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._payload()
            if path == "/api/tasks":
                result = _run_cli(self.server.root, action_argv("new", None, payload))
            elif path == "/api/jobs":
                result = start_durable_run(self.server.root, payload)
            else:
                retry = re.fullmatch(r"/api/jobs/([0-9]+)/retry", path)
                if retry:
                    result = retry_durable_run(self.server.root, retry.group(1), payload)
                else:
                    match = re.fullmatch(r"/api/tasks/([^/]+)/(accept|discard|approval|outcome)", path)
                    if not match:
                        self._json(404, {"error": "not found"})
                        return
                    task_id = urllib.parse.unquote(match.group(1))
                    _task_dir(self.server.root, task_id)
                    result = _run_cli(self.server.root,
                                      action_argv(match.group(2), task_id, payload))
            self._json(200 if result.get("ok") else 409, result)
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rig-mission-control-live",
                                     description="interactive localhost RIG Mission Control")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open the browser automatically")
    return parser


@exitcodes.guard
def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be between 1 and 65535")
    root = find_repo_root(args.repo)
    token = secrets.token_urlsafe(32)
    server = MissionControlHTTPServer(("127.0.0.1", args.port), root, token)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"RIG Mission Control v2: {url}")
    print(f"repo: {root}")
    print("security: loopback-only · per-process CSRF · no GUI force bypass")
    print("durable runs: .rig/queue.json + detached queue worker")
    if not args.no_open:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nMission Control stopped. Detached AI queue workers keep running.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
