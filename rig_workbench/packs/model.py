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

#: What a pack *is*, which decides what it may carry and run. Distinct from `kind`
#: (core/official/domain/project), which decides only where it resolves in the tier order —
#: a tier is not a permission, and folding the two together would make every installed lock
#: unreadable the moment a new value appeared.
PACK_TYPES = ("knowledge", "policy", "reviewer", "skill", "workflow", "tool")

#: Asset kinds any pack may carry whatever its type: inert data that is read, never run.
_INERT_KINDS = frozenset({"wiki", "resource", "eval-case", "eval-result"})
#: Prompt material — text a provider is shown. Carrying it is not executing anything.
_PROMPT_KINDS = _INERT_KINDS | {
    "policy", "persona", "output-contract", "instruction", "recipe", "pattern",
    "command", "agent",
}

#: type → the asset kinds it may declare. A pack that declares a kind outside its type's set
#: is refused; `validate_pack` separately refuses any file the manifest does not declare, so
#: this set is the pack's whole contents and not just what it admits to.
#:
#: `skill` and `workflow` carry the same kinds on purpose — the difference between them is
#: declared intent, not permission, and pretending otherwise would sell a restriction that
#: does not exist. What actually separates `tool` from both is RECIPE_CHECKS_TYPES below.
TYPE_ASSETS = {
    "knowledge": _INERT_KINDS,
    "policy": _INERT_KINDS | {"policy"},
    "reviewer": _INERT_KINDS | {"persona", "output-contract"},
    "skill": _PROMPT_KINDS,
    "workflow": _PROMPT_KINDS,
    "tool": frozenset(ASSET_DIRS),
}

#: The types whose recipes may declare `checks:` — shell commands the orchestrator runs on
#: the host. This is the line the issue draws: adding somebody's domain knowledge must not
#: hand them arbitrary command execution. Every other type ships prompt text, which a
#: provider reads; only `tool` ships something the machine runs.
RECIPE_CHECKS_TYPES = frozenset({"tool"})


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
