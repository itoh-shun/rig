"""Deprecated location of `rig_workbench.assurance.org_knowledge` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.org_knowledge import (  # noqa: F401 (re-exported bridge)
    ACTIVE, APPROVED, CANDIDATE, DEPRECATED, EVALUATED, LEDGER,
    NAMED_TRANSITIONS, OrgKnowledgeError, ROLLED_BACK, SCHEMA, SUPPORTED,
    TRANSITIONS, _ID, _append, _events, _new_id, _now, _scopes_overlap,
    active_rules, annotations, assess, conflicts, history, ledger_path,
    listing, promote, read, register, replay,
)

warnings.warn(
    "rig_workbench.workbench.org_knowledge moved to rig_workbench.assurance.org_knowledge; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
