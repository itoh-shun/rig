"""Deprecated location of `rig_workbench.assurance.contract` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.contract import (  # noqa: F401 (re-exported bridge)
    ACCEPTABLE, EXECUTION_ERROR, EXIT_CODE, NOT_ACCEPTABLE, PENDING,
    SCHEMA, STATUS, _error, annotations, build, cmd_contract,
    final_status_vocabulary, repo_root, resolve_task_id, run_dir,
)

warnings.warn(
    "rig_workbench.workbench.contract moved to rig_workbench.assurance.contract; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
