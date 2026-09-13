"""Deprecated location of `rig_workbench.assurance.knowledge_candidate` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.knowledge_candidate import (  # noqa: F401 (re-exported bridge)
    CANDIDATE_KEYS, CANDIDATE_SCHEMA, CITATION_KEYS, EVIDENCE_KEYS,
    EVIDENCE_SCHEMA, RECORD_KEYS, SCHEMA, SUPPORTED, UNOBSERVABLE,
    UNSUPPORTED, _lifecycle, _lifecycle_requested, _no_duplicate_keys,
    _string_list, _text, _unknown, annotations, assess,
    cmd_knowledge_candidate, read, validate_candidate, validate_evidence,
    view,
)

warnings.warn(
    "rig_workbench.workbench.knowledge_candidate moved to rig_workbench.assurance.knowledge_candidate; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
