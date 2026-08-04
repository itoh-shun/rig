from __future__ import annotations

import json
import os
import pathlib

from .manifest import canonical, digest
from .model import PackError, ResolvedAsset


def _store_path() -> pathlib.Path:
    configured = os.environ.get("RIG_PACK_TRUST_STORE") or os.environ.get("RIG_TRUST_STORE")
    return pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".rig" / "trusted-pack-assets.json"


def _identity(asset: ResolvedAsset) -> dict:
    pack_manifest = pathlib.Path(asset.source) / "pack.yaml" if asset.pack_id else None
    return {
        "kind": asset.kind, "path": str(asset.path.resolve()),
        "content_sha256": digest(asset.path),
        "pack_sha256": digest(pack_manifest) if pack_manifest and pack_manifest.is_file() else None,
        "tier": asset.tier,
    }


def ensure_asset_trusted(asset: ResolvedAsset) -> pathlib.Path:
    if asset.tier not in {"project", "user", "org"}:
        return asset.path
    identity = _identity(asset)
    key = f"{asset.kind}:{identity['path']}"
    store_path = _store_path()
    try:
        store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        store = {}
    if store.get(key) == identity:
        return asset.path
    allowed = (
        os.environ.get("RIG_ALLOW_PROJECT_PACKS") == "1"
        or os.environ.get(f"RIG_ALLOW_PROJECT_{asset.kind.upper().replace('-', '_')}S") == "1"
        or "--allow-project-packs" in os.sys.argv
    )
    if not allowed:
        raise PackError(
            f"untrusted {asset.tier} {asset.kind} asset: {asset.path}; "
            "review it and approve with RIG_ALLOW_PROJECT_PACKS=1"
        )
    store[key] = identity
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = store_path.with_suffix(store_path.suffix + ".tmp")
        temporary.write_text(canonical(store), encoding="utf-8")
        os.replace(temporary, store_path)
    except OSError as exc:
        raise PackError(f"cannot persist pack trust record: {exc}") from exc
    return asset.path
