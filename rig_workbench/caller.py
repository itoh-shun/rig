"""Who invoked rig — as much as can be known, and no more (#416 Phase 2).

Rig is increasingly started by another harness rather than by a person, and one
consequence is already handled: launching headless Claude from inside a Claude Code
session re-enters the same harness, wasting a session to answer a question the outer
one is already holding. `bench_providers` blocked that by reading `CLAUDECODE` inline.
The check was right; what it lacked was a name, so the knowledge of what those
variables mean lived in an `if` and could not be reused, tested, or contradicted.

Three things this is careful about.

**A declaration is not a guess.** `--caller` / `RIG_CALLER` is what the operator
said; the environment is what rig inferred. The result carries `declared` and
`source` so a consumer can weigh them differently — one that cannot tell them apart
will trust the guess exactly as much as the declaration, which is how a heuristic
quietly becomes a fact.

**Depth is a question this declines to answer.** Rig can say *which* harness invoked
it. It cannot say *at what depth*: Claude Code hands a subagent's shell the same
variables it hands the parent's — `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`,
`CLAUDE_CODE_CHILD_SESSION=1` unconditionally — verified against 2.1.224 and 2.1.227
in `context_meter`, which refuses to publish a dispatch rate on exactly this evidence.
A confident depth here would be a fabrication, so there is no field for one.

**This is a hint.** #416 draws the line itself: the caller may inform runtime and
reviewer selection; it must never branch rig's quality rules. A gate that is lenient
when a particular harness calls it is not a gate, and it would be lenient precisely
where nobody is watching. The test suite enforces that structurally rather than
trusting this paragraph.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The repository already has one definition of "characters that make printed text lie
# about itself", and `scan-injection` treats it as fail-grade. Reusing it keeps a
# second, quietly diverging list from existing.
from .workbench.injection import INVISIBLE_RE

#: What rig reports when nothing identifies the caller. Not a failure — a plain
#: terminal is the common case, and guessing a harness there would be worse.
UNKNOWN = "unknown"

#: Environment variables that identify a harness, in the order they are consulted.
#: Each entry is (variable, caller id). Presence is the signal; the value is not
#: parsed, because these are set as markers rather than as data.
#:
#: Only Claude Code is detected, and only because its variables are documented from
#: measurement — `context_meter` records the exact set a Bash tool call receives,
#: verified against 2.1.224 and 2.1.227. No marker is listed for any other harness
#: here: rig has not verified one, and a guessed marker would either fire on the
#: wrong session or, worse, silently never fire while looking like coverage. Those
#: callers say so with `--caller` / `RIG_CALLER`, which is a statement rather than a
#: guess and is treated as one.
_ENV_MARKERS = (
    ("CLAUDECODE", "claude-code"),
    ("CLAUDE_CODE_SESSION_ID", "claude-code"),
)

#: Which provider re-enters which harness. `rig` runs through the Claude CLI (see
#: `orchestrate.runtime`), so it re-enters Claude Code just as `claude` does.
_HARNESS_PROVIDERS = {
    "claude-code": {"claude", "rig"},
    "codex": {"codex"},
}


@dataclass(frozen=True)
class Caller:
    """The caller, and how confident rig is entitled to be about it."""

    id: str
    #: Where the answer came from: `flag`, `env:<NAME>`, or `none`.
    source: str
    #: True when a human or a calling harness said so, rather than rig inferring it.
    declared: bool


def detect(declared: str | None = None) -> Caller:
    """Identify the calling harness.

    `declared` is the `--caller` value when one was passed. It wins over the
    environment, and over `RIG_CALLER`, because it is the most explicit statement
    available.
    """
    if declared is not None:
        if not isinstance(declared, str):
            raise TypeError(f"--caller must be a string, got {type(declared).__name__}")
        return Caller(id=_normalise(declared), source="flag", declared=True)

    from_env = os.environ.get("RIG_CALLER")
    if from_env is not None and from_env.strip():
        return Caller(id=_normalise(from_env), source="env:RIG_CALLER", declared=True)

    for variable, harness in _ENV_MARKERS:
        if os.environ.get(variable):
            return Caller(id=harness, source=f"env:{variable}", declared=False)

    return Caller(id=UNKNOWN, source="none", declared=False)


#: The longest caller name rig will carry. Harness names are short by nature; the
#: bound exists because this value is echoed into stderr when the re-entry guard
#: declines, and an unbounded one turns that message into a paste target.
_MAX_NAME = 64


def _normalise(name: str) -> str:
    """Lower-case and trim, but never snap an unfamiliar name onto a known one — rig
    does not know every harness that will ever call it, and silently rewriting a
    caller's own name for it is how a hint starts lying.

    Two things are refused rather than rewritten. A name carrying zero-width or
    bidi-control characters is rejected on the same definition `workbench.injection`
    treats as fail-grade, because this name is printed back to the operator and those
    code points exist to make printed text lie about itself. Newlines go with them: a
    caller name is one token, and one that spans lines can forge a second log line.
    Rejecting is deliberate — quietly stripping them would hand back a name the
    operator never typed, which is the failure mode this whole module argues against.
    """
    cleaned = name.strip().lower()
    if not cleaned:
        raise ValueError("--caller was given an empty value; omit it instead")
    if len(cleaned) > _MAX_NAME:
        raise ValueError(
            f"--caller must be at most {_MAX_NAME} characters; got {len(cleaned)}"
        )
    if INVISIBLE_RE.search(cleaned) or any(c in cleaned for c in "\n\r"):
        raise ValueError(
            "--caller must not contain zero-width, bidi-control or newline characters"
        )
    return cleaned


def would_re_enter(caller_id: str, *, provider: str) -> bool:
    """Would running `provider` start another instance of the harness that called us?

    An unknown caller answers False. Rig has no reason to believe a plain terminal
    re-enters anything, and blocking on a guess would break the ordinary case to
    protect the unusual one.
    """
    return provider in _HARNESS_PROVIDERS.get(caller_id, frozenset())
