"""validation manifest: manifest value-key checks over `.claude/rig.md` (#341).

Manifest keys are silently swallowed at RESOLVE/COMPOSE time when malformed
(a `default_backend: "manul"` typo falls back to `manual` without a peep),
so this check catches the mechanically-determinable subset before a run:
type/enum/ordering violations, never tier resolution or path existence.

See facets/instructions/validate.md §2 for the canonical spec, which this now
implements in full:

    FAIL  default_backend, default_budget, default_orchestrate,
          worktree.enabled, size_thresholds     type / enum / ordering
    FAIL  default_max_retries                   integer ≥1
    FAIL  default_recipe, default_personas[]    tier resolution (#372)
    WARN  knowledge.context_file / adr_dir /
          design_docs[]                         path existence (#363)

The severity split is the spec's, and it tracks consequence. A malformed
enum or an unresolvable recipe name means the run silently does something
other than what the manifest asked for, so it fails. A missing knowledge path
means the run proceeds with less context than intended — worth saying out
loud, not worth blocking on.
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


def _max_retries_violation(value: object) -> str | None:
    # bool is an int subclass in Python; `default_max_retries: true` is a typo,
    # not the number 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return f"manifest: default_max_retries が不正値です（{value!r}）。有効値: 1以上の整数"
    return None


def _resolve(kind: str, name: str, project: pathlib.Path) -> bool:
    """Ask the resolver COMPOSE uses whether a name resolves in any tier.

    Imported lazily: `validation` is loaded by the CI entry point and must not
    take a hard dependency on the pack machinery just to report a typo.
    """
    try:
        from rig_workbench.packs.resolver import resolve_asset
    except ImportError:  # pragma: no cover - packs ships with the workbench
        return True  # cannot check; do not invent a failure
    try:
        return resolve_asset(kind, name, project=project) is not None
    except Exception:
        # A broken pack collection is a different check's problem. Reporting it
        # here as a manifest typo would point at the wrong file.
        return True


def _tier_violations(fm: dict, project: pathlib.Path) -> tuple[list[str], int]:
    """default_recipe / default_personas[] — resolvable in some tier? (#372)

    Both fall back silently at run time: an unresolvable `default_recipe` drops
    RESOLVE into interactive mode, and an unresolvable persona is dropped from
    the review fan-out. Neither says anything, so the typo survives.
    """
    violations: list[str] = []
    checked = 0

    recipe = fm.get("default_recipe")
    if isinstance(recipe, str) and recipe and recipe != "interactive":
        checked += 1
        if not _resolve("recipe", recipe, project):
            violations.append(
                f"manifest: default_recipe {recipe!r} はどの tier にも見つかりません"
                "（project → user → shipped）。RESOLVE が黙って interactive にフォールバックします"
            )

    personas = fm.get("default_personas")
    if isinstance(personas, list):
        for entry in personas:
            if not isinstance(entry, str) or not entry:
                continue
            checked += 1
            if not (_resolve("persona", entry, project) or _resolve("agent", entry, project)):
                violations.append(
                    f"manifest: default_personas[] の {entry!r} は persona facet にも agents/ にも"
                    "見つかりません。COMPOSE が黙ってこの reviewer を落とします"
                )
    return violations, checked


def _knowledge_warnings(fm: dict, project: pathlib.Path) -> tuple[list[str], int]:
    """knowledge.* — do the declared paths exist? (#363, spec'd in #14)

    WARN rather than FAIL: a missing knowledge path costs the run context, not
    correctness. The run still completes, which is exactly why nobody notices.
    """
    knowledge = fm.get("knowledge")
    if not isinstance(knowledge, dict):
        return [], 0

    warnings: list[str] = []
    checked = 0

    context_file = knowledge.get("context_file")
    if isinstance(context_file, str) and context_file:
        checked += 1
        if not (project / context_file).is_file():
            warnings.append(
                f"manifest: knowledge.context_file が見つかりません（{context_file!r}）。"
                "ドメイン知識注入が無効化されます"
            )

    adr_dir = knowledge.get("adr_dir")
    if isinstance(adr_dir, str) and adr_dir:
        checked += 1
        if not (project / adr_dir).is_dir():
            warnings.append(
                f"manifest: knowledge.adr_dir が見つかりません（{adr_dir!r}）。"
                "ADR の注入が無効化されます"
            )

    design_docs = knowledge.get("design_docs")
    if isinstance(design_docs, list):
        for entry in design_docs:
            if not isinstance(entry, str) or not entry:
                continue
            checked += 1
            if not (project / entry).is_file():
                warnings.append(
                    f"manifest: knowledge.design_docs[] の {entry!r} が見つかりません。"
                    "この設計文書は注入されません"
                )
    return warnings, checked


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

    if "default_max_retries" in fm:
        checked += 1
        violation = _max_retries_violation(fm["default_max_retries"])
        if violation:
            violations.append(violation)

    # The manifest describes the project it sits in, so paths and tier lookups
    # resolve against its directory, not the checker's cwd.
    project = path.parent.parent if path.parent.name == ".claude" else path.parent
    tier_violations, tier_checked = _tier_violations(fm, project)
    violations.extend(tier_violations)
    checked += tier_checked

    warnings, knowledge_checked = _knowledge_warnings(fm, project)
    checked += knowledge_checked

    if not checked:
        return  # manifest present but none of the checkable keys are set

    for warning in warnings:
        _emit("WARN", warning)

    if violations:
        for violation in violations:
            _emit("FAIL", violation)
        return
    _emit("PASS", f"manifest: {checked} value key(s) checked — all valid ({path})")
