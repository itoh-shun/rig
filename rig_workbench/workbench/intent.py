"""Deprecated location of `rig_workbench.assurance.intent` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.intent import (  # noqa: F401 (re-exported bridge)
    AMBIGUITY_KEYS, CONTRACT_KEYS, DECLARED, EVIDENCE_STATES,
    EXECUTION_ERROR, EXPLICIT_USER, FAILED, INFERRED, INVALID,
    IntentContract, Mapping, ORIGINS, PASSED, POLICY_REQUIRED, PROPOSED,
    REPOSITORY_DERIVED, REQUIREMENT_KEYS, Requirement, SATISFIED, SCHEMA,
    UNOBSERVED, UNSATISFIED, UNVERIFIABLE, VALID, _CODEC,
    _CONTRACT_FIELDS, _ambiguities, _codec_gaps, _frozen, _gap, _refuse,
    _requirement, _requirements, _strings, _unknown, _verbatim,
    annotations, cmd_intent, load, read, status, undeclared, unverifiable,
    validate,
)

warnings.warn(
    "rig_workbench.workbench.intent moved to rig_workbench.assurance.intent; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
