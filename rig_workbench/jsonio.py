"""The shape of a rig answer when something is reading it rather than someone (#416 Phase 2).

`exitcodes` says whether rig reached an answer. This says what the answer was.

Both halves have to be a contract for the machine interface to be one, and only the
first half was. Across rig's `--json` outputs today: `wb log` returns a bare list,
`coverage` returns `{items, summary}`, `eval affected` carries its own
`eval_affected_schema_version`, `mission-control` stamps `rig.mission-control/v1`,
and `wb gates` — the command SKILL.md names as the source of truth for
acceptance-criteria IDs — had no `--json` at all. Two of those say what they are.
The rest leave a consumer to learn that the shape changed by breaking on it.

The envelope is deliberately thin, because the expensive part of a machine contract
is not its richness but its stability:

    {"schema": "rig.gates/v1", "status": "ok", "data": {...}}

* **`schema` carries its own version.** `rig.gates/v1` stays attached to the payload
  wherever it is copied or re-wrapped; a sibling `version` field is the first thing
  a consumer drops. A reader that does not know `/v2` can refuse it instead of
  half-understanding it.
* **`status` mirrors the exit status.** A consumer that captured stdout should not
  need to have also captured `$?`, and the two must not be able to disagree — so
  they come from one table, `STATUS_FOR_EXIT`.
* **`data` is everything else.** The envelope makes no claim about the payload; that
  is what the schema name is for.

**Existing outputs are not rewritten.** They have consumers — this repo's own tests,
`mission_control`, the MCP adapter that reads `plan --json` — and breaking those to
tidy a contract trades a real cost for a tidy one. `LEGACY` names every `--json`
still on its own shape and the suite holds a ceiling over its size that may only be
lowered. Adoption is one command at a time, starting where nothing can break.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import exitcodes

ENVELOPE_VERSION = 1

#: The only statuses an envelope may carry, and the exit status each one means. One
#: table, so `status` in the payload and `$?` in the shell cannot drift apart.
STATUS_FOR_EXIT = {
    exitcodes.OK: "ok",
    exitcodes.REJECTED: "rejected",
    exitcodes.ERROR: "error",
}
_EXIT_FOR_STATUS = {status: code for code, status in STATUS_FOR_EXIT.items()}

#: Commands whose `--json` is the envelope.
ENVELOPED = frozenset({"wb gates"})

#: Commands whose `--json` predates the envelope and still emits its own shape.
#: These have consumers; each comes off this list when its consumers move, not
#: before. The list is inventory, not permission — the suite caps its size.
LEGACY = frozenset({
    "wb log", "wb board", "wb route", "wb status", "coverage", "asvs",
    "eval affected", "mission-control", "govern", "evidence", "orchestrate plan",
    "orchestrate graph", "packs", "baseline", "gh-requirement",
})


def envelope(schema: str, data: Any, *, status: str = "ok") -> dict:
    """Wrap `data` as `rig.<schema>/v<N>` with a status a caller can branch on."""
    if status not in _EXIT_FOR_STATUS:
        raise ValueError(
            f"unknown envelope status {status!r}; expected one of "
            f"{sorted(_EXIT_FOR_STATUS)} — a status nobody defined is a lie in a "
            "field something is going to branch on")
    return {"schema": f"rig.{schema}/v{ENVELOPE_VERSION}", "status": status, "data": data}


def exit_for_status(status: str) -> int:
    """The exit status that goes with an envelope status."""
    return _EXIT_FOR_STATUS[status]


def emit(schema: str, data: Any, *, status: str = "ok", stream=None) -> None:
    """Print one envelope. Sorted keys and a trailing newline so a diff of two runs
    is a diff of the answer rather than of dict ordering."""
    print(json.dumps(envelope(schema, data, status=status), ensure_ascii=False,
                     sort_keys=True),
          file=stream if stream is not None else sys.stdout)
