"""Deterministic choices shown before an interactive harness composition."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping

from rig_workbench.orchestrate.recipes import load_manifest, resolve_effective
from rig_workbench.packs.model import PackError
from rig_workbench.packs.resolver import resolve_asset

from .capabilities import resolve_task_route


SCHEMA = "rig.compose-options/v1"
AXES = ("recipe", "step", "gate", "backend", "mode")
_RECIPE_ALTERNATIVES = {
    "bugfix": ("bugfix", "fast-bugfix", "max-bugfix"),
    "performance": ("bugfix", "fast-bugfix", "max-bugfix"),
    "feature": ("feature", "design-first", "release-flow"),
    "review": ("review-only", "adversarial-review"),
    "security_review": ("review-only", "adversarial-review"),
    "release_support": ("release-flow", "hotfix"),
}


class ComposeOptionsError(PackError):
    """The five-axis composition cannot be stated without guessing."""


def non_negative_diff(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("diff must be a non-negative integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("diff must be a non-negative integer")
    return parsed


def _candidate(value: str, source: str, reason: str) -> dict:
    return {"value": value, "source": source, "reason": reason}


def _axis(axis_id: str, candidates: list[dict], recommended, reason: str) -> dict:
    return {"id": axis_id, "candidates": candidates, "recommended": recommended,
            "recommendation_reason": reason}


def validate_options(document: object) -> list[str]:
    """Validate the closed output contract; absence never means an unconstrained axis."""
    if not isinstance(document, Mapping):
        return [f"document: expected object, got {type(document).__name__}"]
    problems = []
    unknown = sorted(set(document) - {"schema", "task_type", "diff_lines", "size", "axes",
                                      "does_not_guarantee"})
    if unknown:
        problems.append(f"document: unknown keys: {', '.join(unknown)}")
    required = {"schema", "task_type", "diff_lines", "size", "axes", "does_not_guarantee"}
    missing_root = sorted(required - set(document))
    if missing_root:
        problems.append(f"document: missing keys: {', '.join(missing_root)}")
    if document.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}")
    if not isinstance(document.get("task_type"), str) or not document.get("task_type", "").strip():
        problems.append("task_type: expected non-empty string")
    diff_lines = document.get("diff_lines")
    if diff_lines is not None and (isinstance(diff_lines, bool) or
                                   not isinstance(diff_lines, int) or diff_lines < 0):
        problems.append("diff_lines: expected a non-negative integer or null")
    if document.get("size") not in {"S", "M", "L", "XL"}:
        problems.append("size: expected S, M, L, or XL")
    if (not isinstance(document.get("does_not_guarantee"), str) or
            not document.get("does_not_guarantee", "").strip()):
        problems.append("does_not_guarantee: expected non-empty string")
    axes = document.get("axes")
    if not isinstance(axes, list):
        return problems + ["axes: expected list"]
    seen = set()
    for position, axis in enumerate(axes):
        where = f"axes[{position}]"
        if not isinstance(axis, Mapping):
            problems.append(f"{where}: expected object")
            continue
        extra = sorted(set(axis) - {"id", "candidates", "recommended", "recommendation_reason"})
        if extra:
            problems.append(f"{where}: unknown keys: {', '.join(extra)}")
        axis_id = axis.get("id")
        if axis_id not in AXES:
            problems.append(f"{where}.id: unknown axis {axis_id!r}")
        elif axis_id in seen:
            problems.append(f"{where}.id: duplicate axis {axis_id!r}")
        else:
            seen.add(axis_id)
        candidates = axis.get("candidates")
        if not isinstance(candidates, list):
            problems.append(f"{where}.candidates: expected list")
            continue
        if not candidates:
            problems.append(f"{where}.candidates: empty; cannot compose this axis")
        values = []
        for offset, item in enumerate(candidates):
            if not isinstance(item, Mapping) or set(item) != {"value", "source", "reason"}:
                problems.append(f"{where}.candidates[{offset}]: expected closed candidate object")
                continue
            if not all(isinstance(item[key], str) and item[key].strip()
                       for key in ("value", "source", "reason")):
                problems.append(f"{where}.candidates[{offset}]: fields must be non-empty strings")
            values.append(item.get("value"))
        recommended = axis.get("recommended")
        chosen = recommended if isinstance(recommended, list) else [recommended]
        if not chosen or any(value not in values for value in chosen):
            problems.append(f"{where}.recommended: unresolved or outside candidates")
        if not isinstance(axis.get("recommendation_reason"), str) or not axis["recommendation_reason"].strip():
            problems.append(f"{where}.recommendation_reason: missing")
    missing = sorted(set(AXES) - seen)
    if missing:
        problems.append(f"axes: missing: {', '.join(missing)}")
    return problems


def compose_options(task_type: str, diff_lines: int | None, project, shared) -> dict:
    route = resolve_task_route(task_type, {}, project, shared=shared)
    if route["status"] not in {"ready", "degraded"} or not route["recipe"]:
        raise ComposeOptionsError(f"recipe axis cannot be resolved: {route['reason']}")
    asset = resolve_asset("recipe", route["recipe"], project=project, shared=shared)
    if asset is None:
        raise ComposeOptionsError(f"recipe axis cannot resolve `{route['recipe']}`")
    manifest = load_manifest()
    try:
        resolved = resolve_effective(asset.path, diff_lines=diff_lines, manifest=manifest)
    except (KeyError, TypeError, ValueError) as exc:
        raise ComposeOptionsError(f"step axis has an invalid source: {exc}") from None
    if resolved.get("errors"):
        raise ComposeOptionsError("step axis cannot be resolved: " + "; ".join(resolved["errors"]))

    recipe_names = list(_RECIPE_ALTERNATIVES.get(task_type, (route["recipe"],)))
    if route["recipe"] not in recipe_names:
        recipe_names.insert(0, route["recipe"])
    recipe_candidates = []
    for name in recipe_names:
        candidate_asset = resolve_asset("recipe", name, project=project, shared=shared)
        if candidate_asset is not None:
            recipe_candidates.append(_candidate(name, f"recipe:{candidate_asset.tier}",
                                                   f"resolvable {candidate_asset.tier} recipe"))
    recipe_candidates.append(_candidate("auto", "§4 RESOLVE", "defer recipe choice to RESOLVE"))

    active_steps = [step for step in resolved["steps"] if step["active"]]
    step_candidates = [_candidate(step["id"], f"recipe:{route['recipe']}.steps",
                                  step["why"]) for step in resolved["steps"]]
    step_candidates.append(_candidate("auto", "§4 RESOLVE", "defer step activation to RESOLVE"))
    gates = [step.get("gate") for step in active_steps]
    gate = ("acceptance-gate" if "acceptance-gate" in gates else
            "review-gate-only" if "review-gate" in gates else "auto")
    gate_reason = (f"active step gate in recipe `{route['recipe']}`" if gate != "auto" else
                   f"recipe `{route['recipe']}` has no active gate; RESOLVE remains authoritative")
    orchestrate = resolved["mode"]["orchestrate"]
    backend = "orchestrate" if orchestrate != "off" else resolved["mode"]["backend"]
    backend_reason = (f"RESOLVE orchestrate={orchestrate}" if backend == "orchestrate" else
                      f"effective backend from recipe/manifest default: {backend}")
    mode = "autonomous" if resolved["mode"]["autonomy"] == "autonomous" else "gated"

    document = {
        "schema": SCHEMA, "task_type": task_type, "diff_lines": diff_lines,
        "size": resolved["size"]["class"],
        "axes": [
            _axis("recipe", recipe_candidates, route["recipe"], route["reason"]),
            _axis("step", step_candidates, [step["id"] for step in active_steps],
                  f"recipe conditions evaluated by RESOLVE at size {resolved['size']['class']}"),
            _axis("gate", [
                _candidate("acceptance-gate", "patterns/acceptance-gate", "quality convergence gate"),
                _candidate("review-gate-only", "patterns/review-gate", "review verdict gate only"),
                _candidate("auto", "§4 RESOLVE", "defer gate choice to RESOLVE"),
            ], gate, gate_reason),
            _axis("backend", [
                _candidate("manual", "manifest.default_backend", "manual harness dispatch"),
                _candidate("workflow", "recipe.backend/--workflow", "workflow backend"),
                _candidate("orchestrate", "recipe.orchestrate/manifest.default_orchestrate",
                           "deterministic orchestration runner"),
                _candidate("auto", "§4 RESOLVE", "defer backend choice to RESOLVE"),
            ], backend, backend_reason),
            _axis("mode", [
                _candidate("gated", "recipe.autonomy", "confirm at step gates"),
                _candidate("autonomous", "recipe.autonomy/--autonomous", "skip step confirmations"),
                _candidate("auto", "§4 RESOLVE", "defer autonomy choice to RESOLVE"),
            ], mode, f"effective recipe autonomy is {resolved['mode']['autonomy']}"),
        ],
        "does_not_guarantee": "These options expose and explain choices; they do not guarantee a good choice.",
    }
    problems = validate_options(document)
    if problems:
        raise ComposeOptionsError("compose options refused:\n  " + "\n  ".join(problems))
    return document


def cmd_compose_options(args) -> None:
    from .state import invocation_root, repo_root

    try:
        document = compose_options(args.type, args.diff, invocation_root(), repo_root())
    except PackError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False,
                             sort_keys=True))
        else:
            print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for axis in document["axes"]:
        values = ", ".join(item["value"] for item in axis["candidates"])
        print(f"{axis['id'].upper()}: {values}")
        print(f"  recommended: {axis['recommended']} — {axis['recommendation_reason']}")
