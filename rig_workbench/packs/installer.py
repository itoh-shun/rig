from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass

from rig_workbench.eval.gate import quality_result_failures
from rig_workbench.eval.compare import validate_result

from .lock import (lock_path, make_entry, read_lock, replace_entry, tree_hash,
                   make_source, resolve_dependencies, validate_lock_root, write_lock)
from .manifest import read_json_yaml
from .model import PROMPT_KINDS, PackError, UnverifiedSignature
from .sources import fetch_revision, parse_spec, read_sources, resolve_revision
from .resolver import pack_roots
from .validation import validate_pack, validate_tiered_collection

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_COMPRESSION_RATIO = 200
@dataclass(frozen=True)
class InstallResult:
    path: pathlib.Path
    manifest: dict
    verification_status: str


def _resolve_source(source: pathlib.Path | str) -> tuple[pathlib.Path, str]:
    """Resolve a local source or an allowlisted packaged domain alias."""
    source_text = str(source)
    if source_text.startswith(("domain:", "official:")):
        from .catalog import resolve_builtin_alias
        resolved, _manifest = resolve_builtin_alias(source_text)
        return resolved, source_text
    if source_text.casefold().startswith(("https://", "http://")):
        raise PackError("URL pack sources are unsupported; use a local directory, zip, or tar")
    resolved = pathlib.Path(source).expanduser().resolve()
    return resolved, str(resolved)


def _absolute_lexical(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(path.expanduser()))


def _reject_symlink_components(path: pathlib.Path, *, base: pathlib.Path | None = None) -> None:
    """Reject existing or broken symlinks before canonicalization or directory creation."""
    target = _absolute_lexical(path)
    anchor = _absolute_lexical(base) if base is not None else pathlib.Path(target.anchor)
    if not target.is_relative_to(anchor):
        raise PackError("pack root escapes its authoritative base")
    cursor = anchor
    if cursor.is_symlink():
        raise PackError("pack root must not traverse a symlink")
    for part in target.relative_to(anchor).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PackError("pack root must not traverse a symlink")


def scope_root(scope: str, *, project: pathlib.Path, root: pathlib.Path | None = None) -> pathlib.Path:
    if scope not in {"project", "user", "org"}:
        raise PackError("pack scope must be project, user, or org")
    roots = dict(pack_roots(project))
    if scope == "org" and "org" not in roots:
        raise PackError("org pack scope requires configured RIG_ORG_HOME")
    expected = _absolute_lexical(roots[scope])
    supplied = _absolute_lexical(root) if root is not None else expected
    if scope == "project":
        project_root = project.resolve()
        if not supplied.is_relative_to(project_root):
            raise PackError("project pack root must remain inside the project")
        _reject_symlink_components(supplied, base=project_root)
        foreign = {
            _absolute_lexical(path) for tier, path in roots.items() if tier in {"user", "org"}
        }
        if supplied in foreign:
            raise PackError("project pack root must not alias another pack tier")
    else:
        if supplied != expected:
            raise PackError(f"explicit {scope} pack root must match the configured tier root")
        _reject_symlink_components(supplied)
    return supplied.resolve(strict=False)


def _safe_member(name: str) -> pathlib.PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise PackError("archive member path is unsafe")
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise PackError(f"archive traversal is forbidden: {name}")
    return path


def _copy_directory(source: pathlib.Path, destination: pathlib.Path) -> None:
    destination.mkdir()
    count = 0
    total = 0
    try:
        for item in sorted(source.rglob("*")):
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise PackError("pack source contains too many entries")
            metadata = item.lstat()
            rel = item.relative_to(source)
            target = destination / rel
            if stat.S_ISLNK(metadata.st_mode):
                raise PackError(f"source symlink is forbidden: {rel}")
            if stat.S_ISDIR(metadata.st_mode):
                target.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
                if metadata.st_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise PackError("pack source exceeds size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                with item.open("rb") as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
            else:
                raise PackError(f"source special file is forbidden: {rel}")
    except OSError as exc:
        raise PackError(f"cannot stage pack directory: {exc}") from exc


def _extract_zip(source: pathlib.Path, destination: pathlib.Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise PackError("archive contains too many entries")
            total = 0
            for member in members:
                rel = _safe_member(member.filename)
                mode = member.external_attr >> 16
                if member.flag_bits & 0x1:
                    raise PackError("encrypted archive members are forbidden")
                if stat.S_ISLNK(mode) or (mode and not member.is_dir() and not stat.S_ISREG(mode)):
                    raise PackError(f"archive symlink/device is forbidden: {member.filename}")
                total += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise PackError("archive exceeds uncompressed size limit")
                if (member.file_size > 0 and member.compress_size == 0) or (
                    member.compress_size > 0
                    and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise PackError("archive compression ratio exceeds limit")
                target = destination.joinpath(*rel.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
    except PackError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackError(f"invalid zip pack source: {exc}") from exc


def _extract_tar(source: pathlib.Path, destination: pathlib.Path) -> None:
    try:
        with tarfile.open(source, mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise PackError("archive contains too many entries")
            total = 0
            for member in members:
                rel = _safe_member(member.name)
                if not (member.isdir() or member.isfile()) or member.issparse():
                    raise PackError(f"archive symlink/device is forbidden: {member.name}")
                total += member.size
                if member.size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
                    raise PackError("archive exceeds uncompressed size limit")
                target = destination.joinpath(*rel.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                reader = archive.extractfile(member)
                if reader is None:
                    raise PackError(f"cannot read archive member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)
            compressed_size = source.stat().st_size
            if ((total > 0 and compressed_size == 0)
                    or (compressed_size > 0 and total / compressed_size > MAX_COMPRESSION_RATIO)):
                raise PackError("archive compression ratio exceeds limit")
    except PackError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PackError(f"invalid tar pack source: {exc}") from exc


def _source_to_staging(source: pathlib.Path, content: pathlib.Path) -> tuple[str, str]:
    if source.is_dir():
        source_hash = tree_hash(source)
        _copy_directory(source, content)
        return "directory", source_hash
    if not source.is_file():
        raise PackError(f"pack source does not exist: {source}")
    try:
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise PackError("archive source exceeds compressed size limit")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise PackError(f"cannot read pack source: {exc}") from exc
    content.mkdir()
    if zipfile.is_zipfile(source):
        _extract_zip(source, content)
        return "zip", source_hash
    if tarfile.is_tarfile(source):
        _extract_tar(source, content)
        return "tar", source_hash
    raise PackError("unsupported pack source; use a local directory, zip, or tar (URL unsupported)")


def _pack_root(content: pathlib.Path) -> pathlib.Path:
    if (content / "pack.yaml").is_file():
        return content
    children = [item for item in content.iterdir() if item.is_dir()]
    files = [item for item in content.iterdir() if item.is_file()]
    if len(children) == 1 and not files and (children[0] / "pack.yaml").is_file():
        return children[0]
    raise PackError("archive must contain one pack root")


def local_quality_status(
    pack: pathlib.Path, manifest: dict, *, publisher_verified: bool = False,
) -> str:
    """Evaluate local promotion quality; this is not publisher/install trust."""
    if not any(manifest["assets"][kind] for kind in PROMPT_KINDS):
        return "verified-local"
    cases: dict[str, dict] = {}
    for rel in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(pack / rel)
        cases[case["id"]] = case
    evidence: dict[str, list[dict]] = {case_id: [] for case_id in cases}
    for rel in manifest["assets"]["eval-result"]:
        _raw, result = read_json_yaml(pack / rel)
        try:
            validate_result(result, verify_attestation=not publisher_verified)
        except Exception as exc:
            raise PackError(f"invalid attested pack evaluation result: {rel}: {exc}") from exc
        if result.get("case_id") not in evidence:
            raise PackError(f"evaluation result is not bound to an owned case: {rel}")
        if result.get("provider") in {"mock", "command"} or result.get(
            "judge_provider"
        ) in {"mock", "command"}:
            return "unverified"
        evidence[result["case_id"]].append(result)
    for case_id, case in cases.items():
        current = [result for result in evidence[case_id] if result["phase"] == "current"]
        if len(current) != 1:
            return "unverified"
        try:
            failures = quality_result_failures(
                current[0], case, verify_attestation=not publisher_verified,
            )
        except Exception as exc:
            raise PackError(
                f"invalid attested pack evaluation result for {case_id}: {exc}"
            ) from exc
        if failures:
            return "unverified"
    return "verified-local"


def verification_status(pack: pathlib.Path, manifest: dict) -> tuple[str, dict | None]:
    """Return publisher trust independently from local structural/quality evidence."""
    from .publisher import verify_publisher_signature

    publisher = verify_publisher_signature(pack, manifest)
    quality = local_quality_status(pack, manifest, publisher_verified=publisher is not None)
    if publisher is not None:
        if quality != "verified-local":
            raise PackError(
                "publisher-signed pack has invalid, mock, mismatched, or non-green evidence"
            )
        return "verified-publisher", publisher
    return quality, None


def _collection_entries(project: pathlib.Path, staging_pack: pathlib.Path,
                        scope: str, destination_root: pathlib.Path,
                        *, replacing: pathlib.Path | None = None) -> list[tuple[str, pathlib.Path]]:
    """Every pack the runtime would see, with the staged one standing in for its own copy.

    `replacing` is the directory the staged pack is about to take over from. An update has to
    exclude it: leaving it in would present the same pack twice and the collection would
    refuse itself as a duplicate id rather than judging the new version against its
    neighbours.
    """
    excluded = replacing.resolve() if replacing is not None else None
    entries: list[tuple[str, pathlib.Path]] = []
    seen_roots: set[pathlib.Path] = set()
    for tier, root in pack_roots(project):
        resolved = root.resolve()
        seen_roots.add(resolved)
        if root.is_dir():
            entries.extend((tier, item) for item in sorted(root.iterdir()) if item.is_dir()
                           and not item.name.startswith((".", "_"))
                           and item.resolve() != excluded)
    if destination_root.resolve() not in seen_roots and destination_root.is_dir():
        entries.extend((scope, item) for item in sorted(destination_root.iterdir()) if item.is_dir()
                       and not item.name.startswith((".", "_"))
                       and item.resolve() != excluded)
    entries.append((scope, staging_pack))
    return entries


def install_pack(
    source: pathlib.Path | str, *, scope: str, project: pathlib.Path | str,
    root: pathlib.Path | str | None = None, allow_unverified: bool = False,
) -> InstallResult:
    project_path = pathlib.Path(project).resolve()
    # A named-source spec (`product:joypla@1.4.0`) is resolved to a commit before anything is
    # staged: `resolve_revision` is the only step that talks to the remote's refs, and doing
    # it up front means a source that cannot be read fails before a staging directory exists.
    spec = parse_spec(str(source))
    plan: dict | None = None
    source_path: pathlib.Path | None = None
    if spec is None:
        source_path, source_label = _resolve_source(source)
    else:
        source_id, pack_name, version = spec
        declared = read_sources(project_path)
        if source_id not in declared:
            raise PackError(
                f"source `{source_id}` is not declared in .rig/sources.json "
                f"(declared: {', '.join(sorted(declared)) or 'none'})")
        plan = {
            "source_id": source_id, "source": declared[source_id], "pack": pack_name,
            "version": version,
            "revision": resolve_revision(declared[source_id], pack_name, version),
        }
        source_label = f"{source_id}:{pack_name}@{version}"
    if allow_unverified and scope != "project":
        raise PackError("--allow-unverified is restricted to project scope")
    destination_root = scope_root(
        scope, project=project_path,
        root=pathlib.Path(root) if root is not None else None,
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    validate_lock_root(destination_root, expected_scope=scope)
    unmanaged = [item.name for item in destination_root.iterdir() if item.is_dir()
                 and not item.name.startswith(".pack-")]
    if unmanaged and not lock_path(destination_root).exists():
        raise PackError(
            f"pack root contains unmanaged packs; migrate/remove before install: {sorted(unmanaged)}"
        )
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".pack-stage-", dir=destination_root))
    installed: pathlib.Path | None = None
    try:
        content = staging / "content"
        if plan is None:
            assert source_path is not None
            source_type, source_hash = _source_to_staging(source_path, content)
            source_block = make_source(source_type, source_label, source_hash)
        else:
            fetch_revision(plan["source"], plan["pack"], plan["version"], plan["revision"],
                           content)
            source_block = make_source(
                "git", source_label, tree_hash(content),
                source_id=plan["source_id"], revision=plan["revision"])
        pack = _pack_root(content)
        manifest = validate_pack(pack)
        destination = destination_root / manifest["id"]
        if destination.exists():
            raise PackError(f"pack target already exists: {destination}")
        records = validate_tiered_collection(
            _collection_entries(project_path, pack, scope, destination_root,
                                replacing=destination)
        )
        status, publisher = verification_status(pack, manifest)
        if status != "verified-publisher" and not allow_unverified:
            raise UnverifiedSignature(
                "unsigned packs require project --allow-unverified; local evaluation quality "
                "does not establish publisher trust"
            )
        lock = read_lock(destination_root)
        if any(item["id"] == manifest["id"] for item in lock["packs"]):
            raise PackError(f"pack is already lock-owned: {manifest['id']}")
        entry = make_entry(
            pack, manifest, scope=scope, source=source_block,
            verification_status=status,
            dependency_resolution=resolve_dependencies(manifest, records),
            publisher_key_id=publisher["key_id"] if publisher else None,
            signed_digest=publisher["signed_digest"] if publisher else None,
        )
        os.replace(pack, destination)
        installed = destination
        try:
            write_lock(destination_root, replace_entry(lock, entry))
        except Exception:
            os.replace(destination, pack)
            installed = None
            raise
        return InstallResult(destination, manifest, status)
    except PackError:
        raise
    except OSError as exc:
        raise PackError(f"pack install transaction failed: {exc}") from exc
    finally:
        if installed is None or installed.exists():
            shutil.rmtree(staging, ignore_errors=True)


def update_pack(
    pack_id: str, *, to: str, scope: str, project: pathlib.Path | str,
    root: pathlib.Path | str | None = None, allow_unverified: bool = False,
) -> InstallResult:
    """Move a git-pinned pack to another version, in place.

    Not remove-then-install: that leaves a window where the pack is gone and a failure in the
    second half strands the project with neither version. The new content is staged and
    validated first, and the directory swap and the lock write are the last two steps, with
    the old directory kept until both have happened.

    Only a git-sourced pack can be updated. A pack installed from a local directory or an
    archive has no version to ask a source about — pointing this at one would mean guessing
    where the newer copy lives, and the honest answer is to install it again from wherever it
    actually came from.
    """
    project_path = pathlib.Path(project).resolve()
    destination_root = scope_root(
        scope, project=project_path,
        root=pathlib.Path(root) if root is not None else None,
    )
    lock = read_lock(destination_root)
    current = next((item for item in lock["packs"] if item["id"] == pack_id), None)
    if current is None:
        raise PackError(f"pack is not owned by this scope lock: {pack_id}")
    if current["source"]["type"] != "git":
        raise PackError(
            f"{pack_id} was installed from {current['source']['type']}, which has no version "
            f"to resolve; reinstall it from its source instead")
    if current["version"] == to:
        raise PackError(f"{pack_id} is already at {to}")
    source_id = current["source"]["source_id"]
    declared = read_sources(project_path)
    if source_id not in declared:
        raise PackError(
            f"source `{source_id}` is not declared in .rig/sources.json "
            f"(declared: {', '.join(sorted(declared)) or 'none'})")
    source = declared[source_id]
    name = current["source"]["path"].split(":", 1)[1].split("@", 1)[0]
    revision = resolve_revision(source, name, to)

    destination = destination_root / current["path"]
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".pack-stage-", dir=destination_root))
    retired = staging / "retired"
    swapped = False
    try:
        content = staging / "content"
        fetch_revision(source, name, to, revision, content)
        pack = _pack_root(content)
        manifest = validate_pack(pack)
        if manifest["id"] != pack_id:
            raise PackError(
                f"source served {manifest['id']} where {pack_id} was expected")
        if manifest["version"] != to:
            raise PackError(
                f"{name}@{to} contains version {manifest['version']}; the tag and the "
                f"manifest disagree")
        records = validate_tiered_collection(
            _collection_entries(project_path, pack, scope, destination_root,
                                replacing=destination)
        )
        status, publisher = verification_status(pack, manifest)
        if status != "verified-publisher" and not allow_unverified:
            raise UnverifiedSignature(
                "unsigned packs require project --allow-unverified; local evaluation quality "
                "does not establish publisher trust"
            )
        entry = make_entry(
            pack, manifest, scope=scope,
            source=make_source("git", f"{source_id}:{name}@{to}", tree_hash(pack),
                               source_id=source_id, revision=revision),
            verification_status=status,
            dependency_resolution=resolve_dependencies(manifest, records),
            publisher_key_id=publisher["key_id"] if publisher else None,
            signed_digest=publisher["signed_digest"] if publisher else None,
        )
        os.replace(destination, retired)
        swapped = True
        os.replace(pack, destination)
        try:
            write_lock(destination_root, replace_entry(lock, entry))
        except Exception:
            os.replace(destination, pack)
            os.replace(retired, destination)
            swapped = False
            raise
        return InstallResult(destination, manifest, status)
    except PackError:
        if swapped and not destination.exists():
            os.replace(retired, destination)
        raise
    except OSError as exc:
        if swapped and not destination.exists():
            os.replace(retired, destination)
        raise PackError(f"pack update transaction failed: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
