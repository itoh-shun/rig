"""Deterministic prompt-surface impact analysis."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from typing import Any

from .cases import EvalCaseError, canonical_json, validate_case
from .execution import GIT_DETERMINISTIC

REGISTRY_VERSION = 2
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

# Roots whose **direct children** are surfaces while their subdirectories are not.
#
# `skills/engine/` holds the engine's own prose — SKILL.md, which decides
# PARSE/RESOLVE/COMPOSE/RUN for every single run, and PACKS.md, which SKILL.md
# itself sends the reader to. Every registered root above is a *subdirectory* of
# this one, so the two documents that govern all of them were the only prompt
# surfaces in the repository that the registry could not see: editing one line of
# a persona registered as an affected surface, while rewriting §6 of SKILL.md
# reported `noop`. That is the same defect the ratchet was built to remove
# (#383/#384), pointing the other way — there, a check that fired on everything
# distinguished nothing; here, the check does not fire on the file that matters
# most.
#
# Stated as a rule about the directory rather than as a list of two filenames on
# purpose: an explicit list reproduces the hole the moment somebody adds a third
# engine document. Subdirectories are excluded because they are either already
# registered above, or are not prompt surfaces at all (`corpora/` is drill
# fixture data — evidence the gate consumes, not prose the model reads).
_SURFACE_FLAT_ROOTS = (
    ("skills/engine/", "engine"),
)
_KNOWN_SUFFIXES = {".md", ".yaml", ".yml"}
# The declaration of what the surfaces are, checked in so a change to the gate's
# field of view shows up in a diff.
REGISTRY_REL = "evals/prompt-surfaces.json"


def prompt_surface_registry() -> dict:
    return {
        "prompt_surface_registry_version": REGISTRY_VERSION,
        "roots": [
            {"prefix": prefix, "kind": kind, "recursive": True,
             "extensions": sorted(_KNOWN_SUFFIXES)}
            for prefix, kind in _SURFACE_PREFIXES
        ] + [
            {"prefix": prefix, "kind": kind, "recursive": False,
             "extensions": sorted(_KNOWN_SUFFIXES)}
            for prefix, kind in _SURFACE_FLAT_ROOTS
        ],
    }


def _merge_base(root: pathlib.Path, base: str, head: str) -> str:
    """The commit this branch actually forked from.

    Diffing against the base *tip* attributes everything the base branch did
    since the fork to this branch as well. On a branch that diverged a hundred
    commits ago that is most of the prompt layer, so the gate demands cases for
    surfaces the author never opened — which is what makes a release-scale PR
    structurally unpassable (#367). The fork point is what "this branch changed"
    means. Falls back to the base when there is no common ancestor.
    """
    revision = "HEAD" if head == "working" else head
    try:
        completed = subprocess.run(
            ["git", "merge-base", base, revision], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return base
    value = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        return base
    return value


def _changed_files(root: pathlib.Path, base: str, head: str) -> list[str]:
    for value, label in ((base, "base"), (head, "head")):
        if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
            raise EvalCaseError(f"affected {label} revision is invalid")
    # Same pins as the signed diff: which files this reports decides which cases
    # are affected, and `diff.renames` alone changes that answer — a rename is one
    # path under detection and two without it. `core.quotePath` decides whether a
    # non-ASCII path arrives in a form any surface prefix can match.
    args = ["git", *GIT_DETERMINISTIC,
            "diff", "--name-only", "--relative", "--no-ext-diff", "--no-textconv",
            _merge_base(root, base, head)]
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
            ["git", *GIT_DETERMINISTIC, "ls-files", "--others", "--exclude-standard"],
            cwd=root,
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


def _classify(path: str, prefix: str, kind: str) -> dict:
    suffix = pathlib.PurePosixPath(path).suffix
    relative = path[len(prefix):]
    name = str(pathlib.PurePosixPath(relative).with_suffix(""))
    resolved_kind = kind if suffix in _KNOWN_SUFFIXES and name else "unknown"
    return {"path": path, "kind": resolved_kind, "id": f"{resolved_kind}:{name}"}


def _surface(path: str) -> dict | None:
    if path == REGISTRY_REL:
        # Not a prompt surface: it is the declaration of what the prompt surfaces
        # *are*. Judged by `_registry_narrowings` instead — no eval case can be
        # written for a registry, so demanding one made it permanently unpassable.
        return None
    for prefix, kind in _SURFACE_PREFIXES:
        if path.startswith(prefix):
            return _classify(path, prefix, kind)
    if path.startswith("skills/engine/facets/"):
        return {"path": path, "kind": "unknown", "id": f"unknown:{path}"}
    # Checked after the recursive roots so a registered subdirectory always wins:
    # a recipe stays `recipe:<name>` rather than becoming `engine:recipes/<name>`.
    for prefix, kind in _SURFACE_FLAT_ROOTS:
        if path.startswith(prefix) and "/" not in path[len(prefix):]:
            return _classify(path, prefix, kind)
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


def _surface_commits(
    root: pathlib.Path, merge_base: str, head: str, paths: list[str],
) -> dict[str, list[str]]:
    """Which commits touched each uncovered path, newest first.

    A large PR that fails this gate otherwise reports a wall of paths with no
    way in. Naming the commit behind each one turns it into a triage list —
    the author can see which change owes a case, rather than the whole branch.
    Only computed for the paths that are actually blocking.
    """
    if not paths:
        return {}
    revision = "HEAD" if head == "working" else head
    result: dict[str, list[str]] = {}
    for path in sorted(set(paths)):
        try:
            completed = subprocess.run(
                ["git", "log", "--format=%h", "--max-count=5",
                 f"{merge_base}..{revision}", "--", path],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            commits = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if commits:
                result[path] = commits
    return result


def _registry_at(root: pathlib.Path, revision: str) -> dict[str, dict] | None:
    """prefix → its declared root at `revision`, or None if unreadable.

    Same stance as `_coverage_at`: None means the question could not be answered,
    and the caller then declines to accuse the change of anything.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"{revision}:{REGISTRY_REL}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if blob.returncode != 0:
        return None                    # not present at the base — nothing to lose
    try:
        value = json.loads(blob.stdout)
    except json.JSONDecodeError:
        return None
    roots = value.get("roots") if isinstance(value, dict) else None
    if not isinstance(roots, list):
        return None
    return {r["prefix"]: r for r in roots if isinstance(r, dict) and isinstance(r.get("prefix"), str)}


def _registry_narrowings(before: dict[str, dict] | None, after: dict) -> list[str]:
    """What this change took away from the gate's field of view.

    Editing the registry used to be fatal outright, on the reasoning that changing
    what the gate can see is not a coverage question. True, and the consequence was
    that **the registry could never be extended without failing the job** — the
    exact shape #383 was: a check nobody can pass, whose real lesson is that this
    job gets merged past. It taught that on the one change class that widens the
    gate's coverage.

    So the same rule the rest of this module uses applies to the registry itself:
    it is monotonic. Adding a root, or widening one, is the direction the gate is
    supposed to move and passes. Removing a root, renaming its kind (which silently
    orphans every case bound to the old ids), or narrowing its extensions or its
    recursion is coverage going *down*, and stays fatal.
    """
    if before is None:
        return []
    after_by_prefix = {r["prefix"]: r for r in after.get("roots", [])}
    lost: list[str] = []
    for prefix, root in sorted(before.items()):
        now = after_by_prefix.get(prefix)
        if now is None:
            lost.append(f"root removed: {prefix} (was {root.get('kind')})")
            continue
        if now.get("kind") != root.get("kind"):
            lost.append(f"kind renamed: {prefix} {root.get('kind')} -> {now.get('kind')} "
                        "(orphans every case bound to the old ids)")
        dropped = set(root.get("extensions") or []) - set(now.get("extensions") or [])
        if dropped:
            lost.append(f"extensions dropped: {prefix} ({', '.join(sorted(dropped))})")
        if root.get("recursive") and not now.get("recursive", True):
            lost.append(f"no longer recursive: {prefix}")
    return lost


def _coverage_at(root: pathlib.Path, revision: str) -> dict[str, set[str]] | None:
    """case id → the prompt surfaces it covered at `revision`, or None if unreadable.

    Read from the git tree rather than the working copy: the ratchet needs to know
    what coverage existed *before* the change in order to tell a surface that was
    never covered (debt) from one whose coverage this change removed (a
    regression). None means the question could not be answered — a shallow clone,
    an unborn ref — and the caller then declines to accuse anyone of a regression
    it cannot demonstrate.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", revision, "--", "evals/cases/"],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    coverage: dict[str, set[str]] = {}
    for path in listing.stdout.splitlines():
        if not path.endswith("/case.json"):
            continue
        try:
            blob = subprocess.run(
                ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=15, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if blob.returncode != 0:
            return None
        try:
            value = json.loads(blob.stdout)
        except json.JSONDecodeError:
            continue        # a malformed case at the base is not this change's fault
        if not isinstance(value, dict) or value.get("status") != "approved":
            continue
        case_id = value.get("id")
        surfaces = value.get("prompt_surfaces")
        if isinstance(case_id, str) and isinstance(surfaces, list):
            coverage[case_id] = {s for s in surfaces if isinstance(s, str)}
    return coverage


def _regressions(before: dict[str, set[str]] | None,
                 after: dict[str, set[str]]) -> list[str]:
    """Coverage the change took away: a case deleted, or one that dropped a surface.

    This is the half of the ratchet that stays a hard failure. Not having written
    a case yet is a starting position; deleting one somebody already earned with a
    measured red→green run is a step backwards, and a coverage gate that permits
    steps backwards is not a ratchet.
    """
    if before is None:
        return []
    lost: list[str] = []
    for case_id, surfaces in sorted(before.items()):
        if case_id not in after:
            lost.append(f"case:{case_id} (deleted; covered {', '.join(sorted(surfaces)) or 'nothing'})")
            continue
        dropped = surfaces - after[case_id]
        if dropped:
            lost.append(f"case:{case_id} (no longer covers {', '.join(sorted(dropped))})")
    return lost


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
    require_cases: bool = False, ratchet: bool = False,
    evidence_dir: pathlib.Path | str | None = None,
) -> dict:
    """Which prompt surfaces a change touches, and whether cases cover them.

    `require_cases` is the strict form: every affected surface must already have a
    case, or the change is `uncovered`. Correct as a destination and unreachable as
    a starting point — with an empty `evals/cases/` it fails every change that
    touches a prompt surface, including the ones that add the first case. A sensor
    that fires on everything reports nothing, and teaches people to merge past it.

    `ratchet` is the same requirement expressed as a direction rather than a
    threshold. A surface nobody has written a case for yet is **debt**: counted,
    named, not fatal. Coverage that this change *removes* is a **regression**, and
    still fatal. Debt can only be paid down and coverage can only go up, which is
    the same monotonic rule the policy layer uses — and unlike a threshold, it
    produces a number that moves from the first day.
    """
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError("cannot resolve affected repository") from exc
    changed = _changed_files(root, base, head)
    resolved_head = _resolved_head(root, head)
    merge_base = _merge_base(root, base, head)
    surfaces = [surface for path in changed if (surface := _surface(path)) is not None]
    recipes_by_surface = _recipes_by_surface(root, surfaces)
    recipes = sorted({recipe for values in recipes_by_surface.values() for recipe in values})
    cases = _load_cases(root)
    selected: list[str] = []
    uncovered: list[str] = []
    debt: list[str] = []
    demand = require_cases or ratchet
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
        missing = demand and not matched and not indirectly_covered
        if surface["kind"] == "unknown":
            # Not a coverage question: a file under a registered root whose kind the
            # registry does not recognise is a surface nobody is even tracking. That
            # stays fatal in both modes — a ratchet on an unmeasured thing is nothing.
            uncovered.append(surface["path"])
        elif missing:
            (debt if ratchet else uncovered).append(surface["path"])
        selected.extend(matched)
    for recipe in recipes:
        matched = recipe_matches[recipe]
        if demand and not matched:
            recipe_paths = [item["path"] for item in surfaces]
            (debt if ratchet else uncovered).extend(recipe_paths or [f"recipe:{recipe}"])
        selected.extend(matched)
    selected = sorted(set(selected))
    debt = sorted(set(debt) - set(uncovered))
    regressions = _regressions(_coverage_at(root, merge_base),
                               {case["id"]: set(case.get("prompt_surfaces", []))
                                for case in cases}) if ratchet else []
    # The registry is monotonic too, in both modes. Widening what the gate can see
    # is the direction it is meant to move; narrowing it is coverage going down.
    registry_changed = REGISTRY_REL in changed
    registry_narrowings = (
        _registry_narrowings(_registry_at(root, merge_base), prompt_surface_registry())
        if registry_changed else []
    )
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
    if uncovered or regressions or registry_narrowings:
        status = "uncovered"
    elif not surfaces:
        status = "noop"
    elif debt:
        # Deliberately its own status rather than folded into `pass`: the run is
        # allowed to proceed, and the number is still reported so paying it down is
        # visible progress instead of a silence that looks like coverage.
        status = "debt"
    else:
        status = "pass"
    return {
        "eval_affected_schema_version": 2,
        "registry_version": REGISTRY_VERSION,
        # Reported rather than inferred from `changed_files`: a reader checking why
        # the gate's field of view moved should not have to know the registry's path.
        "registry_changed": registry_changed,
        "registry_narrowings": registry_narrowings,
        "base": base, "head": head, "resolved_head": resolved_head,
        # The fork point the comparison actually used. Printed so a surprising
        # result can be checked against it instead of guessed at.
        "merge_base": merge_base,
        "changed_files": changed,
        "affected_surfaces": sorted(surfaces, key=lambda item: item["path"]),
        "affected_recipes": recipes, "affected_cases": selected,
        "uncovered": sorted(set(uncovered)),
        "coverage_debt": debt,
        "coverage_regressions": regressions,
        "evidence_status": evidence,
        "surface_commits": _surface_commits(root, merge_base, head,
                                            [*uncovered, *debt]),
        "status": status,
    }
