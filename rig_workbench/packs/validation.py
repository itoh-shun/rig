from __future__ import annotations

import pathlib
import re

from rig_workbench import __version__
from rig_workbench.eval.cases import validate_case
from rig_workbench.eval.safety import unsafe_text_reason
from rig_workbench.orchestrate.gates import validate_executable_recipe
from rig_workbench.workbench.destructive import scan_file as destructive_scan_file
from rig_workbench.workbench.injection import scan_file as injection_scan_file

from .manifest import (canonical, digest, parse_frontmatter_subset, read_json_yaml, safe_relative,
                       validate_compatibility, validate_manifest_shape)
from .model import ASSET_DIRS, PROMPT_KINDS, RECIPE_CHECKS_TYPES, PackError
from .resources import validate_resource


_CHECKS_KEY = re.compile(r"^(\s*)checks:(.*)$")


def declares_recipe_checks(path: pathlib.Path) -> bool:
    """Whether a recipe's frontmatter declares a non-empty `checks:` list.

    `checks:` entries are shell commands the orchestrator runs on the host, so this is the
    one thing in a pack that executes rather than being read. Only the frontmatter block is
    scanned — the word appears in recipe prose, and refusing a pack over a sentence would
    teach people to route around the check.

    Deliberately textual rather than parsed: `parse_frontmatter_subset` handles scalars and
    flat lists, not the list-of-mappings a recipe's `steps:` is, and a parser that cannot
    represent the shape it is asked about answers "no checks" for the wrong reason.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PackError(f"cannot read recipe frontmatter: {path.name}") from exc
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        raise PackError(f"unterminated frontmatter: {path.name}")
    lines = text[4:end].splitlines()
    for index, line in enumerate(lines):
        match = _CHECKS_KEY.match(line)
        if match is None:
            continue
        indent, inline = match.group(1), match.group(2).strip()
        if inline:
            if inline != "[]":
                return True
            continue
        for following in lines[index + 1:]:
            if not following.strip():
                continue
            deeper = len(following) - len(following.lstrip()) > len(indent)
            return deeper and following.lstrip().startswith("- ")
    return False


def _version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else (0, 0, 0)


def _compatible(spec: str, version: str = __version__) -> bool:
    if spec == "*":
        return True
    current = _version(version)
    for item in spec.split(","):
        item = item.strip()
        match = re.fullmatch(r"(>=|<=|>|<|==)?(\d+\.\d+\.\d+)", item)
        if not match:
            return False
        op, wanted = match.group(1) or "==", _version(match.group(2))
        if not {">=": current >= wanted, "<=": current <= wanted, ">": current > wanted,
                "<": current < wanted, "==": current == wanted}[op]:
            return False
    return True


def _frontmatter_refs(path: pathlib.Path) -> list[tuple[str, str]]:
    parsed = parse_frontmatter_subset(path)
    refs: list[tuple[str, str]] = []
    fields = {
        "instruction": "instruction", "pattern": "pattern", "gate": "pattern",
        "output_contract": "output-contract", "extends": "recipe",
        "personas": "persona", "policies": "policy",
    }
    def walk(value: object) -> None:
        if isinstance(value, dict):
            for field, item in value.items():
                if field in fields:
                    values = item if isinstance(item, list) else [item]
                    refs.extend((fields[field], str(entry)) for entry in values
                                if isinstance(entry, str) and entry)
                elif field in {"inject", "links"}:
                    values = item if isinstance(item, list) else [item]
                    for entry in values:
                        match = re.fullmatch(
                            r"\[\[([a-z0-9/_-]+)(?:\|[^]]+)?\]\]", str(entry)
                        )
                        if not match:
                            raise PackError(f"invalid wiki reference in {path.name}")
                        refs.append(("wiki", match.group(1)))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(parsed)
    return sorted(set(refs))


def _core_reference_ids() -> set[tuple[str, str]]:
    """Return only shipped core prompt IDs that extension packs may reuse."""
    from .resolver import _core_assets

    return {(asset.kind, asset.name) for asset in _core_assets()
            if asset.kind in PROMPT_KINDS}


def validate_pack(path: pathlib.Path | str) -> dict:
    supplied = pathlib.Path(path)
    if supplied.is_symlink():
        raise PackError("pack root symlink is forbidden")
    root = supplied.resolve()
    if not root.is_dir():
        raise PackError(f"pack directory does not exist: {root}")
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise PackError(f"pack symlink is forbidden: {candidate.relative_to(root)}")
    raw, manifest = read_json_yaml(root / "pack.yaml")
    validate_manifest_shape(manifest)
    if raw != canonical(manifest):
        raise PackError("pack.yaml is not canonical")
    compat_raw, compatibility = read_json_yaml(root / "compatibility.yaml")
    validate_compatibility(compatibility, manifest)
    if compat_raw != canonical(compatibility):
        raise PackError("compatibility.yaml is not canonical")
    if not _compatible(manifest["engine"]):
        raise PackError(f"pack is incompatible with engine {__version__}")
    declared = {item for paths in manifest["assets"].values() for item in paths}
    actual = {
        asset.relative_to(root).as_posix() for asset in root.rglob("*")
        if asset.is_file() and asset.name not in {
            "pack.yaml", "compatibility.yaml", "pack.sig.json",
        }
    }
    if actual != declared:
        missing, extra = sorted(declared - actual), sorted(actual - declared)
        raise PackError(f"asset declaration drift (missing={missing}, undeclared={extra})")
    ids: set[tuple[str, str]] = set()
    prompt_ids: set[str] = set()
    for kind, paths in manifest["assets"].items():
        prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
        for item in paths:
            rel = safe_relative(item)
            asset = root / rel
            expected_suffix = (
                ".json" if kind in {"eval-case", "eval-result"}
                else ((".yaml", ".yml") if kind == "agent"
                      else (None if kind == "resource" else ".md"))
            )
            suffixes = expected_suffix if isinstance(expected_suffix, tuple) else (expected_suffix,)
            if expected_suffix is not None and asset.suffix not in suffixes:
                raise PackError(f"asset extension is invalid for {kind}: {item}")
            if digest(asset) != manifest["hashes"][item]:
                raise PackError(f"asset hash mismatch: {item}")
            if (kind == "recipe" and manifest["type"] not in RECIPE_CHECKS_TYPES
                    and declares_recipe_checks(asset)):
                raise PackError(
                    f"a {manifest['type']} pack may not ship a recipe declaring `checks:` "
                    f"(host commands the orchestrator runs): {item}")
            name = str(rel.relative_to(prefix).with_suffix(""))
            if kind == "eval-case" and name.endswith("/case"):
                name = name[:-5]
            if (kind, name) in ids:
                raise PackError(f"duplicate asset id: {kind}:{name}")
            ids.add((kind, name))
            if kind in PROMPT_KINDS:
                surface_kind = "contract" if kind == "output-contract" else kind
                prompt_ids.add(f"{surface_kind}:{name}")
            if kind == "resource":
                resources = manifest.get("resources", {})
                if item not in resources:
                    raise PackError(f"resource metadata is missing: {item}")
                validate_resource(root, item, resources[item])
                continue
            try:
                asset_text = asset.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise PackError(f"asset is not readable UTF-8: {item}") from exc
            if unsafe_text_reason(asset_text):
                raise PackError(f"unsafe text in asset: {item}")
            if injection_scan_file(asset, item):
                raise PackError(f"injection marker in asset: {item}")
            if destructive_scan_file(asset, item):
                raise PackError(f"destructive content in asset: {item}")
    if prompt_ids and not manifest["assets"]["eval-case"]:
        raise PackError("prompt-bearing pack requires at least one evaluation case")
    core_ids = _core_reference_ids()
    available = ids | core_ids
    parsed_references: set[tuple[str, str]] = set()
    for kind, paths in manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        for item in paths:
            if kind == "recipe":
                parsed = parse_frontmatter_subset(root / item)
                execution = validate_executable_recipe(parsed)
                if execution["errors"]:
                    raise PackError(execution["errors"][0])
            for ref in _frontmatter_refs(root / item):
                parsed_references.add(ref)
    declared_references = manifest.get("references")
    manifest_references = {
        (reference["kind"], reference["id"]) for reference in (declared_references or [])
    }
    if declared_references is not None and parsed_references != manifest_references:
        missing = sorted(parsed_references - manifest_references)
        stale = sorted(manifest_references - parsed_references)
        raise PackError(f"typed reference drift (missing={missing}, stale={stale})")
    if declared_references is None:
        for key in parsed_references:
            if key not in available:
                raise PackError(f"broken pack reference: {key[0]}:{key[1]}")
    for reference in declared_references or []:
        key = (reference["kind"], reference["id"])
        owner = reference["pack"]
        if owner == manifest["id"] and key not in ids:
            raise PackError(f"broken self reference: {key[0]}:{key[1]}")
        if owner == "rig-core" and key not in core_ids:
            raise PackError(f"broken core reference: {key[0]}:{key[1]}")
        if key not in available and owner in {manifest["id"], "rig-core"}:
            raise PackError(f"broken pack reference: {key[0]}:{key[1]}")
    eval_surfaces: set[str] = set()
    for item in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(root / item)
        validate_case(case)
        if case["status"] != "approved":
            raise PackError(f"pack evaluation case must be promoted/approved: {item}")
        bound = set(case.get("prompt_surfaces", []))
        if not bound or not bound <= prompt_ids:
            raise PackError(f"evaluation case is not bound to owned prompt assets: {item}")
        eval_surfaces.update(bound)
    for entrypoint in manifest.get("entrypoints", []):
        target = (entrypoint["kind"], entrypoint["target"])
        if target not in ids:
            raise PackError(
                f"entrypoint target is not owned: {entrypoint['id']}->{target[0]}:{target[1]}"
            )
        surface_kind = "contract" if target[0] == "output-contract" else target[0]
        if f"{surface_kind}:{target[1]}" not in eval_surfaces:
            raise PackError(f"entrypoint lacks evaluation coverage: {entrypoint['id']}")
    return manifest


def validate_tiered_collection(entries: list[tuple[str, pathlib.Path]]) -> list[tuple[str, pathlib.Path, dict]]:
    records = [(tier, path, validate_pack(path)) for tier, path in entries]
    by_id: dict[str, dict] = {}
    owners: dict[tuple[str, str, str], str] = {}
    for tier, _path, manifest in records:
        if manifest["id"] in by_id:
            raise PackError(f"duplicate pack id: {manifest['id']}")
        by_id[manifest["id"]] = manifest
        for kind, paths in manifest["assets"].items():
            prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
            for item in paths:
                key = tier, kind, str(pathlib.PurePosixPath(item).relative_to(prefix).with_suffix(""))
                if key in owners:
                    raise PackError(f"same-tier asset collision: {kind}:{key[2]}")
                owners[key] = manifest["id"]
    graph = {item["id"]: [dep["id"] for dep in item["dependencies"]]
             for _tier, _path, item in records}
    for pack_id, dependencies in graph.items():
        manifest = by_id[pack_id]
        for dependency in manifest["dependencies"]:
            dep_id = dependency["id"]
            if dep_id not in graph:
                raise PackError(f"missing dependency: {pack_id}->{dep_id}")
            if not _compatible(dependency["range"], by_id[dep_id]["version"]):
                raise PackError(f"incompatible dependency: {pack_id}->{dep_id}")
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []
    def visit(node: str) -> None:
        if node in visiting:
            raise PackError("pack dependency cycle detected")
        if node in visited:
            return
        visiting.add(node)
        for child in sorted(graph[node]):
            visit(child)
        visiting.remove(node)
        visited.add(node)
        order.append(node)
    for node in sorted(graph):
        visit(node)
    closure_cache: dict[str, set[str]] = {}
    def closure(node: str) -> set[str]:
        if node not in closure_cache:
            closure_cache[node] = set(graph[node])
            for dependency in graph[node]:
                closure_cache[node].update(closure(dependency))
        return closure_cache[node]
    asset_ids: dict[str, set[tuple[str, str]]] = {}
    for pack_id, manifest in by_id.items():
        values: set[tuple[str, str]] = set()
        for kind, paths in manifest["assets"].items():
            prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
            for item in paths:
                name = str(pathlib.PurePosixPath(item).relative_to(prefix).with_suffix(""))
                if kind == "eval-case" and name.endswith("/case"):
                    name = name[:-5]
                values.add((kind, name))
        asset_ids[pack_id] = values
    for pack_id, manifest in by_id.items():
        for reference in manifest.get("references", []):
            owner = reference["pack"]
            if owner in {pack_id, "rig-core"}:
                continue
            if owner not in closure(pack_id):
                raise PackError(f"reference escapes dependency closure: {pack_id}->{owner}")
            key = (reference["kind"], reference["id"])
            if key not in asset_ids[owner]:
                raise PackError(
                    f"broken dependency reference: {pack_id}->{owner}:{key[0]}:{key[1]}"
                )
    record_by_id = {manifest["id"]: (tier, path, manifest)
                    for tier, path, manifest in records}
    return [record_by_id[pack_id] for pack_id in order]


def validate_collection(pack_dirs: list[pathlib.Path]) -> list[dict]:
    return [manifest for _tier, _path, manifest in validate_tiered_collection(
        [("global", path) for path in pack_dirs]
    )]
