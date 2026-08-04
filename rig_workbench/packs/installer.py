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
                   validate_lock_root, write_lock)
from .manifest import read_json_yaml
from .model import PROMPT_KINDS, PackError
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


def scope_root(scope: str, *, project: pathlib.Path, root: pathlib.Path | None = None) -> pathlib.Path:
    if scope not in {"project", "user", "org"}:
        raise PackError("pack scope must be project, user, or org")
    if root is not None:
        return root.expanduser().resolve()
    roots = dict(pack_roots(project))
    if scope == "org" and "org" not in roots:
        raise PackError("org pack scope requires RIG_ORG_HOME or --root")
    return roots[scope].resolve()


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


def verification_status(pack: pathlib.Path, manifest: dict) -> str:
    if not any(manifest["assets"][kind] for kind in PROMPT_KINDS):
        return "verified"
    cases: dict[str, dict] = {}
    for rel in manifest["assets"]["eval-case"]:
        _raw, case = read_json_yaml(pack / rel)
        cases[case["id"]] = case
    evidence: dict[str, list[dict]] = {case_id: [] for case_id in cases}
    for rel in manifest["assets"]["eval-result"]:
        _raw, result = read_json_yaml(pack / rel)
        try:
            validate_result(result)
        except Exception as exc:
            raise PackError(f"invalid attested pack evaluation result: {rel}: {exc}") from exc
        if result.get("case_id") not in evidence:
            raise PackError(f"evaluation result is not bound to an owned case: {rel}")
        evidence[result["case_id"]].append(result)
    for case_id, case in cases.items():
        current = [result for result in evidence[case_id] if result["phase"] == "current"]
        if len(current) != 1:
            return "unverified"
        try:
            failures = quality_result_failures(current[0], case)
        except Exception as exc:
            raise PackError(
                f"invalid attested pack evaluation result for {case_id}: {exc}"
            ) from exc
        if failures:
            return "unverified"
    return "verified"


def _collection_entries(project: pathlib.Path, staging_pack: pathlib.Path,
                        scope: str, destination_root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    entries: list[tuple[str, pathlib.Path]] = []
    seen_roots: set[pathlib.Path] = set()
    for tier, root in pack_roots(project):
        resolved = root.resolve()
        seen_roots.add(resolved)
        if root.is_dir():
            entries.extend((tier, item) for item in sorted(root.iterdir()) if item.is_dir()
                           and not item.name.startswith((".", "_")))
    if destination_root.resolve() not in seen_roots and destination_root.is_dir():
        entries.extend((scope, item) for item in sorted(destination_root.iterdir()) if item.is_dir()
                       and not item.name.startswith((".", "_")))
    entries.append((scope, staging_pack))
    return entries


def install_pack(
    source: pathlib.Path | str, *, scope: str, project: pathlib.Path | str,
    root: pathlib.Path | str | None = None, allow_unverified: bool = False,
) -> InstallResult:
    project_path = pathlib.Path(project).resolve()
    source_path, source_label = _resolve_source(source)
    destination_root = scope_root(
        scope, project=project_path,
        root=pathlib.Path(root) if root is not None else None,
    )
    if allow_unverified and scope != "project":
        raise PackError("--allow-unverified is restricted to project scope")
    destination_root.mkdir(parents=True, exist_ok=True)
    validate_lock_root(destination_root)
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
        source_type, source_hash = _source_to_staging(source_path, content)
        pack = _pack_root(content)
        manifest = validate_pack(pack)
        destination = destination_root / manifest["id"]
        if destination.exists():
            raise PackError(f"pack target already exists: {destination}")
        validate_tiered_collection(
            _collection_entries(project_path, pack, scope, destination_root)
        )
        quality = verification_status(pack, manifest)
        if quality != "verified" and not allow_unverified:
            raise PackError(
                "prompt pack requires promoted cases and attested non-mock current green evidence; "
                "project installs may explicitly use --allow-unverified"
            )
        lock = read_lock(destination_root)
        if any(item["id"] == manifest["id"] for item in lock["packs"]):
            raise PackError(f"pack is already lock-owned: {manifest['id']}")
        entry = make_entry(
            pack, manifest, scope=scope, source_type=source_type,
            source_path=source_label, source_hash=source_hash,
            verification_status=quality,
        )
        os.replace(pack, destination)
        installed = destination
        try:
            write_lock(destination_root, replace_entry(lock, entry))
        except Exception:
            os.replace(destination, pack)
            installed = None
            raise
        return InstallResult(destination, manifest, quality)
    except PackError:
        raise
    except OSError as exc:
        raise PackError(f"pack install transaction failed: {exc}") from exc
    finally:
        if installed is None or installed.exists():
            shutil.rmtree(staging, ignore_errors=True)
