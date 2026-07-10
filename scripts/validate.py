#!/usr/bin/env python3
"""Compatibility shim — the implementation moved to the rig_workbench/validation/ package.

Keeps both existing entry paths working: `python3 scripts/validate.py [selftest]`
(CI: .github/workflows/validate.yml), and rig_workbench/cli.py loading this file
with importlib and calling `.main()`.
"""

import pathlib
import sys

# Put the repo root (parent of scripts/) on sys.path so the rig_workbench
# package can be imported from any cwd.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rig_workbench.validation.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
