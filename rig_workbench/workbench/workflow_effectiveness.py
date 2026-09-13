"""Deprecated location of `rig_workbench.assurance.workflow_effectiveness` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.workflow_effectiveness import (  # noqa: F401 (re-exported bridge)
    COMPLETE, EXECUTION_ERROR, FAILURE_STATUSES, PATTERN_KEYS, QUERY_KEYS,
    QUERY_SCHEMA, REFUSED, SCHEMA, _metrics, _nonnegative_int, _patterns,
    _positive_int, _read_jsonl, _read_workbench, _runtime,
    _time_to_assurance, _token_usage, _unobservable, analyse, annotations,
    cmd_workflow_effectiveness, read_query, validate_query,
)

warnings.warn(
    "rig_workbench.workbench.workflow_effectiveness moved to rig_workbench.assurance.workflow_effectiveness; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
