"""Deterministic prompt-surface impact analysis."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from typing import Any

from .cases import EvalCaseError, canonical_json, validate_case

REGISTRY_VERSION = 1
_SURFACE_PREFIXES = (
    ("skills/engine/facets/instructions/", "instruction"),
    ("skills/engine/facets/personas/", "persona"),
    ("skills/engine/facets/policies/", "policy"),
    ("skills/engine/facets/output-contracts/", "contract"),
    ("skills/engine/facets/knowledge/", "wiki"),
    ("skills/engine/patterns/", "pattern"),
    ("skills/engine/recipes/", "recipe"),
    ("skills/engine/agents/", "agent"),
    ("agents/", "agent"),
    ("commands/", "command"),
)
_KNOWN_SUFFIXES = {".md", ".yaml", ".yml"}


def prompt_surface_registry() -> dict:
    return {
        "prompt_surface_registry_version": REGISTRY_VERSION,
        "roots": [
            {"prefix": prefix, "kind": kind, "extensions": sorted(_KNOWN_SUFFIXES)}
            for prefix, kind in _SURFACE_PREFIXES
        ],
    }


def _changed_files(root: pathlib.Path, base: str, head: str) -> list[str]:
    for value, label in ((base, "base"), (head, "head")):
        if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
            raise EvalCaseError(f"affected {label} revision is invalid")
    args = ["git", "diff", "--name-only", "--relative", base]
    if head != "working":
        args.append(head)
    args.append("--")
    try:
        completed = subprocess.run(
            args, cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot compute affected git diff") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot compute affected git diff")
    paths = set(completed.stdout.splitlines())
    if head == "working":
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
        if untracked.returncode != 0:
            raise EvalCaseError("cannot enumerate untracked affected files")
        paths.update(untracked.stdout.splitlines())
    safe = [path for path in paths if path and "\n" not in path and "\x00" not in path]
    return sorted(safe)


def _resolved_head(root: pathlib.Path, head: str) -> str:
    revision = "HEAD" if head == "working" else head
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve affected head revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvalCaseError("cannot resolve affected head revision")
    return value


def _surface(path: str) -> dict | None:
    if path == "evals/prompt-surfaces.json":
        return {"path": path, "kind": "unknown", "id": "unknown:prompt-surface-registry"}
    for prefix, kind in _SURFACE_PREFIXES:
        if path.startswith(prefix):
            suffix = pathlib.PurePosixPath(path).suffix
            relative = path[len(prefix):]
            name = str(pathlib.PurePosixPath(relative).with_suffix(""))
            resolved_kind = kind if suffix in _KNOWN_SUFFIXES and name else "unknown"
            return {"path": path, "kind": resolved_kind, "id": f"{resolved_kind}:{name}"}
    if path.startswith("skills/engine/facets/"):
        return {"path": path, "kind": "unknown", "id": f"unknown:{path}"}
    return None


def _graph(
    root: pathlib.Path, *, mode: str = "source-tree",
) -> tuple[dict[str, dict], list[dict]]:
    """Use a hermetic source-tree graph for prompt regression analysis.

    Installed extension tiers are intentionally excluded: affected-case
    selection must describe the checked-out source tree, not ambient user or
    project pack state.
    """
    if mode != "source-tree":
        raise ValueError(f"unknown affected graph mode: {mode}")
    try:
        from rig_workbench.orchestrate import config
        from rig_workbench.orchestrate.graph import build_brick_graph
        if config.RIG_HOME.resolve() == root.resolve():
            graph = build_brick_graph(project=root, mode="core")
            return ({node["path"]: node for node in graph["nodes"]}, graph["edges"])
    except (OSError, ValueError):
        pass
    # Fixture/project adapter: derive the same relations needed for reverse impact.
    try:
        from rig_workbench.orchestrate.recipes import parse_frontmatter
        nodes: dict[str, dict] = {}
        for prefix, kind in _SURFACE_PREFIXES:
            directory = root / prefix
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix in _KNOWN_SUFFIXES:
                    name = str(path.relative_to(directory).with_suffix(""))
                    node_id = f"{kind}:{name}"
                    nodes[path.relative_to(root).as_posix()] = {
                        "id": node_id, "kind": kind,
                        "path": path.relative_to(root).as_posix(),
                    }
        edges: list[dict] = []
        for node in nodes.values():
            path = root / node["path"]
            if node["kind"] == "recipe":
                fm = parse_frontmatter(path)
                if fm.get("extends"):
                    edges.append({"from": node["id"], "to": f"recipe:{fm['extends']}"})
                for step in fm.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    for field, kind in (("instruction", "instruction"),
                                        ("pattern", "pattern"),
                                        ("output_contract", "contract")):
                        if step.get(field):
                            edges.append({"from": node["id"],
                                          "to": f"{kind}:{step[field]}"})
                    for persona in step.get("personas") or []:
                        edges.append({"from": node["id"], "to": f"persona:{persona}"})
                    for policy in step.get("policies") or []:
                        edges.append({"from": node["id"], "to": f"policy:{policy}"})
            elif node["kind"] == "persona":
                fm = parse_frontmatter(path)
                for value in fm.get("inject") or []:
                    match = re.fullmatch(r"\[\[([a-z0-9-]+)(?:\|[^]]*)?\]\]", str(value))
                    if match:
                        candidates = [item["id"] for item in nodes.values()
                                      if item["kind"] == "wiki"
                                      and item["id"].split(":", 1)[1].endswith(match.group(1))]
                        target = candidates[0] if len(candidates) == 1 else f"wiki:{match.group(1)}"
                        edges.append({"from": node["id"], "to": target})
        return nodes, edges
    except (OSError, UnicodeError, ValueError):
        return {}, []


def _recipes_by_surface(root: pathlib.Path, surfaces: list[dict]) -> dict[str, list[str]]:
    nodes_by_path, edges = _graph(root)
    reverse: dict[str, set[str]] = {}
    for edge in edges:
        reverse.setdefault(edge["to"], set()).add(edge["from"])
    result: dict[str, list[str]] = {}
    for surface in surfaces:
        changed_id = (
            nodes_by_path[surface["path"]]["id"]
            if surface["path"] in nodes_by_path else surface["id"]
        )
        queue = [changed_id]
        visited = set(queue)
        recipes: set[str] = set()
        while queue:
            node = queue.pop(0)
            if node.startswith("recipe:"):
                recipes.add(node.split(":", 1)[1])
            for parent in sorted(reverse.get(node, set())):
                if parent not in visited:
                    visited.add(parent)
                    queue.append(parent)
        result[surface["path"]] = sorted(recipes)
    return result


def _load_cases(root: pathlib.Path) -> list[dict]:
    cases: list[dict] = []
    tier = root / "evals" / "cases"
    if not tier.is_dir():
        return cases
    for path in sorted(tier.glob("*/case.json")):
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvalCaseError(f"cannot read affected case: {path}") from exc
        validate_case(value)
        if value["status"] != "approved" or raw != canonical_json(value):
            raise EvalCaseError(f"affected coverage case is not approved canonical JSON: {path}")
        cases.append(value)
    return cases


def analyze_affected(
    repo: pathlib.Path | str, *, base: str, head: str = "working",
    require_cases: bool = False, evidence_dir: pathlib.Path | str | None = None,
) -> dict:
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError("cannot resolve affected repository") from exc
    changed = _changed_files(root, base, head)
    resolved_head = _resolved_head(root, head)
    surfaces = [surface for path in changed if (surface := _surface(path)) is not None]
    recipes_by_surface = _recipes_by_surface(root, surfaces)
    recipes = sorted({recipe for values in recipes_by_surface.values() for recipe in values})
    cases = _load_cases(root)
    selected: list[str] = []
    uncovered: list[str] = []
    recipe_matches = {
        recipe: [
            case["id"] for case in cases
            if f"recipe:{recipe}" in case.get("prompt_surfaces", [])
        ]
        for recipe in recipes
    }
    for surface in surfaces:
        matched = [case["id"] for case in cases
                   if surface["id"] in case.get("prompt_surfaces", [])]
        if surface["kind"] == "recipe":
            recipe = surface["id"].split(":", 1)[-1]
            matched.extend(recipe_matches.get(recipe, []))
        indirectly_covered = any(
            recipe_matches[recipe] for recipe in recipes_by_surface[surface["path"]]
        )
        if (surface["kind"] == "unknown"
                or (require_cases and not matched and not indirectly_covered)):
            uncovered.append(surface["path"])
        selected.extend(matched)
    for recipe in recipes:
        matched = recipe_matches[recipe]
        if require_cases and not matched:
            recipe_paths = [item["path"] for item in surfaces]
            uncovered.extend(recipe_paths or [f"recipe:{recipe}"])
        selected.extend(matched)
    selected = sorted(set(selected))
    evidence: dict[str, str] = {}
    if evidence_dir is not None:
        evidence_root = pathlib.Path(evidence_dir)
        for case_id in selected:
            found = False
            if evidence_root.is_dir():
                for path in evidence_root.rglob("*.json"):
                    try:
                        value: Any = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and value.get("case_id") == case_id:
                        found = True
                        break
            evidence[case_id] = "present" if found else "absent"
    status = "noop" if not surfaces else ("uncovered" if uncovered else "pass")
    return {
        "eval_affected_schema_version": 1,
        "registry_version": REGISTRY_VERSION,
        "base": base, "head": head, "resolved_head": resolved_head,
        "changed_files": changed,
        "affected_surfaces": sorted(surfaces, key=lambda item: item["path"]),
        "affected_recipes": recipes, "affected_cases": selected,
        "uncovered": sorted(set(uncovered)), "evidence_status": evidence,
        "status": status,
    }
