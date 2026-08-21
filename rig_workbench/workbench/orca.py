"""Whether rig is running inside an Orca session — and nothing beyond that (#472).

Orca manages workspaces of its own, and #462 will teach rig to hold a task in one. Before
any of that, rig has to be able to answer one question honestly: *am I inside Orca right
now*. This module answers that one, and refuses to answer the next one.

**Being inside Orca does not mean Orca's CLI works.** Measured on one host, in one shell, at
one moment, all three of these were simultaneously true:

* `ORCA_WORKTREE_ID` was set, and so were eighteen other `ORCA_*` variables — an Orca
  session, confirmed again by the process tree: the shell running rig was a child of Orca's
  relay.
* `orca` was on `PATH`.
* Every `orca` subcommand failed immediately after its handshake, because the far side was
  not servicing requests.

A detector that returned "available" from either of the first two would hand `--runtime
orca` a backend that dies in `create()`. That is the mirror of the failure `runtime.select`
exists to prevent: it refuses to *downgrade* silently, and this refuses to *upgrade* into
something that cannot do the job.

So the two axes are reported apart, and the second one is reported as unmeasured rather
than guessed — the same rule `hostcheck` states for itself, that a check which cannot verify
its axis reports MISS and not OK. Nothing here starts a process; asking `orca` whether it
works is #462's job, and it is a question with a cost and a failure mode of its own.
"""

from __future__ import annotations

import dataclasses
import os

from .injection import INVISIBLE_RE

#: The variable Orca exports for the worktree a session is bound to. Its value carries an
#: id and a path joined by `::`. The id was a UUID on the one host this was measured on, and
#: nothing here requires it to be: rig has n=1 evidence about Orca's format, and refusing a
#: session whose id is shaped differently would be the same over-claim this module exists to
#: avoid, only pointing the other way. What *is* required of the id is that it be safe to
#: record — see `_split`.
WORKTREE_VAR = "ORCA_WORKTREE_ID"

#: The workspace the worktree belongs to. Same shape.
WORKSPACE_VAR = "ORCA_WORKSPACE_ID"

#: What separates the id from the path inside those values.
_SEPARATOR = "::"


@dataclasses.dataclass(frozen=True)
class OrcaSession:
    """The Orca session rig was started inside, as far as the environment says.

    `worktree_path` is `None` when the variable was present but not in the shape this code
    knows how to read. That is a real state and not an error: rig is inside *something* that
    exported the variable, and saying "I could not read the path" is different from saying
    "there is no session" and different again from inventing a plausible path.
    """

    workspace_id: str | None
    worktree_id: str | None
    worktree_path: str | None

    def as_ref(self) -> dict:
        """The shape `WorktreeHandle.ref` takes, for a backend that ends up owning one."""
        return {"workspace_id": self.workspace_id, "worktree_id": self.worktree_id,
                "worktree_path": self.worktree_path}


def _split(value: str | None) -> tuple[str | None, str | None]:
    """`<id>::<absolute path>` → (id, path). Anything else → no id and no path.

    A value that does not parse yields **no identifier either**, not merely no path. rig has
    one host's worth of evidence about this format and has never observed Orca export a bare
    id, so handing the unparsed blob back as `worktree_id` would be inventing an identifier
    out of a string that failed to be one — and `as_ref()` exists to be recorded, so the
    invention would end up in a record of where a task's work happened. That the variable
    was set is still reported; what it contained is not claimed.

    The id itself is treated as opaque. A reviewer asked for it to be validated as a UUID,
    which is what it was on the host this was measured on — declined, because enforcing a
    format from a single observation would report "no session" for a real one the day Orca
    spells its ids differently, and a detector whose whole thesis is not claiming what it
    has not measured cannot make that claim about someone else's identifiers. What is
    enforced is the property rig needs and can justify: an id that reaches a record must not
    be able to lie about its own length or direction.

    Split once from the left, so a path containing the separator stays whole.

    There is no separate check for a missing separator, and there was one until a mutation
    proved it could never be the condition that decided anything: without a separator
    `partition` leaves the path empty, and an empty path is not absolute. A guard whose
    input set is empty reads as caution and is only noise.
    """
    if not value:
        return None, None
    identifier, _, path = value.partition(_SEPARATOR)
    if not identifier or not path.startswith("/"):
        return None, None
    # The same rule `--caller` is held to (#429), and for the same reason: an identifier
    # that reaches a record must not be able to lie about its own length or direction.
    # Reused rather than restated — a second definition of "deceptive" is one that drifts.
    if INVISIBLE_RE.search(identifier) or any(c in identifier for c in "\n\r"):
        return None, None
    return identifier, path


def detect(env: dict | None = None) -> OrcaSession | None:
    """The Orca session in this environment, or `None` if there is not one.

    Reads variables and returns. It starts no subprocess — deliberately, and not only for
    speed: `runtime.select` promises that choosing the default asks no other tool whether
    it is installed, and a detector that shelled out would put that promise in the hands of
    whatever it shelled out to.
    """
    source = os.environ if env is None else env
    worktree_raw = source.get(WORKTREE_VAR)
    workspace_raw = source.get(WORKSPACE_VAR)
    if not worktree_raw and not workspace_raw:
        return None
    worktree_id, worktree_path = _split(worktree_raw)
    workspace_id, _ = _split(workspace_raw)
    return OrcaSession(workspace_id=workspace_id, worktree_id=worktree_id,
                       worktree_path=worktree_path)


def report(env: dict | None = None) -> dict:
    """Both axes, with the unmeasured one saying so.

    `session` is measured here. `cli` is not, and the field says why rather than defaulting
    to a value — a reader who finds `observed: false` knows nobody looked, where a `false`
    on its own would read as "looked, and it does not work".
    """
    session = detect(env)
    return {
        "session": {
            "observed": True,
            "present": session is not None,
            "workspace_id": session.workspace_id if session else None,
            "worktree_id": session.worktree_id if session else None,
            "worktree_path": session.worktree_path if session else None,
        },
        "cli": {
            "observed": False,
            "reason": (
                "detection starts no process, so whether the Orca CLI answers is unknown "
                "here. Being inside an Orca session does not imply it does: measured on a "
                "host where the session variables were present and every subcommand failed "
                "after its handshake. #462 owns that question."
            ),
        },
    }
