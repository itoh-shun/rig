from __future__ import annotations

import pathlib

from .manifest import PACK_ID, digest
from .model import PackError


def distribution_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def discover_builtin_packs(root: pathlib.Path | None = None) -> dict[tuple[str, str], tuple[pathlib.Path, dict]]:
    """Discover packaged official/domain packs from validated manifests.

    Directory names are never identities.  Every candidate is fully validated,
    its manifest kind must match the catalog namespace, and duplicate manifest
    identities fail the whole discovery operation closed.
    """
    from .validation import validate_pack

    base = (root or distribution_root()).resolve() / "packs"
    found: dict[tuple[str, str], tuple[pathlib.Path, dict]] = {}
    ids: dict[str, str] = {}
    for namespace in ("official", "domain"):
        directory = base / namespace
        if not directory.is_dir():
            continue
        for candidate in sorted(item for item in directory.iterdir() if item.is_dir()
                                and not item.name.startswith((".", "_"))):
            manifest = validate_pack(candidate)
            missing = {
                "display_name", "description", "capabilities", "entrypoints",
                "references", "resources",
            } - set(manifest)
            if missing:
                raise PackError(
                    f"builtin catalog manifest lacks catalog fields: {candidate.name}: "
                    f"{sorted(missing)}"
                )
            if manifest["kind"] != namespace:
                raise PackError(
                    f"builtin catalog kind mismatch: {candidate.name} is {manifest['kind']}, "
                    f"expected {namespace}"
                )
            pack_id = manifest["id"]
            if pack_id in ids:
                raise PackError(f"duplicate builtin pack id: {pack_id}")
            ids[pack_id] = namespace
            found[(namespace, pack_id)] = (candidate.resolve(), manifest)
    return found


def catalog_records(root: pathlib.Path | None = None) -> list[dict]:
    records = []
    for (namespace, _pack_id), (path, manifest) in discover_builtin_packs(root).items():
        records.append({
            "id": manifest["id"], "kind": manifest["kind"],
            "version": manifest["version"], "display_name": manifest["display_name"],
            "description": manifest["description"],
            "capabilities": manifest["capabilities"],
            "entrypoints": manifest["entrypoints"],
            "manifest_sha256": digest(path / "pack.yaml"),
            "alias": f"{namespace}:{manifest['id']}",
        })
    return sorted(records, key=lambda item: (item["kind"], item["id"]))


def resolve_builtin_alias(source: str, root: pathlib.Path | None = None) -> tuple[pathlib.Path, dict]:
    namespace, separator, pack_id = source.partition(":")
    if not separator or namespace not in {"official", "domain"}:
        raise PackError("builtin pack alias must use official:<id> or domain:<id>")
    if not PACK_ID.fullmatch(pack_id):
        raise PackError(f"built-in {namespace} pack id is invalid")
    record = discover_builtin_packs(root).get((namespace, pack_id))
    if record is None:
        raise PackError(f"unknown built-in {namespace} pack: {pack_id}")
    return record
