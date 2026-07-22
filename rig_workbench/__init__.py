"""rig-workbench — quality-gated AI workbench.

The Python package that hosts the orchestrator, workbench, dashboard, and
validate CLIs. Same code powers:

- Claude Code plugin (via `commands/*.md` + `skills/rig/SKILL.md`).
- Standalone `rig-wb` CLI (`pip install rig-workbench`).
- Future codex / cursor / copilot skill wrappers (thin delegation to `rig-wb`).

Historically these lived under `scripts/*.py`. `rig_workbench.cli` imports them
by file path via importlib so the package works before we physically move the
files — a low-risk first step toward the "runs anywhere" positioning.
"""

__version__ = "1.21.0"
