"""PyYAML, reached through a guard that raises instead of ending the process.

This pillar has exactly one optional dependency and `state.py` used to carry it inline::

    try:
        import yaml
    except ImportError:
        print("[ERROR] PyYAML not found. Install it with `pip install pyyaml`.")
        sys.exit(1)

That is two effects (a `print` and a process exit) at **import** time, which is the worst
place for them: `import rig_workbench.validation.state` was a statement that could print to
somebody else's stdout and kill their interpreter, and every module in the pillar imports
`state`. There was no seam at all — no caller, not `cli.main()`, not a test, could see the
condition or choose what to do about it, because the decision had already been made by the
time the name was bound.

So the import and the guard live here, behind a call, and the guard **raises**.
`PyYAMLMissing` reaches `cmd_validate`, which reports it through the `Presenter` the shell
built and returns 1 — the same line on the same stream and the same exit status a caller saw
before, now produced somewhere a caller can intercept.

The text is unchanged and so is the stream. `[ERROR] PyYAML not found …` went to **stdout**,
and `cmd_validate` keeps sending it there through `out.out`; routing it to `out.err` because
of the prefix would move a line out of the stream a caller is piping, which is the change
`ports/__init__.py`'s `Presenter` docstring refuses to make silently.

Nothing is cached. `import yaml` after the first time is a `sys.modules` lookup, and a cache
here would be a second piece of state to reason about in a module whose only job is to make
one import interceptable — including by `tests/test_validation_yaml_guard.py`, which
reproduces the absence by putting `None` in `sys.modules` and would otherwise be measuring
the cache rather than the guard.
"""

from __future__ import annotations

from types import ModuleType


class PyYAMLMissing(RuntimeError):
    """PyYAML is not importable, raised where a caller can still report it."""


def require_yaml() -> ModuleType:
    """The `yaml` module, or `PyYAMLMissing` naming what to install."""
    try:
        import yaml
    except ImportError as exc:
        raise PyYAMLMissing(
            "PyYAML not found. Install it with `pip install pyyaml`."
        ) from exc
    return yaml
