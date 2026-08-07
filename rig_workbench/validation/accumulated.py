"""validation accumulated: schema checks over captured project knowledge (#365).

`capture` (§7) writes what a run learned into
`<repo>/.claude/rig/knowledge/accumulated/*.md`, and COMPOSE injects the body of
those files at the Knowledge position of later prompts. A file whose frontmatter
is malformed still gets injected — it just arrives unclassified and out of step
with the MEMORY.md index, which is why nobody notices.

facets/instructions/validate.md ⑦ and ⑦-b define the schema; this implements it:

    WARN  category   pitfall | decision | convention | stuck-twice
    WARN  title      present and non-empty
    WARN  date       YYYY-MM-DD
    WARN  body       `## 何が起きたか` and `## 次回への示唆` both present

WARN throughout, per the spec: a malformed file degrades the knowledge layer, it
does not stop the run. An absent directory is silence, not a finding — the same
treatment wiki and ai-quirks get, since most projects never capture anything.
"""

import pathlib
import re

from .config import ROOT
from .state import _emit, parse_frontmatter

CATEGORIES = ("pitfall", "decision", "convention", "stuck-twice")
REQUIRED_SECTIONS = ("## 何が起きたか", "## 次回への示唆")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _default_dir() -> pathlib.Path:
    return ROOT / ".claude" / "rig" / "knowledge" / "accumulated"


def check_accumulated(accumulated_dir: pathlib.Path | None = None) -> None:
    directory = accumulated_dir if accumulated_dir is not None else _default_dir()
    if not directory.is_dir():
        return  # capture never ran here; nothing to say

    files = sorted(path for path in directory.glob("*.md") if not path.name.startswith("_"))
    if not files:
        return

    warnings = 0
    for path in files:
        label = f"accumulated/{path.name}"
        fm, body = parse_frontmatter(path)
        if fm is None:
            _emit("WARN", f"{label}: frontmatter が YAML として読めません（SKILL.md §7.2）")
            warnings += 1
            continue
        if not isinstance(fm, dict):
            fm = {}

        category = fm.get("category")
        if category not in CATEGORIES:
            _emit(
                "WARN",
                f"{label}: category が不正値です（{category!r}）。"
                f"有効値: {'|'.join(CATEGORIES)}",
            )
            warnings += 1

        title = fm.get("title")
        if not isinstance(title, str) or not title.strip():
            _emit(
                "WARN",
                f"{label}: title が空です。MEMORY.md インデックスとの整合が取れません。",
            )
            warnings += 1

        date = fm.get("date")
        # PyYAML turns an unquoted 2026-06-10 into a date object, which is the
        # shape the spec asks for; anything else is compared as written.
        if not (hasattr(date, "isoformat") and not hasattr(date, "hour")):
            if not isinstance(date, str) or not _DATE_RE.match(date.strip()):
                _emit(
                    "WARN",
                    f"{label}: date が YYYY-MM-DD 形式ではありません（{date!r}）。",
                )
                warnings += 1

        for section in REQUIRED_SECTIONS:
            if not re.search(rf"^{re.escape(section)}\s*$", body, re.MULTILINE):
                _emit(
                    "WARN",
                    f"{label}: 必須セクション `{section}` が見つかりません（SKILL.md §7.2）。",
                )
                warnings += 1

    _emit("PASS", f"accumulated/: {len(files)} file(s) checked ({warnings} warning(s))")
