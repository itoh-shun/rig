"""Trusted HMAC-SHA256 attestation for evaluation results."""

from __future__ import annotations

import hashlib
import hmac
import os
import pathlib
import re
import secrets
import stat
import tempfile

from .cases import EvalCaseError, canonical_json

# A configured key must have the shape of machine-generated material, not merely
# a length. `key_id = sha256(material)[:16]` used to live only in `.rig/`, which
# is gitignored; signed evidence is committed now, so on a public repository the
# key id is published with it and is a complete offline oracle for guessing the
# key. Against a memorable 32-character passphrase — legal under the old length
# rule, and only discouraged in prose — that turns "the maintainer measured this"
# into something an outsider can forge outright, which is worse than any replay.
# Randomness is not checkable, so the enforceable proxy is the form: 64 hex
# characters, exactly what `openssl rand -hex 32` emits.
_CONFIGURED_KEY = re.compile(r"[0-9a-fA-F]{64}")


def _key_path() -> pathlib.Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = pathlib.Path(state) if state else pathlib.Path.home() / ".local" / "state"
    return base / "rig" / "eval-attestation.key"


def _key_material(stored: bytes) -> bytes:
    """The secret bytes that a stored representation stands for.

    One key reaches this process by routes that cannot all carry the same shape.
    A GitHub secret holds text, so the environment and the file CI writes from it
    hold the 64 hex characters of `openssl rand -hex 32`; a key file Rig generated
    before this held the 32 raw bytes those characters spell. Both are notations
    for one secret, so both decode to it, and `key_id` is a property of the secret
    rather than of how it was written down.

    Reading the hex notation as its own bytes is what made a locally signed
    measurement unverifiable in CI: no secret is simultaneously 32 raw bytes and
    the 64 characters that spell them, so the two ends derived different key ids
    from the same key and every signature crossing between them was refused. The
    two notations are distinguishable by construction — 32 raw bytes are never 64
    characters — so nothing here has to guess.
    """
    try:
        text = stored.strip().decode("ascii")
    except UnicodeDecodeError:
        return stored
    if _CONFIGURED_KEY.fullmatch(text):
        return bytes.fromhex(text)
    return stored


def _read_file_key(path: pathlib.Path, *, create: bool) -> bytes:
    try:
        if path.is_symlink():
            raise EvalCaseError("attestation key must not be a symlink")
        if not path.exists():
            if not create:
                raise EvalCaseError("trusted attestation key is unavailable")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path.parent, 0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".eval-key.", dir=path.parent
            )
            temporary = pathlib.Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                # Written in the hex notation a GitHub secret can hold, so the
                # file's own contents are the value to paste into
                # `RIG_EVAL_ATTESTATION_KEY` — the maintainer never has to convert
                # anything to let CI verify what this key signs.
                key = (secrets.token_hex(32) + "\n").encode("ascii")
                os.write(descriptor, key)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                os.link(temporary, path)
            except FileExistsError:
                pass
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise EvalCaseError("attestation key must be a regular 0600 file")
        key = _key_material(path.read_bytes())
    except EvalCaseError:
        raise
    except OSError as exc:
        raise EvalCaseError(f"attestation key store error: {type(exc).__name__}") from exc
    if len(key) < 32:
        raise EvalCaseError("attestation key is invalid")
    return key


def _trusted_key(*, create: bool) -> bytes:
    configured = os.environ.get("RIG_EVAL_ATTESTATION_KEY")
    if configured is not None:
        if not _CONFIGURED_KEY.fullmatch(configured):
            raise EvalCaseError(
                "configured attestation key is invalid: RIG_EVAL_ATTESTATION_KEY must "
                "be 64 hex characters, as produced by `openssl rand -hex 32`"
            )
        # Decoded, like every other route into `_key_material`. CI writes this
        # same string into a key file and reads it back through `_read_file_key`,
        # and a maintainer's key file holds either these characters or the bytes
        # they spell; one notation decoded and another not is exactly how the two
        # ends stopped agreeing on `key_id`.
        return _key_material(configured.encode("ascii"))
    return _read_file_key(_key_path(), create=create)


def sign_result_attestation(result: dict) -> dict:
    key = _trusted_key(create=True)
    payload = canonical_json(result).encode("utf-8")
    return {
        "algorithm": "HMAC-SHA256",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "signature": hmac.new(key, payload, hashlib.sha256).hexdigest(),
    }


def verify_result_attestation(result: dict) -> bool:
    attestation = result.get("attestation")
    if not isinstance(attestation, dict) or set(attestation) != {
        "algorithm", "key_id", "signature"
    }:
        return False
    if attestation["algorithm"] != "HMAC-SHA256":
        return False
    key = _trusted_key(create=False)
    if attestation["key_id"] != hashlib.sha256(key).hexdigest()[:16]:
        return False
    payload = dict(result)
    payload.pop("attestation")
    expected = hmac.new(
        key, canonical_json(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(str(attestation["signature"]), expected)
