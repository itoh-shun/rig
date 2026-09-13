"""Deprecated location of `rig_workbench.assurance.anomaly_trigger` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.anomaly_trigger import (  # noqa: F401 (re-exported bridge)
    CITATION_KEYS, DOES_NOT_GUARANTEE, EVENT_KEYS, EVENT_SCHEMA,
    EVIDENCE_KEYS, EVIDENCE_SCHEMA, GUARANTEE, READY, RECORD_KEYS, SCHEMA,
    SCOPE_KEYS, SIGNAL_KEYS, SOURCE_KEYS, UNMET, UNOBSERVABLE,
    WINDOW_KEYS, _closed_object, _no_duplicate_keys, _string_list, _text,
    _time, _unknown, annotations, assess, cmd_anomaly_trigger, read,
    validate_event, validate_evidence,
)

warnings.warn(
    "rig_workbench.workbench.anomaly_trigger moved to rig_workbench.assurance.anomaly_trigger; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
