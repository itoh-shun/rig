"""Detached Ed25519 publisher signatures for immutable Rig pack releases."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.resources
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
from typing import Any

from rig_workbench import __version__

from .manifest import canonical, digest, read_json_yaml
from .model import PROMPT_KINDS, PackError

SIGNATURE_NAME = "pack.sig.json"
SIGNATURE_SCHEMA_VERSION = 1
TRUST_ROOTS_SCHEMA_VERSION = 1


def _lexical_path(value: pathlib.Path | str) -> pathlib.Path:
    return pathlib.Path(os.path.abspath(pathlib.Path(value).expanduser()))


def _reject_symlink_path(path: pathlib.Path, *, include_target: bool) -> None:
    cursor = pathlib.Path(path.anchor)
    parts = path.parts[1:] if path.is_absolute() else path.parts
    limit = len(parts) if include_target else max(0, len(parts) - 1)
    for part in parts[:limit]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PackError("publisher key paths must not traverse symlinks")


def _git_top_level(start: pathlib.Path | str) -> pathlib.Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=_lexical_path(start),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackError("publisher key generation requires a git repository") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise PackError("publisher key generation requires a git repository")
    return pathlib.Path(completed.stdout.strip()).resolve(strict=True)


def _open_stable_directory(path: pathlib.Path) -> int:
    """Open an absolute directory by descriptor-walking every component."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise PackError("publisher key parent is not a directory")
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise PackError("publisher key path changed during descriptor validation") from exc
    except Exception:
        os.close(descriptor)
        raise


def generate_publisher_key(
    *, private_key_path: pathlib.Path | str, trust_roots_path: pathlib.Path | str,
    key_id: str, signer: str, source_repository: pathlib.Path | str,
    valid_from: dt.datetime | None = None, valid_until: dt.datetime | None = None,
) -> dict:
    """Generate one release key and atomically register only its public half."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise PackError("publisher key generation requires cryptography>=41") from exc
    if (not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_id)
            or not isinstance(signer, str) or not signer.strip()):
        raise PackError("publisher signer/key_id is invalid")
    private_path = _lexical_path(private_key_path)
    roots_path = _lexical_path(trust_roots_path)
    repository = _git_top_level(source_repository)
    _reject_symlink_path(private_path, include_target=True)
    _reject_symlink_path(roots_path, include_target=True)
    if private_path.is_relative_to(repository):
        raise PackError("publisher private key must be outside the source repository")
    if not roots_path.is_relative_to(repository):
        raise PackError("publisher trust roots must be inside the source repository")
    if private_path.exists():
        raise PackError("publisher private key target already exists")
    if not private_path.parent.is_dir():
        raise PackError("publisher private key parent must already exist")
    private_parent_fd = _open_stable_directory(private_path.parent)
    try:
        roots_parent_fd = _open_stable_directory(roots_path.parent)
    except Exception:
        os.close(private_parent_fd)
        raise
    try:
        if stat.S_IMODE(os.fstat(private_parent_fd).st_mode) & 0o022:
            raise PackError("publisher private key parent must not be group/world writable")
        if stat.S_IMODE(os.fstat(roots_parent_fd).st_mode) & 0o022:
            raise PackError("publisher trust-roots parent must not be group/world writable")
        roots_fd = os.open(
            roots_path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=roots_parent_fd,
        )
        try:
            roots_stat = os.fstat(roots_fd)
            if (not stat.S_ISREG(roots_stat.st_mode)
                    or stat.S_IMODE(roots_stat.st_mode) & 0o022):
                raise PackError(
                    "trust-roots file must be regular, non-symlink, and not writable by group/world"
                )
            with os.fdopen(os.dup(roots_fd), "r", encoding="utf-8") as stream:
                roots = json.load(stream)
        finally:
            os.close(roots_fd)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        os.close(private_parent_fd)
        os.close(roots_parent_fd)
        raise PackError(f"publisher trust roots are unreadable: {exc}") from exc
    try:
        _validate_trust_roots(roots)
        if any(item["key_id"] == key_id for item in roots["keys"]):
            raise PackError("publisher key_id is already registered")
        start = valid_from or dt.datetime.now(dt.timezone.utc)
        if start.tzinfo is None or (valid_until is not None and valid_until.tzinfo is None):
            raise PackError("publisher key validity timestamps must include timezone")
        start_text = start.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
        end_text = (valid_until.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
                    if valid_until is not None else None)
        if valid_until is not None and valid_until <= start:
            raise PackError("publisher key validity interval is invalid")
        private = Ed25519PrivateKey.generate()
        private_bytes = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public_bytes = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        root = {
            "key_id": key_id, "signer": signer.strip(),
            "public_key": base64.b64encode(public_bytes).decode("ascii"),
            "valid_from": start_text, "valid_until": end_text, "revoked_at": None,
        }
        updated = {"publisher_trust_roots_schema_version": TRUST_ROOTS_SCHEMA_VERSION,
                   "keys": sorted([*roots["keys"], root], key=lambda item: item["key_id"])}
        _validate_trust_roots(updated)
    except Exception:
        os.close(private_parent_fd)
        os.close(roots_parent_fd)
        raise
    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    private_created = False
    try:
        for _attempt in range(100):
            temporary_name = f".trust-roots.{secrets.token_hex(12)}"
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    stat.S_IMODE(roots_stat.st_mode), dir_fd=roots_parent_fd,
                )
                temporary_descriptor = descriptor
                break
            except FileExistsError:
                continue
        else:
            raise PackError("cannot allocate trust-roots transaction file")
        os.fchmod(descriptor, stat.S_IMODE(roots_stat.st_mode))
        temporary_stat = os.fstat(descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(canonical(updated))
            stream.flush()
            os.fsync(stream.fileno())
        private_descriptor = os.open(
            private_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=private_parent_fd,
        )
        private_created = True
        with os.fdopen(private_descriptor, "wb") as stream:
            stream.write(private_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        private_stat = os.stat(private_path.name, dir_fd=private_parent_fd,
                               follow_symlinks=False)
        if not stat.S_ISREG(private_stat.st_mode) or stat.S_IMODE(private_stat.st_mode) != 0o600:
            raise PackError("publisher private key permissions must be 0600")
        current_roots = os.stat(roots_path.name, dir_fd=roots_parent_fd,
                                follow_symlinks=False)
        if (current_roots.st_dev, current_roots.st_ino) != (roots_stat.st_dev, roots_stat.st_ino):
            raise PackError("publisher trust roots changed during key registration")
        current_temporary = os.stat(
            temporary_name, dir_fd=roots_parent_fd, follow_symlinks=False,
        )
        if (not stat.S_ISREG(current_temporary.st_mode)
                or (current_temporary.st_dev, current_temporary.st_ino)
                != (temporary_stat.st_dev, temporary_stat.st_ino)
                or (os.fstat(temporary_descriptor).st_dev,
                    os.fstat(temporary_descriptor).st_ino)
                != (temporary_stat.st_dev, temporary_stat.st_ino)):
            raise PackError("publisher trust-roots transaction file changed during registration")
        os.replace(temporary_name, roots_path.name,
                   src_dir_fd=roots_parent_fd, dst_dir_fd=roots_parent_fd)
        temporary_name = None
        # The replacement is the commit point. Directory fsync is best-effort:
        # after it succeeds or fails, reporting rollback would misrepresent the
        # already committed trust-root registration.
        try:
            os.fsync(roots_parent_fd)
            os.fsync(private_parent_fd)
        except OSError:
            pass
        return root
    except PackError:
        raise
    except OSError as exc:
        raise PackError(f"publisher key registration failed: {exc}") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=roots_parent_fd)
            except FileNotFoundError:
                pass
        if temporary_name is not None and private_created:
            try:
                os.unlink(private_path.name, dir_fd=private_parent_fd)
            except FileNotFoundError:
                pass
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        os.close(private_parent_fd)
        os.close(roots_parent_fd)


def _sha_json(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _declared_hashes(manifest: dict, kinds: set[str]) -> str:
    paths = sorted(
        path for kind in kinds for path in manifest["assets"][kind]
    )
    return _sha_json({path: manifest["hashes"][path] for path in paths})


def signed_envelope(
    pack: pathlib.Path, manifest: dict, *, signer: str, key_id: str,
    issued_at: str, engine_release: str = __version__,
) -> dict[str, Any]:
    asset_kinds = set(manifest["assets"]) - {"eval-case", "eval-result"}
    return {
        "signature_envelope_schema_version": SIGNATURE_SCHEMA_VERSION,
        "pack": {
            "id": manifest["id"], "version": manifest["version"], "kind": manifest["kind"],
        },
        "manifest_sha256": digest(pack / "pack.yaml"),
        "compatibility_sha256": digest(pack / "compatibility.yaml"),
        "asset_tree_sha256": _declared_hashes(manifest, asset_kinds),
        "eval_case_tree_sha256": _declared_hashes(manifest, {"eval-case"}),
        "eval_result_tree_sha256": _declared_hashes(manifest, {"eval-result"}),
        "engine_release": engine_release,
        "issued_at": issued_at,
        "signer": signer,
        "key_id": key_id,
    }


def signed_digest(envelope: dict) -> str:
    return _sha_json(envelope)


def load_trust_roots() -> dict:
    resource = importlib.resources.files("rig_workbench.packs").joinpath("trust-roots.json")
    try:
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackError(f"publisher trust roots are unreadable: {exc}") from exc
    _validate_trust_roots(value)
    return value


def _parse_time(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str):
        raise PackError(f"{label} must be a timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PackError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PackError(f"{label} must include timezone")
    return parsed.astimezone(dt.timezone.utc)


def _decode_32(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise PackError(f"{label} must be base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise PackError(f"{label} is invalid base64") from exc
    if len(decoded) != 32:
        raise PackError(f"{label} must contain 32 bytes")
    return decoded


def _validate_trust_roots(value: object) -> None:
    if (not isinstance(value, dict)
            or set(value) != {"publisher_trust_roots_schema_version", "keys"}
            or type(value["publisher_trust_roots_schema_version"]) is not int
            or value["publisher_trust_roots_schema_version"] != TRUST_ROOTS_SCHEMA_VERSION
            or not isinstance(value["keys"], list)):
        raise PackError("publisher trust roots schema is invalid")
    seen: set[str] = set()
    for key in value["keys"]:
        required = {"key_id", "signer", "public_key", "valid_from", "valid_until", "revoked_at"}
        if not isinstance(key, dict) or set(key) != required:
            raise PackError("publisher trust root key schema is invalid")
        if (not isinstance(key["key_id"], str) or not key["key_id"]
                or not isinstance(key["signer"], str) or not key["signer"]
                or key["key_id"] in seen):
            raise PackError("publisher trust root key identity is invalid")
        seen.add(key["key_id"])
        _decode_32(key["public_key"], "publisher public key")
        start = _parse_time(key["valid_from"], "publisher key valid_from")
        end = (_parse_time(key["valid_until"], "publisher key valid_until")
               if key["valid_until"] is not None else None)
        revoked = (_parse_time(key["revoked_at"], "publisher key revoked_at")
                   if key["revoked_at"] is not None else None)
        if end is not None and end <= start:
            raise PackError("publisher key validity interval is invalid")
        if revoked is not None and revoked < start:
            raise PackError("publisher key revocation timestamp is invalid")


def verify_publisher_signature(
    pack: pathlib.Path, manifest: dict, *, trust_roots: dict | None = None,
    now: dt.datetime | None = None,
) -> dict[str, str] | None:
    signature_path = pack / SIGNATURE_NAME
    if not signature_path.exists():
        return None
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise PackError("publisher signature verification requires cryptography>=41") from exc
    raw, document = read_json_yaml(signature_path)
    if raw != canonical(document):
        raise PackError("pack publisher signature is not canonical JSON")
    if set(document) != {"pack_signature_schema_version", "signed", "signature"}:
        raise PackError("pack publisher signature schema is invalid")
    if (type(document["pack_signature_schema_version"]) is not int
            or document["pack_signature_schema_version"] != SIGNATURE_SCHEMA_VERSION
            or not isinstance(document["signed"], dict)):
        raise PackError("pack publisher signature schema is invalid")
    envelope = document["signed"]
    expected_fields = {
        "signature_envelope_schema_version", "pack", "manifest_sha256",
        "compatibility_sha256", "asset_tree_sha256", "eval_case_tree_sha256",
        "eval_result_tree_sha256", "engine_release", "issued_at", "signer", "key_id",
    }
    if set(envelope) != expected_fields:
        raise PackError("pack publisher envelope schema is invalid")
    if (type(envelope["signature_envelope_schema_version"]) is not int
            or envelope["signature_envelope_schema_version"] != SIGNATURE_SCHEMA_VERSION):
        raise PackError("pack publisher envelope version is invalid")
    expected = signed_envelope(
        pack, manifest, signer=envelope.get("signer"), key_id=envelope.get("key_id"),
        issued_at=envelope.get("issued_at"), engine_release=envelope.get("engine_release"),
    )
    if envelope != expected:
        raise PackError("pack publisher signature content binding mismatch")
    if envelope["engine_release"] != __version__:
        raise PackError("pack publisher signature engine release mismatch")
    issued = _parse_time(envelope["issued_at"], "pack signature issued_at")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise PackError("publisher verification time must include timezone")
    if issued > current.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise PackError("pack publisher signature is not yet valid")
    roots = trust_roots if trust_roots is not None else load_trust_roots()
    _validate_trust_roots(roots)
    matches = [key for key in roots["keys"] if key["key_id"] == envelope["key_id"]]
    if len(matches) != 1:
        raise PackError("pack publisher key is unknown")
    key = matches[0]
    if key["signer"] != envelope["signer"]:
        raise PackError("pack publisher signer does not match trust root")
    start = _parse_time(key["valid_from"], "publisher key valid_from")
    end = (_parse_time(key["valid_until"], "publisher key valid_until")
           if key["valid_until"] is not None else None)
    if issued < start or (end is not None and issued >= end):
        raise PackError("pack publisher key was not valid when signed")
    if key["revoked_at"] is not None:
        raise PackError("pack publisher key is revoked")
    public = Ed25519PublicKey.from_public_bytes(_decode_32(key["public_key"], "publisher public key"))
    try:
        signature = base64.b64decode(document["signature"], validate=True)
        public.verify(signature, canonical(envelope).encode("utf-8"))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise PackError("pack publisher signature is invalid") from exc
    return {"key_id": key["key_id"], "signed_digest": signed_digest(envelope)}


def _require_clean_committed(pack: pathlib.Path) -> pathlib.Path:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=pack, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
        )
        if top.returncode != 0:
            raise PackError("pack signing requires a git repository")
        root = pathlib.Path(top.stdout.strip()).resolve()
        relative = pack.resolve().relative_to(root)
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", str(relative)],
            cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
        history = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(relative)], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise PackError("cannot establish clean committed pack source") from exc
    if status.returncode != 0 or status.stdout.strip():
        raise PackError("pack signing refuses dirty or uncommitted source")
    if history.returncode != 0 or len(history.stdout.strip()) != 40:
        raise PackError("pack signing requires committed source")
    return root


def sign_pack(
    pack: pathlib.Path | str, *, private_key_path: pathlib.Path | str,
    key_id: str, signer: str, issued_at: dt.datetime | None = None,
) -> dict:
    from .installer import local_quality_status
    from .validation import validate_pack
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise PackError("pack signing requires cryptography>=41") from exc

    root = pathlib.Path(pack).resolve()
    source_repository = _require_clean_committed(root)
    if (not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_id)
            or not isinstance(signer, str) or not signer.strip()):
        raise PackError("publisher signer/key_id is invalid")
    manifest = validate_pack(root)
    if any(manifest["assets"][kind] for kind in PROMPT_KINDS):
        entry_ids = {item["id"] for item in manifest.get("entrypoints", [])}
        for relative in manifest["assets"]["eval-case"]:
            _raw, case = read_json_yaml(root / relative)
            required = {
                "prompt_entrypoint", "prompt_composition",
                "target_expectations", "clean_expectations",
            }
            if not required.issubset(case):
                raise PackError(
                    f"pack signing requires composition and distinct expectations: {case['id']}"
                )
            if case["prompt_entrypoint"] not in entry_ids:
                raise PackError(f"pack signing case entrypoint is not owned: {case['id']}")
            if case["target_expectations"] == case["clean_expectations"]:
                raise PackError(f"pack signing expectations are not distinct: {case['id']}")
            from .tester import compose_case_prompt
            compose_case_prompt(root, manifest, case, project=source_repository)
        if not manifest["assets"]["eval-result"]:
            raise PackError("pack signing requires durable declared evaluation results")
    if local_quality_status(root, manifest) != "verified-local":
        raise PackError("pack signing requires non-mock current green evaluation evidence")
    key_path = pathlib.Path(private_key_path).expanduser().resolve()
    if key_path.is_relative_to(source_repository):
        raise PackError("publisher private key must be outside the source repository")
    try:
        private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise PackError("cannot load explicit Ed25519 private key") from exc
    if not isinstance(private, Ed25519PrivateKey):
        raise PackError("private key must be Ed25519")
    selected_time = issued_at or dt.datetime.now(dt.timezone.utc)
    if selected_time.tzinfo is None:
        raise PackError("pack signing issued_at must include timezone")
    timestamp = selected_time.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    envelope = signed_envelope(
        root, manifest, signer=signer, key_id=key_id, issued_at=timestamp,
    )
    document = {
        "pack_signature_schema_version": SIGNATURE_SCHEMA_VERSION,
        "signed": envelope,
        "signature": base64.b64encode(
            private.sign(canonical(envelope).encode("utf-8"))
        ).decode("ascii"),
    }
    (root / SIGNATURE_NAME).write_text(canonical(document), encoding="utf-8")
    return document
