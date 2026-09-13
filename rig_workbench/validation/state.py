"""validation state: shared emit/counters + frontmatter parser (split from scripts/validate.py).

Results accumulate in module-level lists/counters via `_emit`. Other modules
must read them as attributes of THIS module (`state.results`, `state._pass`,
`state._warn`, `state._fail`) so a single shared instance is observed across
module boundaries (`from .state import _pass` would snapshot the int).
Importing `_emit` itself is fine — its `global` statement always mutates this
module's namespace.

PyYAML is reached through `yaml_adapter.require_yaml()` rather than imported here. The
guard this module used to hold printed a line and called `sys.exit(1)` while the module
was still being imported, so every importer of `state` inherited a process exit it could
not see coming; `yaml_adapter`'s docstring says what replaced it and why the message and
the stream did not change.
"""

import pathlib

from .yaml_adapter import require_yaml

# ── counters ─────────────────────────────────────────────────────────────────
results: list[str] = []
_pass = _warn = _fail = 0


def _emit(level: str, msg: str) -> None:
    global _pass, _warn, _fail
    if level == "PASS":
        _pass += 1
    elif level == "WARN":
        _warn += 1
    elif level == "FAIL":
        _fail += 1
    results.append(f"[{level}] {msg}")


# ── frontmatter parser ───────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> tuple[dict | None, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    yaml = require_yaml()
    try:
        fm = yaml.safe_load(parts[1]) or {}
        return fm, parts[2]
    except yaml.YAMLError as exc:
        return None, str(exc)
