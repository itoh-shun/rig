"""Deprecated location of `rig_workbench.assurance.assurance` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.assurance import (  # noqa: F401 (re-exported bridge)
    SCHEMA, UNREADABLE, _FINAL_STATUS, _GATE_STATUSES, _INLINE_OPENERS,
    _INTENT_RENDERED, _INTENT_WITHHELD, _MARKDOWN_ESCAPES, _SOURCES,
    _UNRENDERABLE, _approvals, _code, _commit_exists, _digest, _evidence,
    _final_status, _flat, _gap, _gates, _git, _import_block, _isolation,
    _producer, _provenance, _read_contract, _read_json,
    _read_json_document, _render_value, _resolve, _target, _text,
    _unrendered, _verifier, annotations, build_receipt, cmd_receipt, die,
    final_status_values, load_task, observed, render_markdown, repo_root,
    resolve_task_id, run_dir, target_moved, unobserved, verify,
    verify_provenance,
)

warnings.warn(
    "rig_workbench.workbench.assurance moved to rig_workbench.assurance.assurance; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
