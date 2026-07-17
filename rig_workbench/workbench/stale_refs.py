"""workbench stale_refs: staleness check for path references in the manifest
and knowledge layer (issue #316).

Rule files rot: a path that once anchored an instruction keeps being injected
long after the file moved or died, and every later task pays the confusion
tax. Freshness stamps (wiki `reviewed_at`, instinct decay) are time proxies;
this check is the direct signal — does the referenced file still exist?

Deliberately conservative extraction to keep the false-positive rate near
zero (a noisy staleness check trains people to ignore it, which is worse
than not having one):

  - Only backtick-quoted tokens are considered (`like/this.md`). Bare prose
    paths and YAML `sources:` entries are NOT extracted — too many false
    positives without real parsing; documented as a known limitation.
  - The token must contain "/" and end in a file extension (or a trailing
    "/" for directories).
  - Skipped outright: URLs, absolute paths (/... or ~...), placeholder-ish
    tokens (<>, {}, *, $, …, "path/to", "..."), and anything with spaces.

Findings are WARN-grade and never block — deleting or fixing a reference is
a judgment call (the file may be about to be created, or the doc may
describe another repo). CLI exit code is 0 even with findings; CI callers
that want failure semantics can wrap it.
"""

import argparse
import pathlib
import re

from .state import maybe_repo_root

# Backtick-quoted token: no spaces, no nested backticks.
_BACKTICK_RE = re.compile(r"`([^`\s]+)`")
# Looks like a relative file path: at least TWO segments (a bare `personas/`
# is a generic namespace mention, not a checkable reference), ending in a
# file extension or an explicit trailing slash for directories.
_PATHISH_RE = re.compile(r"^[\w.@-]+(?:/[\w.@-]+)+(?:\.\w{1,10}|/)$")
# Tokens that are templates/examples, not real references.
_PLACEHOLDER_MARKERS = ("<", ">", "{", "}", "*", "$", "…", "path/to", "...")

DEFAULT_SURFACES = (".claude/rig.md",)
DEFAULT_SURFACE_DIRS = (".claude/rig/knowledge",)


def extract_path_refs(text: str) -> list[tuple[int, str]]:
    """(lineno, ref) for every backtick-quoted, relative-path-looking token.

    Conservative by design — see the module docstring for what is skipped."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for tok in _BACKTICK_RE.findall(line):
            if tok.startswith(("http://", "https://", "/", "~")):
                continue
            if any(m in tok for m in _PLACEHOLDER_MARKERS):
                continue
            if "/" not in tok:
                continue
            if not _PATHISH_RE.match(tok):
                continue
            out.append((lineno, tok))
    return out


def _resolvable(ref: str, file_dir: pathlib.Path, base_dir: pathlib.Path) -> bool:
    """A reference counts as alive if it exists relative to the referencing
    file's own directory or ANY ancestor up to (and including) `base_dir` —
    docs habitually reference paths relative to a contextual root (a SKILL.md
    speaks skill-dir-relative, a README repo-relative), and guessing the one
    true anchor wrongly would manufacture false positives."""
    d = file_dir
    while True:
        if (d / ref).exists():
            return True
        if d == base_dir or d.parent == d:
            return False
        d = d.parent


def scan_stale_refs(base_dir: pathlib.Path, files: list[pathlib.Path],
                    exclude_prefixes: tuple[str, ...] = ()) -> list[dict]:
    """References in `files` that resolve nowhere between the referencing
    file's directory and `base_dir`.

    `exclude_prefixes` skips reference namespaces that legitimately do not
    exist where the check runs (e.g. shipped docs describe user-project paths
    like `.claude/rig.md`, or other repos' example layouts, that don't exist
    inside the rig repo itself)."""
    findings: list[dict] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        rel_file = f.relative_to(base_dir).as_posix() if f.is_relative_to(base_dir) else str(f)
        for lineno, ref in extract_path_refs(text):
            if any(ref.startswith(p) for p in exclude_prefixes):
                continue
            if not _resolvable(ref, f.parent, base_dir):
                findings.append({"file": rel_file, "line": lineno, "ref": ref})
    return findings


def default_surface_files(root: pathlib.Path) -> list[pathlib.Path]:
    """The user-project surfaces this check exists for: the manifest and the
    project knowledge layer."""
    files: list[pathlib.Path] = []
    for rel in DEFAULT_SURFACES:
        p = root / rel
        if p.is_file():
            files.append(p)
    for rel in DEFAULT_SURFACE_DIRS:
        d = root / rel
        if d.is_dir():
            files.extend(sorted(f for f in d.rglob("*.md") if f.is_file()))
    return files


def cmd_stale_refs(args: argparse.Namespace) -> None:
    root = maybe_repo_root() or pathlib.Path.cwd()
    if args.paths:
        files = []
        for p in (pathlib.Path(x) for x in args.paths):
            if p.is_file():
                files.append(p.resolve())
            elif p.is_dir():
                files.extend(sorted(f for f in p.resolve().rglob("*.md") if f.is_file()))
            else:
                print(f"[WARN] path '{p}' does not exist — skipped")
        scope = ", ".join(args.paths)
    else:
        files = default_surface_files(root)
        scope = f"manifest + project knowledge of {root}"

    findings = scan_stale_refs(root, files, exclude_prefixes=(".rig/",))
    print(f"## stale-refs: {scope} ({len(files)} file(s))")
    if not findings:
        print("No stale references. (Only backtick-quoted relative paths are checked — "
              "bare prose paths are out of scope by design.)")
        return
    print(f"{len(findings)} stale reference(s) — the file no longer exists at that path:")
    for f in findings:
        print(f"  {f['file']}:{f['line']}  `{f['ref']}`")
    print("Advisory only (exit 0): fix or delete the reference, or leave it if the "
          "path is about to exist — that judgment stays with you.")
