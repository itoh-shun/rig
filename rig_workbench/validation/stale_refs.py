"""validation stale_refs: stale path-reference check over the shipped prose
surfaces (issue #316) — thin CI wrapper around workbench.stale_refs.

Shipped docs habitually reference paths in OTHER contexts (a user project's
`.claude/rig.md`, a Remotion project's `src/Root.tsx`, `~/.claude/`-relative
namespaces). Those can never resolve inside the rig repo, so they are
excluded by prefix below — the list is the curated set of example namespaces
the shipped docs legitimately use. Everything else that fails to resolve
between the referencing file and the repo root is a WARN: a doc pointing at
a file that moved or died.
"""

from rig_workbench.workbench.stale_refs import scan_stale_refs

from .config import ROOT, SKILLS
from .state import _emit

# Example-namespace prefixes shipped docs legitimately reference without the
# path existing in this repo (user projects, other repos, ~/.claude layouts).
_EXAMPLE_PREFIXES = (
    ".claude/",              # user-project manifest/knowledge layout
    ".rig/",                 # runtime state, exists only after runs
    "video/",                # movie-harness output dirs in user projects
    "src/",                  # Remotion user-project examples
    "knowledge/wiki/",       # ~/.claude/rig/-relative namespace
    "rig/knowledge/",        # ~/.claude/-relative namespace
    "skills/hyperframes/",   # external-repo import example
    "workbench/injection.py",  # shorthand for rig_workbench/workbench/injection.py
    "docs/screenshots/",     # user-project screenshot location example
)


def check_stale_refs() -> None:
    files = sorted(f for f in SKILLS.rglob("*.md") if f.is_file())
    findings = scan_stale_refs(ROOT, files, exclude_prefixes=_EXAMPLE_PREFIXES)
    if not findings:
        _emit("PASS", f"stale-refs: {len(files)} shipped docs — every checkable path reference resolves")
        return
    for f in findings:
        _emit("WARN", f"stale-refs — {f['file']}:{f['line']} references `{f['ref']}`, "
                      "which does not exist (moved or deleted? fix or drop the reference)")
