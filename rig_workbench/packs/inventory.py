"""What is installed, where it came from, and whether it still holds (#523, slice S3).

The lock has carried a pack's source, version, and integrity since long before packs came
from anywhere but a local directory; nothing read it back. That is the gap this closes —
"install 済み Pack とその source/version/integrity を CLI で説明できる" is a reporting problem,
not a storage one, so this module reads and formats and never writes.

Two commands here reach the network and two do not, and they are kept apart deliberately:
`list`, `info`, and `explain` answer from the lock alone and are always cheap, while
`outdated` asks every source what tags it has. A person should be able to see what they have
without paying for a round trip per pack, and should know when they are about to.
"""

from __future__ import annotations

import pathlib
import re

from .lock import read_lock
from .model import PackError
from .resolver import resolve_all
from .sources import _git, read_sources, resolve_url  # noqa: PLC2701 - one git surface
from .validation import validate_pack

_TAG = re.compile(r"^refs/tags/v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$")


def _entries(root: pathlib.Path, *, scope: str | None = None) -> list[dict]:
    """Every pack in this root: the lock's entries, plus the ones somebody placed by hand.

    A pack can arrive in a scope root without the CLI — copied in, checked out, unpacked
    (#533). The resolver already reads such a directory: `validate_lock_root` returns no
    entries for a root with no lock and the collection walk takes every directory it finds.
    The inventory did not, so `pack list` said "no packs installed" about a pack whose
    recipes were resolving. This lists them, named for what they are.

    Only where there is no lock. A root that has one owns its directories, and an extra
    directory there is drift the lock check already refuses; reporting it here as a pack
    would put a "manual" row beside packs the lock never agreed to share the root with.
    """
    locked = sorted(read_lock(root)["packs"], key=lambda item: item["id"])
    if locked or _has_lock(root):
        return locked
    return _manual_entries(root, scope=scope)


def _has_lock(root: pathlib.Path) -> bool:
    from .lock import lock_path
    return lock_path(root).exists()


def _manual_entries(root: pathlib.Path, *, scope: str | None) -> list[dict]:
    """Lock-shaped entries for directories that were never installed.

    Shaped like a lock entry so every reader here keeps one code path, and marked so no
    reader can mistake one for an installed pack: `source.type` is `manual`, the
    verification status is `unverified` (nothing was checked at install time, because there
    was no install), and the fields only an install produces are absent rather than
    invented — there is no install time, no publisher key, no recorded resolution.
    """
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith((".", "_")):
            continue
        try:
            manifest = validate_pack(item)
        except PackError:
            # Listed as unreadable rather than skipped: a person who dropped a directory
            # here and sees nothing has no way to learn the manifest did not validate.
            entries.append({"id": item.name, "version": "?", "kind": "?",
                            "scope": scope or "manual", "path": item.name,
                            "source": {"type": "manual", "path": str(item)},
                            "verification_status": "unreadable", "dependencies": [],
                            "eval_case_hashes": [], "dependency_resolution": []})
            continue
        entries.append({
            "id": manifest["id"], "version": manifest["version"], "kind": manifest["kind"],
            "scope": scope or "manual", "path": item.name,
            "source": {"type": "manual", "path": str(item), "sha256": None},
            "verification_status": "unverified",
            "dependencies": list(manifest.get("dependencies", [])),
            "eval_case_hashes": [], "dependency_resolution": [],
        })
    return sorted(entries, key=lambda item: item["id"])


def _entry(root: pathlib.Path, pack_id: str, *, scope: str | None = None) -> dict:
    for item in _entries(root, scope=scope):
        if item["id"] == pack_id:
            return item
    raise PackError(f"pack is not owned by this scope lock: {pack_id}")


def _origin(entry: dict) -> str:
    """How this pack got here, in one field, without ever printing a URL."""
    source = entry["source"]
    if source["type"] == "git":
        return f"{source['path']} @{source['revision'][:12]}"
    if source["type"] == "manual":
        # Placed by hand, never installed: say so, and say what would make it a real
        # install, because "manual" alone reads as a category rather than a gap.
        return "manual (not installed; `pack install <dir>` records it)"
    return f"{source['type']}:{source['path']}"


def list_rows(root: pathlib.Path, *, scope: str | None = None) -> list[dict]:
    return [
        {
            "id": entry["id"], "version": entry["version"],
            "type": _installed_type(root, entry), "kind": entry["kind"],
            "origin": _origin(entry), "verification": entry["verification_status"],
        }
        for entry in _entries(root, scope=scope)
    ]


def _installed_type(root: pathlib.Path, entry: dict) -> str:
    """The pack's declared type, read from what is on disk rather than from the lock.

    The lock predates `type` and does not carry it. Reading the manifest keeps this honest
    for packs installed before the field existed, and means the answer describes the pack
    that is actually there — which is the question somebody auditing an install is asking.
    """
    try:
        return validate_pack(root / entry["path"])["type"]
    except PackError:
        return "?"


def info(root: pathlib.Path, pack_id: str, *, scope: str | None = None) -> dict:
    """Everything the lock and the installed manifest say about one pack."""
    entry = _entry(root, pack_id, scope=scope)
    manifest = validate_pack(root / entry["path"])
    source = entry["source"]
    if source["type"] == "manual":
        # No lock ever described this pack, so most of `info`'s rows have no source. The
        # ones that do come from the manifest on disk; the rest are absent, not null-filled.
        return {
            "id": entry["id"], "version": entry["version"], "type": manifest["type"],
            "kind": entry["kind"], "scope": entry["scope"], "path": str(root / entry["path"]),
            "source_type": "manual", "source": source["path"],
            "engine": manifest["engine"], "verification": entry["verification_status"],
            "dependencies": [f"{item['id']}{item['range']}" for item in manifest["dependencies"]],
            "assets": {kind: len(paths) for kind, paths in sorted(manifest["assets"].items())
                       if paths},
            **({"knowledge": manifest["knowledge"]} if "knowledge" in manifest else {}),
        }
    return {
        "id": entry["id"], "version": entry["version"], "type": manifest["type"],
        "kind": entry["kind"], "scope": entry["scope"],
        "path": str(root / entry["path"]),
        "source_type": source["type"], "source": source["path"],
        "source_id": source.get("source_id"), "revision": source.get("revision"),
        "content_sha256": source["sha256"],
        "manifest_sha256": entry["manifest_sha256"],
        "engine": manifest["engine"], "engine_installed_with": entry["engine_version"],
        "installed_at": entry["installed_at"],
        "verification": entry["verification_status"],
        "publisher_key_id": entry["publisher_key_id"],
        "dependencies": [f"{item['id']}{item['range']}" for item in entry["dependencies"]],
        # What answered each range at install time, not just what would have been acceptable:
        # `>=2.1.0` stays satisfied after somebody swaps 2.1.0 for 3.0.0 underneath, and the
        # declared range alone cannot say the pack was installed against something else.
        "dependency_resolution": [
            f"{item['id']}{item['range']} -> "
            f"{item['version'] or 'unresolved'} [{item['tier'] or '-'}]"
            for item in entry["dependency_resolution"]
        ],
        "assets": {kind: len(paths) for kind, paths in sorted(manifest["assets"].items())
                   if paths},
        "eval_cases": len(entry["eval_case_hashes"]),
        # Absent rather than empty when the pack declares none: `{}` here would read as "this
        # pack says it is about nothing", which is a different claim from "this pack does not
        # say what it is about".
        **({"knowledge": manifest["knowledge"]} if "knowledge" in manifest else {}),
    }


def _scope_matches(declared: str, requested: str) -> bool:
    """A request for a bare dimension matches every value under it; a valued request is exact.

    `--scope product` finding `product:northwind-one` is the case that makes this worth spelling
    out: somebody narrowing a question to "the product, whichever one" should not have to know
    the product's slug to do it. The reverse does not hold — `--scope product:northwind-one` must
    not match a pack that only claims `product` in general, or a per-product answer would be
    sourced from something that never claimed to be about that product.
    """
    return declared == requested or declared.startswith(f"{requested}:")


def _documents(project: pathlib.Path, entry: dict, manifest: dict) -> list[dict]:
    """The knowledge material this pack carries, addressed so a caller can read and cite it.

    Two kinds, and they differ in a way that matters to a citation. A `wiki` is resolved by
    name across the tier order, so a project pack's `backup-policy` shadows a user pack's:
    listing this pack's copy without saying which one wins would produce a citation pointing
    at a document that is not the one in force, which is a worse failure than no citation.
    A `resource` is addressed inside its own pack and nothing can shadow it.

    Addressed as `pack://<tier>/<id>/<relative>` rather than as a filesystem path, per the
    rule `ResolvedPack` states: the path is an internal handle, and a projection anybody else
    consumes gets the stable URI.
    """
    documents: list[dict] = []
    for kind in ("wiki", "resource"):
        for item in manifest["assets"].get(kind, []):
            uri = f"pack://{entry['scope']}/{entry['id']}/{item}"
            name = _asset_name(kind, item)
            if name is None:   # `resource`: per-pack, never name-resolved, never shadowed
                documents.append({"kind": kind, "name": pathlib.PurePosixPath(item).name,
                                  "uri": uri, "effective": True,
                                  "provided_by": entry["id"]})
                continue
            winner = resolve_all(kind, name, project=project)
            top = winner[0] if winner else None
            documents.append({
                "kind": kind, "name": name, "uri": uri,
                "effective": bool(top is not None and top.pack_id == entry["id"]),
                "provided_by": top.pack_id if top is not None else None,
            })
    return documents


def knowledge_rows(project: pathlib.Path, root: pathlib.Path, *,
                   topics: tuple[str, ...] = (), scopes: tuple[str, ...] = (),
                   scope: str | None = None) -> dict:
    """Which installed packs declare knowledge for this question — and whether that settles it.

    This selects; it does not answer, and it deliberately does not choose. When candidates
    turn out to span more than one scope, "which scope did you mean" is a question about the
    asker's intent, and no amount of reading the packs can recover it: the issue's own example
    is a checklist asking "do you take backups?", which is a different answer for the company,
    for one product, and for the infrastructure underneath both. What this returns instead is
    the fact that the question is open and the exact set of alternatives, so the layer that
    can hold a conversation has something to ask rather than something to guess.

    `ambiguous` is about scopes, not about counts. Two company packs both matching is not an
    ambiguity — they are two sources for one scope and an answer should rest on both. One
    company pack and one product pack matching is, because merging them produces an answer to
    a question nobody asked.
    """
    candidates: list[dict] = []
    for entry in _entries(root, scope=scope):
        try:
            manifest = validate_pack(root / entry["path"])
        except PackError:
            # An unreadable pack is `?` in `list` and skipped here on purpose: this feeds an
            # answer, and half-reading a pack whose contents failed validation would put
            # unverified material behind a citation.
            continue
        knowledge = manifest.get("knowledge")
        if not knowledge:
            continue
        matched_scopes = sorted(
            declared for declared in knowledge["scope"]
            if not scopes or any(_scope_matches(declared, want) for want in scopes)
        )
        matched_topics = sorted(set(knowledge["topics"]) & set(topics)) if topics else []
        if not matched_scopes or (topics and not matched_topics):
            continue
        candidates.append({
            "id": entry["id"], "version": entry["version"], "type": manifest["type"],
            "scope": knowledge["scope"], "matched_scope": matched_scopes,
            "topics": knowledge["topics"], "matched_topics": matched_topics,
            "owner": knowledge["owner"], "evidence": knowledge["evidence"],
            "reviewed_at": knowledge["reviewed_at"],
            "documents": _documents(project, entry, manifest),
        })
    distinct = sorted({scope for row in candidates for scope in row["matched_scope"]})
    return {
        "query": {"topics": sorted(topics), "scopes": sorted(scopes)},
        "candidates": candidates,
        "scopes": distinct,
        "ambiguous": len(distinct) > 1,
    }


def explain(project: pathlib.Path, root: pathlib.Path, pack_id: str) -> list[dict]:
    """What this pack actually contributes at runtime, and what wins where it does not.

    Separate from `info` on purpose: `info` answers "what is this and where is it from",
    which the lock knows, while this answers "does any of it reach a prompt", which only the
    tier resolver knows. A pack can be installed, valid, and entirely shadowed — that is the
    state a person is trying to find when they ask why an override did nothing.
    """
    manifest = validate_pack(root / _entry(root, pack_id)["path"])
    rows: list[dict] = []
    for kind, paths in sorted(manifest["assets"].items()):
        for item in paths:
            name = _asset_name(kind, item)
            if name is None:
                continue
            winner = resolve_all(kind, name, project=project)
            top = winner[0] if winner else None
            rows.append({
                "kind": kind, "name": name,
                "effective": bool(top is not None and top.pack_id == pack_id),
                "provided_by": top.pack_id if top is not None else None,
                "tier": top.tier if top is not None else None,
            })
    return rows


def _asset_name(kind: str, item: str) -> str | None:
    from .model import ASSET_DIRS, PROMPT_KINDS

    if kind not in PROMPT_KINDS:
        return None
    prefix = ASSET_DIRS[kind]
    relative = item[len(prefix) + 1:]
    return relative.rsplit(".", 1)[0]


def outdated(project: pathlib.Path, root: pathlib.Path) -> list[dict]:
    """For each git-pinned pack, the newest version its source offers.

    This is the one reporting command that talks to the network, one round trip per pack. A
    source that cannot be read is reported as a reason on that row rather than raising: the
    answer for the other packs is still worth having, and a single unreachable remote should
    not hide the rest of the inventory.
    """
    declared = read_sources(project)
    rows: list[dict] = []
    for entry in _entries(root):
        source = entry["source"]
        if source["type"] != "git":
            continue
        source_id = source["source_id"]
        row = {"id": entry["id"], "current": entry["version"], "latest": None,
               "reason": "ok"}
        if source_id not in declared:
            row["reason"] = "source-undeclared"
            rows.append(row)
            continue
        pack = source["path"].split(":", 1)[1].split("@", 1)[0]
        try:
            versions = available_versions(declared[source_id], pack)
        except PackError as error:
            row["reason"] = getattr(error, "reason", "invalid-pack")
            rows.append(row)
            continue
        newer = [item for item in versions if _order(item) > _order(entry["version"])]
        row["latest"] = max(versions, key=_order) if versions else None
        row["reason"] = "outdated" if newer else "ok"
        rows.append(row)
    return rows


def available_versions(source: dict, pack: str) -> list[str]:
    """The versions a source publishes as `vX.Y.Z` tags.

    Only exact three-part tags count. A pre-release or a hand-made tag is not something this
    should recommend upgrading to on its own, and quietly including one would make
    `outdated` push people onto versions their author did not release.
    """
    url = resolve_url(source, pack)
    result = _git(["ls-remote", "--tags", "--refs", url])
    if result.returncode != 0:
        from .sources import _classify  # noqa: PLC2701 - shared failure classification
        raise _classify(result.stderr, url=url)
    versions = []
    for line in result.stdout.splitlines():
        _sha, _tab, ref = line.partition("\t")
        match = _TAG.match(ref.strip())
        if match is not None:
            versions.append(match.group("version"))
    return sorted(set(versions), key=_order)


def _order(version: str) -> tuple[int, int, int]:
    parts = version.split("-", 1)[0].split(".")
    return tuple(int(part) for part in parts[:3])  # type: ignore[return-value]
