from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile
from typing import Any

from rig_workbench import __version__

from .manifest import PACK_ID, VERSION, canonical, digest
from .model import PackError
from .validation import validate_pack

LOCK_NAME = "pack.lock.json"
LOCK_SCHEMA_VERSION = 4


def tree_hash(root: pathlib.Path) -> str:
    checksum = hashlib.sha256()
    try:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.is_symlink():
                raise PackError(f"source symlink is forbidden: {path.relative_to(root)}")
            rel = path.relative_to(root).as_posix().encode("utf-8")
            checksum.update(len(rel).to_bytes(8, "big"))
            checksum.update(rel)
            data = path.read_bytes()
            checksum.update(len(data).to_bytes(8, "big"))
            checksum.update(data)
    except OSError as exc:
        raise PackError(f"cannot hash pack tree: {exc}") from exc
    return checksum.hexdigest()


def lock_path(root: pathlib.Path) -> pathlib.Path:
    return root / LOCK_NAME


def read_lock(root: pathlib.Path) -> dict[str, Any]:
    path = lock_path(root)
    if not path.exists():
        return {"pack_lock_schema_version": LOCK_SCHEMA_VERSION, "packs": []}
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"pack lock is unreadable: {exc}") from exc
    if raw != canonical(value):
        raise PackError("pack lock is not canonical JSON")
    if (not isinstance(value, dict)
            or set(value) != {"pack_lock_schema_version", "packs"}
            or type(value["pack_lock_schema_version"]) is not int
            or value["pack_lock_schema_version"] != LOCK_SCHEMA_VERSION
            or not isinstance(value["packs"], list)):
        raise PackError("pack lock schema is invalid")
    ids = [item.get("id") for item in value["packs"] if isinstance(item, dict)]
    if len(ids) != len(value["packs"]) or ids != sorted(ids) or len(ids) != len(set(ids)):
        raise PackError("pack lock entries must be sorted and unique")
    return value


def write_lock(root: pathlib.Path, value: dict[str, Any]) -> None:
    write_lock_bytes(root, canonical(value).encode("utf-8"))


def refuse_credentials(payload: bytes, *, where: str) -> None:
    """Refuse to persist anything credential-shaped.

    The rule that rig never stores a credential is enforced at the one place that writes,
    not by asking every caller to be careful. A caller can be careful and still be wrong —
    a source URL that carried userinfo, a path with a token in it, a future field nobody
    thought about — and a rule that depends on nobody making that mistake is a wish. The
    sensor is the same one `rig-wb wb scan-secrets` runs, so what the gate refuses and what
    the scanner reports cannot drift apart.
    """
    from rig_workbench.workbench.secrets import scan_line

    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        return
    for number, line in enumerate(text.splitlines(), 1):
        findings = scan_line(line, where, number, skip_entropy=True)
        if findings:
            kinds = sorted({str(item.get("kind")) for item in findings})
            # The finding itself is not echoed: reporting a secret to complain about it
            # writes it somewhere new.
            raise PackError(
                f"refusing to write {where}: it would persist a credential ({', '.join(kinds)})")


def write_lock_bytes(root: pathlib.Path, payload: bytes) -> None:
    """Atomically replace the lock with exact bytes (used by transaction rollback)."""
    refuse_credentials(payload, where=LOCK_NAME)
    root.mkdir(parents=True, exist_ok=True)
    temporary: pathlib.Path | None = None
    descriptor = -1
    try:
        descriptor, name = tempfile.mkstemp(prefix=".pack-lock.", dir=root)
        temporary = pathlib.Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, lock_path(root))
        temporary = None
        try:
            directory = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # The atomic replacement has committed. A directory-fsync failure
            # must not be reported as a rollback-safe pre-commit failure.
            pass
    except OSError as exc:
        raise PackError(f"cannot update pack lock: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def make_source(source_type: str, source_path: str, source_hash: str, *,
                source_id: str | None = None, revision: str | None = None) -> dict[str, Any]:
    """The `source` block of a lock entry.

    A git source records the source's *name* and the commit, never the URL: the URL lives in
    `.rig/sources.json`, so a lock cannot carry an embedded credential no matter how the
    remote was addressed, and moving a pack between forges does not rewrite every lock that
    installed it.
    """
    source: dict[str, Any] = {"type": source_type, "path": source_path, "sha256": source_hash}
    if source_type == "git":
        source["source_id"] = source_id
        source["revision"] = revision
    return source


def resolve_dependencies(manifest: dict, records: list[tuple[str, Any, dict]]) -> list[dict]:
    """What actually satisfied each declared dependency, at install time.

    The entry already copies the declared `dependencies` — ranges, which say what would be
    acceptable. That is not a resolution: `>=2.1.0` is still satisfied after somebody swaps
    2.1.0 for 3.0.0 underneath, and the lock cannot tell that the pack was installed against
    something else. Recording the version and tier that answered the range is what makes the
    resolution reproducible rather than merely permitted.
    """
    installed = {item["id"]: (tier, item["version"]) for tier, _path, item in records}
    resolution = []
    for dependency in manifest["dependencies"]:
        tier, version = installed.get(dependency["id"], (None, None))
        resolution.append({
            "id": dependency["id"], "range": dependency["range"],
            "version": version, "tier": tier,
        })
    return sorted(resolution, key=lambda item: item["id"])


def make_entry(
    pack: pathlib.Path, manifest: dict, *, scope: str, source: dict[str, Any],
    verification_status: str, dependency_resolution: list[dict] | None = None,
    publisher_key_id: str | None, signed_digest: str | None,
    installed_at: dt.datetime | None = None,
) -> dict[str, Any]:
    timestamp = (installed_at or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    return {
        "id": manifest["id"], "version": manifest["version"], "kind": manifest["kind"],
        "scope": scope, "path": manifest["id"],
        "source": source,
        "manifest_sha256": digest(pack / "pack.yaml"),
        "asset_hashes": dict(sorted(manifest["hashes"].items())),
        "engine_version": __version__, "installed_at": timestamp,
        "dependencies": manifest["dependencies"],
        "dependency_resolution": dependency_resolution or [],
        "eval_case_hashes": {
            item: manifest["hashes"][item] for item in manifest["assets"]["eval-case"]
        },
        "verification_status": verification_status,
        "publisher_key_id": publisher_key_id,
        "signed_digest": signed_digest,
    }


def replace_entry(lock: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    packs = [item for item in lock["packs"] if item["id"] != entry["id"]]
    packs.append(entry)
    return {"pack_lock_schema_version": LOCK_SCHEMA_VERSION,
            "packs": sorted(packs, key=lambda item: item["id"])}


def validate_lock_root(
    root: pathlib.Path, *, expected_scope: str | None = None,
) -> list[dict[str, Any]]:
    if not lock_path(root).exists():
        return []
    lock = read_lock(root)
    actual = {
        item.name for item in root.iterdir()
        if item.is_dir() and not item.name.startswith(".pack-")
    }
    expected = {item.get("path") for item in lock["packs"] if isinstance(item, dict)}
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise PackError(
            f"pack lock drift: directory ownership mismatch "
            f"(unowned={extra}, missing={missing})"
        )
    required = {
        "id", "version", "kind", "scope", "path", "source", "manifest_sha256",
        "asset_hashes", "engine_version", "installed_at", "dependencies",
        "eval_case_hashes", "verification_status",
        "publisher_key_id", "signed_digest", "dependency_resolution",
    }
    for entry in lock["packs"]:
        if set(entry) != required or entry["path"] != entry["id"]:
            raise PackError(f"pack lock drift: invalid entry for {entry.get('id', '?')}")
        if expected_scope is not None and entry.get("scope") != expected_scope:
            raise PackError(
                f"pack lock drift: scope mismatch for {entry.get('id', '?')}"
            )
        if (not isinstance(entry["id"], str) or not PACK_ID.fullmatch(entry["id"])
                or not isinstance(entry["version"], str)
                or not VERSION.fullmatch(entry["version"])
                or entry["kind"] not in {"core", "official", "domain", "project"}
                or entry["scope"] not in {"project", "user", "org"}
                or not isinstance(entry["engine_version"], str)
                or entry["verification_status"] not in {
                    "verified-publisher", "verified-local", "unverified",
                }
                or not isinstance(entry["dependencies"], list)
                or not isinstance(entry["dependency_resolution"], list)
                or len(entry["dependency_resolution"]) != len(entry["dependencies"])
                or any(not isinstance(item, dict)
                       or set(item) != {"id", "range", "version", "tier"}
                       or not isinstance(item["id"], str)
                       or not isinstance(item["range"], str)
                       or not (item["version"] is None or isinstance(item["version"], str))
                       or not (item["tier"] is None or isinstance(item["tier"], str))
                       for item in entry["dependency_resolution"])
                or {item["id"] for item in entry["dependency_resolution"]}
                != {item["id"] for item in entry["dependencies"]}
                or not isinstance(entry["asset_hashes"], dict)
                or not isinstance(entry["eval_case_hashes"], dict)
                or not isinstance(entry["manifest_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["manifest_sha256"])
                or any(not isinstance(key, str) or not isinstance(value, str)
                       or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for mapping in (entry["asset_hashes"], entry["eval_case_hashes"])
                       for key, value in mapping.items())):
            raise PackError(f"pack lock drift: invalid metadata for {entry['id']}")
        publisher_fields = (entry["publisher_key_id"], entry["signed_digest"])
        if entry["verification_status"] == "verified-publisher":
            if (not isinstance(publisher_fields[0], str) or not publisher_fields[0]
                    or not isinstance(publisher_fields[1], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", publisher_fields[1])):
                raise PackError(f"pack lock drift: invalid publisher trust for {entry['id']}")
        elif publisher_fields != (None, None):
            raise PackError(f"pack lock drift: unexpected publisher trust for {entry['id']}")
        try:
            installed = dt.datetime.fromisoformat(
                str(entry["installed_at"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise PackError(f"pack lock drift: invalid timestamp for {entry['id']}") from exc
        if installed.tzinfo is None:
            raise PackError(f"pack lock drift: invalid timestamp for {entry['id']}")
        source = entry["source"]
        local_shape = {"type", "path", "sha256"}
        git_shape = local_shape | {"source_id", "revision"}
        if (not isinstance(source, dict)
                or set(source) != (git_shape if source.get("type") == "git" else local_shape)
                or source["type"] not in {"directory", "zip", "tar", "git"}
                or not isinstance(source["path"], str) or not source["path"]
                or not isinstance(source["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])):
            raise PackError(f"pack lock drift: invalid source for {entry['id']}")
        if source["type"] == "git" and (
                not isinstance(source["source_id"], str) or not source["source_id"]
                or not isinstance(source["revision"], str)
                or not re.fullmatch(r"[0-9a-f]{40}", source["revision"])):
            raise PackError(f"pack lock drift: invalid git source for {entry['id']}")
        pack = root / entry["path"]
        if not pack.is_dir():
            raise PackError(f"pack lock drift: missing pack {entry['id']}")
        manifest = validate_pack(pack)
        if (manifest["id"] != entry["id"] or manifest["version"] != entry["version"]
                or manifest["kind"] != entry["kind"]
                or manifest["dependencies"] != entry["dependencies"]):
            raise PackError(f"pack lock drift: identity changed for {entry['id']}")
        if digest(pack / "pack.yaml") != entry["manifest_sha256"]:
            raise PackError(f"pack lock drift: manifest changed for {entry['id']}")
        if manifest["hashes"] != entry["asset_hashes"]:
            raise PackError(f"pack lock drift: asset hashes changed for {entry['id']}")
        expected_cases = {
            item: manifest["hashes"][item] for item in manifest["assets"]["eval-case"]
        }
        if expected_cases != entry["eval_case_hashes"]:
            raise PackError(f"pack lock drift: eval cases changed for {entry['id']}")
        if entry["verification_status"] == "verified-publisher":
            from .publisher import verify_publisher_signature
            verified = verify_publisher_signature(pack, manifest)
            if (verified is None or verified["key_id"] != entry["publisher_key_id"]
                    or verified["signed_digest"] != entry["signed_digest"]):
                raise PackError(f"pack lock drift: publisher signature changed for {entry['id']}")
    return lock["packs"]
