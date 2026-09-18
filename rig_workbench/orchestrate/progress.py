"""Optional, best-effort progress observations, isolated from gate decisions.

Events contain identifiers and outcomes only, never commands, prompts or child
output. Heartbeats mean that the caller is awaiting a result, not that work is
advancing. The observer never reads or writes run-state and may be omitted.
"""
from contextlib import contextmanager
import math
import threading

from ..ports.local import CONSOLE, SYSTEM_CLOCK

_EVENTS = {"run_started", "operation_started", "operation_finished", "transition", "heartbeat", "run_finished"}
_STRINGS = {"run_id", "step_id", "phase", "check_id", "outcome", "provider", "role",
            "state_path", "worktree_path", "result_path", "next_action", "next_command"}
_INTEGERS = {"attempt", "completed_steps", "total_steps", "active_count"}


def _safe_event(event, metadata):
    if type(event) is not str or event not in _EVENTS:
        return None
    safe = {"event": event}
    for key, value in metadata.items():
        if key in _STRINGS and type(value) is str:
            # Single-line identifiers cannot inject terminal escape/control codes.
            limit = 4096 if key == "next_command" else 1024 if key.endswith("_path") else 160
            safe[key] = "".join(c if c.isprintable() else "_" for c in value)[:limit]
        elif key in _INTEGERS and type(value) is int and value >= 0:
            safe[key] = value
        elif key == "elapsed_seconds" and type(value) in (int, float) and math.isfinite(value) and value >= 0:
            safe[key] = value
    return safe


def notify(observer, event, **metadata):
    """Deliver a fresh allowlisted event; observation failure cannot alter gates."""
    if observer is None:
        return
    try:
        safe = _safe_event(event, metadata)
        if safe is not None:
            observer(safe)
    except Exception:
        # This channel is diagnostic. A broken presenter is not a failed check.
        pass


class ProgressReporter:
    """Thread-safe stderr renderer with scoped, bounded-shutdown heartbeats.

    A Presenter should promptly return from err(). The timer is a daemon and join
    is bounded so a broken output sink cannot hold execution open indefinitely.
    It never inspects mutable workflow state; only copies of received events.
    """
    def __init__(self, out=CONSOLE, clock=SYSTEM_CLOCK, interval_seconds=15):
        if (type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds)
                or interval_seconds <= 0):
            raise ValueError("heartbeat interval must be a positive finite number")
        self.out, self.clock, self.interval_seconds = out, clock, interval_seconds
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._active = {}
        self._preparing = None
        self._finished = False

    @staticmethod
    def _key(event):
        return tuple(event.get(key) for key in ("run_id", "step_id", "phase", "attempt", "check_id", "provider", "role"))

    def _write(self, event):
        fields = ["[progress]", event["event"]]
        for key in ("run_id", "step_id", "phase", "attempt", "check_id", "provider", "role", "outcome"):
            if key in event:
                fields.append(f"{key}={event[key]}")
        if event["event"] == "heartbeat":
            fields += ["result-wait", f"elapsed={event['elapsed_seconds']:.0f}s", f"active={event['active_count']}"]
            if event["active_count"] > 1:
                fields.append("context=latest-observed-operation")
        for key in ("completed_steps", "total_steps", "state_path", "worktree_path", "result_path", "next_action", "next_command"):
            if key in event:
                fields.append(f"{key}={event[key]}")
        # Serialize presentation, not workflow state. A final summary cannot be
        # followed by an already prepared stale heartbeat. Drop diagnostics if
        # another write is stuck instead of waiting indefinitely on its lock.
        if not self._write_lock.acquire(timeout=0.1):
            return
        try:
            if event["event"] == "heartbeat":
                with self._lock:
                    if self._finished or self._stop.is_set():
                        return
            try:
                self.out.err(" ".join(fields))
            except Exception:
                pass
        finally:
            self._write_lock.release()

    def __call__(self, event):
        if type(event) is not dict:
            return
        safe = _safe_event(event.get("event"), event)
        if safe is None:
            return
        with self._lock:
            kind = safe["event"]
            now = self.clock.now()
            if kind == "run_started":
                self._finished = False
                self._preparing = (dict(safe), now)
            elif kind == "operation_started":
                self._preparing = None
                self._active.setdefault(self._key(safe), []).append((dict(safe), now))
            elif kind == "operation_finished":
                key = self._key(safe)
                active = self._active.get(key)
                if active:
                    active.pop(0)
                    if not active:
                        self._active.pop(key)
            elif kind == "run_finished":
                self._finished = True
                self._active.clear()
                self._preparing = None
        self._write(safe)

    def _heartbeat(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                with self._lock:
                    active = [entry for entries in self._active.values() for entry in entries]
                    latest = max(active, key=lambda item: item[1]) if active else self._preparing
                    if latest is None or self._finished:
                        continue
                    context, started = latest
                    event = {**context, "event": "heartbeat", "active_count": len(active),
                             "elapsed_seconds": max(0, (self.clock.now() - started).total_seconds())}
                if not self._stop.is_set():
                    self._write(event)
            except Exception:
                # Timer/reporting problems are never allowed to affect execution.
                continue

    @contextmanager
    def running(self):
        """Start one timer; always stop it on normal exit or any exception."""
        if self._thread is not None and self._thread.is_alive():
            raise ValueError("progress reporter is already running")
        self._stop.clear()
        self._thread = threading.Thread(target=self._heartbeat, name="rig-progress", daemon=True)
        self._thread.start()
        try:
            yield self
        finally:
            self._stop.set()
            self._thread.join(timeout=1.0)
