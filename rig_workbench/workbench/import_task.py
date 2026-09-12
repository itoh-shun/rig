"""Deprecated location of `rig_workbench.assurance.import_task` (design brief §11 T11).

A re-export bridge, kept for one major and removed in 4.0.0; the rule that decides
what it lists, and the one thing it cannot carry, are in `assurance/__init__.py`.
"""

import warnings

from rig_workbench.assurance.import_task import (  # noqa: F401 (re-exported bridge)
    IMPORT_KEY, PackError, TASK_TYPES, _derived_diff_md, _parse_claims,
    _resolve_head, annotations, build_acceptance, cmd_import,
    current_branch, die, ensure_rig_gitignored, find_similar_tasks, git,
    invocation_root, load_recipe_steps, make_slug, make_task_id, now_iso,
    render_flow, repo_root, resolve_task_route, runs_dir, save_json,
)

warnings.warn(
    "rig_workbench.workbench.import_task moved to rig_workbench.assurance.import_task; "
    "this shim is removed in 4.0.0",
    DeprecationWarning,
    stacklevel=2,
)
