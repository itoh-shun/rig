from __future__ import annotations

import dataclasses
import pathlib


class PackError(ValueError):
    """A pack is malformed, unsafe, incompatible, or ambiguous."""


ASSET_DIRS = {
    "recipe": "recipes",
    "persona": "facets/personas",
    "instruction": "facets/instructions",
    "pattern": "patterns",
    "wiki": "facets/knowledge",
    "policy": "facets/policies",
    "output-contract": "facets/output-contracts",
    "command": "commands",
    "agent": "agents",
    "eval-case": "evals/cases",
    "eval-result": "evals/results",
    "resource": "resources",
}
PROMPT_KINDS = frozenset(set(ASSET_DIRS) - {"eval-case", "eval-result", "resource"})
TIERS = ("project", "user", "org", "official", "core")


@dataclasses.dataclass(frozen=True)
class ResolvedAsset:
    kind: str
    name: str
    path: pathlib.Path
    tier: str
    source: str
    pack_id: str | None
    shadowed: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "name": self.name, "path": str(self.path),
            "tier": self.tier, "source": self.source, "pack_id": self.pack_id,
            "shadowed": list(self.shadowed),
        }


@dataclasses.dataclass(frozen=True)
class ResolvedPack:
    """A validated member of the active tiered pack collection.

    ``path`` is intentionally an internal filesystem handle.  User-facing
    projections (notably the brick graph) must expose a stable ``pack://`` URI
    instead of serialising it.
    """

    tier: str
    path: pathlib.Path
    manifest: dict
    verification_status: str

    @property
    def id(self) -> str:
        return self.manifest["id"]
