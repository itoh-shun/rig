"""Durable queue-worker lifecycle for interactive Mission Control.

The persistent task queue remains RIG's existing ``.rig/queue.json``.  This
module only records whether a detached queue-draining worker is active and where
its log lives.  It deliberately does not invent a second job scheduler.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from typing import Any

WORKER_SCHEMA = "rig.mission-worker/v1"
ALLOWED_PROVIDERS = ("rig", "claude", "codex", "grok", "lmstudio", "ollama", "mock")
MAX_PARALLEL = 8
_STARTING_STALE_SECONDS = 15
_SUBMIT_LOCK_STALE_SECONDS = 30


def _control_dir(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "mission-control"


def worker_state_path(root: pathlib.Path) -> pathlib.Path:
    return _control_dir(root) / "worker.json"


def worker_log_path(root: pathlib.Path) -> pathlib.Path:
    return _control_dir(root) / "worker.log"


@contextlib.contextmanager
def submission_lock(root: pathlib.Path, timeout: float = 5.0):
    """Cross-process lock around provider-check → enqueue → worker-launch.

    ``queue.json`` already serializes each individual mutation, but two Mission
    Control servers could otherwise both observe "no worker", enqueue different
    provider requests, then launch two queue drainers. Atomic directory creation
    gives a dependency-free lock on POSIX and Windows. The critical section is
    intentionally tiny; a 30s-old empty lock directory is treated as an
    abandoned launcher and reclaimed.
    """
    control = _control_dir(root)
    control.mkdir(parents=True, exist_ok=True)
    lock_dir = control / "submission.lock"
    deadline = time.monotonic() + timeout
    acquired = False
    while not acquired:
        try:
            lock_dir.mkdir()
            acquired = True
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > _SUBMIT_LOCK_STALE_SECONDS:
                    lock_dir.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                continue
            if time.monotonic() >= deadline:
                raise ValueError("another Mission Control process is submitting durable work")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def queue_items(root: pathlib.Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read the existing local queue without rewriting it on corruption."""
    path = root / ".rig" / "queue.json"
    if not path.is_file():
        return [], None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"unreadable queue: {exc}"
    if not isinstance(value, dict) or not isinstance(value.get("items", []), list):
        return [], "unreadable queue: .rig/queue.json must contain an object with items[]"
    return [x for x in value.get("items", []) if isinstance(x, dict)], None


def queue_item(root: pathlib.Path, queue_id: str) -> dict[str, Any]:
    items, error = queue_items(root)
    if error:
        raise ValueError(error)
    match = next((item for item in items if str(item.get("id")) == queue_id), None)
    if match is None:
        raise ValueError(f"queue item not found: {queue_id}")
    return match


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def worker_state(root: pathlib.Path) -> dict[str, Any]:
    state = _load_json(worker_state_path(root))
    if not state:
        return {"configured": False, "status": "idle", "alive": False}
    result = dict(state)
    result["configured"] = True
    status = result.get("status")
    alive = _pid_alive(result.get("pid")) if status == "running" else False
    if status == "starting":
        started = result.get("started_at")
        age = None
        if isinstance(started, str):
            try:
                stamp = dt.datetime.fromisoformat(started)
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=dt.timezone.utc)
                age = (dt.datetime.now().astimezone() - stamp).total_seconds()
            except ValueError:
                pass
        if age is not None and age > _STARTING_STALE_SECONDS:
            result["status"] = "stale"
    elif status == "running" and not alive:
        result["status"] = "stale"
    result["alive"] = alive
    return result


def worker_log_tail(root: pathlib.Path, max_bytes: int = 16_384) -> str:
    path = worker_log_path(root)
    if not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def validate_run_request(payload: dict[str, Any]) -> dict[str, Any]:
    task = str(payload.get("task") or "").strip()
    provider = str(payload.get("provider") or "rig").strip()
    verifier = str(payload.get("verifier_provider") or provider).strip()
    try:
        max_parallel = int(payload.get("max_parallel") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_parallel must be an integer") from exc
    if not task or len(task) > 4000:
        raise ValueError("task is required and must be <= 4000 characters")
    if provider not in ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")
    if verifier not in ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported verifier provider: {verifier!r}")
    if not (1 <= max_parallel <= MAX_PARALLEL):
        raise ValueError(f"max_parallel must be between 1 and {MAX_PARALLEL}")
    return {
        "task": task,
        "provider": provider,
        "verifier_provider": verifier,
        "max_parallel": max_parallel,
    }


def assert_worker_compatible(root: pathlib.Path, *, provider: str,
                             verifier_provider: str, max_parallel: int) -> dict[str, Any]:
    """Refuse a provider change while one shared local queue worker is active."""
    current = worker_state(root)
    active = current.get("status") == "starting" or (
        current.get("status") == "running" and current.get("alive")
    )
    if not active:
        return current
    requested = (provider, verifier_provider, max_parallel)
    actual = (
        current.get("provider"),
        current.get("verifier_provider"),
        current.get("max_parallel"),
    )
    if requested != actual:
        raise ValueError(
            "a durable worker is already active with "
            f"{actual[0]}→{actual[1]} parallel={actual[2]}; "
            "new queue items must use the same worker configuration until it drains"
        )
    return current


def assert_retryable(root: pathlib.Path, queue_id: str) -> dict[str, Any]:
    """Do not requeue work that a live provider process still owns."""
    item = queue_item(root, queue_id)
    status = item.get("status")
    if status not in {"failed", "running"}:
        raise ValueError(f"queue #{queue_id} is {status!r}, not retryable from Mission Control")
    worker = worker_state(root)
    if status == "running" and worker.get("status") == "running" and worker.get("alive"):
        raise ValueError(f"queue #{queue_id} is still owned by the live worker; wait or inspect its log")
    return item


def ensure_worker(root: pathlib.Path, *, provider: str, verifier_provider: str,
                  max_parallel: int) -> dict[str, Any]:
    """Launch one detached queue-drainer, or reuse the compatible live one."""
    current = assert_worker_compatible(
        root,
        provider=provider,
        verifier_provider=verifier_provider,
        max_parallel=max_parallel,
    )
    if current.get("status") in {"starting", "running"} and (
        current.get("status") == "starting" or current.get("alive")
    ):
        return {"started": False, "worker": current}

    if provider not in ALLOWED_PROVIDERS or verifier_provider not in ALLOWED_PROVIDERS:
        raise ValueError("provider is not allowed for Mission Control durable runs")
    if not (1 <= max_parallel <= MAX_PARALLEL):
        raise ValueError("invalid max_parallel")

    generation = uuid.uuid4().hex
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    state = {
        "schema": WORKER_SCHEMA,
        "generation": generation,
        "status": "starting",
        "started_at": now,
        "provider": provider,
        "verifier_provider": verifier_provider,
        "max_parallel": max_parallel,
        "pid": None,
        "last_exit_code": None,
        "log": str(worker_log_path(root)),
    }
    _atomic_json(worker_state_path(root), state)
    log_path = worker_log_path(root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    command = [
        sys.executable, "-m", "rig_workbench.mission_worker",
        "--repo", str(root),
        "--provider", provider,
        "--verifier-provider", verifier_provider,
        "--max-parallel", str(max_parallel),
        "--generation", generation,
    ]
    kwargs: dict[str, Any] = {
        "cwd": root,
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":  # pragma: no cover - CI is POSIX; WSL uses the POSIX branch
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception:
        state["status"] = "failed_to_start"
        state["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        _atomic_json(worker_state_path(root), state)
        raise
    finally:
        log.close()
    return {"started": True, "pid": proc.pid, "generation": generation}


def update_worker_state(root: pathlib.Path, generation: str, **updates: Any) -> bool:
    """Update only the worker generation that launched this process."""
    path = worker_state_path(root)
    state = _load_json(path)
    if not state or state.get("generation") != generation:
        return False
    state.update(updates)
    _atomic_json(path, state)
    return True


def wait_for_worker_registration(root: pathlib.Path, generation: str, timeout: float = 2.0) -> None:
    """Small hand-off barrier so a stale launcher cannot overwrite a newer worker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _load_json(worker_state_path(root))
        if state and state.get("generation") == generation:
            return
        time.sleep(0.05)
