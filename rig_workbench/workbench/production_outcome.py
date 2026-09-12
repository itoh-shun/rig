"""Deprecated location of `rig_workbench.assurance.production_outcome` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.production_outcome import (  # noqa: F401 (re-exported bridge)
    ACHIEVED, AT_LEAST, AT_MOST, BAR_KEYS, BOUNDS, CONFIRMED, DECLARED,
    DECREASE, DIRECTIONS, ENTRY_KEYS, ESTIMATED, EXECUTION_ERROR,
    EXPECTATION, EXPECTATION_KEYS, GUARDRAIL, INCONCLUSIVE, INCREASE,
    INVALID, KINDS, MEASURED, NOT_ACHIEVED, NOT_SHOWN, OBJECTIVE,
    OBJECT_ID, OBSERVATION, OBSERVATION_KEYS, OUTCOMES,
    PARTIALLY_ACHIEVED, PRECEDENCE, RECORD_NAME, REGRESSED, REPORTED,
    ROLES, ROLE_KEYS, SCHEMA, SETTLING, SHOWN, SUPERSEDED_GUARDRAIL_KEYS,
    UNMEASURED, UNOBSERVABLE, UNREADABLE_FILE, WINDOW_KEYS, _BAR_REASON,
    _BOUND_REASON, _bound, _compare_one, _gaps, _object_id, _refuse,
    _render, _resolve_change, _unknown, _vocabulary_gaps, annotations,
    change_cross_check, cmd_production_outcome, compare, finite_number,
    nonempty_text, offset_timestamp, projection, read, recorded,
    validate_expectation, validate_observation,
)

warnings.warn(
    "rig_workbench.workbench.production_outcome moved to rig_workbench.assurance.production_outcome; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
