from __future__ import annotations

import os
import pathlib
from collections.abc import Iterable

from .model import ASSET_DIRS, PackError, ResolvedAsset


def _rig_home() -> pathlib.Path:
    configured = os.environ.get("RIG_HOME")
    return pathlib.Path(configured).expanduser().resolve() if configured else pathlib.Path(__file__).resolve().parents[2]


def _project_root(project: pathlib.Path | str | None) -> pathlib.Path:
    return pathlib.Path(project or pathlib.Path.cwd()).resolve()


def pack_roots(project: pathlib.Path | str | None = None) -> list[tuple[str, pathlib.Path]]:
    root = _project_root(project)
    home = pathlib.Path(os.environ.get("RIG_USER_HOME", pathlib.Path.home())).expanduser()
    org = os.environ.get("RIG_ORG_HOME")
    result = [("project", root / ".rig" / "packs"), ("user", home / ".rig" / "packs")]
    if org:
        result.append(("org", pathlib.Path(org).expanduser() / "packs"))
    rig = _rig_home()
    result.extend((("official", rig / "packs" / "official"), ("core", rig / "packs" / "core")))
    return result


def _pack_entries(project: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    return [(tier, item) for tier, root in pack_roots(project) if root.is_dir()
            for item in sorted(root.iterdir()) if item.is_dir()]


def _validated_pack_assets(project: pathlib.Path) -> list[ResolvedAsset]:
    from .validation import validate_tiered_collection
    found: list[ResolvedAsset] = []
    for tier, pack, manifest in validate_tiered_collection(_pack_entries(project)):
        for kind, paths in manifest["assets"].items():
            for rel in paths:
                path = pack / rel
                prefix = pathlib.PurePosixPath(ASSET_DIRS[kind])
                name = str(pathlib.PurePosixPath(rel).relative_to(prefix).with_suffix(""))
                if kind == "eval-case" and name.endswith("/case"):
                    name = name[:-5]
                found.append(ResolvedAsset(kind, name, path, tier, str(pack), manifest["id"]))
    return found


def _legacy_assets(project: pathlib.Path) -> Iterable[ResolvedAsset]:
    mappings = [
        ("project", project / ".rig" / "recipes", "recipe"),
        ("project", project / ".claude" / "rig" / "recipes", "recipe"),
        ("project", project / ".claude" / "rig" / "personas", "persona"),
        ("project", project / ".claude" / "rig" / "knowledge", "wiki"),
    ]
    for tier, directory, kind in mappings:
        if directory.is_dir():
            for path in sorted(directory.rglob("*.md")):
                yield ResolvedAsset(kind, str(path.relative_to(directory).with_suffix("")), path,
                                    tier, f"legacy:{directory}", None)


def _core_assets() -> Iterable[ResolvedAsset]:
    rig = _rig_home()
    mappings = {
        "recipe": rig / "skills" / "rig" / "recipes",
        "persona": rig / "skills" / "rig" / "facets" / "personas",
        "instruction": rig / "skills" / "rig" / "facets" / "instructions",
        "pattern": rig / "skills" / "rig" / "patterns",
        "wiki": rig / "skills" / "rig" / "facets" / "knowledge",
        "policy": rig / "skills" / "rig" / "facets" / "policies",
        "output-contract": rig / "skills" / "rig" / "facets" / "output-contracts",
        "command": rig / "commands", "agent": rig / "agents",
    }
    for kind, directory in mappings.items():
        if directory.is_dir():
            for suffix in ("*.md", "*.yaml", "*.yml"):
                for path in sorted(directory.rglob(suffix)):
                    yield ResolvedAsset(kind, str(path.relative_to(directory).with_suffix("")), path,
                                        "core", f"core:{directory}", "rig-core")


def resolve_all(kind: str, name: str, *, project: pathlib.Path | str | None = None) -> list[ResolvedAsset]:
    if kind not in ASSET_DIRS:
        raise PackError(f"unknown asset kind: {kind}")
    project_root = _project_root(project)
    candidates: list[ResolvedAsset] = []
    candidates.extend(item for item in _validated_pack_assets(project_root)
                      if item.kind == kind and item.name == name)
    candidates.extend(item for item in _legacy_assets(project_root)
                      if item.kind == kind and item.name == name)
    if kind != "eval-case":
        candidates.extend(item for item in _core_assets() if item.kind == kind and item.name == name)
    rank = {tier: index for index, tier in enumerate(("project", "user", "org", "official", "core"))}
    return sorted(candidates, key=lambda item: (rank[item.tier], item.source, str(item.path)))


def resolve_asset(kind: str, name: str, *, project: pathlib.Path | str | None = None) -> ResolvedAsset | None:
    matches = resolve_all(kind, name, project=project)
    if not matches:
        return None
    winner = matches[0]
    return ResolvedAsset(
        winner.kind, winner.name, winner.path, winner.tier, winner.source, winner.pack_id,
        tuple(str(item.path) for item in matches[1:]),
    )


def catalog(*, project: pathlib.Path | str | None = None) -> list[ResolvedAsset]:
    root = _project_root(project)
    all_items: list[ResolvedAsset] = _validated_pack_assets(root)
    all_items.extend(_legacy_assets(root))
    all_items.extend(_core_assets())
    return sorted(all_items, key=lambda item: (item.kind, item.name, item.tier, str(item.path)))
