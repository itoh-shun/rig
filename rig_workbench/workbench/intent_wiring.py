"""Deprecated location of `rig_workbench.assurance.intent_wiring` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.intent_wiring import (  # noqa: F401 (re-exported bridge)
    DERIVED, EXECUTION_ERROR, FLOOR_SOURCES, NOT_DERIVABLE,
    OPERATOR_REQUESTED, POLICY_REQUIRED, Required, annotations,
    check_floor, cmd_derive, floor_from, intent_unobserved, projection,
    resting_on, target_from, unaskable, unmatched,
)

warnings.warn(
    "rig_workbench.workbench.intent_wiring moved to rig_workbench.assurance.intent_wiring; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
