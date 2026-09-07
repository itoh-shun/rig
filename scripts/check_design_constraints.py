#!/usr/bin/env python3
"""Launcher for `python3 scripts/check_design_constraints.py <artifact>...`.

The implementation lives in `rig_workbench/design_constraints.py` so the
installed `rig-wb` can import it — under site-packages there is no sibling
`scripts/` dir to put on `sys.path`. This file keeps the documented
direct-script entry point working from a checkout; it holds no checking logic
of its own.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rig_workbench.design_constraints import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
