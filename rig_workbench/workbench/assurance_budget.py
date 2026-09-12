"""Deprecated location of `rig_workbench.assurance.assurance_budget` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.assurance_budget import (  # noqa: F401 (re-exported bridge)
    ALTERNATE_RUNTIME, BALANCED, BELOW_FLOOR, BLOCKED, BUDGET_KEYS,
    Budget, CHEAPEST, CHOSEN, COST_BASIS, DOCUMENT_KEYS, ESTIMATED,
    EXECUTION_ERROR, EXHAUSTED, EXHAUSTION_ANSWERS, FASTEST, MEASURED,
    MORE_BUDGET, NO_PLAN, OPTIMISATIONS, OVER_BUDGET, PLAN_FIELDS,
    PRICE_UNKNOWN, Plan, REFUSED, RELAXED, SCHEMA, SELECTED, TOO_SLOW,
    UNKNOWN, _is_amount, _is_name, _rank, annotations, cmd_budget_plan,
    excluded, load, load_budget, plan_problems, select, validate,
)

warnings.warn(
    "rig_workbench.workbench.assurance_budget moved to rig_workbench.assurance.assurance_budget; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
