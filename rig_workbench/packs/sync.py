"""Re-derive a pack manifest's `assets` and `hashes` from what is on disk.

This exists because of what an author hits on their second action. `pack init` scaffolds a
pack; the author adds a persona file; `pack validate` refuses with `asset declaration drift`.
The only way forward was to hand-edit `pack.yaml` — which is canonical single-line JSON, keys
sorted, no separators, trailing newline, byte-compared against `canonical()` by the very
check that just failed — and to add the file's sha256 to `hashes`, which must cover the
declared set exactly. Nothing in the tree wrote either field. Every shipped pack's manifest
was produced by something outside the CLI.

The canonical form is not the problem and is not relaxed here. It is what makes a manifest
hashable and signable, and `read_json_yaml` deliberately parses only the JSON subset so a
manifest cannot execute a YAML tag. The problem was that a machine-owned file had no machine
to own it. That is what this is.

Two refusals are deliberate.

**A file in no asset directory is an error, not a silent omission.** Dropping it would let a
file sit inside a pack, unhashed and undeclared, and `validate_pack` would then report the
pack as clean — the pack's contents and the pack's manifest would disagree with nobody
watching. It is named instead.

**A signed pack is refused outright.** Rewriting the manifest invalidates `pack.sig.json`,
and a sync that silently left a stale signature behind would be worse than no sync: the next
`verify` would fail somewhere far from the edit that caused it. Re-signing is the author's
decision, made with their key, so this stops and says so.
"""

from __future__ import annotations

import pathlib

from .manifest import canonical, digest, read_json_yaml
from .model import ASSET_DIRS, PackError, TYPE_ASSETS

#: Files that belong to the pack but are not assets: the manifest pair the assets are
#: declared in, and the signature over them.
NON_ASSETS = frozenset({"pack.yaml", "compatibility.yaml", "pack.sig.json"})

#: asset directory → kind. Reversed from `ASSET_DIRS` rather than written out again, so a new
#: kind added there is picked up here instead of quietly falling into the "unknown" branch.
_KIND_BY_DIR = {directory: kind for kind, directory in ASSET_DIRS.items()}


def _kind_of(relative: str) -> str | None:
    """The asset kind owning `relative`, by longest matching directory prefix.

    Longest wins because the directories nest: `facets/knowledge` and `facets/personas` share
    a parent, and a shortest-match rule would file every facet under whichever one sorted
    first. A file directly at the pack root matches nothing and returns None.
    """
    best: tuple[int, str] | None = None
    for directory, kind in _KIND_BY_DIR.items():
        if relative.startswith(f"{directory}/") and (best is None or len(directory) > best[0]):
            best = (len(directory), kind)
    return best[1] if best else None


def scan_assets(root: pathlib.Path) -> dict[str, list[str]]:
    """Every asset file under `root`, grouped by kind and sorted within each kind.

    Sorted because the manifest is byte-compared: an unsorted list would make the file's
    bytes depend on the order the filesystem happened to hand entries back, and two syncs of
    an unchanged pack would produce two different manifests.
    """
    grouped: dict[str, list[str]] = {kind: [] for kind in ASSET_DIRS}
    unknown: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PackError(f"pack symlink is forbidden: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in NON_ASSETS:
            continue
        kind = _kind_of(relative)
        if kind is None:
            unknown.append(relative)
            continue
        grouped[kind].append(relative)
    if unknown:
        raise PackError(
            "file is in no asset directory, so it cannot be declared: "
            + ", ".join(unknown)
            + " (move it under one of: " + ", ".join(sorted(ASSET_DIRS.values())) + ")")
    return grouped


def sync_manifest(root: pathlib.Path | str) -> dict[str, object]:
    """Rewrite `pack.yaml` so its `assets` and `hashes` describe the files that are there.

    Returns what changed, so the caller can print it. Nothing else in the manifest is
    touched: version, description, entrypoints and capabilities are the author's, and a sync
    that edited them would be making decisions it has no basis for.
    """
    root = pathlib.Path(root).resolve()
    if (root / "pack.sig.json").exists():
        raise PackError(
            "pack is signed; syncing would invalidate pack.sig.json — remove the signature "
            "and re-sign after the manifest is correct")
    _raw, manifest = read_json_yaml(root / "pack.yaml")
    type_ = manifest.get("type")
    if type_ not in TYPE_ASSETS:
        raise PackError(f"pack type is missing or unknown: {type_!r}")
    grouped = scan_assets(root)
    forbidden = sorted(kind for kind, paths in grouped.items()
                       if paths and kind not in TYPE_ASSETS[type_])
    if forbidden:
        raise PackError(
            f"a {type_} pack may not carry: {', '.join(forbidden)} "
            f"(allowed: {', '.join(sorted(TYPE_ASSETS[type_]))})")

    before = {item for paths in manifest.get("assets", {}).values() for item in paths}
    after = {item for paths in grouped.values() for item in paths}
    manifest["assets"] = grouped
    manifest["hashes"] = {item: digest(root / item) for item in sorted(after)}
    (root / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "total": len(after),
    }
