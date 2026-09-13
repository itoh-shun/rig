"""Deprecated location of `rig_workbench.assurance.team_routing` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.team_routing import (  # noqa: F401 (re-exported bridge)
    ADMISSIBLE, ADMITTED, ARCHITECTURE_VERIFIER, ASSIGNMENT_FIELDS,
    ASSURANCE_ROLES, Assignment, CONFIDENCE, CONSTRAINT_KEYS, Constraints,
    DEVELOPER, DOCUMENT_KEYS, EXECUTION_ERROR, IDENTITY_UNKNOWN, JUDGE,
    MEASURED, NOT_APPROVED, NOT_CAPABLE, NOT_INDEPENDENT, NOT_MEASURED,
    NOT_THIS_TASK, PLANNER, REFUSED, REJECTED, ROLES, ROLE_TWICE,
    ROLE_UNFILLED, SCHEMA, SECURITY_VERIFIER, SHADOW, UNMEASURED,
    _is_provider, annotations, assignment_problems, check, cmd_route_team,
    load, load_constraints, validate, violations,
)

warnings.warn(
    "rig_workbench.workbench.team_routing moved to rig_workbench.assurance.team_routing; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
