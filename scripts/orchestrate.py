#!/usr/bin/env python3
"""Compatibility shim — the implementation moved to the rig_workbench/orchestrate/ package.

Keeps both existing entry paths working: `python3 scripts/orchestrate.py <cmd>` (including
via bin/orchestrate and .claude-plugin/bin/rig), and rig_workbench/cli.py loading this file
with importlib and calling `.main()`. The usage text (`print(__doc__)`) lives in the cli module.
"""

import pathlib
import sys

# Put the repo root (parent of scripts/) on sys.path so the rig_workbench
# package can be imported from any cwd.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rig_workbench.orchestrate.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
