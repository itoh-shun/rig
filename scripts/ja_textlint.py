#!/usr/bin/env python3
"""Launcher for `python3 scripts/ja_textlint.py <artifact>...`.

The implementation lives in `rig_workbench/ja_textlint.py` so the installed
`rig-wb ja-lint` can import it — under site-packages there is no sibling
`scripts/` dir to put on `sys.path`. This file keeps the documented
direct-script entry point working from a checkout; it holds no linting logic
of its own.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rig_workbench.console import harden_streams  # noqa: E402
from rig_workbench.ja_textlint import main  # noqa: E402

if __name__ == "__main__":
    # The process boundary for `python3 scripts/ja_textlint.py ...` is this file rather than the
    # package's `main()`, so this is where rig's streams are hardened against a console
    # that cannot encode its prose — `PYTHONIOENCODING=ascii`, a `LANG=C` CI runner —
    # where `print` raises instead of printing. `rig_workbench/console.py` states what
    # that covers and what it does not. The installed `rig-wb` gets the same call from
    # `exitcodes.guard`; `rig_workbench/cli.py`'s importlib path never runs this block.
    harden_streams()
    sys.exit(main())
