"""Process-local MCP credentials, removed from the inherited environment at entry."""
import os

from ...ports.local import OS_ENV

_token = None


def capture_t3_token():
    """Move the bearer token before any CLI work can launch children; safe to repeat."""
    global _token
    # The read-only Env port cannot remove credentials from a process environment.
    token = os.environ.pop("RIG_T3_MCP_TOKEN", None)  # noqa: TID251
    if token is not None:
        _token = token or None


def t3_token(env=OS_ENV):
    """Injected environments stay isolated; only the real process uses the holder."""
    if env is OS_ENV:
        # A value that reached os.environ after entry is moved out before it can reach a
        # child, and the newest value wins, matching the old env-first order.
        capture_t3_token()
        return _token
    return env.get("RIG_T3_MCP_TOKEN") or None
