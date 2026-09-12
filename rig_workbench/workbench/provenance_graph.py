"""Deprecated location of `rig_workbench.assurance.provenance_graph` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.provenance_graph import (  # noqa: F401 (re-exported bridge)
    APPROVAL, APPROVED_BY, AUTHORITY_KINDS, BASES, COMMIT, CONFIRMED,
    DECISION, DECLARED_AUTHORITIES, DEPLOYED_AS, DEPLOYMENT, DERIVED_FROM,
    DOCUMENT_KEYS, EDGE_FIELDS, EVIDENCE, EXECUTION_ERROR, Edge, FOUND,
    GOAL, IMPLEMENTS, INFERRED, INTENT, INVALIDATES, KINDS, MEASURED_BY,
    MISSING, NODE_FIELDS, NOT_CHECKED, NOT_TRACEABLE, Node, OBJECT_ID,
    OUTCOME, RELATIONS, REQUIREMENT, RESOLVABLE_AUTHORITIES, SATISFIES,
    SCHEMA, TASK, TRACED, VERIFIED_BY, _FORBIDDEN_EDGE_KEYS, _answer,
    _is_name, _looked_up, _reachable, _resolution, annotations,
    cmd_provenance, edge_problems, invalidated, load, node_problems,
    repository_resolver, trace, validate,
)

warnings.warn(
    "rig_workbench.workbench.provenance_graph moved to rig_workbench.assurance.provenance_graph; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
