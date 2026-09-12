"""Deprecated location of `rig_workbench.assurance.assurance_wiring` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.assurance_wiring import (  # noqa: F401 (re-exported bridge)
    ABSENT, DERIVED, ENTRY_KEYS, EXECUTION_ERROR, INVALID, NOT_DERIVABLE,
    NOT_RECORDED, Required, UNREADABLE_FILE, _entry, _unobserved,
    annotations, check_floor, cmd_assurance_derive, floor_from,
    load_requires, projection, read_requires, unreachable,
)

warnings.warn(
    "rig_workbench.workbench.assurance_wiring moved to rig_workbench.assurance.assurance_wiring; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
