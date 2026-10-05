"""Process-local MCP credentials, removed from the inherited environment at entry."""
import os
import threading

from ...ports.local import OS_ENV

_token = None
# One lock covers pop -> holder update -> read, so a thread that pops a newer value can
# never be overtaken by one that still reads the older holder.
_LOCK = threading.Lock()


def _renew_lock_in_child():
    # fork() copies a lock that another thread may hold; that thread does not exist in the
    # child, so the copy would never be released and t3_token() would wait forever.
    global _LOCK
    _LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_renew_lock_in_child)


def _reset_for_tests():
    """Forget any held token so one test cannot read what an import or another test captured."""
    global _token
    with _LOCK:
        _token = None


def _capture_locked():
    global _token
    # The read-only Env port cannot remove credentials from a process environment.
    token = os.environ.pop("RIG_T3_MCP_TOKEN", None)  # noqa: TID251
    # An empty or blank value is still removed (never handed to a child) but does not
    # replace a token already held.
    if token is not None and token.strip():
        _token = token


def capture_t3_token():
    """Move the bearer token before any CLI work can launch children; safe to repeat."""
    with _LOCK:
        _capture_locked()


def t3_token(env=OS_ENV):
    """Injected environments stay isolated; only the real process uses the holder."""
    if env is OS_ENV:
        # A value that reached os.environ after entry is moved out before it can reach a
        # child, and the newest value wins, matching the old env-first order.
        with _LOCK:
            _capture_locked()
            return _token
    return env.get("RIG_T3_MCP_TOKEN") or None
