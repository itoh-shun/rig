#!/usr/bin/env python3
"""Compatibility shim — the implementation moved to the rig_workbench/orchestrate/ package.

Keeps both existing entry paths working: `python3 scripts/orchestrate.py <cmd>` (including
via .claude-plugin/bin/rig), and rig_workbench/cli.py loading this file
with importlib and calling `.main()`. The usage text (`print(__doc__)`) lives in the cli module.
"""

import pathlib
import sys

# Put the repo root (parent of scripts/) on sys.path so the rig_workbench
# package can be imported from any cwd.
_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from rig_workbench.console import harden_streams  # noqa: E402
from rig_workbench.orchestrate.cli import main  # noqa: E402

if __name__ == "__main__":
    # The process boundary for `python3 scripts/orchestrate.py ...` is this file rather than the
    # package's `main()`, so this is where rig's streams are hardened against a console
    # that cannot encode its prose — `PYTHONIOENCODING=ascii`, a `LANG=C` CI runner —
    # where `print` raises instead of printing. `rig_workbench/console.py` states what
    # that covers and what it does not. The installed `rig-wb` gets the same call from
    # `exitcodes.guard`; `rig_workbench/cli.py`'s importlib path never runs this block.
    harden_streams()
    main()
