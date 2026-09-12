"""Re-derive a pack manifest's `assets` and `hashes` from what is on disk.

This exists because of what an author hits on their second action. `pack init` scaffolds a
pack; the author adds a persona file; `pack validate` refuses with `asset declaration drift`.
The only way forward was to hand-edit `pack.yaml` — which is canonical single-line JSON, keys
sorted, no separators, trailing newline, byte-compared against `canonical()` by the very
check that just failed — and to add the file's sha256 to `hashes`, which must cover the
declared set exactly. Nothing in the tree wrote either field. Every shipped pack's manifest
was produced by something outside the CLI.

The canonical form is not the problem and is not relaxed here. It is what makes a manifest
hashable, and `read_json_yaml` deliberately parses only the JSON subset so a manifest cannot
execute a YAML tag. The problem was that a machine-owned file had no machine to own it. That
is what this is.

**A file in no asset directory is an error, not a silent omission.** Dropping it would let a
file sit inside a pack, unhashed and undeclared, and `validate_pack` would then report the
pack as clean — the pack's contents and the pack's manifest would disagree with nobody
watching. It is named instead.

That rule absorbed the one that used to sit beside it. Sync refused a pack carrying
`pack.sig.json` by name, because rewriting the manifest invalidated the signature over it.
Nothing signs a pack any more, so `pack.sig.json` is not a pack file at all — it is a stray
at the pack root, in no asset directory, and the rule above already names it. `validate_pack`
agrees: it no longer excuses the name either, so such a pack is `asset declaration drift`
there. One rule, stated once, instead of a special case for a mechanism that is gone.

**An installed pack is not an author's tree, and sync refuses it.** Everything above is
written for a tree its author owns: `pack init` scaffolds it, the author adds a file, sync
declares it, `pack validate` passes. `pack install` produces a different object — a copy
under `.rig/packs/` whose manifest bytes `pack.lock.json` records as `manifest_sha256` and
`validate_lock_root` recomputes on every resolve. Rewriting that manifest is exactly the
drift the lock exists to catch, and `resolved_collection` is fail-closed, so a sync there
stops every persona, recipe and wiki lookup in the project.

Refused rather than repaired, and the alternative is worth stating because it looks helpful:
sync could update `manifest_sha256` and `asset_hashes` in the same operation. That would make
the lock re-bless whatever is on disk — one command, and the record that detects a changed
installed pack agrees with the change. It could not be done honestly in any case:
`source.sha256` pins the artefact the pack was installed from, and no amount of rescanning a
modified installed tree re-derives it. Nothing in the tree wants this either; the shipped
`pack-author` recipe's declare step runs sync, validate, doctor and test, and installs
nothing.
"""

from __future__ import annotations

import pathlib

from .lock import lock_path, read_lock
from .manifest import canonical, digest, read_json_yaml
from .model import ASSET_DIRS, PackError, TYPE_ASSETS
from .resources import describe_resource

#: Files that belong to the pack but are not assets: the manifest pair the assets are
#: declared in. Everything else at the pack root is a stray.
NON_ASSETS = frozenset({"pack.yaml", "compatibility.yaml"})

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


def owning_lock_entry(root: pathlib.Path) -> dict | None:
    """The `pack.lock.json` entry that owns `root`, or None if nothing installed it.

    Keyed on the lock, never on the directory. `.rig/packs/<id>` is where `pack install`
    puts a pack and it is also where `pack init`'s own signposting tells an author to
    scaffold one, so the path says nothing. The lock does: an entry here means `pack
    install` wrote this directory and recorded a digest over its manifest.

    A lock that cannot be read is treated as one that owns the pack. The question being
    asked is "may this manifest be rewritten", and "I cannot tell" is not a yes.
    """
    parent = root.parent
    if not lock_path(parent).exists():
        return None
    try:
        lock = read_lock(parent)
    except PackError:
        return {"id": root.name, "scope": "?", "source": {"path": "?"}}
    return next((item for item in lock["packs"]
                 if isinstance(item, dict) and item.get("path") == root.name), None)


def sync_manifest(root: pathlib.Path | str) -> dict[str, object]:
    """Rewrite `pack.yaml` so its `assets` and `hashes` describe the files that are there.

    Three fields are derived, because all three describe the directory rather than the
    author's intent: `assets` (what is there), `hashes` (its bytes) and, for resource files,
    the `{media_type, size, sha256}` triple `validate_pack` demands under `resources`. The
    media type is derived rather than asked for — the pairing between extension and declared
    type is checked, so a hand-written declaration could only agree with the derivation or be
    wrong.

    Everything else is untouched: version, description, entrypoints and capabilities are the
    author's, and a sync that edited them would be making decisions it has no basis for.
    """
    root = pathlib.Path(root).resolve()
    entry = owning_lock_entry(root)
    if entry is not None:
        # Before `read_json_yaml`, and a long way before the write: a manifest rewritten
        # under a lock that still names the old digest is drift, and the failure it
        # produces is worse than the one it was meant to fix. An undeclared file inside an
        # installed pack already fails `validate_pack` as `asset declaration drift`, which
        # names the file and so can be undone by deleting it. Syncing replaces that with
        # `pack lock drift: manifest changed`, which names nothing recoverable — the
        # manifest's original bytes are gone, and `pack remove` runs the same lock check,
        # so the pack cannot even be uninstalled.
        raise PackError(
            f"{entry['id']} is installed here and its manifest is pinned by "
            f"{lock_path(root.parent).name}, so sync will not rewrite it "
            f"(sync the pack where it is authored, then reinstall: "
            f"rig-wb pack remove {entry['id']} --scope {entry['scope']} && "
            f"rig-wb pack install {entry['source']['path']} --scope {entry['scope']})")
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
    # A resource carries a third derived field. `validate_pack` requires `resources` to cover
    # the resource assets exactly, so deriving two of the three and stopping would leave the
    # pack unvalidatable — which is what the first version of this function did.
    manifest["resources"] = {item: describe_resource(root, item)
                             for item in grouped["resource"]}
    (root / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "total": len(after),
    }
