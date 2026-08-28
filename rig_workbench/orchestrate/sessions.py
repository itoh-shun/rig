"""Reusing a CLI provider's conversation across steps (#326).

Every step currently starts a CLI provider from nothing, so a run pays the process's startup
cost once per step and re-injects the prior context into each prompt. Where the CLI can carry
a conversation forward, it does not have to.

Four rules, and each of them is the reason this module is shaped the way it is.

**Opt-in, never the default.** Statelessness is not only a cost; it is part of the design.
Each step starting clean is what keeps steps independent and what keeps "the grader is not the
generator" true in practice rather than by assertion. So the default does not move, and the
saving is something a caller asks for.

**Never the verifier.** A checker that inherits the generator's conversation is not an
independent checker, whatever its prompt says — it has already read the generator's reasoning
and will agree with it more often. Reuse is generator-only, and this module has no path that
can produce a verifier session even if a caller asks for one.

**The CLI decides whether it can, not a table in here.** Session support is strongly
version-dependent, so the capability is read out of the tool's own `--help` at runtime. A
hardcoded list would be a claim about versions this code has never seen, and would keep being
wrong quietly as those tools change under it.

**A fallback is recorded, never silent.** Dropping to stateless is fine; dropping to stateless
without saying so means somebody later measures no improvement and cannot tell whether the
feature failed or was never active.
"""

from __future__ import annotations

import subprocess
import threading
import uuid

#: provider -> the flags it would use, if its own `--help` confirms them. Only providers whose
#: session flags are documented somewhere this repository can point at: `claude` (verified
#: against the shipped CLI) and `grok` (docs.x.ai/build/cli/headless-scripting, recorded on
#: #326). `codex` is deliberately absent — its session flags are not documented here, and
#: guessing them would produce an argv that either fails loudly or, worse, means something
#: else. An absent provider falls back with a reason, which is the honest outcome.
SESSION_FLAGS = {
    "claude": {"start": "--session-id", "resume": "--resume"},
    "grok": {"start": "--session-id", "resume": "--resume"},
}

#: How long to wait for a `--help` that should return immediately. A CLI that hangs on its own
#: help is a CLI this run should not be threading a session through.
PROBE_TIMEOUT = 10.0

_LOCK = threading.Lock()


def supports(provider: str, cfg: dict | None = None) -> tuple[bool, str]:
    """Does this provider's CLI advertise the flags reuse needs? (supported, reason).

    Asks the tool. `--help` is read once per process and cached, because a run makes many
    provider calls and the answer cannot change under it.

    The reason is filled in on the negative side only, and it is what reaches the run log: a
    fallback whose cause is not written down is indistinguishable from a feature that was
    never switched on.
    """
    flags = SESSION_FLAGS.get(provider)
    if flags is None:
        return False, f"no documented session flags for provider {provider!r}"
    cache = _cache(cfg)
    with _LOCK:
        if provider in cache:
            return cache[provider]
    try:
        completed = subprocess.run([provider, "--help"], capture_output=True, text=True,
                                   timeout=PROBE_TIMEOUT)
    except FileNotFoundError:
        answer = (False, f"{provider} not found on PATH")
    except (subprocess.SubprocessError, OSError) as error:
        answer = (False, f"{provider} --help failed: {type(error).__name__}")
    else:
        help_text = (completed.stdout or "") + (completed.stderr or "")
        missing = [flag for flag in (flags["start"], flags["resume"]) if flag not in help_text]
        answer = ((False, f"{provider} --help does not advertise {', '.join(missing)}")
                  if missing else (True, ""))
    with _LOCK:
        cache[provider] = answer
    return answer


def _cache(cfg: dict | None) -> dict:
    if not isinstance(cfg, dict):
        return {}
    return cfg.setdefault("_session_support", {})


def session_argv(provider: str, role: str, cfg: dict, state: dict | None = None) -> list[str]:
    """The flags that continue this run's generator conversation, or `[]` for stateless.

    Returning `[]` is always safe: it is exactly the argv the caller had before this module
    existed, so every refusal below degrades to the behaviour that already worked.

    The first generator call of a run names a fresh session; later ones resume it. The id lives
    on the run, so two runs never share a conversation and a resumed session cannot outlive the
    work it belongs to.
    """
    if role != "generator" or not cfg.get("reuse_session"):
        return []
    ok, reason = supports(provider, cfg)
    if not ok:
        _record_fallback(state, provider, reason)
        return []
    flags = SESSION_FLAGS[provider]
    sessions = cfg.setdefault("_sessions", {})
    with _LOCK:
        session_id = sessions.get(provider)
        if session_id is None:
            # A v4 UUID because `claude --session-id` requires one; grok accepts any id, so one
            # shape serves both rather than each provider inventing its own.
            sessions[provider] = session_id = str(uuid.uuid4())
            return [flags["start"], session_id]
    return [flags["resume"], session_id]


def _record_fallback(state: dict | None, provider: str, reason: str) -> None:
    """Write the fallback into the run's history, once per provider.

    Once, because this is asked on every provider call and a run would otherwise carry dozens
    of identical lines; but at least once, because the whole point is that a reader can tell
    "reuse was asked for and could not happen" from "reuse was never asked for".
    """
    if state is None:
        return
    with _LOCK:
        already = {entry.get("provider") for entry in state.get("history", [])
                   if entry.get("action") == "SESSION_REUSE_FALLBACK"}
        if provider in already:
            return
        state.setdefault("history", []).append({
            "action": "SESSION_REUSE_FALLBACK", "provider": provider, "reason": reason})
