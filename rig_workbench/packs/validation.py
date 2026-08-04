from __future__ import annotations

import pathlib
import re

from rig_workbench import __version__
from rig_workbench.eval.cases import validate_case
from rig_workbench.eval.safety import unsafe_text_reason
from rig_workbench.workbench.destructive import scan_file as destructive_scan_file
from rig_workbench.workbench.injection import scan_file as injection_scan_file

from .manifest import (canonical, digest, parse_frontmatter_subset, read_json_yaml, safe_relative,
                       validate_compatibility, validate_manifest_shape)
from .model import ASSET_DIRS, PROMPT_KINDS, PackError


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
        "instruction": "instruction", "pattern": "pattern",
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
    try:
        body = path.read_text(encoding="utf-8").split("\n---", 1)[-1]
    except (OSError, UnicodeError) as exc:
        raise PackError(f"cannot read prompt asset: {path.name}") from exc
    refs.extend(("wiki", match.group(1)) for match in re.finditer(
        r"\[\[([a-z0-9/_-]+)(?:\|[^]]+)?\]\]", body
    ))
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
        if asset.is_file() and asset.name not in {"pack.yaml", "compatibility.yaml"}
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
                else ((".yaml", ".yml") if kind == "agent" else ".md")
            )
            suffixes = expected_suffix if isinstance(expected_suffix, tuple) else (expected_suffix,)
            if asset.suffix not in suffixes:
                raise PackError(f"asset extension is invalid for {kind}: {item}")
            if digest(asset) != manifest["hashes"][item]:
                raise PackError(f"asset hash mismatch: {item}")
            name = str(rel.relative_to(prefix).with_suffix(""))
            if kind == "eval-case" and name.endswith("/case"):
                name = name[:-5]
            if (kind, name) in ids:
                raise PackError(f"duplicate asset id: {kind}:{name}")
            ids.add((kind, name))
            if kind in PROMPT_KINDS:
                prompt_ids.add(f"{kind}:{name}")
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
    available = ids | _core_reference_ids()
    for kind, paths in manifest["assets"].items():
        if kind not in PROMPT_KINDS:
            continue
        for item in paths:
            for ref in _frontmatter_refs(root / item):
                if ref not in available:
                    raise PackError(f"broken pack reference: {ref[0]}:{ref[1]}")
    for item in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(root / item)
        validate_case(case)
        if case["status"] != "approved":
            raise PackError(f"pack evaluation case must be promoted/approved: {item}")
        bound = set(case.get("prompt_surfaces", []))
        if not bound or not bound <= prompt_ids:
            raise PackError(f"evaluation case is not bound to owned prompt assets: {item}")
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
    def visit(node: str) -> None:
        if node in visiting:
            raise PackError("pack dependency cycle detected")
        if node in visited:
            return
        visiting.add(node)
        for child in graph[node]:
            visit(child)
        visiting.remove(node)
        visited.add(node)
    for node in sorted(graph):
        visit(node)
    return records


def validate_collection(pack_dirs: list[pathlib.Path]) -> list[dict]:
    return [manifest for _tier, _path, manifest in validate_tiered_collection(
        [("global", path) for path in pack_dirs]
    )]
