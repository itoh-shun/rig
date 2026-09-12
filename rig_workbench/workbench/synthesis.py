"""Deprecated location of `rig_workbench.assurance.synthesis` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.synthesis import (  # noqa: F401 (re-exported bridge)
    ERROR_SCHEMA, EXECUTION_ERROR, FLOOR_ENTRY_KEYS, MANDATORY_SOURCES,
    OPERATOR_REQUESTED, PLANNER_PROPOSED, POLICY_REQUIRED, REFUSED,
    REPORT_SCHEMA, RESOLVED, RISK_DERIVED, Required, SCHEMA, SOURCES,
    STEP_FIELDS, Step, TASK_TYPE_DEFAULT, WORKFLOW_KEYS, _floor_entry,
    _no_duplicate_keys, annotations, check_floor, cmd_synthesis, floor,
    load, load_catalog, missing_floor, resolve, validate, weakened,
)

warnings.warn(
    "rig_workbench.workbench.synthesis moved to rig_workbench.assurance.synthesis; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
