"""One place that answers "where is `scripts/<name>.py`?".

The package is installed without a `scripts/` sibling, so every module that wants
one of the repository's standalone scripts has to *search* for a checkout rather
than compute a path relative to itself. Computing it was the bug: modules one
level deeper wrote `parent.parent.parent`, modules at the top wrote
`parent.parent`, and the deeper ones also skipped `RIG_HOME` — under a
site-packages layout they landed on a `scripts/` that does not exist and reported
it as the feature being uninstalled.

Resolving from this module's own location removes the depth question by
construction: `repo_paths.py` sits at the package root, so its parents are the
same candidates no matter who is asking.

Order (same as `.claude-plugin/bin/rig`):
  1. `RIG_HOME`
  2. the install source (a `pip install -e .` checkout, or a plain checkout)
  3. the current directory and its parents (`cd path/to/rig` then `rig-wb`)
"""

from __future__ import annotations

import os
import pathlib

# Marker for "this directory is a rig checkout" when no particular script is asked
# about: orchestrate.py is the one every repo-backed subcommand needs.
ROOT_MARKER = "orchestrate.py"


def _candidate_roots() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    env = os.environ.get("RIG_HOME")
    if env:
        roots.append(pathlib.Path(env).resolve())
    here = pathlib.Path(__file__).resolve().parent   # <root>/rig_workbench
    roots.extend([here.parent, here.parent.parent])
    cwd = pathlib.Path.cwd().resolve()
    roots.extend([cwd, *cwd.parents])
    return roots


def find_script(name: str) -> pathlib.Path | None:
    """Return `<checkout>/scripts/<name>`, or None when no checkout has it.

    `name` is the file name, extension included (`"dashboard.py"`).
    """
    for root in _candidate_roots():
        script = root / "scripts" / name
        if script.is_file():
            return script
    return None


def find_root() -> pathlib.Path | None:
    """Return the rig checkout root, or None. A root is a directory holding
    `scripts/orchestrate.py`."""
    script = find_script(ROOT_MARKER)
    return script.parent.parent if script else None


def script_path(name: str) -> pathlib.Path:
    """Where `scripts/<name>` would be if it resolved — for error messages.

    Callers that have to name the path they could not find use this instead of
    re-deriving one, so the message points at a plausible location rather than at
    a computed non-existent one.
    """
    found = find_script(name)
    if found:
        return found
    root = find_root()
    base = root if root else pathlib.Path(__file__).resolve().parent.parent
    return base / "scripts" / name
