"""The one table of what rig can do (`docs/v3-architecture-design-brief.ja.md` §2, §9).

Every capability is declared here exactly once, and every surface that offers capabilities —
the `rig-wb` CLI, `scripts/mcp_server.py`, `rig_workbench/remote_mcp.py`, `action.yml`, and
the routing `skills/engine/facets/instructions/talk-loop.md` performs — becomes a projection
of this tuple instead of a hand-written copy of it.

Stage 2 is declaration only. All 137 entries are here, and the surfaces still execute
through the code they execute through today; rewiring them onto this table is stage 3. So
nothing in this package imports the code that runs a capability, and nothing here can run
one: see `model.Capability` on why a declaration may not carry a callable.
"""

from __future__ import annotations

from .entries_cli import CLI_CAPABILITIES
from .entries_subgroups import SUBGROUP_CAPABILITIES
from .entries_wb import WB_CAPABILITIES
from .model import (
    EFFECT_CLASSES,
    FLAG_TYPES,
    NO_PRECONDITION,
    PARENTS,
    Capability,
    ExitCode,
    Flag,
)

__all__ = [
    "CAPABILITIES",
    "EFFECT_CLASSES",
    "FLAG_TYPES",
    "NO_PRECONDITION",
    "PARENTS",
    "Capability",
    "ExitCode",
    "Flag",
    "by_id",
    "children",
]

#: Every capability rig has, in the order a listing shows them. Filled in by concatenating
#: one entries module per surface, one line each, so that two of them can land side by side
#: without either rewriting the other's — an entry appearing here does not yet change any
#: surface's behaviour.
CAPABILITIES: tuple[Capability, ...] = (
    *WB_CAPABILITIES,
    *CLI_CAPABILITIES,
    *SUBGROUP_CAPABILITIES,
)


def by_id(
    capability_id: str, capabilities: tuple[Capability, ...] | None = None
) -> Capability | None:
    """The capability with this id, or `None` when nothing declares it.

    `None` rather than a raised `KeyError` because every caller here is answering the same
    question — a subcommand nobody registered, an MCP tool name off the wire, a verb a
    conversation guessed at — and each wants to say something different about it.

    `capabilities` overrides the table, for tests and for a caller holding a filtered view.
    """
    table = CAPABILITIES if capabilities is None else capabilities
    for capability in table:
        if capability.id == capability_id:
            return capability
    return None


def children(
    parent: str | None, capabilities: tuple[Capability, ...] | None = None
) -> tuple[Capability, ...]:
    """Everything hanging under `parent`, in declaration order.

    `children(None)` is the top level — the verbs typed straight after `rig-wb` — which is
    why the parameter is not optional: passing nothing and meaning "all of them" would make
    the top level unaskable.
    """
    if parent is not None and parent not in PARENTS:
        raise ValueError(f"parent {parent!r} is not one of {', '.join(PARENTS)} (or None)")
    table = CAPABILITIES if capabilities is None else capabilities
    return tuple(capability for capability in table if capability.parent == parent)
