"""PyYAML, reached through a guard that raises instead of deciding for its caller.

`recipes.py` carried the import inline, and the shape it had was already one step better
than `validation/state.py`'s::

    try:
        import yaml
    except ImportError:
        # Don't kill importers (pytest collection, library use) at import time —
        # fail with the CLI hint on first actual use instead (parse_frontmatter).
        yaml = None

So there is no import-time `print` and no import-time `sys.exit` to preserve here; that
comment is the record of somebody having already fixed the worse half. What is left is the
half `validation/yaml_adapter.py` names: a third-party import standing in a judgement
module, and a module-level `yaml = None` that every reader has to check before use. The
import moves here, behind a call, and the guard raises — `parse_frontmatter` turns
`PyYAMLMissing` into the same line on the same stream and the same exit status through the
`Presenter` the shell built.

**The text and the stream are unchanged.** `[ERROR] PyYAML not found. \\`pip install
pyyaml\\`.` went to **stdout** and still does. It is not `validation`'s wording — that one
says `Install it with \\`pip install pyyaml\\`` — and the two are deliberately not unified
here: this pass promised a user's output would not move, and rewording a message is a
change to what a user reads, with no migration reason behind it.

Nothing is cached, for the reason `validation/yaml_adapter.py` gives: `import yaml` after
the first time is a `sys.modules` lookup, and a cache would be a second piece of state in a
module whose only job is to make one import interceptable — including by
`tests/test_orchestrate_yaml_guard.py`, which reproduces the absence by putting `None` in
`sys.modules`. The old `yaml = None` *was* such a cache, resolved once at import: with
PyYAML installed, the `if yaml is None` branch below could not be reached at all from a
running process, which is why it had never been executed by a test.
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
        raise PyYAMLMissing("PyYAML not found. `pip install pyyaml`.") from exc
    return yaml
