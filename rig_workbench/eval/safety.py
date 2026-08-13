"""Shared unsafe-text policy for evaluation validation and capture."""

from __future__ import annotations

import re
import unicodedata
import urllib.parse

_SECRET_KEY = re.compile(
    r"(?:secret|password|passwd|credential|token|api[_-]?key|private[_-]?key)", re.I
)
_SECRET_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{16,}|"
    r"(?:eyJ[A-Za-z0-9_-]{8,})\.(?:[A-Za-z0-9_-]{8,})\.(?:[A-Za-z0-9_-]{8,})|"
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._~-]{8,})"
)
_ASSIGNMENT_LHS = re.compile(r"([A-Za-z0-9_-]+)\s*[:=]")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s'\"=(\[{,:])(?:/[A-Za-z0-9._~-]|[A-Za-z]:[\\/]|[\\/]{2}[^\\/\s])"
)
_FILE_URI = re.compile(r"(?:^|[\s'\"=(\[{,:])file:", re.I)
_HOME_PATH = re.compile(r"(?:^|[\s'\"=(\[{,:])~[\\/]")
_WEB_SCHEME = re.compile(r"https?://", re.I)


def unsafe_key_reason(key: object) -> str | None:
    return "secret-like field name" if _SECRET_KEY.search(str(key)) else None


def _percent_decoded(value: str) -> str:
    decoded = value
    for _ in range(3):
        unquoted = urllib.parse.unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    return decoded


def unsafe_path_reason(value: str) -> str | None:
    """Why a repository-relative path may not be recorded, or `None`.

    Everything `unsafe_text_reason` refuses about *where* a string points —
    escapes out of the tree, absolute and home-relative paths, `file:` URIs, and
    the control and format characters that let a path lie about itself in a log
    or a terminal — and nothing about what a value might contain.

    Scanning a path for secret-like *values* looks free and is not. These paths
    come from `git ls-tree` under registered prompt-surface prefixes: they name
    files that are public in the repository, so a filename can never be a leaked
    credential, and the credential patterns match ordinary English through their
    prefixes. `sk-[A-Za-z0-9_-]{8,}` matched inside
    `skills/engine/facets/policies/risk-based-testing.md` — the `sk-based-testing`
    in `ri|sk-based-testing` — and refused every measurement this repository could
    produce, after the providers had run and the result had been signed. A check
    that cannot be true of a path but can be false of one is not protection.
    """
    if any(unicodedata.category(ch) == "Cf" for ch in value):
        return "Unicode format control"
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        return "control character"
    decoded = _percent_decoded(value)
    if _FILE_URI.search(decoded):
        return "file URI"
    if _HOME_PATH.search(decoded):
        return "home-relative absolute path"
    normalized = decoded.replace("\\", "/")
    path_probe = _WEB_SCHEME.sub("web__", decoded)
    if _ABSOLUTE_PATH.search(path_probe):
        return "absolute path"
    if ".." in normalized.split("/"):
        return "path traversal"
    return None


def unsafe_text_reason(value: str) -> str | None:
    reason = unsafe_path_reason(value)
    if reason is not None:
        return reason
    if _SECRET_VALUE.search(value):
        return "secret-like value"
    if any(unsafe_key_reason(match.group(1))
           for match in _ASSIGNMENT_LHS.finditer(_percent_decoded(value))):
        return "secret-like assignment"
    return None
