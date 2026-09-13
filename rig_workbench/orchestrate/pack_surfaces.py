"""The pack machinery this pillar borrows, in the one module allowed to know where it lives.

Twenty of this pillar's cross-pillar edges point at `packs`, and they all ask one of four
questions: does this asset name resolve, is the file it resolves to trusted, which packs
are installed, and what does a pack call this kind of asset. The orchestrator cannot answer
any of them itself — resolution order across the core, org, project and pack tiers is the
`packs` pillar's rule, and the trust store is its record. Answering them a second time here
would give the runner a private copy of the resolver that drifts from the one `rig-wb pack`
enforces, which is the failure a trust gate may not have.

They are not moved here; they are inverted. `recipes.py`, `providers.py`, `runstate.py` and
`graph.py` each state the narrow thing they need — `PackAssets`, `PackComposition`,
`PackProvenance`, `PackInventory` — and this module satisfies those shapes without any of
them naming another pillar. It is declared a shell in `tests/test_layering_contract.py` for
the reason `PORT_ADAPTERS` gives for `ports/local.py`: an adapter exists precisely to hold
what the protocol may not.

**Every call goes through the module, not through a bound name.** `resolve_asset`,
`resolve_bound_asset` and `ensure_asset_trusted` are looked up on `packs.resolver` and
`packs.trust` at call time rather than imported as names, because that is what the existing
tests substitute: `tests/test_headless_compose.py` retargets composition by assigning to
`resolver.resolve_asset` and `resolver.resolve_bound_asset`, and a `from ... import` here
would freeze the shipped function at import time and make those substitutions silently stop
biting. The same reason `queueing.QUEUE_PATH` is frozen and documented as frozen: which
lookup is live is a contract, not an implementation detail.

**`PackError` is republished rather than inverted, and it is the one thing here that
cannot be a protocol.** An `except` clause compares class identity, so a pillar that
declared its own error class would not catch the one `packs.resolver` raises, and
`packs/cli.py` — which wraps `pack invoke`, and so wraps this pillar's runner — would stop
catching the one this pillar raises. Two classes would be two rules. `packs/case_schema.py`
republishes `EvalCaseError` as `CaseError` for exactly this reason, and the name is exported
from here so that `providers.py` and `commands.py` raise and catch it without either of
them naming another pillar.

**What stays inside a function, and why that is not hiding it.** `packs.trust` reaches
`packs.manifest` and from there `packs.scanners`, which pulls `eval.safety` and three
`workbench` modules in behind it; `packs.catalog` reaches `packs.validation` and the same
chain. `providers._caller_record` already carries the rule those two would break — "the
orchestrator should not need the workbench package to start" — so the two heavy imports are
made where they are used, in the module whose declared job is to hold them.
`rig_surfaces._ParserSource.build` defers for the same reason. `packs.model` and
`packs.resolver` reach nothing but the ports, so they are named at module level, where the
edge is visible rather than deferred.

The arrow stays one-way: this module imports no `orchestrate` judgement module.
"""

from __future__ import annotations

import pathlib
from typing import Any

from .. import __version__ as _package_version
from ..packs import resolver as _resolver
from ..packs.model import ASSET_DIRS as _ASSET_DIRS
from ..packs.model import PackError as _PackError

#: The error the pack machinery raises, re-published so a caller can raise and catch it
#: without naming another pillar's class. Same class, deliberately: see the module docstring.
PackError: type[Exception] = _PackError

#: Where a pack keeps each kind of asset, `graph.py`'s only use of the pack vocabulary.
ASSET_DIRS: dict[str, str] = _ASSET_DIRS

#: The version of the built-in `rig-core` pack, which is the release the core assets ship
#: in. `graph.py` records it on the core pack node and consults it for nothing, which is why
#: this is a value handed over rather than an import held — the reason `eval/cases.py` gives
#: for keeping `EXECUTOR_VERSION` of its own.
CORE_PACK_VERSION: str = _package_version


class _PackSurfaces:
    """The pack pillar, under the names this pillar's four protocols ask for."""

    PackError: type[Exception] = _PackError
    ASSET_DIRS: dict[str, str] = _ASSET_DIRS
    CORE_PACK_VERSION: str = _package_version

    @staticmethod
    def resolve(kind: str, name: str, *, project: Any = None, shared: Any = None) -> Any:
        return _resolver.resolve_asset(kind, name, project=project, shared=shared)

    @staticmethod
    def resolve_bound(kind: str, name: str, source: Any, *,
                      project: Any = None, shared: Any = None) -> Any:
        return _resolver.resolve_bound_asset(kind, name, source, project=project,
                                             shared=shared)

    @staticmethod
    def installed(*, project: Any = None, shared: Any = None) -> list[Any]:
        return _resolver.resolved_collection(project=project, shared=shared)

    @staticmethod
    def trusted_path(asset: Any) -> pathlib.Path:
        from ..packs import trust

        return trust.ensure_asset_trusted(asset)

    @staticmethod
    def consent_flag_passed(flag: str, argv: list[str]) -> bool:
        from ..packs import trust

        return trust.passed_as_option(flag, argv)

    @staticmethod
    def builtin() -> dict:
        """The bundled packs, keyed `(namespace, pack_id)`, with the core ids applied.

        `discover_builtin_packs(core_ids=core_reference_ids())` is the only form either
        caller used, so the pairing is bound here instead of being repeated at two call
        sites where getting it wrong would show up as a pack that silently has no owner.
        """
        from ..packs.catalog import discover_builtin_packs

        return discover_builtin_packs(core_ids=_resolver.core_reference_ids())


#: The shipped implementation, named as a value so a caller passes one rather than a module
#: whose address it would have to know — as `ports.local`'s instances are.
PACK_SURFACES = _PackSurfaces()
