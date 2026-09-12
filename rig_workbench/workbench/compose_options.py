"""Deprecated location of `rig_workbench.assurance.compose_options` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.compose_options import (  # noqa: F401 (re-exported bridge)
    AXES, ComposeOptionsError, Mapping, PackError, SCHEMA,
    _RECIPE_ALTERNATIVES, _axis, _candidate, annotations,
    cmd_compose_options, compose_options, load_manifest,
    non_negative_diff, resolve_asset, resolve_effective,
    resolve_task_route, validate_options,
)

warnings.warn(
    "rig_workbench.workbench.compose_options moved to rig_workbench.assurance.compose_options; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
