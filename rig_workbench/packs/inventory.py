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


def _entries(root: pathlib.Path) -> list[dict]:
    return sorted(read_lock(root)["packs"], key=lambda item: item["id"])


def _entry(root: pathlib.Path, pack_id: str) -> dict:
    for item in _entries(root):
        if item["id"] == pack_id:
            return item
    raise PackError(f"pack is not owned by this scope lock: {pack_id}")


def _origin(entry: dict) -> str:
    """How this pack got here, in one field, without ever printing a URL."""
    source = entry["source"]
    if source["type"] == "git":
        return f"{source['path']} @{source['revision'][:12]}"
    return f"{source['type']}:{source['path']}"


def list_rows(root: pathlib.Path) -> list[dict]:
    return [
        {
            "id": entry["id"], "version": entry["version"],
            "type": _installed_type(root, entry), "kind": entry["kind"],
            "origin": _origin(entry), "verification": entry["verification_status"],
        }
        for entry in _entries(root)
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


def info(root: pathlib.Path, pack_id: str) -> dict:
    """Everything the lock and the installed manifest say about one pack."""
    entry = _entry(root, pack_id)
    manifest = validate_pack(root / entry["path"])
    source = entry["source"]
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
