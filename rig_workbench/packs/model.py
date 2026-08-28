from __future__ import annotations

import dataclasses
import pathlib


class PackError(ValueError):
    """A pack is malformed, unsafe, incompatible, or ambiguous.

    `reason` is a stable machine-readable label. Every refusal used to arrive as this one
    class, which reads the same to a caller whether the host was unreachable or the person
    was not logged in — and those want opposite responses: `gh auth login` fixes one and
    does nothing for the other. The subclasses below name the cases a caller can act on
    differently; everything else keeps the base reason, so nothing has to be classified
    before it can be raised.
    """

    reason = "invalid-pack"


class SourceUnreachable(PackError):
    """The source could not be reached at all — no network, unknown host, dead remote."""

    reason = "source-unreachable"


class AuthFailed(PackError):
    """The source answered and refused the credentials (or there were none to offer)."""

    reason = "auth-failed"


class RevisionNotFound(PackError):
    """The source was read but does not carry the requested tag or commit."""

    reason = "revision-not-found"


class DigestMismatch(PackError):
    """The revision resolved but its content is not what the lock recorded.

    This is what makes `@1.4.0` mean one thing forever. It does not mean the supply chain is
    safe — a mismatch says the bytes changed, not who changed them; that is the signature's
    question, not the digest's.
    """

    reason = "digest-mismatch"


class CapabilityRefused(PackError):
    """The pack declares something its type may not carry or run."""

    reason = "capability-refused"


class EngineIncompatible(PackError):
    """The pack's engine range excludes the running engine."""

    reason = "engine-incompatible"


class UnverifiedSignature(PackError):
    """The pack carries no publisher signature that verifies against a trust root."""

    reason = "unverified-signature"


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
