#!/usr/bin/env python3
"""Deprecated entry point. The mutation adapter is `rig-wb mutation`.

1.31.x shipped this as a loose script, which made it the one detection surface an
operator had to reach into `scripts/` for while `hostcheck`, `coverage` and `asvs`
were all `rig-wb` subcommands. The logic now lives in `rig_workbench/mutation.py`
and is reachable the same way as the rest.

This file stays so instructions written against 1.31.x keep working: the old
positional form `mutation_adapter.py <format> <report>` is still accepted by the
new command, so every argument is forwarded unchanged.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rig_workbench.console import harden_streams  # noqa: E402
from rig_workbench.mutation import cmd_mutation  # noqa: E402

if __name__ == "__main__":
    # Every process entry under `scripts/` hardens its output streams here — see
    # `rig_workbench/console.py`, and `scripts/workbench.py` for the reasoning in full.
    harden_streams()
    print("[note] scripts/mutation_adapter.py is deprecated — use `rig-wb mutation`.",
          file=sys.stderr)
    sys.exit(cmd_mutation(sys.argv[1:]))
