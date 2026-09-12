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

import pathlib
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .config import ROOT, SKILLS
from .rig_surfaces import STALE_REF_SCANNER
from .state import _emit


@runtime_checkable
class StaleRefScanner(Protocol):
    """Path references in a set of documents that do not resolve against a root.

    What counts as a path reference in prose — backticked tokens, link targets, what to
    ignore — is `workbench.stale_refs`' rule, and it is the same rule `rig scan stale-refs`
    applies interactively. A second reading of the same documents here would be a second
    definition of "reference", and the CI check and the interactive one would disagree
    about the same file. So this module supplies the corpus and the exclusion list and
    borrows the sensor, declared as the shape it needs rather than imported.
    """

    def __call__(self, root: pathlib.Path, files: Sequence[pathlib.Path], *,
                 exclude_prefixes: Sequence[str]) -> list[dict]:
        ...

# Example-namespace prefixes shipped docs legitimately reference without the
# path existing in this repo (user projects, other repos, ~/.claude layouts).
_EXAMPLE_PREFIXES = (
    ".claude/",              # user-project manifest/knowledge layout
    ".rig/",                 # runtime state, exists only after runs
    "video/",                # generated media output dirs in user projects
    "src/",                  # Remotion user-project examples
    "knowledge/wiki/",       # ~/.claude/rig/-relative namespace
    "rig/knowledge/",        # ~/.claude/-relative namespace
    "skills/hyperframes/",   # external-repo import example
    "workbench/injection.py",  # shorthand for rig_workbench/workbench/injection.py
    "docs/screenshots/",     # user-project screenshot location example
)


def check_stale_refs(*, scanner: StaleRefScanner = STALE_REF_SCANNER) -> None:
    files = sorted(f for f in SKILLS.rglob("*.md") if f.is_file())
    findings = scanner(ROOT, files, exclude_prefixes=_EXAMPLE_PREFIXES)
    if not findings:
        _emit("PASS", f"stale-refs: {len(files)} shipped docs — every checkable path reference resolves")
        return
    for f in findings:
        _emit("WARN", f"stale-refs — {f['file']}:{f['line']} references `{f['ref']}`, "
                      "which does not exist (moved or deleted? fix or drop the reference)")
