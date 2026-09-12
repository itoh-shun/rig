"""Deprecated location of `rig_workbench.assurance.development_loop` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.development_loop import (  # noqa: F401 (re-exported bridge)
    ADMISSIBLE, ADMITTED, AMBIGUOUS, BLOCKED, BUDGET, CAPABILITY,
    CYCLE_FIELDS, Cycle, DESTRUCTIVE, DOCUMENT_KEYS, ESCALATIONS,
    ESCALATION_REQUIRED, EXECUTION_ERROR, History, IMPLEMENT, Limits,
    MAX_CYCLES, NOT_DECLARED_DONE, NOT_THIS_GOAL, NOT_THIS_TASK,
    NO_PROGRESS, OBJECT_ID, OUTCOMES, PLAN, POLICY_APPROVAL,
    PRODUCTS_NOT_A_CHAIN, PRODUCT_UNRELATED, READY_FOR_ASSURANCE, REFUSED,
    REJECTED, REPAIR, REPEATED_FAILURE, REPLAN, RESEARCH, REVIEW, SCHEMA,
    SELF_REPORTED_KEYS, STATES, STOP_REASONS, TARGET_NOT_IMMUTABLE,
    TARGET_NOT_THE_LOOPS, TEST, _FORBIDDEN_SELF_REPORTED, _longest_run,
    annotations, cmd_dev_loop, git_history, handoff, load, must_stop,
    validate,
)

warnings.warn(
    "rig_workbench.workbench.development_loop moved to rig_workbench.assurance.development_loop; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
