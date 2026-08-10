#!/usr/bin/env python3
"""Launcher for `python3 scripts/ast_diff.py <base.py> <new.py>` (#280).

The implementation moved to `rig_workbench/ast_diff.py` so the installed
`rig-wb` can import it — under site-packages there is no sibling `scripts/`
dir to put on `sys.path`. This file keeps the documented direct-script entry
point working from a checkout; it holds no diff logic of its own.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rig_workbench.ast_diff import main  # noqa: E402

if __name__ == "__main__":
    main()
