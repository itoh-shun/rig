"""Verifying detached Ed25519 publisher signatures on Rig packs.

Split out of `packs.publisher` so that the *verify* half is reachable from the brick
resolution path. `publisher` cannot be imported from `packs.resolver`: its signing half
imports `resolver.core_reference_ids` and `tester.compose_case_prompt`, and `tester`
imports `resolver` — so `resolver -> publisher` closes a component. Verifying a signature
needs none of that. It reads `pack.sig.json`, rebuilds the envelope from the manifest,
consults the shipped trust roots, and runs Ed25519 over `canonical(envelope)`; its only
dependencies are `.manifest`, `.model` and the engine version, which makes this module a
leaf and safely importable from anywhere in the package, `resolver` included.

That matters because it is what `lock.PublisherVerifier` is handed. `validate_lock_root`
takes the check as a required argument rather than importing it, and a caller that passes
`None` is declaring that it is not re-running the cryptography — which for the resolve path
meant a tampered `pack.sig.json` reached `resolve_all` unnoticed, `pack.sig.json` being the
one file in a pack that `manifest["hashes"]` does not cover. Every shipped caller now
passes `verify_publisher_signature`.

**This module is the monkeypatch point.** `publisher` re-exports these names so
`from .publisher import verify_publisher_signature` keeps working, but a re-export is a
second binding: patching `publisher.load_trust_roots` would not be seen by the function
defined here, whose global lookup lands in *this* module's namespace. Tests that substitute
trust roots or disarm the verifier must patch `rig_workbench.packs.signature`.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.resources
import json
import pathlib
from typing import Any

from rig_workbench import __version__

from .manifest import canonical, digest, read_json_yaml
from .model import PackError

SIGNATURE_NAME = "pack.sig.json"
SIGNATURE_SCHEMA_VERSION = 1
TRUST_ROOTS_SCHEMA_VERSION = 1


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
