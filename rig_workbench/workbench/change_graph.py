"""Deprecated location of `rig_workbench.assurance.change_graph` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.change_graph import (  # noqa: F401 (re-exported bridge)
    COMPATIBILITY_KEYS, COMPATIBILITY_STATUSES, DEPENDENCY_KEYS,
    DEPENDENCY_KINDS, IMMUTABLE_GIT, NODE_KEYS, NODE_STATUSES,
    REPORT_SCHEMA, ROOT_KEYS, SCHEMA, _no_duplicate_keys, _text, _unknown,
    annotations, assess, cmd_change_graph, read, validate,
)

warnings.warn(
    "rig_workbench.workbench.change_graph moved to rig_workbench.assurance.change_graph; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
