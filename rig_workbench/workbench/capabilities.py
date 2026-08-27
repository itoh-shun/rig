"""Pure, read-only task capability routing for the workbench entrypoint."""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from collections.abc import Mapping

from rig_workbench.packs.catalog import catalog_records
from rig_workbench.packs.model import PackError, ResolvedAsset
from rig_workbench.packs.resolver import _core_assets, resolve_asset


ROUTE_SCHEMA_VERSION = 1
_REMOTE_PR_RECIPE = "pr-review"
_PREFERRED_PACKS = {
    "design": ("design", "design"),
    "test": ("test-design", "test-design"),
    "remote_pr": ("pr-review", "pr-review"),
}
_CORE_DEFAULTS = {
    "bugfix": "bugfix",
    "feature": "feature",
    "refactor": "refactor",
    "documentation": "documentation",
    "performance": "bugfix",
    "investigation": "debug",
    "release_support": "release-flow",
}

# Route-level evidence ownership.  This is deliberately separate from recipe
# ``acceptance[]``: recipes name work they perform, while these records account
# for every criterion the selected task type puts on the binding gate.  A
# manual owner is data, not a fallback used by the validator.
_STANDARD_PRODUCERS = {
    name: {"kind": "manual", "name": "operator"} for name in (
        "task_intent_satisfied", "no_unrelated_diff", "diff_summary_written",
        "risk_summary_written", "tests_pass_or_explained",
        "no_type_errors_or_explained", "no_secret_leak", "no_gate_tampering",
        "no_injection_markers", "no_destructive_operation",
    )
}
_STANDARD_PRODUCERS.update({
    "no_secret_leak": {"kind": "sensor", "name": "scan-secrets"},
    "no_gate_tampering": {"kind": "sensor", "name": "anti-tamper"},
    "no_injection_markers": {"kind": "sensor", "name": "scan-injection"},
    "no_destructive_operation": {"kind": "sensor", "name": "scan-destructive"},
})
_TASK_MANUAL = {
    "bugfix": {**_STANDARD_PRODUCERS, **{
        name: {"kind": "manual", "name": "operator"} for name in (
            "bug_cause_identified", "fix_is_minimal",
            "regression_test_added_or_explained", "existing_behavior_preserved",
            "no_unrelated_refactor",
        )}},
    "feature": {**_STANDARD_PRODUCERS, **{
        name: {"kind": "manual", "name": "operator"} for name in (
            "requirement_summary_written", "implementation_matches_requirement",
            "tests_added_or_explained", "public_api_changes_documented",
            "migration_or_backward_compatibility_considered",
        )}},
    "refactor": {**_STANDARD_PRODUCERS, **{
        name: {"kind": "manual", "name": "operator"} for name in (
            "behavior_boundaries_identified", "no_unintended_behavior_change",
            "tests_confirm_behavior_preserved", "no_unrelated_refactor",
            "public_api_changes_documented_if_any",
        )}},
    "standard": _STANDARD_PRODUCERS,
    "review": {name: {"kind": "manual", "name": "operator"} for name in (
        "findings_are_concrete", "severity_labeled", "file_references_included",
        "blocking_and_non_blocking_separated", "false_positive_risk_considered",
    )},
    "security_review": {name: {"kind": "manual", "name": "operator"} for name in (
        "findings_are_concrete", "severity_labeled", "file_references_included",
        "blocking_and_non_blocking_separated", "false_positive_risk_considered",
        "authn_authz_impact_checked", "user_input_flow_checked",
        "secret_exposure_checked", "unsafe_eval_or_shell_checked",
        "dependency_risk_checked",
    )},
}


def _step_owned(base, step, criteria):
    producers = dict(base)
    for criterion in criteria:
        if producers[criterion]["kind"] != "sensor":
            producers[criterion] = {"kind": "step", "name": step}
    return producers


_STANDARD_STEP_CRITERIA = (
    "task_intent_satisfied", "no_unrelated_diff", "diff_summary_written",
    "risk_summary_written", "tests_pass_or_explained", "no_type_errors_or_explained",
)
_BUGFIX_FLOW = _step_owned(
    _TASK_MANUAL["bugfix"], "acceptance", _STANDARD_STEP_CRITERIA + (
        "bug_cause_identified", "fix_is_minimal", "regression_test_added_or_explained",
        "existing_behavior_preserved", "no_unrelated_refactor",
    ),
)
_FEATURE_FLOW = _step_owned(
    _TASK_MANUAL["feature"], "acceptance", _STANDARD_STEP_CRITERIA + (
        "requirement_summary_written", "implementation_matches_requirement",
        "tests_added_or_explained", "public_api_changes_documented",
        "migration_or_backward_compatibility_considered",
    ),
)
_REFACTOR_FLOW = _step_owned(
    _TASK_MANUAL["refactor"], "acceptance", _STANDARD_STEP_CRITERIA + (
        "behavior_boundaries_identified", "no_unintended_behavior_change",
        "tests_confirm_behavior_preserved", "no_unrelated_refactor",
        "public_api_changes_documented_if_any",
    ),
)
_DOCUMENTATION_FLOW = _step_owned(
    _TASK_MANUAL["standard"], "acceptance", _STANDARD_STEP_CRITERIA,
)
_BUGFIX_AS_TEST = _step_owned(
    _TASK_MANUAL["feature"], "acceptance", _STANDARD_STEP_CRITERIA,
)


def _route(task_type, recipe, capability, context, profile, producers):
    return {
        "task_type": task_type, "recipe": recipe, "capability": capability,
        "context": context, "profile": profile, "producers": producers,
    }

# Closed-schema records consumed by ``scripts/validate.py``.  ``capabilities``
# is the routing authority, so keeping ownership beside the routes makes route
# additions visible in the same review.  Multiple capabilities may share a
# truthful ownership map; they remain distinct records and are validated
# independently.
ROUTE_PRODUCERS = (
    _route("bugfix", "bugfix", "bugfix", {}, "core", _BUGFIX_FLOW),
    _route("performance", "bugfix", "performance", {}, "core", _BUGFIX_FLOW),
    _route("feature", "feature", "feature", {}, "core", _FEATURE_FLOW),
    _route("refactor", "refactor", "refactor", {}, "core", _REFACTOR_FLOW),
    _route("documentation", "documentation", "documentation", {}, "core", _DOCUMENTATION_FLOW),
    _route("investigation", "debug", "investigation", {}, "core", _TASK_MANUAL["standard"]),
    _route("release_support", "release-flow", "release_support", {}, "core", _TASK_MANUAL["standard"]),
    _route("design", "design-first", "generic-design", {}, "core", _TASK_MANUAL["standard"]),
    _route("design", "design", "design", {}, "preferred-design", _TASK_MANUAL["standard"]),
    _route("test", "test-design", "test", {}, "preferred-test", _TASK_MANUAL["feature"]),
    _route("test", "feature", "test-implementation", {}, "core", _FEATURE_FLOW),
    _route("test", "bugfix", "test-implementation", {"implementation_type": "bugfix"}, "core", _BUGFIX_AS_TEST),
    _route("test", "review-only", "test-review", {"read_only": True}, "core", _TASK_MANUAL["feature"]),
    _route("review", "review-only", "review", {}, "core", _TASK_MANUAL["review"]),
    _route("review", "review-only", "diff-review", {"remote_pr": True, "has_diff": True}, "core", _TASK_MANUAL["review"]),
    _route("review", "pr-review", "remote_pr", {"remote_pr": True}, "preferred-pr", _TASK_MANUAL["review"]),
    _route("security_review", "review-only", "security-review", {}, "core", _TASK_MANUAL["security_review"]),
    _route("security_review", "review-only", "diff-review", {"remote_pr": True, "has_diff": True}, "core", _TASK_MANUAL["security_review"]),
    _route("security_review", "pr-review", "remote_pr", {"remote_pr": True}, "preferred-pr", _TASK_MANUAL["security_review"]),
)


class RouteResolutionError(PackError):
    """An explicitly requested route cannot be resolved safely."""


@dataclass(frozen=True)
class LocalRecipe:
    """Filesystem-free recipe facts consumed by the pure selector."""

    name: str
    tier: str
    pack: str | None
    trusted: bool
    canonical: bool = False


def _bool(context: Mapping[str, object], *names: str) -> bool:
    return any(context.get(name) is True for name in names)


def _read_only(context: Mapping[str, object]) -> bool:
    return _bool(context, "read_only") or context.get("mode") in {"read-only", "read_only"}


def _has_diff(context: Mapping[str, object]) -> bool:
    if _bool(context, "has_diff", "supplied_diff", "local_diff"):
        return True
    return any(isinstance(context.get(name), str) and bool(context[name].strip())
               for name in ("diff", "diff_path"))


def _explicit_value(context: Mapping[str, object]) -> object:
    if "recipe" in context and context.get("recipe") is not None:
        return context.get("recipe")
    return context.get("explicit_recipe")


def _plan(
    *, status: str, recipe: str | None, capability: str, reason: str,
    degraded: bool = False, reviewers: tuple[str, ...] = (), worktree: bool,
    context_kind: str,
) -> dict:
    return {
        "status": status, "recipe": recipe, "capability": capability,
        "reason": reason, "degraded": degraded, "reviewers": list(reviewers),
        "worktree": worktree, "context_kind": context_kind,
    }


def select_task_route(
    task_type: str,
    context: Mapping[str, object] | None,
    available: Mapping[str, LocalRecipe],
) -> dict:
    """Purely select a route from task context and pre-discovered recipe facts."""
    from .config import TASK_TYPES

    if task_type not in TASK_TYPES:
        raise RouteResolutionError(
            f"task_type `{task_type}` is invalid; valid: {', '.join(TASK_TYPES)}"
        )
    if context is None:
        values: Mapping[str, object] = {}
    elif isinstance(context, Mapping):
        values = context
    else:
        raise RouteResolutionError("route context must be a mapping")

    explicit = _explicit_value(values)
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise RouteResolutionError("explicit recipe must be a non-empty name")
        name = explicit.strip()
        candidate = available.get(name)
        if candidate is None:
            raise RouteResolutionError(f"explicit recipe `{name}` is not resolvable")
        if not candidate.trusted:
            return _plan(
                status="trust_required", recipe=name, capability="explicit-recipe",
                reason=(f"recipe `{name}` is shadowed by an untrusted {candidate.tier} asset; "
                        "routing will not bypass or approve it"),
                worktree=False, context_kind="explicit",
            )
        return _plan(
            status="ready", recipe=name, capability="explicit-recipe",
            reason=f"explicit recipe `{name}` preserved",
            worktree=not _read_only(values), context_kind="explicit",
        )

    def choose(
        name: str, *, capability: str, reason: str, worktree: bool,
        degraded: bool = False, reviewers: tuple[str, ...] = (),
        context_kind: str,
    ) -> dict:
        candidate = available.get(name)
        if candidate is None:
            raise RouteResolutionError(f"required core recipe `{name}` is not resolvable")
        if not candidate.trusted:
            return _plan(
                status="trust_required", recipe=name, capability=capability,
                reason=(f"recipe `{name}` is shadowed by an untrusted {candidate.tier} asset; "
                        "routing will not bypass or approve it"),
                worktree=False, context_kind=context_kind,
            )
        return _plan(
            status="degraded" if degraded else "ready", recipe=name,
            capability=capability, reason=reason, degraded=degraded,
            reviewers=reviewers, worktree=worktree, context_kind=context_kind,
        )

    def preferred(key: str) -> LocalRecipe | None:
        pack_id, name = _PREFERRED_PACKS[key]
        candidate = available.get(name)
        if candidate is None or candidate.tier == "core":
            return None
        if not candidate.trusted or (candidate.pack == pack_id and candidate.canonical):
            return candidate
        return None

    remote_pr = _bool(values, "remote_pr") or values.get("target") in {
        "remote-pr", "remote_pr",
    }
    has_diff = _has_diff(values)
    if task_type == "design":
        selected = preferred("design")
        if selected is not None:
            return choose(
                selected.name, capability="design", reason="preferred `design` capability is installed",
                worktree=False, context_kind="design",
            )
        return choose(
            "design-first", capability="generic-design",
            reason=("preferred design capability is unavailable; using the generic "
                    "core design-first workflow"),
            degraded=True, worktree=False, context_kind="design",
        )
    if task_type == "test":
        selected = preferred("test")
        if selected is not None:
            return choose(
                selected.name, capability="test", reason="preferred `test-design` capability is installed",
                worktree=False, context_kind="test-design",
            )
        if _read_only(values):
            return choose(
                "review-only", capability="test-review",
                reason="test task is read-only; using core review-only with test-reviewer",
                degraded=True, reviewers=("test-reviewer",), worktree=False,
                context_kind="read-only",
            )
        implementation = values.get("implementation_type") or values.get("classification") or "feature"
        if implementation not in {"feature", "bugfix"}:
            raise RouteResolutionError("test implementation_type must be `feature` or `bugfix`")
        return choose(
            str(implementation), capability="test-implementation",
            reason=("preferred test-design capability is unavailable; mapping test implementation "
                    f"to core {implementation}"),
            degraded=True, worktree=True, context_kind=f"implementation:{implementation}",
        )
    if task_type in {"review", "security_review"} and remote_pr:
        selected = preferred("remote_pr")
        reviewers = ("security-reviewer",) if task_type == "security_review" else ()
        if selected is not None:
            return choose(
                selected.name, capability="remote_pr",
                reason="preferred `pr-review` capability is installed",
                reviewers=reviewers, worktree=False, context_kind="remote-pr",
            )
        if not has_diff:
            return _plan(
                status="stopped", recipe=None, capability="remote-pr",
                reason=("remote PR review capability is unavailable and no local or supplied diff "
                        "was provided"),
                reviewers=reviewers, worktree=False, context_kind="remote-pr:no-diff",
            )
        return choose(
            "review-only", capability="diff-review",
            reason="remote PR capability is unavailable; reviewing the supplied diff locally",
            degraded=True, reviewers=reviewers, worktree=False,
            context_kind="remote-pr:diff",
        )
    if task_type in {"review", "security_review"}:
        reviewers = ("security-reviewer",) if task_type == "security_review" else ()
        return choose(
            "review-only", capability="security-review" if reviewers else "review",
            reason="ordinary review uses the trusted core review workflow",
            reviewers=reviewers, worktree=False, context_kind="review",
        )
    recipe = _CORE_DEFAULTS.get(task_type)
    if recipe is None:
        raise RouteResolutionError(f"no safe route is defined for task_type `{task_type}`")
    return choose(
        recipe, capability=task_type,
        reason=f"task type `{task_type}` maps to the trusted core `{recipe}` workflow",
        worktree=True, context_kind="default",
    )


def _catalog() -> list[dict]:
    # catalog_records performs full canonical manifest validation and fails closed.
    return catalog_records()


def _catalog_hint(records: list[dict], pack_id: str, recipe: str) -> str | None:
    for record in records:
        if record["kind"] != "official" or record["id"] != pack_id:
            continue
        if not any(
            entry["kind"] == "recipe" and entry["target"] == recipe
            for entry in record["entrypoints"]
        ):
            continue
        return f"Install the canonical capability with `rig-wb pack install official:{pack_id}`."
    return None


def _canonical_preferred(
    asset: ResolvedAsset, records: list[dict], pack_id: str, recipe: str,
) -> bool:
    """Require both runtime resolution and exact canonical catalog identity."""
    if asset.pack_id != pack_id:
        return False
    record = next((item for item in records if item["kind"] == "official"
                   and item["id"] == pack_id), None)
    if record is None or not any(
        entry["kind"] == "recipe" and entry["target"] == recipe
        for entry in record["entrypoints"]
    ):
        return False
    manifest = pathlib.Path(asset.source) / "pack.yaml"
    try:
        actual = hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError:
        return False
    return actual == record["manifest_sha256"]


def _legacy_project_recipe_trusted(asset: ResolvedAsset) -> bool:
    from rig_workbench.orchestrate import recipes

    try:
        digest = hashlib.sha256(asset.path.resolve().read_bytes()).hexdigest()
    except OSError:
        return False
    return recipes._load_trust_store().get(str(asset.path.resolve())) == digest


def _pack_asset_trusted(asset: ResolvedAsset) -> bool:
    from rig_workbench.packs import trust

    try:
        identity = trust._identity(asset)
        store_path = trust._store_path()
        store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False
    return store.get(f"{asset.kind}:{identity['path']}") == identity


def _trusted(asset: ResolvedAsset) -> bool:
    if asset.tier not in {"project", "user", "org"}:
        return True
    if asset.pack_id is None:
        return _legacy_project_recipe_trusted(asset)
    return _pack_asset_trusted(asset)


def _levenshtein(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for index, a in enumerate(left, 1):
        current = [index]
        for offset, b in enumerate(right, 1):
            current.append(min(
                previous[offset] + 1,
                current[offset - 1] + 1,
                previous[offset - 1] + (a != b),
            ))
        previous = current
    return previous[-1]


def _trusted_recipe_catalog(project: pathlib.Path) -> tuple[list[str], list[dict]]:
    del project  # Deliberately exclude ambient project/user assets from suggestions.
    records = _catalog()
    names = {asset.name for asset in _core_assets() if asset.kind == "recipe"}
    names.update(
        entry["target"]
        for record in records
        for entry in record["entrypoints"]
        if entry["kind"] == "recipe"
    )
    return sorted(names), records


def _missing_recipe_message(recipe: str, project: pathlib.Path) -> str:
    names, records = _trusted_recipe_catalog(project)
    suggestions = sorted(
        ((_levenshtein(recipe, candidate), candidate) for candidate in names),
        key=lambda item: (item[0], item[1]),
    )[:3]
    close = [candidate for distance, candidate in suggestions if distance <= 3]
    details = f"explicit recipe `{recipe}` is not resolvable"
    if close:
        details += f"; trusted suggestions: {', '.join(close)}"
    install = next(
        (
            f"rig-wb pack install {record['alias']}"
            for record in records
            if any(entry["kind"] == "recipe" and entry["target"] == recipe
                   for entry in record["entrypoints"])
        ),
        None,
    )
    if install:
        details += f"; install from the canonical catalog with `{install}`"
    return details


def resolve_task_route(
    task_type: str,
    context: Mapping[str, object] | None,
    project: pathlib.Path | str,
    shared: pathlib.Path | str | None = None,
) -> dict:
    """Resolve a task route without installing, downloading, approving, or writing.

    ``context`` is intentionally small and JSON-shaped. Recognized keys include
    ``recipe``/``explicit_recipe``, ``remote_pr``/``target=remote-pr``, boolean
    or string diff signals, ``read_only``/``mode=read-only``, and
    ``implementation_type`` (``feature`` or ``bugfix``). Unknown keys do not
    influence routing.
    """
    if context is None:
        values: Mapping[str, object] = {}
    elif isinstance(context, Mapping):
        values = context
    else:
        raise RouteResolutionError("route context must be a mapping")

    project_root = pathlib.Path(project).resolve()
    # `project` is the tree whose tracked content counts; `shared` is the repository whose
    # gitignored install state does. They differ whenever a caller stands in a worktree,
    # and collapsing them routes a task by whichever half happened to win (#471).
    shared_root = project_root if shared is None else pathlib.Path(shared).resolve()
    records = _catalog()
    explicit = _explicit_value(values)
    names = set(_CORE_DEFAULTS.values()) | {
        "design", "design-first", "test-design", "pr-review", "review-only",
    }
    if isinstance(explicit, str) and explicit.strip():
        names.add(explicit.strip())

    assets: dict[str, ResolvedAsset] = {}
    available: dict[str, LocalRecipe] = {}
    for name in sorted(names):
        asset = resolve_asset("recipe", name, project=project_root, shared=shared_root)
        if asset is None:
            continue
        assets[name] = asset
        preferred = _PREFERRED_PACKS.get(
            "remote_pr" if name == "pr-review" else "test" if name == "test-design" else name
        )
        canonical = bool(
            preferred
            and _canonical_preferred(asset, records, preferred[0], preferred[1])
        )
        available[name] = LocalRecipe(
            name=name,
            tier=asset.tier,
            pack=asset.pack_id,
            # An exact manifest from the packaged official catalog is trusted
            # by that canonical identity even when installed in project scope.
            trusted=canonical or _trusted(asset),
            canonical=canonical,
        )

    try:
        selected = select_task_route(task_type, values, available)
    except RouteResolutionError as exc:
        if isinstance(explicit, str) and explicit.strip() and explicit.strip() not in available:
            raise RouteResolutionError(
                _missing_recipe_message(explicit.strip(), project_root)
            ) from None
        raise exc

    recipe = selected["recipe"]
    asset = assets.get(recipe) if recipe else None
    capability = selected["capability"]
    hint: str | None = None
    preferred_key = (
        "design" if task_type == "design"
        else "test" if task_type == "test"
        else "remote_pr" if capability in {"remote_pr", "remote-pr", "diff-review"}
        else None
    )
    if preferred_key and not (asset and available[recipe].canonical):
        pack_id, preferred_recipe = _PREFERRED_PACKS[preferred_key]
        hint = _catalog_hint(records, pack_id, preferred_recipe)
    if selected["status"] == "stopped" and hint is None:
        hint = "Supply a local diff before using the core review-only fallback."
    if selected["status"] == "trust_required":
        hint = "Review and explicitly trust the winning recipe before creating a task."

    return {
        "route_schema_version": ROUTE_SCHEMA_VERSION,
        "task_type": task_type,
        "status": selected["status"],
        "degraded": selected["degraded"],
        "reason": selected["reason"],
        "capability": capability,
        "recipe": recipe,
        "tier": asset.tier if asset else None,
        "pack": asset.pack_id if asset else None,
        "reviewers": selected["reviewers"],
        "worktree": selected["worktree"],
        "hint": hint,
        "provenance": {
            "authority": "rig_workbench.workbench.capabilities.select_task_route",
            "resolver": "validated-tiered-recipe-resolver",
            "catalog": "trusted-builtin-canonical-catalog",
            "explicit": selected["context_kind"] == "explicit",
            "context": selected["context_kind"],
        },
    }
