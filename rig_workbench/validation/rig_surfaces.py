"""The rig surfaces this pillar validates, in the one module allowed to know where they live.

`validation` is the repository's own CI check: it reads the shipped recipes, personas,
commands, manifests and docs and says whether they are consistent with the code that runs
them. That last clause is the whole difficulty. "Consistent with the code" cannot be
decided without asking the code — whether a gate name is one the runner actually runs,
which task types and gate presets exist, which subcommands `workbench.py` registers,
whether an asset name resolves in some tier. Answering any of those a second time here
would produce a checker that passes while the thing it checks has moved, which is the one
failure a validator may not have.

So the answers are borrowed, and the point of this module is *where the borrowing sits*.
`tests/test_layering_contract.py` lets a judgement module import the standard library, its
own pillar and the six ports. `workbench.cli`, `workbench.config`, `workbench.capabilities`,
`workbench.stale_refs`, `orchestrate.gates`, `orchestrate.mcp_scan`, `govern.stage` and
`packs.resolver` are none of those, and before this module existed those edges sat in
`catalog.py`, `drill.py`, `manifest.py`, `mcp_scan.py`, `recipes.py`, `routes.py` and
`stale_refs.py` — eleven edges, six at module level and five hidden inside function
bodies, which looks like a fix and is not.

They are not moved here; they are inverted. Each judgement module states the narrow thing
it needs as a protocol of its own — `RecipeGate`, `RuntimeGateTest`, `HumanGateParser`,
`TaskRouter`, `StaleRefScanner`, `McpScanner`, `AssetResolver`, `ParserSource` — and this
module satisfies those shapes without any of them naming another pillar. It is declared a
shell in `tests/test_layering_contract.py` for the reason `PORT_ADAPTERS` gives for
`ports/local.py`: an adapter exists precisely to hold what the protocol may not.

**One module, not three.** `packs` needed its borrowing split across `scanners.py`,
`case_schema.py` and `eval_bridge.py`, and the split was forced by a measurement rather
than chosen: taking the case schema from the wide bridge closed a new ten-module runtime
cycle that `tests/test_architecture_inventory.py` refused. That constraint does not exist
here. Nothing in `rig_workbench` imports `rig_workbench.validation.*` — the pillar is a
sink in the import graph, reached only from `scripts/validate.py` and `rig_workbench/cli.py`
by path — so no collaborator's closure can come back through this module, and splitting it
would give three files that each hold two imports and say nothing one file does not.

**The one import that stays inside a method.** `workbench.cli` pulls 101 rig_workbench modules behind it,
because building the parser means registering every subcommand the workbench has. Two
checks need it and the other twenty do not, so it is imported where it is used rather than
at module level — which is exactly why `catalog.py` had it function-local before this
module existed. `packs.resolver` is deferred for the same reason its old call site
documented: a manifest typo is not worth loading the pack machinery for, and the import may
legitimately fail.

The arrow stays one-way: this module imports no `validation` judgement module. It reaches
`.config` for nothing and `.state` for nothing; a default binding flows from here outward.
"""

from __future__ import annotations

import argparse
import pathlib
from collections.abc import Mapping, Sequence

from ..orchestrate.gates import is_runtime_gate, validate_executable_recipe
from ..workbench.capabilities import ROUTE_PRODUCERS as _ROUTE_PRODUCERS
from ..workbench.capabilities import LocalRecipe, select_task_route
from ..workbench.config import GATE_PRESETS as _GATE_PRESETS
from ..workbench.config import TASK_TYPES as _TASK_TYPES
from ..workbench.stale_refs import scan_stale_refs

# ── the vocabulary, borrowed as data rather than copied ──────────────────────
# Re-exported, never re-typed. These three are the exact objects the workbench routes and
# gates with, and the checks that read them exist to catch a shipped document drifting from
# them; a copy here would drift in lockstep with the document and report nothing. That is
# not hypothetical — `recipes.PRESET_CRITERION_IDS` carries the note that the copy in
# `facets/instructions/acceptance-check.md` had already drifted by eleven criteria.
GATE_PRESETS: Mapping[str, Sequence[str]] = _GATE_PRESETS
TASK_TYPES: Mapping[str, Sequence[str]] = _TASK_TYPES
ROUTE_PRODUCERS: Sequence[Mapping[str, object]] = _ROUTE_PRODUCERS


# ── the orchestrator's gate rules ────────────────────────────────────────────
class _RecipeGate:
    """`orchestrate.gates`' two questions, under the names the callers ask for.

    What counts as a runtime gate and what counts as an executable recipe are the
    orchestrator's rules, not this pillar's. A copy of either here would be a second answer
    consulted only by the validator — free to drift from the one that actually runs the
    step, and wrong in the direction of passing a recipe the runner would refuse.
    """

    @staticmethod
    def is_runtime_gate(gate: object) -> bool:
        return is_runtime_gate(gate)

    @staticmethod
    def executable(recipe: object) -> dict:
        return validate_executable_recipe(recipe)


# ── the stage governance parser ──────────────────────────────────────────────
def _parse_human_gate(value: object, *, where: str) -> tuple[dict | None, str | None]:
    """`govern.stage.parse_human_gate`, returning its refusal instead of raising it.

    The shape is deliberately not the one `govern` exports. `parse_human_gate` signals a
    malformed `human_gate:` by raising `StageConfigError`, and a caller that catches it
    names a class from another pillar — a type in an `except` clause is an edge exactly as
    much as a function in a call. So the error crosses this boundary as the message the
    caller was going to print anyway, and `recipes.py` declares a parser that answers
    `(rule, error)` with neither half borrowed.
    """
    from ..govern.stage import StageConfigError, parse_human_gate

    try:
        return parse_human_gate(value, where=where), None
    except StageConfigError as refused:
        return None, str(refused)


# ── the task-route selector ──────────────────────────────────────────────────
#: The recipes a shipped route may select between. This is where `LocalRecipe` is
#: constructed, which is why the selector could not be inverted with a protocol alone:
#: `routes.py` does not merely *call* the selector, it builds the selector's input type,
#: and a judgement module that constructs another pillar's dataclass has the edge whatever
#: the call looks like. The names are the shipped core recipe set and the three project
#: profiles `commands/go.md` documents.
_CORE_NAMES = (
    "bugfix", "feature", "refactor", "documentation", "debug", "release-flow",
    "design-first", "review-only",
)
_PROFILE_ADDITIONS: Mapping[str, tuple[str, str] | None] = {
    "core": None,
    "preferred-design": ("design", "design"),
    "preferred-test": ("test-design", "test-design"),
    "preferred-pr": ("pr-review", "pr-review"),
}


class _TaskRouter:
    """Which recipe and capability the real selector picks for a route declaration."""

    @staticmethod
    def route(task_type: str, context: Mapping[str, object],
              profile: str) -> Mapping[str, str] | None:
        """The selector's answer, or `None` when `profile` names no known profile."""
        if profile not in _PROFILE_ADDITIONS:
            return None
        available = {name: LocalRecipe(name, "core", None, True) for name in _CORE_NAMES}
        addition = _PROFILE_ADDITIONS[profile]
        if addition is not None:
            name, pack = addition
            available[name] = LocalRecipe(name, "project", pack, True, True)
        return select_task_route(task_type, context, available)


# ── the standalone sensors ───────────────────────────────────────────────────
def _scan_stale_refs(root: pathlib.Path, files: Sequence[pathlib.Path], *,
                     exclude_prefixes: Sequence[str]) -> list[dict]:
    return scan_stale_refs(root, files, exclude_prefixes=exclude_prefixes)


def _mcp_scan(path: pathlib.Path) -> dict:
    """The orchestrator's MCP static threat scan.

    Imported inside the call for the reason `mcp_scan.py` gave when it held this import
    itself: the check is skipped outright unless `scripts/mcp_server.py` exists, and a
    check that usually does not run should not cost an import on every validate.
    """
    from ..orchestrate.mcp_scan import mcp_scan

    return mcp_scan(path)


def _resolve_asset(kind: str, name: str, *, project: pathlib.Path) -> bool:
    """Whether `name` resolves in any tier, asked of the resolver COMPOSE itself uses.

    Imported inside the call, as the old call site in `manifest.py` documented: `validation`
    is loaded by the CI entry point and must not take a hard dependency on the pack
    machinery just to report a manifest typo. `ImportError` answers `True` — a check that
    cannot run invents no failure.

    **`PackError` answers `True` too, and it is the only exception that does.** A malformed
    installed pack takes its whole tier down fail-closed, so `resolve_asset` refuses rather
    than returning nothing: measured, `pack.yaml must be JSON-compatible canonical YAML: …`
    against a pack whose manifest does not parse. That is `check_packs_catalog`'s finding,
    and a manifest check that failed on it would name the wrong file — which is what
    `manifest._resolve` has always meant by "a broken pack collection is a different check's
    problem". `CapabilityRefused` and `EngineIncompatible` are subclasses and so travel with
    it; `PackError` subclasses `ValueError` rather than the reverse, so this catches neither
    a stray `ValueError` nor anything wider.

    The catch is here rather than in `manifest.py` because a type in an `except` clause is
    an edge exactly as much as a function in a call — the reason `_parse_human_gate` above
    keeps `StageConfigError` on this side of the boundary. `manifest._resolve` is left with
    no `except` at all, so every *other* failure surfaces as the `check_manifest` FAIL
    `cli.py` already wraps each check in instead of being answered "resolves fine".
    """
    try:
        from ..packs.model import PackError
        from ..packs.resolver import resolve_asset
    except ImportError:  # pragma: no cover - packs ships with the workbench
        return True
    try:
        return resolve_asset(kind, name, project=project) is not None
    except PackError:
        return True


# ── the workbench's own argument parser ──────────────────────────────────────
class _ParserSource:
    """The built `workbench.py` parser, which is the list of subcommands rig really has.

    Two checks compare a shipped document against it — `commands/go.md`'s route table and
    `SKILL.md` §2's brick catalogue — and both exist because a surface went missing from a
    document three times and a person noticed rather than this repository did.
    """

    @staticmethod
    def build() -> argparse.ArgumentParser:
        # Method-local on purpose: building the parser registers every workbench
        # subcommand, which drags 101 rig_workbench modules behind it. Twenty of the twenty-two checks
        # never ask for it, and a validate run that does not reach these two should not pay
        # for them at import time.
        from ..workbench.cli import build_parser

        return build_parser()


#: The shipped implementations, named as values so a caller passes one rather than a
#: function whose module it would have to know — as `ports.local`'s adapter instances are.
RECIPE_GATE = _RecipeGate()
HUMAN_GATE_PARSER = _parse_human_gate
TASK_ROUTER = _TaskRouter()
STALE_REF_SCANNER = _scan_stale_refs
MCP_SCANNER = _mcp_scan
ASSET_RESOLVER = _resolve_asset
PARSER_SOURCE = _ParserSource()
