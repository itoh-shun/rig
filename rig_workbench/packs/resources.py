from __future__ import annotations

import mimetypes
import pathlib
import re

from .manifest import digest, safe_relative
from .model import PackError

MAX_RESOURCE_BYTES = 16 * 1024 * 1024
EXECUTABLE_SUFFIXES = frozenset({
    ".bat", ".cmd", ".com", ".dll", ".dylib", ".exe", ".jar", ".msi",
    ".ps1", ".py", ".sh", ".so",
})
EXECUTABLE_MIME_PREFIXES = ("application/x-executable", "application/x-sharedlib")
ALLOWED_MIME = frozenset({
    "application/json", "application/pdf", "image/gif", "image/jpeg", "image/png",
    "image/svg+xml", "text/css", "text/csv", "text/html", "text/markdown",
    "text/plain",
})
SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def _looks_like_html(data: bytes) -> bool:
    head = data[:1024].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<!--"))


def validate_resource(root: pathlib.Path, relative: str, metadata: object) -> None:
    rel = safe_relative(relative)
    path = root / rel
    if (not isinstance(metadata, dict)
            or set(metadata) != {"media_type", "size", "sha256"}):
        raise PackError(f"resource metadata is invalid: {relative}")
    media_type = metadata.get("media_type")
    size = metadata.get("size")
    sha256 = metadata.get("sha256")
    if (not isinstance(media_type, str) or media_type not in ALLOWED_MIME
            or media_type.startswith(EXECUTABLE_MIME_PREFIXES)):
        raise PackError(f"resource MIME is forbidden or unsupported: {relative}")
    if path.suffix.casefold() in EXECUTABLE_SUFFIXES:
        raise PackError(f"executable resource extension is forbidden: {relative}")
    if (isinstance(size, bool) or not isinstance(size, int)
            or size < 0 or size > MAX_RESOURCE_BYTES):
        raise PackError(f"resource size is invalid: {relative}")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise PackError(f"resource hash is invalid: {relative}")
    try:
        actual_size = path.stat().st_size
        data = path.read_bytes()
    except OSError as exc:
        raise PackError(f"resource cannot be read: {relative}: {exc}") from exc
    if actual_size != size or len(data) != size:
        raise PackError(f"resource size mismatch: {relative}")
    if digest(path) != sha256:
        raise PackError(f"resource hash mismatch: {relative}")
    guessed, _encoding = mimetypes.guess_type(path.name)
    aliases = {".md": "text/markdown", ".svg": "image/svg+xml"}
    expected = aliases.get(path.suffix.casefold(), guessed)
    if expected != media_type:
        raise PackError(f"resource MIME/extension mismatch: {relative}")
    signatures = SIGNATURES.get(media_type)
    if signatures and not data.startswith(signatures):
        raise PackError(f"resource signature mismatch: {relative}")
    if media_type == "text/html" and not _looks_like_html(data):
        raise PackError(f"resource HTML signature mismatch: {relative}")
    if media_type.startswith("text/") or media_type in {"application/json", "image/svg+xml"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PackError(f"text resource is not UTF-8: {relative}") from exc
