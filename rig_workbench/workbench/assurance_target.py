"""Deprecated location of `rig_workbench.assurance.assurance_target` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.assurance_target import (  # noqa: F401 (re-exported bridge)
    AXES, BLOCKS, COMPLETE, EXECUTION_ERROR, INCOMPLETE, MET, OUTCOMES,
    SCHEMA, TARGET_KEYS, UNMET, UNOBSERVABLE, VAGUE, _achieved,
    annotations, cmd_assurance_target, evaluate, read, validate,
)

warnings.warn(
    "rig_workbench.workbench.assurance_target moved to rig_workbench.assurance.assurance_target; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
