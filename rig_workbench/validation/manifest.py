"""validation manifest: manifest value-key checks over `.claude/rig.md` (#341).

Manifest keys are silently swallowed at RESOLVE/COMPOSE time when malformed
(a `default_backend: "manul"` typo falls back to `manual` without a peep),
so this check catches the mechanically-determinable subset before a run:
type/enum/ordering violations, never tier resolution or path existence.

See facets/instructions/validate.md §2 for the canonical spec. This
implements 5 of its items — `default_backend`, `default_budget`,
`default_orchestrate`, `worktree.enabled`, `size_thresholds` — chosen because
each is a self-contained type/enum/ordering check with one exact right
answer. `default_recipe`/`default_personas[]` tier resolution and
`knowledge.*` path existence are explicitly out of scope (need the same
project→user→shipped resolver COMPOSE uses, tracked separately).
"""

import pathlib

from .config import ROOT
from .state import _emit, parse_frontmatter

# Generic size-aware defaults (§4.1) substituted for unset size_thresholds
# subkeys before the ordering check, so a partial override is still validated
# against its real effective values.
_SIZE_DEFAULTS = {"S_max": 100, "M_max": 200, "L_max": 400}


def _bool_violation(value: object, key: str) -> str | None:
    if isinstance(value, bool):
        return None
    return f"manifest: {key} が不正値です（{value!r}）。有効値: true | false"


def _size_thresholds_violation(value: dict) -> str | None:
    effective: dict[str, object] = dict(_SIZE_DEFAULTS)
    is_default = {"S_max": True, "M_max": True, "L_max": True}
    for key in ("S_max", "M_max", "L_max"):
        if key in value:
            effective[key] = value[key]
            is_default[key] = False

    for key in ("S_max", "M_max", "L_max"):
        raw = effective[key]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            return f"manifest size_thresholds: {key} は正の整数である必要があります（実際: {raw!r}）"

    if effective["S_max"] < effective["M_max"] < effective["L_max"]:
        return None

    def fmt(key: str) -> str:
        suffix = "(既定)" if is_default[key] else ""
        return f"{key}={effective[key]}{suffix}"

    return (
        "manifest size_thresholds: "
        f"{fmt('S_max')} < {fmt('M_max')} < {fmt('L_max')} を満たしません — "
        "size-aware 判定が機能しません。"
        f"実効値: {fmt('S_max')} / {fmt('M_max')} / {fmt('L_max')}"
    )


def check_manifest(manifest_path: pathlib.Path | None = None) -> None:
    path = manifest_path if manifest_path is not None else ROOT / ".claude" / "rig.md"
    if not path.exists():
        return  # manifest is optional (§4.1) — no PASS/WARN/FAIL when absent

    fm, _ = parse_frontmatter(path)
    if fm is None:
        _emit("FAIL", f"manifest {path} — frontmatter did not parse as YAML")
        return
    if not isinstance(fm, dict):
        return

    violations: list[str] = []
    checked = 0

    if "default_backend" in fm:
        checked += 1
        value = fm["default_backend"]
        if value not in ("manual", "workflow"):
            violations.append(
                f"manifest: default_backend が不正値です（{value!r}）。有効値: manual | workflow"
            )

    if "default_budget" in fm:
        checked += 1
        value = fm["default_budget"]
        if value not in ("low", "mid"):
            violations.append(
                f"manifest: default_budget が不正値です（{value!r}）。有効値: low | mid"
            )

    if "default_orchestrate" in fm:
        checked += 1
        violation = _bool_violation(fm["default_orchestrate"], "default_orchestrate")
        if violation:
            violations.append(violation)

    worktree = fm.get("worktree")
    if isinstance(worktree, dict) and "enabled" in worktree:
        checked += 1
        violation = _bool_violation(worktree["enabled"], "worktree.enabled")
        if violation:
            violations.append(violation)

    if isinstance(fm.get("size_thresholds"), dict):
        checked += 1
        violation = _size_thresholds_violation(fm["size_thresholds"])
        if violation:
            violations.append(violation)

    if not checked:
        return  # manifest present but none of the 5 checkable keys are set

    if violations:
        for violation in violations:
            _emit("FAIL", violation)
        return
    _emit("PASS", f"manifest: {checked} value key(s) checked — all valid ({path})")
